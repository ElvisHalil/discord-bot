import os
import json
import threading
from contextvars import ContextVar
from typing import Optional
import numexpr as ne
from datetime import datetime

from src.utils.timezones import build_reference_clock

ACTIVE_MEMORY_FILE = "data/memory.json"
_LAST_SET_MEMORY_FILE = ACTIVE_MEMORY_FILE
_ACTIVE_MEMORY_FILE_VAR: ContextVar[Optional[str]] = ContextVar("ACTIVE_MEMORY_FILE", default=None)
_MEMORY_LOCK = threading.RLock()


def set_memory_file(path: str) -> None:
    """Point save_memory/recall_memories at a mode-specific memory file.
    Must be called through this module: rebinding ACTIVE_MEMORY_FILE on another
    module that imported it (e.g. the registry) has no effect here."""
    global ACTIVE_MEMORY_FILE, _LAST_SET_MEMORY_FILE
    ACTIVE_MEMORY_FILE = path
    _LAST_SET_MEMORY_FILE = path
    _ACTIVE_MEMORY_FILE_VAR.set(path)


def _get_active_memory_file() -> str:
    # If a unit test mutated ACTIVE_MEMORY_FILE directly without set_memory_file(), honor it.
    if ACTIVE_MEMORY_FILE != _LAST_SET_MEMORY_FILE:
        return ACTIVE_MEMORY_FILE
    return _ACTIVE_MEMORY_FILE_VAR.get() or ACTIVE_MEMORY_FILE


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
    mem_file = _get_active_memory_file()
    print(f"💾 [Saving Memory to {mem_file}]: {fact}")
    with _MEMORY_LOCK:
        memories = []
        if os.path.exists(mem_file):
            try:
                with open(mem_file, "r", encoding="utf-8") as f:
                    memories = json.load(f)
            except Exception:
                memories = []

        memories.append(fact)
        os.makedirs(os.path.dirname(mem_file) or ".", exist_ok=True)
        with open(mem_file, "w", encoding="utf-8") as f:
            json.dump(memories, f, indent=2)
    return "Memory successfully saved."

def recall_memories() -> str:
    """Recall all permanently saved memories and facts about the user."""
    mem_file = _get_active_memory_file()
    print(f"🧠 [Recalling Memories from {mem_file}]")
    with _MEMORY_LOCK:
        if os.path.exists(mem_file):
            try:
                with open(mem_file, "r", encoding="utf-8") as f:
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

