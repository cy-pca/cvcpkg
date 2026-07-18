# Known Issues

## BSD: cryptography package requires OS-level installation

### Symptom

`pip install cvcpkg` fails on FreeBSD, OpenBSD, and NetBSD when
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

| OS | Command | Package version |
|---|---|---|
| FreeBSD 14.4 | `pkg install -y py311-cryptography` | 46.0.7 |
| OpenBSD 7.7 | `pkg_add py3-cryptography` | 44.0.2 |
| NetBSD 10.1 | `pkgin -y install py313-cryptography` | 46.0.5 |

The BSD remote builders run this step automatically before
installing cvcpkg.

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

## Windows static builds: mpfr hang in vcpkg

### Symptom

When running vcpkg's `mpfr` port against the `x64-windows-static`
triplet on hosted GitHub Actions `windows-latest` runners, the build
stalls indefinitely (>1.5 h observed; never completes within the
6 h hosted-runner cap) after the autotools `configure` stage.

Comparable `x64-windows` (dynamic) builds of the same port and
version complete in ~6 minutes total via the same code path.

### Reproduction in CI

Observed twice in the `libcvc-deps` release pipeline on
`windows-latest` (windows-2025 → redirected to windows-2025-vs2026):

* Workflow runs `25697939991` and `25769524450`.
* `windows-vcpkg (Debug, x64-windows-static)` and
  `windows-vcpkg (Release, x64-windows-static)` jobs.
* vcpkg port `mpfr@4.2.2#1`, triplet `x64-windows-static`.

Log evidence (from job `75689399776`):

```
00:36:42  Installing 113/124 mpfr:x64-windows-static@4.2.2#1...
00:36:42  Building mpfr:x64-windows-static@4.2.2#1...
00:36:44  Successfully downloaded mpfr-4.2.2.tar.xz
00:36:45  -- Extracting source D:/vcpkg-downloads/mpfr-4.2.2.tar.xz
00:36:47  -- Loading CMake variables from .../cmake-get-vars_C_CXX-x64-windows-static.cmake.log
00:37:01  Downloading msys2-gmp-6.3.0-2-x86_64.pkg.tar.zst, trying https://mirror.msys2.org/msys/x86_64/gmp-6.3.0-2-x86_64.pkg.tar.zst
00:37:01  Successfully downloaded msys2-gmp-6.3.0-2-x86_64.pkg.tar.zst
00:37:05  Downloading msys2-mpfr-4.2.2-1-x86_64.pkg.tar.zst, trying https://mirror.msys2.org/msys/x86_64/mpfr-4.2.2-1-x86_64.pkg.tar.zst
00:37:06  Successfully downloaded msys2-mpfr-4.2.2-1-x86_64.pkg.tar.zst
   <silence — no further log output for >1h32m; job cancelled at 02:09>
```

Compare to the same step on the dynamic triplet (job
`75689399753`, `x64-windows`):

```
00:23:33  Building mpfr:x64-windows@4.2.2#1...
00:23:53  Successfully downloaded msys2-mpfr-4.2.2-1-x86_64.pkg.tar.zst
00:26:34  -- Using cached msys2-mpfr-4.2.2-1-x86_64.pkg.tar.zst
00:29:32  -- Fixing pkgconfig file: .../mpfr_x64-windows/lib/pkgconfig/mpfr.pc
00:29:32  Elapsed time to handle mpfr:x64-windows: 6 min
```

Same vcpkg port + version, same runner image, only the triplet
differs. Static triplet hangs after MSYS2 packages download
(during `vcpkg_make_configure`/`vcpkg_make_install` invocation of
the bundled `libtool`) and never produces another log line.

### Suspected cause

vcpkg's `mpfr` port uses `vcpkg_make_configure` + `vcpkg_make_install`
(autotools), which shells out to an MSYS2 `libtool`. On the
`x64-windows-static` triplet, libtool runs an extra static-archive
relink step (`ar`/`ranlib`) inside the MSYS2 environment that on
GitHub's hosted Windows runners exhibits one of:

* A `fork()` retry loop in MSYS2 against Windows Defender real-time
  scan on `D:\vcpkg-buildtrees/...` (the runner's `D:` drive is the
  Azure ephemeral volume).
* libtool hanging in a wait on a stalled background `cmd.exe`
  invocation from `vcpkg_execute_in_download_mode` when relinking
  with the static CRT.

