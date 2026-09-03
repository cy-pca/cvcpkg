# Application packaging & desktop delivery (`cvcpkg bake`)

> Extracted from [CVCPKG-ROADMAP.md](CVCPKG-ROADMAP.md) **Phase 19 —
> Application Packaging & Desktop Delivery** during the 2026-08 docs
> consolidation. This file holds the full design and feasibility research;
> the roadmap keeps the phase entry.

**Status: not started.** No `bake` or installer command exists in
`src/cvcpkg/cli/`, and the recipe schema
(`src/cvcpkg/schemas/recipe-schema.yaml`) has no entry-point or
desktop-asset fields yet. Roadmap position: required before the v2.0.0
PyPI release. The feasibility research below was done **2026-07-18** and
is preserved as recorded; two details have moved since it was written:

- **`recipes/zstd/build-cosmo.sh` now exists** (the research below said
  zstd "would need" one). The full squashfs compression-codec set —
  zlib, xz, lz4, bzip2, and now zstd — builds for the cosmo platform.
  The `squashfs-tools` recipe itself is still net-new.
- **Release sha256 checksums now ship** (#514): the standalone-release
  pipeline (`.github/workflows/cvcpkg-standalone.yml`) generates a
  per-asset `.sha256` file for every GitHub Release artifact, and the
  `install.sh` / `install.ps1` quick-install scripts verify against them
  before running anything they downloaded. The checksum half of the
  Linux code-signing item below is done; detached GPG signatures and
  cosign attestations are not.

Related reading:

- [static-single-binary-python.md](static-single-binary-python.md) — the
  embedded-CPython variant of the same single-binary idea (a cosmo APE or
  `.wasm` carrying a static CPython + vtk-python payload). It builds
  directly on `cvcpkg bake` as its packaging mechanism, and its inittab /
  zipos / MEMFS machinery is shared substrate with the cosmo bake below.
- Phase references (Phase 8, 11, 15, 16, 17) point into
  [CVCPKG-ROADMAP.md](CVCPKG-ROADMAP.md).

## Goal

cvcpkg prefixes already carry applications, not just libraries. This
design lets recipes describe the application surface (entry points,
icons, docs, media) and turns a finished install prefix into a native
installer per platform — or into a single self-mounting binary.

## Recipes describe applications

- [ ] **CLI entry points in recipes** — applications that have CLI entry
      points specify them in their recipes (the AppImage/installer work
      below consumes them).
- [ ] **Desktop assets in recipes** — recipes can specify desktop icons,
      help documentation, images, video, and any other media as part of
      the recipe, installed with the package (declared via Phase 17's
      artifact schema).
- [ ] **Desktop integration** — optionally edit the user's desktop to add
      a desktop icon / start-menu launcher / program-files entry, etc.,
      for an installed application in the prefix (opt-in at install time;
      cleanly reversible).

## Installers from an install prefix

- [ ] **Windows** — a command to easily make an **exe or MSI installer**
      from an install prefix, using info from the manifest, README, and
      other cvcpkg metadata in the prefix (Phase 16's provenance records
      supply the metadata).
- [ ] **Linux** — a command to easily make an **AppImage** containing the
      contents of an install prefix, using an entry point specified in
      the application's recipe.
- [ ] **macOS** — a command to easily make a **dmg installer** from an
      install prefix.

## `cvcpkg bake` — self-mounting prefix binaries (feasibility)

**`cvcpkg bake <prefix>`** packages an install prefix as a **single
binary deliverable with a defined entry point**: executing the bake
launches the user into that entry point (an application entry point from
the recipe, or a shell by default) with the **entire install prefix
mounted and available** — as a **user-mutable volume that unmounts when
the main shell (or entry point) exits**.

### Feasibility verdict

**Researched 2026-07-18: no Docker required — on any platform.** On
Linux everything needed is a plain unprivileged process using kernel
features (user + mount namespaces, FUSE-in-userns since 4.18,
unprivileged overlayfs since 5.11); Apptainer and NVIDIA enroot ship
exactly this UX today with no daemon and no setuid. On macOS and
Windows, Docker is a Linux VM and could not even host a prefix of native
binaries — the native mechanisms below are the only real options. A
container engine adds machinery without adding capability.

### Per-platform mechanism ladder

Best rung first, detected at runtime:

- **Linux** — launcher stub (static musl, embedded squashfuse/libfuse3 +
  zstd) with the prefix appended as a squashfs image (the AppImage
  type-2 runtime layout). `unshare(CLONE_NEWUSER|CLONE_NEWNS)` →
  squashfuse mounts the image as `lowerdir` → kernel **overlayfs upper
  layer** for mutability → exec the entry point with the prefix
  activated. Teardown is a **kernel invariant**: when the last process
  in the mount namespace exits — even on SIGKILL — the kernel destroys
  the namespace and every mount in it; no cleanup code runs at all.
  Fallback rungs: fuse-overlayfs (kernels 4.18–5.10), plain FUSE mount
  with `-o auto_unmount` (no userns), proot, and finally makeself-style
  extract-and-run (no kernel features at all — the nix-portable-style
  capability ladder).
- **macOS** — launcher + appended read-only dmg;
  `hdiutil attach -nobrowse -mountpoint … -shadow <file>` gives a
  **natively copy-on-write, user-mutable volume** with no kext, no
  admin, no macFUSE (rejected: kext/Reduced-Security friction on Apple
  Silicon). Bonus finding: `hdiutil attach` has a documented
  **`-section`** option (0-based 512-byte sectors) which, combined with
  `-imagekey diskimage-class=CRawDiskImage`, may attach the dmg payload
  **in place at its byte offset inside the bake binary** with no carve
  step — validate per macOS release in CI, and keep carve-to-cache as
  the fallback (commit needs the standalone base image anyway). Mounts
  outlive the process, so the launcher needs a watchdog
  `hdiutil detach` plus a stale-attachment sweep on start.
- **Windows** — **read-only ISO mount + scratch directory** (validated:
  standard users can `Mount-DiskImage` ISOs with no admin since
  Windows 8; always read-only; `-StorageType ISO` lifts the `.iso`
  extension requirement). The payload must be a real local file, so the
  bake carves its ISO out to a content-addressed cache once and reuses
  it (clear the sparse attribute before mounting — sparse ISOs fail
  with `0xc03a0005`; never mount from a UNC path). Layering without
  ProjFS is **additive shadowing**, not a true union: PATH-order
  layering (scratch dirs precede mount dirs), env-var redirection of
  writable app state into scratch, shell-shim copy-up on first write,
  and NTFS junctions (no-admin) to graft scratch subtrees — deletions
  of baked files are recorded as tombstones in the bake state, not the
  filesystem. SFX-extract remains the fallback (hardened environments
  can block ISO mounting via policy); ProjFS / WinFsp stay opt-in power
  modes where pre-enabled. A 4 GB PE ceiling applies to the bake binary
  on Windows — Windows will not load executables ≥ 4 GB (llamafile hit
  exactly this), so oversized payloads must ship as sidecar volumes.

### Persistent bake filesystem

**Yes, on all three platforms.** Model every bake as an **immutable
content-addressed base** plus a **named mutable state layer**, with the
same verbs everywhere: `bake status`, `bake reset` (drop the layer),
`bake commit` (fold the layer into a *new* immutable base with a new
digest), `bake states` (multiple named layers over one shared base —
per-project scratch spaces):

- *Linux* — reuse a persistent overlayfs `upperdir`/`workdir` across
  runs (same-filesystem pair, one overlay mount at a time per pair;
  mount with `userxattr` and `index/metacopy/redirect_dir` off so the
  upper stays portable plain-files-plus-whiteouts and survives base
  updates as path-based merging). Commit = mksquashfs of the merged
  view.
- *macOS* — reuse the shadow file (documented behavior: re-attach with
  the same `-shadow` and prior writes reappear). The shadow is
  block-level CoW **tied to the exact base image** — key it by base
  digest and invalidate on base update; it grows monotonically until
  merged. Commit = `hdiutil convert -shadow` → new base dmg (the
  native flow).
- *Windows* — the scratch dir is a plain NTFS directory: persistence is
  free. Commit = rebuild the ISO from mount + scratch + tombstones.

### One payload format for all platforms? No

ISO9660 was evaluated and rejected as the universal payload: Windows
CDFS ignores Rock Ridge (POSIX modes and symlinks are lost, Joliet caps
name components at 64 chars) and on Linux kernel iso9660 is a block
filesystem (`FS_REQUIRES_DEV`, no `FS_USERNS_MOUNT`) that cannot be
mounted in an unprivileged userns — the FUSE ISO implementations are
unmaintained (fuseiso: last upstream release 2007). Baking therefore
uses the native payload per OS — squashfs (Linux), dmg (macOS), ISO
(Windows) — or one zip payload in extraction mode.

### Cosmo bake — one APE deliverable for every platform

**Feasible, with sharp edges.** One cosmocc-built fat APE
(x86_64+aarch64) runs the same file on Linux, macOS, Windows 8+, and the
three BSDs; llamafile proves multi-GB payload-carrying APEs in the wild
(and its zipalign trick — uncompressed page-aligned zip members mmap'd
straight from the executable — avoids extraction for big blobs). Cosmo
libc provides fork/exec on all six OSes (including Windows) for driving
host tools (`hdiutil`, PowerShell `Mount-DiskImage`, fusermount) and
real `mount()`/raw-syscall access on Linux/BSD/XNU for the namespace
path. The pragmatic ladder: default = carve/extract the payload to a
content-addressed cache and exec the entry point (the APE loader itself
already does exactly this dd-to-`$TMPDIR` dance); upgrade rungs = Linux
squashfuse/userns+overlay, macOS hdiutil, Windows ISO mount. Build with
**bundled ape loaders** (on Apple Silicon a loader is compiled from
embedded source on first run — requires Xcode CLT — and downloaded bakes
face the standard Gatekeeper quarantine dance; never rely on first-run
`dd` self-assimilation, which mutates the deliverable).

Phase 8/11's `cvpkg` APE scoping (#513) grounds the sharp edges for the
cvcpkg-shaped payload specifically: cosmo APEs are fully static, so
`dlopen()` inside an embedded interpreter delegates to the *host's*
dlopen and a standard pip wheel's `.so` cannot load at all — every C
extension must be compiled **in** at cosmo-CPython build time (the
PyOxidizer model, not the wheel model). A client-only `cvpkg` has a
clean import boundary (fastapi/uvicorn/sqlalchemy/pydantic/greenlet are
server-only), but `cryptography` (Rust + CFFI) is an equivalent
client-side blocker, and `recipes/python312/build-cosmo.sh` stops at an
unlinked static archive — no apelink/zipos step or launcher `main()`
exists in-repo yet. See Phase 8 in
[CVCPKG-ROADMAP.md](CVCPKG-ROADMAP.md) and
[static-single-binary-python.md](static-single-binary-python.md) for the
full analysis.

### In-binary persistent data store (cosmo bake)

Options, worst to best:

1. **Live self-modifying zip (redbean precedent).** redbean's
   `StoreAsset()` appends a new member + rewritten central directory +
   EOCD to its *own executable* under an fcntl write-lock. Proof it
   works — but it is officially proof-of-concept: Linux/XNU/FreeBSD
   only, **impossible on Windows while running** (the OS write-locks a
   running exe), append-only growth until offline compaction, and not
   crash-atomic as implemented.
2. **Reserved uncompressed zip member as a raw block region + SQLite
   custom VFS** pwriting into the bake's own byte range (EOCD never
   moves, so the zip stays valid; the member's CRC goes stale by
   design — cf. SQLite's official `appendvfs`). No known prior art does
   SQLite-into-own-binary; same self-write platform limits as (1).
3. **EOCD-last append journal** — checksummed records appended after
   the payload, then a fresh central directory + EOCD (fsync payload
   *before* the EOCD append makes the trailing EOCD the atomic commit
   point; a torn tail is detected and truncated at next start). The
   most robust *self-write* design; still subject to the Windows lock
   and 4 GB ceiling.
4. **Sidecar store + explicit `bake commit` (recommended default).**
   The bake file stays an immutable, hash-stable artifact; mutable
   state lives in the content-addressed cache (or beside the binary
   when writable) as plain files or SQLite. An explicit `bake commit`
   rewrites the binary offline — zip-append or full rewrite + atomic
   rename; on Windows the rename-to-`.old` dance (rclone-style) or
   apply-on-next-run. TiddlyWiki's single-file self-rewrite is the UX
   precedent: live writes go to a store, "saving the file" is a
   deliberate act. This is the only option that survives Windows exe
   locking, ro/noexec media, AV heuristics against self-writing
   executables, concurrent instances, and macOS signing.

### Feasibility work items

- [ ] **Prototype the Linux bake** (userns + squashfuse + overlayfs;
      prove the mount-namespace auto-teardown, persistent upperdir
      reuse, and the fallback ladder).
- [ ] **Prototype the macOS bake** (embedded dmg + `-shadow`
      copy-on-write; try in-place `-section` attach vs carve-to-cache;
      watchdog detach + stale sweep; shadow keyed by base digest).
- [ ] **Prototype the Windows bake** (carve ISO → no-admin read-only
      mount + persistent scratch dir + PATH/junction layering +
      tombstones; SFX-extract fallback; janitor for crashed sessions).
- [ ] **Prototype the cosmo bake** (fat APE stub + per-OS ladder;
      bundled loaders; fleet smoke test like Phase 8's cvpkg).
- [ ] **Design the persistent-state model** (`bake status` / `reset` /
      `commit` / `states`; sidecar store as the default, EOCD-last
      append journal as the opt-in in-binary mode).
- [ ] **Entry points** — the baked entry point comes from the
      application recipe's CLI entry point (see "Recipes describe
      applications" above); default entry point is the user's shell
      with the prefix activated.
- [ ] **`squashfs-tools` recipe** — net-new; needed to build bake
      payloads from prefixes on the fleet. Payload compression is
      covered: zlib/xz/lz4/bzip2 recipes already build for the cosmo
      platform, and zstd gained its `build-cosmo.sh` after this
      research was written (the original text listed it as missing).

### Prior art and composition

Prior art to steal from: **Apptainer** (`apptainer shell image.sif` —
the exact UX, rootless since 1.1.0, `--writable-tmpfs`/`--overlay`),
**AppImage type2-runtime** (static stub + appended-squashfs layout,
`--appimage-extract-and-run` fallback), **enroot** (minimal
namespaces-only philosophy), **nix-portable** (runtime capability
ladder), **makeself** (universal floor), **llamafile** (multi-GB APE
payloads, zipalign), **redbean** (self-modifying zip, embedded SQLite),
**TiddlyWiki** (single-file commit UX).

Composes with: the AppImage installer item above (same payload tech),
Phase 8's `cvpkg` APE (the portable stub — a cosmo bake is `cvpkg` +
payload), Phase 15's activation semantics (the bake shell is
`cvcpkg activate` applied to a mounted root) and prefix registry,
Phase 16's provenance (catalog + recipes travel *inside* the bake — and
its private-org warning applies to baking one), and Phase 15's air-gap
story (a bake is its logical endpoint: one file, no network, no
unpack).

