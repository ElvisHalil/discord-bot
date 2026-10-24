import re
import urllib.parse
from ddgs import DDGS
from src.skills.youtube.aggregators.base import BaseYouTubeAggregator
from src.skills.youtube.catalogs import CHANNEL_QUERY_ALIASES, TRUSTED_VIDEO_CHANNELS

_VIDEO_QUERY_STOPWORDS = frozenset({
    "youtube", "channel", "video", "videos", "watch", "official", "pull", "up",
    "from", "the", "for", "me", "show", "find", "please", "can", "you", "on", "in", "of", "to", "a", "an",
})


def is_trusted_or_requested_video_channel(query: str, channel: str) -> bool:
    """
    Returns True if `channel` is a known official broadcaster/publisher (e.g., 'FORMULA 1',
    'Sky Sports', 'ESPN', 'PlayStation', 'Rockstar Games') OR matches the channel name/alias
    requested in `query`.
    """
    chan_low = (channel or "").strip().lower()
    if not chan_low:
        return False

    q_low = (query or "").strip().lower()

    # 1. Exact or phrase match against known official broadcasters/publishers
    if chan_low in TRUSTED_VIDEO_CHANNELS:
        return True
    for trusted in TRUSTED_VIDEO_CHANNELS:
        if len(trusted) >= 4 and re.search(rf"(?<![a-z0-9]){re.escape(trusted)}(?![a-z0-9])", chan_low):
            return True

    # 2. Shorthand alias match in query (e.g. 'f1' or 'fp1' -> 'formula 1')
    for alias, target_chan in CHANNEL_QUERY_ALIASES.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", q_low):
            if target_chan in chan_low:
                return True

    # 3. Explicit channel name tokens present in user's query
    chan_tokens = [
        w for w in re.findall(r"[a-z0-9]+", chan_low)
        if len(w) > 1 and w not in _VIDEO_QUERY_STOPWORDS
    ]
    if chan_tokens:
        q_words = set(re.findall(r"[a-z0-9]+", q_low))
        if all(w in q_words for w in chan_tokens):
            return True

    return False


def has_video_query_relevance(query: str, title: str, channel: str) -> bool:
    """Verifies that a candidate video's title/channel shares meaningful tokens with the query."""
    if not title:
        return False
    q_words = [
        w for w in re.findall(r"[a-z0-9]+", (query or "").lower())
        if len(w) > 1 and w not in _VIDEO_QUERY_STOPWORDS
    ]
    if not q_words:
        return True
    cand_words = set(re.findall(r"[a-z0-9]+", f"{title} {channel}".lower()))
    # Expand shorthand aliases (e.g. 'f1' in query matches 'formula' in candidate)
    for alias, target_chan in CHANNEL_QUERY_ALIASES.items():
        if alias in q_words and target_chan in f"{title} {channel}".lower():
            cand_words.add(alias)
    overlap = [w for w in q_words if w in cand_words]
    if not overlap:
        return False
    # If query has subject tokens beyond just the channel name (e.g. 'fp1', 'highlights'),
    # require at least one subject token in the video title itself.
    title_words = set(re.findall(r"[a-z0-9]+", title.lower()))
    chan_words = set(re.findall(r"[a-z0-9]+", (channel or "").lower()))
    subject_tokens = [w for w in q_words if w not in chan_words and w not in CHANNEL_QUERY_ALIASES]
    if subject_tokens and title_words and not any(w in title_words for w in subject_tokens):
        # Allow if at least one raw q_word (such as 'fp1') is in title_words
        if not any(w in title_words for w in q_words):
            return False
    return True


