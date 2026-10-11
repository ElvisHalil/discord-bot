"""
TEMPLATE SKILL (TOOL)

In Agentic AI, a "skill" or "tool" is a python function that the LLM can decide to run on its own. 
The LLM figures out *when* to use it by reading the function's name and its docstring (the text in the triple quotes).
"""

def my_example_skill(target_name: str) -> str:
    """
    Briefly describe what this tool does so the AI knows when to use it.
    For example: "Generates a friendly greeting for a specific person."
    """
    # 1. Provide a print statement so you can see it working in the terminal
    print(f"🛠️ [Running Example Skill] Target: {target_name}")
    
    # 2. Write your custom Python logic here (API calls, file reading, calculations, etc.)
    greeting = f"Hello there, {target_name}! I am a custom skill."
    
    # 3. Always return a string so the AI can read the output and summarize it for the user
    return greeting

# ==========================================
# HOW TO ENABLE THIS SKILL:
# ==========================================
# 1. Import it in `src/config.py` (e.g., `from src.tools.template_skill import my_example_skill`)
# 2. Add it to the tool dictionary of your choice in `src/config.py`:
#    "tools": {
#        ...
#        "my_example_skill": my_example_skill
#    }

