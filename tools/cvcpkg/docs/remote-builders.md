# Remote Builder Infrastructure

This document describes the cvcpkg remote builder system — the distributed
build fleet that compiles packages across multiple platforms.

## Architecture

```
                    ┌─────────────────────┐
                    │   cvcpkg.org        │
                    │   (cvcpkg-server)   │
                    │   PostgreSQL + API  │
                    └────────┬────────────┘
                             │ WebSocket / HTTP
              ┌──────────────┼──────────────────┐
              │              │                   │
     ┌────────┴───┐  ┌──────┴─────┐   ┌────────┴────────┐
     │ Linux      │  │ BSD        │   │ Windows         │
     │ star-00    │  │ netbsd-*   │   │ sandipaws       │
     │ star-01    │  │ freebsd-*  │   │                 │
     │            │  │ openbsd-*  │   │                 │
     └────────────┘  └────────────┘   └─────────────────┘
```

Builders connect to the server, register themselves, and poll for jobs.
When a job is dispatched, the builder downloads the recipe, executes the
build, streams logs via WebSocket, and publishes the resulting archive.

## Production Builders

| Builder | Host | Platform | Arch | Max Jobs | Persistence |
|---------|------|----------|------|----------|-------------|
| star-00 | star-00 (LAN) | linux | x86_64 | 4 | systemd |
| star-01 | star-01 (LAN) | linux | x86_64 | 4 | systemd |
| netbsd-build | incus VM (star cluster) | netbsd | x86_64 | 2 | cron @reboot |
| netbsd-build-2 | incus VM (star cluster) | netbsd | x86_64 | 2 | cron @reboot |
| freebsd-build | incus VM (star cluster) | freebsd | x86_64 | 2 | cron @reboot |
| freebsd-build-2 | incus VM (star cluster) | freebsd | x86_64 | 2 | cron @reboot |
| openbsd-build | incus VM (star cluster) | openbsd | x86_64 | 2 | cron @reboot |
| openbsd-build-2 | incus VM (star cluster) | openbsd | x86_64 | 2 | cron @reboot |
| sandipaws | Windows 11 workstation | win | x86_64 | 2 | schtasks |

**Total capacity:** 22 concurrent build slots across 5 platforms.

## Dev/Test Builders

| Builder | Host | IP | Server URL | Persistence |
|---------|------|----|-----------|-------------|
| dev-builder-01 | cvcpkg-builder-01 (incus VM) | 10.99.0.222 | http://10.99.0.250:8420 | systemd |
| dev-builder-02 | cvcpkg-builder-02 (incus VM) | 10.99.0.110 | http://10.66.77.207:8420 | systemd |

**Dev server:** cvcpkg-server incus VM, IPs: 10.99.0.250 (incus net), 10.66.77.207 (bridge).
Docker binds to 0.0.0.0:8420 (`BACKEND_BIND_ADDR=0.0.0.0` in `.env.production`).

**Note:** builder-02 cannot reach 10.99.0.250 (different incus network segment) so it
connects via 10.66.77.207 instead.

**Dev tokens** (role: purpose):
- `dev-admin2` (admin): server management
- `dev-builder-01b` (publisher): builder-01 registration
- `dev-builder-02b` (publisher): builder-02 registration

Token raw values are in `/etc/systemd/system/cvcpkg-builder.service` on each VM.

These builders connect to the dev cvcpkg-server VM (not cvcpkg.org).

## Builder Registration

Builders register with the server on startup and maintain presence via
periodic heartbeats. If a builder misses heartbeats, it transitions to
"offline" status and won't receive new jobs.

```bash
cvcpkg builder run \
  --server https://cvcpkg.org \
  --token $BUILDER_TOKEN \
  --name $(hostname) \
  --max-jobs 4 \
  --work-dir /tmp/cvcpkg-builder \
  --daemon
```

### Key Options

| Option | Description |
|--------|-------------|
| `--server URL` | Server to connect to (env: `CVCPKG_SERVER_URL`) |
| `--token TOKEN` | Publisher-role auth token (env: `CVCPKG_TOKEN`) |
| `--name NAME` | Unique builder name |
| `--max-jobs N` | Maximum concurrent build jobs |
| `--work-dir DIR` | Scratch directory for builds |
| `--daemon` | Fork to background (Unix only) |
| `--pidfile PATH` | Write PID file for daemon management |
| `--platform` | Override auto-detected platform |
| `--arch` | Override auto-detected architecture |

## Boot Persistence

### Linux (systemd) — star-00, star-01

```ini
# /etc/systemd/system/cvcpkg-builder.service
[Unit]
Description=cvcpkg builder agent
After=network-online.target
Wants=network-online.target

[Service]
Type=forking
User=tfx
ExecStart=/usr/local/bin/cvcpkg builder run \
  --server https://cvcpkg.org \
  --token <TOKEN> \
  --name %H \
  --max-jobs 4 \
  --work-dir /tmp/cvcpkg-builder \
  --daemon --pidfile /var/run/cvcpkg-builder.pid
PIDFile=/var/run/cvcpkg-builder.pid
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now cvcpkg-builder
```

### BSD (cron @reboot) — all NetBSD/FreeBSD/OpenBSD builders

```bash
@reboot /usr/local/bin/cvcpkg builder run \
  --server https://cvcpkg.org \
  --token <TOKEN> \
  --name $(hostname) \
  --max-jobs 2 \
  --work-dir /tmp/cvcpkg-builder \
  --daemon
```

