# cvcpkg-server Operator Runbook

Procedures for operating the cvcpkg-server production deployment.

All commands assume you are in the the repo root directory.

---

## 1. Starting and Stopping

### Start (foreground)

```bash
./scripts/run-production.sh
```

### Start (background)

```bash
./scripts/run-production.sh --detach
```

### Stop

```bash
./scripts/run-production.sh --down
```

### Rebuild and restart

```bash
./scripts/run-production.sh --build
./scripts/run-production.sh --down
./scripts/run-production.sh --detach
```

---

## 2. Health Checks

### Quick check

```bash
curl -s http://localhost:8420/healthz | python3 -m json.tool
```

### Service status

```bash
./scripts/run-production.sh --status
```

### Container resource usage

```bash
docker stats cvcpkg-prod-backend cvcpkg-prod-postgres --no-stream
```

---

## 3. Log Inspection

### View live logs

```bash
./scripts/run-production.sh --logs
```

### Backend only

```bash
docker compose -f docker-compose.production.yml logs -f --tail=200 backend
```

### Search logs for errors

```bash
docker compose -f docker-compose.production.yml logs backend 2>&1 | grep -i error
```

---

## 4. Database Operations

### Backup

```bash
./scripts/run-production.sh --backup
# Output: backups/cvcpkg-YYYYMMDDTHHMMSSZ.sql.gz
```

### Restore

```bash
./scripts/run-production.sh --restore backups/cvcpkg-20260525T120000Z.sql.gz
```

### Check migration status

```bash
docker compose -f docker-compose.production.yml exec backend \
    cvcpkg-server migrate current
```

### Apply migrations

```bash
docker compose -f docker-compose.production.yml exec backend \
    cvcpkg-server migrate upgrade head
```

### Connect to PostgreSQL

```bash
docker compose -f docker-compose.production.yml exec postgres \
    psql -U cvcpkg -d cvcpkg
```

### Check database size

```sql
SELECT pg_size_pretty(pg_database_size('cvcpkg'));
```

### Check table row counts

```sql
SELECT 'packages' AS tbl, count(*) FROM packages
UNION ALL SELECT 'tokens', count(*) FROM tokens
UNION ALL SELECT 'audit_log', count(*) FROM audit_log;
```

---

## 5. Token Management

### Create admin token

```bash
./scripts/run-production.sh --token-create myname admin
```

### Create publisher token

```bash
docker compose -f docker-compose.production.yml exec backend \
    cvcpkg-server token create --name ci-bot --role publisher --state-dir /app/data
```

### List tokens

```bash
docker compose -f docker-compose.production.yml exec backend \
    cvcpkg-server token list --state-dir /app/data
```

### Revoke token

```bash
docker compose -f docker-compose.production.yml exec backend \
    cvcpkg-server token revoke --name ci-bot --state-dir /app/data
```

---

## 6. Audit Trail

### View recent entries

```bash
docker compose -f docker-compose.production.yml exec backend \
    cvcpkg-server audit log --limit 20 --state-dir /app/data
```

### Verify chain integrity

```bash
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
    http://localhost:8420/v1/audit/verify | python3 -m json.tool
```

---

## 7. Monitoring

### Check metrics

```bash
curl -s http://localhost:8420/metrics
```

Key metrics to watch:

| Metric | Alert threshold | Action |
|---|---|---|
| `cvcpkg_up` | Missing for >1m | Restart backend |
| `cvcpkg_responses{status="5xx"}` | Rising | Check logs |
| `cvcpkg_requests_total` | Spike | Check for abuse |
| disk usage on `/app/data` | >80% | Archive old packages |

---

## 8. Troubleshooting

### Backend not starting

```bash
# Check logs for error
docker compose -f docker-compose.production.yml logs --tail=50 backend

# Common issues:
# - CVCPKG_DATABASE_URL misconfigured → check .env.production
# - PostgreSQL not healthy → check postgres container
# - Port 8420 in use → check for stale containers
```

### Database connection failures

```bash
# Verify postgres is healthy
docker compose -f docker-compose.production.yml ps postgres

# Check connection from backend
docker compose -f docker-compose.production.yml exec backend \
    python3 -c "import asyncpg; print('asyncpg ok')"

# Restart postgres
docker compose -f docker-compose.production.yml restart postgres
```

