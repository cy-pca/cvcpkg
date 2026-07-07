# Platform-coverage gaps blocking a PyPI release of `cvcpkg`

**Status:** Active — required before announcing `cvcpkg` on PyPI
**Author:** generated 2026-06-25 from `https://cvcpkg.org/v1/catalog`
revision 216 vs `recipes/*/recipe.yaml`
**Owner:** TBD

## Problem statement

We are ready to publish `cvcpkg` to PyPI (workflow, OIDC trusted
publishing, TestPyPI dry-run, and live smoke tests are all in place —
see [`docs/operator-runbook.md` §11](../../docs/operator-runbook.md)).

But a PyPI release means *anyone* doing `pip install cvcpkg && cvcpkg
install boost qt6 vtk grpc protobuf openssl` against the default
registry (`https://cvcpkg.org`) becomes a user. Today that user is
broken on Windows for most of the components they would actually want,
and broken on OpenBSD for almost everything.

This document tracks the catalog-coverage gaps that must close before a
public PyPI announcement, ordered by user-visible severity.

## Source-of-truth snapshot

Catalog revision 216 (2026-06-08) at `https://cvcpkg.org/v1/catalog`:

| Platform/arch | Distinct packages published | Recipes declaring it | Coverage |
|---|---|---|---|
| linux/x86_64 | 49 | 53 | 92.5% |
| macos/arm64 | 46 | 51 | 90.2% |
| wasm/wasm32 | 27 | 29 | 93.1% |
| freebsd/x86_64 | 31 | 44 | 70.5% |
| netbsd/x86_64 | 26 | 44 | 59.1% |
| windows/x86_64 | 26 | 48 | 54.2% |
| wasi/wasm32 | 3 | 6 | 50.0% |
| openbsd/x86_64 | 8 | 44 | 18.2% |

All published bundles are `release` only. No debug builds in the
catalog.

## Tier-1 blockers (must land before PyPI announcement)

### W1 — Windows: heavyweight components missing

The 22 recipes that declare `windows` but have no published
windows/x86_64 bundle:

- **GUI / visualization:** qt6, vtk, cgal
- **Core libraries:** boost, openssl, hdf5
- **Network / RPC:** grpc, protobuf
- **Imaging:** imagemagick, tiff
- **Math:** mpfr, levmar, nfft3
- **DB clients:** mariadb-connector-c, libpq
- **Other:** zlib, pcre2, re2, swig, libiimod
- **wasm tooling on Windows host:** wamr, wasmedge

**Most user-visible:** qt6, vtk, boost, grpc, protobuf, openssl,
hdf5. Without these, the headline workflow on Windows
(`cvcpkg install qt6 vtk boost`) returns "package not available on
windows/x86_64" and the user gives up.

**Hypothesis on root cause:** these are the recipes that historically
required a long single-job Windows build that exceeded the GitHub
runner timeout. The `dev` branch (`fix: builder disk cleanup +
Windows Defender exclusions`, 2026-06-06) and self-hosted Windows
runner (`sandipaws`, registered via SSH from `star-00` in v2.0.0)
should unblock most of these.

**Action items:**

- [ ] Inventory which of the 22 builds *attempted* on `sandipaws`
      and failed vs. were never attempted. Pull builder job history
      from the daemon.
- [ ] Re-submit the failing-but-attempted set via
      `cvcpkg builds submit-dag --platform windows --arch x86_64`.
- [ ] For never-attempted, audit each `recipes/<name>/build.ps1`
      for Windows portability (Strawberry/Git PATH, NASM, MSVC CRT
      `/MD` alignment, `liblzma` CONFIG mode — the v1.3.0 lessons
      should already apply but were not exhaustively validated on
      `sandipaws`).
- [ ] Document any recipes that cannot reasonably build on Windows
      so the recipe can mark itself unsupported instead of declaring
      `windows` and silently failing.

### W2 — Linux: `vtk` missing

`vtk` is declared for linux but only published for macos/arm64 and
wasm/wasm32. `vtk` is one of the four highest-value packages in the
catalog (alongside qt6, boost, hdf5) — its absence on linux/x86_64
is almost as bad as the Windows gap.

