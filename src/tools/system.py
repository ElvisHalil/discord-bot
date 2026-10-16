import os
import json
import numexpr as ne
from datetime import datetime

from src.orchestrator import build_reference_clock

ACTIVE_MEMORY_FILE = "data/memory.json"

def set_memory_file(path: str) -> None:
    """Point save_memory/recall_memories at a mode-specific memory file.
    Must be called through this module: rebinding ACTIVE_MEMORY_FILE on another
    module that imported it (e.g. the registry) has no effect here."""
    global ACTIVE_MEMORY_FILE
    ACTIVE_MEMORY_FILE = path

def get_current_datetime() -> str:
    """Get the exact current date, day of the week, and time across Local, US, Europe, and UTC timezones."""
    result = build_reference_clock()
    print(f"🕒 [Checking Clock]: {result}")
    return result

def evaluate_math(expression: str) -> str:
    """Evaluate an exact mathematical expression (e.g., 25 * 4 + 100).

    Args:
        expression: The arithmetic expression to evaluate, using numbers and operators only.
    """
    print(f"🧮 [Calculating]: {expression}")
    try:
        result = ne.evaluate(expression)
        return str(result)
    except Exception as e:
        return f"Math error: {e}"

def save_memory(fact: str) -> str:
    """Save a permanent fact, preference, or rule about the user.

    Args:
        fact: One short, self-contained statement to remember about the user.
    """
    print(f"💾 [Saving Memory to {ACTIVE_MEMORY_FILE}]: {fact}")
    memories = []
    if os.path.exists(ACTIVE_MEMORY_FILE):
        try:
            with open(ACTIVE_MEMORY_FILE, "r", encoding="utf-8") as f:
                memories = json.load(f)
        except Exception:
            memories = []
    
    memories.append(fact)
    os.makedirs(os.path.dirname(ACTIVE_MEMORY_FILE), exist_ok=True)
    with open(ACTIVE_MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memories, f, indent=2)
    return "Memory successfully saved."

def recall_memories() -> str:
    """Recall all permanently saved memories and facts about the user."""
    print(f"🧠 [Recalling Memories from {ACTIVE_MEMORY_FILE}]")
    if os.path.exists(ACTIVE_MEMORY_FILE):
        try:
            with open(ACTIVE_MEMORY_FILE, "r", encoding="utf-8") as f:
                memories = json.load(f)
            if memories:
                return "Known facts about the user:\n" + "\n".join(f"- {m}" for m in memories)
        except Exception:
            pass
    return "No memories saved yet."

SYSTEM_TOOLS = {
    "get_current_datetime": get_current_datetime,
    "evaluate_math": evaluate_math,
    "save_memory": save_memory,
    "recall_memories": recall_memories,
}

