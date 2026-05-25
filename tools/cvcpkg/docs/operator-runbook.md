# cvcpkg-server Operator Runbook

Procedures for operating the cvcpkg-server production deployment.

All commands assume you are in the `tools/cvcpkg` directory.

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
- [ ] Check for cvcpkg updates (`git log --oneline origin/main..HEAD`)
- [ ] Verify audit chain integrity

### Quarterly

- [ ] Rotate admin tokens
- [ ] Review and revoke unused publisher tokens
- [ ] Test backup restore procedure on a staging instance
- [ ] Update Docker base images (`docker compose build --pull`)
