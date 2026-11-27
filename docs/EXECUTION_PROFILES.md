# Explicit execution profiles

Docker commands normally have no network, no host mounts and no forwarded host environment. An explicit execution profile can grant a particular command bridge networking and selected startup-shell credentials. It cannot mount filesystems, enable host networking, change the runtime user, or select credential values.

This is separate from dependency preparation: `prepare_environment --allow-network` applies only while building a dependency image. It grants no runtime network access.

## Create a user-owned profile

Save a JSON file outside the project the agent can edit, for example `~/.config/codine/integration-profile.json`. Use an exact canonical project path and the immutable image ID reported by `/sandbox`:

```json
{
  "version": 1,
  "executor": "docker",
  "project": "/absolute/canonical/project",
  "image": "sha256:REPLACE_WITH_64_HEX_DIGITS",
  "filesystem": "staged-only",
  "rules": [
    {
      "id": "service-check",
      "cwd": ".",
      "argv": ["python", "integration_check.py"],
      "network": "bridge",
      "credentials": ["SERVICE_API_KEY"]
    }
  ]
}
```

The image placeholder is intentionally invalid until replaced. The profile must be owned by your user, must not be group/world writable, and cannot be a symlink or hardlink. Parent directory symlinks and changes while reading are also rejected. `chmod 600` is a suitable file mode.

Set the named credential in the shell before starting Codine, without putting its value in the profile, command arguments or chat. Then launch explicitly:

```bash
codine --project /absolute/canonical/project \
  --executor docker --image sha256:YOUR_IMMUTABLE_IMAGE_ID \
  --execution-profile ~/.config/codine/integration-profile.json
```

Codine captures only credential names listed by this file, before loading application/project `.env` files. A credential added only by `.env` is not forwarded. Environment values never become session fields or Docker command-line flags. Missing credentials fail closed when the matching command is approved.

## What a rule allows

Rules match the full argv array and relative working directory exactly. The profile itself also binds the project, Docker executor and image ID. There are no executable prefixes, wildcards or model-selected profile names. An unmatched command keeps the ordinary offline, credential-free Docker behavior.

Allowed network modes are `none` and `bridge`. Bridge means general outbound connectivity through Docker, **not domain filtering**. A command given both bridge networking and a credential can send that credential over the network; approve only the exact staged program you intend to authorize.

The filesystem remains the explicit staged snapshot and ephemeral container outputs. Host home directories, `.env`, credential files, SSH agents, Docker sockets and arbitrary mounts are not exposed. Existing read-only root, non-root user, resource limits and container cleanup still apply. The allowed credential names cannot override interpreter/loader controls such as `PATH`, `PYTHONPATH`, `LD_PRELOAD`, Docker configuration or Codine settings.

## Approval and command policy

Every network or credential expansion requires fresh visible approval for that invocation. The approval includes the exact command, cwd, pinned image, staged file hashes, network mode and credential **names only**. A command-policy `allow` rule cannot bypass this approval. A command-policy `deny` still blocks the command. Noninteractive runs cannot approve expanded access.

`/sandbox` reports the profile's scope and isolation mode. A profile selected explicitly for the current process is retained across `/restart` by its path and reloaded. Restart restores the captured shell values for selected names before process replacement, removing names absent from the original shell, so project `.env` values cannot become newly authorized shell credentials. Ordinary session resumption does not restore profile authority from session metadata; provide `--execution-profile` again if you want it. Editing a profile does not mutate the in-memory rules of an already running process.

## Credential-bearing output

For a command receiving credentials, Codine suppresses streaming and withholds **all stdout and stderr** from the terminal UI, model and saved conversation. This includes successful output, partial output, failures and timeouts. Only bounded status metadata such as exit code, elapsed time and staged manifest identity is returned, with an `output_withheld` notice. Transport/cleanup exceptions are replaced with a generic credential-scoped failure message.

This deliberately sacrifices command diagnostics. Exact-string redaction cannot reliably catch encoded or transformed credentials; withholding the whole output avoids claiming otherwise. Credential-free commands retain their ordinary bounded output and streaming. Do not put credential values in command arguments or staged source files: the profile is designed to inject values into the approved process environment instead.

The profile is a user authorization boundary, not a secret manager or network firewall. It does not hide secrets from the approved program, the Docker daemon administrator, or an external service contacted by that program.
