# Install and maintain Codine

Codine requires Python 3.11+ on macOS or Linux; Python 3.11–3.14 are maintained in CI. See [the platform policy](PLATFORMS.md) for tested OS versions and experimental WSL2 status. Windows is currently unsupported because sessions and commands use POSIX locking/process controls. The distribution is named `codine-harness`; its terminal command is `codine` and its Python module is `codine`. This repository does not claim a published PyPI release.

## Build and install a wheel

From the checkout, build with a separate development environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install build
.venv/bin/python -m build
```

The build produces a source archive and a wheel in `dist/`. Replace the version in the examples with the generated filename when upgrading. For a command available outside this checkout, install the wheel using an existing `pipx` installation:

```bash
pipx install ./dist/codine_harness-0.2.0-py3-none-any.whl
codine --version
codine --check
cd /path/to/your/project
codine
```

Alternatively, use an isolated virtual environment, without pipx:

```bash
python3 -m venv ~/.local/share/codine-venv
~/.local/share/codine-venv/bin/python -m pip install ./dist/codine_harness-0.2.0-py3-none-any.whl
~/.local/share/codine-venv/bin/codine --check
```

Add that environment's `bin` directory to PATH if you want to type just `codine`. `python -m codine` provides the same entry point when using its interpreter. Use `command -v codine` to check which installation your shell selects if an older checkout launcher is still present.

## Configuration and state

An installed wheel loads configuration from `~/.config/codine/.env`, or `$XDG_CONFIG_HOME/codine/.env`. Create that directory and file yourself; startup does not manufacture credentials. Shell environment variables take precedence. The selected project's `.env` supplies settings not already set by the shell or application configuration. See the repository's `.env.example` for provider settings; never copy over existing secrets during an upgrade.

Sessions and edit journals remain inside the selected project's `.codine/` directory. The shared paid-evaluation ledger is in `~/.local/state/codine/evals/budget.json`, or `$XDG_STATE_HOME/codine/evals/budget.json`. Evaluation reports use their requested output path, normally `.codine/evals/` in the current directory. Configuration, sessions and budget ledgers do not belong in `site-packages`.

Set `CODINE_HOME=/absolute/path` to override application configuration and shared state: `.env` is read from that directory and the ledger is under its `state/evals/` directory. This override also works in development checkouts. Keep the same state directory across upgrades; changing it starts a different ledger and does not migrate previous spending. Do not change it to bypass a spending cap or pending charge.

## Develop from a checkout

```bash
.venv/bin/python -m pip install -e .
.venv/bin/python main.py --check
.venv/bin/codine --version
```

Editable installs load your checkout code. The existing `scripts/install_cli.py` remains supported: it writes a launcher pointing at the checkout's `.venv` and `main.py`. Do not run it when you want the standalone wheel command instead. A checkout keeps its existing root `.env` and `.codine` shared evaluation state unless `CODINE_HOME` is set. Project sessions still belong to the selected project.

Editing Python files changes disk contents, not the code already running in memory. `/restart` checks the updated source and startup before replacing the process and resuming its session. Installed wheels restart with the same interpreter using `python -m codine`; checkouts continue to restart through `main.py`.

## Upgrade and uninstall

Build a new wheel from the desired revision and then replace the installed distribution:

```bash
# pipx installation
pipx install --force ./dist/codine_harness-0.2.0-py3-none-any.whl

# standalone virtual environment
~/.local/share/codine-venv/bin/python -m pip install --upgrade --force-reinstall ./dist/codine_harness-0.2.0-py3-none-any.whl
```

Exit running Codine sessions before upgrading, then verify `codine --version` and `codine --check`. The package version has one source, `codine/__init__.py`; release changes must update it. Direct runtime dependencies are pinned in `requirements.txt`, which also supplies wheel metadata. Transitive dependencies are resolved by the installer; this is not a complete transitive lockfile. Keep the previous wheel if you need to reinstall a previous application version.

Uninstall using the tool that installed it:

```bash
pipx uninstall codine-harness
# or
~/.local/share/codine-venv/bin/python -m pip uninstall codine-harness
```

For the old checkout launcher, remove only its managed `~/.local/bin/codine` file. Uninstalling the distribution does not remove project sessions or user configuration. Keep the evaluation ledger if you may run paid evaluations again.

## Verify an installation without model calls

```bash
python3 scripts/check_install.py /absolute/path/to/installed/venv/bin/python
```

This smoke check runs from a temporary unrelated project and blocks Python socket connections. It verifies version/startup entry points, bundled evaluation fixtures, model-picker imports, offline `/help`, project session storage, and restart into the installed module. It does not invoke model inference. For infrastructure evaluation after installation:

```bash
/path/to/installed/venv/bin/python -m codine.evals --offline
```

Offline fixtures test the harness, not a model's coding ability. Paid evaluation remains an explicit separate step.
