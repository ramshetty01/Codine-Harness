from groq import Groq
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

client = Groq(api_key=api_key)

# Keep the conversation between turns for this run of the program.
messages = [
    {
        "role": "system",
        "content": "You are Codine, a coding assistant. Reply in English."
    }
]

print("Codie — type 'exit' to quit.")

while True:
    message = input("\nYou: ").strip()
    if message.lower() == "exit":
        break
    if not message:
        continue

    messages.append({"role": "user", "content": message})
    completion = client.chat.completions.create(
        model="qwen/qwen3.8-27b",
        messages=messages,
        max_completion_tokens=512,
        stream=True,
    )

    print("Codine: ", end="", flush=True)
    answer = ""
    for chunk in completion:
        if chunk.choices:
            text = chunk.choices[0].delta.content or ""
            answer += text
            print(text, end="", flush=True)
    print()
    messages.append({"role": "assistant", "content": answer})
