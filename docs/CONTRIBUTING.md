# Contributing

This is the canonical contribution guide for cvcpkg. It covers the pull
request workflow, local development setup, running the test suite, and the
recipe-contribution quick start. For deep dives, see
[recipe-authoring.md](recipe-authoring.md), [BUILDING.md](BUILDING.md), and
[ci-cd-pipeline.md](ci-cd-pipeline.md).

## Design principles

Carried from the project roadmap — changes should not fight these:

1. **Simplicity over cleverness.** A graduate student should be able to
   understand the system in an afternoon. One CLI, one server, one database.
2. **Reproducibility is non-negotiable.** If a build worked yesterday, it must
   work next year. Pinned versions, checksums, signed packages.
3. **Cross-platform is a first-class citizen.** Every recipe must build on
   Linux, macOS, and Windows or clearly document platform restrictions.
4. **No vendor lock-in.** Commodity hardware, open source software, HTTP +
   JSON. Any client can interoperate.
5. **Security by default.** TLS everywhere, signed packages, tamper-evident
   audit trail, role-based access — without unnecessary complexity. HMAC
   tokens for machine auth, delegated OIDC for humans.
6. **Community-first.** Recipes are plain YAML — no DSL to learn. Publishing
   is a single CLI command.
7. **Data-driven decisions.** Analytics and telemetry (always opt-in) help
   prioritize effort.

## Pull request workflow

- **Never commit to `master` directly.** Branch, push, and open a pull
  request — even for small fixes.
- **PRs are squash-merged**, one commit per PR with the PR number in the
  subject (e.g. `fix(builder): publish cached cross-toolchains atomically
  (#518)`). Write a PR title that works as that commit subject. This has been
  the consistent convention since 2026-08-18; older `master` history
  predates it and contains merge commits.
- **Formatting is enforced with `black`** (line length 100, configured in
  `../pyproject.toml`). CI fails on `black --check src/ tests/`; `ruff check`
  and `ruff format --check` also run but are advisory. Run `black src/ tests/`
  before pushing.
- **License headers are checked** with
  `python scripts/apply_headers.py --check`.

## Local development setup

The project is packaged with Poetry, but a plain editable pip install works
(an in-tree PEP 517 backend syncs `recipes/` into the package at build time):

```bash
git clone https://github.com/transfix/libcvc-deps.git
cd libcvc-deps
pip install -e ".[server]"     # CLI + FastAPI server
pip install pytest black       # test/format tools (or use poetry, below)
```

Or with Poetry, which also brings in the dev group (pytest, black, ruff,
mypy):

```bash
poetry install --with dev --extras server
```

Useful extras (defined in `../pyproject.toml`): `server` (FastAPI/uvicorn),
`production` (server + asyncpg + alembic), `db`, `db-sqlite`, `db-mysql`,
`db-all`, `progress` (tqdm), and the storage backends `s3`, `azure`, `gcs`,
`sftp`, `storage-all`.

Entry points: `cvcpkg` (the CLI) and `cvcpkg-server` (runs and administers
the archive server).

## Running tests

The suite lives under `tests/` in three tiers:

| Tier | Path | What it needs |
|---|---|---|
| Unit | `tests/unit/` | Nothing — fast, hermetic |
| Integration | `tests/integration/` | Optional live server; server-dependent modules auto-skip when nothing answers `/healthz` at `CVCPKG_TEST_SERVER_URL` (default `http://127.0.0.1:8421`) |
| End-to-end | `tests/e2e-live/` | Docker; run via `tests/e2e-live/run-e2e.sh` |

```bash
python -m pytest tests/unit/ -v --tb=short          # what ci.yml runs
python -m pytest tests/integration/ -v --tb=short   # slower; auto-skips server tests
```

CI runs the unit and integration suites as **separate pytest processes**
(see `../.github/workflows/cvcpkg-ci.yml`) — mirror that locally rather than
one combined run, so cross-suite environment pollution can't hide.

Pytest is configured in `../pyproject.toml` with `pythonpath = ["src"]`, so
the suite always tests the working tree — a stale `cvcpkg` installed into
site-packages will not shadow your changes.

## Contributing a recipe

First check where the recipe belongs: this repo's `recipes/` tree is the
**shared dependency ecosystem** (~750 third-party libraries and toolchains).
First-party CVC projects keep their own recipe in their own repository and
overlay it with `--recipes-dir` — see
[recipe-authoring.md](recipe-authoring.md).

Quick start:

```bash
# 1. Scaffold — blank template, or generated from an existing project
cvcpkg init mylib --build-system cmake --version 1.2.3 \
    --url https://example.org/mylib-1.2.3.tar.gz
cvcpkg generate ../myproject          # alternative: detect build system + metadata

# 2. Validate schema, build-script references, and dependency resolution
cvcpkg validate                       # everything
cvcpkg validate recipes/mylib         # just yours

# 3. Build it locally
cvcpkg build mylib --prefix ./prefix              # deps already in ./prefix
cvcpkg build mylib --prefix ./prefix --with-deps  # or build the closure too

# 4. Pack into a distributable archive
cvcpkg pack mylib --output-dir dist
```

Notes:

- `cvcpkg validate` checks schemas and reference integrity; it does **not**
  execute build scripts. A recipe can validate green and still fail to build,
  so always run step 3 before opening the PR.
- Published variants are **immutable** — republishing the same
  name/version/platform/arch/config/link tuple is rejected with a 409. When
  changing an already-published recipe, bump `cvc_revision` (the `+cvc.N`
  version suffix). `cvcpkg pack --bump` queries the server and packs one above
  the highest published revision; see also `cvcpkg rev-bump`,
  `cvcpkg next-revision`, and `cvcpkg cascade-bump` for dependents.

### Recipe PR CI

A PR that touches `recipes/**` triggers
`../.github/workflows/pr-recipe-build-dev.yml`: the changed recipes are pushed
to the internal dev cvcpkg-server, built on the dev builder cluster, and the
per-recipe results are commented back on the PR. This runs for same-repo PRs
only — fork PRs deliberately cannot reach the self-hosted runner or the dev
cluster secrets.

Schema validation and the recipe dependency-graph checks also run on PRs in
`../.github/workflows/ci.yml`, with no builders involved.

## Documentation

Docs live in `docs/` and are plain Markdown.

**Diagrams are Mermaid.** Charts, graphs, and architecture diagrams use fenced
`mermaid` code blocks (GitHub renders them natively) rather than ASCII art.
When editing a document that still contains an ASCII diagram, convert it to
Mermaid as part of the change.

Good starting points: [getting-started-tutorial.md](getting-started-tutorial.md),
[recipe-authoring.md](recipe-authoring.md), [USAGE.md](USAGE.md), and the
roadmap under `roadmap/`.