**Action items:**

- [ ] Submit `cvcpkg builds submit vtk --platform linux --arch x86_64`
      and capture the failure.
- [ ] Linux builders have plenty of resources; this is almost
      certainly a recipe issue (host_tools, runtime deps, or a
      missing transitive dep) rather than a capacity issue.

### W3 — macOS / Linux: `libpq` and `grpc` missing on linux

`libpq` is missing on every platform (linux, macos, windows, freebsd,
netbsd, openbsd). `grpc` is missing on linux/freebsd/netbsd/openbsd
but is published on macos.

**Action items:**

- [ ] Ship `libpq` on at least linux/macos/windows. `libpq` was
      added in the `feature/new-dependency-recipes` branch
      (2026-05-25) but never built. Either it has not been pushed
      to a builder yet or the build fails — check the recipe
      `recipes/libpq/recipe.yaml`.
- [ ] Ship `grpc` on linux/x86_64. macOS works, so the recipe is
      sound; the linux failure is likely environmental.

### W4 — `cvcpkg` PyPI release plumbing complete

(Tracked here so the whole picture is in one place.)

- [x] Hardened `.github/workflows/cvcpkg-publish.yml` with TestPyPI
      dry-run + Linux/macOS/Windows live smoke against `cvcpkg.org`.
- [x] Operator runbook §11 documents the PyPI/TestPyPI pending-publisher
      setup.
