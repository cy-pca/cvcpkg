# Roadmap: cvcpkg Remote Builders

Status: **Planning** (no implementation started).
Author: roadmap drafted 2026-05-30.
Target: libcvc-deps v1.7.0 or v2.0.0 (depending on scope alignment with cvcpkg-2.0 roadmap).

---

## 1. Motivation

Today, builds are orchestrated by GitHub Actions workflows
(`recipe-build.yml`, `bsd-recipe-build.yml`).  The CI matrix defines
platform×config×link combinations and GitHub assigns hosted or
self-hosted runners.  This works but has significant drawbacks:

- **Queue delays**: GitHub-hosted runners are shared infrastructure;
  jobs can wait minutes in the queue before a runner is available.
- **No scheduling control**: We cannot prioritize one build over
  another, reorder the queue, or cancel individual matrix entries
  without cancelling the entire workflow run.
- **Rigid runner assignment**: Self-hosted runners are tagged with
  fixed labels.  If a runner goes offline, jobs fail rather than
  being rerouted to an equivalent machine.
- **No organization builds**: Third-party organizations using the
  cvcpkg ecosystem cannot contribute their own build capacity or
  build private packages without forking the CI configuration.
- **Recipe distribution**: New recipes or recipe updates between
  releases require a full repository checkout.  There is no mechanism
  to push recipe changes directly to builders.

The remote builder feature makes **cvcpkg itself the build
orchestrator**.  Builders register with the server, advertise their
capabilities, and receive build jobs dispatched by the server based on
platform, architecture, and dependency ordering.  This replaces the
GitHub Actions matrix with a self-managed, priority-aware build
system that supports both global and organization-scoped runners.

### Goals

| # | Goal |
|---|------|
| G1 | Any machine running `cvcpkg builder run` becomes a build worker that receives and executes jobs from the cvcpkg server. |
| G2 | Build requests are dispatched as a DAG — the server resolves recipe dependencies and schedules parallel work according to the dependency graph. |
| G3 | Builders support configurable parallelism (default: number of CPU cores) for concurrent job execution. |
| G4 | Organizations can register their own builders to build packages scoped to their org. Global admins can assign global builders to org work on request. |
| G5 | Only global admins can submit build jobs for base recipes. Organization admins (or users with the `builder` role) can submit jobs for their org's recipes. |
| G6 | Build logs are streamed to object storage and downloadable as artifacts from package pages (subject to ACL). Log storage counts against org quotas. |
| G7 | The server supports webhooks for state changes: package published, build started, build completed, build cancelled. |
| G8 | The server pushes recipe updates to builders — builders do not need access to the git repository. |

### Non-goals

- Replacing GitHub Actions entirely.  CI workflows will continue to
  run tests, linting, and validation.  Remote builders handle the
  heavy build+publish work.
- Running builders in untrusted environments.  Builders execute
  arbitrary build scripts; they must be on trusted infrastructure.
- Multi-tenancy isolation.  Builders run as a single OS user.  Process
  isolation (containers, VMs) is out of scope for v1.

---

## 2. Architecture Overview

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
│  cvcpkg CLI  │────▸│  cvcpkg-server   │◂────│  Builder A   │
│              │     │                  │     │  (linux/x64) │
│ remote-pack  │     │  ┌────────────┐  │     └──────────────┘
│ builds list  │     │  │ Job Queue  │  │     ┌──────────────┐
│ builds cancel│     │  │  (DAG)     │  │◂────│  Builder B   │
│ webhooks     │     │  └────────────┘  │     │  (macos/arm) │
└──────────────┘     │  ┌────────────┐  │     └──────────────┘
                     │  │ Webhooks   │  │     ┌──────────────┐
