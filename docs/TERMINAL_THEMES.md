# Terminal preferences

Use `/theme list` to inspect the current preference or select one explicitly:

| Command | Appearance |
| --- | --- |
| `/theme auto` | Default: inherit the terminal's foreground/background with its ANSI accents |
| `/theme dark` | Dark background, light text, subdued borders and distinct diff colors |
| `/theme light` | White background, dark text, subdued borders and distinct diff colors |
| `/theme contrast` | Inherited foreground/background, bold labels and reverse selection, without a custom color palette |

Selection applies to the running terminal and is saved in the private user file
`~/.config/codine/theme.json`. It does not modify the project, provider, session
messages, permissions or spending. The file contains only version 1 and a theme
name. Files inside the project, symlinks, shared-readable files and invalid or
oversized preference data are refused. When the configured user-input paths are
inside the selected project, normal startup keeps defaults as it does for input
history; use a narrower project to enable user customization.

`NO_COLOR` (even an empty value), `TERM=dumb`, and noninteractive output override
all color presets. JSON/RPC consumers receive structured data rather than a
terminal palette. `/theme` is a local user command, not a model tool; repository
instructions cannot select a palette or inject arbitrary terminal styling.

Dark/light palette text, secondary labels, errors, success, code and diff colors
are tested for at least 4.5:1 contrast against their specified background using
sRGB luminance. This calculation describes those explicit colors, not the user's
terminal configuration, color vision, font, or ANSI/256-color approximation.
Auto/contrast inherit the user's foreground/background and cannot promise a
measured ratio. Diff signs, error/status text, and selection emphasis remain
available without relying on red/green alone. These are presets, not arbitrary
CSS, terminal-background detection, or an accessibility certification.

Verification includes real 52-column PTYs in `xterm-256color` and `vt100`, live
switching through all four preferences, private persistence, plain CLI output,
and existing multiline input, completion, approval, history, restart and status
checks. Linux/macOS CI covers supported Python versions. There is no claim of
native Windows or exhaustive emulator/screen-reader certification.
