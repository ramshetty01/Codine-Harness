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

print("Codie — type 'exit' to quit.")

while True:
    message = input("\nYou: ").strip()

    if message.lower() == "exit":
        break

    if not message:
        continue

    completion = client.chat.completions.create(
        model="qwen/qwen3.8-27b",
        messages=[
            {
                "role": "system",
                "content": "You are Codie, a coding assistant. Reply in English."
            },
            {
                "role": "user",
                "content": message
            }
        ],
        max_completion_tokens=2048,
        stream=True,
    )

    print("Codie: ", end="", flush=True)

    for chunk in completion:
        if chunk.choices:
            text = chunk.choices[0].delta.content or ""
            print(text, end="", flush=True)

    print()