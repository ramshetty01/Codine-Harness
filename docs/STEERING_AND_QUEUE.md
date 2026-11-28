# Steering and queued follow-ups

While Codine is working, the `busy ›` editor remains available. Ordinary text
queues a separate follow-up task. `/steer TEXT` supplies an urgent correction to
the current task. Enter sends; Alt+Enter and bracketed paste keep multiline input.
The toolbar shows BUSY or STOPPING and the queue count.

- `/queue` lists pending IDs, lanes and short text previews.
- `/drop ID` removes one pending item; an unambiguous eight-character prefix works.
- `/stop` or Ctrl+C in the busy editor requests cancellation. Unsent Ctrl+C draft
  text is retained. Pending tasks remain queued; `/next` resumes them explicitly.
- `/next` at the ordinary prompt releases recovered/paused inputs and sends the
  next urgent item first, otherwise the oldest follow-up.
- While an approval is displayed, its input belongs exclusively to the approval
  reader. Decline or cancel it before entering new steering. Queued text never
  grants approval. Other slash commands (including `/jobs`) submitted while busy
  are retained in a separate, 16-command in-memory FIFO and dispatched by the main
  thread after the busy reader stops. They take priority over starting another
  follow-up; they never run on the input thread or become model messages. Commands
  still use their ordinary approval rules. This FIFO is not replayed after a crash;
  pending command text is limited to 16,000 characters per entry.

## Safe boundaries

The input reader runs in one thread. It writes only a separate, locked inbox;
it never changes conversation history or executes tools. The agent and all tools
remain on the main thread. Before an approval, the busy reader is stopped and
joined, retaining its draft. Only then does the approval reader start. Failure to
stop the reader terminates the interactive flow rather than opening another reader.

A model request already in progress may finish before steering takes effect.
Steering does not reuse partial streaming tool arguments. If a complete response
contains tool calls based on the old request, unstarted calls receive explicit
`skipped` tool results. If a tool was already underway, its actual result is saved
first; it is not replayed. All calls in the assistant batch receive matching results
before the new user message is appended and the next model request begins. The
normal step limit and context/billing limits still apply.

`/stop` is checked by stream callbacks and at agent boundaries. Foreground host,
Docker and discovered-check commands receive the cancellation event and clean up
their process tree/container. Synchronous filesystem operations finish their safe
operation before cancellation is observed. A provider request producing no stream
callbacks may remain blocked until it returns or reaches its configured timeout.
An interrupted provider request can have unknown cost; its reservation stays
blocked until reconciled. Cancellation cannot retract a completed side effect.
Background jobs have their own `/cancel ID` command.

Follow-ups run as separate turns only after a normally completed, saved current
turn. Errors, cancellation, step/output limits, restart or unknown billing stop
automatic progression. Follow-ups remain pending. A recovered queue never runs
merely because the process started or an unrelated new question completed; review
`/queue` and use `/next`. Urgent messages submitted during a new active turn can
still run while older recovered entries remain deferred.

## Persistence and limits

Each session has an owner-private `<session-id>.inbox` sidecar next to its JSON
snapshot. It contains at most 32 combined pending items, each at most 16,000
characters, with a 600,000-byte total ceiling. Credentials known to the redactor
are removed before saving. Invalid, linked, oversized or failed queue storage is
rejected. The active SessionStore lock excludes another process using that session.

Delivery saves the user message and its queue receipt in one session snapshot,
then removes the inbox item. A crash between those writes is reconciled from the
receipt, so the same queued item is not automatically delivered twice. Receipts
are bounded to 128 IDs; the queue cannot advance past failed acknowledgment.
Delivery means recorded in conversation, not successfully answered: recovery
never silently retries a provider request or underway action. The full archive
retains delivered text; the queue is not a second transcript.

Switching sessions keeps each inbox separate. Session migration to a new ID does
not implicitly transfer queued work. Noninteractive runs have no background stdin
reader. There is no inference used to operate or inspect the queue.

## Verification

`test_steering.py` uses controlled providers for in-flight steering, tool-pair
validity, one-time side effects, receipt crash recovery, queue/session save failure,
limits, redaction and uncertain-cost blocking. `test_steering_terminal.py` uses a
real PTY for busy typing, separate follow-up delivery, approval handoff and stopping
a real foreground subprocess while preserving a queued follow-up. No paid APIs.
