# Background jobs (E8)

`start_job` starts an approved, bounded command without occupying the conversation
loop. The model can use `job_status` to inspect progress and `cancel_job` to stop
it. Users can type `/jobs`, `/jobs JOB_ID`, or `/cancel JOB_ID`. Cancellation is
allowed even in planning mode; starting a command is not.

The selected host/Docker executor, exact command policy, snapshot approval and
execution-profile rules apply exactly as they do to foreground commands. Approval
and credential selection occur synchronously before a worker thread is started.
Docker snapshots keep the approved bytes; the worker never asks for permission or
falls back to host execution. Host commands retain their existing user privileges.

Defaults bound a process to four active jobs, sixteen retained job summaries,
16,000 sanitized log characters per job, and a 300-second default job lifetime.
Each start request can choose a lifetime up to 3600 seconds, shown in the approval;
foreground `run_command` keeps its separate 120-second ceiling. A supervisor timer
and the transport timeout both enforce the job lifetime. Transport output/resource caps still apply. Completed history is
pruned when necessary. Background commands are suitable for bounded test/build or
short development checks, not indefinitely running services.

`/jobs JOB_ID` shows status, exit code, elapsed time and bounded log excerpts.
Output remains separated by stream labels, terminal controls are stripped, and
known secret values are redacted across chunks before logs are retained. Execution
profiles that supply credentials continue to withhold all command output, including
encoded output. Raw command results, environment values and credential dictionaries
are not retained in job metadata. No mechanism can identify every unknown secret
printed by arbitrary code; keep sensitive data out of ordinary commands.

Cancellation sets a thread-safe signal. The command transport notices it at its
next selector boundary and runs its normal process-group/container cleanup. Docker
control operations have their own bounded timeout, so cancellation is not always
instantaneous. Normal exit cancels and joins jobs; restart does the same before
replacing the process. A cleanup timeout or unconfirmed worker failure is reported
explicitly, rather than claiming cleanup succeeded. Failed restart can leave the
same supervisor usable after jobs have stopped.

Jobs and logs exist only in this process. They are not resumable services and do
not survive a crash or restart. Old IDs in conversation history are reported as
unknown/expired; Codine does not replay their commands. Host process-group cleanup
cannot contain a deliberately detached process. Abrupt process/OS termination can
prevent cleanup; crash recovery and durable background jobs are separate work.
