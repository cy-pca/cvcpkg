<img src="src/cvcpkg/server/assets/cyberpc-angel-gears.png" alt="CyberPC Angel, LLC" width="72" align="left" />

# cvcpkg

A [CyberPC Angel, LLC](https://cyberpcangel.com) project.

Cross-platform, language-agnostic package manager and binary archive for
the scientific computing community.

`cvcpkg` resolves a set of component requirements against a package
catalog, downloads the matching prebuilt bundles, verifies their integrity,
and materializes a single `CMAKE_PREFIX_PATH`-compatible install prefix —
or builds any component from source via versioned recipes when no prebuilt
bundle fits. It also runs as a self-hostable archive server with its own
build orchestration, multi-platform builders, and publishing pipeline.

**Downstream projects should adopt `cvcpkg` instead of manually managing
dependency archives by hand.** Per-component bundles are smaller,
cacheable, and version-locked — you only pull what you need.

## Quick start

```bash
# Install from PyPI (once published):
pipx install cvcpkg

# Or install from source:
pip install -e '.[progress]'

# List available components:
cvcpkg list --available

# Install specific components into a prefix:
cvcpkg install --prefix ./deps boost hdf5 fftw3

# Install from a requirements file:
cvcpkg install --from cvc-requirements.yaml --prefix ./deps

# Verify an existing prefix:
cvcpkg verify --prefix ./deps
```

## Installation: core vs extras

The core install is **`click` + `PyYAML`, and nothing else**:

```bash
pip install cvcpkg
```

That is the whole client. Resolving, downloading (`urllib`), sha256
verification, extraction, lockfiles, CMake config and activation scripts,
`cvcpkg build`, and the recipe tooling all run on those two — which is what
lets cvcpkg install on platforms whose wheel ecosystem is thin (see
[docs/haikuports-integration.md](docs/haikuports-integration.md)).

Everything else is an **extra**, named after the entry point that needs it.
Install one when you take on that role:

| Extra | Install | For |
|---|---|---|
| `remote` | `pip install 'cvcpkg[remote]'` | commands that talk to a cvcpkg server: `search`, `recipe pull`, `builds list/log/monitor`, `webhook …`, `token`/`user`/`org` admin, `doctor --server` |
| `publish` | `pip install 'cvcpkg[publish]'` | `publish`, `recipe push`, `recipe publish`, `builds submit-dag` |
| `builder` | `pip install 'cvcpkg[builder]'` | running a builder agent (`cvcpkg builder run`) |
| `signing` | `pip install 'cvcpkg[signing]'` | `cvcpkg key …`, `sign`, `verify-sig`, and `install --verify-signatures` |
| `validate` | `pip install 'cvcpkg[validate]'` | `cvcpkg validate` — checking a recipe or `components.yaml` against the bundled JSON-Schemas |
| `server` | `pip install 'cvcpkg[server,db]'` | running `cvcpkg-server` (ASGI stack + SQLAlchemy; add a `db*` extra for the driver) |
| `progress` | `pip install 'cvcpkg[progress]'` | download/extract progress bars |
| `s3`, `azure`, `gcs`, `sftp` | `pip install 'cvcpkg[s3]'` | storage backends for `publish --dest` |
| `all` | `pip install 'cvcpkg[all]'` | everything above |

`remote`, `publish` and `builder` all resolve to the same package (`httpx`);
the three names exist so the error you get names *your* command's extra.

Running a command whose extra is missing is never a traceback — it is one
line telling you what to install:

```
$ cvcpkg publish zlib
Error: httpx is required to talk to a cvcpkg server over HTTP. Install it with: pip install 'cvcpkg[publish]'
```

**Upgrading from cvcpkg ≤ 2.0.1?** `sqlalchemy`, `cryptography`, `httpx` and
`greenlet` used to be mandatory, so you had them whether you used them or not.
If you would rather not think about which role you are in, `pip install
'cvcpkg[all]'` is a superset of what you had before.

## Recipe ownership

This repository's `recipes/` set is the **shared dependency ecosystem**
(Boost, Qt6, VTK, CGAL, the CUDA-math libs, the Python interpreters, …).
**A project owns the recipe for its own package, in its own repo** — `libcvc`,
`volrover`, and `grl-snam` each keep their recipe under `cvcpkg/recipes/` in
their own repository and publish under the `cvc` org. Don't add project
packages here. See [docs/recipe-authoring.md](docs/recipe-authoring.md#recipe-ownership--where-a-recipe-lives).

## Build modes

cvcpkg supports two primary modes: **server mode** (default) and
**local mode**.  The mode determines where recipes and prebuilt
packages come from.

### Server mode (default)

By default, cvcpkg connects to a package server to fetch prebuilt
binaries and the latest recipe definitions.  The server is specified
by the `CVCPKG_SERVER_URL` environment variable or `--server` flag.
When neither is set, the official server at `https://cvcpkg.org` is
used.

```bash
# Install prebuilt binaries from the official server:
cvcpkg install zlib boost --prefix ./deps

# Build from recipes pulled from the server:
cvcpkg build zlib --prefix ./prefix

# Build all recipes (fetches latest from server):
cvcpkg build-all --prefix ./prefix

# Use a custom server:
export CVCPKG_SERVER_URL=https://pkg.mycompany.com
cvcpkg install --from cvc-requirements.yaml --prefix ./deps
```

### Local mode (`--local`)

Pass `--local` (or set `CVCPKG_LOCAL=1`) to skip all server
communication and use only bundled/local recipes.  This is useful for
air-gapped environments, offline development, or when you want to
build against a specific set of recipes without pulling updates.

```bash
# Build a recipe from local/bundled recipes:
cvcpkg build zlib --local --prefix ./prefix

# Build all recipes from local sources:
cvcpkg build-all --local --prefix ./prefix

# Install from source using local recipes (no catalog):
cvcpkg install --local zlib boost --prefix ./deps

# Combine with --recipes-dir to overlay custom recipes:
cvcpkg build zlib --local --recipes-dir ./my-recipes --prefix ./prefix
```

When `--local` is used with `cvcpkg install`, it implies
`--fallback-to-source` — all components are built from source recipes
rather than downloaded as prebuilt binaries.

You can also overlay additional recipe directories with `--recipes-dir`
(may be specified multiple times; later directories win on name
conflicts).

---

## cvcpkg as a build & configuration system

> **Status: planned (roadmap Phase 23).**  This section describes a
> direction, not shipped behavior.  It is documented here because it
> shapes the recipe format; the checkboxes live in `CVCPKG-ROADMAP.md`.

cvcpkg is not only for publishing packages.  A recipe set can be used as a
**general build system and configuration-management tool** — closer in
spirit to SaltStack/Ansible than to a plain package manager, but built into
one holistic, cross-platform, content-addressed system rather than bolted
on beside it:

- **Installing a recipe applies state**; **uninstalling tears it down.**
- A **machine's configuration is a dependency graph of recipes** — installing
  one triggers its dependent recipes and their state changes, in dependency
  order.
- Recipes can **initialize proprietary or licensed software and then layer
  legal, first-party modifications on top**, with the license and
  redistributability of every input declared explicitly in the recipe.
- **Bring-your-own (BYO)** recipes reference assets cvcpkg cannot legally
  redistribute (a licensed installer, retail game data, a client's
  proprietary blob): the *user* supplies the file, cvcpkg verifies it by
  a pre-published `sha256`, and never fetches or re-hosts it.

The model is deliberately **declarative-with-an-escape-hatch**: typed
`state:` resources (`file`, `template`, `service`, `env`, `registry-key`,
…) follow a Get/Test/Set contract so they are idempotent and
auto-reversible, while an explicit `script:` + `teardown:` pair handles
anything the built-ins do not cover.  Three modes, **no resident agent**:
`cvcpkg check` (audit/report-only), `cvcpkg apply`, and `cvcpkg uninstall`;
a scheduler (cron/CI) owns any enforcement loop.

**Honest limits (documented on purpose, so nothing over-promises):**

- **Teardown is authoritative inside the prefix, best-effort outside it.**
  Files cvcpkg tracks, it removes cleanly; state a recipe reaches out to
  mutate (system services, the registry, `/etc`) is reverted only by a
  declared inverse — a `teardown:` slot, or the captured prior value of a
  typed resource.  Recipes with an untracked imperative effect are *labeled
  non-revertible* in status output.
- **Apply is on-demand and non-atomic** — it corrects drift when you run it
  (Ansible-shaped), not continuously (Puppet-shaped).  A half-failed apply
  leaves a half-configured host; there is no automatic rollback of arbitrary
  scripts.
- **Idempotency is a per-recipe contract**, enforced in CI (apply twice →
  the second run is a no-op), not a magic property of the engine.

Every state operation is recorded in a per-machine, hash-chained,
append-only **transaction journal** (who, from where, what changed, with
per-file before/after hashes) that cross-anchors to the server audit log —
a tamper-evident paper trail for forensics that mainstream configuration
tools do not provide.  See `CVCPKG-ROADMAP.md` Phase 23 for the full design,
security model, and worked recipe examples.

## Activating a prefix

`cvcpkg install` writes shell activation scripts into the prefix in
the style of Python's `venv`.  Sourcing one of them prepends the
prefix to `PATH`, `CMAKE_PREFIX_PATH`, `PKG_CONFIG_PATH`, and the
platform's dynamic-linker variable (`LD_LIBRARY_PATH` on
Linux/BSD/WASI, `DYLD_LIBRARY_PATH` on macOS).  A matching
`cvcpkg_deactivate` function restores the previous environment.

| Shell                        | Command                                        |
|------------------------------|------------------------------------------------|
| bash / zsh / dash / sh       | `source ./deps/bin/activate`                   |
| fish                         | `source ./deps/bin/activate.fish`              |
| csh / tcsh                   | `source ./deps/bin/activate.csh`               |
| PowerShell (any OS)          | `. ./deps/Scripts/Activate.ps1`                |
| cmd.exe (Windows)            | `.\deps\Scripts\activate.bat`                  |

Deactivate:

```bash
cvcpkg_deactivate           # bash / zsh / fish / csh / PowerShell
.\deps\Scripts\cvcpkg_deactivate.bat   # cmd.exe
```

Environment variables exported after activation:

* `CVCPKG_ACTIVE_PREFIX` — the absolute prefix path (also used to
  detect a stale activation on re-source).
* `PATH` — prepended with `<prefix>/bin` (POSIX) or
  `<prefix>/Scripts` + `<prefix>/bin` (Windows).
* `CMAKE_PREFIX_PATH` — prepended with `<prefix>`.
* `PKG_CONFIG_PATH` — prepended with `<prefix>/{lib,lib64,share}/pkgconfig`
  for whichever of those directories exist.
* `LD_LIBRARY_PATH` / `DYLD_LIBRARY_PATH` — prepended with
  `<prefix>/lib` and `<prefix>/lib64` if present.

Set `CVCPKG_ACTIVATE_NO_PROMPT=1` before sourcing to skip the
`(<prefix-name>)` shell-prompt annotation.

The scripts are self-contained and safe to copy along with the prefix;
they do not require `cvcpkg` at activation time.

---

## Python packages & interpreter selection

A cvcpkg prefix can carry **several CPython interpreters side by side** —
`python311`, `python312`, `python313`, and the free-threaded `python313t` —
each installed as its own `<prefix>/bin/pythonX.Y` with its own
`<prefix>/lib/pythonX.Y/site-packages`. There is no global "the" Python; you
pick one.

**Selecting an interpreter** — just run the version you want:

```bash
source ./deps/bin/activate       # puts <prefix>/bin on PATH
python3.12 -c "import numpy; print(numpy.__version__)"
python3.11 my_script.py
python3        # bare python3 / python -> the prefix's DEFAULT interpreter (a symlink)
```

`python3` / `pip3` come from the `python3` meta package and `python` / `pip`
from the `python` meta package — both resolve to the prefix's DEFAULT
interpreter (currently python313). Install `python` when you want the
conventional commands; a prefix that only installed `python312` exposes
`python3.12` and nothing else, exactly as its dependency graph says. (An
**embedding host** like volrover3 does *not* choose at runtime — it links
`libpython3.11` at build time, so its embedded interpreter is fixed to that
version, and its recipe pins the matching columns.)

**Every Python package is a per-interpreter column recipe** —
`<name>-cp311`, `-cp312`, `-cp313`, `-cp313t` — one per interpreter cvcpkg
ships. A column depends on *its* interpreter (and on its deps' matching
columns) and installs only into that interpreter's own
`lib/pythonX.Y[t]/site-packages`. The dependency graph is the whole story:
installing `fastapi-cp313` gives `python3.13` a working fastapi and touches
nothing else; `numpy-cp313t` serves the free-threaded build and its import
check runs with the GIL genuinely disabled. How the *wheel* behind a column
is sourced varies, but the naming and import rules do not:

| Wheel kind | Columns that exist | Notes |
|---|---|---|
| **pure-Python** (`py3-none-any`) — e.g. `click`, `jinja2`, `sympy` | all four | same wheel in every column |
| **stable-ABI** (`abi3`) — e.g. `cryptography`, `bcrypt` | `cp311/312/313` (+ `cp313t` only if an exact free-threaded wheel exists) | the free-threaded build has no stable ABI |
| **per-version wheel** — e.g. `pydantic-core`, `markupsafe`, `cffi` | wherever upstream ships a wheel | `markupsafe-cp313t` exists; `pydantic-core-cp313t` does not (no wheel) |
| **built from source** — e.g. `numpy-cp311`, `h5py-cp311`, `vtk-python-cp31x`, `pyside6-cp311` | the columns we have built | extend by adding a column recipe |

A column exists **only if its whole dependency closure exists for that
interpreter**: `pydantic-core` ships no `cp313t` wheel, so there is no
`pydantic-cp313t` and no `fastapi-cp313t` — the catalog never promises an
import that cannot work. Adding a future `python314` is a new column, not a
rebuild of the existing ones.

`import numpy` from `python3.12` therefore means: the prefix's closure must
include `numpy-cp312` (install it, or depend on it). Requirements files and
recipes always name the `-cpNNN` column matching their interpreter.

Packages whose wheels install **console scripts** (`pytest`, `black`,
`uvicorn`, ...) declare `provides: [<base>]`: their columns clobber the same
`bin/` entry points, so the slot makes them mutually exclusive per prefix —
and lets `cvcpkg install pytest` resolve a column by its bare name. Library
columns coexist freely (their payloads live in disjoint site-packages).

---

## Recipe management

Recipes define how to build each component from source.  They live in
`recipes/` directories and contain a `recipe.yaml`, platform-specific
build scripts, and optional patches.

### Listing recipes

```bash
# List bundled/local recipes:
cvcpkg recipes

# List recipes on the server:
cvcpkg recipe list

# Show details of a specific recipe:
cvcpkg recipes --show grpc

# Filter by tag:
cvcpkg recipes --tag math
```

### Downloading recipes from the server

```bash
# Download a single recipe:
cvcpkg recipe pull zlib --output-dir ./recipes

# Download the full base recipe set:
cvcpkg recipe pull-all --output-dir ./recipes

# Download an organization's recipe set:
cvcpkg recipe pull-all --org my-org --output-dir ./org-recipes
```

### Pushing recipes to the server

```bash
# Needs the publish extra (httpx):  pip install 'cvcpkg[publish]'

# Push a single recipe (admin):
cvcpkg recipe push zlib

# Push and register as a placeholder package:
cvcpkg recipe publish zlib

# Push all recipes at once:
cvcpkg recipe push-all --recipes-dir ./recipes
```

`recipe publish` is a convenience command that pushes the recipe
bundle **and** registers a placeholder entry in the catalog.  The
placeholder tells consumers "this recipe exists" before any binary
has been built.  Remote builders or local users can then produce the
actual binaries.

---

## Remote builders

cvcpkg supports a remote build system where dedicated builder agents
poll the server for build jobs, execute them, and publish the results.
This replaces long-running CI workflows (some builds exceed 6 hours)
with persistent, uncapped build agents.

### Starting a builder

```bash
# Needs the builder extra (httpx):  pip install 'cvcpkg[builder]'

export CVCPKG_SERVER_URL=https://cvcpkg.org
export CVCPKG_TOKEN=cvctok_...

# Start a builder agent (platform and arch are auto-detected):
cvcpkg builder run \
    --name linux-x64-builder-1 \
    --max-jobs 4 \
    --work-dir /mnt/scratch/builder

# Start with wasm cross-compilation support:
cvcpkg builder run \
    --name linux-x64-builder-1 \
    --max-jobs 4 \
    --work-dir /mnt/scratch/builder \
    --cross-platform wasm

# Specify a non-default cross-arch:
cvcpkg builder run \
    --name linux-riscv-builder \
    --max-jobs 2 \
    --work-dir /mnt/scratch/builder \
    --cross-platform linux --cross-arch riscv64

# Start multiple builders for parallel builds:
cvcpkg builder run --name builder-2 &
cvcpkg builder run --name builder-3 &
```

Builders that pass `--cross-platform wasm` register the target in
their capabilities with a default arch of `wasm32`.  The scheduler
dispatches jobs to any builder whose `cross_platforms` list includes
a matching platform/arch pair, even though the builder's native
platform is linux or windows.  The builder automatically passes
`--host-platform` to the build so that the correct cross-compilation
scripts (e.g. `build-wasm.sh`) and toolchains (emsdk) are selected.

`--cross-arch` is paired positionally with `--cross-platform`.  If
omitted, sane defaults are applied:

| `--cross-platform` | Default `--cross-arch` |
|--------------------|------------------------|
| `wasm` | `wasm32` |
| `wasi` | `wasm32` |
| *(other)* | host architecture |

Builders register with the server and receive jobs via WebSocket (with
HTTP long-poll fallback).  Each job downloads the recipe from the
server, builds it, packages the result, and publishes the archive.

### Submitting builds

```bash
# Submit a single build job:
cvcpkg builds submit --recipe zlib --platform linux --arch x86_64

# Submit a dependency graph (DAG) of build jobs:
cvcpkg builds submit-dag \
    --recipe zlib --recipe zstd --recipe hdf5 \
    --platform linux --arch x86_64

# Submit wasm builds (dispatched to builders with --cross-platform wasm):
cvcpkg builds submit-dag \
    --recipe zlib --recipe zstd \
    --platform wasm --arch wasm32

# Wait for builds to finish (exits non-zero on failure):
cvcpkg builds submit-dag --wait \
    --recipe zlib --recipe boost \
    --platform linux --arch x86_64

# Monitor build progress:
cvcpkg builds list --status running
cvcpkg builds monitor           # top-like live dashboard
```

### Build log streaming

Remote builders capture full build output (cmake, make, gcc, etc.)
and stream it to the server in real time.  You can tail any job's
log or follow an entire DAG:

```bash
# View the full log for a completed job:
cvcpkg builds log <job-id>

# Follow a single job's output in real time (SSE stream):
cvcpkg builds log <job-id> --follow

# Follow ALL jobs in a DAG — multiplexed output with [builder/recipe/platform/arch] prefixes:
cvcpkg builds follow-dag <dag-id>
```

#### `builds log -f` vs `builds follow-dag`

| | `builds log <id> -f` | `builds follow-dag <dag-id>` |
|---|---|---|
| **Scope** | Single job (you supply the job ID) | All jobs in a DAG (discovered automatically) |
| **Output** | Raw build output, no prefix | Lines prefixed with `[builder/recipe/platform/arch]` |
| **Job discovery** | None — you must know the ID | Polls for new jobs as dependencies finish and they get dispatched |
| **Concurrency** | One stream | One thread per active job, interleaved |
| **Exit code** | 0 when stream ends | 0 if all succeed, 1 if any fail |
| **Best for** | Debugging a single build | CI pipelines, bulk build monitoring |

`follow-dag` is designed for CI pipelines where you need live output
from all builders at once.  It spawns a thread per active job, prints
prefixed lines as they arrive, and exits with code 0 if all jobs
succeed or code 1 if any fail.

The `populate-server.yml` GitHub Actions workflow uses this pattern:

```yaml
- name: Submit build DAGs
  id: submit
  run: |
    DAG_ID="populate-$(date +%Y%m%d-%H%M%S)"
    echo "dag_id=$DAG_ID" >> "$GITHUB_OUTPUT"
    cvcpkg builds submit-dag --dag-id "$DAG_ID" \
        --platform linux,freebsd --arch x86_64 \
        zlib boost hdf5

- name: Follow build output
  run: cvcpkg builds follow-dag "${{ steps.submit.outputs.dag_id }}"
```

### Listing and managing builders

```bash
# List registered builders:
cvcpkg builder list

# Check a specific builder:
cvcpkg builder status --name linux-x64-builder-1

# Unregister a builder:
cvcpkg builder unregister <builder-id>
```

---

## Using the official server

The official cvcpkg server at `https://cvcpkg.org` hosts prebuilt
binaries for all supported platforms and the canonical recipe set.

**As a consumer** — install prebuilt packages:

```bash
# No configuration needed — cvcpkg.org is the default:
cvcpkg install --from cvc-requirements.yaml --prefix ./deps
```

**As a contributor** — register and publish:

```bash
# Register for an API token:
cvcpkg register --server https://cvcpkg.org \
    --name alice --email alice@example.org --role publisher

# Set credentials:
export CVCPKG_SERVER_URL=https://cvcpkg.org
export CVCPKG_TOKEN=cvctok_...

# Publish recipes and packages:
cvcpkg recipe publish my-library
cvcpkg publish my-library --output-dir ./dist
```

---

## Self-hosted server and builder registry

You can run your own cvcpkg server for private packages, custom
recipes, or air-gapped environments.

### Setting up the server

```bash
# Install with server + database extras:
pip install 'cvcpkg[server,db]'

# Start with PostgreSQL:
export CVCPKG_DATABASE_URL="postgresql+asyncpg://user:pass@localhost/cvcpkg"
cvcpkg-server run \
    --state-dir /var/lib/cvcpkg \
    --host 0.0.0.0 --port 8080

# Bootstrap the first admin token:
cvcpkg-server bootstrap --name admin --email admin@example.org
```

### Populating with recipes

```bash
export CVCPKG_SERVER_URL=https://my-server.example.com
export CVCPKG_TOKEN=cvctok_<admin-token>

# Push all base recipes to your server:
cvcpkg recipe push-all --recipes-dir ./recipes

# Or push individual recipes:
cvcpkg recipe push zlib
cvcpkg recipe push boost
```

### Setting up builders

Run builder agents on each target platform (`pip install 'cvcpkg[builder]'`):

```bash
# On a Linux x86_64 build host (platform auto-detected):
cvcpkg builder run \
    --server https://my-server.example.com \
    --token cvctok_... \
    --name linux-builder \
    --max-jobs 4 --work-dir /scratch/builder

# On a macOS arm64 build host (platform auto-detected):
cvcpkg builder run \
    --server https://my-server.example.com \
    --token cvctok_... \
    --name macos-builder \
    --max-jobs 2 --work-dir ~/builder-work
```

### Building everything

```bash
# Submit DAG builds for all recipes:
cvcpkg builds submit-dag \
    --recipe zlib --recipe boost --recipe hdf5 ... \
    --platform linux --arch x86_64

# Or build locally and publish:
cvcpkg pack-all --local --output-dir ./dist
cvcpkg publish --all --output-dir ./dist
```

### Configuring clients

Point downstream consumers at your server:

```bash
export CVCPKG_SERVER_URL=https://my-server.example.com
cvcpkg install --from cvc-requirements.yaml --prefix ./deps
```

Or configure it in `~/.config/cvcpkg/config.yaml`:

```yaml
catalog:
  primary: https://my-server.example.com/v1/catalog
```

---

## Fresh deployment walkthrough

A complete guide to bootstrapping a cvcpkg server, creating API keys,
registering builders, and kicking off your first remote builds.

### 1. Deploy the server

Using Docker Compose (recommended for production):

```bash
# from the repo root
cp .env.production.example .env.production
# Edit .env.production — set POSTGRES_PASSWORD and BACKEND_BIND_ADDR

docker compose -f docker-compose.production.yml \
    --env-file .env.production up -d
```

Or run directly for development:

```bash
pip install 'cvcpkg[server,db]'
export CVCPKG_DATABASE_URL="postgresql+asyncpg://user:pass@localhost/cvcpkg"
cvcpkg-server run --state-dir /var/lib/cvcpkg --host 127.0.0.1 --port 8420
```

### 2. Create the first API keys

On a fresh server there are no tokens.  Use the server CLI (or
`docker compose exec`) to create them directly against the database:

```bash
# Create an admin token (full access: manage tokens, delete packages, etc.)
docker compose -f docker-compose.production.yml \
    --env-file .env.production exec -T backend \
    cvcpkg-server token create --name my_admin --role admin --email you@example.org

# Create a publisher token (for builders to push packages)
docker compose -f docker-compose.production.yml \
    --env-file .env.production exec -T backend \
    cvcpkg-server token create --name builder_publisher --role publisher

# Without Docker — if running cvcpkg-server directly:
cvcpkg-server token create --name my_admin --role admin --email you@example.org
cvcpkg-server token create --name builder_publisher --role publisher
```

Save the `cvctok_...` values that are printed — they are shown only
once and cannot be recovered.

Available roles:

| Role | Permissions |
|------|-------------|
| `admin` | Full access: create/revoke tokens, delete packages, manage orgs |
| `publisher` | Publish packages, push recipes, yank/unyank |
| `reader` | Browse catalog, download packages |

After you have an admin token, you can also create tokens via the API:

```bash
export CVCPKG_SERVER_URL=https://cvcpkg.org
export CVCPKG_TOKEN=cvctok_<admin-token>

cvcpkg token create --name ci_reader --role reader
cvcpkg token create --name another_publisher --role publisher --expires-in-days 90
cvcpkg token list
cvcpkg token revoke --name old_token

# Rotate a secret in place (name/role/org memberships survive); the old
# secret keeps working for an hour so CI secrets can be swapped calmly:
cvcpkg token rotate --name another_publisher --grace-minutes 60
```

### 3. Push recipes to the server

```bash
export CVCPKG_SERVER_URL=https://cvcpkg.org
export CVCPKG_TOKEN=cvctok_<admin-token>

# Push all recipes at once:
cvcpkg recipe push-all --recipes-dir ./recipes

# Or push individual recipes:
cvcpkg recipe push zlib
cvcpkg recipe push boost
```

### 4. Start builder agents

On each build machine, start a builder agent with the publisher token:

```bash
export CVCPKG_SERVER_URL=https://cvcpkg.org
export CVCPKG_TOKEN=cvctok_<publisher-token>

# Linux x86_64 builder (platform auto-detected):
cvcpkg builder run \
    --name linux-builder-01 \
    --max-jobs 4 \
    --work-dir /scratch/builder

# macOS arm64 builder (platform auto-detected):
cvcpkg builder run \
    --name macos-builder-01 \
    --max-jobs 2 \
    --work-dir ~/builder-work
```

Builders connect via WebSocket (with HTTP long-poll fallback),
register their platform capabilities, and wait for jobs.

### 5. Submit builds

```bash
export CVCPKG_SERVER_URL=https://cvcpkg.org
export CVCPKG_TOKEN=cvctok_<admin-token>

# Submit a single recipe:
cvcpkg builds submit --recipe zlib --platform linux --arch x86_64

# Submit a DAG of recipes (respects dependency order):
cvcpkg builds submit-dag \
    --recipe zlib --recipe boost --recipe hdf5 \
    --platform linux --arch x86_64

# Monitor progress (top-like dashboard):
cvcpkg builds monitor

# Follow a single job's build output in real time:
cvcpkg builds log <job-id> -f

# Follow all jobs in a DAG (great for CI):
cvcpkg builds follow-dag <dag-id>

# Pause/resume builds (e.g. to free builder capacity):
cvcpkg builds pause <job-id>
cvcpkg builds resume <job-id>
cvcpkg builds pause-dag <dag-id>
cvcpkg builds resume-dag <dag-id>

# Cancel builds:
cvcpkg builds cancel <job-id>
cvcpkg builds cancel-dag <dag-id>
```

### 6. Verify

```bash
# Check builders are connected:
cvcpkg builder list

# Check packages were published:
cvcpkg search --server https://cvcpkg.org

# Install a built package:
cvcpkg install zlib --prefix ./deps
```

---

## Migrating from GitHub CI builds

GitHub Actions has a 6-hour job time limit that is insufficient for
large dependency builds (e.g. Qt6, VTK, LLVM).  cvcpkg's remote
builder system eliminates this constraint:

1. **Set up a server** (see [Self-hosted server](#self-hosted-server-and-builder-registry))
   or use `https://cvcpkg.org`.

2. **Deploy builder agents** on persistent build machines (bare metal,
   VMs, or containers without time limits).

3. **Push your recipes** to the server:
   ```bash
   cvcpkg recipe push-all --recipes-dir ./recipes
   ```

4. **Submit builds** via the API or CLI:
   ```bash
   cvcpkg builds submit-dag --recipe zlib --recipe boost \
       --platform linux --arch x86_64
   ```

5. **Simplify CI** to just install prebuilt packages:
   ```yaml
   # .github/workflows/build.yml
   - name: Install dependencies
     run: |
       pip install cvcpkg
       cvcpkg install --from cvc-requirements.yaml --prefix ./deps

   - name: Build project
     run: cmake -S . -B build -DCMAKE_PREFIX_PATH=$PWD/deps && cmake --build build
   ```

Builders run on your own infrastructure with no time caps, and CI
jobs become fast install-only workflows (typically under 2 minutes).

---

## Integrating your downstream project

### Step 1: Create `cvc-requirements.yaml`

Place this file in your project root (e.g. alongside `CMakeLists.txt`):

```yaml
# cvc-requirements.yaml — declare which libcvc-deps components you need.
#
# cvcpkg resolves these against the published catalog and installs
# exactly the matching per-component bundles for your platform.

platform: auto          # auto-detect, or: linux | macos | windows
arch: auto              # auto-detect, or: x86_64 | arm64
config: release         # release | debug
link: shared            # shared | static

# Pin the libcvc-deps release to consume bundles from:
libcvc-deps: ">=1.2.0"

# Components your project needs — only these are downloaded:
components:
  - boost
  - hdf5
  - fftw3
  - tiff
  - vtk
  - qt6
```

### Step 2: Install dependencies

```bash
# Resolve, download, verify, and install into ./deps:
cvcpkg install --from cvc-requirements.yaml --prefix ./deps

# Or specify overrides on the command line:
cvcpkg install --from cvc-requirements.yaml --prefix ./deps \
  --config debug --link static
```

### Step 3: Point CMake at the prefix

```bash
cmake -S . -B build -DCMAKE_PREFIX_PATH="$(pwd)/deps"
```

All `find_package()` calls (Boost, HDF5, FFTW3, VTK, Qt6, etc.) will
resolve from the cvcpkg-managed prefix.

### Step 4: Lock for CI reproducibility

After a successful install, cvcpkg writes a lockfile:

```bash
# Commit this for reproducible CI builds:
git add cvcpkg.lock.yaml
```

Re-running `cvcpkg install` with a lockfile present replays the exact
same downloads (same SHA-256 digests), regardless of catalog updates.

### Example: CMake preset integration

```json
{
  "configurePresets": [{
    "name": "default",
    "cacheVariables": {
      "CMAKE_PREFIX_PATH": "${sourceDir}/deps"
    }
  }]
}
```

### Example: CI workflow

```yaml
- name: Install libcvc-deps
  run: |
    pip install cvcpkg
    cvcpkg install --from cvc-requirements.yaml --prefix ./deps

- name: Configure
  run: cmake -S . -B build -DCMAKE_PREFIX_PATH=${{ github.workspace }}/deps
```

## Publishing a downstream package

If you maintain a library that other CVC projects depend on, you can
publish it to the cvcpkg server so consumers can pull it with
`cvcpkg install`.

### Step 1: Write a recipe

Create `recipes/<your-package>/recipe.yaml`:

```yaml
# recipe.yaml
schema_version: 1

recipe:
  name: my-library
  upstream_version: "2.1.0"
  cvc_revision: 1
  description: "My library for CVC downstream consumers"

source:
  type: tarball
  url: "https://github.com/org/my-library/archive/refs/tags/v2.1.0.tar.gz"
  sha256: "<sha256-of-tarball>"

depends:
  build:
    - name: boost
      version: ">=1.83"
    - name: hdf5
      version: ">=1.10"
  runtime:
    - name: boost
      version: ">=1.83"
    - name: hdf5
      version: ">=1.10"

build:
  matrix:
    - platform: linux
      script: build.sh
    - platform: macos
      script: build.sh
    - platform: windows
      script: build.ps1

package:
  files:
    - "lib/**"
    - "include/**"
    - "share/**/cmake/**"
    - "bin/**"
  cmake_packages:
    - name: MyLibrary
      targets: ["MyLibrary::MyLibrary"]
```

Each `matrix` entry's `script` names a build file next to `recipe.yaml`
(not inline shell).  The script receives the staged source, the install
prefix, and the resolved dependency prefix via environment variables:

```bash
#!/bin/bash
# build.sh
set -e
cmake -S "$CVC_SOURCE_DIR" -B build \
  -DCMAKE_BUILD_TYPE="$CVC_BUILD_TYPE" \
  -DBUILD_SHARED_LIBS="$BUILD_SHARED_LIBS" \
  -DCMAKE_INSTALL_PREFIX="$CVC_INSTALL_DIR" \
  -DCMAKE_PREFIX_PATH="$CVC_DEPS_PREFIX"
cmake --build build --parallel
cmake --install build
```

### Step 2: Build the package

```bash
# Build using the recipe (fetches source, runs cmake, stages output):
cvcpkg build my-library --prefix ./stage \
  --config release --link shared

# Pack into a distributable archive:
cvcpkg pack my-library --prefix ./stage \
  --config release --link shared
```

### Step 3: Publish

```bash
# Needs the publish extra (httpx):  pip install 'cvcpkg[publish]'

# Publish to a cvcpkg-server (REST API):
export CVCPKG_TOKEN="cvctok_..."
export CVCPKG_SERVER_URL="https://cvcpkg.org"
cvcpkg publish my-library --output-dir ./dist
cvcpkg publish --all --output-dir ./dist

# Or publish to a storage backend (S3, SFTP, local dir):
cvcpkg publish --all --dest s3://my-bucket/cvcpkg/
cvcpkg publish --all --dest file:///shared/cvcpkg-repo/
```

### Server administration

```bash
# Install with server extras:
pip install cvcpkg[server]

# Start the server:
cvcpkg-server run --state-dir /var/lib/cvcpkg --host 0.0.0.0 --port 8080

# Bootstrap the first admin token on a fresh server:
cvcpkg-server bootstrap --name admin --email admin@example.org

# After that, manage tokens via the client CLI (through the API):
export CVCPKG_SERVER_URL=https://cvcpkg.org
export CVCPKG_TOKEN="cvctok_<admin-token>"
cvcpkg token create --name ci-publisher --role publisher
cvcpkg token create --name dev-reader --role reader

# View audit log:
cvcpkg-server audit log --last 20
cvcpkg-server audit verify
```

---

## Platform `any` (platform-independent packages)

Some packages are not compiled — they contain platform-independent
content such as HTML/CSS assets, ISO images, media files, data bundles,
or configuration archives.  cvcpkg supports a special **`any`** platform
for these recipes.

### Writing an `any` recipe

Set `platform: any` in every `build.matrix` entry.  The builder
automatically assigns `arch: noarch` and skips the CMake configure
marker check:

```yaml
# recipe.yaml
schema_version: 1

recipe:
  name: my-data-bundle
  upstream_version: "1.0.0"
  cvc_revision: 1
  description: "Platform-independent data files"
  kind: data          # optional — hints: data | media | config | iso

source:
  type: tarball
  url: "https://example.com/data-v1.0.0.tar.gz"
  sha256: "<sha256>"

build:
  matrix:
    - platform: any
      script: build.sh

package:
  files:
    - "share/**"
```

`script` names a file next to `recipe.yaml` (not inline shell).  The
build script receives the staged source and install prefix via
environment variables:

```bash
#!/bin/bash
# build.sh
set -e
mkdir -p "$CVC_INSTALL_DIR/share/my-data-bundle"
cp -r "$CVC_SOURCE_DIR"/* "$CVC_INSTALL_DIR/share/my-data-bundle/"
```

### How it works

| Aspect | Behaviour |
|--------|-----------|
| **Architecture** | Automatically set to `noarch` — no user override needed |
| **Build** | Included in *every* platform's `build-all` run so it is always available |
| **Cache key** | Uses `any/noarch` — the same artifact is shared across all platforms |
| **Dependencies** | Other recipes can depend on `any` packages; they are included regardless of the consuming platform |
| **Builder** | Maps `platform: any` to `ARCH=noarch` and skips the cmake marker |
| **Recipe `kind`** | Optional `recipe.kind` field (e.g. `data`, `media`, `config`, `iso`) is emitted as `meta.kind` in the manifest for downstream tooling hints |

### `cvc-requirements.yaml` usage

Consumers do not need to do anything special — `any` packages are
resolved automatically when listed as dependencies.  If you want to
pull an `any` package directly:

```yaml
platform: auto
components:
  - my-data-bundle    # resolved regardless of host platform
```

### Tags and metadata

`any` recipes support the same `tags` list as compiled recipes.  Tags
are emitted as `meta.tags` in the manifest (comma-joined) and displayed
on the package server front page.

---

## Authentication and Authorization

cvcpkg-server uses a **token-based RBAC** (role-based access control)
system.  Every mutating API call requires a bearer token; read-only
endpoints are unauthenticated by default but can be locked down.

### Server bootstrap

When setting up a new server for the first time, use the `bootstrap`
command to create the initial admin token:

```bash
cvcpkg-server bootstrap --name admin --email admin@example.org
```

This only works when no admin tokens exist yet.  The generated token
is printed exactly once — **store it in a password manager or secrets
vault** immediately.  Then configure the client:

```bash
cvcpkg config set server https://cvcpkg.org
cvcpkg config set token cvctok_<your-admin-token>
```

### Self-service registration

Users can register for an API token without contacting an admin.  The
server supports two **registration modes**, configured when starting
the server:

```bash
# Default: anyone can register and immediately gets a token
cvcpkg-server run --registration-mode open ...

# Admin-gated: registration requests go to a queue for admin approval
cvcpkg-server run --registration-mode admin-gated ...
```

The `CVCPKG_REGISTRATION_MODE` environment variable is also supported.

**Open mode (default):**

```bash
cvcpkg register --server https://cvcpkg.org \
  --name alice --email alice@example.org --role reader
# Token is returned immediately
```

**Admin-gated mode:**

```bash
# User submits a request:
cvcpkg register --server https://cvcpkg.org \
  --name bob --email bob@example.org --role publisher
# → "Registration request submitted. An admin will review it."

# Admin reviews pending requests:
cvcpkg token requests --status pending

# Approve a request (creates the token):
cvcpkg token approve 42
# → prints the token — send it to the requester

# Or deny it:
cvcpkg token deny 43
```

### Token lifecycle

Tokens are issued by an admin (or via self-service registration) and
shown **exactly once** at creation time.  Only an HMAC-SHA256 hash of
the token is persisted on the server — the raw secret is never stored.

```bash
# Create a publisher token via the client CLI (talks to the server API):
export CVCPKG_SERVER_URL=https://cvcpkg.org
export CVCPKG_TOKEN="cvctok_<admin-token>"
cvcpkg token create --name ci-publisher --role publisher

# Create a reader token with 90-day expiry:
cvcpkg token create --name dev-reader --role reader \
  --expires-in-days 90
```

The raw token looks like `cvctok_<base64url>`.  Store it securely
(e.g. in a CI secret) and pass it via the `CVCPKG_TOKEN` environment
variable or `Authorization: Bearer <token>` header.

### Roles

| Role        | Permissions                                                  |
|-------------|--------------------------------------------------------------|
| `reader`    | Query catalog, list packages, download archives              |
| `publisher` | All reader permissions plus publish packages, yank versions  |
| `admin`     | All permissions: publish, yank, unyank, delete, manage tokens, view audit log |

### Managing tokens (client CLI)

Use the `cvcpkg token` commands to manage tokens remotely via the
server's REST API.  This is the recommended approach — it goes
through the same code path as normal requests, records audit entries,
and avoids race conditions with the running server.

```bash
# Set the server and admin token (or pass --server/--token each time):
export CVCPKG_SERVER_URL=https://cvcpkg.org
export CVCPKG_TOKEN="cvctok_<admin-token>"

# Create a token:
cvcpkg token create --name ci-publisher --role publisher

# List all tokens:
cvcpkg token list

# Revoke a token immediately:
cvcpkg token revoke --name ci-publisher
```

Revoked tokens are rejected on the next API call — no restart needed.

> **Note:** `cvcpkg-server token create/list/revoke` commands exist for
> direct DB access when no server is running.  `cvcpkg-server bootstrap`
> is the recommended way to create the first admin token.  For all
> subsequent token management, use the client commands
> (`cvcpkg token ...`) which go through the HTTP API.

### Organization-level access control

Organizations have their own membership model.  An **org owner** can
add or remove members to control who can publish to the org's
namespace — without affecting the member's global token or access to
anything else.

```bash
# List members of an org:
cvcpkg org members my-org

# Add a member (org owners or global admins):
cvcpkg org add-member my-org --name ci-publisher --role member

# Remove a member (revokes org access only, token stays valid):
cvcpkg org remove-member my-org --name ci-publisher
```

| Org role  | Permissions                                              |
|-----------|----------------------------------------------------------|
| `member`  | Publish packages to the org's namespace                  |
| `owner`   | All member permissions plus add/remove members, update org settings |

### Locking down reads

By default, `GET /v1/catalog`, `GET /v1/packages`, and
`GET /v1/download/{filename}` are public.  To require authentication
for all endpoints, start the server with:

```bash
cvcpkg-server run --state-dir /var/lib/cvcpkg --require-auth-for-reads
```

---

## Package Serving

### How the catalog works

cvcpkg-server maintains an `index.yaml` file in `--state-dir` that
lists every published bundle (name, version, platform, arch,
build_type, link, SHA-256 digest, archive URL, and optional signature
metadata).  The index revision increments on each publish/yank/delete.

Clients call `GET /v1/catalog` to receive the full bundle list, then
`GET /v1/download/{filename}` to fetch individual archives.

### API endpoints

| Method | Path                                   | Auth          | Description                        |
|--------|----------------------------------------|---------------|------------------------------------|
| GET    | `/healthz`                             | none          | Server health + uptime             |
| GET    | `/v1/catalog`                          | reader/public | Full bundle catalog                |
| GET    | `/v1/packages`                         | reader/public | Paginated package listing          |
| GET    | `/v1/packages/{name}`                  | reader/public | Versions of a specific component   |
| GET    | `/v1/download/{filename}`              | reader/public | Download an archive                |
| POST   | `/v1/publish`                          | publisher     | Upload a new bundle                |
| POST   | `/v1/packages/{name}/{version}/yank`   | publisher     | Yank a version (soft delete)       |
| POST   | `/v1/packages/{name}/{version}/unyank` | admin         | Restore a yanked version           |
| DELETE | `/v1/packages/{name}/{version}`        | admin         | Permanently delete a version       |
| POST   | `/v1/tokens`                           | admin         | Create a new API token             |
| DELETE | `/v1/tokens/{name}`                    | admin         | Revoke a token                     |
| POST   | `/v1/tokens/{name}/rotate`             | admin/self    | Rotate a token's secret in place   |
| GET    | `/v1/tokens`                           | admin         | List all tokens                    |
| GET    | `/v1/audit`                            | admin         | Paginated audit log                |
| GET    | `/v1/audit/verify`                     | admin         | Verify audit chain integrity       |
| GET    | `/v1/orgs/{slug}`                      | public/member | Organization detail + members      |
| POST   | `/v1/orgs/{slug}/members`              | org owner     | Add a member to an organization    |
| DELETE | `/v1/orgs/{slug}/members/{token_name}` | org owner     | Remove a member from an organization |
| POST   | `/v1/builds`                           | publisher     | Submit a single build job            |
| POST   | `/v1/builds/dag`                       | publisher     | Submit a DAG of build jobs           |
| GET    | `/v1/builds`                           | publisher     | List builds (filterable)             |
| GET    | `/v1/builds/{job_id}`                  | publisher     | Get build job details                |
| POST   | `/v1/builds/{job_id}/cancel`           | publisher     | Cancel a pending/dispatched job      |
| POST   | `/v1/builds/{job_id}/pause`            | publisher     | Pause a pending/dispatched job       |
| POST   | `/v1/builds/{job_id}/resume`           | publisher     | Resume a paused job                  |
| POST   | `/v1/builds/dag/{dag_id}/cancel`       | publisher     | Cancel all pending/dispatched in DAG |
| POST   | `/v1/builds/dag/{dag_id}/pause`        | publisher     | Pause all pending/dispatched in DAG  |
| POST   | `/v1/builds/dag/{dag_id}/resume`       | publisher     | Resume all paused jobs in DAG        |

### SHA-256 integrity

Every archive receives a SHA-256 digest at publish time, recorded in
the catalog.  `cvcpkg install` verifies the digest after download
before extracting — a mismatch aborts the install.

---

## Publishing Packages

### Quick publish flow

```bash
# 1. Build a component from recipe:
cvcpkg build zlib --prefix ./stage \
  --config release --link shared

# 2. Pack to an archive:
cvcpkg pack zlib --prefix ./stage \
  --config release --link shared

# 3. Publish (to cvcpkg-server):
export CVCPKG_TOKEN="cvctok_..."
export CVCPKG_SERVER_URL="https://cvcpkg.org"
cvcpkg publish zlib --output-dir ./dist

# Or publish all archives in dist/:
cvcpkg publish --all --output-dir ./dist

# Publish to a storage backend instead:
cvcpkg publish --all --dest s3://my-bucket/cvcpkg/
```

### Signed publishing

To attach a cryptographic signature at publish time, first generate
a signing key (see [Package Signing](#package-signing) below), then
pass `--signing-key` during pack:

```bash
cvcpkg pack zlib --prefix ./stage \
  --config release --link shared \
  --signing-key ~/.config/cvcpkg/keys/release.key
```

The resulting archive will have a `.sig` sidecar file.  When the
archive is published to cvcpkg-server, the signature and key
fingerprint are stored in the catalog so consumers can verify.

### Yanking vs. deleting

**Yanking** is a soft delete: the archive stays on disk but `cvcpkg
install` will skip yanked versions (unless the lockfile pins one).
Only admins can **unyank**.

**Deleting** permanently removes the catalog entry.  Use with care —
consumers that pinned the deleted version will get download errors.

---

## Versioning and Revisions

### Version string format

Every published package has a version string of the form:

```
<upstream_version>+cvc.<cvc_revision>
```

For example, `1.86.0+cvc.1` means upstream Boost 1.86.0, CVC recipe
revision 1.  The `+cvc.N` suffix is SemVer build metadata — it is
ignored for range comparisons but used as a tiebreaker by the
resolver when multiple builds of the same upstream version exist.

The `cvc_revision` field in `recipe.yaml` controls the suffix:

```yaml
recipe:
  name: boost
  upstream_version: "1.86.0"
  cvc_revision: 1      # → published as 1.86.0+cvc.1
```

### Duplicate detection (publish conflicts)

The server rejects a publish with **HTTP 409 Conflict** if a package
with the same 6-field key already exists:

```
(name, version, platform, arch, build_type, link)
```

The error message is:

> `"{name}=={version} (...) already published.  Yank the existing
> version first, or use a new revision."`

Because the `version` field includes the `+cvc.N` suffix, bumping
`cvc_revision` produces a different version string and is **not**
considered a duplicate.  This is the intended mechanism for
re-publishing a corrected build of the same upstream version.

Note: yanking alone is **not** sufficient to re-publish — the
duplicate check does not filter yanked entries.  To re-publish the
exact same version string, an admin must **delete** the old entry
first.

### Bumping revisions with `rev-bump`

When a recipe needs a rebuild (patch fix, build script change,
dependency update), bump its `cvc_revision`:

```bash
# Bump zlib and all downstream dependents:
cvcpkg rev-bump zlib

# Output:
#   zlib: cvc_revision 1 → 2
#   hdf5: cvc_revision 3 → 4
#   vtk:  cvc_revision 1 → 2
```

The `--cascade` flag (default: on) automatically bumps every recipe
that transitively depends on the target.  This ensures the entire
dependency chain is rebuilt and re-published against the patched
version, catching breakage early rather than shipping an inconsistent
set of binaries.

**Why cascade?**  If a patch to `openssl` fixes a security issue,
every library linked against it (e.g. `grpc`, `protobuf`, `qt6`)
must be rebuilt to pick up the fix.  Publishing only the patched
`openssl` without rebuilding downstream would leave consumers with
binaries linked against the old, vulnerable version.  The cascade
ensures that either the full stack builds cleanly or the patch author
is forced to fix downstream breakage before publishing.

After bumping, the typical workflow is:

```bash
# 1. Bump revisions (edits recipe.yaml files in-place):
cvcpkg rev-bump openssl

# 2. Commit the bumped recipes:
git add recipes/ && git commit -m "rev-bump openssl + downstream"

# 3. Tag and push — CI rebuilds and publishes everything:
git tag v2.0.0 && git push origin v2.0.0
```

### Revision vs. version vs. catalog revision

| Term | Scope | Example | Purpose |
|------|-------|---------|---------|
| `upstream_version` | Recipe | `1.86.0` | The third-party project's own version |
| `cvc_revision` | Recipe | `3` | Rebuild counter for CVC-specific patches or build fixes |
| `version` (full) | Published package | `1.86.0+cvc.3` | Uniquely identifies this build in the catalog |
| Catalog `revision` | Server index | `42` | Monotonic counter incremented on each publish/yank/delete; used by clients to detect catalog staleness |

---

## Package Signing

cvcpkg supports **Ed25519 package signing** for publisher identity
verification.  The `cryptography` package is a required dependency
and is installed automatically with cvcpkg.

### Key management

Keys are stored in `~/.config/cvcpkg/keys/` (or
`$XDG_CONFIG_HOME/cvcpkg/keys/`) with three files per identity:

| File              | Contents                                        |
|-------------------|-------------------------------------------------|
| `<label>.key`     | PEM-encoded Ed25519 private key (mode 0600)     |
| `<label>.pub`     | PEM-encoded Ed25519 public key                  |
| `<label>.fp`      | SHA-256 fingerprint of the raw 32-byte public key (hex) |

#### Generate a keypair

```bash
cvcpkg key generate --label release

# Output:
# Generated key 'release'
#   Fingerprint: a1b2c3d4e5f6...
#   Private key: /home/user/.config/cvcpkg/keys/release.key
#   Public key:  /home/user/.config/cvcpkg/keys/release.pub
```

Optionally password-protect the private key:

```bash
cvcpkg key generate --label release --password "s3cret"
```

#### List keys

```bash
cvcpkg key list

# Output:
#   release              a1b2c3d4e5f67890…  (private+public)
#   upstream-qt          f0e1d2c3b4a59687…  (public only)
```

#### Import a publisher's public key

When a trusted publisher shares their public key, import it to
enable signature verification:

```bash
cvcpkg key import publisher-release.pub --label upstream
# Imported 'upstream' (f0e1d2c3b4a5…)
```

#### Export a public key

Share your public key with consumers:

```bash
cvcpkg key export --label release > release.pub
```

### Signing archives

#### Sign during pack

The easiest way: pass `--signing-key` to `cvcpkg pack` or
`cvcpkg pack-all` and the archive is signed automatically:

```bash
cvcpkg pack zlib --prefix ./stage \
  --config release --link shared \
  --signing-key ~/.config/cvcpkg/keys/release.key
```

This creates the archive **and** a `.sig` sidecar file.

#### Sign an existing archive

```bash
cvcpkg sign dist/zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.gz \
  --signing-key ~/.config/cvcpkg/keys/release.key

# Output:
# Signed: zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.gz.sig
#   (key: a1b2c3d4e5f6…)
```

### Signature format

Signatures are stored in `.sig` YAML sidecar files:

```yaml
signature: <base64url-encoded 64-byte Ed25519 signature>
key_fingerprint: <SHA-256 hex of the 32-byte Ed25519 public key>
```

The signature covers the **SHA-256 digest** of the archive contents
(not the raw file bytes directly), providing a standard
digest-then-sign construction.

### Verifying signatures

#### Verify a single archive

```bash
cvcpkg verify-sig dist/zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.gz

# Output:
# Verified: signed by 'release' (a1b2c3d4e5f6…)
```

The command looks for `<archive>.sig` by default, or use
`--sig-file` to specify a different path.

#### Verify during install

Pass `--verify-signatures` to `cvcpkg install` to verify every
downloaded archive before extraction:

```bash
cvcpkg install --prefix ./deps --verify-signatures boost hdf5 zlib
```

If a package in the catalog has a signature and the matching public
key is in your keyring, verification happens automatically.  If the
signature is invalid or the signing key is not trusted, installation
aborts with a clear error.

### Trust model

1. **Key generation**: Each publisher generates their own Ed25519
   keypair with `cvcpkg key generate`.

2. **Key distribution**: The publisher shares their `.pub` file
   out-of-band (e.g. committed to the repo, posted on a website,
   or exchanged directly).

3. **Key import**: Consumers import the publisher's public key with
   `cvcpkg key import`.

4. **Verification**: When `--verify-signatures` is enabled,
   cvcpkg checks the archive's signature against the local keyring.
   It first tries the key whose fingerprint matches the catalog
   entry, then falls back to trying all trusted keys (to support
   key rotation).

5. **Non-repudiation**: The server records the signature and key
   fingerprint in the catalog at publish time, providing an audit
   trail of who signed each package.

### CI signing workflow

```yaml
- name: Sign and publish
  env:
    CVCPKG_TOKEN: ${{ secrets.CVCPKG_PUBLISHER_TOKEN }}
    SIGNING_KEY: ${{ secrets.SIGNING_PRIVATE_KEY }}
  run: |
    # Write the signing key from CI secrets:
    mkdir -p ~/.config/cvcpkg/keys
    echo "$SIGNING_KEY" > ~/.config/cvcpkg/keys/ci.key
    chmod 600 ~/.config/cvcpkg/keys/ci.key

    # Build, pack (with signature), and publish:
    cvcpkg build $COMPONENT --prefix ./stage
    cvcpkg pack $COMPONENT --prefix ./stage \
      --signing-key ~/.config/cvcpkg/keys/ci.key \
      --config release --link shared
```

---

## Audit Trail

cvcpkg-server maintains a tamper-evident, append-only audit log.
Every mutation (publish, yank, unyank, delete, token create, token
revoke) is recorded with:

- **Timestamp** (UTC)
- **Action** (the operation performed)
- **Actor** (the token name that performed it)
- **Target** (the component or token affected)
- **Detail** (platform, SHA-256, etc.)
- **Chain hash** (SHA-256 of the previous entry for tamper detection)

### Viewing the log

```bash
# Last 20 entries:
cvcpkg-server audit log --last 20

# Filter by action:
cvcpkg-server audit log --action publish

# Filter by target:
cvcpkg-server audit log --target "boost==1.86.0+cvc.1"
```

### Verifying integrity

```bash
cvcpkg-server audit verify

# Output (if intact):
# chain intact (142 entries)
```

The verify command walks the full chain and checks that each entry's
`prev_sha256` matches the hash of the preceding entry.  A broken
chain indicates tampering or data corruption.

---

## Build Directory Configuration

By default, cvcpkg creates intermediate build trees in the system temp
directory (`$TMPDIR`, `/tmp`, etc.).  For large builds this can exhaust
space on small temp partitions, or be slow on non-SSD storage.

Use **`--work-dir`** (or the **`CVCPKG_WORK_DIR`** environment variable)
to redirect build trees to a dedicated volume:

```bash
# Point builds at a fast NVMe scratch partition:
cvcpkg build-all --work-dir /mnt/scratch/cvcpkg-builds \
    --config release --link shared

# Or set it globally via environment:
export CVCPKG_WORK_DIR=/mnt/scratch/cvcpkg-builds
cvcpkg pack-all --config release --link shared
```

The directory is created automatically if it doesn't exist.  Each recipe
gets its own sub-directory under `--work-dir` (e.g.
`/mnt/scratch/cvcpkg-builds/cvcpkg-zlib-XXXXXXXX/`).

When `--work-dir` is not set, the default prefix directory for
`build-all` (when `--prefix` is also omitted) is likewise placed in the
system temp directory.

---

## Mirror Support

cvcpkg-server supports **mirror mode**, where a read-only replica
syncs its catalog from an upstream primary and proxies archive
downloads on demand.  Clients automatically discover healthy mirrors
and use them as fallback download sources.

### Setting up a mirror

Start a mirror server pointing at an upstream primary:

```bash
cvcpkg-server run \
    --mirror-mode \
    --mirror-upstream https://cvcpkg.org \
    --mirror-token cvctok_... \
    --database-url postgresql+asyncpg://user:pass@localhost/mirror_db \
    --state-dir ./mirror-data \
    --port 8421
```

| Flag | Env var | Description |
|------|---------|-------------|
| `--mirror-mode` | `CVCPKG_MIRROR_MODE` | Enable read-only mirror mode |
| `--mirror-upstream` | `CVCPKG_MIRROR_UPSTREAM` | Upstream server URL (required) |
| `--mirror-token` | `CVCPKG_MIRROR_TOKEN` | Token for upstream auth |
| `--mirror-sync-interval` | `CVCPKG_MIRROR_SYNC_INTERVAL` | Catalog sync interval in seconds (default: 3600) |

Mirror-mode servers reject publish and upload requests (HTTP 403) and
periodically sync the catalog from the upstream.  Archive files are
fetched on first request and cached locally.

### Registering mirrors with the primary

Mirrors register themselves with the primary so clients can discover
them:

```bash
curl -X POST https://cvcpkg.org/v1/mirrors/register \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://eu.cvcpkg.org", "display_name": "EU Mirror", "contact": "ops@eu.cvcpkg.org"}'
```

The primary health-checks registered mirrors every 5 minutes.  After
3 consecutive failures a mirror is marked unhealthy and removed from
the client mirror list.  Re-registering clears rejection/unhealthy
state.

### Admin mirror management

```bash
# List all mirrors (admin-only, includes rejected/unhealthy)
curl -H "Authorization: Bearer $ADMIN_TOKEN" https://cvcpkg.org/v1/mirrors/all

# Reject a mirror
curl -X POST "https://cvcpkg.org/v1/mirrors/reject?url=https://bad.example.com" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Permanently remove a mirror
curl -X DELETE "https://cvcpkg.org/v1/mirrors?url=https://old.example.com" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

### Client mirror failover

When `CVCPKG_SERVER_URL` is set, the `install` and `sync` commands
automatically fetch the mirror list from the server and inject mirror
URLs as fallback download sources.  If the primary download fails,
mirrors are tried in order.

```bash
export CVCPKG_SERVER_URL=https://cvcpkg.org
cvcpkg install --from cvc-requirements.yaml --prefix ./deps
```

### Downloading archives without installing

The `download` command fetches archives to a local directory without
extracting them:

```bash
# Download specific components
cvcpkg download zlib boost --output-dir ./archives

# With mirror failover
cvcpkg download zlib --server https://cvcpkg.org -o ./dist

# Pin a version
cvcpkg download zlib==1.3.1+cvc.1 -o ./dist --config debug
```

---

## Cleaning Up Work Directories

When builds are interrupted or crash, they can leave behind orphaned
`cvcpkg-*` temporary directories in the system temp folder.  The
`clean` command removes them:

```bash
# Remove work directories older than 2 hours (default):
cvcpkg clean

# Preview what would be removed:
cvcpkg clean --dry-run

# Remove directories older than 30 minutes:
cvcpkg clean --older-than 30

# Remove all cvcpkg work directories regardless of age:
cvcpkg clean --all

# Target a specific parent directory:
cvcpkg clean --work-dir /mnt/scratch
```

The CI workflows run `cvcpkg clean` automatically before and after
builds to prevent disk-full failures on shared runners.

---

## Troubleshooting

### 502 errors during publish

When many CI runners publish archives concurrently (e.g. a tagged
release building 4 macOS configs × 16 packages), the cvcpkg-server
backend can run out of memory and restart, causing the reverse proxy
to return **502 Bad Gateway**.

**Checklist:**

1. **Container memory limit** — Ensure the backend container has
   enough memory for concurrent uploads.  In
   `docker-compose.production.yml`, set `deploy.resources.limits.memory`
   to at least 4–8 GB for production workloads with many concurrent
   publishers.

2. **Reverse proxy body limit** — If using Apache, the
   `LimitRequestBody` directive must be large enough for the biggest
   archive (e.g. emsdk at ~840 MB).  Set it to at least 1.1 GB:
   ```
   LimitRequestBody 1153433600
   ```
   For nginx, use `client_max_body_size 1100m;`.

3. **Proxy timeout** — Large chunked uploads can take several minutes.
   Ensure your proxy timeout is at least 900 s (`ProxyTimeout 900` in
   Apache, `proxy_read_timeout 900s` in nginx).

**Symptoms:** Container restart count > 0 (`docker inspect <container>
--format '{{.RestartCount}}'`), 502 responses in the proxy access log
concentrated in a short time window.

---

## Development

```bash
# from the repo root
pip install -e '.[progress,server]'
pytest
```

### Running with coverage

```bash
pytest --cov=cvcpkg --cov-branch --cov-report=html:htmlcov tests/
open htmlcov/index.html
```

Coverage reports are also generated as CI artifacts on every push/PR —
download them from the workflow run's Artifacts section.

## Contributors

cvcpkg is a **[CyberPC Angel, LLC](https://cyberpcangel.com)** project —
designed, funded, and maintained by the CyberPC Angel team, who own the
project's intellectual property.

Community contributions are welcome via pull request; see the full list of
everyone who has contributed on the
[contributors page](https://github.com/transfix/libcvc-deps/graphs/contributors).

## License

MIT — Copyright (c) 2026 CyberPC Angel, LLC. See [LICENSE](LICENSE) for the
full text.
