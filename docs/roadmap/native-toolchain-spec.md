# SCOPE: Pinned Native C/C++ Toolchain Recipe (`gcc-toolchain`)

Implementation spec for `docs/roadmap/hermetic-native-toolchain.md`. Consolidates Report 1 (packaging mechanism) and Report 2 (toolchain selection). All paths relative to `/home/joe/src/cvc/libcvc-deps`.

---

## 1. DECISION — compiler, version, source, floor

**Compiler: GCC, not Clang.** The published catalog (vtk `9.5.0+cvc.1`, etc.) is already built with `g++` (`env-linux.sh:21`) and carries the GNU `libstdc++` C++ ABI (Report 2 §2). A stock Linux Clang *does not* pin the C++ runtime — it detects and links the build box's system `libstdc++`, so "pin Clang" does not pin the ABI. Truly pinning under Clang requires either a second pinned GCC just for its libstdc++ (`clang --gcc-install-dir=`, two artifacts) or switching to `libc++` (a different, incompatible ABI → catalog-wide rebuild, unsafe mixed loads). Pinning GCC gives compiler + `libstdc++` + ABI in one artifact, preserves the ABI already in the catalog, and needs zero recipe source changes (recipes inherit `CC`/`CXX`).

**Exact version: GCC 14.2.0, glibc 2.28 sysroot floor.**
- GCC 14 matches what `manylinux_2_28` / `manylinux_2_34` currently ship (both bumped to gcc-toolset-14), so our binaries and the PyPI wheels we sit alongside come from the same compiler generation (Report 2 §1).
- **glibc 2.28** is the hard constraint: it is the `manylinux_2_28` floor, and **NumPy 2.3.0+ (2025-06-07) moved its wheels to `manylinux_2_28`** (current tag `manylinux_2_27_x86_64.manylinux_2_28_x86_64`). Our published packages must import alongside those wheels, so the glibc symbol floor must be **≤ 2.28**. This eliminates any dev-box GCC (Ubuntu 24.04 = glibc 2.39). The floor is set by the glibc the compiler *links against* (its sysroot), not the GCC version — GCC 14 + a glibc-2.28 sysroot yields 2.28-floored binaries.

