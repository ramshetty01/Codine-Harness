# Prepared Docker environments

`python -m codine.prepare_environment` prepares dependencies separately from candidate execution. The command runs only when explicitly invoked. It does not ask a model to choose installation commands, load `.env`, forward host credentials, mount the project, or fall back to installing on the host.

## Workflow

1. Choose and locally install a trusted base image by immutable digest. Image pulling is a separate explicit action.
2. Select the exact dependency lock/config files needed for setup.
3. Supply a setup command as a JSON argv array.
4. Add `--allow-network` only if the setup needs dependency downloads.
5. Use the returned immutable `image` ID with the Docker executor's `--image` option.

For example, with a Python lockfile that contains fully pinned dependencies and hashes:

```bash
python -m codine.prepare_environment \
  --project /absolute/project \
  --file requirements.lock \
  --base-image python@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b \
  --command-json '["python","-m","pip","install","--require-hashes","-r","requirements.lock"]' \
  --allow-network
```

The referenced image must already exist locally. Preparation does not pull it. The JSON response contains `image`, `cache_key`, and `cached`. Pass the resulting `sha256:...` image ID to Codine's Docker runtime or an offline evaluation:

```bash
python -m codine.evals --offline --executor docker --image sha256:YOUR_RETURNED_IMAGE_ID
```

Candidate containers remain offline even when their image was prepared using network access. Dependencies must be readable by user `65534:65534`, and importable from standard interpreter paths. Install them globally in the image; do not depend on a root user's home directory or a writable root filesystem during execution.

## Isolation and bounds

Setup creates a named container with a writable container filesystem, root inside that container, all capabilities dropped, no privilege escalation, one CPU, 512 MB memory, and 128 processes. Root is needed to install image-wide dependencies; it does not get a host filesystem mount. The setup command is passed as argv, with no generated shell interpolation. `/usr/bin/env -i` clears inherited image environment before starting it, supplying only `PATH` and `HOME`. The base image must provide `/usr/bin/env` and the requested executable.

The setup network is `none` unless `--allow-network` explicitly selects Docker bridge networking. That opt-in permits general setup egress; it is not a package-domain allowlist. Host API keys, package-manager credentials, SSH agents and Docker sockets are never mounted or forwarded. Private authenticated registries are not supported by this version. Trusted base images may themselves contain files and programs: this is not an audit of their contents.

Only selected normal UTF-8 dependency files are copied through a generated stdin tar archive. Recognized names include `requirements*.txt`, `requirements*.lock`, `pyproject.toml`, `uv.lock`, `poetry.lock`, `Pipfile`, `Pipfile.lock`, `package.json`, `package-lock.json`, and `yarn.lock`. There is no implicit whole-project build context. Paths must be normalized and relative; symlinks, hardlinked aliases, traversal and changes during reading are rejected through the shared descriptor-based staging reader. Limits are 32 files, 256 KB per file and 1 MB total. Obvious credential fields and authenticated URLs are rejected. These filters are conservative checks, not a guarantee that a user-selected file contains no sensitive content.

Setup defaults to 300 seconds and accepts `--timeout` from 1 to 900 seconds. Docker operation output is capped at 64 KB. Error output is withheld because installer logs can contain private URLs. The named container is forcibly removed on success, nonzero exit, timeout or output-limit failure; Docker cleanup errors are reported. The image is committed only after Docker reports a successful exit without OOM termination. Committed images replace the inherited entrypoint/setup command with a clean-environment Python entrypoint, set `/tmp` as workdir, and default to the executor's non-root user. There is no per-container disk quota in this implementation; use a dedicated Docker environment with suitable disk limits for large or untrusted dependency builds.

## Cache identity and reproducibility

A cache key hashes:

- Preparation protocol version.
- Resolved immutable base image ID plus inspected OS, architecture and variant.
- Each selected file's relative name and exact content hash.
- Setup argv hash.
- Setup network mode.

Receipts live in the shared state directory's `environments/` folder (see [Installation](INSTALLATION.md)). They store hashes and image IDs, not file contents or setup argv. Cache directories and receipt files cannot be symlinks; linked or changing receipts are rejected. New cache directories use mode 0700 and atomic receipt writes use mode 0600, even with a permissive process umask. Cache hits verify the local image ID, identity label, non-root default user, clean-environment Python entrypoint/command, and absence of credential-like environment settings. A missing or mismatched image is rebuilt. A failed setup does not write a successful receipt. Docker's image storage holds the actual prepared layers; no package-manager cache directory is mounted from the host.

This supports reuse of a verified environment from the same inputs. It does not promise bit-identical rebuilds: Docker commit timestamps and a networked installer may vary. For reproducible dependencies, use complete lockfiles and installer hash validation, plus a trusted immutable base. Changing network mode, files or command invalidates the cache. Inspected OS/architecture/variant participate separately in the identity, so an image store exposing a multi-platform index as its image ID still gets platform-specific cache keys.

Prepared images and receipts are intentionally retained for reuse. To remove one, remove the exact returned image ID with `docker image rm sha256:...` and delete its corresponding receipt. Preparation does not remove unrelated images, volumes or caches. Do not prune images used by running tasks.

## Verification

Unit tests use a fake Docker boundary to check flags, manifest selection, command isolation, image/cache validation, cleanup and failure behavior. Subprocess tests exercise actual output and time limits. The Docker-opt-in test prepares a tiny standard-library fixture, verifies cache reuse, and imports the resulting module through the non-root offline executor:

```bash
CODINE_DOCKER_TESTS=1 python -m unittest discover -s tests -p test_prepare_environment.py -v
```

The test requires a local `python:3.12-slim` image. CI tags its digest-pinned image under that name before running the test. No package downloads or model API calls are needed for the test.
