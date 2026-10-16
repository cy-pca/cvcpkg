# Building `cryptography` 48.0.0 (cp312) on OpenBSD 7.7 x86_64 in cvcpkg

Investigated 2026-10-15/16 on the `openbsd-build-2` builder (OpenBSD 7.7 GENERIC.MP#625 amd64, 4 vCPU,
4 GB), in a scratch directory under `/usr/local`. Why OpenBSD has no `cryptography-cp312` (and therefore no
registry `cvcpkg` package) and what it would take; written for cvcpkg maintainers and for anyone
looking to contribute the missing pieces upstream.

## Summary

- **Nothing upstream blocks it. A from-source build of cryptography 48.0.0 for cp312 works on OpenBSD 7.7.**
  It built and passed an Ed25519/Fernet/x509 smoke test in three configurations:
  (a) ports rustc 1.86 + maturin 1.11.5 + base LibreSSL 4.1.0,
  (b) ports rustc 1.86 + maturin 1.11.5 + OpenSSL 3.4.1 via `OPENSSL_DIR`,
  (c) **exactly cvcpkg's pins**: rustc/cargo 1.90.0 + maturin 1.14.1 + OpenSSL 3.4.1.
  Each build produced `cryptography-48.0.0-cp312-abi3-openbsd_7_7_amd64.whl`.
- **The gap is in cvcpkg's `recipes/rust`, not in Rust, maturin or cryptography.** Upstream Rust ships no
  OpenBSD binaries (`x86_64-unknown-openbsd` is tier 3), and the recipe only consumes
  static.rust-lang.org. Every OpenBSD Rust toolchain comes from the OpenBSD ports tree, which uses
  semarie's bootstrap tarballs. **One of those tarballs (rustc 1.90.0, built 2025-09-18) runs on 7.7 and
  built maturin 1.14.1 and cryptography 48.0.0** with cvcpkg's exact pins.
- **The real limit of the 7.7 *package* rust is its version, 1.86.0, which is older than maturin 1.14.1's
  MSRV of 1.89.** This is an MSRV failure, not a platform failure. maturin 1.11.5 (MSRV 1.85) is the newest
  release that builds with rustc 1.86, and cryptography 48 accepts it (`maturin>=1.9.4,<2,!=1.12.0`).
- **Both claims in `VM-INVENTORY.md` (and `docs/known-issues.md`) are wrong or stale.**
  - "maturin's platform detection doesn't support OpenBSD" is wrong. maturin has full OpenBSD
    support and tagged the wheels `openbsd_7_7_amd64`. The "Unsupported platform: 312" error comes from
    **puccinialin**, maturin's helper that downloads a Rust toolchain through rustup. maturin only calls it when no `cargo` is on PATH,
    and it can't parse OpenBSD's `SOABI` (`cpython-312`).
  - The "segfault with Rust 1.86 due to compiler bugs" did not reproduce. maturin 1.11.5 and cryptography
    48 built cleanly with ports rustc 1.86, and no SIGSEGV appeared in any of the five cargo builds.
- **LibreSSL is not an obstacle.** cryptography 47+ needs LibreSSL ≥ 4.1 (OpenBSD 7.7 has 4.1.0), and
  openssl-sys 0.9.115 accepts LibreSSL 3.5–4.3. The OpenBSD port of cryptography carries **no patches**.

## Toolchain status

### Upstream Rust
| Item | Status | Evidence |
|---|---|---|
| `x86_64-unknown-openbsd` tier | **Tier 3** (std ✓, host ✓) | doc.rust-lang.org/nightly/rustc/platform-support.html. Every OpenBSD triple is tier 3. `x86_64-unknown-{freebsd,netbsd}` are tier 2 with host tools |
| Target maintainer | @semarie (also the maintainer of the OpenBSD `lang/rust` port) | platform-support/openbsd.md |
| Prebuilt dist | None. The docs say "Rust does not yet ship pre-compiled artifacts for these targets" | `curl -I .../rust-1.90.0-x86_64-unknown-openbsd.tar.gz` → **404**, `rust-std-…-openbsd` → **404**, the NetBSD equivalent → 200, and `channel-rust-stable.toml` has **0** mentions of `openbsd` |
| rustup | No OpenBSD host (it isn't in rustup's target list) | puccinialin's `rustup_targets` list has no openbsd entry |
| Structural reason | OpenBSD makes no ABI promise across releases ("all OS versions are *major*… could be binary incompatible"). libc gets a major bump every release (7.7 has `libc.so.100.3`, while the 1.90 bootstrap carries `libc.so.102.0`), so one tier-2 artifact can't serve every OpenBSD release | platform-support/openbsd.md, and `ls /usr/lib/libc.so.*` on the builder |

