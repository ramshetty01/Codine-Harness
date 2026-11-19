from groq import Groq
import json
import os
from pathlib import Path

env_path = Path(__file__).parent / ".env"
if not env_path.exists():
    raise FileNotFoundError(f"Environment file not found: {env_path}")

with env_path.open() as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")

api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    raise RuntimeError("Set GROQ_API_KEY in .env")

def list_project_files():
    project_dir = Path(__file__).resolve().parent

    names = [
        item.name
        for item in project_dir.iterdir()
        if not item.name.startswith(".")
    ]

    return "\n".join(sorted(names))

def read_project_file(filename):
    project_dir = Path(__file__).resolve().parent

    if not isinstance(filename, str):
        return "Error: filename must be a string."

    # For this first version, allow only top-level Python files.
    if Path(filename).name != filename:
        return "Error: provide a filename without folders."

    if filename.startswith(".") or not filename.endswith(".py"):
        return "Error: only visible Python files are allowed."

    file_path = project_dir / filename

    if file_path.is_symlink():
        return "Error: symbolic links are not allowed."

    if not file_path.is_file():
        return "Error: file not found."

    try:
        with file_path.open(encoding="utf-8") as file:
            content = file.read(12_001)
    except (OSError, UnicodeError) as error:
        return f"Error reading file: {error}"

    if len(content) > 12_000:
        return content[:12_000] + "\n[File truncated]"

    return content

client = Groq(api_key=api_key)

# Keep the conversation between turns for this run of the program.
messages = [
    {
        "role": "system",
        "content": "You are Codine, a coding assistant. Reply in English."
    }
]

print("Codie — type 'exit' to quit.")

tools = [
    {
        "type": "function",
        "function": {
            "name": "list_project_files",
            "description": (
                "List the names of visible files and folders "
                "in the project's top-level directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_project_file",
            "description": "Read a visible Python file in the project's top-level directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "The filename, for example main.py"
                    }
                },
                "required": ["filename"]
            }
        }
    }
]

while True:
    message = input("\nYou: ").strip()
    print("\n[Debug] Sending user message:", repr(message))

    if message.lower() == "exit":
        break

    if not message:
        continue

    messages.append({"role": "user", "content": message})

    # Allow multiple tool steps, but cap API calls per user message.
    for step in range(5):
        completion = client.chat.completions.create(
            model="qwen/qwen3.8-27b",
            messages=messages,
            tools=tools,
            max_completion_tokens=1000,
            stream=False,
        )

        reply = completion.choices[0].message
        messages.append(reply.model_dump(exclude_none=True))

        if not reply.tool_calls:
            print("Codine:", reply.content or "")
            break

        for call in reply.tool_calls:
            print(f"[Tool requested] {call.function.name}")

            try:
                arguments = json.loads(call.function.arguments)
                if not isinstance(arguments, dict):
                    raise TypeError("arguments must be a JSON object")

                if call.function.name == "list_project_files":
                    result = list_project_files(**arguments)
                elif call.function.name == "read_project_file":
                    result = read_project_file(**arguments)
                else:
                    result = "Error: unknown tool."
            except (json.JSONDecodeError, TypeError, ValueError, OSError) as error:
                result = f"Error executing tool: {error}"

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": result,
            })

    else:
        print("Codine: Reached the 5-step limit. Send another message to continue.")

print("[Debug] Finish reason:", completion.choices[0].finish_reason)