### Windows (schtasks) — sandipaws

```powershell
schtasks /create /tn "cvcpkg-builder" /tr "cvcpkg builder run --server https://cvcpkg.org --token <TOKEN> --name sandipaws --max-jobs 2 --work-dir C:\temp\cvcpkg-builder" /sc onstart /ru tfx /rl highest
```

## API Tokens

| Token Name | Role | Purpose |
|-----------|------|---------|
| admin | admin | Server administration |
| builders | publisher | Builder registration + package publishing |
| ci_publisher | publisher | GitHub Actions CI (recipe push + build submission) |

**Important:** Token hashes are HMAC-keyed using a secret stored at
`<state_dir>/.hmac_key` (in Docker: `/app/data/.hmac_key`). When creating
tokens via Python directly (bypassing the API), you **must** pass the same
`state_dir` the running server uses, otherwise the hashes won't match.

Tokens are created via:
```bash
# From within the server container:
docker compose exec backend cvcpkg server token create \
  --name <name> --role publisher --email info@cvcpkg.org

# Or via API with admin token:
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"mytoken","role":"publisher","email":"info@cvcpkg.org"}' \
  https://cvcpkg.org/v1/tokens
```

## Monitoring

### Live Monitor (top-like)

```bash
cvcpkg builds monitor --server https://cvcpkg.org --token $TOKEN
cvcpkg builds monitor --interval 2 --dag-id populate-20260604-190000
```

Shows: builder fleet status, active jobs, recent completions, capacity utilization.

### Check Builder Status

```bash
# List all builders
curl -H "Authorization: Bearer $TOKEN" https://cvcpkg.org/v1/builders | python3 -m json.tool

# Public builders (no auth required for public builders)
curl https://cvcpkg.org/v1/builders
```

### Build Job Queries

```bash
# List recent jobs
cvcpkg builds list --server https://cvcpkg.org --token $TOKEN

# Filter by status
cvcpkg builds list --status failed --limit 20

# View build log
cvcpkg builds log 42 --server https://cvcpkg.org --token $TOKEN

# Stream live log
cvcpkg builds log 42 --follow
```

## Submitting Builds

### Single Build

```bash
cvcpkg builds submit \
  --recipe zlib \
  --platform linux \
  --arch x86_64 \
  --server https://cvcpkg.org \
  --token $TOKEN
```

### Bulk DAG (all recipes, multiple platforms)

```bash
cvcpkg builds submit-dag \
  --platform linux,freebsd,netbsd,openbsd,win \
  --arch x86_64 \
  --config release \
  --link shared \
  --dag-id "populate-$(date +%Y%m%d)" \
  zlib boost openssl curl ...
```

### Wait for Completion

Both `submit` and `submit-dag` support `--wait` (`-w`) to block until
all jobs finish:

```bash
cvcpkg builds submit --recipe zlib --platform linux --arch x86_64 --wait
cvcpkg builds submit-dag --platform linux --arch x86_64 --wait zlib boost
```

## Troubleshooting

### Builder shows "offline"

The builder daemon may have crashed or the machine rebooted without the
persistence mechanism triggering:

```bash
# Check if process is running
ssh <host> "pgrep -f 'cvcpkg builder run'"

# Check logs
ssh <host> "journalctl -u cvcpkg-builder --since '1h ago'"  # systemd
ssh <host> "cat /tmp/cvcpkg-builder/builder.log"  # if logging to file

# Restart manually
ssh <host> "cvcpkg builder run --server https://cvcpkg.org --token $TOKEN \
  --name <name> --max-jobs 2 --work-dir /tmp/cvcpkg-builder --daemon"
```

### Build job stuck in "dispatched"

The assigned builder may have gone offline mid-build. The server will
timeout the job after `--timeout` seconds (default: 3600). To cancel:

```bash
cvcpkg builds cancel <job_id> --server https://cvcpkg.org --token $TOKEN
```

### Updating builders

Builders are updated automatically by the `deploy-prod` workflow. To
manually update a specific builder:

```bash
ssh <host> "cd ~/libcvc-deps && git fetch origin && git checkout origin/master && \
  cd tools/cvcpkg && pip install --break-system-packages --quiet ."
```

No builder restart is needed — the running daemon will use the new code
for the next job it claims.

## Network Topology

```
catx-03.tx.wtf (38.57.161.5)     ── pkg.tx.wtf server + prod CI runner
    │
    │ SSH (10.10.10.x LAN)
    ▼
star-00 / star-01                 ── Linux builders + incus cluster hosts
    │                                + GitHub runners (cvcpkg-builder label)
    │ incus exec / 10.99.0.x
    ▼
incus VMs:
  cvcpkg-server (dev)             ── Test cvcpkg-server (Docker)
  cvcpkg-builder-01/02 (dev)      ── Test builders
  netbsd-build / netbsd-build-2   ── Production NetBSD builders
  freebsd-build / freebsd-build-2 ── Production FreeBSD builders
  openbsd-build / openbsd-build-2 ── Production OpenBSD builders

cvcpkg-00 (10.10.10.134)         ── cvcpkg.org server (Docker)
    │
    │ reverse proxy (Apache2 + TLS)
    ▼
tx VM (38.57.161.23)              ── Public endpoint for cvcpkg.org

sandipaws                         ── Windows 11 builder (local network)
```
