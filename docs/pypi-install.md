# Installing cvcpkg from PyPI

`cvcpkg` is distributed on PyPI as the **`cvcpkg`** package. It ships two
console entry points — `cvcpkg` (the client) and `cvcpkg-server` (the
registry server) — plus the bundled recipe set used for building from
source.

## Quick start

```bash
pip install cvcpkg
cvcpkg --version
cvcpkg doctor          # check your local toolchain is ready
```

`cvcpkg doctor` verifies Python, CMake, Ninja, a C/C++ compiler, and git,
and (with `--server URL`) that a registry is reachable.

## Requirements

- **Python** ≥ 3.10.
- For **installing** prebuilt bundles: nothing else — archives are
  downloaded from the catalog and extracted.
- For **building** recipes from source (`cvcpkg build`, `cvcpkg install
  --fallback-to-source`): CMake, Ninja, and a C/C++ compiler. Individual
  recipes may need more (Meson, flex/bison, etc.); `cvcpkg doctor` reports
  the basics.

## Core vs extras

As of **2.0.2**, `pip install cvcpkg` resolves to **`click` + `PyYAML`, and
nothing else**. That is the whole client: resolving the catalog, downloading
(`urllib.request`), sha256 verification, extraction, lockfiles, the
`cvcpkgConfig.cmake` and activation scripts, `cvcpkg build`, and the recipe
tooling all run on those two. Keeping the mandatory list at two is what makes
cvcpkg installable where the wheel ecosystem is thin — see
[haikuports-integration.md](haikuports-integration.md), where four
mandatory-but-never-imported distributions were the entire blocker.

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

> **Upgrading from cvcpkg ≤ 2.0.1?** `httpx`, `cryptography`, `sqlalchemy` and
> `greenlet` used to be mandatory, so you had them whether or not you used
> them. `pip install "cvcpkg[all]"` is a superset of what you had before.

## Extras

Install optional dependency groups with pip extras:

| Extra | Installs | When you need it |
|-------|----------|------------------|
| _(none)_ | click, PyYAML | `install`, `upgrade`, `build`, `pack`, `init`, and the rest of the recipe tooling — the whole client. |
| `remote` | httpx | Any command that talks to a cvcpkg registry: `search`, `recipe pull`/`list`, `builds list`/`log`/`monitor`/`purge`, `webhook …`, `token`/`user`/`org` admin, `yank`/`unyank`/`nuke`, `doctor --server`. |
| `publish` | httpx | Publishing: `publish`, `recipe push`, `recipe push-all`, `recipe publish`, `builds submit-dag`/`follow-dag`. |
| `builder` | httpx | Running a builder agent: `cvcpkg builder run`/`list`/`logs`. |
| `signing` | cryptography | Ed25519 key management (`cvcpkg key …`, `sign`, `verify-sig`) and `install --verify-signatures`/`--require-signatures`. |
| `validate` | jsonschema | `cvcpkg validate`: checking `recipe.yaml` / `components.yaml` against the bundled Draft 2020-12 schemas. Recipe authoring only — installing a package never reads a schema. |
| `progress` | tqdm | Download/extract progress bars. |
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
| `all` | everything above | One-word migration from ≤ 2.0.1; also what CI installs to run the full test suite. |

Extras combine — e.g. a SQLite-backed server is `server` + `db-sqlite`:

```bash
pip install "cvcpkg[server,db-sqlite]"   # server on SQLite
pip install "cvcpkg[production]"          # server on PostgreSQL
pip install "cvcpkg[server,db-all]"       # server, any DB backend
pip install "cvcpkg[publish,signing]"     # publish signed packages
pip install "cvcpkg[all]"                 # everything
```

If you are scripting an install for CI or a deployment, pick the extra that
matches the command that host actually runs — a builder host wants `[builder]`,
a host that runs `recipe push` or `builds submit-dag` wants `[publish]`.

## Verifying the install

```bash
cvcpkg --version
cvcpkg-server --version
cvcpkg recipes --list          # the bundled recipe set
```

If `cvcpkg recipes --list` is empty, the wheel was built without its
recipes — please file an issue (the release workflow bundles and verifies
them).

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

Downloaded archives are cached under the cvcpkg cache dir and reused, so a
warm cache also serves offline installs.

## Upgrading cvcpkg itself

```bash
pip install --upgrade cvcpkg
```
