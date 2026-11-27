# Cancellation boundaries

Ctrl+C during terminal input keeps the unsent editor draft in memory. Edit it and
press Enter to submit, or clear it with Ctrl+U. Drafts are not sent to the model or
written to sessions merely because input was interrupted. In `/paste` fallback
mode, completed lines are retained; a partially typed `input()` line cannot be
recovered. Draft persistence across process exit is not implemented.

Ctrl+C during an approval stops the turn; it does not grant approval or continue
the remaining tool batch. EOF at an approval declines it. During model requests,
semantic summaries or tools, cancellation returns through the same turn recovery
boundary: known results remain, uncertain outcomes are recorded and tools are not
replayed. Pending provider charges remain pending until reconciled.

Host commands terminate their process group; Docker commands remove their named
container. Deliberately detached host processes can escape a process group, so
use Docker when containment is required. Cleanup errors must be reported instead
of claiming successful cancellation. Background jobs have their own explicit
cancel/status lifecycle and are cleaned up on normal exit/restart.
