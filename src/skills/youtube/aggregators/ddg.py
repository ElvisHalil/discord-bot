import re
from ddgs import DDGS
from src.skills.youtube.aggregators.base import BaseYouTubeSearchAggregator

class DuckDuckGoYouTubeAggregator(BaseYouTubeSearchAggregator):
    """Fallback search aggregator that scrapes DuckDuckGo for working, embeddable YouTube videos."""

    def __init__(self, verify_embeddable_fn=None, get_oembed_fn=None, is_short_fn=None):
        self.verify_embeddable = verify_embeddable_fn
        self.get_video_oembed = get_oembed_fn
        self.is_short_fn = is_short_fn

    def search(self, query: str, max_results: int = 1) -> list[dict]:
        queries_to_try = [
            f"site:youtube.com {query}",
            f"site:youtube.com {query} video"
        ]
        seen_ids = set()
        candidates = []

        for q in queries_to_try:
            try:
                res = list(DDGS().text(q, max_results=max_results + 4))
                for r in res:
                    href = r.get('href', '')
                    if "/shorts/" in href:
                        continue
                    m = re.search(r'(?:v=|\/watch\?v=|\/embed\/|youtu\.be\/)([0-9A-Za-z_-]{11})(?:[?&/]|\b|$)', href)
                    if m:
                        vid = m.group(1)
                        if vid not in seen_ids:
                            seen_ids.add(vid)
                            candidates.append((vid, r.get('title', ''), href))
                if len(candidates) >= max_results + 3:
                    break
            except Exception:
                continue

        verified_results = []
        for vid, raw_title, href in candidates:
            if self.is_short_fn and self.is_short_fn(vid, href=href, title=raw_title):
                print(f"  └─ ⚠️ [YouTube DDG]: Skipping {vid} ('{raw_title}') - YouTube Short")
                continue
            if self.verify_embeddable and not self.verify_embeddable(vid):
                print(f"  └─ ⚠️ [YouTube DDG]: Skipping {vid} ('{raw_title}') - Embedding disabled")
                continue

            oembed = self.get_video_oembed(vid) if self.get_video_oembed else {"title": "", "author": ""}
            title = oembed.get("title") or raw_title
            author = oembed.get("author", "")

            verified_results.append({
                "id": vid,
                "title": title,
                "channel": author,
                "url": f"https://www.youtube.com/watch?v={vid}"
            })
            print(f"  └─ ✅ [YouTube DDG]: Verified candidate {vid} ('{title}')")

            if len(verified_results) >= max_results:
                break

        return verified_results

