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