def _fetch_direct_youtube_video_ids(yt_q: str, limit: int, http_get_fn=None) -> list[str]:
    """Queries YouTube search directly over the persistent HTTP session and extracts video IDs (~0.4s)."""
    if http_get_fn is None:
        return []
    try:
        print(f"  └─ 🔍 [YouTube Direct Video]: Querying '{yt_q}'...")
        yt_search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(yt_q)}"
        r_yt = http_get_fn(
            yt_search_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=3.5,
        )
        if r_yt.status_code == 200:
            direct_vids = list(
                dict.fromkeys(re.findall(r'"videoRenderer":\{"videoId":"([0-9A-Za-z_-]{11})"', r_yt.text))
            )
            if not direct_vids:
                direct_vids = list(dict.fromkeys(re.findall(r'"videoId":"([0-9A-Za-z_-]{11})"', r_yt.text)))
            return direct_vids[:limit]
    except Exception as e:
        print(f"  └─ ⚠️ [YouTube Direct Video Warning]: {e}")
    return []


class DuckDuckGoYouTubeAggregator(BaseYouTubeAggregator):
    """
    Primary YouTube video search aggregator (0 API quota units):
    1. Phase 1A: Direct YouTube search (`youtube.com/results?search_query=...`) for instant indexing of brand-new uploads.
    2. Phase 1B: DuckDuckGo search fallback (`site:youtube.com ...`).
    Uses a 3-Tier Official Priority Hierarchy so official broadcaster uploads (like 'FORMULA 1', 'NFL', 'Sky Sports')
    whose inline iframe player is disabled by the rights holder still beat random third-party re-uploads.
    """

    def __init__(self, verify_embeddable_fn=None, get_oembed_fn=None, is_short_fn=None, http_get_fn=None):
        self.verify_embeddable = verify_embeddable_fn
        self.get_video_oembed = get_oembed_fn
        self.is_short_fn = is_short_fn
        self.http_get_fn = http_get_fn

    def _evaluate_candidates(
        self,
        query: str,
        batch_candidates: list[tuple[str, str, str]],
        official_embeddable: list[dict],
        official_blocked: list[dict],
        standard_embeddable: list[dict],
        standard_blocked: list[dict],
        max_results: int,
    ) -> None:
        for idx, (vid, raw_title, href) in enumerate(batch_candidates):
            if self.is_short_fn and self.is_short_fn(vid, href=href, title=raw_title):
                print(f"  └─ ⚠️ [YouTube Video]: Skipping {vid} ('{raw_title}') - YouTube Short")
                continue

            is_embed = True if not self.verify_embeddable else bool(self.verify_embeddable(vid))
            oembed = self.get_video_oembed(vid) if self.get_video_oembed else {"title": "", "author": ""}
            title = oembed.get("title") or raw_title
            author = oembed.get("author", "")

            if not is_embed:
                if not oembed.get("title"):
                    print(f"  └─ ⚠️ [YouTube Video]: Skipping {vid} ('{raw_title}') - Unavailable/dead on oEmbed")
                    continue
                if self.is_short_fn and self.is_short_fn(vid, href=href, title=title):
                    continue
                if not has_video_query_relevance(query, title, author):
                    print(f"  └─ ⚠️ [YouTube Video]: Skipping {vid} ('{title}') - Low relevance to '{query}'")
                    continue
                is_official = is_trusted_or_requested_video_channel(query, author)
                entry = {
                    "id": vid,
                    "title": title,
                    "channel": author,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "embeddable": False,
                    "is_official_fallback": is_official,
                }
                if is_official:
                    official_blocked.append(entry)
                    print(
                        f"  └─ 📌 [YouTube Video]: Recorded official channel upload {vid} ('{title}' by '{author}') "
                        f"(broadcaster disabled inline iframe embed; rich Discord preview card active)."
                    )
                    if len(official_blocked) >= max_results and idx >= max_results:
                        return
                else:
                    standard_blocked.append(entry)
                    print(f"  └─ ⚠️ [YouTube Video]: Skipping {vid} ('{title}') - Embedding disabled")
                continue

            if self.get_video_oembed and not oembed.get("title"):
                print(f"  └─ ⚠️ [YouTube Video]: Skipping {vid} ('{raw_title}') - Failed oEmbed verification")
                continue

            if not has_video_query_relevance(query, title, author):
                print(f"  └─ ⚠️ [YouTube Video]: Skipping {vid} ('{title}') - Low relevance to '{query}'")
                continue

            is_official = is_trusted_or_requested_video_channel(query, author)
            entry = {
                "id": vid,
                "title": title,
                "channel": author,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "embeddable": True,
                "is_official_fallback": False,
            }
            if is_official:
                official_embeddable.append(entry)
                print(f"  └─ ✅ [YouTube Video]: Verified official channel candidate {vid} ('{title}' by '{author}')")
            else:
                standard_embeddable.append(entry)
                print(f"  └─ ✅ [YouTube Video]: Verified candidate {vid} ('{title}')")

            if len(official_embeddable) >= max_results:
                return
            if len(official_blocked) >= max_results and idx >= max_results:
                return

    def search(self, query: str, max_results: int = 1) -> list[dict]:
        seen_ids: set[str] = set()
        official_embeddable: list[dict] = []
        official_blocked: list[dict] = []
        standard_embeddable: list[dict] = []
        standard_blocked: list[dict] = []

        # Phase 1A: Direct YouTube search (instant indexing of same-day uploads like F1 FP1 highlights)
        direct_vids = _fetch_direct_youtube_video_ids(query, max_results + 5, http_get_fn=self.http_get_fn)
        if direct_vids:
            direct_batch = []
            for vid in direct_vids:
                if vid not in seen_ids:
                    seen_ids.add(vid)
                    direct_batch.append((vid, "", f"https://www.youtube.com/watch?v={vid}"))
            self._evaluate_candidates(
                query,
                direct_batch,
                official_embeddable,
                official_blocked,
                standard_embeddable,
                standard_blocked,
                max_results,
            )
            if len(official_embeddable) >= max_results:
                return official_embeddable[:max_results]
            if len(official_blocked) >= max_results:
                print(
                    f"  └─ 📌 [YouTube Video]: Returning official broadcaster upload {official_blocked[0]['id']} "
                    f"('{official_blocked[0]['title']}' by '{official_blocked[0]['channel']}')."
                )
                return official_blocked[:max_results]

        # Phase 1B: DuckDuckGo search fallback
        queries_to_try = [
            f"site:youtube.com {query}",
            f"site:youtube.com {query} video",
        ]
        for q in queries_to_try:
            batch_candidates = []
            try:
                res = list(DDGS().text(q, max_results=max_results + 5))
                for r in res:
                    href = r.get("href", "")
                    if "/shorts/" in href:
                        continue
                    m = re.search(r"(?:v=|\/watch\?v=|\/embed\/|youtu\.be\/)([0-9A-Za-z_-]{11})(?:[?&/]|\b|$)", href)
                    if m:
                        vid = m.group(1)
                        if vid not in seen_ids:
                            seen_ids.add(vid)
                            batch_candidates.append((vid, r.get("title", ""), href))
            except Exception:
                continue

            if not batch_candidates:
                continue

            self._evaluate_candidates(
                query,
                batch_candidates,
                official_embeddable,
                official_blocked,
                standard_embeddable,
                standard_blocked,
                max_results,
            )
            if len(official_embeddable) >= max_results:
                return official_embeddable[:max_results]
            if not official_blocked and len(standard_embeddable) >= max_results:
                return standard_embeddable[:max_results]

        # 3-Tier Decision Engine:
        # 1. Official channel (embeddable) -> 2. Official channel (embed-blocked by rights holder)
        # -> 3. Standard upload (embeddable) -> 4. Standard oEmbed-verified upload (embed-blocked)
        combined = official_embeddable + official_blocked + standard_embeddable + standard_blocked
        if combined:
            if not official_embeddable and official_blocked:
                print(
                    f"  └─ 📌 [YouTube Video]: Returning official broadcaster upload {official_blocked[0]['id']} "
                    f"('{official_blocked[0]['title']}' by '{official_blocked[0]['channel']}')."
                )
            elif not official_embeddable and not official_blocked and not standard_embeddable and standard_blocked:
                print(
                    f"  └─ 📌 [YouTube Video]: No inline-embeddable video found; returning oEmbed-verified fallback {standard_blocked[0]['id']}."
                )
            return combined[:max_results]

        return []


