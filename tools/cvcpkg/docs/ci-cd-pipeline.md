# CI/CD Pipeline & Deployment

This document describes the automated deployment pipelines for cvcpkg.

## Environments

| Environment | Branch | Server | Runners | Scope |
|------------|--------|--------|---------|-------|
| **Production** | `prod` | cvcpkg.org + pkg.tx.wtf | catx-03 (`cvcpkg-prod`) | Public-facing package registry |
| **Dev/Test** | `dev` | cvcpkg-server (incus VM) | star-00/star-01 (`cvcpkg-builder`) | QA and local testing |

## Production Deployment (`deploy-prod.yml`)

**Trigger:** Push to `prod` branch or manual `workflow_dispatch`.

**Runner:** `[self-hosted, Linux, cvcpkg-prod]` (catx-03.tx.wtf)

### Flow

1. **Deploy to cvcpkg.org** — rsync code to cvcpkg-00 (10.10.10.134), rebuild Docker container, run migrations, health check
2. **Deploy to pkg.tx.wtf** — rebuild locally on catx-03, run migrations, health check
3. **Update builder agents** — SSH to all builder machines, `git checkout` + `pip install` to update the cvcpkg CLI

### Deploying to Production

```bash
# From master, push to prod:
git push origin master:prod

# Or deploy a specific SHA:
gh workflow run deploy-prod -f sha=abc1234
```

### Builder Auto-Update

After server deployment, the workflow updates cvcpkg on all registered builder machines:

- **Linux** (star-00, star-01): `tfx@star-00`, `tfx@star-01`
- **BSD** (netbsd-build, netbsd-build-2, freebsd-build, freebsd-build-2, openbsd-build, openbsd-build-2): `root@<host>`
- **Windows** (sandipaws): `tfx@sandipaws`

Builder agents don't need to be restarted after a CLI update — they'll pick up the new code on next job claim. If you need to force restart, kill the daemon and it will restart on next cron/systemd trigger.

## Dev/Test Deployment (`deploy-dev.yml`)

**Trigger:** Push to `dev` branch or manual `workflow_dispatch`.

**Runner:** `[self-hosted, Linux, cvcpkg-builder]` (star-00 or star-01)

### Flow

1. **Update cvcpkg-server VM** — `incus exec` to pull code, rebuild Docker container, run migrations, health check
2. **Update builder VMs** — `incus exec` on cvcpkg-builder-01 and cvcpkg-builder-02, install latest cvcpkg
3. **Restart builder agents** — kill and restart the builder daemons on test VMs

### Dev Infrastructure (Star Incus Cluster)

| VM | Role | IP | Notes |
|----|------|----|----|
| cvcpkg-server | Test server | 172.18.0.x (Docker bridge) | Docker Compose deployment |
| cvcpkg-builder-01 | Test builder | 10.99.0.222 | Linux x86_64 |
| cvcpkg-builder-02 | Test builder | 10.99.0.110 | Linux x86_64 |

### Deploying to Dev

```bash
# From any branch, push to dev:
git push origin HEAD:dev

# Or deploy a specific SHA:
gh workflow run deploy-dev -f sha=abc1234
```

## Populate Server (`populate-server.yml`)

**Trigger:** Manual `workflow_dispatch` only.

Pushes all recipes to a cvcpkg server and submits remote build DAGs for selected platforms.

### Usage

```bash
gh workflow run "Populate cvcpkg.org" \
  -f platforms="linux,freebsd,netbsd,openbsd,win" \
  -f arch="x86_64" \
  -f config="release" \
  -f link="shared"
```

### Options

| Input | Default | Description |
|-------|---------|-------------|
| `platforms` | `linux,freebsd,netbsd,openbsd,win` | Comma-separated target platforms |
| `arch` | `x86_64` | Target architecture |
| `config` | `release` | `release`, `debug`, or `all` |
| `link` | `shared` | `shared`, `static`, or `all` |
| `server` | `https://cvcpkg.org` | Target server URL |
| `skip_push` | `false` | Skip recipe upload (if already on server) |

### Secrets

| Secret | Purpose |
|--------|---------|
| `CVCPKG_PUBLISHER_TOKEN` | Publisher-role token for recipe push and build submission |

## Other CI Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | PR/push to master | Full test suite |
| `cvcpkg-ci.yml` | Changes to `tools/cvcpkg/`, `recipes/`, `packaging/` | cvcpkg-specific tests |
| `source-fallback-ci.yml` | Changes to recipes | Verify source download fallbacks |

## GitHub Actions Runners

| Runner Name | Labels | Host | Role |
|-------------|--------|------|------|
| cvcpkg-prod | `self-hosted, Linux, X64, cvcpkg-prod` | catx-03.tx.wtf | Production deployment |
| star-00 | `self-hosted, Linux, X64, cvcpkg-builder` | star-00 (LAN) | Dev deploy, builds |
| star-01 | `self-hosted, Linux, X64, cvcpkg-builder` | star-01 (LAN) | Dev deploy, builds |
| sandipaws | `self-hosted, X64, Windows` | Windows 11 workstation | Windows builds |
| phm-win11 | `self-hosted, X64, Windows` | Incus VM on prettyhatemachine | Windows builds |
| lat | `self-hosted, Linux, X64, cvcpkg-builder` | laptop | Local builds |
| rebota | `self-hosted, Linux, X64, cvcpkg-builder` | rebota | Local builds |

## Concurrency

All deploy workflows use `concurrency` groups to prevent overlapping deployments:

```yaml
concurrency:
  group: deploy-prod   # or deploy-dev
  cancel-in-progress: false
```

This means a new deploy will queue (not cancel) behind a running one.
