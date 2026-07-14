# Remote Builders

This document describes the cvcpkg remote builder system — the distributed
build fleet that compiles packages across multiple platforms.

## Architecture

```
              +---------------------+
              |    cvcpkg-server    |
              |   (API + database)  |
              +----------+----------+
                         | WebSocket / HTTP
        +----------------+----------------+
        |                |                |
   +----+----+      +----+----+      +----+----+
   | Linux   |      | BSD     |      | Windows |
   | builders|      | builders|      | builders|
   +---------+      +---------+      +---------+
```

Builders connect to the server, register themselves, and poll for jobs.
When a job is dispatched, the builder downloads the recipe, executes the
build, streams logs via WebSocket, and publishes the resulting archive.

Each builder auto-detects its platform/arch (override with `--platform` /
`--arch`) and can advertise cross-compilation targets with `--cross-platform`
(e.g. `--cross-platform wasm`). The scheduler dispatches a job to any builder
whose platform/arch — or advertised cross target — matches.

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
| `--cross-platform` | Advertise a cross-compilation target (repeatable) |

## Boot Persistence

Run the builder under whatever service manager the platform provides so it
survives reboots. The templates below use placeholders — substitute your own
token and paths.

### Linux (systemd)

```ini
# /etc/systemd/system/cvcpkg-builder.service
[Unit]
Description=cvcpkg builder agent
After=network-online.target
Wants=network-online.target

[Service]
Type=forking
User=<user>
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

### BSD (cron @reboot)

```bash
@reboot /usr/local/bin/cvcpkg builder run \
  --server https://cvcpkg.org \
  --token <TOKEN> \
  --name $(hostname) \
  --max-jobs 2 \
  --work-dir /tmp/cvcpkg-builder \
  --daemon
```

### Windows (scheduled task + supervisor)

`--daemon` is rejected on Windows, so a Windows builder is backgrounded by a
scheduled task that launches a small **supervisor wrapper** instead of
`cvcpkg builder run` directly. The supervisor loops: update cvcpkg, run the
builder with `CVCPKG_BUILDER_SUPERVISED=1`, and relaunch it on exit — keeping
the builder a singleton and applying self-updates without a manual restart.

When `CVCPKG_BUILDER_SUPERVISED` is set, a server-pushed `builder.update`
makes the builder exit with sentinel code `90` (`_SUPERVISOR_RESTART_CODE`)
instead of trying to re-exec in place (Windows `os.execv` cannot replace the
process there); the supervisor then pulls the latest cvcpkg and relaunches on
fresh code. Without a supervisor, the update is pip-installed and applies on
the builder's next start.

### WSL2 (Debian on a Windows host) — Linux builder

For Windows-only machines that should contribute **Linux** capacity, a
builder can run inside a Debian WSL2 instance. It registers as `linux/x86_64`
(optionally with a `wasm` cross target) — it does not build native Windows
artifacts. The inside-the-distro setup is identical to the systemd Linux case
above; WSL adds a boot `schtasks` task (WSL doesn't start with Windows) and
either `networkingMode=mirrored` or a `netsh portproxy` forward so SSH is
reachable. Full walkthrough — SSH + OpenSSL setup, the Windows-side plumbing,
and disk cleanup / `vhdx` compaction — in
[cvcpkg-builder-wsl-debian.md](cvcpkg-builder-wsl-debian.md).

## API Tokens

Builders authenticate with a publisher-role token; server administration uses
an admin-role token. Create tokens with:

```bash
# From within the server container:
docker compose exec backend cvcpkg server token create \
  --name <name> --role publisher --email <email>

# Or via API with an admin token:
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"<name>","role":"publisher","email":"<email>"}' \
  https://cvcpkg.org/v1/tokens
```

**Note:** Token hashes are HMAC-keyed using a secret stored at
`<state_dir>/.hmac_key` (in Docker: `/app/data/.hmac_key`). When creating
tokens via Python directly (bypassing the API), pass the same `state_dir` the
running server uses, otherwise the hashes won't match.

## Monitoring

### Live Monitor (top-like)

```bash
cvcpkg builds monitor --server https://cvcpkg.org --token $TOKEN
cvcpkg builds monitor --interval 2 --dag-id <dag-id>
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

# View / stream a build log
cvcpkg builds log <job-id> --server https://cvcpkg.org --token $TOKEN
cvcpkg builds log <job-id> --follow
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

Both `submit` and `submit-dag` support `--wait` (`-w`) to block until all
jobs finish:

```bash
cvcpkg builds submit --recipe zlib --platform linux --arch x86_64 --wait
cvcpkg builds submit-dag --platform linux --arch x86_64 --wait zlib boost
```

## Troubleshooting

### Builder shows "offline"

The builder daemon may have crashed or the machine rebooted without the
persistence mechanism triggering:

```bash
# Check if the process is running
pgrep -f 'cvcpkg builder run'

# Check logs
journalctl -u cvcpkg-builder --since '1h ago'   # systemd
cat /tmp/cvcpkg-builder/builder.log             # if logging to a file
```

### Build job stuck in "dispatched"

The assigned builder may have gone offline mid-build. The server times out the
job after `--timeout` seconds (default: 3600). To cancel:

```bash
cvcpkg builds cancel <job-id> --server https://cvcpkg.org --token $TOKEN
```

### Updating builders

Pull the latest cvcpkg and reinstall:

```bash
cd <libcvc-deps checkout> && git fetch origin && git checkout origin/master && \
  pip install --quiet .
```

The new code takes effect when the builder next (re)starts, not mid-run — a
long-lived process keeps the modules it imported at startup. Restart via the
platform's service manager (systemd / cron / scheduled task); Windows builders
under the supervisor wrapper restart automatically.
