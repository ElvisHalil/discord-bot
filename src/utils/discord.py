import re
import subprocess
import time
import requests

def ensure_ollama_running():
    """Checks if Ollama is running and starts it invisibly if it is not."""
    print("⚙️ [Boot] Checking Ollama server status...")
    try:
        requests.get("http://127.0.0.1:11434/", timeout=2)
        print("🟢 [Boot] Ollama is already running.")
    except requests.exceptions.ConnectionError:
        print("🔴 [Boot] Ollama is offline. Starting background service...")
        subprocess.Popen(
            ["ollama", "serve"],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(4)
        print("🟢 [Boot] Ollama server successfully started.")

def chunk_message(text: str, max_length: int = 1900) -> list[str]:
    """Chunks a long message for Discord safely, avoiding splitting words or markdown."""
    chunks = []
    while len(text) > max_length:
        split_index = text.rfind("\n\n", 0, max_length)
        if split_index == -1:
            split_index = text.rfind("\n", 0, max_length)
        if split_index == -1:
            split_index = text.rfind(" ", 0, max_length)
        if split_index == -1:
            split_index = max_length

        chunks.append(text[:split_index].strip())
        text = text[split_index:].strip()
    
    if text:
        chunks.append(text.strip())
        
    return chunks

# URL body that allows one level of balanced parentheses, e.g. .../Python_(programming_language)
_URL_BODY = r'https?://(?:[^\s()<>]|\([^\s()<>]*\))+'
_YOUTUBE_HOST = re.compile(r'(?:youtube\.com|youtu\.be)', re.IGNORECASE)
_TRAILING_PUNCT = '.,;:!?\'"'

# Lines where the model tells the *user* to run an internal tool, e.g.
# "Need deeper details on any story? Use read_website to explore specific URLs!"
# Deliberately narrow: ordinary sentences that merely mention a tool name are kept.
_LEAKED_TOOL_FOOTER = re.compile(
    r'(?im)^[^\S\n]*(?:[-*•>][^\S\n]*)?'
    r'(?:need (?:deeper|more) (?:details|info|information)\b[^\n]*\b(?:read_website|search_web)\b[^\n]*'
    r'|(?:you can |feel free to )?(?:use|call|try) (?:the )?`?(?:read_website|search_web)`?(?: tool)? (?:to|on|for)\b[^\n]*)'
    r'[^\S\n]*(?:\n|$)'
)


def suppress_discord_embeds(text: str) -> str:
    """Enclose web links in angle brackets <URL> to prevent Discord from generating
    huge embed preview cards for article links, while preserving intentional YouTube embeds.
    Also strips leaked "use read_website ..." footers the model sometimes echoes to the user.
    """
    if not text:
        return text

    # 0. Repair masked links missing their closing bracket: [Title](<https://...) -> [Title](<https://...>)
    text = re.sub(rf'\]\(<({_URL_BODY})\)', r'](<\1>)', text)

    # 1. Masked markdown links: [Title](https://...) -> [Title](<https://...>)
    # Exempt youtube.com and youtu.be so intended media cards embed.
    def _mask_link(m):
        label, url = m.group(1), m.group(2)
        if _YOUTUBE_HOST.search(url):
            return m.group(0)
        return f"[{label}](<{url}>)"

    text = re.sub(rf'\[([^\]]+)\]\(({_URL_BODY})\)', _mask_link, text)

    # 2. Standalone raw URLs: https://... -> <https://...>
    # Skip URLs already wrapped in <...> and markdown link targets "](url)" (YouTube ones kept bare above).
    def _mask_raw(m):
        url = m.group(1)
        trailing = ""
        while url and url[-1] in _TRAILING_PUNCT:
            trailing = url[-1] + trailing
            url = url[:-1]
        if _YOUTUBE_HOST.search(url):
            return url + trailing
        return f"<{url}>{trailing}"

    text = re.sub(rf'(?<!<)(?<!\]\()({_URL_BODY})', _mask_raw, text)

    # 3. Strip leaked tool-instruction footers
    text = _LEAKED_TOOL_FOOTER.sub('', text)

    # 4. Clean up any trailing empty separator dashes
    text = re.sub(r'\n*---\s*$', '', text)

    return text.strip()

