# Conversation rewind versus file rollback

Codine keeps these actions separate:

| Command | Changes | Preserves |
| --- | --- | --- |
| `/rewind-context TURN` | Creates and activates a new child conversation ending after a selected completed user turn | Parent archive, current project files, shared project memory, current spending and usage |
| `/fork [TITLE]` | Creates and activates a child at the current conversation tip | Current active context/summary, files, accounting |
| `/rewind CHANGESET` | Reviews and restores a supported group of file-tool changes | Conversation history and accounting |
| `/undo [CHECKPOINT]` | Reviews and restores a supported single-file checkpoint | Conversation history and accounting |

**None of these commands undoes arbitrary shell commands, network requests,
purchases, API calls, or other external effects.** A historical conversation can
mention a file's old content while the current file has changed. Read the current
file before editing it; historical tool results do not grant edit permission.

## Choose a conversation boundary

```text
/rewind-context
/rewind-context 2
```

The first command lists up to 50 recent completed user turns, their absolute turn
numbers, retained message endpoints, and short request previews. An earlier known
turn number remains selectable. Only a turn ending with a final assistant message
without pending tool calls can be selected. A tool-call batch or its results
alone is not a completed user turn. No boundary splits tool calls from their
results, and no final answer is invented for recovered/incomplete work.

The second command previews the parent session, selected endpoint and number of
later messages excluded, then asks for explicit approval. Declining leaves the
active session unchanged. A noninteractive terminal cannot provide the required
approval. Approval is bound to a hash of the saved semantic session state,
including history, context settings, plan and accounting (excluding only the
save-time `updated` timestamp). If that state changes while the preview is open,
Codine refuses and asks you to review again.

On success, the child has a new session ID and ordinary fork ancestry recording
the parent, root, depth, selected message count and historical-prefix hash.
`/tree` shows the child and parent; `/tree PARENT_ID` or `/resume PARENT_ID` returns
to the original conversation. The original archived messages are not truncated.
Nothing is sent to a provider, and tools are not replayed by navigation.

## Context and accounting

The child starts with the system message and the full retained conversation
prefix active. It clears its summary and plan because those can contain claims
or tasks from later turns. The parent's summary and plan remain in its archive.
Current project guidance and reviewed project memory remain shared and can still
contain more recent information: this feature is not a time machine for project
state or user configuration. Request context limits still apply on the next turn;
rewinding does not increase the model's context window.

Current settled local spending and usage are inherited, even when those costs
were incurred after the selected turn. They are not restored to historical lower
amounts. The same durable shared spending ledger and key guard remain in force.
Settled request ownership references are cleared in the child using the existing
fork rules, without changing the shared ledger. Pending, blocked or uncertain
billing prevents a fork, including a new pending request appearing during
approval. Active turns, unresolved tool journals, queued work, active jobs and
uncertain job cleanup also block it.

The common session-switch path clears generic file-read grants, notebook and
skill receipts, file lifecycle grants, check results and instruction scope.
The child gets a fresh Git observation rather than a filesystem restoration.
Session creation uses the existing project-local store and child-lock handoff;
a failed child write does not intentionally switch away from the parent.

## Verification

```bash
python -m unittest tests.test_conversation_rewind tests.test_session_forks -v
```

Tests cover historical tool pairs, preservation of current cost and usage,
shared-ledger pending guards, changed approval snapshots, removal of future
summary/plan text, unchanged files, retained parent archives, and a real PTY
approval flow with a fake provider proving no navigation calls are replayed.
No live model API is needed.
