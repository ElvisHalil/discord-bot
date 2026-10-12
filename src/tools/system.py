import os
import json
import numexpr as ne
from datetime import datetime

ACTIVE_MEMORY_FILE = "data/memory.json"

def get_current_datetime() -> str:
    """Get the exact current date, day of the week, and local time from the host machine."""
    now = datetime.now()
    result = now.strftime("Current Date: %A, %B %d, %Y | Local Time (UTC-6): %I:%M:%S %p")
    print(f"🕒 [Checking Clock]: {result}")
    return result

def evaluate_math(expression: str) -> str:
    """Evaluate an exact mathematical expression (e.g., 25 * 4 + 100)."""
    print(f"🧮 [Calculating]: {expression}")
    try:
        result = ne.evaluate(expression)
        return str(result)
    except Exception as e:
        return f"Math error: {e}"

def save_memory(fact: str) -> str:
    """Save a permanent fact, preference, or rule about the user."""
    print(f"💾 [Saving Memory to {ACTIVE_MEMORY_FILE}]: {fact}")
    memories = []
    if os.path.exists(ACTIVE_MEMORY_FILE):
        try:
            with open(ACTIVE_MEMORY_FILE, "r") as f:
                memories = json.load(f)
        except Exception:
            memories = []
    
    memories.append(fact)
    os.makedirs(os.path.dirname(ACTIVE_MEMORY_FILE), exist_ok=True)
    with open(ACTIVE_MEMORY_FILE, "w") as f:
        json.dump(memories, f, indent=2)
    return "Memory successfully saved."

def recall_memories() -> str:
    """Recall all permanently saved memories and facts about the user."""
    print(f"🧠 [Recalling Memories from {ACTIVE_MEMORY_FILE}]")
    if os.path.exists(ACTIVE_MEMORY_FILE):
        try:
            with open(ACTIVE_MEMORY_FILE, "r") as f:
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

