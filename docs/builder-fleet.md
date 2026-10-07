# Multi-homed builder fleet

The "Multi-tenant / shared builder fleet" feature (see
`roadmap/CVCPKG-ROADMAP.md`) lets
one physical builder serve **several namespaces** (the public catalogue *and*
one or more orgs) and register with **several servers** at once. This collapses
what used to be separate per-server / per-org builder deployments into a single
machine driven by one config file and one service unit.

## Two axes

1. **Served namespaces (one server).** `cvcpkg builder run` advertises a *set*
   of namespaces via `--org` (home) plus repeatable `--serve`:

   ```bash
   # One builder on cvcpkg.org that takes BOTH public and cvc-org jobs
   # (token from CVCPKG_TOKEN / an env file, not argv):
   cvcpkg builder run --server https://cvcpkg.org \
       --name catx-03 --org "" --serve cvc
   ```

   The scheduler dispatches a job to any builder whose served set contains the
   job's org. Each job's recipe fetch and publish use the *job's* namespace, so
   a shared builder never fetches or publishes a job under the wrong org.

2. **Multiple servers (the fleet supervisor).** `cvcpkg builder fleet` runs one
   `builder run` worker per server listed in a config file, under one process
   and one unit. Each worker is handed only its own server's token (see
   [Tokens](#tokens)).

## Consolidating the dev + prod fleets

Before, the dev fleet (pointed at the dev server) and the prod fleet (pointed at
`cvcpkg.org`) ran as separate builder instances even on machines that overlapped.
One fleet config replaces both:

```yaml
# /etc/cvcpkg/fleet.yaml
name: catx-03
max_jobs: 4
work_dir: /var/lib/cvcpkg-builder      # each server gets a subdirectory
servers:
  - server: https://cvcpkg.org         # prod
    token_env: CVCPKG_TOKEN_PROD
    serve: ["", "cvc"]                  # public + cvc org
  - server: https://pkg.tx.wtf          # dev / edge
    token_env: CVCPKG_TOKEN_DEV
    serve: ["", "cvc"]
```

```bash
# Inspect the workers that would run (tokens masked), without spawning them:
cvcpkg builder fleet --config /etc/cvcpkg/fleet.yaml --dry-run

# Run the supervised fleet:
cvcpkg builder fleet --config /etc/cvcpkg/fleet.yaml
```

The `cvcpkg-builder.service` unit's `ExecStart` becomes
`cvcpkg builder fleet --config /etc/cvcpkg/fleet.yaml`, with the per-server
tokens supplied as `Environment=` / `EnvironmentFile=` entries
(`CVCPKG_TOKEN_PROD`, `CVCPKG_TOKEN_DEV`). One unit, one config, both registries.

## Notes

- A builder always serves its own `--org`; `--serve`/`serve:` only *adds*
  namespaces. `""` is the public namespace.
- Package-namespace isolation is unchanged: org packages still never populate
  or shadow the public catalogue. Only *build execution* is shared.
- Tokens should come from `token_env` (or systemd `EnvironmentFile`), not be
  written literally into the config file.
- `ExecStart` may point at a pip-installed `cvcpkg` or at the single-file
  binary (`packaging/cvcpkg.spec`). Either way the supervisor starts each
  worker with the same cvcpkg it is running itself: `python -m cvcpkg builder
  run ...` from a pip install, `<binary> builder run ...` from the binary
  (each binary worker unpacks its own copy). A server-pushed self-update only
  applies to a pip install; to update a binary fleet, replace the binary and
  restart the unit.
- From the binary, the supervisor and every worker each unpack the binary into
  `$TMPDIR` (about 85 MB apiece for the combined build) and remove it on exit.
  A worker killed with SIGKILL leaves its copy behind, so give the unit a
  `TimeoutStopSec=` above the supervisor's 120 s drain (e.g. `150`) rather than
  the systemd default of 90 s.
- A worker that exits is restarted after `--restart-delay` (default 5 s). One
  that keeps dying within a minute of starting -- a revoked token, a stuck
  pidfile -- waits twice as long each time, up to 5 minutes, so a crash loop
  does not unpack a fresh 85 MB binary every few seconds; a worker that ran
  for a minute or more starts over at `--restart-delay`.
- Windows: each worker runs in its own console process group and is stopped
  with Ctrl+Break, which `builder run` drains on like Ctrl+C. A worker still
  running after the 120 s drain is killed with its whole process tree
  (`taskkill /T /F`).

## Tokens

The supervisor resolves every server's token, then starts each worker with
**only its own**, as `CVCPKG_TOKEN` in the worker's environment. The token is
never on a worker's command line (`ps` and `/proc/<pid>/cmdline` show argv to
every local user). Every server's `token_env` variable, every
`CVCPKG_...TOKEN...` variable and any variable holding a fleet token are
removed from each worker's environment, and the worker in turn drops its own
token from the environment its build scripts inherit -- a recipe built for one
server cannot read any server's token from its environment.

```ini
# cvcpkg-builder.service (excerpt)
[Service]
EnvironmentFile=/etc/cvcpkg/fleet.env     # CVCPKG_TOKEN_PROD=..., CVCPKG_TOKEN_DEV=...; 0600 root
ExecStart=/usr/local/bin/cvcpkg builder fleet --config /etc/cvcpkg/fleet.yaml
TimeoutStopSec=150
```

A pip-installed worker that self-updates re-execs onto the new code, but the
supervisor keeps running the code it started with until its unit restarts --
including how it starts workers. A supervisor from before this isolation (2.4.0
or older) passes each worker `--token` on the command line and the whole fleet
environment; a new worker drops `CVCPKG_...TOKEN...` names and its own token
from what its builds see, but cannot know a custom-named `token_env` variable.
So after an update that changes the supervisor, restart the fleet unit
(`systemctl restart cvcpkg-builder`) for the isolation above to apply.

This is hygiene, not a sandbox: the supervisor and all its workers run as one
user, and a build script can read any same-user process's environment and
memory. Servers (or orgs) that must not be able to reach each other's
credentials need **one user per server** -- separate `builder run` services
under separate accounts -- rather than one fleet.
