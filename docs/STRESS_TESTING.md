# Offline stress and compatibility checks

```bash
python -m codine.stress --rounds 300 --files 5000 --commands 50
```

This command creates temporary fixtures and never reads an API key or instantiates
a live provider. It exercises bounded listing/search in a large repository,
thousands of descriptor-safe reads, repeated real child processes, long-session
save/resume and automatic compaction using a deterministic fake summarizer. It
checks message/tool validity, retained task state, archive length, descriptor
counts and retained Python allocations after garbage collection.

Each run creates a unique private report under
`.codine/evals/stress-attempts/`. Failed and interrupted attempts remain; explicit
report paths must be new files. Provenance fingerprints the harness/runtime and
workload parameters. Results are descriptive: fake summaries do not establish live
model quality, tracemalloc does not measure every native allocation, and a finite
stress exercise is not proof of indefinite or OS-wide leak freedom.

Verified checkpoint: 300 turns, 5,000 repository files and 50 child processes;
59 semantic fixture summaries; 601 valid archived messages; descriptors 4 before,
4 after child processes and 4 after session cleanup. The read loop retained 32
Python-traced bytes after collection, with a 67,049-byte peak. No inference calls.
Exact timings and memory numbers vary by platform; assertions use bounded growth,
not these illustrative numbers.

The Linux Docker CI job runs the full suite with Docker tests enabled, the five
repository-derived repair exercises and this stress workload. The eight-job
Linux/macOS Python matrix separately covers real PTY flows in xterm-compatible and
vt100 modes, installation, restarts and offline evaluation. Jobs and streaming
regressions verify exact-container/process cleanup, cancellation and output floods.
Windows, additional terminals and extended real-world soaks remain separate
compatibility/release work.
