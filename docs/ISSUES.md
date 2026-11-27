# GitHub implementation backlog

Repository: https://github.com/ramshetty01/Codine-Harness

Created and assigned through the `ramshetty01` account. Issue closure requires implementation evidence. Milestones remain open until their acceptance gates pass.

| Roadmap | Priority | Milestone | Issue |
|---|---|---|---|
| E1 | P0 | M2 | [#1: Explicit sandbox mode for normal terminal tasks, with visible effective isolation status](https://github.com/ramshetty01/Codine-Harness/issues/1) |
| E2 | P0 | M2 | [#2: Controlled repository staging for selected source/config/test files; protect credentials, internal data, symlinks, and generated directories](https://github.com/ramshetty01/Codine-Harness/issues/2) |
| E3 | P0 | M2 | [#3: Run real repository test/build commands with working directory, environment, exit status, and bounded output](https://github.com/ramshetty01/Codine-Harness/issues/3) |
| E4 | P0 | M2 | [#4: Reproducible dependency setup and caches; separate setup access from candidate execution](https://github.com/ramshetty01/Codine-Harness/issues/4) |
| E5 | P0 | M2 | [#5: Explicit filesystem/network rules; narrowly scoped credential forwarding when a task requires it](https://github.com/ramshetty01/Codine-Harness/issues/5) |
| E6 | P1 | M2 | [#6: Scoped allow/ask/deny rules and explainable permission decisions; never equate approval with isolation](https://github.com/ramshetty01/Codine-Harness/issues/6) |
| E7 | P1 | M2 | [#7: Live bounded command output, user cancellation, and reliable process-tree cleanup](https://github.com/ramshetty01/Codine-Harness/issues/7) |
| E8 | P1 | M2 | [#8: Track long-running tests/dev servers, inspect logs, cancel jobs, and clean them up on exit/restart](https://github.com/ramshetty01/Codine-Harness/issues/8) |
| C1 | P0 | M3 | [#9: Add model-aware token/context estimates instead of relying only on character counts; reserve output space](https://github.com/ramshetty01/Codine-Harness/issues/9) |
| C2 | P1 | M3 | [#10: Add automatic semantic compaction while preserving constraints, decisions, changed files, test results, and unresolved work](https://github.com/ramshetty01/Codine-Harness/issues/10) |
| C3 | P1 | M3 | [#11: Compact long active turns at valid tool boundaries instead of requiring the user to restart the task](https://github.com/ramshetty01/Codine-Harness/issues/11) |
| C4 | P1 | M3 | [#12: Keep a persistent task plan with completed, pending, and blocked items](https://github.com/ramshetty01/Codine-Harness/issues/12) |
| C5 | P1 | M3 | [#13: Add a context inspector showing instructions, recent history, tool-output size, summary, and remaining budget](https://github.com/ramshetty01/Codine-Harness/issues/13) |
| C6 | P1 | M3 | [#14: Keep large output in bounded local artifacts and send useful excerpts; detect stale file summaries/read caches](https://github.com/ramshetty01/Codine-Harness/issues/14) |
| C7 | P2 | M5 | [#15: Define a reviewed memory policy for reusable project facts, separate from a conversation summary](https://github.com/ramshetty01/Codine-Harness/issues/15) |
| A1 | P1 | M4 | [#16: Accept user steering while a request/tool is running; apply it at a defined safe boundary](https://github.com/ramshetty01/Codine-Harness/issues/16) |
| A2 | P1 | M4 | [#17: Separate urgent steering from a queue of follow-up tasks](https://github.com/ramshetty01/Codine-Harness/issues/17) |
| A3 | P1 | M3 | [#18: Provide consistent cancellation during requests, tools, approvals, and compaction; preserve unsent drafts](https://github.com/ramshetty01/Codine-Harness/issues/18) |
| A4 | P1 | M3 | [#19: Add structured completion reasons and task states beyond a step/loop limit](https://github.com/ramshetty01/Codine-Harness/issues/19) |
| A5 | P1 | M3 | [#20: Classify transient provider failures and support bounded backoff only when replay and billing are safe](https://github.com/ramshetty01/Codine-Harness/issues/20) |
| A6 | P2 | M7 | [#21: Add a task supervisor with explicit budgets, stop conditions, checkpoints, and recovery—not an unrestricted infinite loop](https://github.com/ramshetty01/Codine-Harness/issues/21) |
| R1 | P0 | M1 | [#22: Add representative multi-language repository fixtures, beyond small Python packages](https://github.com/ramshetty01/Codine-Harness/issues/22) |
| R2 | P1 | M4 | [#23: Add glob/name search, regex search, ignore-file support, and more useful large-repository result limits](https://github.com/ramshetty01/Codine-Harness/issues/23) |
| R3 | P1 | M4 | [#24: Add explicit rename/move/delete tools with preview, stale-state checks, and recovery](https://github.com/ramshetty01/Codine-Harness/issues/24) |
| R4 | P1 | M4 | [#25: Review related edits across multiple files as a change set; design recovery for partial application](https://github.com/ramshetty01/Codine-Harness/issues/25) |
| R5 | P1 | M4 | [#26: Add Git status/diff awareness and distinguish pre-existing user work from agent changes](https://github.com/ramshetty01/Codine-Harness/issues/26) |
| R6 | P1 | M4 | [#27: Recognize common test/lint/type-check commands from repository configuration and collect their real outcomes](https://github.com/ramshetty01/Codine-Harness/issues/27) |
| R7 | P2 | M4 | [#28: Add symbol/navigation/diagnostic integration when representative tasks demonstrate a need](https://github.com/ramshetty01/Codine-Harness/issues/28) |
| R8 | P2 | M4 | [#29: Add richer document/notebook handling rather than treating every supported input as plain UTF-8 text](https://github.com/ramshetty01/Codine-Harness/issues/29) |
| S1 | P1 | M4 | [#30: Searchable/named session picker instead of choosing raw IDs from a text list](https://github.com/ramshetty01/Codine-Harness/issues/30) |
| S2 | P1 | M4 | [#31: Export/import transcripts with versioned validation and explicit redaction review](https://github.com/ramshetty01/Codine-Harness/issues/31) |
| S3 | P1 | M4 | [#32: Define handling for moved/renamed projects and session migration](https://github.com/ramshetty01/Codine-Harness/issues/32) |
| S4 | P1 | M4 | [#33: Turn-level, multi-file checkpoints; previews and conflict-safe rewind](https://github.com/ramshetty01/Codine-Harness/issues/33) |
| S5 | P2 | M7 | [#34: Session forks and a navigable conversation tree, with clearly defined active context](https://github.com/ramshetty01/Codine-Harness/issues/34) |
| S6 | P2 | M7 | [#35: Separate conversation rewind from file rollback; do not pretend either undoes shell/network effects](https://github.com/ramshetty01/Codine-Harness/issues/35) |
| S7 | P2 | M7 | [#36: Retention policies and disk-usage visibility for sessions, output artifacts, and before-images](https://github.com/ramshetty01/Codine-Harness/issues/36) |
| I1 | P1 | M5 | [#37: Hierarchical user/project/subdirectory instructions with documented scope and precedence](https://github.com/ramshetty01/Codine-Harness/issues/37) |
| I2 | P1 | M5 | [#38: Clear global versus project settings; show which source supplied the active value](https://github.com/ramshetty01/Codine-Harness/issues/38) |
| I3 | P1 | M5 | [#39: Trust decisions for repository-provided executable resources before loading them](https://github.com/ramshetty01/Codine-Harness/issues/39) |
| I4 | P1 | M5 | [#40: Discover `SKILL.md` metadata, load full instructions on demand, and resolve supporting references correctly](https://github.com/ramshetty01/Codine-Harness/issues/40) |
| I5 | P1 | M5 | [#41: Reusable prompt templates/custom commands with arguments](https://github.com/ramshetty01/Codine-Harness/issues/41) |
| I6 | P2 | M5 | [#42: Reviewed persistent project memory with provenance, editing, and deletion](https://github.com/ramshetty01/Codine-Harness/issues/42) |
| X1 | P2 | M5 | [#43: Public, versioned extension API for tools, commands, events, and UI contributions](https://github.com/ramshetty01/Codine-Harness/issues/43) |
| X2 | P2 | M5 | [#44: Lifecycle hooks with explicit ordering, timeouts, failure behavior, and permission boundaries](https://github.com/ramshetty01/Codine-Harness/issues/44) |
| X3 | P2 | M5 | [#45: Safe extension discovery and boundary-aware reload so adding a tool need not restart the entire harness](https://github.com/ramshetty01/Codine-Harness/issues/45) |
| X4 | P2 | M6 | [#46: MCP client support, tool discovery, connection lifecycle, credentials, and per-server trust](https://github.com/ramshetty01/Codine-Harness/issues/46) |
| X5 | P2 | M6 | [#47: Versioned plugin/package manifests, dependency checks, compatibility checks, installation and removal](https://github.com/ramshetty01/Codine-Harness/issues/47) |
| X6 | P2 | M6 | [#48: A reusable Python SDK and bidirectional RPC interface; retain the existing JSONL transport](https://github.com/ramshetty01/Codine-Harness/issues/48) |
| X7 | P3 | M8 | [#49: A curated package registry/marketplace and signed/reproducible distribution](https://github.com/ramshetty01/Codine-Harness/issues/49) |
| X8 | P2 | M6 | [#50: Deferred tool discovery so large integrations do not place every schema in every request](https://github.com/ramshetty01/Codine-Harness/issues/50) |
| X9 | P2 | M6 | [#51: Controlled tool orchestration and result filtering for tasks spanning many external calls](https://github.com/ramshetty01/Codine-Harness/issues/51) |
| M1 | P1 | M4 | [#52: Stream OpenRouter responses with correct reconstruction of tool calls and provider reasoning fragments](https://github.com/ramshetty01/Codine-Harness/issues/52) |
| M2 | P1 | M4 | [#53: Track capabilities per model/endpoint and adapt unsupported parameters before sending requests](https://github.com/ramshetty01/Codine-Harness/issues/53) |
| M3 | P1 | M4 | [#54: Cached model catalogs and deliberate provider/model selection when metadata is unavailable](https://github.com/ramshetty01/Codine-Harness/issues/54) |
| M4 | P1 | M4 | [#55: Shared spending controls for normal interactive sessions, not only the evaluation runner](https://github.com/ramshetty01/Codine-Harness/issues/55) |
| M5 | P1 | M4 | [#56: Report actual input/output/cache/reasoning usage when supplied; expose missing usage as unknown](https://github.com/ramshetty01/Codine-Harness/issues/56) |
| M6 | P2 | M6 | [#57: Direct provider adapters and local/custom compatible endpoints, with provider-specific compatibility tests](https://github.com/ramshetty01/Codine-Harness/issues/57) |
| M7 | P2 | M6 | [#58: Secure authentication management and supported provider login flows; API and subscription access are not interchangeable](https://github.com/ramshetty01/Codine-Harness/issues/58) |
| M8 | P2 | M6 | [#59: Measured prompt caching and explicit fallback policies that preserve budgets and conversation validity](https://github.com/ramshetty01/Codine-Harness/issues/59) |
| U1 | P1 | M4 | [#60: `@file` selection, path completion, and explicit attached-file previews](https://github.com/ramshetty01/Codine-Harness/issues/60) |
| U2 | P1 | M4 | [#61: Richer Markdown/code rendering while preserving readable streaming and copyable output](https://github.com/ramshetty01/Codine-Harness/issues/61) |
| U3 | P1 | M4 | [#62: Expand/collapse tool groups; browse more than the last tool result](https://github.com/ramshetty01/Codine-Harness/issues/62) |
| U4 | P1 | M4 | [#63: Persistent searchable prompt history, external-editor input, and user-configurable key bindings](https://github.com/ramshetty01/Codine-Harness/issues/63) |
| U5 | P1 | M4 | [#64: Accurate busy/cancel/queue status and context/cost visibility](https://github.com/ramshetty01/Codine-Harness/issues/64) |
| U6 | P2 | M8 | [#65: Image/screenshot inputs with capability checks and a session-schema migration](https://github.com/ramshetty01/Codine-Harness/issues/65) |
| U7 | P2 | M8 | [#66: User theme preferences, light/dark/accessibility checks, and broad terminal compatibility](https://github.com/ramshetty01/Codine-Harness/issues/66) |
| U8 | P3 | M8 | [#67: Additional convenience surfaces such as voice input, where platform support and demand justify them](https://github.com/ramshetty01/Codine-Harness/issues/67) |
| P1 | P2 | M7 | [#68: Read-only subagents with separate context and bounded summaries](https://github.com/ramshetty01/Codine-Harness/issues/68) |
| P2 | P2 | M7 | [#69: Shared budget/cancellation supervision across all workers](https://github.com/ramshetty01/Codine-Harness/issues/69) |
| P3 | P2 | M7 | [#70: Isolated worktrees for concurrent edits, with conflict-aware integration](https://github.com/ramshetty01/Codine-Harness/issues/70) |
| P4 | P2 | M7 | [#71: Task dependency tracking and explicit ownership of shared resources](https://github.com/ramshetty01/Codine-Harness/issues/71) |
| P5 | P3 | M8 | [#72: Durable background/scheduled work with reconnectable status and logs](https://github.com/ramshetty01/Codine-Harness/issues/72) |
| P6 | P3 | M8 | [#73: Optional agent teams and messaging after simpler parallel workflows are reliable](https://github.com/ramshetty01/Codine-Harness/issues/73) |
| D1 | P1 | M8 | [#74: Installable Python package/console entry point, version command, upgrade/uninstall workflow, and dependency management](https://github.com/ramshetty01/Codine-Harness/issues/74) |
| D2 | P2 | M8 | [#75: CI installation/testing matrix across supported Python/OS/terminal versions](https://github.com/ramshetty01/Codine-Harness/issues/75) |
| D3 | P2 | M8 | [#76: Windows/WSL support or an explicit maintained platform policy; current implementation uses POSIX mechanisms](https://github.com/ramshetty01/Codine-Harness/issues/76) |
| D4 | P2 | M8 | [#77: Editor integration through the stable SDK/RPC layer](https://github.com/ramshetty01/Codine-Harness/issues/77) |
| D5 | P2 | M8 | [#78: GitHub/GitLab automation and reviewable PR workflows](https://github.com/ramshetty01/Codine-Harness/issues/78) |
| D6 | P3 | M8 | [#79: Browser/computer integrations and web-app verification tools](https://github.com/ramshetty01/Codine-Harness/issues/79) |
| D7 | P3 | M8 | [#80: Desktop/web/mobile clients and remote control](https://github.com/ramshetty01/Codine-Harness/issues/80) |
| D8 | P3 | M8 | [#81: Organization policy, shared configuration, audit logging, and administration](https://github.com/ramshetty01/Codine-Harness/issues/81) |
| D9 | P3 | M8 | [#82: Hosted scheduling, team collaboration, and managed deployment](https://github.com/ramshetty01/Codine-Harness/issues/82) |
| Q1 | P0 | M1 | [#83: Realistic bug-fix, feature, refactor, test-repair, and recovery tasks from real repositories](https://github.com/ramshetty01/Codine-Harness/issues/83) |
| Q2 | P0 | M1 | [#84: Separate model-visible checks from held-back final grading; prevent reference/expected-answer leakage](https://github.com/ramshetty01/Codine-Harness/issues/84) |
| Q3 | P0 | M1 | [#85: Freeze and hash tasks, source versions, execution images, and request configuration for reproducibility](https://github.com/ramshetty01/Codine-Harness/issues/85) |
| Q4 | P0 | M1 | [#86: Compare the same model and task conditions across harness changes; repeat runs to expose variance](https://github.com/ramshetty01/Codine-Harness/issues/86) |
| Q5 | P0 | M1 | [#87: Track success, regressions, incorrect edits, tool failures, interventions, latency, actual/unknown cost, and cost per solved task](https://github.com/ramshetty01/Codine-Harness/issues/87) |
| Q6 | P1 | M3 | [#88: Fault injection for provider disconnects, rate limits, disk failures, corrupted state, cancellation, and process crashes](https://github.com/ramshetty01/Codine-Harness/issues/88) |
| Q7 | P1 | M3 | [#89: Adversarial repository/tool-output cases and permission-boundary tests](https://github.com/ramshetty01/Codine-Harness/issues/89) |
| Q8 | P1 | M3 | [#90: Long-session, large-repository, terminal compatibility, and sustained resource-leak testing](https://github.com/ramshetty01/Codine-Harness/issues/90) |
| Q9 | P1 | M3 | [#91: Private diagnostic bundles, structured error categories, and documented migration/recovery paths](https://github.com/ramshetty01/Codine-Harness/issues/91) |

## First parallel batch

- Q3: evaluation provenance (main checkout).
- E2: controlled repository staging (isolated worktree).
- C1: model-aware context budgets (isolated worktree).
- D1: installable package and CLI (isolated worktree).

D1 is pulled forward because reproducible installation supports the other milestones. Four workstreams means the coordinator plus three isolated implementation agents. Later work is selected by dependency readiness, not by closing incomplete issues.