### Out of disk space

```bash
# Check Docker disk usage
docker system df

# Prune unused images
docker image prune -f

# Check data volume size
docker system df -v | grep cvcpkg-prod
```

### Rate limit issues

If legitimate CI is being rate limited, increase `CVCPKG_RATE_LIMIT_RPM`
in `.env.production` and restart the backend.

### Slow responses

```bash
# Check PostgreSQL slow query log (queries > 1s are logged by default)
docker compose -f docker-compose.production.yml logs postgres | grep duration

# Check backend resource usage
docker stats cvcpkg-prod-backend --no-stream
```

---

## 9. Disaster Recovery

### Complete rebuild from backup

```bash
# 1. Stop everything
./scripts/run-production.sh --down

# 2. Remove volumes (DATA LOSS — ensure you have a backup)
docker volume rm cvcpkg-prod-postgres-data

# 3. Start fresh
./scripts/run-production.sh --detach

# 4. Wait for health
./scripts/run-production.sh --status

# 5. Restore from backup
./scripts/run-production.sh --restore backups/latest.sql.gz

# 6. Run migrations to ensure schema is current
docker compose -f docker-compose.production.yml exec backend \
    cvcpkg-server migrate upgrade head
```

### Emergency: Reset everything

```bash
./scripts/run-production.sh --reset-db
# This creates a backup first, then removes all data.
# Follow with --detach to restart clean.
```

---

## 10. Routine Maintenance

### Weekly

- [ ] Verify backups are running (check `backups/` directory)
- [ ] Check disk usage
- [ ] Review audit log for anomalies

### Monthly

- [ ] Rotate/prune old backups (>30 days)
- [ ] Review rate limit and size limit settings
- [ ] Check for cvcpkg updates (`git fetch origin && git log --oneline HEAD..origin/master`)
- [ ] Verify audit chain integrity

### Quarterly

- [ ] Rotate admin tokens
- [ ] Review and revoke unused publisher tokens
- [ ] Test backup restore procedure on a staging instance
- [ ] Update Docker base images (`docker compose build --pull`)

---

## 11. Publishing `cvcpkg` to PyPI

The `cvcpkg` Python CLI is published to PyPI by the
[`.github/workflows/cvcpkg-publish.yml`](../.github/workflows/cvcpkg-publish.yml)
workflow, which fires on tags matching `cvcpkg-v*` and uses **trusted
publishing (OIDC)** — no API tokens stored in the repo.

