# CI/CD Pipeline & Deployment

This document describes the automated deployment pipelines for cvcpkg.

## Environments

| Environment | Tracks | Server | Deploy runner | Scope |
|------------|--------|--------|---------|-------|
| **Production** | `prod` branch (a record of what is live) | cvcpkg.org + pkg.tx.wtf | cy-pca's ops deploy VM | Public-facing package registry |
| **Dev/Test** | `master` (every push) | cvcpkg-server (incus VM) | `star-01` | QA, recipe PR builds, local testing |

## Production Deployment

Since 2026-11-28 production deploys run from cy-pca's private ops repository
(cy-pca/ops), not from this repo: its `cvcpkg-deploy.yml` workflow, by
dispatch, on a deploy VM that runs nothing else. This repo's `deploy-prod.yml`
was removed; a push to `prod` no longer deploys anything.

What a deploy does, per host (cvcpkg.org first, then the pkg.tx.wtf mirror):
the job SSHes to the host with a key whose `authorized_keys` line forces a
fixed wrapper, which fetches this public repo, refuses a commit on neither
`prod` nor `master`, checks it out (keeping `.env.production` and `backups/`),
backs up the database, rebuilds the backend container, runs migrations
(`cvcpkg-server migrate upgrade head`) and waits up to 300 s for `/healthz`.
Then the job checks the builder WebSocket route through the public URL
(`403`/`401` expected; see
[deployment-guide.md](deployment-guide.md#checking-the-websocket-route)),
tells connected builders to self-update (`POST /v1/admin/update-builders`),
and pushes the recipes that changed since the previous deploy
(`scripts/changed_recipes.py`, `cvcpkg recipe push-all --only NAME ...`).

After a deploy, `prod` is fast-forwarded to the deployed commit, so it keeps
naming what is live.

> **Extras matter on every host these pipelines touch.** A bare
> `pip install .` now gets the core client only (`click` + `PyYAML`). Each
> workflow installs the extra its own commands need — `[builder,validate]` on
> builder hosts, `[publish]` where it runs `recipe push` / `builds submit-dag`
> / `publish`, `[remote]` for registry admin. Dropping the extra back to a
> bare `.` silently breaks that host at the first HTTP call. See
> [pypi-install.md](pypi-install.md).

### Deploying to Production

```bash
# Ops repo access needed; sha may be master, prod, a tag or a full sha:
gh workflow run cvcpkg-deploy.yml -R cy-pca/ops -f sha=master
```

## Dev/Test Deployment (`deploy-dev.yml`)

**Trigger:** Every push to `master`, or manual `workflow_dispatch` (optional `sha` input, defaults to master HEAD).

There is no `dev` deploy branch anymore. Deploys used to key off a `dev` branch, but nothing ever pushed there and the dev cluster silently ran week-old code while recipe PRs were gated against master's CLI — so the dev cluster now tracks `master` directly.

**Runner:** `[self-hosted, Linux, star-01]` (a host of the star incus cluster)

### Flow

1. **Update cvcpkg-server VM** — `incus exec`: check out the SHA, `pip install`, rebuild the Docker container, run migrations, health-check `http://localhost:8420/healthz`. The deploy also enforces `CVCPKG_POPULATE_UPSTREAM=https://cvcpkg.org` in `.env.production`, so the dev catalog continuously mirrors the public prod catalog
2. **Update builder VMs** — `incus exec` on cvcpkg-builder-01 and cvcpkg-builder-02, install latest cvcpkg
3. **Restart builder agents** — restart the `cvcpkg-builder` systemd unit on both VMs (ensuring the `--cross-platform wasm`, `wasi`, and `cosmo` flags are present)
4. **Update sandipaws-wsl** *(best-effort)* — the WSL windows-cross builder, reached directly over the star WireGuard overlay; skipped with a warning when the laptop is off
5. **Notify builders to self-update** — `POST /v1/admin/update-builders` on the dev server (needs `CVCPKG_ADMIN_TOKEN`)

### Dev Infrastructure (Star Incus Cluster)

| VM | Role | Notes |
|----|------|----|
| cvcpkg-server | Test server | Docker Compose deployment; API on port 8420. Workflows resolve its IP at run time via `incus list cvcpkg-server` |
| cvcpkg-builder-01 | Test builder | Linux x86_64, plus wasm/wasi/cosmo cross builds |
| cvcpkg-builder-02 | Test builder | Linux x86_64, plus wasm/wasi/cosmo cross builds |
| sandipaws-wsl | windows-cross builder | WSL2 distro on the sandipaws laptop; recipes' `build.ps1` runs on the Windows host via interop |

### Deploying to Dev

```bash
# Merging or pushing to master deploys automatically.

# Or deploy a specific SHA by hand:
gh workflow run deploy-dev -f sha=abc1234
```

## Populate Server (`populate-server.yml`)

**Trigger:** Manual `workflow_dispatch` only. Runs on a GitHub-hosted runner.

Pushes all recipes to a cvcpkg server (`cvcpkg recipe push-all`), submits remote build DAGs (`cvcpkg builds submit-dag`) for the selected platforms, and follows them to completion. By default it skips variants the server has already published, so a run fills gaps rather than rebuilding the world.

### Usage

```bash
gh workflow run "Populate cvcpkg.org" \
  -f platforms="linux,freebsd,netbsd,openbsd,windows" \
  -f arch="x86_64"

# Rebuild a specific few recipes, even if already published:
gh workflow run "Populate cvcpkg.org" \
  -f recipes="zlib openssl" \
  -f skip_existing=false
```

### Options

| Input | Default | Description |
|-------|---------|-------------|
| `platforms` | `linux,freebsd,netbsd,openbsd,windows,wasm,wasi,cosmo` | Comma-separated target platforms |
| `arch` | `x86_64,wasm32` | Target architecture(s), comma-separated |
| `config` | `release` | `release`, `debug`, or `all` |
| `link` | `shared` | `shared`, `static`, or `all` |
| `server` | `https://cvcpkg.org` | Target server URL |
| `skip_push` | `false` | Skip recipe upload (if already on server) |
| `recipes` | *(all)* | Recipe names to build, space- or comma-separated; a typo fails loudly |
| `skip_existing` | `true` | Skip variants already published (turn off to force a rebuild) |

### Platforms without persistent builders

`builds submit-dag` can only schedule onto the registered builder fleet, which is x86_64-only. Two companion lanes cover the rest:

- **macOS** — `macos` in `platforms` is filtered out of the DAG submission and instead dispatches `macos-build.yml` on ephemeral GitHub-hosted macOS runners (arm64, plus a `macos-15-intel` lane when `arch` includes `x86_64`)
- **linux/arm64** — `arm64` in `arch` dispatches `linux-arm-build.yml` on GitHub-hosted arm64 runners

### Secrets

| Secret | Purpose |
|--------|---------|
| `CVCPKG_PUBLISHER_TOKEN` | Publisher-role token for recipe push and build submission |

## Other Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | PR / push to master | Fast checks: Black formatting, schema + recipe validation, unit tests, recipe dependency-graph and platform-closure validation |
| `cvcpkg-ci.yml` | PR / push to master touching `src/`, `recipes/`, `packaging/`, `tests/`, or packaging metadata | Test matrix, secret scan, schema validation, lint & type-check, Docker integration tests |
| `wheel-smoke.yml` | PR / push touching the packaging surface; manual | Build wheel + sdist, verify their contents, install and smoke the CLI on Linux/macOS/Windows across Python 3.10–3.13 |
| `source-fallback-ci.yml` | PR / push touching installer/builder code or recipes | Build zlib from source via the real recipe to exercise `cvcpkg install`'s source-build fallback (Linux + macOS) |
| `pr-recipe-build-dev.yml` | PR touching `recipes/` (same-repo PRs only) | Push the changed recipes to the dev cluster, build them across the dev platforms, comment the per-recipe result on the PR |
| `populate-dev.yml` | Manual | Dev-cluster counterpart of populate-server: push all recipes to the dev server and submit build DAGs to the dev builders |
| `windows-build.yml` | Manual | Sharded build of all (or selected) Windows recipes on GitHub-hosted `windows-2022` runners, published to cvcpkg.org |
| `macos-build.yml` | Manual, or called by populate-server | Sharded build + publish of the macOS recipe DAG on GitHub-hosted macOS runners |
| `linux-arm-build.yml` | Manual, or called by populate-server when `arch` includes `arm64` | Sharded build + publish of linux/arm64 packages on GitHub-hosted arm64 runners |
| `macos-drain.yml` | Every 2 hours (`17 */2 * * *`) + manual | Drain pending macOS build jobs on cvcpkg.org without registering a builder; a cheap pre-check skips the macOS runner when the queue is empty |
| `windows-recipe-check.yml` | Manual (`-f recipe=<name>`); PR touching the workflow file itself | Build ONE recipe on `windows-latest` and throw the result away — proves a `build.ps1` actually runs on Windows; never publishes |
| `linux-arm-recipe-check.yml` | Manual (`-f recipe=<name>`); PR touching it or `linux-arm-build.yml` | Build recipes on `ubuntu-22.04-arm` with `linux-arm-build.yml`'s tool setup and throw the result away; never publishes |
| `cvcpkg-publish.yml` | Push of a `cvcpkg-v*` tag, or manual | Test, build the wheel, live-smoke it across OSes, and publish cvcpkg to PyPI (OIDC trusted publishing) |
| `cvcpkg-standalone.yml` | Push of a `cvcpkg-v*` tag, or manual | Build self-contained single-file cvcpkg binaries (PyInstaller) for Linux, macOS, Windows, and the BSDs; attach them to the GitHub Release |
| `build-cli-binary.yml` | Manual, or on a published release | Build the `client` and `combined` (cvcpkg + cvcpkg-server in one binary, argv[0] dispatch) PyInstaller variants per OS |
| `package-lifecycle.yml` | Manual | Yank, unyank, or nuke a published package version on a server |
| `health-check.yml` | Daily 08:00 UTC + manual | Probe `/healthz` on prod, mirror, and dev cluster; check mirror sync; file or comment on a GitHub issue on failure |
| `catalog-publish.yml` | Manual (disabled) | Legacy gh-pages catalog publish — superseded by the server's own catalog endpoint; retained for reference |

## GitHub Actions Runners

Self-hosted runner labels targeted by the workflows:

| Label | Host | Used by |
|-------|------|---------|
| `cvcpkg-prod` | catx-03 | cvcpkg-ci Docker integration tests |
| `star-00` | star incus cluster host | itself in the Linux builder matrix |
| `star-01` | star incus cluster host | deploy-dev, pr-recipe-build-dev, populate-dev, health-check dev probe; Linux builder matrix |
| `lat` | laptop | Linux builder matrix |
| `rebota` | rebota | Linux builder matrix |
| `cvcpkg-builder` | star cluster | cvcpkg-standalone BSD binary builds (drives the BSD VMs over SSH) |

Package builds on GitHub-hosted runners (`windows-2022`, `macos-latest`, `macos-15-intel`, arm64 Linux) go through `windows-build.yml` / `macos-build.yml` / `linux-arm-build.yml`. Note that the remote builder fleet (BSD guests, the Windows builder, prettyhatemachine) is *not* made of Actions runners — those machines run the cvcpkg builder daemon and are updated over SSH by the deploy workflows. See [builder-fleet.md](builder-fleet.md).

## Concurrency

Deploy and populate workflows use non-cancelling `concurrency` groups (`deploy-dev`, `populate-server`, `populate-dev`, `macos-drain`; production's `cvcpkg-deploy` in cy-pca/ops):

```yaml
concurrency:
  group: deploy-dev   # or populate-server, populate-dev, ...
  cancel-in-progress: false
```

A new run queues (not cancels) behind a running one. CI-style workflows (`ci.yml`, `cvcpkg-ci.yml`, `wheel-smoke.yml`, `pr-recipe-build-dev.yml`) do the opposite: they cancel superseded runs on the same ref or PR.
