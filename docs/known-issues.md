# Known Issues

## BSD: cryptography package requires OS-level installation

> **Largely resolved (2.4.0).** `cryptography` is no longer a core
> dependency — it moved behind the `[signing]` extra when the core install
> shrank to `click` + `PyYAML` (see [pypi-install.md](pypi-install.md), *Core
> vs extras*). A plain `pip install cvcpkg` no longer builds it, so the client
> installs cleanly on the BSDs with no Rust and no OS package. The section
> below applies **only** when you deliberately install `cvcpkg[signing]` (or
> `[all]`) — i.e. you need Ed25519 package signing/verification on a BSD host.
> Keeping the whole core off the heavy wheels is the same motivation as the
> multi-platform binaries (down to cosmo APE), where those wheels do not exist.

### Symptom

`pip install 'cvcpkg[signing]'` fails on FreeBSD, OpenBSD, and NetBSD when
building the `cryptography` dependency from source:

```
× pip subprocess to install build dependencies did not run successfully.
│ exit code: 1
  Collecting maturin!=1.12.0,<2,>=1.9.4
    Unsupported platform: 312
    Rust not found, installing into a temporary directory
  error: metadata-generation-failed
```

### Root cause

The `cryptography` Python package uses **maturin** as its build
backend (a Rust-based tool). On BSD platforms:

1. **maturin's platform detection fails** — it emits "Unsupported
   platform: 312" and cannot determine the target OS.
2. **Even with Rust installed from packages**, building maturin from
   source via `cargo install` either:
   - Fails due to Cargo.lock pinning dependencies that require a
     newer Rust (e.g., `time@0.3.47` needs rustc 1.88+, but OpenBSD
     7.7 ships rustc 1.86).
   - Segfaults (SIGSEGV in `cc1plus` or `rustc`) on OpenBSD 7.7
     due to compiler bugs.
3. **OpenBSD disk layout** — the default auto-partitioning allocates
   only ~3GB to `/usr` and 1GB to `/`. Rust alone needs ~750MB
   under `/usr/local`, and `cargo build` fills `/tmp` (on root).

### Solution

Install `cryptography` from **OS packages** (pre-built by
ports/packages maintainers) before running `pip install cvcpkg`:

| OS | Command | Package version (as tested) |
|---|---|---|
| FreeBSD 14.4 | `pkg install -y py311-cryptography` | 46.0.7 |
| OpenBSD 7.7 | `pkg_add py3-cryptography` | 44.0.2 |
| NetBSD 10.1 | `pkgin -y install py313-cryptography` | 46.0.5 |

The BSD remote builders run this step automatically before
installing cvcpkg.

### Escape hatch: standalone binaries need no pip at all

The [`cvcpkg-standalone.yml`](../.github/workflows/cvcpkg-standalone.yml)
workflow builds single-file PyInstaller executables that embed the
Python runtime and every dependency — `cryptography` included — so
neither pip nor Rust nor OS packages are needed. It attaches these to
the GitHub Release for each `cvcpkg-v*` tag, alongside `.sha256`
checksums:

- `cvcpkg-freebsd-x86_64`
- `cvcpkg-openbsd-x86_64`
- `cvcpkg-netbsd-x86_64`

(plus `cvcpkg-linux-x86_64`, `cvcpkg-macos-arm64`, and
`cvcpkg-windows-x86_64.exe` for the other platforms).

The quick-install script detects FreeBSD/OpenBSD/NetBSD on x86_64,
downloads the matching asset, verifies its sha256, and installs to
`~/.local/bin` (override with `CVCPKG_INSTALL_DIR`; pin a tag with
`CVCPKG_VERSION`):

```sh
curl -fsSL https://cvcpkg.org/install.sh | sh
```

One build-side quirk: upstream PyInstaller ships no NetBSD bootloader,
so the NetBSD binary is built from a PyInstaller source tree with
[`recipes/pyinstaller-cp313/netbsd-platform-tables.patch`](../recipes/pyinstaller-cp313/netbsd-platform-tables.patch)
applied (submitted upstream). The other five platforms use the same
PyInstaller version from PyPI.

### Related: the jsonschema pin