┌──────────────┐     │  └────────────┘  │◂────│  Builder C   │
│  Web UI      │────▸│  ┌────────────┐  │     │  (win/x64)   │
│ (build logs, │     │  │ Log Store  │  │     └──────────────┘
│  status)     │     │  └────────────┘  │
└──────────────┘     └──────────────────┘
```

Three actors:

1. **Builder agent** (`cvcpkg builder run`) — long-running process on
   a build machine.  Registers with the server, polls or receives jobs
   via WebSocket (with long-poll fallback), executes builds, streams
   logs, and publishes results.

2. **Dispatcher** (`cvcpkg remote-pack`, `remote-pack-all`) — client
   commands that submit build requests.  The server resolves the
   dependency DAG and creates a set of inter-dependent jobs.

3. **Server** — maintains the builder registry, job queue, DAG
   scheduler, log storage, and webhook dispatch.

---

## 3. Database Schema

### 3.1 `builders` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `name` | TEXT | Human-readable name (unique per org_slug) |
| `org_slug` | TEXT (nullable) | NULL = global builder; non-NULL = org-scoped |
| `platform` | TEXT | Detected platform (linux, macos, windows, freebsd, …) |
| `arch` | TEXT | Detected architecture (x86_64, arm64, riscv64, …) |
| `labels` | JSON | Arbitrary labels (e.g. `["gpu", "high-mem"]`) |
| `capabilities` | JSON | Supported link modes, configs, max parallel jobs |
| `status` | TEXT | `online`, `offline`, `busy` |
| `current_jobs` | INT | Number of currently running jobs |
| `max_jobs` | INT | Configurable max parallel jobs (default: CPU count) |
| `prefer_affinity` | BOOL | Prefer this builder for recipes it has previously built (default: false) |
| `last_heartbeat` | TIMESTAMP | Last heartbeat time |
| `registered_by` | TEXT | Token name that registered this builder |
| `created_at` | TIMESTAMP | Registration time |

Unique constraint: `(name, org_slug)`.

### 3.2 `build_jobs` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `dag_id` | UUID (nullable) | Groups jobs from a single `remote-pack-all` submission |
| `org_slug` | TEXT (nullable) | NULL = global; non-NULL = org-scoped |
| `recipe_name` | TEXT | Recipe to build |
| `recipe_version` | TEXT | Version from recipe.yaml |
| `recipe_hash` | TEXT | Chain hash for cache dedup |
| `platform` | TEXT | Target platform |
| `arch` | TEXT | Target architecture |
| `config` | TEXT | `release` or `debug` |
| `link` | TEXT | `shared` or `static` |
| `builder_id` | UUID (FK, nullable) | Assigned builder (NULL while pending) |
| `status` | TEXT | `pending`, `dispatched`, `running`, `succeeded`, `failed`, `cancelled`, `timed_out` |
| `priority` | INT | Higher = runs first (default 0) |
| `timeout_seconds` | INT | Per-job timeout override (NULL = use server default) |
| `submitted_by` | TEXT | Token name that submitted the request |
| `submitted_at` | TIMESTAMP | Submission time |
| `started_at` | TIMESTAMP (nullable) | When builder claimed the job |
| `finished_at` | TIMESTAMP (nullable) | Completion time |
| `log_url` | TEXT (nullable) | Object storage URL for build log |
| `log_size_bytes` | BIGINT (nullable) | Log file size (counts against org quota) |
| `error_message` | TEXT (nullable) | Failure reason |
| `result_archive_url` | TEXT (nullable) | Published archive URL on success |

### 3.3 `build_job_deps` table

| Column | Type | Description |
|--------|------|-------------|
| `job_id` | UUID (FK) | The job that depends on another |
| `depends_on_job_id` | UUID (FK) | The prerequisite job |

Unique constraint: `(job_id, depends_on_job_id)`.

This table encodes the DAG edges.  A job with unmet dependencies
stays in `pending` until all prerequisite jobs reach `succeeded`.

### 3.4 `webhooks` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `url` | TEXT | Delivery URL (HTTPS required) |
| `events` | JSON | List of subscribed events |
| `org_slug` | TEXT (nullable) | NULL = global events; non-NULL = org-scoped |
| `secret` | TEXT | HMAC-SHA256 signing secret for payload verification |
| `active` | BOOL | Enabled/disabled |
| `registered_by` | TEXT | Token name |
| `created_at` | TIMESTAMP | Registration time |
| `last_delivery_at` | TIMESTAMP (nullable) | Most recent delivery |
| `consecutive_failures` | INT | Failure count (auto-disable after threshold) |

---

## 4. API Endpoints

### 4.1 Builder Registration & Management

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `POST` | `/v1/builders/register` | Register builder (platform, arch, labels, capabilities, max_jobs) | Publisher (global) or Org member (org-scoped) |
| `POST` | `/v1/builders/{id}/heartbeat` | Heartbeat + status update | Builder's own token |
| `DELETE` | `/v1/builders/{id}` | Unregister builder | Builder's own token or Admin |
| `GET` | `/v1/builders` | List builders (filterable by platform, arch, org, status) | Admin (global) or Org admin (org-scoped) |
| `GET` | `/v1/builders/{id}` | Builder detail | Admin or builder's own token |
| `PATCH` | `/v1/builders/{id}` | Update labels, max_jobs, capabilities | Builder's own token or Admin |

### 4.2 Build Jobs

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `POST` | `/v1/builds` | Submit build request (single recipe or batch with DAG) | Admin (global recipes) or Org admin/builder role |
| `POST` | `/v1/builds/pack-all` | Submit full recipe set as DAG (supports multi-platform + matrix expansion) | Admin (global) or Org admin/builder role |
| `GET` | `/v1/builds` | List jobs (filterable by status, platform, org, recipe, dag_id) | Admin or Org member |
| `GET` | `/v1/builds/{id}` | Job detail | Admin or Org member |
| `POST` | `/v1/builds/{id}/cancel` | Cancel pending/running job | Admin or submitter |
| `GET` | `/v1/builds/{id}/log` | Download build log | Admin or Org member |
| `GET` | `/v1/builds/{id}/log/stream` | SSE stream of live log | Admin or Org member |
| `DELETE` | `/v1/builds/{id}/log` | Delete build log (frees quota) | Admin |

### 4.3 Builder ↔ Server (Job Lifecycle)

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `WS` | `/v1/builders/{id}/ws` | WebSocket for job dispatch + log streaming | Builder's own token |
| `GET` | `/v1/builders/{id}/next-job` | Long-poll fallback (30s timeout) | Builder's own token |
| `POST` | `/v1/builds/{id}/claim` | Builder claims a dispatched job → `running` | Builder's own token |
| `POST` | `/v1/builds/{id}/complete` | Report success + upload result archive | Builder's own token |
| `POST` | `/v1/builds/{id}/fail` | Report failure + error message | Builder's own token |
| `PATCH` | `/v1/builds/{id}/log` | Append log chunk (during build) | Builder's own token |

### 4.4 Recipe Distribution

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `GET` | `/v1/recipes` | List all available recipes (with versions and hashes) | Builder's token or Admin |
| `GET` | `/v1/recipes/{name}` | Download recipe bundle (yaml + scripts + patches) | Builder's token or Admin |
| `POST` | `/v1/recipes` | Upload/update a recipe | Admin (global) or Org admin (org recipes) |

### 4.5 Webhooks

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `POST` | `/v1/webhooks` | Register webhook (url, events, secret) | Admin (global) or Org admin (org-scoped) |
| `GET` | `/v1/webhooks` | List webhooks | Admin or Org admin |
| `GET` | `/v1/webhooks/{id}` | Webhook detail + recent deliveries | Admin or Org admin |
| `PATCH` | `/v1/webhooks/{id}` | Update URL, events, active flag | Admin or Org admin |
| `DELETE` | `/v1/webhooks/{id}` | Remove webhook | Admin or Org admin |
| `POST` | `/v1/webhooks/{id}/test` | Send test payload | Admin or Org admin |

**Webhook events:**

| Event | Trigger | Payload |
|-------|---------|---------|
| `package.published` | New package version published | Package metadata (name, version, platform, arch, org) |
| `build.started` | Builder claims a job | Job ID, recipe, platform, builder name |
| `build.completed` | Job finishes successfully | Job ID, recipe, platform, archive URL, duration |
| `build.failed` | Job fails | Job ID, recipe, platform, error message |
| `build.cancelled` | Job cancelled by user/admin | Job ID, recipe, cancelled_by |
| `build.timed_out` | Job exceeded timeout | Job ID, recipe, platform, timeout_seconds, elapsed |
| `build.dag_completed` | All jobs in a DAG reached terminal state | dag_id, summary (succeeded/failed/cancelled/timed_out counts) |
| `builder.online` | Builder registers or comes back online | Builder name, platform, arch |
| `builder.offline` | Builder goes offline (missed heartbeats) | Builder name, last_heartbeat |

**Delivery format:**

```http
POST https://example.com/webhook
Content-Type: application/json
X-CvcPkg-Event: build.completed
X-CvcPkg-Signature: sha256=<HMAC-SHA256 of body using webhook secret>
X-CvcPkg-Delivery: <UUID>

