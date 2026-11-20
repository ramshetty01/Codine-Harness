# Codine-Harness

A Python terminal coding harness built step by step. The active provider is NVIDIA NIM, using `nvidia/nemotron-3.5-super-vl-120b-a12b` by default.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

For a new checkout, copy `.env.example` to `.env` and add your NVIDIA API key. Keep your existing `.env` when updating.

```dotenv
NVIDIA_API_KEY=your-key
NVIDIA_MODEL=nvidia/nemotron-3.5-super-vl-120b-a12b
CODINE_MAX_TOKENS=16384
```

Run `python main.py`. The default output cap is 16,384 tokens; an existing `CODINE_MAX_TOKENS` setting takes precedence. This uses the OpenAI-compatible SDK with NVIDIA's endpoint (`https://integrate.api.nvidia.com/v1`), not the OpenAI or OpenRouter service.

If NVIDIA returns 401, check your NVIDIA key. Shell variables take precedence over `.env`; run `unset NVIDIA_API_KEY` before restarting if your shell holds an old key. Credentials are excluded from Git.

## Commands

- `/model`: show the active NVIDIA model. Configure `NVIDIA_MODEL` in `.env` and restart to change it. Other models may require different thinking settings.
- `/clear`: clear chat history while retaining system instructions. Saved edits remain on disk.
- `/help`: display commands.
- `exit` or `/quit`: quit.

Ctrl+C cancels an active turn or exits at the input prompt. The OpenRouter popup module is retained as an earlier learning example, but is not used by this NVIDIA-only entry point.

## Model loop

Your message is appended to history and sent to NVIDIA with the tool definitions. Reasoning and answer text stream to the terminal with separate labels. The harness collects fragmented tool names and JSON arguments before executing anything. Multiple tool requests are supported; local actions execute sequentially so edit approvals remain clear.

The request follows the NVIDIA sample: temperature 1, top_p 0.95, seed 42, parallel tool calls enabled, and `chat_template_kwargs.enable_thinking=true`. Streamed reasoning is preserved as `reasoning_content` in assistant history. Usage is printed when the provider reports it, along with total request time.

The loop permits up to five model calls per user message. Incomplete or truncated tool requests are rejected. Connection failures, quota errors, and interrupted streams return to the prompt; failed turns are removed from history. Already approved file edits are not undone. The client has a 120-second network timeout and automatic retries are disabled.

## Tools

- `list_project_files`: list visible names beside `main.py`.
- `read_project_file`: read a visible, top-level `.py` file, up to 12,000 characters. Rejects symlinks and paths outside that directory.
- `edit_project_file`: replace one exact text occurrence in an existing, visible, top-level Python file. Shows a diff and saves only when you type `y`. Rejects ambiguous matches and files changed while awaiting approval.
- `check_python_syntax`: parse a visible, top-level Python file without executing it and report syntax errors with line numbers.

Tool results go to NVIDIA on the next model request. Keep secrets out of exposed Python files. There is no shell execution tool. Syntax checking does not prove runtime correctness. Editing `main.py` affects the next run, not the current process.

## Checks and learning history

```bash
python -m unittest discover -s tests -v
```

Tests cover fragmented streaming tool calls, reasoning and usage accumulation, interrupted streams, tool result history, and the earlier model-picker UI.

Earlier commits cover NVIDIA experiments, Groq chat, OpenRouter, conversation history, file tools, approval, syntax checks, and the free-model picker. Each milestone is committed separately. The current entry point targets NVIDIA; `model_picker.py` remains a standalone OpenRouter learning example.

Conversation history disappears on exit and is not automatically trimmed. This is a learning project, not a production coding agent. The terminal currently accepts text prompts; the sample image question was not hardcoded into every conversation.