**Relocatable source (SHA256-pinnable):**
- **Primary (full provenance): crosstool-NG-built** GCC 14.2.0 / glibc 2.28 / binutils 2.42 / linux-headers ~4.18, built once in CI, tarball re-hosted on our mirror with our own SHA256 (same model as GHC/Python/emscripten host-tools). Inputs have canonical `ftp.gnu.org` URLs with GNU-published checksums. ~30–60 min one-time bootstrap per (version × platform), cached forever.
- **Fast-start / phase-1 fallback (identical observable contract): extract `gcc-toolset-14`** from `quay.io/pypa/manylinux_2_28_x86_64` (pin by **image digest** `@sha256:…` + `gcc-toolset-14-*.rpm` versions), re-host the extracted tree as a tarball with our SHA256. Same GCC 14 / glibc 2.28, zero bootstrap. No single upstream tarball URL exists for this path — that is its only drawback.
- **Rejected:** Red Hat gcc-toolset *as a runtime dep* (RHEL-only, hardcoded `/opt/rh` paths); LLVM/Clang tarballs (don't pin libstdc++); Bootlin prebuilts (2025.08 = glibc 2.41, floor too high).

Whichever acquisition path: the contract is **GCC 14, libstdc++ from GCC 14, glibc 2.28 floor, one SHA256.** GCC is relocatable by design — `libgcc`/`libiberty` `make_relative_prefix()` locates `libexec/gcc`, headers, and `libstdc++` relative to `argv[0]`; set `GCC_EXEC_PREFIX` or invoke via absolute path (Report 2 §1a).

---

## 2. RECIPE SHAPE

**Name:** `gcc-toolchain` (Linux/BSD). Model it on `recipes/wasi-sdk/` — `source.type: prebuilt` with a per-`(os,arch)` `artifacts:` map, each entry SHA256-pinned (Report 1 §1b, §6.1; Report 2 doctrine "repackage a bindist, key on content hash," GHC Phase 7.5).

```yaml
schema_version: 1
recipe:
  name: gcc-toolchain
  upstream_version: "14.2.0"
  cvc_revision: 1
  homepage: https://gcc.gnu.org
  license: GPL-3.0-with-GCC-exception
  tags: [tools, compiler, toolchain]
  description: >-
    Pinned native GCC 14.2 + libstdc++ + glibc-2.28 sysroot floor
    (manylinux_2_28-compatible). Owns the GNU C++ ABI end-to-end so the
    whole native catalog is coherent by construction.

# NOTE: no cross_toolchain block — see §3. Native (target==host) toolchains
# are NOT auto-merged by _collect_toolchains (builder.py:1921-1922). Activation
# is via env-linux.sh reading CVC_NATIVE_TOOLCHAIN_DIR.

source:
  type: prebuilt
  base_url: https://mirror.cvc/<path>            # our re-hosted tarballs
  artifacts:
    linux-x86_64: gcc-toolchain-14.2.0-glibc2.28-x86_64-linux.tar.zst
  # sha256 per artifact (mirror.py / manifest pinning)

build:
  build_type_independent: true                   # advisory today (§ below)
  matrix:
    - { platform: linux, script: build.sh }

package:
  files: [bin/, lib/, lib64/, libexec/, include/, x86_64-*/, sysroot/]
```

- **Packaging (mirror emsdk/wasi-sdk/python):** `build.sh` extracts the prebuilt tree and `rsync`/stages it verbatim into the install prefix — no compile at consume time. Layout carries `bin/{gcc,g++,ar,ranlib,nm,strip,ld,...}`, `libexec/gcc/x86_64-*/14/`, `lib/gcc/.../14/{libstdc++.so.6,libgcc_s.so.1}`, and the bundled `sysroot/` (glibc 2.28).
- **host_tools / build-only prefix:** install into the **build-only prefix** (`--build-prefix` / `--host-tools-prefix`) and flag `host_tool` so the compiler never ships inside a consumer bundle (e.g. vtk). `build_prefix/bin` is prepended to `PATH` and `build_prefix/lib` to `LD_LIBRARY_PATH` at build time (`builder.py:712-720, 726-738`). This mirrors the `host_tools.py` strip model (Report 1 §3, §6.3). Consumed by every native recipe via `depends.host_tools: [gcc-toolchain]` (or globally, see §3).
- **Relocatable RPATH:** the shipped `libstdc++.so.6` / `libgcc_s.so.1` must be `$ORIGIN`-relocatable via `builder.py:_patch_elf_rpath` (`_ELF_RPATH_PLATFORMS`), and consumer packages must RPATH to the toolchain's runtime libs (or bundle them) so the ABI pin is real at runtime, not just compile time (Report 1 §5, §6.8).
- **Build vs prebuilt:** prebuilt tarball (phase 1 = manylinux-extract; later = crosstool-NG output). `llvm` proves cvcpkg *can* build a full toolchain from source, but the GHC "repackage a bindist" doctrine says prefer prebuilt + content hash.
- **Size:** ~300–500 MB extracted (compiler + libstdc++ + binutils + sysroot).
- **`build_type_independent: true`:** advisory only today — not read by any Python (`grep` over `src/cvcpkg/*.py` returns nothing); it's manifest/documentation metadata. Set it anyway for consistency with every other toolchain recipe; the catalog-key dedup it implies is future wiring (split-distribution roadmap).

---

## 3. ENV INTEGRATION — `recipes/_common/env-linux.sh`

Activation is **design (A)** from Report 1 §3: env-var redirection, mirroring `env-wasi.sh:30-36`. **Do not** use the `cross_toolchain` auto-merge — `_collect_toolchains` short-circuits `if target_platform == host_platform: return []` (`builder.py:1921-1922`), so a native toolchain is never merged by that path.

**Edit 1 — replace `env-linux.sh:20-21`.** Point the toolchain vars at the pinned prefix when set, falling back to system for bootstrap (before the toolchain itself exists):

```sh
# Pinned native toolchain (gcc-toolchain host-tool). When installed, its
# prefix is exported as CVC_NATIVE_TOOLCHAIN_DIR by the build harness
# (build_prefix). Fall back to system compiler when unset (bootstrap only).
if [[ -n "${CVC_NATIVE_TOOLCHAIN_DIR:-}" ]]; then
    _tc="${CVC_NATIVE_TOOLCHAIN_DIR}"
    export CC="${_tc}/bin/gcc"
    export CXX="${_tc}/bin/g++"
    export AR="${_tc}/bin/gcc-ar"
    export RANLIB="${_tc}/bin/gcc-ranlib"
    export NM="${_tc}/bin/gcc-nm"
    export STRIP="${_tc}/bin/strip"
    export LD="${_tc}/bin/ld"
    export CVC_TC_SYSROOT="${_tc}/sysroot"
    export CFLAGS="${CFLAGS:-} --sysroot=${CVC_TC_SYSROOT} -B${_tc}/bin"
    export CXXFLAGS="${CXXFLAGS:-} --sysroot=${CVC_TC_SYSROOT} -B${_tc}/bin"
    export LDFLAGS="${LDFLAGS:-} --sysroot=${CVC_TC_SYSROOT}"
    unset _tc
else
    export CC="${CC:-gcc}"
    export CXX="${CXX:-g++}"
fi
```

**Edit 2 — reproducibility knobs (append after the block above).** Neither `SOURCE_DATE_EPOCH` nor `-ffile-prefix-map` exists in linux/macos/windows env today; the only in-repo precedent is `env-cosmo.sh:47`. Per Report 2 §4:

```sh
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-${CVC_SOURCE_EPOCH:-0}}"
export CFLAGS="${CFLAGS:-} -ffile-prefix-map=${CVC_BUILD_DIR}=. -ffile-prefix-map=${CVC_SOURCE_DIR}=."
export CXXFLAGS="${CXXFLAGS:-} -ffile-prefix-map=${CVC_BUILD_DIR}=. -ffile-prefix-map=${CVC_SOURCE_DIR}=."
export LDFLAGS="${LDFLAGS:-} -Wl,--build-id=sha1"
export ARFLAGS="crD"          # deterministic archives (zero uid/gid/mtime/mode)
export LC_ALL=C TZ=UTC
```

(`SOURCE_DATE_EPOCH` should be derived from each recipe's source commit/tarball mtime via a new `CVC_SOURCE_EPOCH`; `0` is the safe default and matches `env-cosmo.sh`.)

**How recipes inherit it with zero per-recipe change:** every `build.sh` sources `env-linux.sh`; `cvc_cmake_build()` (`:58-74`) already passes `CMAKE_CXX_STANDARD=17`, PIC, `$ORIGIN` RPATH, and now the redirected `CC`/`CXX` flow into CMake automatically. The single remaining question is **how `CVC_NATIVE_TOOLCHAIN_DIR` gets set** for every native build. Two options:

- **(A1, minimal):** the harness sets `CVC_NATIVE_TOOLCHAIN_DIR=<build_prefix>` whenever `gcc-toolchain` is present in the build prefix — a small addition to `_build_env` (analogous to the existing `cross_toolchain_env` templating at `builder.py:702-704`), keyed on the recipe being installed rather than on target≠host. No per-recipe edit.
- **(A2):** add `gcc-toolchain` to a global/default `host_tools` set so it is always in the build prefix; `env-linux.sh` derives `CVC_NATIVE_TOOLCHAIN_DIR` from `${CVC_BUILD_PREFIX}` when a sentinel (`${CVC_BUILD_PREFIX}/bin/gcc` from our toolchain) exists.

Recommend **A1**. Apply the same `CC`/`CXX` redirection consistently to `env-freebsd.sh` / `env-netbsd.sh` / `env-openbsd.sh` (they use `${CC:-cc}` today). macOS/Windows: §5.

---

## 4. ROLLOUT — single-toolchain catalog without breaking consumers

The GHC Phase 7.5 model: "a compiler bump rebuilds the world; the builder fleet absorbs it as a scheduled event." Order of operations:

1. **Land `gcc-toolchain` recipe + mirror the artifact** (phase-1: manylinux gcc-toolset-14 extract, pinned by image digest). Validate with `cvcpkg validate`.
2. **Land the `env-linux.sh` edits behind the `CVC_NATIVE_TOOLCHAIN_DIR` guard.** With the var unset, behavior is byte-identical to today (`CC=gcc`), so nothing breaks before the toolchain is wired in — this is the bootstrap fallback.
3. **Wire A1** (harness sets `CVC_NATIVE_TOOLCHAIN_DIR` when `gcc-toolchain` is in the build prefix). Add `gcc-toolchain` as a build/host dependency for native recipes (or global default).
4. **Rebuild the native catalog once** on the pinned toolchain, bumping each package's `cvc_revision` (not upstream version). Publish the coherent set.
5. **ABI-compat window with existing GCC-11 packages:** GCC 14's `libstdc++` is **backward-compatible** with GCC-11-built binaries — libstdc++ maintains ABI back-compat, so a GCC-14 `libstdc++.so.6` (`GLIBCXX_3.4.x`) can load consumers built against the older one. The reverse is not guaranteed. Therefore the safe sequence is: **ship the GCC-14 `libstdc++`/`libgcc_s` in the runtime closure first (or concurrently), then rebuild consumers against GCC 14.** During the transition, mixed catalogs work **only if the newest (GCC-14) libstdc++ is the one loaded at runtime** — enforce this by having every package RPATH to the pinned toolchain's runtime libs (§2, Report 1 §6.8). Do the rebuild as one atomic catalog revision to minimize the window where a GCC-11 libstdc++ could be the resolved one. glibc floor moves from whatever the old build boxes had to a controlled 2.28 — verify no published package currently requires a symbol newer than 2.28 (it shouldn't, if it was manylinux-compatible).

---

## 5. macOS / Windows — short plan (Linux first)

**macOS arm64.** The C++ runtime is Apple's OS-shipped `libc++` (stable ABI) — far less of a landmine than Linux libstdc++, so *don't bundle* a C++ runtime. Pin two knobs (Report 2 §3):
- Compiler: pinned **LLVM 18.1.8 arm64 clang** (`clang+llvm-18.1.8-arm64-apple-macos11.tar.xz`, relocatable, SHA-pinnable via GitHub release `.sig`), replacing "whatever Apple clang the dev's Xcode has."
- Floor: **`MACOSX_DEPLOYMENT_TARGET=11.0`** + `-isysroot` against the system CLT SDK. `env-macos.sh:23-24` (`clang`/`clang++` defaults) gets the same `CVC_NATIVE_TOOLCHAIN_DIR`-guarded redirection; the existing `MACOSX_DEPLOYMENT_TARGET:=13.0` default (revisit down to 11.0) already provides part of the hermeticity. Pinning the SDK itself (re-hosting `MacOSX*.sdk`) is licensing-touchy — later hardening step.

**Windows — out of scope for v1** (Report 1 §2, §6.7; Report 2 §3). `env-windows.ps1` *forces* MSVC (`$env:CC='cl'`, auto-imports vcvars via `vswhere`, lines 41-137) — a fundamentally different pin mechanism than `CC`/`CXX` redirection. Windows hermeticity = pin a **MSVC toolset version** (e.g. `-vcvars_ver=14.38`) + pinned **Windows SDK**, matching the MSVC ABI most Windows consumers expect. Keep pinned **mingw-w64** (already a recipe: `recipes/mingw-w64/`, WinLibs-style, bundles its own libstdc++, relocatable) as the option if/when Windows wants the same "our-GCC-our-ABI" hermeticity — but that requires a self-consistently mingw-built Windows catalog. Document the deferral explicitly, as GHC Phase 7.5 scoped BSD out.

---

## 6. OPEN QUESTIONS / RISKS for the user

1. **Acquisition path for v1:** ship the **manylinux gcc-toolset-14 extract** (fast, zero bootstrap, but no clean upstream tarball URL — pinned by image digest + rpm versions) or go straight to **crosstool-NG-built** (full input provenance, ~30–60 min one-time CI bootstrap)? Recommend extract-first, migrate later; both give the identical observable contract.
2. **glibc floor 2.28 vs 2.34:** 2.28 = `manylinux_2_28` (NumPy 2.3+). If any consumer needs a newer glibc feature or we want to track `manylinux_2_34` (AlmaLinux 9), the floor rises and old-distro compat drops. Confirm 2.28 is the intended target.
3. **`SOURCE_DATE_EPOCH` provenance:** derive per-recipe from source commit/tarball mtime (needs a new `CVC_SOURCE_EPOCH` plumbed by the harness), or accept a global `0`? A global `0` is reproducible but flattens all timestamps.
4. **Activation mechanism A1 vs A2** (§3): small `_build_env` change to set `CVC_NATIVE_TOOLCHAIN_DIR` when the toolchain is present (A1, recommended) vs. deriving it in shell from `CVC_BUILD_PREFIX` sentinel (A2). A1 is cleaner but touches `builder.py`.
5. **ABI transition atomicity:** can we rebuild-and-republish the entire native catalog as one revision, or will there be a live window with mixed GCC-11/GCC-14 `libstdc++`? The RPATH-to-pinned-runtime mitigation (§4) must be verified on the actual published bundles before flipping.
6. **`build_type_independent` is advisory** — if we want the catalog to actually dedup the toolchain to one flavor, that dedup wiring (split-distribution catalog key) is separate, unimplemented work.
7. **macOS deployment-target bump** from the current 13.0 default down to 11.0 — confirm no macOS consumer relies on a 13.0-only symbol.
8. **Consider lifting the `target==host` short-circuit** (`builder.py:1921-1922`) as a future consolidation (design B) so native toolchains auto-merge like cross ones — needs a bootstrap-compiler guard so the toolchain doesn't try to compile itself with itself. Not required for v1.

**Key files to edit:** `recipes/gcc-toolchain/{recipe.yaml,build.sh}` (new); `recipes/_common/env-linux.sh:20-21` (+ append repro block); `recipes/_common/env-{freebsd,netbsd,openbsd}.sh` (same redirection); `recipes/_common/env-macos.sh:23-24` (macOS phase); `src/cvcpkg/builder.py` `_build_env` (set `CVC_NATIVE_TOOLCHAIN_DIR`, design A1).