{
  "event": "build.completed",
  "timestamp": "2026-05-30T12:34:56Z",
  "data": { ... }
}
```

Delivery retries: 3 attempts with exponential backoff (10s, 60s, 300s).
Auto-disable after 10 consecutive failures; re-enable via PATCH.

---

## 5. CLI Commands

### 5.1 Builder Agent

```
cvcpkg builder run \
    --server https://pkg.example.com \
    --token $CVCPKG_TOKEN \
    --name linux-builder-01 \
    --labels cvcpkg-builder,high-mem \
    --org myorg \
    --max-jobs 8 \
    --prefer-affinity \
    --work-dir /var/lib/cvcpkg/builds
```

Runs as a foreground process (or `--daemon` for background with PID
file).  On startup:

1. Detects platform, arch, CPU count.
2. Registers via `POST /v1/builders/register`.
3. Opens WebSocket to `/v1/builders/{id}/ws` (falls back to
   long-poll on `/v1/builders/{id}/next-job` if WebSocket fails).
4. Heartbeat thread: every 60s via the WebSocket control frame or
   `POST /v1/builders/{id}/heartbeat`.
5. On receiving a job: claims it, fetches recipe from
   `/v1/recipes/{name}`, executes the build (same flow as local
   `pack`), streams log via WebSocket or `PATCH`, publishes result
   via `POST /v1/builds/{id}/complete`.
6. On `SIGTERM`/`SIGINT`: finishes current jobs, sends offline
   heartbeat, unregisters, exits.

```
cvcpkg builder status [--server URL]    # Show builder info
cvcpkg builder stop [--server URL]      # Graceful shutdown via API
cvcpkg builder list [--platform X] [--org Y]  # List registered builders
```

### 5.2 Remote Build Dispatch

```
# Single recipe
cvcpkg remote-pack zlib \
    --platform linux --arch x86_64 \
    --config release --link shared \
    --server https://pkg.example.com