## Code signing — official CyberPC Angel, LLC binaries

Every installer and bake above should eventually ship **signed as
CyberPC Angel, LLC**, so our builds are verifiably official. Researched
2026-07-18 — the landscape moved recently, and two common assumptions
need correcting up front:

> **You don't buy a "Microsoft license" or an "Apple license" for
> this.** Windows Authenticode certificates come from commercial CAs
> (DigiCert, Sectigo, SSL.com, GlobalSign…), not Microsoft — though
> Microsoft *now sells a signing service* (below) that is the best fit
> for us. Apple's is a **$99/yr Developer Program membership** plus a
> Developer ID certificate, not a per-product license.
>
> **EV certificates no longer buy SmartScreen reputation.** Microsoft
> removed the EV fast-track in 2024 and its docs now say paying an EV
> premium "solely to avoid SmartScreen warnings is no longer
> justified" — OV and EV are treated identically, and reputation
> accrues organically from clean-install volume for both. Ignore any
> vendor page still claiming "instant SmartScreen bypass"; do not buy
> EV.

- [ ] **Windows — Azure Artifact Signing (~$120/yr), signed from Linux
      CI.** Microsoft's signing service (GA Jan 2026, $9.99/mo Basic,
      5,000 signatures/mo): a US LLC qualifies, the old 3-year org-age
      rule is gone, no USB token or HSM to manage. Certs are 72-hour
      short-lived, so **RFC 3161 timestamping is mandatory**, and
      **`jsign`** is the only client that reaches it from Linux — which
      suits the builder fleet. Fallback path: SSL.com OV + eSigner
      (~$309/yr). Context on why cloud signing: since June 2023 all
      code-signing keys must live in FIPS-140-2-L2 hardware (the
      downloadable `.pfx` is dead), and since March 2026 certs max out
      at ~15 months — cloud signing sidesteps the token-reship
      treadmill.
