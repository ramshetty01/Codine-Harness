# Python SDK and local RPC, version 1

The SDK controls a persistent Codine process. It does not implement another model
loop. The child runs the same CLI startup, Agent, tool registry, permissions,
session store and shared OpenRouter accounting as an ordinary terminal session.
The existing `codine --json --prompt ...` one-shot JSONL interface is unchanged.

## Python client

```python
from codine.sdk import Client

def show(event):
    if event['event'] == 'text':
        print(event['data'], end='', flush=True)

def approve(description):
    print(description)
    return input('Approve this action? [yes/no] ').strip() == 'yes'

with Client('/absolute/project/path', provider='openrouter',
            model='YOUR_EXPLICIT_MODEL_ID', on_event=show, approve=approve) as codine:
    print(codine.ready['session'])
    result = codine.run('Inspect the project and explain its test command.')
    print(result['outcome'])
    print(codine.status())
    codine.command('/save')
```

Credentials stay in the existing shell/configuration path. The RPC interface has
no method for accepting keys, changing endpoints or resetting allowances. New
requests use the same reservation, settlement and unknown-cost blocking as CLI
requests. An SDK request timeout does **not** mean the provider charged nothing.
Inspect status and saved session state before retrying.

No approval callback means decline. The callback must return the literal boolean
`True` to approve; exceptions and other values decline. Decisions apply only to
the current approval token, not later actions. A caller can explicitly change
ordinary edit policy with `/permissions`, subject to the same approval as the
terminal. Planning restrictions remain enforced by the existing registry.

`run(text, timeout=180)` returns the durable run outcome, not a claim that tests
passed. Text/tool events go to `on_event`. `command(text)` executes a supported
slash command at an idle boundary. `status()` and `cancel()` can be called from
another thread while a run waits. Agent turns are serialized; concurrent run
requests are queued, not parallel agents. `close()` requests cancellation and
shutdown, waits up to five seconds by default, then kills the child process group
if necessary. Abrupt shutdown can leave uncertain outcomes/charges for normal
session recovery; nothing is automatically replayed.

Resume by constructing `Client(project, resume=SESSION_ID)`; the saved provider,
model and execution settings follow CLI validation. Reconnect explicitly to
restart or upgrade: RPC declines model-requested process restart and does not
expose the terminal `/restart` command. Explicit remote model changes require an
exact model ID; terminal model/session pickers are not invoked over RPC.

The optional `command=[EXECUTABLE, ...]` constructor argument is a **trusted host
launch vector**, useful for an alternate Python environment or scripted testing.
It is not a tool or a model-controlled parameter. The default uses the current
Python interpreter and this installed/check-out Codine package against the chosen
project directory. Provider/executor/model/resume options are passed as literal
arguments, never shell text.

## Wire protocol

Start `codine --rpc --project /absolute/project`. This is local stdin/stdout IPC,
not an authenticated network service. Keep stdin open while using the connection.
Do not expose it as a public socket. `--rpc` cannot combine with `--json`,
`--prompt`, `--check` or migration modes. Stdout contains versioned JSON lines;
implementation stderr is separate.

Every request has exactly four fields, with a unique printable ASCII string ID:

```json
{"version":1,"id":"run-1","method":"run","params":{"text":"Explain the test layout"}}
{"version":1,"id":"status-1","method":"status","params":{}}
{"version":1,"id":"cancel-1","method":"cancel","params":{}}
{"version":1,"id":"save-1","method":"command","params":{"text":"/save"}}
```

Responses have `version`, `id`, and either `result` or `error.message`. Events have
`version`, increasing `sequence`, `event`, `request_id` and `data`. Startup emits
`ready` with the project/session/model and supported method names. A run response
arrives after the CLI has finished the turn and its event callbacks, before the
next queued request starts. A failed provider/tool lifecycle retains the same
structured `run_end` event and recovery state as the terminal.

Approvals are bidirectional: the server emits an `approval` event containing a
fresh token, a redacted description and timeout. The client sends:

```json
{"version":1,"id":"approve-1","method":"approval","params":{"token":"TOKEN_FROM_EVENT","allow":false}}
```

No answer within 60 seconds declines. Cancellation or disconnect also declines.
Expired, duplicate or mismatched tokens never grant another action. The reader
handles approval replies and cancellation while the CLI owner thread is waiting;
there is no competing terminal input reader.

`cancel` targets the active request and does not discard queued commands. `close`
requests shutdown, cancels active work and reports queued requests as unstarted.
EOF has shutdown semantics; it is not a way to submit a batch and wait for all
queued work. There are no automatic command retries or reconnects.

## Boundaries and recovery

- Frames: at most 64 KiB, newline terminated; duplicate JSON fields and nonfinite
  numbers are rejected. Oversized/incomplete input disconnects and cancels.
- Tasks/commands: 1–16,000 characters; 16 queued requests; 4,096 unique IDs per
  connection. Duplicate IDs are rejected rather than executed twice.
- Outgoing transport: 256 queued frames. Oversized events carry an explicit
  `truncated` marker and preview; the saved session/tool artifacts remain the
  authoritative full record. A stalled consumer stops the transport.
- SDK: 32 outstanding requests, 512 pending events. It drains stderr without
  exposing raw contents through exceptions. Transport failure closes stdin to
  request cancellation; errors never imply safe replay.
- Only the CLI owner thread changes sessions, tools and permissions. The input
  thread queues commands, delivers a matching approval, sets cancellation, or
  reads the same in-memory status snapshot used by the terminal.
- RPC does not provide exactly-once delivery across crashes. If a client loses a
  response after a write, inspect the saved tool result/checkpoint before retrying.
  Request IDs prevent duplicate execution only inside the live connection.

Supported slash commands are the explicit `COMMANDS` list in `rpc_terminal.py`.
Interactive pickers, paste/external-editor flows, templates/draft editing and
process restart are intentionally not wire methods. New integrations should add
an explicit bounded method or supported command rather than drive terminal
keystrokes. SDK callbacks run in the waiting caller thread and should return
promptly; a blocking user callback can delay that caller independently of the
server's approval timeout. An event callback exception requests cancellation.

## Offline evidence

`test_sdk_rpc` launches actual child processes with scripted providers and blocked
networking. It checks persistence/resume, default denial, explicit write approval,
plan mode, cancellation followed by another turn, command validation, shared-cost
settlement and unknown-cost blocking. `test_rpc_protocol` checks duplicate IDs and
JSON keys, queue/frame bounds, expired approvals, redaction, disconnect and output
truncation. These tests establish transport/accounting behavior, not model quality.