# All recipes as DAG
cvcpkg remote-pack-all \
    --platform linux --arch x86_64 \
    --config release --link shared \
    --server https://pkg.example.com \
    --keep-going \
    --priority 10

# Multi-platform submission (creates parallel DAGs, one per platform)
cvcpkg remote-pack-all \
    --platform linux,macos,windows,freebsd \
    --arch x86_64 \
    --config all --link all \
    --server https://pkg.example.com

# Matrix expansion: --config all expands to release+debug,
# --link all expands to shared+static.
# The above creates 4 platforms × 2 configs × 2 links = 16 parallel DAGs.

# Org-scoped build
cvcpkg remote-pack my-custom-lib \
    --org myorg \
    --platform linux --arch x86_64 \
    --server https://pkg.example.com
```

### 5.3 Build Job Management

```
cvcpkg builds list [--status pending|running|...] [--platform X] [--org Y]
cvcpkg builds info <job-id>
cvcpkg builds cancel <job-id>
cvcpkg builds cancel-all [--dag-id <dag-id>]   # Cancel all jobs in a DAG
cvcpkg builds log <job-id> [--follow]           # Stream or download log
cvcpkg builds log delete <job-id>               # Free log storage
cvcpkg builds purge [--older-than 30d] [--status failed|cancelled]
```

### 5.4 Webhook Management

```
cvcpkg webhook register \
    --url https://example.com/hooks/cvcpkg \
    --events package.published,build.completed,build.failed \
    --org myorg \
    --server https://pkg.example.com

