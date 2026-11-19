# Codine-Harness

A small Python terminal coding harness built step by step to learn model APIs, conversation history, and local tools.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Put your Groq API key in `.env` as `GROQ_API_KEY=your-key`, then run `python main.py`. Type `exit` to quit. `.env` and `.venv` are ignored by Git. Never put credentials in Python source files.

The scripts use `qwen/qwen3.8-27b` from the author's Groq console. Model availability and rate limits depend on your account; update the model ID if needed. The main harness requests up to 1,000 completion tokens. Lower this if your account's output-token limit rejects a request.

## Learning milestones

The initial commits organize the existing learning work into milestones; they were prepared together rather than recorded when each lesson happened.

1. Project setup, dependencies, and credential exclusions.
2. `model-test.py`: a first NVIDIA request using `requests`. This script reads `NVIDIA_API_KEY` from the terminal environment, not `.env`.
3. `groq-test.py`: streaming Groq replies in a terminal loop without conversation history.
4. `main.py`: conversation history stored in a Python list for the current run.
5. Local tools: list project files, read a Python file, and return results to the model.
6. Debug output: report the finish reason immediately after each model response.

Future learning changes will be committed separately as they are implemented and checked.

## How it works

Terminal input is appended to history and sent to the model. The model responds with text or requests a tool. Python runs the requested tool and sends the result back for the model's next step. The tool loop permits up to five API calls per user message.

`list_project_files` lists visible names in this directory. `read_project_file` reads only visible, top-level `.py` files, rejects symlinks, and returns at most 12,000 characters plus a truncation marker. File contents returned by a tool are sent to Groq on the next request. Do not store secrets in files you expose to the model.

## Current limits

- Conversation history disappears on exit and has no context trimming.
- The main tool loop uses non-streaming responses for clarity.
- There is no shell execution or file editing tool yet.
- API errors and Control+C currently end the program; friendly error handling is a future lesson.

This is a learning project, not a production coding agent.