### OpenBSD ports/packages
| Release | `rust` | `maturin` | `py3-cffi` | `py3-cryptography` |
|---|---|---|---|---|
| 7.7 (this builder) | **1.86.0** (installed: `rustc 1.86.0 (05f9846f8 2025-03-31) … host: x86_64-unknown-openbsd, LLVM 19.1.7`) | 1.8.3 | 1.17.1 | 44.0.2 (installed) |
| 7.8 | 1.90.0 | 1.9.4 | 2.0.0 | 46.0.2 |
| 7.9 | 1.94.1 | 1.13.1 | 2.0.0p0 | 46.0.7 |
| -current | 1.99.1 (bootstrap BV-amd64 1.99.0) | 1.15.0 | — | 50.0.2 |

Source: `cdn.openbsd.org/pub/OpenBSD/<rel>/packages/amd64/` listings and `openbsd/ports` master
(`lang/rust/Makefile`, `devel/maturin/Makefile`, `security/py-cryptography/Makefile`).

How ports bootstraps Rust: `lang/rust` downloads a prebuilt `rustc-bootstrap-${MACHINE_ARCH}-${BV}.tar.lz`
from `https://github.com/semarie/openbsd-rustc-bootstrap/releases/download/openbsd/`. The port's
`bootstrap` target makes this tarball itself by packing rustc, rustdoc, cargo, rustlib and every shared
library they need, including libc, libc++, libcrypto, libssl, libcurl and libgit2.
There is a `native_bootstrap` flavor that uses the installed rust instead. `lang/rust` carries 14
local patches (for example `library/std/src/sys/paths/unix.rs`, `library/unwind`, `vendor/psm/src/arch/x86_64.s`,
`compiler/rustc_llvm/build.rs`).

The bootstrap release is one *rolling* GitHub release (tag `openbsd`). Old assets get pruned: on 2026-10-15
the amd64 assets ran from 1.89.0 to 1.99.0, and 1.86.0 was already gone. Each asset is about 94–99 MB.

### cvcpkg
- `recipes/rust` (1.90.0, cvc.2) is `source.type: prebuilt` from `static.rust-lang.org/dist`.
  It has linux/macos/windows/freebsd/netbsd columns and **no openbsd column**, and `build.sh` has no
  OpenBSD branch (`sha_for()` returns nothing, so the build exits 1).
- In the cryptography-cp312 build closure, **only `rust` and `maturin-cp312` lack openbsd.** Everything
  else already supports it: `python312` (openbsd ✓), `cffi-cp312` (openbsd ✓), `openssl` (openbsd ✓),
  `patchelf` (openbsd ✓), and `pycparser`/`setuptools`/`setuptools-rust`/`wheel` (`any`).
- The recipe comment says ports rust is "a system toolchain and therefore exactly the thing cvcpkg
  refuses to depend on". That is a policy choice, not a technical limit.

## What was tried + results

Environment: ports `python-3.12.11`, a fresh venv (no system site-packages), and setuptools 84.0.0,
setuptools-rust 1.13.0 and wheel 0.48.0 from PyPI. TMPDIR, CARGO_HOME and PIP_CACHE_DIR all pointed inside the probe dir.
Sdist sha256s matched the recipe pins (`5c3932f4…5920` cryptography, `9d6577a6…b3df` maturin).