cvcpkg webhook list [--org myorg]
cvcpkg webhook info <webhook-id>
cvcpkg webhook update <webhook-id> --events build.completed
cvcpkg webhook delete <webhook-id>
cvcpkg webhook test <webhook-id>
```

---

## 6. ACL Rules

### 6.1 New Role: `builder`

Added as an org-level role alongside `owner` and `member`:

| Org Role | Can register org builder | Can submit org build jobs | Can view org builds |
|----------|-------------------------|--------------------------|---------------------|
| `owner` | Yes | Yes | Yes |
| `builder` | Yes | Yes | Yes |
| `member` | No | No | Yes |

Global operations remain admin-only:

| Action | Required Auth |
|--------|---------------|
| Register global builder | Admin |
| Submit global build job | Admin |
| View all builders/jobs | Admin |
| Cancel any job | Admin or original submitter |
| Assign global builder to org work | Admin |
| Manage global webhooks | Admin |
| Manage org webhooks | Org admin or Org owner |
| Set log retention policy | Admin |
| Delete build logs | Admin |

### 6.2 Builder Token Binding

When a builder registers, the server records `registered_by` (the
token name).  Subsequent heartbeats, claims, and completions require
the same token.  This prevents one builder from impersonating another.

---

## 7. DAG Scheduling

When `remote-pack-all` is invoked:

1. **Matrix expansion**: The client expands `--platform`, `--config`,
   and `--link` arguments.  Comma-separated values and the special
   value `all` are supported:
   - `--platform linux,macos` → two platforms
   - `--config all` → `release` + `debug`
   - `--link all` → `shared` + `static`

   Each unique `(platform, arch, config, link)` tuple produces a
   separate DAG.  For example, `--platform linux,macos --config all
   --link all` creates 2×2×2 = 8 independent DAGs.

2. **Server loads all recipes** from its recipe store (not from the
   git repo — recipes are pushed to the server via
   `POST /v1/recipes`).

3. **Dependency resolution**: Topological sort produces a DAG.  Each
   recipe becomes a job node; `depends_on` edges come from recipe
   `deps` fields.

4. **Job creation**: One `build_jobs` row per recipe per DAG, all
   sharing the same `dag_id`.  Dependencies recorded in
   `build_job_deps`.  Cross-platform DAGs are independent — they
   share no edges.

5. **Scheduling loop** (runs continuously on the server):
   - Find all `pending` jobs whose dependencies are all `succeeded`.
   - For each ready job, find a matching online builder with
     `current_jobs < max_jobs` and matching `(platform, arch)`.
   - **Builder affinity**: If `prefer_affinity` is enabled on a
     matching builder, the scheduler prefers builders that have
     previously built the same recipe (cache locality).  Affinity is
     a soft preference — if the preferred builder is at capacity, the
     job goes to any available matching builder.  Affinity is
     configurable per-builder via `--prefer-affinity` at registration
     or `PATCH /v1/builders/{id}`.
   - Dispatch: send job via WebSocket (or mark for long-poll pickup).
   - If no matching builder exists, job stays `pending`.
   - If a dependency fails and `--keep-going` was not set, cancel
     all downstream jobs in the DAG.

6. **Parallelism**: Independent branches of the DAG run concurrently
   on the same or different builders.  A builder with `max_jobs=8`
   can run 8 independent jobs simultaneously.  Cross-platform DAGs
   run fully in parallel.

7. **Job timeout**: The server enforces a maximum build time per job.
   Default: 24 hours (configurable via `max-build-timeout-seconds`
   server setting).  Individual jobs can override with a shorter
   timeout via the `timeout_seconds` field.  When a job exceeds its
   timeout, the server transitions it to `timed_out` and notifies
   the builder to abort.  Downstream jobs are cancelled unless
   `--keep-going` was set.

8. **DAG completion**: When all jobs in a `dag_id` reach a terminal
   state (`succeeded`, `failed`, `cancelled`, `timed_out`), fire a
   `build.dag_completed` webhook with a summary.

### Cache integration

Before dispatching, the server checks the build cache
(`GET /v1/cache/status` with the job's `recipe_hash`).  If a cache
hit exists, the job is immediately marked `succeeded` with the cached
archive URL — no builder is needed.

---

## 8. Builder ↔ Server Communication

### 8.1 WebSocket Protocol (Preferred)

Connection: `wss://server/v1/builders/{id}/ws?token=<bearer>`

