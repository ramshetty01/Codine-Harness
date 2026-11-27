# Offline continuous integration

`.github/workflows/ci.yml` runs on pushes, pull requests, and manual dispatch. The workflow has read-only repository permission, does not persist checkout credentials, and does not reference repository secrets. It makes no paid model requests.

## Coverage

The main matrix runs Python 3.11, 3.12, 3.13, and 3.14 on `ubuntu-24.04` and `macos-15` (eight combinations). Each job:

1. Installs the pinned direct dependencies.
2. Runs the unit suite, including fake-provider PTY tests, and CLI startup/version checks.
3. Runs the trusted offline function fixtures.
4. Repeats PTY tests with `vt100` rather than the default `xterm-256color` terminal type.
5. Builds a source archive, then builds a wheel from that archive.
6. Installs the wheel and its dependencies in a fresh virtual environment.
7. Runs the installed-wheel smoke check from an unrelated directory, with socket connections blocked.
8. Runs installed offline evaluation outside the checkout, exercising bundled fixtures.

PTY tests cover multiline paste, approval separation, a narrow command menu, and process restart/session restoration. These are terminal emulations on hosted runners, not screenshots or proof of compatibility with every terminal application/version. Windows is excluded because the current runtime uses POSIX locking and process groups. Python versions after 3.14 are not yet part of the tested matrix.

The separate Linux Docker job runs boundary regressions and repository fixtures. It pulls the official Python image by immutable multi-platform index digest:

```
python@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b
```

The digest was resolved from Docker Hub's `library/python:3.12-slim` manifest on 28 November 2026. Docker chooses the Linux runner's platform manifest. The job also tags this exact image locally as `python:3.12-slim` for existing regression tests; the sandbox resolves every supplied reference to a content-addressed local image ID. Candidate containers have networking disabled. A missing Docker daemon/image is a failure in this job, never a silent host-execution fallback. Regular matrix jobs skip Docker opt-in tests.

## What offline means here

Dependency installation, GitHub actions, Docker pulls and artifact upload need network access. Test commands put `scripts/offline_guard` on `PYTHONPATH`; its `sitecustomize.py` blocks Python socket connection attempts. The installed-wheel checker additionally supplies its own isolated guard and clean environment. PTY child fixtures use fake providers. No production secret is passed into any job, and provider-key environment variables are explicitly empty.

This is an accidental-network guard for our own tests, not a security sandbox for arbitrary Python code. Only the Docker executor is intended to isolate candidate code during repository evaluation. Offline scripted fixture success does not measure a model's autonomous solve rate.

## Reproduce locally

From a dependency-installed checkout:

```bash
PYTHONPATH=scripts/offline_guard python -m unittest discover -s tests -v
PYTHONPATH=scripts/offline_guard CODINE_TEST_TERM=vt100 python -m unittest discover -s tests -p test_terminal_integration.py -v
python main.py --check
python -m codine --version
python -m build
```

Follow [Installation](INSTALLATION.md) to install the wheel into a fresh environment and run `scripts/check_install.py`. For Docker checks, ensure Docker is running and pull the pinned image explicitly; the harness never pulls it automatically:

```bash
docker pull python@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b
python -m codine.evals --offline --executor docker --suite repository --image python@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b
```

The workflow retains only offline JSON evaluation evidence for seven days, not `.env`, sessions, journals, or whole working directories. Workflow steps time out after 20 minutes per job; superseded runs for the same branch are cancelled.

## Maintenance and limits

Action revisions are pinned to full commit SHAs verified against the upstream `v6` checkout/setup-python and `v4` upload-artifact tags. Review and update these pins deliberately; see the upstream [checkout](https://github.com/actions/checkout), [setup-python](https://github.com/actions/setup-python), and [upload-artifact](https://github.com/actions/upload-artifact) repositories. Runner OS labels pin major releases, not immutable virtual machine images. Transitive Python dependencies remain installer-resolved, as described in [Installation](INSTALLATION.md).

Local checks validate workflow YAML, shell syntax, offline test commands and package installation. Only an actual GitHub Actions run can establish the full hosted OS/Python matrix result. Do not report that matrix as passed before its jobs finish.