- [ ] **macOS — Apple Developer Program (org) + notarization.**
      Enrollment needs a **D-U-N-S number** (free, ~week) and the real
      legal entity; then a **Developer ID Application** cert (plus
      Developer ID *Installer* for `.pkg`). Pipeline: `codesign
      --options runtime --timestamp` → `notarytool` submit (`altool` is
      dead) → `stapler staple`. **Notarization is effectively mandatory
      now** — macOS 15 removed the Control-click bypass, leaving
      unnotarized apps behind an admin-password wall. Signing/stapling
      needs a macOS runner (the fleet's mac builders); `rcodesign` is a
      viable Linux-native secondary, not the primary. For dmg:
      sign+notarize+**staple the app first**, then
      build/sign/notarize/staple the dmg. Universal binaries: `lipo`
      first, sign the fat binary.
- [ ] **Linux — GPG + checksums; cosign for provenance.** No OS trust
      authority exists to pay; ship detached GPG signatures + sha256
      checksums on release artifacts, sign apt/rpm repos if we publish
      them, and add **sigstore/cosign** attestations in CI for
      supply-chain provenance (valuable to automated consumers,
      invisible to desktop users; AppImage's own signature field is
      effectively decorative). Composes with cvcpkg's existing Ed25519
      package signing. *Update: the checksum half landed —
      per-asset `.sha256` files ship with every standalone release and
      the quick-install scripts verify them (#514); GPG signatures and
      cosign attestations remain open.*
- [ ] **Order of operations** — Windows first (worst unsigned UX,
      cheapest fix, reputation takes weeks to accrue so start early),
      macOS second (longer enrollment lead time), Linux
      continuous/cheap. Realistic floor: **~$220/yr** in program fees
      plus macOS CI capacity.
- [ ] **Pitfalls to design in from day one** — timestamp *everything*
      (an untimestamped signature dies with its cert, retroactively, on
      every user's disk); expect a SmartScreen **reputation reset on
      any cert change** (renew early, overlap, keep one consistent
      signing identity); make notarization CI steps retryable (no SLA).