**Server → Builder messages:**

```json
{"type": "job.dispatch", "job": { "id": "...", "recipe_name": "zlib", ... }}
{"type": "job.timeout", "job_id": "...", "message": "exceeded 86400s limit"}
{"type": "recipe.push",  "recipe": { "name": "zlib", "version": "1.3.1", "bundle_url": "..." }}
{"type": "ping"}
```

**Builder → Server messages:**

```json
{"type": "job.claim",    "job_id": "..."}
{"type": "job.log",      "job_id": "...", "data": "base64-encoded-chunk"}
{"type": "job.complete",  "job_id": "...", "archive_url": "..."}
{"type": "job.fail",      "job_id": "...", "error": "..."}
{"type": "heartbeat",    "status": "online", "current_jobs": 3}
{"type": "pong"}
```

Reconnection: exponential backoff starting at 1s, max 60s.
If WebSocket is unavailable, builder falls back to REST long-poll.

### 8.2 Long-Poll Fallback

`GET /v1/builders/{id}/next-job` blocks for up to 30 seconds.
Returns a job object or 204 No Content.  Builder loops with a 1s
pause between polls.

### 8.3 Recipe Push

When a recipe is created or updated via `POST /v1/recipes`, the
server:

1. Stores the recipe bundle (yaml + scripts + patches) in object
   storage.
2. Sends a `recipe.push` message to all connected builders via
   WebSocket.
3. Builders download the bundle and update their local recipe cache.

This ensures builders always have the latest recipes without needing
git access.

---

## 9. Build Log Management

### 9.1 Storage

- Logs are streamed to object storage (same backend as package
  archives — S3, GCS, Azure, local, etc.).
- Path convention: `logs/{dag_id}/{job_id}.log` (or
  `logs/standalone/{job_id}.log` for single jobs).
- During the build, log chunks are appended via the WebSocket `job.log`
  message or `PATCH /v1/builds/{id}/log`.  The server buffers and
  flushes to storage periodically (every 10s or 64 KB).

### 9.2 Access

- Build logs are downloadable from `GET /v1/builds/{id}/log`.
- Package detail pages on the web UI show a "Build Log" link for
  packages that were built via remote builders.
- ACL: same as viewing the build job (Admin or Org member).

### 9.3 Quota

- Log size (`log_size_bytes`) is recorded on the `build_jobs` row.
- For org-scoped builds, log storage counts against the org's
  `storage_limit_bytes` quota (same as package archives).
- When an org approaches its quota, the server warns but does not
  block builds — logs can be deleted to reclaim space.

### 9.4 Retention Policy

