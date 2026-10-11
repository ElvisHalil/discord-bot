import ollama
import subprocess
import os

print("1. Downloading official Llama 3.1 to harvest the tool template...")
ollama.pull('llama3.1:8b')
info = ollama.show('llama3.1:8b')
official_template = info['template']

print("2. Ensuring Dolphin 3 is downloaded...")
ollama.pull('dolphin3:8b')

print("3. Writing temporary Modelfile to disk...")
modelfile_content = f"""FROM dolphin3:8b
TEMPLATE \"\"\"
{official_template}
\"\"\"
"""

# Save the Modelfile to the current directory
with open("Modelfile", "w", encoding="utf-8") as f:
    f.write(modelfile_content)

print("4. Fusing the template onto Dolphin 3 via Ollama CLI...")
# Run the native terminal command to build the model
subprocess.run(["ollama", "create", "dolphin3-tools", "-f", "Modelfile"], check=True)

# Clean up the temporary file
if os.path.exists("Modelfile"):
    os.remove("Modelfile")

print("✅ Success! 'dolphin3-tools' has been created with perfect tool support.")