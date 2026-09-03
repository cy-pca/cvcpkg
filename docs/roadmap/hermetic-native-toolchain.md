# Hermetic Native C/C++ Toolchain — "Our GCC, Our ABI"

**Status:** proposed · **Motivation:** compiler drift breaks binary hermeticity across build environments.

## The problem

cvcpkg recipes build C/C++ from source using **whatever compiler the build box happens to have**:

```sh
# recipes/_common/env-linux.sh
export CC="${CC:-gcc}"
export CXX="${CXX:-g++}"
```

So a package's binaries depend on the machine that built them. Concretely, this bit the VTK-Python work (2026-07-22):

- The published `vtk` (`9.5.0+cvc.1`) was built on the fleet (Ubuntu 22.04 → **GCC 11.4.0**).
- A local wrapper build on a dev box (Ubuntu 24.04 → **GCC 13.3.0**) produced `vtk-python` wrappers linking a GCC-13 `libstdc++`.
- Mixing them (GCC-13 wrappers ↔ GCC-11 `libvtk*.so`) is the two-toolchain hazard: the VTK public C++ ABI is identical across GCC 11/13 (verified: 0 real VTK-API symbol diffs), so it *usually* loads via libstdc++ forward-compat — but it is **not** a clean, reproducible build, and it silently defeats hermeticity.

More generally: **"same source + same flags" does NOT yield the same binary if the compiler can differ.** The `vtk-python` recipe's whole premise (its wrappers are ABI-compatible with the `vtk` package) rests on the two builds using the same toolchain — which nothing currently guarantees. There is no `SOURCE_DATE_EPOCH` either, so builds aren't reproducible even on one box.

## The fix — pin the toolchain, like we already do everywhere else

cvcpkg already ships **hermetic toolchains** for the non-native ecosystems:

- **WASM** → bundled Emscripten SDK (Clang→wasm), see the emsdk row of
  `new-dependencies.md`'s shipped ledger.
- **Haskell** → our own GHC (Phase 7.5, "Our GHC, Our ABI").
- **Python** → hermetic interpreters (`python311/312/313`, Phase 7).

The **native C/C++ compiler is the one ecosystem still floating to the system default.** Close the gap:

1. **Ship a pinned native toolchain as a cvcpkg host-tool** — e.g. a `gcc-toolchain` (or `clang-toolchain`) recipe pinning a specific GCC/Clang + its `libstdc++`/`libc++`, one per platform. (A relocatable GCC or an LLVM release tarball, SHA256-pinned, mirrored like the other tools.)
2. **`env-linux.sh` (and macos/windows) point `CC`/`CXX` at it** instead of `${CC:-gcc}`, so *every* recipe compiles with the same compiler regardless of the build box (fleet or a dev laptop).
3. **Set `SOURCE_DATE_EPOCH` + `-ffile-prefix-map`** in the shared env so builds are reproducible/bit-identical, not merely ABI-compatible.

### Payoff
- **Binary hermeticity across environments:** a package built locally == the fleet's == another dev's. The vtk ↔ vtk-python ABI match becomes *structural*, not coincidental.
- **Local publishing becomes safe** — the reason a locally-built `vtk-python` couldn't be published against the fleet's `vtk` was purely the compiler mismatch this eliminates.
- **Reproducible builds** for auditing/caching (feeds the build-cache roadmap).
- Symmetry with the existing hermetic-toolchain phases (WASM/Haskell/Python) — the native compiler stops being the odd one out.

### Sequencing
Fits alongside Phase 1.5 (Release Engineering Readiness) / the build-cache work. The toolchain recipe + `env-*.sh` switch is the bulk; recipes need no per-recipe changes (they inherit `CC`/`CXX`). Roll out by rebuilding the native packages once on the pinned toolchain so the published catalog is single-toolchain.

## Implementation spec
See [`native-toolchain-spec.md`](native-toolchain-spec.md) — GCC 14.2 / glibc-2.28-floor decision, `gcc-toolchain` recipe shape, `env-linux.sh` edits, and the catalog-rebuild rollout.

## Related
- [`../build-cache.md`](../build-cache.md) — the build cache has shipped; reproducible builds feed it (identical chain hash ⇒ reusable artifact)
- The multi-python `vtk-python-cpXXX` recipes assume ABI-matched `vtk` — they are only truly hermetic once the native toolchain is pinned.