Both patterns are known issues in the vcpkg + MSYS2 + hosted-runner
combination and have surfaced in adjacent vcpkg ports
(`gettext`, `libtool`, `gmp` historically). They do not occur on
the dynamic triplet because the libtool relink for `.dll` output
does not trigger the same path.

We have not isolated the root cause beyond confirming the symptom
reproduces deterministically across runs and that the dynamic
triplet of the identical port version is unaffected.

### Mitigation in this repo

`x64-windows-static` is removed from the release matrix. The
Windows artifact in `v1.0.0` is shared-only:

* `libcvc-deps-<ver>-windows-x86_64-debug.zip`
* `libcvc-deps-<ver>-windows-x86_64-release.zip`

A `*-static` Windows artifact is not produced.

### Re-enabling once fixed

When upstream vcpkg or its `mpfr` port resolves this (or we
introduce an overlay port that builds mpfr via a non-autotools
path, e.g. MSBuild project files generated from the source tree),
restore the `x64-windows-static` entries to both Windows matrices
in `.github/workflows/release.yml`:

```yaml
# windows-vcpkg.strategy.matrix.include:
  - build_type: Debug
    triplet: x64-windows-static
  - build_type: Release
    triplet: x64-windows-static

# windows.strategy.matrix.include:
  - build_type: Debug
    link: static
  - build_type: Release
    link: static
```

Then bump `vcpkg-deps-...-v2-...` and `vtk-...-v2` cache keys to
`-v3` if the prep stages' cache layout needs invalidating.

### Workaround for downstream consumers needing static-link Windows

If you need a static-link Windows bundle right now:

1. Clone this repo on a Windows host.
2. Run `vcpkg install <packages> --triplet x64-windows-static`
   locally with the same package list as
   `.github/workflows/release.yml` (`windows-vcpkg` job). The
   hang has only been observed on the GitHub-hosted runners; on
   most developer Windows machines (no real-time AV scan on the
   build tree) `mpfr` completes in well under 10 min.
3. Continue the staging steps manually following the patterns in
   the `windows` assemble job of the workflow.

Or, with our own self-hosted Windows runner (planned), the
`x64-windows-static` matrix entries can be restored with
`runs-on: [self-hosted, windows]` overriding the hosted runner.

## Windows static builds: grpc hang in vcpkg

### Symptom

When running vcpkg's `grpc` port (or any of its transitive
dependencies — `abseil`, `c-ares`, `protobuf`) against the
`x64-windows-static` triplet on hosted GitHub Actions
`windows-latest` runners, the build stalls indefinitely during the
`Install vcpkg dependencies` step. We have observed >1.5 h with
the job still in dependency install and no further progress, while
the same packages against the `x64-windows` (shared) triplet
finish in under 20 minutes.

This is the same class of failure as the `mpfr` hang documented
above: a vcpkg port build under `x64-windows-static` that completes
quickly under `x64-windows`.

### Mitigation in this repo

The `windows-vcpkg` job skips `protobuf[libprotoc]` and `grpc`
from the `x64-windows-static` install set. The `windows` assemble
job then restores the `x64-windows` (shared) cache alongside the
static one and stitches the gRPC / Protobuf stack into the static
bundle as shared `.dll` + import `.lib` + headers + cmake configs,
using the same hybrid-static fallback mechanism already in place
for cgal/gmp/mpfr.

The transitive support libraries shipped from the shared tree in
the static bundle are: `abseil`, `c-ares`, `OpenSSL`, `re2`,
`upb`, `utf8_range`, `zlib`. Codegen tools (`protoc.exe`,
`grpc_*_plugin.exe`) and the `tools/protobuf` and `tools/grpc`
subtrees are likewise copied from the shared tree.

The result: the Windows static bundle exposes the same
`find_package(Protobuf)` / `find_package(gRPC)` surface as the
shared bundle, just with those packages resolving to `.dll`
runtime artifacts inside an otherwise-`.lib` install prefix.

### Re-enabling once fixed

When upstream vcpkg or the affected ports resolve this on the
static triplet, restore `'protobuf[libprotoc]','grpc'` to the
static install package set in `.github/workflows/release.yml`
(`windows-vcpkg` job, inside the
`if ('${{ matrix.triplet }}' -eq 'x64-windows-static')` block)
and drop the matching entries from the assemble job's
`$fbPrefixes` fallback array.

