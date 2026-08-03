# cvcpkg-server Deployment Guide

Production deployment of the cvcpkg package server.

## Architecture

```
Internet ──▶ Apache2/Nginx (TLS) ──▶ cvcpkg-server:8420 ──▶ PostgreSQL:5432
               (reverse proxy)          (FastAPI+uvicorn)      (persistent data)
```

All three tiers run on the same host in the default Docker Compose setup.
TLS is terminated at the reverse proxy (Apache2 with Let's Encrypt or Nginx
with certbot).

## Prerequisites

- Docker Engine ≥ 24.0 and Docker Compose v2
- A domain name with DNS pointing to the host (e.g. `cvcpkg.org`)
- A TLS-terminating reverse proxy (Apache2+LE or Nginx+certbot)

## Quick Deploy

```bash
# from the repo root

# 1. Create environment file
cp .env.production.example .env.production
$EDITOR .env.production        # Set POSTGRES_PASSWORD, CORS origins, etc.

# 2. Build and start
./scripts/run-production.sh --build
./scripts/run-production.sh --detach

# 3. Bootstrap admin token
./scripts/run-production.sh --token-create bootstrap-admin admin
# Save the printed token securely.

# 4. Verify
curl http://localhost:8420/healthz
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_USER` | `cvcpkg` | PostgreSQL user |
| `POSTGRES_PASSWORD` | — | **Required.** Database password |
| `POSTGRES_DB` | `cvcpkg` | Database name |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `BACKEND_PORT` | `8420` | Backend listen port |
| `REQUIRE_AUTH_READS` | *(empty)* | Set `1` to require tokens for reads |
| `CVCPKG_RELEASE` | `dev` | Release tag for OCI labels |
| `TIMEZONE` | `UTC` | Container timezone |
| `CVCPKG_MAX_UPLOAD_BYTES` | `4294967296` | Max bundle upload size (4 GiB). Accepts `8GB`/`512MB`; same as `cvcpkg server run --max-upload-bytes`. |
| `CVCPKG_RATE_LIMIT_RPM` | `60` | Rate limit: requests/min for writes |
| `CVCPKG_CORS_ORIGINS` | *(empty)* | Comma-separated allowed CORS origins |
| `CVCPKG_LOG_JSON` | *(empty)* | Set `1` for structured JSON logs |

## TLS Configuration

The backend container binds to `127.0.0.1:8420`. Configure your reverse
proxy to terminate TLS and proxy to `localhost:8420`.

### Apache2 (with Let's Encrypt)

```apache
<VirtualHost *:443>
    ServerName pkg.example.com
    SSLEngine on
    SSLCertificateFile    /etc/letsencrypt/live/pkg.example.com/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/pkg.example.com/privkey.pem

    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:8420/
    ProxyPassReverse / http://127.0.0.1:8420/

    Header always set Strict-Transport-Security "max-age=63072000"
</VirtualHost>
```

### Nginx

```nginx
server {
    listen 443 ssl;
    server_name pkg.example.com;

    ssl_certificate     /etc/letsencrypt/live/pkg.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pkg.example.com/privkey.pem;

    client_max_body_size 512M;

    location / {
        proxy_pass http://127.0.0.1:8420;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Database Migrations

Schema changes are managed with Alembic:

```bash
# Check current migration version
CVCPKG_DATABASE_URL=postgresql+asyncpg://... cvcpkg-server migrate current

# Apply all pending migrations
CVCPKG_DATABASE_URL=postgresql+asyncpg://... cvcpkg-server migrate upgrade head

# Rollback one migration
CVCPKG_DATABASE_URL=postgresql+asyncpg://... cvcpkg-server migrate downgrade -1

# Show history
CVCPKG_DATABASE_URL=postgresql+asyncpg://... cvcpkg-server migrate history
```

For Docker Compose deployments, run inside the container:

```bash
docker compose -f docker-compose.production.yml exec backend \
    cvcpkg-server migrate upgrade head
```

## Backups

### Manual Backup

```bash
./scripts/run-production.sh --backup
# Creates: backups/cvcpkg-20260525T120000Z.sql.gz
```

### Manual Restore

```bash
./scripts/run-production.sh --restore backups/cvcpkg-20260525T120000Z.sql.gz
```

### Automated Backups

Add a cron job:

```cron
0 3 * * * cd /path/to/cvcpkg && ./scripts/run-production.sh --backup
0 4 * * 0 find /path/to/cvcpkg/backups -name '*.gz' -mtime +30 -delete
```

## Monitoring

### Health Check

```bash
curl https://pkg.example.com/healthz
```

Returns JSON with `version`, `storage_scheme`, `packages_count`, `uptime_seconds`.

### Prometheus Metrics

Scrape `https://pkg.example.com/metrics` for:

- `cvcpkg_up` — always 1 if the server is running
- `cvcpkg_uptime_seconds` — process uptime
- `cvcpkg_packages_total` — published package count
- `cvcpkg_requests_total` — total HTTP requests
- `cvcpkg_requests_by_method{method="GET|POST|DELETE"}` — by HTTP method
- `cvcpkg_responses{status="2xx|4xx|5xx"}` — by response class
- `cvcpkg_publishes_total` — successful publish count
- `cvcpkg_bytes_uploaded_total` — total bytes uploaded

### Prometheus scrape config

```yaml
scrape_configs:
  - job_name: cvcpkg
    static_configs:
      - targets: ['pkg.example.com:443']
    scheme: https
```

## Updating

```bash
# from the repo root
git pull
./scripts/run-production.sh --build
./scripts/run-production.sh --down
./scripts/run-production.sh --detach
```

## Security Checklist

- [ ] `POSTGRES_PASSWORD` is a strong random value (≥32 chars)
- [ ] TLS is configured with a valid certificate
- [ ] Backend is only accessible via `127.0.0.1` (not exposed externally)
- [ ] `CVCPKG_CORS_ORIGINS` is set to your frontend domain (or empty)
- [ ] Rate limiting is enabled (`CVCPKG_RATE_LIMIT_RPM`)
- [ ] Upload size limit is appropriate for your bundles
- [ ] Admin token is stored securely
- [ ] Automated backups are configured
- [ ] Firewall blocks direct access to ports 5432, 8420 from the internet
