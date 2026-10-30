import os
import re
import shutil
import subprocess
import time
import requests


def get_ollama_base_url() -> str:
    """Resolve the base URL for the Ollama server from OLLAMA_HOST (defaulting to http://127.0.0.1:11434)."""
    raw = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").strip()
    if not raw:
        return "http://127.0.0.1:11434"
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    return raw.rstrip("/")


def load_modes_config() -> dict:
    """Load the MODES configuration dictionary from src.config, falling back to src/config.example.py."""
    try:
        from src.config import MODES
        if MODES:
            return MODES
    except Exception:
        pass
    try:
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "src.config",
            Path(__file__).resolve().parent.parent / "config.example.py",
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return getattr(mod, "MODES", {})
    except Exception:
        pass
    return {}


def ensure_ollama_models(base_url: str, models: list[str] | None = None) -> None:
    """Verify configured Ollama models exist in the target Ollama instance and pull any missing ones."""
    if models is None:
        modes_cfg = load_modes_config()
        models = list(dict.fromkeys(m.get("model", "") for m in modes_cfg.values() if m.get("model")))

    if not models:
        return

    try:
        tags_resp = requests.get(f"{base_url}/api/tags", timeout=5)
        tags_resp.raise_for_status()
        installed = {
            m.get("name", "")
            for m in (tags_resp.json() or {}).get("models", [])
            if isinstance(m, dict) and m.get("name")
        }
    except Exception as e:
        print(f"⚠️ [Boot] Could not query installed Ollama models at {base_url}: {e}")
        return

    for model_name in models:
        if model_name in installed or f"{model_name}:latest" in installed:
            continue
        print(f"📥 [Boot] Pulling missing model '{model_name}' into Ollama ({base_url})...")
        try:
            pull_resp = requests.post(
                f"{base_url}/api/pull",
                json={"name": model_name, "stream": False},
                timeout=1800,
            )
            if pull_resp.status_code == 200:
                print(f"✅ [Boot] Model '{model_name}' is ready.")
            else:
                print(f"⚠️ [Boot] Ollama pull for '{model_name}' returned HTTP {pull_resp.status_code}: {pull_resp.text[:200]}")
        except Exception as e:
            print(f"⚠️ [Boot] Failed to pull model '{model_name}': {e}")


def ensure_ollama_running():
    """Checks if Ollama is running, starts it if running natively on localhost, and auto-pulls models when OLLAMA_AUTO_PULL=true."""
    base_url = get_ollama_base_url()
    print(f"⚙️ [Boot] Checking Ollama server status at {base_url}...")
    try:
        requests.get(f"{base_url}/", timeout=3)
        print("🟢 [Boot] Ollama is already running.")
    except requests.exceptions.RequestException:
        is_local = (
            any(h in base_url for h in ("127.0.0.1", "localhost", "0.0.0.0"))
            and not os.path.exists("/.dockerenv")
        )
        if is_local and shutil.which("ollama"):
            print("🔴 [Boot] Ollama is offline. Starting background service...")
            popen_kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            subprocess.Popen(["ollama", "serve"], **popen_kwargs)
            time.sleep(4)
            print("🟢 [Boot] Ollama server successfully started.")
        else:
            print(
                f"⚠️ [Boot] Could not reach Ollama at {base_url}. "
                "Ensure the 'ollama' container service or host server is running."
            )
            return

    if os.getenv("OLLAMA_AUTO_PULL", "").strip().lower() in ("1", "true", "yes", "on"):
        ensure_ollama_models(base_url)


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

    # 3. Strip leaked tool-instruction footers and internal [SYSTEM INSTRUCTION ...] blocks
    text = text.split("\n\n[SYSTEM INSTRUCTION")[0]
    text = _LEAKED_TOOL_FOOTER.sub('', text)

    # 4. Clean up any trailing empty separator dashes
    text = re.sub(r'\n*---\s*$', '', text)

    return text.strip()

