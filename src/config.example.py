"""
Blank-Slate Example Configuration (`src/config.example.py`).

Copy this file to `src/config.py` to configure your own Ollama models,
system prompts, context limits, and persona profiles:
    cp src/config.example.py src/config.py

`src/config.py` is git-ignored so your custom prompts and local model tags
remain private.
"""

import src.registry as tools
from src.utils.timezones import get_local_now


def _build_research_prompt(facts: str) -> str:
    """Example system prompt for the tool-enabled research assistant mode."""
    now_str = get_local_now().strftime("%A, %B %d, %Y")
    return (
        f"Today is {now_str}. You are a helpful research assistant with access to live tools.\n"
        "Use `skill_markets_info` for stocks, global indices (TSX, S&P 500, NASDAQ, Europe, Asia), commodities, crypto, and historical returns.\n"
        "Use `skill_graphing_info` ON DEMAND to plot/graph/diagram data, tickers, heatmaps, Monte Carlo simulations (`chart_type='monte_carlo'`), or workflows, `search_images` to find and vision-verify web pictures/photos in memory, and `skill_voice_info` to join/leave/speak/recap voice calls.\n\n"
        f"Known user context:\n{facts}"
    )


def _build_chat_prompt(facts: str) -> str:
    """Example system prompt for a casual conversational persona mode."""
    now_str = get_local_now().strftime("%A, %B %d, %Y")
    return (
        f"Today is {now_str}. You are a casual, friendly conversational companion.\n\n"
        f"Known user context:\n{facts}"
    )


MODES = {
    "research": {
        "model": "gemma4:e4b",
        "memory_file": "data/research_memory.json",
        "max_iterations": 10,
        "is_research": True,
        "history_limit": 40,
        "supports_tools": True,
        "tools": tools.RESEARCH_TOOLS,
        "ollama_options": {
            "num_ctx": 16384,
            "num_predict": 4096,
            "temperature": 0.7,
        },
        "get_prompt": _build_research_prompt,
    },
    "chat": {
        "model": "qwen3:8b",
        "memory_file": "data/chat_memory.json",
        "max_iterations": 4,
        "is_research": False,
        "history_limit": 40,
        "supports_tools": True,
        "tools": tools.CHAT_TOOLS,
        "ollama_options": {
            "num_ctx": 8192,
            "num_predict": 1024,
            "temperature": 0.8,
        },
        "get_prompt": _build_chat_prompt,
    },
}
