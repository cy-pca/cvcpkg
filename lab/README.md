# Federation laboratory

Proves **cross-server (federated) dependency resolution**: a package on one
cvcpkg server can depend on a (public *or* private) org package hosted on a
different cvcpkg domain, resolved with per-registry credentials and gated by a
trust allowlist.

## Topology

```
  edge-a   publishes  app             ── dep cvc://edge-b/shell/iqi-core  (PRIVATE)
  edge-b   publishes  shell/iqi-core  ── dep cvc://edge-c/pub/base        (public org)
  edge-c   publishes  pub/base        (leaf)
  upstream (canonical)                ── edges populate their PUBLIC catalog from here
```

- `server` in a dependency is a **logical host** (`edge-b`), never a URL.
- The client's `registries.yaml` maps host → `{url, token}`. That single file is
  the **URL map**, the **per-domain credential store**, and the **allowlist**:
  a host that is not listed is refused (no SSRF via recipe-supplied hosts).
- A remote package's *bare* deps resolve on that remote; only explicit `cvc://`
  refs cross a boundary.

## Run it — automated, in-process (no docker)

Spins up three real `cvcpkg-server` processes, seeds the chain, and asserts the
full behaviour:

```
PYTHONPATH=src python3 lab/federation_lab.py
```

Asserts: the closure resolves deepest-first across all three servers; each node
is fetched from the correct registry with that registry's token; the **private**
edge-b package is **invisible without a valid edge-b token**; and an
**un-allowlisted host is refused**. Also runs as a gated pytest:
`pytest tests/integration/test_federation_lab.py`.

## Run it — realistic, multi-host (docker)

```
docker compose -f docker-compose.federation.yml up --build -d
```

Brings up `upstream` + `edge-a/b/c` (each populating its public catalog from
`upstream`, each hosting its own org namespaces). Seed the same chain against the
containers (bootstrap an admin token per server with `cvcpkg-server bootstrap`,
create the orgs, and `cvcpkg publish --org ...` with `required_deps` carrying the
`server` host), then point a client's `registries.yaml` at the edges and resolve.

## `registries.yaml` shape

```yaml
registries:
  edge-b:
    url: http://edge-b:8420
    token: <a token that is a member of edge-b's private orgs>
  edge-c:
    url: http://edge-c:8420
    token: <edge-c token>
```

Override the file location with `$CVCPKG_REGISTRIES_FILE`, or pass the whole map
inline as YAML/JSON via `$CVCPKG_REGISTRIES` (handy in CI/containers).
