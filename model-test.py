import os
import requests

api_key = os.environ["NVIDIA_API_KEY"]

response = requests.post(
    "https://integrate.api.nvidia.com/v1/chat/completions",
    headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    },
    json={
        "model": "moonshotai/kimi-k3.1-pro",
        "messages": [
            {"role": "user", "content": "Explain a Python function in two sentences."}
        ],
        "max_tokens": 1024,
        "stream": False,
        "reasoning_effort": "low",
    },
    timeout=120,
)

response.raise_for_status()

data = response.json()
print(data["choices"][0]["message"]["content"])