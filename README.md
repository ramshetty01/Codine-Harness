# Codine-Harness

A small Python terminal coding harness built step by step. OpenRouter is the only provider, using `particle-ai/particle-1:free` by default.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

For a new checkout, copy `.env.example` to `.env` and add your OpenRouter API key. Keep your existing `.env` when updating an installation.

```dotenv
OPENROUTER_API_KEY=your-key
OPENROUTER_MODEL=particle-ai/particle-1:free
CODINE_MAX_TOKENS=4096
```

Run `python main.py`. Type `exit` to quit. Ctrl+C cancels an active turn or exits at the input prompt. `.env` and `.venv` are excluded from Git.

## How it works

`main.py` uses the OpenAI-compatible Python SDK with `base_url="https://openrouter.ai/api/v1"` and your **OpenRouter** key. No OpenAI account or key is needed. There is no separate API URL setting.

1. Your message is appended to in-memory conversation history.
2. OpenRouter returns an answer or structured tool requests.
3. Python runs the requested tools and appends their results.
4. The model receives the updated history and continues, up to five API calls per turn.

Provider fields such as `reasoning_details` are preserved in assistant history. Reasoning is enabled, and the output budget defaults to 4,096 tokens. Replies are non-streaming. Model availability, free quotas, and latency depend on OpenRouter and the upstream provider.

## Tools

- `list_project_files`: lists visible files and folders beside `main.py`.
- `read_project_file`: reads visible, top-level `.py` files only. Rejects symlinks and paths outside that directory, and returns at most 12,000 characters plus a truncation marker.

File results are sent to OpenRouter on the next model request. Keep secrets out of exposed Python files. This harness has no shell execution or file editing tool.

## Project files

- `main.py`: configuration, tools, model loop, and terminal input.
- `requirements.txt`: the SDK and `.env` loader.
- `.env.example`: credential-free configuration template.
- `.gitignore`: excludes credentials and local generated files.

Conversation history disappears on exit and is not automatically trimmed. Connection failures, rate limits, and HTTP errors return you to the prompt; failed turns are removed from history so you can retry. The SDK has a 120-second network timeout and automatic retries are disabled.

## Learning history

The first six commits organize earlier lessons: setup, NVIDIA requests, Groq streaming, conversation history, file tools, and debug output. Those initial milestones were reconstructed together. The old provider experiments remain available in Git history; the current folder contains only the OpenRouter implementation. Each new milestone is committed separately.

This is a learning project, not a production coding agent.
