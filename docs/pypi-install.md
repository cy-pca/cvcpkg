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

## Extras

Install optional dependency groups with pip extras:

| Extra | Installs | When you need it |
|-------|----------|------------------|
| _(none)_ | client + build tooling | Using `cvcpkg` to install/build packages. |
| `server` | FastAPI, Uvicorn, Pydantic, python-multipart | Running `cvcpkg-server` with the file backend. |
| `db-sqlite` | aiosqlite, Alembic | Server on a SQLite database. |
| `db` | asyncpg, Alembic | Server on PostgreSQL. |
| `db-mysql` | aiomysql, Alembic | Server on MySQL/MariaDB. |
| `db-all` | asyncpg, aiosqlite, aiomysql, Alembic | Server on any of the above. |
| `production` | `server` + asyncpg + Alembic | Production server on PostgreSQL. |

Extras combine — e.g. a SQLite-backed server is `server` + `db-sqlite`:

```bash
pip install "cvcpkg[server,db-sqlite]"   # server on SQLite
pip install "cvcpkg[production]"          # server on PostgreSQL
pip install "cvcpkg[server,db-all]"       # server, any DB backend
```

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