Admins can set a log retention policy:

```
cvcpkg-server config set log-retention-days 90
```

- Server-side background task (or `POST /v1/admin/gc/logs`) deletes
  logs older than the retention period.
- Manual deletion: `cvcpkg builds log delete <job-id>` (Admin only).
- Bulk purge: `cvcpkg builds purge --older-than 30d --delete-logs`.

---

## 10. Implementation Phases

### Phase 1: Builder Registration & Heartbeat

**Scope**: Server-side builder registry, CLI `builder run` skeleton.

- [ ] Alembic migration: `builders` table
- [ ] `BuilderRow` ORM model, `DbBuilderStore` (CRUD, heartbeat,
  status transitions, stale-builder reaper)
- [ ] Pydantic models: `BuilderInfo`, `BuilderRegisterRequest`,
  `BuilderListResponse`
- [ ] API endpoints: register, heartbeat, unregister, list, detail,
  update
- [ ] `cvcpkg builder run` — register, heartbeat loop, graceful
  shutdown
- [ ] `cvcpkg builder list` / `status` / `stop`
- [ ] Unit tests for store, API, CLI
- [ ] Audit actions: `builder_register`, `builder_unregister`

### Phase 2: Job Queue & DAG Scheduling

**Scope**: Job submission, DAG resolution, dispatch loop.

- [ ] Alembic migration: `build_jobs`, `build_job_deps` tables
- [ ] `BuildJobRow`, `BuildJobDepRow` ORM models
- [ ] `DbBuildJobStore` (create, list, cancel, claim, complete, fail,
  find-ready-jobs, DAG creation from recipe deps)
- [ ] Pydantic models: `BuildJobInfo`, `BuildJobSubmitRequest`,
  `BuildJobListResponse`, `DagSubmitRequest`
- [ ] API endpoints: submit, pack-all, list, detail, cancel, claim,
  complete, fail
- [ ] DAG scheduler: background task that matches ready jobs to
  available builders (with configurable affinity preference)
- [ ] Multi-platform DAG support: `--platform X,Y,Z` creates
  parallel DAGs per platform
- [ ] Matrix expansion: `--config all` / `--link all` expand to
  all combinations
- [ ] Job timeout enforcement: background reaper marks overdue
  jobs as `timed_out` (default 24h, configurable)
- [ ] Cache integration: skip jobs with cache hits
- [ ] `cvcpkg remote-pack` / `remote-pack-all`
- [ ] `cvcpkg builds list` / `info` / `cancel` / `cancel-all`
- [ ] Unit and integration tests

### Phase 3: Build Execution & Log Streaming

**Scope**: Builder executes jobs, streams logs, publishes results.

- [ ] Builder job execution loop: fetch recipe, run build, stream
  log, upload archive, report completion/failure
- [ ] WebSocket protocol: `job.dispatch`, `job.claim`, `job.log`,
  `job.complete`, `job.fail`, heartbeat
- [ ] Long-poll fallback: `next-job` endpoint
- [ ] Log streaming: server buffers + flushes to object storage
- [ ] `GET /v1/builds/{id}/log` (download) and
  `GET /v1/builds/{id}/log/stream` (SSE)
- [ ] `cvcpkg builds log <id> [--follow]`
- [ ] Log deletion: `cvcpkg builds log delete <id>`
- [ ] Configurable parallelism: `--max-jobs N` (default: CPU count)
- [ ] Package page "Build Log" link in web UI

### Phase 4: Recipe Distribution

**Scope**: Server-managed recipe store, push to builders.

- [ ] Recipe storage in object storage (bundle = yaml + scripts +
  patches as tar.gz)
- [ ] `POST /v1/recipes` — upload/update recipe
- [ ] `GET /v1/recipes` / `GET /v1/recipes/{name}` — list/download
- [ ] WebSocket `recipe.push` message to connected builders
- [ ] Builder local recipe cache with version tracking
- [ ] `cvcpkg recipe push <name>` CLI command
- [ ] ACL: Admin for global recipes, Org admin for org recipes

