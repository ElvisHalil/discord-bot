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

def suppress_discord_embeds(text: str) -> str:
    """Enclose web links in angle brackets <URL> to prevent Discord from generating
    huge embed preview cards for article links, while preserving intentional YouTube embeds.
    Also strips any accidentally echoed tool instructions.
    """
    if not text:
        return text

    # 1. Masked markdown links: [Title](https://...) -> [Title](<https://...>)
    # Exempt youtube.com and youtu.be so intended media cards embed.
    def _mask_link(m):
        label, url = m.group(1), m.group(2)
        if re.search(r'(?:youtube\.com|youtu\.be)', url, re.IGNORECASE):
            return m.group(0)
        return f"[{label}](<{url}>)"

    text = re.sub(r'\[([^\]]+)\]\((https?://[^>)\s]+)\)', _mask_link, text)

    # 2. Standalone raw URLs: https://... -> <https://...>
    # Match URLs not already preceded by < or ( or " or '
    def _mask_raw(m):
        url = m.group(1)
        if re.search(r'(?:youtube\.com|youtu\.be)', url, re.IGNORECASE):
            return url
        return f"<{url}>"

    text = re.sub(r'(?<![<(\"\'])(https?://[^\s>)\"\']+)', _mask_raw, text)

    # 3. Strip any leaked internal tool instruction lines
    leak_pattern = r'(?im)^.*?\b(?:read_website|search_web)\b.*$\n?'
    text = re.sub(leak_pattern, '', text)

    # 4. Clean up any trailing empty separator dashes
    text = re.sub(r'\n*---\s*$', '', text)

    return text.strip()