| # | Build | Toolchain | Result |
|---|---|---|---|
| 1 | maturin **1.14.1** sdist (cvcpkg pin) | ports rustc/cargo **1.86.0** | **FAIL (MSRV)** in under 1 min. See output below |
| 2 | maturin **1.11.5** sdist | ports 1.86.0 | **OK**. `Finished release in 7m 54s`, gave `maturin-1.11.5-cp312-cp312-openbsd_7_7_amd64.whl` |
| 3 | cffi **2.0.0** sdist (`--no-binary cffi`) + pycparser | clang 16.0.6, ports libffi 3.4.7 | **OK** |
| 4 | cryptography **48.0.0** sdist, **base LibreSSL 4.1.0** (no `OPENSSL_DIR`) | ports 1.86.0 + maturin 1.11.5 | **OK**. `Finished release in 2m 26s`. `_rust.abi3.so` links `/usr/lib/libssl.so.59.1` and `libcrypto.so.56.0`. Smoke test printed `LibreSSL 4.1.0`, ed25519 ok, fernet ok, x509 ok |
| 5 | OpenSSL **3.4.1** (`./Configure BSD-x86_64 shared no-dynamic-engine no-legacy`, the same options as `recipes/openssl/build.sh` uses for openbsd) | clang/gmake | **OK** |
| 6 | cryptography 48.0.0, `OPENSSL_DIR=<openssl 3.4.1>` | ports 1.86.0 + maturin 1.11.5 | **OK**. `Finished in 2m 44s`. NEEDED `libssl.so.3`, `libcrypto.so.3`, `libc++abi.so.7.0`, `libc.so.100.3`. Printed `OpenSSL 3.4.1 11 Feb 2025`, ed25519 ok |
| 7 | semarie bootstrap `rustc-bootstrap-amd64-1.90.0.tar.lz` (sha256 `a81167c4…c398`, unpacked with lzip 1.25 built from source) | — | **Runs on 7.7** with `LD_LIBRARY_PATH=<boot>/lib`. Gave `rustc 1.90.0 (1159e78c4 2025-09-14) … host: x86_64-unknown-openbsd` and `cargo 1.90.0 (840b83a10 2025-07-30)`. Without the LD_LIBRARY_PATH, cargo fails with `can't load library 'libllhttp.so.0.0'` |
| 8 | maturin **1.14.1** sdist (cvcpkg pin) | **bootstrap 1.90.0** | **OK**. `Finished release in 13m 27s` (cold crate cache), gave `maturin-1.14.1-cp312-cp312-openbsd_7_7_amd64.whl`. The maturin binary links the system `libc.so.100.3`, not the libc bundled in the bootstrap |
| 9 | cryptography 48.0.0, OpenSSL 3.4.1 (**the full cvcpkg configuration**) | bootstrap 1.90.0 + maturin 1.14.1 | **OK**. `Finished in 3m 33s`, then `📦 Built wheel for abi3 Python ≥ 3.12 … cryptography-48.0.0-cp312-abi3-openbsd_7_7_amd64.whl`. Smoke test: `48.0.0 OpenSSL 3.4.1`, ed25519 ok |

Run #1 failure, verbatim:
```
error: rustc 1.86.0 is not supported by the following packages:
  cargo-platform@0.3.2 requires rustc 1.88
  maturin@1.14.1 requires rustc 1.89
  time@0.3.47 requires rustc 1.88.0
  time-core@0.1.8 requires rustc 1.88.0
  zip@8.5.1 requires rustc 1.88
error: `cargo build --manifest-path Cargo.toml ... --release -v --no-default-features --locked` failed with code 101
```

