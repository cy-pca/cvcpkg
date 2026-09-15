# Installing cvcpkg

`cvcpkg` ships two console entry points — `cvcpkg` (the client) and
`cvcpkg-server` (the registry server) — plus the bundled recipe set used
for building from source.

> **Status: not on PyPI yet.** Publishing to PyPI is deliberately the
> *final* step of the release roadmap. The publish workflow itself (build +
> live-smoke gates, OIDC trusted publishing) is ready, but no trusted
> publisher is registered on PyPI yet, and the release is gated on the full
> checklist in
> [roadmap/CVCPKG-ROADMAP.md](roadmap/CVCPKG-ROADMAP.md#path-to-pypi-release-blockers)
> — including the repo transfer and the platform-coverage blockers in
> [roadmap/platform-coverage-pypi-blockers.md](roadmap/platform-coverage-pypi-blockers.md).
> Until then, install with a [quick-install one-liner](#quick-install),
> a [standalone binary](#standalone-binaries), or
> [pip from a source checkout](#installing-from-a-source-checkout).

## Quick install

The fastest way to a working `cvcpkg` — no Python required (#514).

Linux, macOS, FreeBSD, OpenBSD, NetBSD (needs `curl`):

```bash
curl -fsSL https://cvcpkg.org/install.sh | sh
```

Windows (PowerShell):

```powershell
irm https://cvcpkg.org/install.ps1 | iex
```

Both scripts are served by the registry itself (the sources live in
[src/cvcpkg/server/assets/](../src/cvcpkg/server/assets/), so you can
read exactly what will run before piping). They:

1. detect your OS and architecture,
2. download the latest stable (non-rc) `cvcpkg-v*` standalone binary
   from GitHub Releases,
3. verify its **sha256 checksum** against the `.sha256` file published
   next to the asset — a mismatch aborts before anything runs,
4. install it: POSIX to `$HOME/.local/bin/cvcpkg` (with a PATH hint if
   needed); Windows to `%LOCALAPPDATA%\cvcpkg\cvcpkg.exe`, added to your
   user PATH — no admin rights required.

Both accept environment overrides:

| Variable | Meaning | Default |
|----------|---------|---------|
| `CVCPKG_VERSION` | Pin a release tag, e.g. `cvcpkg-v2.0.2`. | latest stable release |
| `CVCPKG_INSTALL_DIR` | Install location. | `$HOME/.local/bin` / `%LOCALAPPDATA%\cvcpkg` |

```bash
# Pin a specific release
CVCPKG_VERSION=cvcpkg-v2.0.2 curl -fsSL https://cvcpkg.org/install.sh | sh
```

## Standalone binaries

Every `cvcpkg-v*` tag builds single-file executables via PyInstaller
([.github/workflows/cvcpkg-standalone.yml](../.github/workflows/cvcpkg-standalone.yml),
from [packaging/cvcpkg.spec](../packaging/cvcpkg.spec)) and attaches
them to the matching GitHub Release. They embed the Python runtime, all
dependencies, and the standard recipe + schema set — `cvcpkg validate`
and `cvcpkg build` work on a machine with no Python and no checkout.

| Platform | Asset |
|----------|-------|
| Linux x86_64 | `cvcpkg-linux-x86_64` |
| macOS arm64 | `cvcpkg-macos-arm64` |
| Windows x86_64 | `cvcpkg-windows-x86_64.exe` |
| FreeBSD x86_64 | `cvcpkg-freebsd-x86_64` |
| OpenBSD x86_64 | `cvcpkg-openbsd-x86_64` |
| NetBSD x86_64 | `cvcpkg-netbsd-x86_64` |

Other platform/arch combinations (e.g. Linux arm64) currently need a
[source checkout](#installing-from-a-source-checkout).

Each asset has a sibling `<asset>.sha256`. To download and verify
manually instead of using the one-liner:

```bash
tag=cvcpkg-v2.0.2
base=https://github.com/transfix/libcvc-deps/releases/download/$tag
curl -fsSLO "$base/cvcpkg-linux-x86_64"
curl -fsSLO "$base/cvcpkg-linux-x86_64.sha256"
sha256sum -c cvcpkg-linux-x86_64.sha256      # shasum -a 256 -c on macOS
chmod +x cvcpkg-linux-x86_64
mv cvcpkg-linux-x86_64 ~/.local/bin/cvcpkg
```

The released binaries are the **client CLI only** — `cvcpkg-server` is
not included. To run the server, install from source with the `server`
extra (below).

## Installing from a source checkout

With Python ≥ 3.10:

```bash
git clone https://github.com/transfix/libcvc-deps
cd libcvc-deps
pip install .
cvcpkg --version
cvcpkg-server --version
```

This installs both entry points and the bundled recipe set. Extras (see
next section) select the optional dependency groups — e.g. a runnable
server needs at least:

```bash
pip install ".[server,db-sqlite]"
```

See [BUILDING.md](BUILDING.md) for a full development setup.

## Core vs extras

`pip install cvcpkg` resolves to **`click` + `PyYAML`, and
nothing else**. That is the whole client: resolving the catalog, downloading
(`urllib.request`), sha256 verification, extraction, lockfiles, the
`cvcpkgConfig.cmake` and activation scripts, `cvcpkg build`, and the recipe
tooling all run on those two. Keeping the mandatory list at two is what makes
cvcpkg installable where the wheel ecosystem is thin — four
mandatory-but-never-imported distributions were the entire blocker on such
platforms.

Everything heavier belongs to a **role**, not to the client, and lives behind
an extra named after the *entry point* that needs it. Several extras resolve to
the same distribution on purpose: the name is what the error message tells you
to install, so a publisher is pointed at `[publish]` rather than at something
that sounds like it belongs to somebody else.

Running a command whose extra is missing is never a traceback — it is one line
naming the extra:

```
$ cvcpkg publish zlib
Error: httpx is required to talk to a cvcpkg server over HTTP. Install it with: pip install 'cvcpkg[publish]'
```

> **Upgrading from cvcpkg ≤ 2.0.2?** `httpx`, `cryptography`, `sqlalchemy` and
> `greenlet` used to be mandatory, so you had them whether or not you used
> them. `pip install "cvcpkg[all]"` is a superset of what you had before.

## Extras

Optional dependency groups, selected with pip extras. They apply to
source-checkout installs today and to `pip install cvcpkg` once the
PyPI release lands.

| Extra | Installs | When you need it |
|-------|----------|------------------|
| _(none)_ | click, PyYAML | `install`, `upgrade`, `build`, `pack`, `init`, and the rest of the recipe tooling — the whole client. |
| `remote` | httpx | Any command that talks to a cvcpkg registry: `search`, `recipe pull`/`list`, `builds list`/`log`/`monitor`/`purge`, `webhook …`, `token`/`user`/`org` admin, `yank`/`unyank`/`nuke`, `doctor --server`. |
| `publish` | httpx | Publishing: `publish`, `recipe push`, `recipe push-all`, `recipe publish`, `builds submit-dag`/`follow-dag`. |
| `builder` | httpx | Running a builder agent: `cvcpkg builder run`/`list`/`logs`. |
| `signing` | cryptography | Ed25519 key management (`cvcpkg key …`, `sign`, `verify-sig`) and `install --verify-signatures`/`--require-signatures`. |
| `validate` | jsonschema | `cvcpkg validate`: checking `recipe.yaml` / `components.yaml` against the bundled Draft 2020-12 schemas. Recipe authoring only — installing a package never reads a schema. |
| `progress` | tqdm | Reserved for download/extract progress bars — currently unwired (nothing in `src/cvcpkg` imports tqdm yet; see [roadmap/cli-ux-recipe-first.md](roadmap/cli-ux-recipe-first.md)). |
| `server` | FastAPI, Uvicorn, Pydantic, python-multipart, SQLAlchemy, greenlet, httpx | Running `cvcpkg-server` (and its `bootstrap`, `token`, `audit` subcommands). |
| `db-sqlite` | aiosqlite, Alembic, SQLAlchemy, greenlet | Server on a SQLite database. |
| `db` | asyncpg, Alembic, SQLAlchemy, greenlet | Server on PostgreSQL. |
| `db-mysql` | aiomysql, Alembic, SQLAlchemy, greenlet | Server on MySQL/MariaDB. |
| `db-all` | asyncpg, aiosqlite, aiomysql, Alembic, SQLAlchemy, greenlet | Server on any of the above. |
| `production` | `server` + asyncpg + Alembic | Production server on PostgreSQL. |
| `s3` | boto3 | S3 (or Garage/MinIO) storage backend for `publish --dest` and server-side archives. |
| `azure` | azure-storage-blob, azure-identity | Azure Blob storage backend. |
| `gcs` | google-cloud-storage | Google Cloud Storage backend. |
| `sftp` | paramiko | SFTP storage backend. |
| `storage-all` | boto3, azure-*, google-cloud-storage, paramiko | Every storage backend. |
| `all` | everything above | One-word migration from ≤ 2.0.2; also what CI installs to run the full test suite. |

Extras combine — e.g. a SQLite-backed server is `server` + `db-sqlite`:

```bash
pip install ".[server,db-sqlite]"   # server on SQLite
pip install ".[production]"          # server on PostgreSQL
pip install ".[server,db-all]"       # server, any DB backend
pip install ".[publish,signing]"     # publish signed packages
pip install ".[all]"                 # everything
```

If you are scripting an install for CI or a deployment, pick the extra that
matches the command that host actually runs — a builder host wants
`[builder]`, a host that runs `recipe push` or `builds submit-dag` wants
`[publish]`.

The `https`/`http`, `file` and `gh-release` storage backends are always
available with no extras; `rsync`, `rclone` and `s3-cli` shell out to
the corresponding binary on PATH.

## From PyPI (once published)

When the PyPI release lands, this becomes:

```bash
pip install cvcpkg
pip install "cvcpkg[server,db-sqlite]"   # extras work the same way
pip install --upgrade cvcpkg             # later upgrades
```

Until then `pip install cvcpkg` will not find the package — use one of
the methods above.

## Checking your toolchain

```bash
cvcpkg doctor
cvcpkg doctor --server https://cvcpkg.org   # also ping a registry
```

`cvcpkg doctor` verifies the host toolchain: Python (≥ 3.10), CMake and
a C/C++ compiler are required checks; pip, Ninja and git are reported
as warnings when missing. With `--server URL` (or `CVCPKG_SERVER_URL`)
it also checks that a registry answers on `/healthz`. It exits non-zero
if any required check fails, so it works as a CI/setup gate.

Only **installing prebuilt bundles** needs no toolchain at all —
archives are downloaded from the catalog and extracted. **Building
recipes from source** (`cvcpkg build`, `cvcpkg install
--fallback-to-source`) needs CMake, Ninja, and a C/C++ compiler;
individual recipes may need more (Meson, flex/bison, etc.).

## Verifying the install

```bash
cvcpkg --version
cvcpkg recipes --list          # the bundled recipe set
```

Both the standalone binaries and pip installs bundle the standard
recipes and schemas, so `cvcpkg recipes --list` should never be empty —
if it is, please file an issue (the release workflows validate a
bundled recipe before publishing).

## Installing packages

```bash
# Install components into a prefix
cvcpkg install zlib boost --prefix ./deps

# Or from a requirements file
cvcpkg install --from cvc-requirements.yaml --prefix ./deps

# Enforce signatures
cvcpkg install zlib --prefix ./deps --require-signatures

# Later, pull newer versions in place
cvcpkg upgrade --prefix ./deps --dry-run
cvcpkg upgrade --prefix ./deps
```

`cvcpkg install` writes a `cvcpkgConfig.cmake` into the prefix, so a
downstream CMake project can `find_package(cvcpkg CONFIG REQUIRED)` with no
manual `CMAKE_PREFIX_PATH` — see [cmake-integration.md](cmake-integration.md).

## Offline / air-gapped use

`cvcpkg` can install from a **local catalog file** and build missing
components from source:

```bash
# Use a catalog YAML you mirrored earlier
cvcpkg install zlib --prefix ./deps --catalog /path/to/catalog.yaml

# Build everything from local recipes, no network/registry
cvcpkg install --local zlib boost --prefix ./deps
```

Downloaded archives are cached and reused, so a warm cache also serves
offline installs. The cache lives at `$CVCPKG_CACHE` if set, else
`$XDG_CACHE_HOME/cvcpkg`, else `~/.cache/cvcpkg`.

## Upgrading cvcpkg itself

| Installed via | Upgrade with |
|---------------|--------------|
| Quick-install one-liner | Re-run the one-liner — it fetches the latest release. |
| Standalone binary (manual) | Download the newer asset and replace the binary. |
| Source checkout | `git pull && pip install .` |
| PyPI (once published) | `pip install --upgrade cvcpkg` |