There is no TestPyPI leg any more (dropped in #145/#147): a pre-release
tag now stops after the live smoke instead of pushing anywhere.

### 11.1. The publish gate

`publish-pypi` is the deliberate final step of a release and only runs
when **all** of the following hold:

1. **Repo variable `CVCPKG_PUBLISH_TO_PYPI` is `true`** (Settings →
   Secrets and variables → Actions → Variables). Until it is set,
   pushing even a stable tag builds and live-smokes but does **not**
   publish — so the release can be staged first (repo rename / org
   move, trusted-publisher registration, roadmap gaps).
2. **The run was triggered by a tag push.** `workflow_dispatch` runs
   never publish; they exist to exercise test/build/smoke against a
   branch or tag.
3. **The tag is stable**: `cvcpkg-vMAJOR.MINOR.PATCH` with no
   pre-release marker — any `a`, `b`, `rc`, `dev` or `post` in the tag
   name skips the publish job.
4. **The GitHub environment `pypi` allows it.** The job runs in the
   `pypi` environment with OIDC (`id-token: write`) via
   `pypa/gh-action-pypi-publish` (`skip-existing: true`). Add a
   required reviewer to the environment to additionally force a human
   approval.

### 11.2. One-time setup — trusted publisher

PyPI must have the `cvcpkg` project pre-registered with a **pending
publisher** that matches the workflow exactly, otherwise the first push
fails with `404 Not Found` (project unknown) or `403 Forbidden`
(trusted publisher not configured). On <https://pypi.org>, as an
account with permission to claim the `cvcpkg` name: **Your projects →
Publishing → Add a new pending publisher**, with:

- **PyPI Project Name:** `cvcpkg`
- **Owner / Repository name:** the repo's identity **at publish time**.
  The repo is moving into the CyberPC Angel GitHub org as
  **`cy-pca/cvcpkg`**; that transfer is still pending (see the
  "Ownership, Copyright & Branding" section of
  [the roadmap](roadmap/CVCPKG-ROADMAP.md)). Register the trusted
  publisher against that post-transfer identity, not
  `transfix/libcvc-deps`. No PyPI release exists and no publisher is
  registered today, so there is no stale binding to migrate — but one
  registered against the pre-transfer name would become exactly that.
- **Workflow name:** `cvcpkg-publish.yml`
- **Environment name:** `pypi` (must match the workflow's
  `environment:` key)

Then in the GitHub repo (Settings → Environments) create the `pypi`
environment. It needs **no** secrets — OIDC handles auth.

References:
- <https://docs.pypi.org/trusted-publishers/adding-a-publisher/>
- <https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/>

### 11.3. Cutting a release

1. Update `pyproject.toml` `version`.
2. Add a top section to [`CHANGELOG.md`](../CHANGELOG.md) for the
   new version.
3. Make sure the repo variable `CVCPKG_PUBLISH_TO_PYPI` is `true`
   (once — it stays set after the first release).
4. Tag the release commit and push the tag (the workflow is tag-driven
   and checks out the tag; the branch the commit lives on does not
   change the pipeline):
   ```bash
   git tag cvcpkg-v<MAJOR>.<MINOR>.<PATCH>
   git push origin cvcpkg-v<MAJOR>.<MINOR>.<PATCH>
   ```
5. Watch the workflow:
   - `test` → unit + integration tests on Python 3.10–3.13.
   - `build` → sdist + wheel via the in-tree PEP 517 backend (which
     bundles the recipes), `twine check`, a content gate that asserts
     ≥ 100 recipes actually landed in the artifacts, and a sanity
     install.
   - `live-smoke` → installs the built wheel on Linux/macOS/Windows
     (Python 3.10 and 3.12) and drives it against the live
     `https://cvcpkg.org` registry (catalog probe, `list`, `info`,
     `install zlib`). A green smoke run is the gate for any publish.
   - `publish-pypi` → trusted-publish to PyPI, if every gate in §11.1
     passes.

### 11.4. Pre-release flow (recommended for any version bump)

To exercise the full pipeline without burning a PyPI version number:

```bash
git tag cvcpkg-v2.0.1rc1
git push origin cvcpkg-v2.0.1rc1
```

`publish-pypi` is skipped automatically on a pre-release tag; the
tests, the build gates and the live smoke against `https://cvcpkg.org`
all still run. (Before `CVCPKG_PUBLISH_TO_PYPI` is set, even a stable
tag behaves this way.)

### 11.5. Verifying a published release

```bash
pip install --upgrade cvcpkg
cvcpkg --version
cvcpkg list --available | head
cvcpkg install --prefix /tmp/cvcpkg-smoke zlib
cvcpkg verify --prefix /tmp/cvcpkg-smoke
```

### 11.6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `publish-pypi` is skipped on a stable-looking tag | Repo variable `CVCPKG_PUBLISH_TO_PYPI` unset or not `true`; or the tag contains `a`, `b`, `rc`, `dev` or `post`; or the run was `workflow_dispatch` | Set the variable; push a strict `cvcpkg-vMAJOR.MINOR.PATCH` tag with no suffix. |
| `403 Forbidden` on publish | Trusted publisher not configured for this repo identity / environment | Re-check the project's *Publishing* settings on PyPI. Owner/repo/workflow/environment must all match the repo **as it is named at publish time** (post-transfer: `cy-pca/cvcpkg`). |
| `400 File already exists` | Re-running the workflow for a tag that was already published | Bump to the next patch version and re-tag. `skip-existing: true` already handles common cases; this fires when the wheel bytes differ. |
| `live-smoke` install fails on Windows | A heavy Windows package (qt6, vtk, …) was added to the smoke set without Windows coverage | Keep the smoke set to packages that are known-published on all three platforms (currently zlib is the canary). |