- [ ] **Register `cvcpkg` as a PyPI project** with a pending
      publisher matching `transfix/libcvc-deps` + workflow
      `cvcpkg-publish.yml` + environment `pypi`. See
      [operator runbook §11.1](../../docs/operator-runbook.md#111-one-time-setup).
- [ ] **Register `cvcpkg` on TestPyPI** with the same pending
      publisher, environment `testpypi`.
- [ ] Create GitHub Actions environments `pypi` and `testpypi`
      under Settings → Environments. No secrets needed (OIDC).
      Optional: require a human approver on the `pypi` environment.
- [ ] Smoke-tag `cvcpkg-v2.0.1rc1` to exercise the full pipeline
      through TestPyPI. Verify a TestPyPI install on a clean host.
- [ ] Cut the first PyPI tag (`cvcpkg-v2.0.1` or
      `cvcpkg-v2.1.0`).
- [ ] Bump `Development Status` classifier in
      [`pyproject.toml`](../../pyproject.toml)
      from `3 - Alpha` → `4 - Beta` (the system is in production at
      cvcpkg.org and exercised by `live-smoke` in CI).

## Tier-2 (target for the release after the first PyPI cut)

### W5 — WASI: complete the declared set

Three recipes declare `wasi` but have no published wasi/wasm32 bundle:
`libjpeg-turbo`, `xz`, `zlib`. These are small and the wasi-sdk
cross-toolchain is shipped, so this is mostly a "submit the jobs"
exercise.

### W6 — FreeBSD / NetBSD: close out the easy gaps

FreeBSD is at 70%, NetBSD at 59%. The pattern is the same on both:
13 / 18 declared-but-unpublished recipes including the predictable
heavy hitters (boost on netbsd, grpc/protobuf on both, vtk on
neither). The `feat/bsd-dual-builders` branch
(`ci: distribute BSD builds across two VMs per platform`, 2026-05-30)
should help capacity here; recipe-by-recipe debugging is the work.

### W7 — `wasm/wasm32`: finish the last two

`libiimod` and `qt6-wasm-singlethread` declare wasm but have not
published. `qt6-wasm-singlethread` is a separate recipe from
`qt6` because of pthread-vs-singlethread Qt configuration; finishing
it gives us first-class Qt-on-wasm including singlethread targets
for environments without SharedArrayBuffer.

## Tier-3 (post-PyPI, longer-term — defer to v2.1+)

### W8 — OpenBSD: 36 of 44 declared recipes unpublished

OpenBSD is essentially empty. Either:

- The OpenBSD VM is broken / underprovisioned and the BSD CI work
  in v1.6.1 only landed on NetBSD/FreeBSD; or
- The recipes were added optimistically without OpenBSD-specific
  testing.

**Recommendation:** mark OpenBSD as **Tier C** (community/best-effort)
in the recipe schema and *demote* the unbuilt declarations rather
than keeping a 36-recipe debt visible in the gap report. Re-promote
recipe-by-recipe as they actually build green.

### W9 — Debug builds

The catalog is `release` only. The roadmap calls for `debug` builds
too. Defer until after PyPI announcement; double the storage cost
and not on the critical path for first-time users.

### W10 — Additional architectures

linux/arm64, macOS/x86_64, windows/arm64, linux/musl, riscv64,
ppc64le. Covered by [`cvcpkg-2.0.md` §4 / Phase 8](cvcpkg-2.0.md);
not blocking a PyPI cut at x86_64/arm64-only coverage of the three
major desktop OSes.

## Suggested workflow

1. Stand up a "windows-gap" branch and submit per-recipe rebuilds
   on `sandipaws` for W1 (the 22 missing Windows packages),
   capturing failure modes in this document as we go.
2. In parallel, resolve W2 (`vtk` linux) and W3 (`libpq`/`grpc`
   on linux) since they are likely small fixes.
3. Once W1/W2/W3 are green, complete W4 — PyPI/TestPyPI pending
   publishers + environments + first `cvcpkg-v*rc*` smoke tag.
4. Cut the first stable PyPI tag and announce.
5. W5–W7 follow as a v2.1.x point release.
6. W8–W10 fold into the long-term cvcpkg 2.x roadmap.

## Progress log

### 2026-06-26 — stale-DAG cleanup + fresh gapfill submissions

**Diagnosis of root causes** behind the 161 pending jobs that had
accumulated since 2026-06-06:

1. **`/tmp` disk exhaustion on a linux builder.** Jobs `#1503`
   (openssl linux) and `#1505` (protobuf linux) failed with
   `fatal error: error writing to /tmp/cc*.s: No space left on
   device`. These pre-dated the `fix: builder disk cleanup`
   commit (`300a346`, 2026-06-06) which now removes
   `cvcpkg-{name}-*` work dirs after each job.
2. **Outdated `cvcpkg` on the `tfx`-user linux builder.** Job
   `#1507` (re2 linux) failed with
   `cvcpkg.builder.BuildError: Unknown script type: {script.suffix}`
   from `/home/tfx/.local/lib/python3.10/site-packages/cvcpkg/cli.py:4561`,
   which is the pre-v2.0.0 monolithic CLI path. The
   `deploy-prod.yml` builder-restart step did not refresh this
   particular host's install.
3. **`sandipaws` (the only registered Windows builder) is offline.**
   That fact alone accounts for ~half of the W1 missing-Windows-
   packages list: the queued jobs cannot dispatch.
4. **Orchestrator did not fall back to catalog deps when a same-DAG
   dep had failed.** Because openssl/protobuf/re2 failures landed
   inside the same `populate-20260606-*` DAGs as their dependents
   (vtk, grpc, qt6, cmake, libpq, …), all dependents stayed
   pending forever instead of using already-published versions
   from the catalog.

**Actions taken:**

- Cancelled the 18 stale `populate-20260606-{043740,070640,095925}-{linux,freebsd,netbsd,openbsd,wasm,windows}-x86_64-release-shared`
  DAGs (≈154 pending jobs flushed), plus the orphan
  `wasm-rebuild-20260606-230006-*` (6 jobs) and
  `a5eaf737-77c` (1 job) DAGs.
- Submitted fresh narrow gapfill DAGs targeting only the genuine
  gaps:
  - `gapfill-20260625-linux-x86_64-release-shared` —
    cmake, grpc, libpq, vtk (dispatched on builder #1; `clapack`
    skipped because no linux matrix in `recipes/clapack/recipe.yaml`).
  - `gapfill-20260625-freebsd-x86_64-release-shared` —
    9 jobs (cgal, vtk skipped — no freebsd matrix).
- Confirmed wasm builds are healthy independently (job `#2113 vtk
  wasm release static succeeded` ~hour before today's work).

**Refined gap list** (catalog snapshot 2026-06-25, after dedup of
recipes with no matching matrix entry):

| Platform | "Missing" raw | Real gap | Skipped (no matrix) |
|---|---|---|---|
| linux/x86_64/release/shared | 7 | 4 (cmake, grpc, libpq, vtk) | clapack, pthreads4w (win-only), qt6-wasm-singlethread (wasm-only) |
| windows/x86_64/release/shared | 30 | TBD (need sandipaws back online) | — |
| freebsd/x86_64/release/shared | 25 | ≥9 queued (cmake, grpc, imagemagick, libpq, nfft3, protobuf, re2, swig, tiff) | cgal, vtk |
| wasm/wasm32/release/static | 29 | 2 (libiimod, qt6-wasm-singlethread per W7) | host-tools (cmake/ninja/autoconf/m4/…), POSIX-only (libpq, openblas, mariadb-connector-c, readline, gettext, curl, lz4, bison, flex, libtool, swig), wasm-runtime hosts (wamr, wasmedge, wasmer, wasmtime) |

**Builder fleet inventory (verified 2026-06-26 via `tfx@star-00`):**

The registered builders are not all what they look like. The `cvcpkg
builder list` view shows daemons; the underlying machines are a mix
of bare hosts and incus VMs hosted across `star-00` and `star-01`.

| Registered | Host | Backing VM/host | cvcpkg | Status |
|---|---|---|---|---|
| `star-00` (linux, 4) | star-00 | bare host, user `tfx` | v2.0.0 (Jun 8) | online |
| `star-01` (linux, 2) | star-01 | bare host | current | online |
| `lat` (linux, 4) | lat | bare host | current | online |
| `rebota` (linux, 4) | rebota | bare host | current | online |
| `freebsd-build` (2) | star-01 | incus VM | current | online (since 2026-06-26 after star-01 FORWARD fix) |
| `freebsd-build-2` (2) | star-00 | incus VM | current | online |
| `netbsd-build` (2) | star-01 | incus VM | current | online (after crontab path fix) |
| `netbsd-build-2` (2) | star-00 | incus VM | current | online |
| `openbsd-build` (2) | star-00 | incus VM | current | online |
| `openbsd-build-2` (2) | star-01 | incus VM | current | online (after star-01 FORWARD fix) |
| `sandipaws` (windows, 2) | (off-net) | physical | unknown | **offline** |
| `phm-win11` (windows, 1) | prettyhatemachine | incus VM (Windows 11 Pro) | v2.0.0 | online (since 2026-06-26) |

The ENOSPC issue from job `#1503` was on the *bare* star-00 host's
`/tmp` (now showing 707G free), not on the cvcpkg-builder-{01,02}
incus VMs (those have only 9.6G total and are idle). The
`disk cleanup` fix in `300a346` is therefore on the host that
needed it, and the disk has since been freed.

The outdated re2 failure (`#1507`) was from a *previous* install of
cvcpkg on the same `tfx` host; the current install at
`~/.local/lib/python3.10/site-packages/cvcpkg/cli/_builder.py`
(dated 2026-06-08) is the split-CLI layout, so that bug is gone.

### W1 mitigation: ephemeral Windows builds via GitHub Actions

While `sandipaws` is offline, [`windows-build.yml`](../../.github/workflows/windows-build.yml)
mirrors [`macos-build.yml`](../../.github/workflows/macos-build.yml)
on a `windows-latest` runner (with MSVC, vcpkg, ninja, nasm, choco
cmake 3.31.7, pwsh, msys bash). It uses `cvcpkg pack-all
--server-cache-push` so each shard publishes incrementally as
packages finish — a single timed-out shard still ships everything
that built. Trigger via:

```
gh workflow run windows-build.yml \
  -R transfix/libcvc-deps --ref feat/pypi-release-prep \
  -f config=release -f link=shared -f shards=4
```

This is the unblock-W1-now path. The long-term plan remains to
bring `sandipaws` (and/or the local `win11` incus VM at
`10.65.122.140`) back online as persistent builders.

**Open operator tasks** (still require host access):

- [ ] **Bring `sandipaws` back online**. Now that `phm-win11`
      (the local win11 incus VM at `10.65.122.140`) is registered
      as builder #12, sandipaws is no longer on the critical
      path for Windows throughput — but a second windows builder
      restores capacity and survives phm-win11 host reboots.
- [x] **Restart cvcpkg daemons on `freebsd-build`, `netbsd-build`,
      `openbsd-build-2`** — done 2026-06-26. See progress log.

### 2026-06-26 (cont.) — builder fleet restored, phm-win11 registered

- All four linux builders online (star-00, star-01, lat, rebota).
- All six BSD builders online (`netbsd-build`, `netbsd-build-2`,
  `freebsd-build`, `freebsd-build-2`, `openbsd-build`,
  `openbsd-build-2`). Two issues found and fixed along the way:
  - `star-01` had `iptables FORWARD policy=drop` (Docker default),
    blocking incusbr0 → br-uplink NAT for its BSD VMs. Fixed with
    `sudo iptables -P FORWARD ACCEPT` and persisted via
    `/etc/systemd/system/incus-forward-accept.service` (oneshot,
    `RemainAfterExit=yes`).
  - `netbsd-build` crontab still referenced `/usr/local/bin/cvcpkg`
    but the binary now lives at `/usr/pkg/bin/cvcpkg`. Patched in
    place with `crontab -l | sed -e '...' | crontab -`.
- **`phm-win11` registered as builder #12** (windows/x86_64, online,
  max-jobs=1, work-dir `C:\Users\trans\cvcpkg-builder`). SSH via
  `trans@10.65.122.140` (passwordless ed25519, per
  [vm-provisioning/windows/WINDOWS-SETUP.md](../../../vm-provisioning/windows/WINDOWS-SETUP.md#L61)).
  The daemon runs via Windows scheduled task `cvcpkg-builder`
  (`/SC ONSTART /RU SYSTEM /RL HIGHEST`) which launches
  `C:\Users\trans\cvcpkg-builder\run-builder.bat`. Windows Defender
  exclusions added for the work-dir, `%TEMP%`, `pwsh.exe`,
  `bash.exe`, and `cvcpkg.exe`. WebSocket transport falls back to
  HTTP long-poll (server returns 404 for the WS upgrade route).
- Builder fleet token (from `creds.txt`):
  `cvctok_z2-N1_Km6dzn-1TFNtQ-QDi4pEp7r1j2rHgBY3R5W2A`.

After the linux/freebsd gapfill DAGs finish and the Windows
workflow completes, re-run the gap script at the bottom of this
document and update the coverage table.

## Reproducing the gap analysis

```bash
# Pull live catalog
curl -s https://cvcpkg.org/v1/catalog > /tmp/cvcpkg_catalog.json

# Compare with recipes/*/recipe.yaml build matrices
python3 - <<'EOF'
import yaml, os, glob, json
from collections import defaultdict
declared = {}
for r in sorted(glob.glob('recipes/*/recipe.yaml')):
    d = yaml.safe_load(open(r))
    name = (d.get('recipe') or {}).get('name') or os.path.basename(os.path.dirname(r))
    matrix = (d.get('build') or {}).get('matrix') or []
    declared[name] = {e['platform'] for e in matrix if isinstance(e, dict) and 'platform' in e}
pub = defaultdict(set)
for x in json.load(open('/tmp/cvcpkg_catalog.json'))['bundles']:
    pub[x['name']].add(x['platform'])
for plat in sorted({p for ps in declared.values() for p in ps}):
    miss = sorted(n for n, plats in declared.items() if plat in plats and plat not in pub.get(n, set()))
    print(f'{plat}: {len(miss)} missing — {", ".join(miss) if miss else "(none)"}')
EOF
```