### Phase 5: Webhooks

**Scope**: Event notification system.

- [ ] Alembic migration: `webhooks` table
- [ ] `WebhookRow` ORM model, `DbWebhookStore`
- [ ] Pydantic models: `WebhookInfo`, `WebhookRegisterRequest`,
  `WebhookListResponse`
- [ ] API endpoints: register, list, detail, update, delete, test
- [ ] Webhook delivery engine: background task with retry logic
  (3 attempts, exponential backoff, HMAC-SHA256 signing)
- [ ] Event emission points: publish, build state changes, builder
  state changes
- [ ] `cvcpkg webhook register` / `list` / `info` / `update` /
  `delete` / `test`
- [ ] Auto-disable after consecutive failures
- [ ] Audit actions: `webhook_register`, `webhook_delete`,
  `webhook_delivery_failed`

### Phase 6: Retention & Quota Management

**Scope**: Admin controls for log lifecycle.

- [ ] Log retention policy setting (server config)
- [ ] Background GC task for expired logs
- [ ] `POST /v1/admin/gc/logs` endpoint
- [ ] `cvcpkg builds purge` with `--older-than`, `--delete-logs`
- [ ] Org quota enforcement: warn on approach, track log bytes
- [ ] Admin settings: `log-retention-days` in runtime config

---

## 11. Migration

Single Alembic migration file adding all four tables (`builders`,
`build_jobs`, `build_job_deps`, `webhooks`).  The `builder` org role
is added as a value in the existing `org_members.role` column (no
schema change needed — it's a TEXT column).

---

## 12. Relationship to Existing Features

| Existing Feature | Interaction |
|------------------|-------------|
| **Build cache** | Remote builds check the server cache before dispatching. Completed builds auto-populate the cache. |
| **Mirror system** | Mirrors sync published packages regardless of whether they were built locally or remotely. |
| **Chunked upload** | Builder uses the existing chunked upload flow for large archives. |
| **Audit log** | All builder/job/webhook actions are audit-logged. |
| **Org storage limits** | Log storage counts against org quotas. |
| **Signing** | Remote-built archives are signed by the builder's key (the builder must have a signing key configured). |

---

## 13. Design Decisions (Resolved)

1. **Builder authentication**: Builders use standard `cvctok_` API
   tokens.  No separate credential type.  Can be revisited later if
   finer-grained revocation is needed.

2. **Job timeout**: The server enforces a configurable maximum build
   time.  Default: **24 hours**.  Setting:
   `max-build-timeout-seconds` (server config, `PATCH /v1/admin/settings`,
   or `cvcpkg-server config set max-build-timeout-seconds 86400`).
   Individual jobs can set a shorter timeout via the `timeout_seconds`
   field.  Timed-out jobs transition to `timed_out` status.

3. **Builder affinity**: Configurable per-builder.  The
   `prefer_affinity` flag (default: off) tells the scheduler to
   prefer builders that have previously built the same recipe,
   improving cache locality.  Affinity is a soft preference — when
   the preferred builder is at capacity, any matching builder is
   used.  Set via `--prefer-affinity` at registration or
   `PATCH /v1/builders/{id}`.

4. **Multi-platform DAGs**: Fully supported.  `--platform` accepts
   comma-separated values (e.g. `--platform linux,macos,windows`).
   Each platform creates an independent DAG.  Cross-platform DAGs
   share a parent `dag_id` for tracking but have no inter-DAG
   dependencies.

5. **Build matrix expansion**: Fully supported.  `--config all`
   expands to `release` + `debug`.  `--link all` expands to
   `shared` + `static`.  Combined with multi-platform, a single
   command like `--platform linux,macos --config all --link all`
   creates 2×2×2 = 8 parallel DAGs.  The server returns the
   parent `dag_id` and a list of sub-DAG IDs for tracking.