`jsonschema` is likewise no longer core — it moved behind the `[validate]`
extra (only `cvcpkg validate` and the `kind: image` layout gate construct a
`Draft202012Validator`). So a plain `pip install cvcpkg` no longer pulls it,
and the BSD `rpds-py` problem below only applies to `pip install
'cvcpkg[validate]'`.

`cvcpkg` still pins `jsonschema = ">=4.0,<4.18"` in
[`pyproject.toml`](../pyproject.toml): jsonschema 4.18 swapped the pure-Python
`pyrsistent` for `rpds-py`, which is Rust. PyPI ships no BSD wheels for it and
the BSD builders have no cargo, so an unbounded bound would make `pip install
'cvcpkg[validate]'` unsatisfiable on the BSDs. Lifting the pin is gated on a
rust-toolchain recipe — see
[roadmap/platform-coverage-pypi-blockers.md](roadmap/platform-coverage-pypi-blockers.md)
(§W11). When you do install `[validate]` on a BSD VM, do not install jsonschema
explicitly — let pip resolve it inside the pin.

### OpenBSD disk requirements

The `openbsd-build` VM has the following disk layout to accommodate
Rust and build artifacts:

- `/usr/local` mounted on a separate 43GB partition (`sd0f`) —
  required because the default `/usr` is only 3GB.
- `TMPDIR=/usr/local/tmp` must be set for all builds to avoid
  filling the 1GB root partition.
- fstab entry: `/dev/sd0f /usr/local ffs rw,nodev 1 2`

### Verified functionality

With the OS-packaged `cryptography`, the full `cvcpkg.signing`
module works on OpenBSD 7.7:

- Ed25519 key generation (`generate_keypair`)
- File signing (`sign_file`) and verification (`verify_file`)
- Bytes signing (`sign_bytes`) and verification (`verify_bytes`)
- PEM key serialization and keyring management

## cvcpkg.org: sporadic 502s through the reverse proxy

### Symptom

Very occasionally, a request to `cvcpkg.org` returns `502 Bad
Gateway` and succeeds on the next attempt. Measured against
production in August 2026: 40 of 540,889 requests over two days
(0.0074%), spread 1–4 per hour.

### Root cause

`cvcpkg.org` runs uvicorn behind a TLS-terminating reverse proxy
(see [deployment-guide.md](deployment-guide.md)). A proxy in that
position occasionally hands back a 5xx that has nothing to do with
the request — reusing a backend keep-alive connection the app
already closed, a worker recycling, a deploy swapping the container.

### Mitigation

Since #502 the client's download path retries transient failures
itself: [`src/cvcpkg/retry.py`](../src/cvcpkg/retry.py) retries 408,
425, 429, 502, 503, 504 and connection-level faults (reset, timeout,
incomplete read, remote disconnect) on idempotent GET/HEAD requests,
bounded by both attempt count and a wall-clock budget. Deliberately
*not* retried: every 4xx, plain 500 (a deterministic application
bug), TLS verification failures, and SHA-256 mismatches.

If a single 502 fails an install for you, update cvcpkg. If you hit
`cvcpkg.org` directly from your own scripts, add your own retry —
e.g. `curl -fsSL --retry 3`.

## Windows static builds: vcpkg hangs on x64-windows-static (historical)

The original libcvc-deps release pipeline
(`.github/workflows/release.yml`) built Windows bundles with vcpkg on
hosted GitHub runners. Two of its autotools-based ports hung
indefinitely under the `x64-windows-static` triplet while the same
port versions on the dynamic `x64-windows` triplet, same runner
image, completed in minutes:

- **mpfr** — silent stall right after the MSYS2 gmp/mpfr package
  downloads, inside the `vcpkg_make_configure`/`vcpkg_make_install`
  libtool path; >1.5 h with no further log output vs ~6 min on the
  dynamic triplet. Reproduced across release runs `25697939991` and
  `25769524450`.
- **grpc** (and transitive deps `abseil`, `c-ares`, `protobuf`) —
  the same class of stall during dependency install.

The root cause was never isolated. The suspects were an MSYS2
`fork()` retry loop against the runner's real-time AV scanning and a
libtool static-relink hang — both known trouble spots for the
vcpkg + MSYS2 + hosted-runner combination. The hang was never
observed on developer machines without real-time AV scanning of the
build tree.

Two consequences baked into the `v1.0.0` release still stand:

- The Windows artifacts are shared-only
  (`libcvc-deps-<ver>-windows-x86_64-{debug,release}.zip`); no
  `*-static` Windows artifact was ever produced.
- The gRPC/Protobuf stack in the Windows bundle resolves to `.dll`
  runtime artifacts stitched in from the shared triplet (the same
  hybrid-static fallback used for cgal/gmp/mpfr).

The re-enable instructions that used to live in this section targeted
matrix entries in `release.yml`, which was retired once package
building migrated to the cvcpkg remote-builder system and deleted in
July 2026 (commit `79946574`). Windows packages are now produced by
the recipe pipeline instead — `windows-build.yml` on hosted
`windows-2022` runners plus the self-hosted Windows builders (see
[cvcpkg-remote-builders.md](cvcpkg-remote-builders.md)) — which does
not build through vcpkg triplet matrices. If you resurrect a
vcpkg-based static Windows build, assume the hang still reproduces on
hosted runners until verified otherwise.

## BSD standalone binaries: no `cvcpkg-builder` runner on cy-pca yet

### Symptom

A `cvcpkg-vX.Y.Z` GitHub Release built by
[`cvcpkg-standalone.yml`](../.github/workflows/cvcpkg-standalone.yml) ships only
the Linux, macOS and Windows single-file binaries. The FreeBSD, OpenBSD and
NetBSD assets (`cvcpkg-freebsd-x86_64`, `cvcpkg-openbsd-x86_64`,
`cvcpkg-netbsd-x86_64`) are missing, so `curl -fsSL https://cvcpkg.org/install.sh | sh`
fails on the BSDs with *"no prebuilt binary for &lt;platform&gt;/x86_64 yet"*.

### Root cause

The workflow's `build-bsd` job is **double-gated**:

1. `if: vars.ENABLE_BSD_BUILDS == 'true'` — off by default.
2. `runs-on: [self-hosted, Linux, X64, cvcpkg-builder]` — it drives the BSD
   builder VMs (`10.66.77.x`) over SSH from a self-hosted runner labelled
   `cvcpkg-builder`, which is intended to live on the **star cluster**
   (`star-00` shares the BSD VMs' LAN — see the runner table in
   [ci-cd-pipeline.md](ci-cd-pipeline.md)).

After the relocation to `cy-pca/cvcpkg`, **no `cvcpkg-builder` runner is
registered** (the org has only `catx-03-cy-pca` and `star-01-cypca`, neither
with that label). So even with `ENABLE_BSD_BUILDS=true`, `build-bsd` would sit
queued with no runner and stall the release job (which `needs: [build,
build-bsd]`). The variable is therefore kept **`false`**, and the BSD binaries
are cut out-of-band.

### Cutting the BSD binaries today (manual)

Until a `cvcpkg-builder` runner exists, build the three BSD columns with
[`scripts/build-bsd-standalone.sh`](../scripts/build-bsd-standalone.sh) from any
host that can SSH to the BSD VMs (e.g. `prettyhatemachine` or `star-00`, both on
the `10.66.77.x` LAN):

```bash
git checkout cvcpkg-vX.Y.Z          # build the exact released source
scripts/build-bsd-standalone.sh cvcpkg-vX.Y.Z
```

It mirrors the `build-bsd` job step-for-step (same PyInstaller version, the
NetBSD bootloader patch, the `--version` + `validate recipes/zlib` smoke test),
writes `dist-bsd/cvcpkg-*bsd-x86_64` + `.sha256`, and attaches them to the
release with retry (GitHub's asset upload 500s intermittently on large files).
`cvcpkg-v2.2.2`'s BSD binaries were produced this way.

### Automating it (the real fix)

1. Register a self-hosted **org** runner on the star cluster (a host that shares
   the BSD VMs' LAN, i.e. can `ssh root@10.66.77.x`) with labels
   `self-hosted,Linux,X64,cvcpkg-builder`. Use a **fresh tarball install** — a
   dir-copy of an existing runner fails `config.sh` with *"already configured"*.
2. Confirm the `FREEBSD/OPENBSD/NETBSD_BUILDER_IPS/VMS` repo variables still
   point at live VMs (they did as of the relocation).
3. Set `ENABLE_BSD_BUILDS=true`.

The next `cvcpkg-v*` tag then builds and attaches the BSD binaries
automatically, and this manual step is no longer needed.
