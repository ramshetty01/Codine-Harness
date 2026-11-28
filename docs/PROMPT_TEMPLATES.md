# Reusable prompt templates

Templates are bounded UTF-8 JSON files containing **text**, not executable
custom-command hooks. There are two explicit scopes:

- Project: `.codine-prompts/NAME.json`, subject to workspace exclusions, file
  permissions and Git ignore rules. Its contents are untrusted project data.
- User: `~/.config/codine/prompts/NAME.json`, private mode 0600, outside the project.
  This location is captured from the user's home and cannot be selected by
  project settings. Model file tools cannot read or edit this user template root. Protection also reserves case and Unicode-normalization variants before the directory exists, including in a broad HOME workspace; similarly named sibling directories are not treated as this root.

For example, create `.codine-prompts/review.json`:

```json
{
  "version": 1,
  "description": "Review a chosen part of the project",
  "arguments": ["topic", "focus"],
  "text": "Review {{topic}} with attention to {{focus}}. Explain findings before proposing changes."
}
```

List available names and metadata with `/templates`, then invoke one explicitly:

```text
/template project:review {"topic":"the parser","focus":"error handling"}
```

Codine shows the source scope/path, SHA256 of the source bytes, and expanded text,
then places the expansion in an **unsent draft**. Review and edit it before
pressing Enter. It does not call the model when expanding. An existing unsent
draft is preserved; clear or finish it before selecting another template.

Bare names such as `/template review ...` work only when the name is unambiguous.
If user and project scopes both contain `review`, choose the scope explicitly;
there is no silent override. Names use lowercase letters, digits, underscores
and hyphens, begin with a letter, and never contain paths. There are no implicit
`/NAME` aliases or executable hooks. When the project includes the user template
root (for example HOME), user scope is disabled with a notice; project scope can
still work under its normal policies.

Arguments are a JSON object of strings. Missing, extra, duplicate or non-string
arguments are rejected. Every declared argument must appear as `{{name}}` in the
text. Replacement is literal and happens once: an argument containing `{{other}}`,
Python, shell syntax or environment-variable syntax stays text. Expressions,
Jinja evaluation, includes and file interpolation do not exist.

The draft has a provenance prefix so a body beginning `/quit` or another slash
command cannot become a terminal command. All template-origin `@` characters,
including substituted arguments, are escaped once in the draft; normal submission
restores them as text without reading files. Review the draft and add intentional
attachments yourself if wanted. Template text cannot change the system prompt,
file/command permissions, provider or budget. Any later model tool request still
uses the normal controls. A preview is a snapshot: changing the template file
before submitting does not change the already rendered draft.

Discovery reads at most 64 templates per scope. User directory scanning stops at
256 entries; project scanning uses existing repository bounds and ignore rules.
Truncation and unavailable scopes are reported. Each template is at most 16 KB;
there are at most 16 simple argument names, each value at most 8 KB, and the final
draft including provenance/escaping is at most 32 KB. Terminal controls are
rejected. Symlinks, hardlinks, malformed versions/metadata and unsafe user-file
permissions are rejected. Automatically listed metadata skips project files
requiring read approval; explicit invocation can request that approval. Reading
a template never creates file-edit read guards.

Known credentials are redacted in both preview and draft. This does not guarantee
that arbitrary unknown secrets in user-authored text are detected. Keep templates
free of credentials and review the full expansion. Noninteractive mode may list
metadata, but cannot expand/submit a template implicitly.

Offline verification:

```sh
python -m unittest tests.test_prompt_templates
```

The real PTY test verifies preview without a model call, literal arguments,
non-execution of shell/slash text, no implicit attachment read, and submission of
the exact reviewed snapshot after the source file changes.
