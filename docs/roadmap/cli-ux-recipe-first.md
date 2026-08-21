# Roadmap: CLI UX and the recipe-first workflow

**Status:** Partially implemented — the core recipe-first build loop has
shipped (see [Shipped](#shipped)); everything under
[Remaining design](#remaining-design) is still planned, and required before
the v2.0.0 PyPI release.

> Extracted 2026-08-21 from [CVCPKG-ROADMAP.md](CVCPKG-ROADMAP.md) Phase 15
> ("CLI UX & the Recipe-First Workflow"), which now carries only a summary.
> This document is the full design and the working reference for the
> remaining items.

The developer-facing polish pass: make recipes (not requirements files) the
one way to describe a build, give cvcpkg a stable per-user home under
`~/.cvcpkg/`, make install prefixes first-class managed objects, and make
the terminal experience worthy of the web front end.

---

## Shipped

Verified against the CLI/server code and `git log`:

| What | PR |
|---|---|
| `cvcpkg install-deps <recipe>` — install a recipe's dependency closure prebuilt | #348 |
| One-shot build from a repo root: `--no-deps` default, CWD `./recipes` overlay, `--incremental` | #401 |
| `cvcpkg validate` runs from any repo: schemas ship in the package, repeatable `--recipes-dir`, path targets, cross-recipe missing-dependency check | #349 |
| `cvcpkg generate`, grouped `--help`, server-side recipe browsing, `world` marked legacy | #503 |

Detail:

- **`cvcpkg install-deps <recipe>`** (#348) — "just recipes": point at a
  recipe (path, directory, or name) and cvcpkg installs its build + runtime
  dependency closure, transitively resolved, as prebuilt bundles into
  `--prefix`. Host-tool deps (cmake/ninja/swig/…) are excluded by default;
  `--include-host-tools` adds them.
- **`build --no-deps` is the default** (#401) — `cvcpkg build <name>` builds
  only the named recipe(s) and assumes their deps are already in `--prefix`;
  `--with-deps` is the opt-in to compile the whole closure from source in
  topological order. A `./recipes` directory in the current working
  directory is auto-overlaid on the recipe search path (later wins), so a
  repo root with vendored recipes needs no `--recipes-dir`. `--incremental`
  reuses a stable build tree (under `~/.cache/cvcpkg/incremental`) so
  re-runs recompile only what changed.
- **`cvcpkg validate` portability** (#349) — the checking logic and JSON
  schemas moved into the installed package (`cvcpkg/validation.py` +
  `cvcpkg/schemas/`), so `validate` runs from any repo's CI with
  `pip install cvcpkg` alone. It takes the same recipe surface as
  `build`/`pack` (repeatable `--recipes-dir`, `--no-default-recipes`, a path
  target) and checks schema, build-script/patch existence, minted-version
  order, and cross-recipe missing dependencies over the merged set.
- **`cvcpkg generate`** (#503) — scaffolds a recipe from a project that
  already builds: detects CMake, Autotools, Meson, plain Makefile, or
  Python packaging (PEP 621 `pyproject.toml`, Poetry, `setup.cfg`), reads
  the metadata the build system already declares, and emits a recipe.
  Detected dependencies are matched against the real recipe set (unmatched
  ones become commented suggestions, so the output always validates), and
  anything guessed is marked TODO.
- **Grouped `--help`** (#503) — the ~45 top-level commands are listed under
  task headings ("Find and install packages", "Recipes and building", …)
  instead of one flat alphabetical list; unlisted commands still appear
  under "Other".
- **Server-side recipe browsing** (#503) — read endpoints under
  `/v1/recipe/{name}` (`/files`, `/file`, `/archive`) serve a published
  recipe's sources to anyone who can see the package, and the landing page
  grew a matching in-place file viewer (shared `_common` helpers tagged as
  such). The `/archive` form extracts to a well-formed recipe directory
  usable with `cvcpkg build <name> --recipes-dir <dir>`.
- **`world` is marked legacy** (#503) — its help text now steers to the
  recipe-first loop (`install-deps` + `build`), and `add`/`remove` sit under
  a "Requirements files (legacy)" help section.
- **`cvcpkg uninstall`** (#522) — the install-conflict error in
  `cli/_install.py` had told users to run this command since before it
  existed; it now does, ahead of `prefix.db`. It derives each package's
  owned-file set from the bundle archive the lockfile names (the archive
  member list is exactly what extraction materialized), supports `--cascade`
  (removes the dependent closure, refusing by default when dependents
  exist) and `--dry-run`, and refuses on source-built entries — there is no
  archive to enumerate their files from. See "Per-prefix state database"
  below for what the archive-derived approach cannot do yet (post-install
  file writes, modified-file drift, a teardown hook) and what `prefix.db`
  fixes.

The rest of the phase is unimplemented. In particular `cvcpkg verify` hashes
no files, `cvcpkg gc` prunes against an empty referenced set, and there is no
`cvcpkg config` command or settings write path.

---

## Remaining design

### Recipe-first: retire the `cvc-requirements.yaml` build style

- **Flag `cvc-requirements.yaml` for deprecation** and lean only on recipes.
  `cvcpkg install --from cvc-requirements.yaml` keeps working through
  v2.0.0 but emits a deprecation warning and a pointer to the migration
  path (today it runs silently; only the docs and `world`'s help text mark
  the style legacy). The docs and quick-starts stop leading with it.
- **Downstream projects maintain recipes in their own source.** The
  supported model: a project carries its recipes and the related
  scripts/media as part of its source tree, exactly like this repo's
  `recipes/` directory (composes with Phase 17's declared artifacts).
- **`cvcpkg build` prefers prebuilt dependency bundles.** Today the default
  `--no-deps` assumes deps are already installed in `--prefix`, so the
  documented loop is two commands: `cvcpkg install-deps <recipe>` then
  `cvcpkg build <recipe>` — and `--with-deps` recompiles the entire closure
  from source. Fold the prebuilt fetch into `build` itself: install
  prebuilt bundles for every dependency available upstream (honouring
  `--platform`/`--config`/`--link`/version constraints) and build only the
  requested recipe(s) from source; a dependency is built from source only
  when no matching bundle exists upstream or the client is
  offline/`--local` — a natural fallback, no flag required. Keep an opt-in
  flag (today's `--with-deps`) for the full from-source closure
  (reproducibility, bisecting a dep, a locally-patched recipe), and
  `--no-deps` continues to mean "assume deps are already in `--prefix`".
  Acceptance: `cvcpkg build imagemagick --prefix ./p` on a box with network
  access and an empty prefix fetches the jpeg/png/tiff/… bundles and
  compiles only ImageMagick; with `--local` or no network it transparently
  builds the deps it cannot fetch.
- **Developer loop for downstream users** — an easy workflow to do a local
  build of a project, debug it, and generate + add recipe patches
  (`cvcpkg`-assisted patch generation rather than hand-maintained diffs).
- **Extend `cvcpkg generate`'s import surface** — beyond the shipped
  CMake/Autotools/Meson/Makefile/Python detection: qmake, cpkg, Conan, and
  similar existing package descriptions.

### Single entry point — fold `cvcpkg-server` into `cvcpkg server`

Today there are **two** console entry points (`[tool.poetry.scripts]` in
`pyproject.toml`): `cvcpkg` (the client) and a separate `cvcpkg-server`
(`cvcpkg.server.cli:server_cli`, whose `run` command is the actual server,
alongside its own `bootstrap` command and `token`/`audit` groups). The
client already has a `cvcpkg server` group, but it is **management-only**
(`stop`, `status`, `stats`, `backup` — commands that talk *to* a running
server). Everything should live behind the single `cvcpkg` binary.

- **Fold the server into `cvcpkg server run`** — move the `cvcpkg-server`
  subcommands (`run`, `bootstrap`, plus its server-local `token`/`audit`
  management) under the existing client `cvcpkg server` group, and drop the
  separate `cvcpkg-server` console script (keep a deprecation shim for one
  release). One binary, one entry point; `cvcpkg server run --port …`
  starts the server. This is also the prerequisite for the single
  self-contained binary running a server (Phase 8) — a bake/APE with one
  entry point can't ship two console scripts.

### `~/.cvcpkg/` — settings, search paths, and default prefixes

- **Recipe discovery** — by default, look for a `recipes/` directory in the
  current working directory, then in a list of paths from an environment
  variable (e.g. `CVCPKG_RECIPES_PATH`), then in hardcoded defaults like
  `~/.cvcpkg/recipes`. (Today: frozen-binary data → bundled wheel recipes →
  repo walk-up → CWD fallback, plus the #401 CWD overlay; the env-var path
  list and the `~/.cvcpkg/recipes` default are net-new.)
- **User settings in `~/.cvcpkg/settings.yaml`** — user settings override
  built-in defaults *and* environment variables. (Today config lives in
  `~/.config/cvcpkg/config.yaml`; consolidate the user-facing home under
  `~/.cvcpkg/` as part of this phase.)
- **Auto-populate `settings.yaml` with defaults on first client run.** When
  cvcpkg runs as a client and no `~/.cvcpkg/settings.yaml` exists, write
  one seeded with the effective defaults, fully commented, so it doubles as
  self-documentation of every knob. Today `config.py` is **load-only** — it
  reads the config file but never creates it and has no write path at all —
  so this is net-new.
- **`--save` to persist overrides (sticky settings).** A flag on all
  relevant commands that writes the values overridden *this invocation* —
  whether they came from a CLI argument or an environment variable — back
  into `~/.cvcpkg/settings.yaml`, so future commands don't have to repeat
  the same `--server`/`--prefix`/`--cache-dir`/… or `CVCPKG_*` exports.
  Precedence stays: explicit CLI arg > env var > saved settings > built-in
  default; `--save` just promotes the top of that stack into the file.
  Pairs with a `cvcpkg config get/set/unset/edit` surface for direct
  editing (no `config` command exists today).
- **Default build prefix `~/.cvcpkg/build`** — builds no longer require an
  explicit `--prefix` to have a sane, stable home.
- **Default install prefix `~/.cvcpkg/install`** — likewise for installs
  (today's default is `./deps`); `--prefix` remains the override.
- **Cache directory flag + default** — add a CLI flag for the recipe source
  download cache directory where it makes sense, with the default moving to
  `~/.cvcpkg/cache` (today `~/.cache/cvcpkg` holds the download cache, with
  `sources/`, `git/`, `incremental/`, and `builds/` beside it), consistent
  with the `~/.cvcpkg/` consolidation.
- **Headers land in `<install prefix>/inc`** — make sure library recipes
  correctly put headers in the prefix's `inc` directory, and that libraries
  are *never* classified as build tools: headers and libs are deliverables
  and must survive the build-prefix strip (see Phase 4's build-prefix
  hygiene — mis-filing a library as a host tool is a bug).

### Prefix registry — `~/.cvcpkg/local.db`

cvcpkg currently has **no machine-level record of the prefixes it has
installed**: every command takes `--prefix <path>` (default `./deps`) and
all state lives inside each prefix tree
(`share/libcvc-deps/lockfile.yaml` + per-bundle manifests). The gap has a
real consequence — `cvcpkg gc` documents pruning archives "no longer
referenced by any installed prefix" but cannot enumerate prefixes, so it
passes an **empty referenced set** to the cache GC.

- **Track install prefixes in a local database** — an sqlite database file
  (by default `~/.cvcpkg/local.db`) that maps install prefix names to
  install prefix locations. The per-prefix lockfile remains canonical
  *inside* the prefix; `local.db` is the machine-level index over them.
  (This would be the client's first sqlite use — client state today is
  YAML + a file cache. Not to be confused with `registries.yaml`, which
  maps *federated package registries*.)
- **Alias shorthand** — a command-line option to set an install prefix's
  alias. (Today the closest thing to a prefix name is the activation
  prompt tag, which defaults to the directory basename.)
- **Delete an install prefix** — deregister it from `local.db` and remove
  the tree.
- **Inspect an install prefix** — show installed packages, settings,
  metadata. (The lockfile header — platform/arch/config/link, catalog
  revision — the per-bundle entries, and the host-tools record are the
  natural data sources.)
- **Modify install prefix settings.**
- **Path or alias everywhere** — every prefix-taking command (install,
  list, verify, sync, upgrade, world, build, pack/build-all/pack-all,
  cpkg deps, …) accepts either the path or the alias — including when
  activating a prefix in the shell: the `cvcpkg activate` front door below
  resolves aliases through `local.db`.
- **Stale-entry tolerance** — prefix trees can be moved, copied, or deleted
  out-of-band (activation scripts are self-contained but bake in the
  absolute prefix path), so the database must detect, repair — e.g.
  re-register and regenerate the path-baked activation scripts after a
  move — or prune stale entries rather than break.
- **Registry-powered `gc`** — with prefixes enumerable, `cvcpkg gc`
  computes the real referenced-hash set from each registered prefix's
  lockfile instead of pruning against an empty set.

### Per-prefix state database — `share/cvcpkg/prefix.db`

Four primitives that are **first-class functionality, not nice-to-haves**
(directive 2026-07-18): today extraction is a blind merge into the prefix
tree, recipes have no teardown slot, and re-install always re-extracts.
`cvcpkg uninstall` now exists (#522), but as an archive-derived command
rather than a DB-backed one — see below. Note the DB work is not starting
from zero either: each bundle already ships a real per-package file list —
`generate_manifest()`
walks the actually-staged install tree and writes it into
`share/libcvc-deps/<name>/manifest.yaml` (`file_conflicts.py` already
computes cross-package overlaps from exactly these lists). What is missing
is a *queryable, per-prefix* index of that data. The data backbone is a
**per-prefix SQLite database at `share/cvcpkg/prefix.db`**, next to the
prefix's existing metadata (today `share/libcvc-deps/lockfile.yaml` +
per-bundle `manifest.yaml`) — the machine-level `~/.cvcpkg/local.db` indexes
prefixes; `prefix.db` is each prefix's own ground truth and **travels with
the prefix**.

- **Installed-file tracking.** At extract time, record each materialized
  path's sha256 and mode into `prefix.db`, keyed by owning package (sourced
  from the existing per-bundle manifests, not re-derived) — plus, for paths
  where two packages' manifests overlap, which package's copy actually won
  on disk. Enables `cvcpkg owns <file>`, real file-conflict detection, and
  hash-level verification.
- **Migrate `cvcpkg uninstall` from archive-derived to DB-backed.** The
  shipped command (#522) already removes exactly the files a package's
  bundle archive lists, prunes emptied directories, and handles dependents
  deliberately (refuse by default when other installed packages depend on
  the target, `--cascade` to remove the dependent closure — the resolver
  already knows the runtime graph). What the DB adds: atomic lockfile+DB
  updates (a SQLite transaction, vs. today's plain lockfile rewrite that can
  go stale if interrupted), support for source-built entries (today refused
  outright — no archive to enumerate), drift-aware removal of files modified
  since install (today deleted blind), and running the recipe's teardown
  hook when one is declared (the state contract in the
  configuration-management phase).
- **Idempotent installs.** Installing a variant already recorded in
  `prefix.db` that passes verification is a **no-op** (today install
  unconditionally re-extracts over the tree; only the download itself is
  saved by the content-addressed cache); `--force` overrides; partial
  damage re-extracts only what fails verification.
- **`cvcpkg verify` with teeth.** Verify currently cross-checks metadata
  only — it confirms each lockfile bundle has a `manifest.yaml` with the
  matching version, and no file is ever hashed, despite the docstring
  claiming corruption detection. With the file table it becomes a real
  integrity check: hash installed files against recorded digests, report
  modified/missing/unowned files (drift detection for the
  configuration-management phase).
- **Upgrades stop leaving corpses.** `upgrade` currently extract-merges the
  new version over the old (it re-runs the installer's extract step); with
  file tracking it becomes install-new + remove-files-no-longer-present —
  no orphaned files from renamed or dropped paths.
- **Append-only operations journal.** A journal table in `prefix.db`
  records every install / uninstall / upgrade / verify / state-apply with
  timestamp, acting user, package set, and pre/post digests — the local
  forensic substrate (see the configuration-management phase for chaining
  and server-anchoring).

### `clean` and `activate`

- **`cvcpkg clean` for build trees** — make it easy to clean the whole
  build directory or the build directories of specific packages only.
  (Today's `clean` only sweeps orphaned `cvcpkg-*` temp work directories
  left in `$TMPDIR` by interrupted builds.)
- **First-class prefix activation** — a command that makes it easy to
  activate an install prefix so the user can run the apps and runtime libs
  installed there. Today install writes venv-style activation scripts
  (`bin/activate`, `bin/activate.fish`, `bin/activate.csh` on POSIX;
  `Scripts\Activate.ps1` and `Scripts\activate.bat` on Windows); add a
  `cvcpkg activate <prefix>`
  front door (spawn a subshell or print eval-able environment) so users
  don't need to know the script paths per shell.

### Terminal experience

- **Nice terminal graphics when the terminal supports it** — progress bars
  for package downloads and installs, colorized status/summaries, spinners
  for resolution — using the **same color palette as the web front end**
  (the Bulma-dark landing/package pages: link blue `#3273dc`, success green
  `#48c774`, warning yellow `#ffdd57`, danger red `#ff3838`, on the
  `#0a0a0a`/`#1a1a2e` dark ground). Degrade gracefully: plain output on
  dumb terminals and CI pipes, honor `NO_COLOR`.

### Source-complete and offline builds

- **End-to-end from-source builds for downstream projects** — make sure a
  downstream project's recipe build builds *everything* correctly from
  sources when the packages aren't available on a cvcpkg-server (the
  Phase 1 source-fallback path, canonized by an end-to-end
  downstream-project test so it cannot regress).
- **Pre-download for air-gapped machines** — an option to pre-download the
  recipe sources/archives *referred to* by recipes (as opposed to the
  scripts/docs/media packaged *with* the recipe, which Phase 17 covers) and
  look them up in the cache **by hash** at build time, so a machine with no
  internet access can build from a warmed cache.

### Recipe-set export and source pre-seeding (air-gapped self-hosting)

The payoff scenario: pre-download a recipe set **and** its source cache
online, carry them to an air-gapped host, and build there with a single
self-contained binary (Phase 8 / Phases 19–20) — no network, no server.
Also the extraction path for a self-contained binary that has recipes baked
in but where the user wants them on disk.

- **`cvcpkg recipe export <packages…>` — recipe-set archive with dependency
  closure.** Generate an archive (tarball / zip / others) of the recipes
  for the requested packages. **By default it pulls in the full recipe
  dependency closure** (build + host_tools + runtime deps, transitively,
  plus `_common`); `--no-deps` exports only the named recipes. Include each
  recipe's declared artifacts (Phase 17: scripts, patches, media) so the
  result extracts to a well-formed recipes directory usable directly by
  `cvcpkg build`/`install`. This is distinct from what exists today:
  `GET /v1/recipes/bundle` (used by `cvcpkg recipe pull-all`) returns
  **all** recipes with no selection or closure; `cvcpkg recipe pull` (via
  `GET /v1/recipes/{name}`) and the #503 `GET /v1/recipe/{name}/archive`
  each return **one** recipe with no dependency closure; and
  `cvcpkg download` fetches **binary bundles**, not recipe sources.
- **Newest-recipe resolution, remote-preferred but local-revision-aware.**
  Lean on remote servers for the newest recipes (top-down per Phase 22
  authority), but a **newer local `cvc_revision`** wins when present —
  export the newest available version per recipe from either source, and
  report where each came from. Composes with the Phase 22 cross-tier
  consistency warnings.
- **Server API for the selective bundle** — extend the recipe API with a
  package-set + closure parameter (e.g. `POST /v1/recipes/export` taking a
  package list and a `deps` flag), returning the archive. Honors
  org/private visibility and the hidden-package rules (Phase 21) — a hidden
  recipe still exports when explicitly requested or pulled in as a
  dependency. The client falls back to composing the closure itself from
  per-recipe fetches when the server predates the endpoint.
- **`cvcpkg source fetch <packages…>` — pre-download the source cache.**
  Download all upstream source archives *referred to* by the exported
  recipe set (the `source.url` / `source.artifacts` tarballs, verified by
  their recipe `sha256`) into a directory, so the air-gapped host builds
  entirely from the warmed cache. `--cache-dir` controls the destination
  (default `~/.cvcpkg/cache` after the consolidation above).
- **Compressed or extracted source cache.** The pre-downloaded source cache
  can be kept **either** as the fetched compressed archives **or** already
  extracted into source directories — a flag selects. Extracted trees are
  the substrate for later patch generation and recipe-patch iteration (the
  developer loop above), so the two forms are first-class, not an
  afterthought.
- **One-shot seed + a manifest** — a convenience that runs `recipe export`
  + `source fetch` together and writes a small manifest (recipe set,
  versions, source hashes, provenance) so the air-gapped side can verify
  completeness before building, and so the bundle itself is reproducible
  and auditable (ties into Phase 16 provenance and Phase 23's forensic
  journal).