MSRVs of maturin releases (`rust-version` in each sdist's Cargo.toml):
1.9.4 and 1.9.6 → 1.74; 1.10.2 → 1.83; **1.11.5 → 1.85** (edition 2024); 1.12.6 and 1.13.1 → 1.88; 1.14.1 → 1.89.

Other observations:
- **Import warning with cvcpkg's OpenBSD OpenSSL.** Because that build uses `no-legacy`, cryptography warns
  on import: `Warning: OpenSSL 3's legacy provider failed to load. Legacy algorithms will not be available.`
  Setting `CRYPTOGRAPHY_OPENSSL_NO_LEGACY=1` silences it (checked in run #9). This affects any future openbsd column.
- The `pip install cryptography` failure that `docs/known-issues.md` quotes (`Unsupported platform: 312` /
  `Rust not found, installing into a temporary directory`) comes from puccinialin 0.1.14, which
  maturin's build backend pulls in **only when `cargo` is not on PATH**. `_target.get_triple()` splits
  `SOABI` and only knows the Linux, macOS and Windows forms. OpenBSD's SOABI is `cpython-312`, so the
  "platform" it extracts is `312`. Even if it parsed that correctly, rustup has no OpenBSD host. With
  ports rust on PATH, a plain `pip install cryptography` still fails today because build isolation
  picks the newest maturin (1.14.x), which needs rustc ≥ 1.89 (see run #1). This is the real cause behind the
  old note's "Cargo.lock pinning" bullet.
- The source of the "SIGSEGV in cc1plus or rustc" claim is unknown, and it did not reproduce. Note that `cc1plus` is
  GCC, which isn't involved here because base clang 16 is the system compiler. It might have been a resource limit
  (`datasize` is 4 GB for root on this box) or a full `/` when TMPDIR was left on root. **Unverified.**
- Disk: the unpacked bootstrap toolchain is 443 MB. The crates.io cache for maturin plus cryptography is about 1 GB. The whole probe
  peaked at 2.6 GB, all under `/usr/local`.
- How the OpenBSD standalone binary (`cvcpkg-openbsd-x86_64`) gets cryptography today:
  `.github/workflows/cvcpkg-standalone.yml` (build-bsd job) makes a
  `python3.12 -m venv --system-site-packages --without-pip` venv on the OpenBSD VM. PyInstaller then bundles the
  **OS package `py3-cryptography-44.0.2`**, which is installed on the builder, matching `pkg_info`. `docs/known-issues.md`
  documents `pkg_add py3-cryptography` for the BSD builders. That package is itself a from-source
  maturin/cargo build by ports, made with the release's own `rust` package and base LibreSSL.

## Root cause(s)

1. **cvcpkg policy plus upstream distribution.** `recipes/rust` only consumes static.rust-lang.org, which has
   nothing for OpenBSD because the target is tier 3. Every recipe with a `rust` build edge, including maturin-cp312,
   cryptography-cp3xx, pydantic-core and bcrypt, inherits the missing openbsd column through the
   closure check.
2. **Version skew on OpenBSD 7.7 specifically.** The 7.7 package rust (1.86.0) is below the MSRV of
   cvcpkg's pinned maturin 1.14.1 (1.89) and below the `rust` recipe pin (1.90.0). On 7.8 (rust 1.90.0)
   and 7.9 (1.94.1), the package rust already satisfies both pins (inferred from MSRVs, **not built there**).
3. **Not a cause:** maturin platform support, pyo3, openssl-sys, LibreSSL, `ring` (not in cryptography's
   dependency graph), or compiler segfaults. None of these failed.

## Paths forward for cvcpkg (concrete recipe options)

**A. Add an openbsd column to `recipes/rust` that stages semarie's bootstrap tarball** (recommended if cvcpkg's
"no system toolchain" rule must hold).
- `build.sh`: add an `OpenBSD)` case with `TRIPLE=x86_64-unknown-openbsd`. Download
  `rustc-bootstrap-amd64-1.90.0.tar.lz`, check it against a pinned sha256 (`a81167c4f89aacacf745813c92689e4b4c9d278127ff130bc4532cecdfa5c398`),
  decompress with lzip, and copy `bin/` and `lib/` (including `lib/rustlib`) into `CVC_INSTALL_DIR`.
- rustc and cargo need their bundled libs at runtime. Either install wrapper scripts that set
  `LD_LIBRARY_PATH=$prefix/lib/rust-bootstrap`, keeping the bundled libc, libssl, libcurl and so on out of the
  shared `lib/` so they can't shadow cvcpkg's openssl or curl, or set a RUNPATH. Run #7 showed that cargo won't start
  without them.
- Needs a `lzip` (or `tarlz`) host tool. cvcpkg could add a small `lzip` recipe (autotools-free, C++), or
  recompress the archive once and host it as a cvcpkg-mirrored artifact.
- **Risks.** (1) The rolling release prunes old assets, so cvcpkg must mirror the pinned tarball or the URL
  will rot. (2) The binary was built on -current *after* 7.7 and carries libc.so.102. It worked here, but it
  is not a supported combination: a later -current bootstrap could use syscalls the 7.7 kernel doesn't
  have. Pin per OpenBSD release and test. (3) Provenance is a third party, although it is the same input the
  OpenBSD ports build trusts.
- With this in place, `maturin-cp312` and `cryptography-cp312` only need `- platform: openbsd` added
  (`build.sh` already handles `OPENSSL_DIR` and the patchelf RUNPATH). Run #9 is exactly this configuration.

**B. Build rust from source in a cvcpkg recipe on OpenBSD.** This is the "pure" route, but it still needs a
bootstrap rustc (stage0), which means A's tarball or ports rust, plus OpenBSD's 14 `lang/rust` patches.
The build would take hours and tens of GB, and it doesn't remove the bootstrap trust question. Not recommended.

**C. Allow ports rust as a declared host tool, for openbsd only.** Make the openbsd column of `recipes/rust` a
thin shim that checks `rustc -V` is at least the required version and symlinks `/usr/local/bin/{rustc,cargo}`
into the prefix. This is the cheapest option, and it is exactly how ports builds py3-cryptography. On 7.7 it would
also need maturin-cp312 pinned to ≤ 1.11.5 for the openbsd column (an openbsd-only maturin pin, or a
separate `maturin-cp312` revision). The other option is to move the builders to 7.8 or 7.9, whose packaged rust meets 1.89/1.90.
It breaks the "no system toolchain" rule, and builders would need `pkg_add rust` (already true on openbsd-build-2).

**D. Status quo / interim.** Keep using `py3-cryptography` from packages for the cvcpkg tool itself (version 44.0.2 on 7.7).
That doesn't give cvcpkg a `cryptography-cp312` openbsd package, though.

Whichever route is taken, also: build openssl's openbsd column so cryptography doesn't warn about the legacy provider,
or export `CRYPTOGRAPHY_OPENSSL_NO_LEGACY=1` from the cryptography package's activation. And correct
`recipes/rust/recipe.yaml`, `recipes/cryptography-cp312/recipe.yaml`, `VM-INVENTORY.md` and
`docs/known-issues.md`, which currently blame upstream Rust and maturin.

## Community contribution opportunities (ranked by impact ÷ effort)

1. **puccinialin: handle BSD SOABIs and give a useful error.** High visibility, very low effort.
   `get_triple()` misparses `SOABI=cpython-312` as the platform `312`. On BSDs it should fall back to
   `sysconfig.get_platform()` (`openbsd-7.7-amd64`). For OpenBSD it should say plainly that rustup has no
   OpenBSD host and suggest `pkg_add rust`, instead of printing "Unsupported platform: 312".
   Every BSD user who runs `pip install cryptography` without cargo sees that confusing message.
   The same parse probably breaks FreeBSD and NetBSD too, even though their triples *are* in `rustup_targets`.
   That is inferred from the code, and **FreeBSD/NetBSD SOABIs weren't checked**. Upstream: github.com/konstin/puccinialin.
2. **maturin: per-OS arch names for OpenBSD platform tags.** Low effort, moderate impact (non-amd64 OpenBSD).
   The ports tree patches `src/target/mod.rs` so that `Arch::Riscv64 → "riscv64"`, because maturin uses FreeBSD's
   `MACHINE_CPUARCH` ("riscv") for every BSD. On OpenBSD that gives the wrong wheel platform tag. maturin `main`
   is still unfixed (line 157 `Arch::Riscv32 | Arch::Riscv64 => "riscv"`). An upstream fix would let ports
   drop the patch. Also check armv7 and powerpc64 on OpenBSD for the same mismatch (**unverified**).
   The port also regenerates `Cargo.lock` because the shipped lockfile doesn't resolve offline
   (`cargo-xwin = "^0.23.1"` was locked to an unpublished version). That's worth reporting to maturin's release process.
3. **cvcpkg itself: publish the working recipe (path A or C).** This would be the first reproducible, non-ports
   cryptography 48 build for OpenBSD. Moderate effort. The evidence above shows it works.
4. **Docs fixes upstream.** cryptography's `docs/installation.rst` has no OpenBSD section. A short
   "OpenBSD: `pkg_add rust`, then build against base LibreSSL ≥ 4.1, or use `pkg_add py3-cryptography`" note, plus
   a mention of the maturin-MSRV-vs-packaged-rust trap on older releases, would help. Low effort, modest impact.
5. **openssl-sys: keep the LibreSSL allow-list ahead of OpenBSD releases.** openssl-sys hard-fails on
   unknown LibreSSL versions ("compatible with … LibreSSL 3.5.0 through 4.3.x"). Each new LibreSSL (and so each
   new OpenBSD) needs a crate bump *before* release, or every Rust/OpenSSL consumer on OpenBSD breaks. Testing
   openssl-sys against LibreSSL snapshots is a recurring, low-effort, high-leverage contribution.
6. **Rust tier-2 for OpenBSD.** This is the "real" fix for static.rust-lang.org, but OpenBSD's lack of a
   cross-release ABI makes a single dist artifact unworkable. It would need per-release dist artifacts and CI capacity,
   which means an RFC-level discussion with @semarie and the Rust infra team. Highest impact, highest effort, unlikely soon.
   A more realistic step is to help @semarie make the bootstrap tarballs **non-pruned and versioned per OpenBSD release**,
   for example by tagging per release. That alone would make path A a stable dependency for cvcpkg and other
   third-party builders.

## References

- Rust platform support: https://doc.rust-lang.org/nightly/rustc/platform-support.html and
  https://doc.rust-lang.org/nightly/rustc/platform-support/openbsd.html
- 404 probe: https://static.rust-lang.org/dist/rust-1.90.0-x86_64-unknown-openbsd.tar.gz, and
  https://static.rust-lang.org/dist/channel-rust-stable.toml (no openbsd entries)
- OpenBSD ports: https://github.com/openbsd/ports/blob/master/lang/rust/Makefile,
  `lang/rust/patches/` (14 patches), `devel/maturin/{Makefile,patches/patch-src_target_mod_rs,patches/patch-Cargo_lock}`,
  `security/py-cryptography/Makefile` (no patches dir)
- OpenBSD package listings: https://cdn.openbsd.org/pub/OpenBSD/{7.7,7.8,7.9}/packages/amd64/
- Bootstrap tarballs: https://github.com/semarie/openbsd-rustc-bootstrap/releases/tag/openbsd
- cryptography 48.0.0: CHANGELOG (47.0.0 has "Dropped support for LibreSSL < 4.1", and OpenSSL ≥ 3.0 is required),
  `.github/workflows/ci.yml` (CI covers LibreSSL 4.1.2, 4.2.1 and 4.3.1), `docs/installation.rst`, and
  `src/_cffi_src/openssl/cryptography.py`
- openssl-sys 0.9.115 `build/main.rs` `version_error()` (LibreSSL 3.5.0–4.3.x)
- maturin 1.14.1 sdist: `src/target/mod.rs`, `src/target/platform_tag.rs` (OpenBSD handled),
  `maturin/__init__.py` and `maturin/bootstrap.py` (the puccinialin fallback when there's no cargo)
- puccinialin 0.1.14 `_target.py` (`get_triple`, `rustup_targets`)
- cvcpkg: `recipes/rust/{recipe.yaml,build.sh}`,
  `recipes/maturin-cp312/recipe.yaml`, `recipes/cryptography-cp312/recipe.yaml`, `recipes/openssl/build.sh`
  (openbsd: `BSD-x86_64`, `no-dynamic-engine no-legacy`), `.github/workflows/cvcpkg-standalone.yml` (build-bsd,
  `--system-site-packages`), `scripts/build-bsd-standalone.sh`, `docs/known-issues.md`
- vm-provisioning `bsd/VM-INVENTORY.md`, the "Python / cvcpkg Dependencies" section (claims refuted above)
