# Multi-homed builder fleet

The "Multi-tenant / shared builder fleet" feature (see `CVCPKG-ROADMAP.md`) lets
one physical builder serve **several namespaces** (the public catalogue *and*
one or more orgs) and register with **several servers** at once. This collapses
what used to be separate per-server / per-org builder deployments into a single
machine driven by one config file and one service unit.

## Two axes

1. **Served namespaces (one server).** `cvcpkg builder run` advertises a *set*
   of namespaces via `--org` (home) plus repeatable `--serve`:

   ```bash
   # One builder on cvcpkg.org that takes BOTH public and cvc-org jobs:
   cvcpkg builder run --server https://cvcpkg.org --token "$TOK" \
       --name catx-03 --org "" --serve cvc
   ```

   The scheduler dispatches a job to any builder whose served set contains the
   job's org. Each job's recipe fetch and publish use the *job's* namespace, so
   a shared builder never fetches or publishes a job under the wrong org.

2. **Multiple servers (the fleet supervisor).** `cvcpkg builder fleet` runs one
   `builder run` worker per server listed in a config file, under one process
   and one unit. Each worker holds only its own server's token — so credentials
   and build outputs are isolated per server by construction.

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
