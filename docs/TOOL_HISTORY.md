# Browse recorded tool groups

`/tools` shows the latest eight groups, collapsed. A group is one assistant
response requesting one or more tools, followed by its recorded results. The
stable group ID is its position in the session archive, such as `G12`; individual
calls are `G12.1`, `G12.2`, and so on. IDs are scoped to the current session.

```text
/tools                 latest page
/tools next            older groups
/tools prev            newer groups
/tools 3               page three
/tools expand G12      show arguments and result excerpts for this group
/tools collapse G12    hide those excerpts
/tools collapse all    collapse all expanded groups
/details G12.2         open the second call's recorded result
/details next          next result page
/details prev          previous result page
/details G12.2 8000    character offset after current redaction
/details               latest group's last call
```

Expansion moves the list to the selected group's page. Excerpts show at most
12 calls per group; `/details GROUP.N` can open any call. Result pages contain at
most 4,000 characters plus a bounded header/argument excerpt. Results retain
stdout/stderr and other structured fields as saved text rather than asserting
that every recorded result means success. Expanded/collapsed state and the cursor
are temporary UI state; session switching resets them. Tool history itself is
read from the durable session, so it remains available after restart, resume and
context compaction. Duplicate provider call IDs are matched only within their
original assistant batch.

The browser labels saved history, archive-only groups and pending journals.
Pending calls without results are **outcome unresolved**, not successful or safe
to retry. Imported history may describe another project. Nothing in the browser
executes a tool, calls the model, changes the transcript, or grants permission to
edit a file. Read current files before making new edits.

Large results automatically page their immutable artifact when present. Artifact
identity and source-tool provenance are checked; missing, corrupted or mismatched
artifacts fall back to the saved excerpt with an explicit notice. Browsing does
not check the current source file or refresh file read guards. It never repeats
an action to recover missing output.

Known credentials are redacted across the **whole** value before clipping or
paging, so a key crossing a page boundary is not partially displayed. Terminal
controls are removed before display. Artifact pages apply the current redactor
in addition to the redaction performed when the output was first saved. Unknown
secrets remain subject to the same limitations as other session output; this is
not a public sharing/export feature.

Verification:

```sh
python -m unittest tests.test_historyview tests.test_artifacts
```
