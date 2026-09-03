# cvcpkg roadmap

> A cross-platform, language-agnostic binary package archive
> for the scientific computing community.

cvcpkg builds pinned, reproducible binary packages from ~750 YAML recipes and
serves them from a FastAPI archive server (PostgreSQL or SQLite) with
organizations, federation, and a tamper-evident audit chain. Builds run on a
self-hosted fleet covering 12 canonical platform targets — Linux, macOS,
Windows, the BSDs, Haiku, the `wasm32-emscripten`/`wasi`/cosmo cross targets,
and the noarch `any` pseudo-platform. The Python story is a full
per-interpreter `-cpXXX` column matrix
(cp311/cp312/cp313/cp313t, including the free-threaded channel). One CLI
(`cvcpkg`) drives recipes, builds, publishing, installs, and the server.

**Consolidated 2026-08-21.** This file was rewritten from the 4,083-line
historical roadmap after a 36-section audit against the code (2026-08-20). The
full pre-consolidation text — per-phase design discussions, worked examples,
research notes, and the license-choice rationale — is preserved in git history
as this file's previous version, and the still-active designs were extracted
into the documents indexed under [Design docs](#design-docs) below.

**Phase numbers are stable identifiers.** Code comments and docstrings cite
"roadmap Phase N"; numbers are never reused or renumbered, and closed phases
keep their rows.

---

## Status snapshot (2026-08-21)

| Phase | Title | Status | What's left |
|---|---|---|---|
| 1 | Foundation | Complete (v2.0.0) | Nothing structural; a couple of doc leftovers |
| 1.5 | Release engineering readiness | Mostly done | Hermetic native toolchain; recipe-coverage CI gate; signature-default decision |
| 2 | Analytics & telemetry | Mostly done | Deferred analytics only (geo-IP, install outcomes, per-version breakdown) |
| 3 | Admin dashboard | Mostly done | Release creation/promotion follow-up; audit/token UI polish |
| 4 | Multi-language & ecosystem expansion | Partial | cpkg recipe; compat-layer keep-or-drop; language rows re-homed to 7.5/7.6/11 |
| 5 | Federation & scaling | Partial | Named volumes, org-scoped builder admin, storage migration, read replicas |
| 6 | Community & governance | Partial | Ownership model, fork-PR CI, advisories, quality-tier decision |
| 7 | Python ecosystem integration | Mostly done | scipy/mpi4py columns, CUDA-math C++ recipes, release-manifest freeze |
| 7.5 | Haskell ecosystem integration | Not started | Everything (plan written; re-verify GHC/Stackage facts at kickoff) |
| 7.6 | Go ecosystem integration | Not started | Everything (design in [hermetic-container-runtime.md](hermetic-container-runtime.md)) |
| 8 | Self-hosting & universal bootstrap (`cvpkg`) | Partial | Self-install recipe, cosmo APE, `cvcpkg-sc`; standalones + quick-install shipped |
| 9 | Fleet & platform expansion | In progress | Haiku fleet wiring, qemu stack, image recipes, deployed-commit legibility |
| 10 | Peer providers & hardware-aware concretization | Partial | Contracts, ISA/hardware profiles, providers CLI, BLAS family (minimal slice shipped) |
| 11 | Self-hosting toolchains + `cvpkg` | Partial | gcc-toolchain, binutils/assemblers, gfortran, the `cvpkg` recipe |
| 12 | Federation hardening | Mostly done | Mirror-policy admin surface; release-tag eviction exemption |
| 13 | Identity & access (OIDC) | Mostly done | JWKS verification, OIDC-authenticated publish, IdP-group→org sync |
| 14 | Source recipes (file-artifact packages) | Complete | First production adopter; #496 catalog cleanup (operational) |
| 15 | CLI UX & the recipe-first workflow | Partial | `~/.cvcpkg` home, prefix DBs + `uninstall`, air-gap export ([cli-ux-recipe-first.md](cli-ux-recipe-first.md)) |
| 16 | Prefix provenance & server seeding | Not started | Everything |
| 17 | Recipe archives: declared artifacts & package-page UX | Partial | Governance half: quotas, size caps, StorageBackend routing (UX half shipped, #503) |
| 18 | Server backups, scheduled jobs & quota governance | Not started | Everything |
| 19 | Application packaging & desktop delivery | Not started | Everything; research done ([application-packaging.md](application-packaging.md)) |
| 20 | First-party & featured software recipes | Partial | All featured-app recipes; `redistributable` flag (wheel closure + sdl3 shipped) |
| 21 | Package visibility: hidden packages | Not started | Everything (the yanked-flag work pre-built much of the machinery) |
| 22 | Federation topology: nested authority & introspection | Partial | N-tier resolution, topology model, network stats (divergence warnings shipped) |
| 23 | Build & configuration-management system | Not started | Everything ([config-management.md](config-management.md)) |
| 24 | Live updates, activity feed & build transparency | Not started | Everything |
| 25 | PyPI release (final phase) | Open | Scoped by #515, not executed — see [Path to PyPI](#path-to-pypi-release-blockers) |

---

## Path to PyPI (release blockers)

> **Proposal, not decided policy.** The original release rule — publish to
> PyPI only after *every* roadmap phase closes — is under review. The list
> below is the proposed narrower gate: the minimal, ordered set of steps to a
> first publish, with phases 15–24 moving to post-release work. Until that
> decision is made, Phase 25 formally remains the final phase.

The publish workflow itself (test → build → live smoke → double-gated
publish) is ready and untouched; no PyPI release has ever occurred, so the
"rename before first publish" ordering constraint is intact and still
executable.

1. **Repo transfer** `transfix/libcvc-deps` → `cy-pca/cvcpkg`, per the #515
   scoping note (the org exists; secrets transfer automatically). In the
   *same sitting*: push the four one-line `uses:` fixes to the downstream
   repos (`transfix/libcvc`, `transfix/TexMol`, `transfix/volrover`,
   `CVC-Lab/GRL-SNAM` — `uses:` references do not follow transfer
   redirects), then verify the self-hosted runners survived
   (`gh api repos/cy-pca/cvcpkg/actions/runners`) before trusting any
   builder-dependent CI.
2. **In-repo reference sweep** — ~50 `transfix/libcvc-deps` occurrences
   across ~20 files: pyproject URLs, the `_GITHUB_REPO` defaults in
   `landing.py`/`app.py`, `config.py`'s catalog URL, `REPO=` in the served
   `install.sh`/`install.ps1`, workflows, composite actions, and docs.
3. **Trusted publisher registration** against the final `cy-pca/cvcpkg`
   identity on PyPI (workflow `cvcpkg-publish.yml`, environment `pypi`) and
   create the `pypi` GitHub Environment. Note: `cvcpkg-publish.yml` has no
   TestPyPI leg — it was dropped in #145/#147, so a pre-release tag stops
   after the live-smoke job instead of pushing anywhere (see the
   [operator runbook](../operator-runbook.md) §11).
4. **Re-triage [platform-coverage-pypi-blockers.md](platform-coverage-pypi-blockers.md)**
   — its snapshot is catalog revision 216 (2026-06-25) and predates two
   months of catalog growth; several of its W1 blockers have since closed.
   Re-verify what actually remains (Windows/OpenBSD coverage) and close or
   explicitly waive it.
5. **CHANGELOG entries for v2.0.1 and v2.0.2** — `pyproject.toml` is at
   2.0.2 but [CHANGELOG.md](../../CHANGELOG.md) tops out at v2.0.0.
6. **Cut the release**: set the repo variable `CVCPKG_PUBLISH_TO_PYPI=true`
   and push a stable `cvcpkg-vX.Y.Z` tag; verify the published package per
   the [operator runbook](../operator-runbook.md).

---

## Remaining work by phase

### Phase 1 — Foundation

**Complete — shipped as v2.0.0** (git tag `v2.0.0`; the tree is at 2.0.2).
Recipe-based builds, the Click CLI, the FastAPI server, the remote-builder
fleet, CI/CD, organizations, and CMake integration all shipped and are
documented across [../deployment-guide.md](../deployment-guide.md),
[../ci-cd-pipeline.md](../ci-cd-pipeline.md),
[../recipe-authoring.md](../recipe-authoring.md),
[../cmake-integration.md](../cmake-integration.md),
[../organizations.md](../organizations.md), and
[../cvcpkg-remote-builders.md](../cvcpkg-remote-builders.md). The v2.0.0
announcement stats ("99 recipes, 13 builders, 650+ packages") are historical;
`recipes/` carries ~750 recipes today.

- [ ] Document cosmo (Cosmopolitan) as a supported — or explicitly
      experimental — cross-compile platform somewhere user-facing
- [ ] Optional, low priority: a short page on the landing-page package index
      (search, sorting, org scoping)

### Phase 1.5 — Release engineering readiness

**Mostly done.** Packaging & distribution, the admin CLI, testing & quality,
CMake integration, and documentation all verify against the tree (the test
counts have only grown: ~2,950 test functions in 123 files, 24 integration
modules). What remains is what the phase itself flagged:

- [ ] Hermetic native C/C++ toolchain — recipes still build with the system
      compiler (`CC=${CC:-gcc}` in `recipes/_common/env-linux.sh`). Design:
      [hermetic-native-toolchain.md](hermetic-native-toolchain.md) +
      [native-toolchain-spec.md](native-toolchain-spec.md); executes as
      Phase 11 work
- [ ] Wire `scripts/recipe_coverage.py --require` into a CI workflow (the
      capability exists; no workflow references it)
- [ ] Close cross-platform recipe coverage on the fleet
      ([platform-coverage-pypi-blockers.md](platform-coverage-pypi-blockers.md))
- [ ] Decide whether `--require-signatures` becomes the default install
      policy (opt-in today)
- [ ] Version constraints on `depends.host_tools` (still a bare string array
      in the schema) — tracked as Phase 7.6
- [ ] Document `cvcpkg server stop/status/stats/backup` and
      `cvcpkg builder logs` (all shipped, none documented)
- [ ] CHANGELOG entries for v2.0.1/v2.0.2 (also a PyPI-path item above)

### Phase 2 — Analytics & telemetry

**Effectively complete.** Download-event analytics (migration 013), the four
admin `/v1/analytics` endpoints plus `/v1/downloads/stats`, opt-in client
telemetry (`cvcpkg telemetry`, `CVCPKG_TELEMETRY=1`, migration 014), and the
dashboard panels all shipped (#228/#238/#239). Documented in
[../analytics-and-telemetry.md](../analytics-and-telemetry.md) and
[../api-reference.md](../api-reference.md). Deferred/optional leftovers:

- [ ] Geo-IP bucketing (deferred; blocked on a GeoIP data-source decision)
- [ ] Install success/failure telemetry per package/platform (deferred)
- [ ] Per-version download/bandwidth breakdown (version is already recorded
      per event; needs a GROUP BY + API surface)
- [ ] Aggregate the telemetry `tools` column (compiler/CMake/ninja mix) in
      the summary and show it on the dashboard
- [ ] Optional: top-consumer grouping by `client_ip_hash`; resolution-time
      field; trend spike detection; make download-event recording truly
      fire-and-forget

### Phase 3 — Admin dashboard

**Effectively complete.** All six `/admin` pages (overview, packages, tokens,
audit, health, releases) shipped mid-July (#239/#240/#241) with OIDC login
(#269) and an XSS-hardening pass; mutations are audit-logged. Documented in
[../admin-dashboard.md](../admin-dashboard.md) (login flow:
[../oidc-identity.md](../oidc-identity.md)). Follow-ups:

- [ ] Release creation/promotion on top of the read-only Releases view — the
      phase's one named follow-up (see [Release model](#release-model))
- [ ] Search/filter controls on `/admin/audit` (the `/v1/audit` API already
      supports filters)
- [ ] Per-variant detail on `/admin/packages`: checksums, signatures,
      per-package download counts
- [ ] Token last-used tracking + usage history on `/admin/tokens`
- [ ] Decide or drop: geographic analytics; certificate-expiry monitoring
- [ ] Vendor Bulma CSS (currently pulled from a CDN, so the dashboard is
      unstyled offline/air-gapped)
- [ ] Optional: trigger cross-platform CI builds from the admin UI

### Phase 4 — Multi-language & ecosystem expansion

**Partial.** The recipe-ecosystem half is fully shipped (`cvcpkg init`,
`cvcpkg validate`, the resolver, the content-addressed build cache — see
[../build-cache.md](../build-cache.md)); interoperability is half shipped
(CMake config auto-install, pkg-config wiring, the cpkg Lua helper +
`cvcpkg cpkg deps` — see [../cpkg-integration.md](../cpkg-integration.md)).
Language support largely re-homed into Phases 7/7.5/7.6/11.

- [ ] Ship the cpkg recipe itself (the cvcpkg-side helper already shipped)
- [ ] Decide keep-or-drop: Spack/Conan compat layers and vcpkg manifest mode
      (zero code, zero demand signal so far)
- [ ] Fortran toolchain recipe (gfortran or flang) — currently routed around
      via f2c/clapack/`NOFORTRAN`; tracked under Phase 11
- [ ] Rust: standalone C-ABI library packages + cargo package support (the
      toolchain recipe shipped; see Phase 11)
- [ ] Julia artifacts — candidate for dropping (no evidence of demand)
- [ ] Haskell → Phase 7.5, Go → Phase 7.6 (pointers only; both unstarted)
- [ ] Thread host-tools separation through `cvcpkg install`'s from-source
      fallback, plus a server/requirements-level strip policy
- [ ] `cvcpkg init`: add or drop the promised `custom` template (only
      cmake/meson/autotools exist)
- [ ] `cvcpkg validate`: build-script linting / insecure-pattern checks
      (validate never parses build scripts today)

### Phase 5 — Federation & scaling

**Partial — much further along than the old snapshot admitted.** Shipped:
the cluster-role model (primary/mirror/edge), the full upstream-authoritative
mirror protocol (yank/nuke/tombstone reconciliation, `--trust-mirror`
dissent), federated cross-server dependency resolution with a runnable lab,
the multi-tenant builder fleet (served-namespace sets + the
`cvcpkg builder fleet` supervisor), and a backend-aware server write path
with `storage migrate`/`doctor` tooling. Documented in
[../clusters-and-federation.md](../clusters-and-federation.md). Note: an edge
now *accepts* public publishes with a divergence warning; the 409 hard-reject
is opt-in via `CVCPKG_EDGE_STRICT_PUBLIC=1`.

- [ ] CDN offload / redirect-to-edge downloads — or explicitly drop it
      ([cvcpkg-2.0.md](cvcpkg-2.0.md) lists "no CDN" as a non-goal)
- [ ] Populate: re-sync upstream re-published (same-key, new-bytes) variants
      (today: flagged `diverges_upstream`, manual nuke to reconverge)
- [ ] Populate: multi-upstream fan-in (`CVCPKG_POPULATE_UPSTREAM` is a
      single URL)
- [ ] Org-admin role (or org-membership check) for builder administration;
      scope `POST /v1/admin/update-builders` by `org_slug`
- [ ] Drain-then-update for builder self-update (the current re-exec can
      kill in-flight jobs)
- [ ] Named storage volumes: declare in server config, advertise via API,
      per-recipe/publish selection, tier/SLA metadata, volume authorization
- [ ] Per-backend publish/download round-trip tests (gcs, azure, sftp,
      rsync, rclone, gh-release)
- [ ] Execute the `storage_uri` `file://` → Garage (S3) migration on dev,
      then prod (tooling is ready)
- [ ] PostgreSQL read replicas for read-heavy workloads
- [ ] Download locality: fetch root-resolved public archives from the nearer
      satellite mirror (shared with Phase 12)
- [ ] Docs: a storage-backends reference (10 backends, per-scheme options,
      the `cvcpkg.storage_backends` entry point)

### Phase 6 — Community & governance

**Partial.** Organization namespaces are fully shipped and documented
([../organizations.md](../organizations.md)); a contribution guide now
exists ([../CONTRIBUTING.md](../CONTRIBUTING.md)). The rest:

- [ ] Package ownership model: per-package maintainers/co-maintainers and a
      name-transfer process (design: [cvcpkg-2.0.md](cvcpkg-2.0.md) §9;
      today ownership is implicit `published_by` + org membership)
- [ ] Safe recipe-build CI for *fork* PRs (the recipe-PR build workflow is
      same-repo only)
- [ ] Quality tiers: decide keep-or-drop
- [ ] Advisory database: CVE tracking, automatic yank, advisory
      notifications (the manual yank/unyank/retention primitive is shipped)
- [ ] Documentation hosting: render a recipe's README.md on the package
      detail page (server-side recipe-file reading already exists)

### Phase 7 — Python ecosystem integration

**Mostly done — and exceeded.** The `python_wheel` source type, the
`python:` block, the per-interpreter `-cp311/312/313/313t` column model
(uniform across all 163 Python package bases — 554 column recipes), the
no-GIL cp313t channel with its test-harness assertion, capability routing,
and disk-aware scheduling are all in. Documented in
[../python-wheels.md](../python-wheels.md) and
[../capabilities-and-hardware.md](../capabilities-and-hardware.md);
from-source builds follow
[from-source-python-packages-plan.md](from-source-python-packages-plan.md).

- [ ] scipy beyond Windows/cp312: build + verify linux/macos columns and
      cp311/cp313 (cp313t where upstream supports it)
- [ ] mpi4py — blocked on an MPI recipe (openmpi/mpich) that does not exist;
      implement both or explicitly drop
- [ ] CUDA-math C++ recipes with relocatable `Config.cmake` (cudart →
      cublas/cusparse → cusolver; cufft first, for F2Dock) — the
      `nvidia-*-cu12-cp311` Python wheels do *not* cover this
- [ ] Release-manifest freeze: an LTS manifest pinning the interpreter ×
      wheel matrix (filenames + sha256) alongside the C recipes (see
      [Release model](#release-model))
- [ ] Decide the fate of the unused `python_sdist` source type (0 adopters;
      from-source packages use `type: tarball`) — adopt it or fold the
      docs/schema onto the tarball pattern
- [ ] h5py cp313t column (needs a free-threading-safe h5py upstream)
- [ ] python314/python314t recipes — prerequisite for advancing the numpy
      pin

### Phase 7.5 — Haskell ecosystem integration

**Not started.** A design-and-scoping phase (merged 2026-07 as #332); no
Haskell work of any kind exists in the repo. The plan's doctrine (repackage a
bindist, key on content hash; a compiler bump rebuilds the world) is already
cited by the toolchain design docs. External version facts (GHC 9.14 LTS,
Stackage lts-24) are from July 2026 — re-verify at kickoff.

- [ ] Repackage GHC bindists as content-hash-keyed prebuilt recipes per
      platform
- [ ] Reconcile GHC native deps against existing recipes (gmp/libffi/
      ncurses/zlib exist; libiconv is missing); ship bignum variants and
      document the libgmp LGPL implication
- [ ] cabal-install recipe (stack deprioritized); hls as a GHC-by-platform
      matrix recipe
- [ ] Write the GHCup positioning decision (replace vs coexist)
- [ ] Static end-user tool recipes first: pandoc, shellcheck, hledger,
      postgrest, dhall (musl-static, `cabal list-bin`)
- [ ] Wire the cvcpkg prefix into cabal (`--extra-lib-dirs` /
      `--extra-include-dirs` / `PKG_CONFIG_PATH`)
- [ ] Decide GHC 9.14 vs Stackage lts-24 (GHC 9.10) before any library work
- [ ] If library distribution proceeds: one Stackage snapshot,
      freeze-file-as-cache-key, a `hackage_sdist` source type + `haskell:`
      schema block, a per-package quirks file, and a build-from-source
      policy for out-of-snapshot packages

### Phase 7.6 — Go ecosystem integration

**Not started.** Design-only (added 2026-08-04); full design in
[hermetic-container-runtime.md](hermetic-container-runtime.md). The
Incus-not-LXD licensing decision is made (the lxd probe stays for
compatibility).

- [ ] `go` toolchain recipe: SHA256-pinned official upstream tarball per
      platform, consumed via `depends.host_tools`
- [ ] Standardize offline Go module builds: `GOFLAGS=-mod=vendor` +
      `GOPROXY=off` against vendored trees
- [ ] Decide cgo hermeticity (`CGO_ENABLED`) — blocked on the
      [hermetic-native-toolchain.md](hermetic-native-toolchain.md) outcome
- [ ] verlihub recipe with the Go-built TLS proxy enabled (first Go
      consumer; no recipe exists yet)
- [ ] ~6 C recipes: liblxc (>= 5.0.0), libcap, libacl, libattr, libudev,
      libuv
- [ ] `incus` recipe from the release tarball (bundled Go deps +
      libraft/libcowsql)
- [ ] Split the capability: recipes use `host_tools: [incus]` +
      `requires_capabilities: [linux-containers]`; migrate haiku-image's
      `test.vm` gate
- [ ] Schema: let `depends.host_tools` accept full dep entries
      (name/version/platforms); fix the latent host_tools platform-filter
      bug in `builder.py::_dep_names`
- [ ] Enforce host-tool version constraints at build time; record host-tool
      provenance in built artifacts (ties to Phase 16)

### Phase 8 — Self-hosting & universal bootstrap (`cvpkg`)

**Partial.** The three headline artifacts (cvcpkg self-install recipe,
`cvcpkg-sc` bake, the `cvpkg` APE) are unstarted — but the bootstrap *goal*
has real partial delivery by other means: per-platform PyInstaller standalone
binaries for six OSes with CI smoke tests, a combined busybox client+server
binary (#396/#449, CI-artifact-only), and checksummed `curl|sh` / `irm|iex`
quick-install served from cvcpkg.org (#514). Those are now documented in
[../pypi-install.md](../pypi-install.md) and
[../getting-started-tutorial.md](../getting-started-tutorial.md).
Single-binary design: [static-single-binary-python.md](static-single-binary-python.md).
Known blockers (#513 scoping): cryptography's Rust+CFFI core is client-side,
and cosmo cannot `dlopen` standard wheels, forcing compiled-in extensions.

- [ ] cvcpkg self-install recipe (python313 + wheel-column deps +
      `bin/cvcpkg` shim); make `cvcpkg install cvcpkg` the recommended
      build-machine path
- [ ] Prove cosmo-CPython boots at all: finish apelink + zipos stdlib
      packaging + a launcher `main()`
- [ ] Client-only `cvpkg` APE: pure-Python PyYAML loader, bundle jsonschema,
      and solve or scope out cryptography (the long pole)
- [ ] Combined client+builder+server APE on top of the client-only cut
- [ ] `cvcpkg-sc` recipe — blocked on Phase 19 `cvcpkg bake`
- [ ] Publish the APE at cvcpkg.org/cvpkg, fold it into the quick-install
      flow, and smoke-test it across linux/windows/macos/freebsd/openbsd/
      netbsd
- [ ] Cut a `cvcpkg-v*` release so the #514 one-liners are verified against
      a real asset (also a PyPI-path item)
- [ ] Air-gapped story: `recipe export` + a source-cache pre-seed
      (`source fetch`), then an end-to-end airgap test (shared with
      Phase 15)
- [ ] Decide whether releases ship the server-bundled combined binary
      (currently CI-artifact-only; released standalones are client-only)

### Phase 9 — Fleet & platform expansion

**In progress.** Haiku became a real platform with SSH cross-build delegation
(#431, 2026-08-11): haiku-image is published and boot-proven, covered in
[../image-packages.md](../image-packages.md). Dev-cluster redeploys now track
master on every merge (#494). The DragonflyBSD track is dropped; GhostBSD's
decision is made (freebsd compat mode, no builder provisioned). The
emulated-arm64 pilot is superseded by GitHub-hosted arm runners (#451).

- [ ] Surface the deployed commit through `/healthz` and
      `cvcpkg server status` (`CVCPKG_RELEASE=dev-<shortsha>` is already
      baked into the dev image tag; plumb it into the health response and
      CLI printout)
- [ ] Haiku fleet membership: provision the published haiku-image VM, wire
      the owning Linux builder's haikuhost settings, register it in the
      fleet tables
- [ ] Plumb `HAIKU_BUILDER_SSH_KEY` into an Incus-capable builder so
      haiku-image's `test.vm` becomes a real signal instead of a green skip
- [ ] Add the missing CHANGELOG entry for Haiku SSH cross-build delegation
      (#431)
- [ ] qemu recipe stack (qemu, dtc, libslirp, capstone) — still the gate for
      emulated riscv64/exotic-BSD builders and reproducible provisioning
- [ ] Optional: provision a GhostBSD builder VM to validate the desktop-BSD
      freebsd-compat story
- [ ] Fleet-wide image recipes: linux-image first, then the BSDs; Windows as
      an org-private image with licensing settled up front; decide
      storage/retention policy for large image packages first

### Phase 10 — Peer providers & hardware-aware concretization

**Partial — a minimal slice shipped.** Recipe-level `provides:` virtual slots
with mutex enforcement (#280), capability-ranked virtual-package resolution
at install time with host probes and a `CVCPKG_CAPABILITIES` override (#337),
server-side provides propagation, and builder-side `requires_capabilities` +
free-disk routing are in — which delivers CUDA-peer auto-selection in
miniature (`torch-cp311` → `torch-cp311-cuda` on a CUDA host). Documented in
[../capabilities-and-hardware.md](../capabilities-and-hardware.md) and
[../mutual-exclusion.md](../mutual-exclusion.md). The full design remains:

- [ ] Capability contract files (`capabilities/<name>.yaml`,
      `kind: capability`) with declared per-provider priorities; replace the
      `len(requires_capabilities)` ranking heuristic
- [ ] `requires_isa` CPU-ISA gating and a HardwareProfile descriptor
      (`{arch, isa[], gpu:{vendor,arch}}`) probed from
      cpuid//proc/cpuinfo/sysctl
- [ ] Explicit profile input for cross-target concretization
      (`install --hardware-profile <file>`)
- [ ] Per-user provider override: a `providers:` config key and
      `install --provider <virtual>=<peer>`
- [ ] CLI introspection: `cvcpkg providers <virtual>` and `cvcpkg profile`
      (or fold into `cvcpkg doctor`)
- [ ] GPU-presence probe (cuda-runtime) separate from the nvcc compile
      probe, so install-side `-cuda` selection works on GPU hosts without a
      toolkit
- [ ] BLAS family: blas/lapack/fftw3 virtuals; mkl/blis/armpl/accelerate
      peer recipes; numpy/scipy columns depending on the virtual instead of
      openblas
- [ ] Provenance: record *why* a peer was chosen in the lockfile;
      per-profile provider pinning in LTS releases
- [ ] `validate`: check that peers of one slot honour a shared ABI contract
      (cmake target/soname), not just name resolution
- [ ] libcvc repo: `cvc::volume` ABI variant-invariance (reserve the
      shared_ptr slot) so cvcgl stays single-package; then unblock
      pycvc-gl-cuda
- [ ] Run the dlopen-boundary spike for single-binary runtime GPU dispatch
      (volrover3) — or formally close it in favor of install-time selection

### Phase 11 — Self-hosting toolchains + `cvpkg`

**Partial.** Landed since the old snapshot: an llvm recipe shipping
clang+lld, llvm-mingw, a native mingw-w64-gcc (with gfortran) +
mingw-w64-runtime, and rust 1.90 with cargo exercised by real from-source
builds. Toolchain design:
[hermetic-native-toolchain.md](hermetic-native-toolchain.md) +
[native-toolchain-spec.md](native-toolchain-spec.md); single-binary design:
[static-single-binary-python.md](static-single-binary-python.md).

- [ ] Pinned native gcc-toolchain recipe (GCC 14.2 + glibc 2.28 sysroot) per
      the spec, then point `env-linux.sh` `CC`/`CXX` at it
- [ ] Decide clang20: drop the checkbox (llvm-cbe needs no clang) or add
      `clang;lld` to llvm20 for LLVM-20-pinned consumers
- [ ] Standalone binutils recipe (or an explicit llvm-ar/lld-suffices
      decision); libc++/compiler-rt runtimes if a non-GNU ABI track is ever
      wanted
- [ ] Cross-arch assemblers: per-target cross-binutils (aarch64, riscv64)
      and/or vasm (today: nasm for x86 + the LLVM integrated assembler)
- [ ] Native linux/macos gfortran story (mingw-w64-gcc is Windows-only)
- [ ] Intel oneAPI (icx/ifx) licensing survey — prebuilt-staging recipe vs
      provisioning dep
- [ ] First-class Rust package (cargo/crates) support beyond host-tool usage
      (ties to Phase 4)
- [ ] `cvpkg` proper and the `cvpkg`/`cvcpkg-sc` recipe — cvcpkg building
      itself through its own recipe system (see Phase 8)
- [ ] Push a `cvcpkg-v*` tag so the standalone-release + checksums +
      install.sh pipeline produces a verified GitHub Release asset

### Phase 12 — Federation hardening

**Effectively complete.** Selective mirroring allow/deny lists (#260), the
mirror size budget with usage-based eviction (#261), and top-down
root-authoritative resolution (#265) all shipped with tests and operator docs
in [../clusters-and-federation.md](../clusters-and-federation.md). Leftovers:

- [ ] Mirror-policy management surface: admin API, `/admin` section, and a
      `cvcpkg server mirror-policy` CLI (today `CVCPKG_POPULATE_*` env vars
      only, requiring a server restart to change)
- [ ] Exempt release-tagged / pinned populate-origin packages from
      mirror-budget eviction — or explicitly drop that promise
- [ ] Optional arch/variant selectors for the allow/deny lists (name +
      platform + size cap only today)
- [ ] Download locality (shared with Phase 5)
- [ ] Fix: `CVCPKG_POPULATE_MAX_MIRROR_BYTES` is parsed with a bare `int()`
      while `MAX_PACKAGE_BYTES` gained the `8GB`/`512MB` size grammar — an
      operator writing `10GB` crashes server startup

### Phase 13 — Identity & access (OIDC)

**Effectively complete.** OIDC admin login (code flow + PKCE-S256,
signed-txn cookie, 404-when-unconfigured) shipped as #269 and is documented
in [../oidc-identity.md](../oidc-identity.md). Follow-ups:

- [ ] Local id_token signature verification via JWKS (opt-in hardening on
      top of the TLS-validated direct exchange)
- [ ] OIDC-authenticated publish: map an OIDC session to a publisher
      identity (`CVCPKG_OIDC_PUBLISHER_GROUPS` is currently inert)
- [ ] IdP-group → org-membership sync (also Phase 6 governance)
- [ ] Optional: a dedicated AuditAction for logins (currently reuses
      `token_create`); login auditing on the file-state backend

### Phase 14 — Source recipes (file-artifact packages)

**Complete.** `platform: any` file-artifact packages, the staging helpers,
schema support, and a canonizing end-to-end test shipped (#259/#275); the
noarch pipeline is heavily exercised in production (860 recipe files declare
`platform: any`) with post-roadmap hardening in #376/#379/#457/#496.
Documented in [../source-recipes.md](../source-recipes.md).

- [ ] Adopt the source→binary staging pattern in at least one production
      recipe (`cvc_stage_source`/`cvc_source_dir_of` are test-canonized but
      have zero users under `recipes/`)
- [ ] Operational: the gated yank/republish of the 278 mis-tagged noarch
      bundles identified by the #496 identity fix
- [ ] Optional: extend the e2e test to round-trip an any/any package through
      the server publish/install path

### Phase 15 — CLI UX & the recipe-first workflow

**Partial.** Full design: [cli-ux-recipe-first.md](cli-ux-recipe-first.md).
Shipped since the old snapshot: `cvcpkg generate` (build-system detection/
import), grouped `--help`, and server-side recipe browsing (#503); the build
default flipped to `--no-deps` with a CWD `./recipes` auto-overlay and
`--incremental` (#401); `install-deps` (#348) provides the prebuilt-deps
path; `world` is legacy; and `cvcpkg uninstall` (#522) — the install-conflict
error had pointed at this command since before it existed. It ships ahead of
`prefix.db`, deriving each package's owned-file set from its cached bundle
archive rather than a DB (`--cascade` and `--dry-run`; refuses on
source-built entries, since there is no archive to enumerate). The `~/.cvcpkg/`
home and the prefix databases are otherwise unbuilt.

- [ ] Deprecation warning on `cvcpkg install --from cvc-requirements.yaml`;
      lead the README quick start with the recipe-first flow
- [ ] Make `build` auto-install prebuilt dep bundles with a from-source
      fallback when offline/`--local`; add `--build-deps-from-source`
- [ ] Developer loop: cvcpkg-assisted recipe patch generation from an edited
      source tree
- [ ] Extend `cvcpkg generate` with qmake/Conan/cpkg importers
- [ ] Fold `cvcpkg-server` under `cvcpkg server`; drop the second console
      script (keep a one-release shim)
- [ ] Recipe discovery via `CVCPKG_RECIPES_PATH` and `~/.cvcpkg/recipes`
      (the CWD overlay is done)
- [ ] `~/.cvcpkg` home: auto-seeded commented `settings.yaml`, `--save`
      sticky settings, `cvcpkg config get/set/unset/edit`, default
      build/install prefixes, cache move to `~/.cvcpkg/cache` with a client
      `--cache-dir` flag
- [ ] `cvcpkg clean` for whole/per-package build trees (incl.
      `--incremental` trees)
- [ ] `cvcpkg activate <prefix>` front door (subshell or eval-able env)
- [ ] Audit header/lib placement so libraries are never stripped as host
      tools
- [ ] `~/.cvcpkg/local.db` prefix registry: register/alias/delete/inspect/
      modify, path-or-alias everywhere, stale-entry repair, registry-powered
      gc (gc currently prunes against an empty set)
- [ ] Per-prefix `prefix.db`: installed-file tracking (sha256/mode/owner),
      `cvcpkg owns`, migrating `cvcpkg uninstall` from archive-derived to
      DB-backed (adds a teardown hook and drift detection on modified
      files), idempotent installs, a `verify` that actually hashes files,
      corpse-free upgrades, an append-only operations journal
- [ ] Terminal UX: real progress bars/spinners for downloads/installs (wire
      up or drop the dead `[progress]` tqdm extra); colorized status
      summaries
- [ ] Canonize an end-to-end downstream-project from-source build test
- [ ] Pre-download command to warm the sha256-keyed source cache without
      building; compressed-or-extracted cache forms
- [ ] `cvcpkg recipe export` with dependency closure + a server export API +
      `cvcpkg source fetch` + a one-shot seed with a provenance manifest
      (the air-gapped story; shared with Phase 8)

### Phase 16 — Prefix provenance & server seeding

**Not started.** Groundwork exists (per-prefix `lockfile.yaml`; every bundle
embeds its producing recipe + a transitive `recipe_sha256`), but multi-package
installs clobber the shared `share/libcvc-deps/` manifest/recipe paths
last-writer-wins, so even the incidental provenance is unreliable.

- [ ] An install flag that writes catalog seeding records (catalog snapshot,
      per-package entries) into `share/cvcpkg/` in the prefix
- [ ] Install each package's recipe into per-package dirs (and fix the
      last-writer-wins collision)
- [ ] Record org and private status per installed package
- [ ] Warn (or refuse) when a prefix seed would carry private-org content
- [ ] Server-side counterpart: bootstrap a cvcpkg-server catalog + archives
      + recipes from a seeded prefix
- [ ] Decide and execute the `share/libcvc-deps/` → `share/cvcpkg/` rename
      the design presumes

### Phase 17 — Recipe archives: declared artifacts & package-page UX

**Partial — the UX half shipped** (#503): `GET /v1/recipe/{name}` plus
`/files`, `/file`, `/archive`, and the package-page recipe section with
click-to-expand artifacts, inline images, and archive downloads. The
governance half is untouched.

- [ ] Count org-scoped recipe-bundle bytes against the org storage quota
      (global/base recipes stay exempt)
- [ ] Require admin for `POST /v1/recipes/{name}` in the global namespace,
      matching DELETE and register-placeholder
- [ ] Add a size cap to the recipe-upload path and stream to disk instead of
      reading the upload into memory
- [ ] Route recipe bundles through the StorageBackend abstraction (s3://,
      gcs://; also a Phase 18 backup prerequisite)
- [ ] Decide the declared-artifacts schema item: add declaration +
      completeness enforcement, or drop it as obsoleted by the shipped
      server-side enumeration
- [ ] Collapse same platform/arch revision rows in the package-page builds
      table (show newest, expand older unyanked revisions)
- [ ] Add the missing CHANGELOG entry for #503

### Phase 18 — Server backups, scheduled jobs & quota governance

**Not started.** Backups are still a database dump only; all periodic work
remains hardcoded asyncio loops; quotas are editable only via the JSON API.

- [ ] First-class recipe backups in `cvcpkg server backup`, with explicit
      public/private-org inclusion flags
- [ ] Selective package backups with size/date/type filters (full archive vs
      bounded backup)
- [ ] `cvcpkg server restore` for recipe and recipe+package backups
      (complement to Phase 16 seed-from-prefix)
- [ ] A scheduled-task abstraction: jobs-with-schedule table + a scheduler
      running registered jobs
- [ ] An admin job manager: API + `/admin` page + CLI to list/pause/resume/
      reschedule/trigger/cancel jobs with run history, audit-logged
- [ ] Bring the six hardcoded lifespan loops (build scheduler, log GC, yank
      GC, mirror health, populate sync, mirror sync) under the manager
- [ ] Admin-managed scheduled backups targeting the Phase 5 storage backends
- [ ] Flip the `CVCPKG_GLOBAL_CACHE_STORAGE_LIMIT_BYTES` default from
      100 GiB to 0/unlimited (0-semantics already implemented)
- [ ] A quota-reconciliation job recomputing each org's
      `storage_used_bytes` from `SUM(size_bytes)`, including recipe bundles
      once Phase 17 counts them
- [ ] A quota admin UI page to view/edit global and per-org limits and usage

### Phase 19 — Application packaging & desktop delivery

**Not started — research done.** The extensive feasibility research (bake
mechanism ladders per OS, the persistent-state model, the cosmo APE variant,
the code-signing landscape) is preserved in
[application-packaging.md](application-packaging.md). Per-asset sha256
release checksums already ship (#514), partially covering the Linux signing
item.

- [ ] Recipe-schema application surface: CLI entry points + desktop assets
      (icons/docs/media)
- [ ] Opt-in, reversible desktop integration at install time
- [ ] Installer commands from an install prefix: Windows exe/MSI, Linux
      AppImage, macOS dmg
- [ ] `cvcpkg bake` prototypes: Linux (userns + squashfuse + overlayfs),
      macOS (embedded dmg + shadow CoW), Windows (ISO carve + junction
      layering), cosmo (fat APE stub + per-OS ladder) — each with the
      documented fallback ladder and a fleet smoke test
- [ ] Persistent-state model implementation (`bake status/reset/commit`;
      sidecar store default)
- [ ] squashfs-tools recipe (also wanted by the hermetic-incus work)
- [ ] Code signing: Windows (Azure Artifact Signing + jsign in Linux CI with
      RFC 3161 timestamps); macOS (Developer Program enrollment, Developer
      ID certs, codesign/notarytool/staple); Linux (GPG detached signatures
      + cosign attestations on top of the shipped sha256 checksums)

### Phase 20 — First-party & featured software recipes

**Partial — the infrastructure shipped, the featured recipes did not.**
Shipped: the full cvcpkg self-hosting Python wheel closure (#370, converted
to the `-cpXXX` matrix), sdl3 with a 6-platform matrix (#398), and the org
homepage/profile-link field. Every actual featured-software recipe has zero
code. This phase gates the PyPI *announcement* quality bar, not the publish
mechanics.

- [ ] cvcpkg self-install recipe (`recipes/cvcpkg`) now that its wheel
      closure exists (the Phase 8 item this phase completes)
- [ ] cp313t columns for pyyaml + greenlet via the sdist path; decide the
      cryptography/pydantic-core cp313t story (blocked on the Rust
      toolchain; abi3 cannot back free-threaded columns)
- [ ] eiskaltdcpp recipe + a libidn2 recipe; decide frontend variants
      (-qt/-gtk/-daemon/-cli)
- [ ] eiskaltdcpp-py recipe + missing extras wheels (python-jose,
      pytest-asyncio, pytest-timeout)
- [ ] verlihub recipe + new icu and libmaxminddb recipes; the TLS proxy
      waits on the Phase 7.6 hermetic Go toolchain
- [ ] TexMol recipe in the cvc org (Qt6 fork, depends on libcvc; watch the
      vendored glew/libCG/levmar/contourtree)
- [ ] ezquake recipe (tfx org) + new jansson and minizip recipes
- [ ] sdl2 recipe (2.32.x) + the four SDL satellites (SDL_image, SDL_mixer,
      SDL_ttf, SDL_net) as separate recipes
- [ ] Extend the sdl3 matrix beyond its 6 platforms (wasm/emscripten first;
      haiku when the Haiku builder lands)
- [ ] Quake engines: fteqw + the FTE tool set, fteqcc, qss-m, quakespasm
      (-spiked), darkplaces (client+server); optional vkquake/ironwail
- [ ] Quake servers/mods: mvdsv, qwfwd, ktx, crmod7; fortressone engine-only
      pending an upstream licensing conversation
- [ ] Implement the `redistributable: false` recipe flag + a client-side
      install-time-fetch path (highest-value legal-safety item; shared with
      Phase 23 BYO)
- [ ] Tier A content recipes: librequake, spirit-quake-maps-gpl; make
      LibreQuake the default engine content
- [ ] Publish a DMCA/takedown contact and record per-package provenance
      before hosting any game content
- [ ] Config-only: set the tfx org homepage link on the live server

### Phase 21 — Package visibility: hidden packages

**Not started.** No `hidden` column, filters, CLI flags, or endpoints exist.
The upstream-authority machinery built for the *yanked* flag (migrations
020–022/024, populate reconciliation) covers much of what the design's
propagation items asked for — and staled some of its premises, corrected
below.

- [ ] Add a `hidden` boolean to the package row (migration 028+ — the head
      is 027) covering public, org-scoped, and private-org packages
- [ ] Filter hidden from `/v1/packages`, `/v1/search` (+ facets),
      `/v1/packages/{name}`, `/v1/feed.xml`, tag counts, and the package
      counts in `/healthz`/`/metrics`/admin stats — each with an
      `include_hidden` opt-in mirroring `include_yanked`
- [ ] Keep hidden bundles in the default `/v1/catalog` feed; add `hidden` to
      CatalogEntry and the catalog parse path
- [ ] CLI: `--include-hidden` / `--hidden-only` on `cvcpkg search`; make
      `cvcpkg list --available` honor hidden
- [ ] POST hide/unhide endpoints modeled on yank/unyank (publisher-or-admin,
      ownership/org checks, audit-logged)
- [ ] First, fold the four duplicated org-ACL visibility predicates into one
      shared helper before adding a third axis
- [ ] Populate: add `hidden` to the add_package field allowlist and extend
      the reconcile flag-refresh pattern (built for yanked) to hidden flips
- [ ] Decide the hidden authority policy against the shipped yanked
      precedent (mirror-may-dissent + `--trust-mirror`) — the original
      "downstream must not un-hide" rule contradicts it
- [ ] Decide whether org-scoped hidden packages propagate at all (populate
      skips org packages on both sides today)

### Phase 22 — Federation topology: nested authority & network introspection

**Partial.** Client resolution is still strictly two-tier and
`federation.py` has zero production callers, but cross-tier divergence
visibility shipped (#406): an edge may publish into the public namespace with
a divergence warning, and populate flags/clears `diverges_upstream` — see
[../clusters-and-federation.md](../clusters-and-federation.md).

- [ ] Generalize `merge_root_authoritative` to an N-tier chain (root → mid →
      edge), higher tiers authoritative
- [ ] Same-org override across tiers — drop the unconditional
      all-org-bundles-from-local two-tier simplification
- [ ] Client resolution-time cross-tier mismatch warnings; add server
      provenance to CatalogEntry (only ResolvedNode carries it)
- [ ] Make prefer-higher/fall-back-on-unreachable explicit across the chain;
      warn on every lower-tier substitution
- [ ] Server-side topology model: parent/upstream identity, tier or depth,
      orgs served; report via `/healthz` or a new endpoint
- [ ] Decide mirror-under-mirror policy deliberately (currently a hard 403
      while populate-edge nesting is unblocked)
- [ ] Wire `federation.py` / `resolve_federated` into the production
      resolution path
- [ ] Permission-gated network statistics commands (server roles, domains,
      cluster nodes per domain)
- [ ] Decide the topology-disclosure policy for `/healthz` and `/metrics`
      (both fully public today, leaking `mirror_mode` + populate upstream)
- [ ] Add the missing CHANGELOG entry for #406

### Phase 23 — cvcpkg as a build & configuration-management system

**Not started.** The full design — typed state resources, check/apply modes,
BYO non-redistributable assets, security hardening (config-channel-is-C2),
and the forensic journal — is extracted to
[config-management.md](config-management.md). Some prerequisites already
exist (Ed25519 signing, the hash-chained server audit log, org namespaces,
signed-bundle file lists). Condensed deliverables:

- [ ] Recipe schema: typed `state:` resources (file/symlink/template/env/
      service/registry-key/user) with Get/Test/Set semantics and auto-revert
      via a per-prefix state DB; `script:`+`teardown:` escape hatch with
      non-revertible labeling
- [ ] CLI: `cvcpkg check` (machine-readable audit) and `cvcpkg apply`;
      explicitly no daemon/pull server
- [ ] Prerequisite from Phase 15: the per-prefix state database
      (`cvcpkg uninstall` itself shipped in #522, archive-derived; state
      teardown needs the DB-backed version)
- [ ] Windows: delegate state resources to the DSC v3 executable+manifest
      protocol; drift correction touches only declared fields
- [ ] BYO: `source.type: byo` + a `restrict: [fetch, mirror]` axis;
      sha256+size verification invariant to provenance; a distfiles search
      path (`~/.cvcpkg/distfiles`, `CVCPKG_DISTFILES`, `--asset`)
- [ ] Licensing: a `license.eula` acceptance gate and the
      `redistributable: false` publish-block flag (shared with Phase 20)
- [ ] Security: TUF-style key rotation/thresholds/expiry; a
      client-verifiable transparency log over the existing audit chain;
      apply-mode hash pinning; sandboxed fetch/build; secret references
      resolved at apply time; signed lockfile and state DBs
- [ ] Longevity: a written schema-versioning + deprecation policy; a CI
      idempotency contract (apply twice clean + once dirty)
- [ ] Forensics: a hash-chained append-only local journal with server
      chain-head cross-anchoring; generation snapshots; a rebuildable local
      index; DFIR outputs
- [ ] Worked example recipes (BYO retail data, fetch-no-mirror,
      state/config composition) + integration tests

### Phase 24 — Live updates, activity feed & build transparency

**Not started.** The server has exactly one builder-facing WebSocket and one
gated build-log SSE; lifecycle events write audit rows only. The
"private stays private" guard-rails (`_assert_build_visible` /
`_assert_dag_visible`) already exist and must be preserved.

- [ ] Client-facing WebSocket/SSE catalog-event channel, reusing the
      existing webhook event bus
- [ ] Push `package.published` to connected clients; SPA new-package
      toast/live counter (replacing the polling intervals)
- [ ] Per-user subscription primitive: new version of package X / new
      packages in org Y
- [ ] Emit bus/webhook events for yank, unyank, nuke, and recipe upload
      (audit-row-only today)
- [ ] Public visibility-filtered activity-feed endpoint (excluding
      private-org and hidden-package events) + an SPA scrolling feed,
      generalizing `/v1/feed.xml`
- [ ] Open `GET /v1/builds`, `/v1/builds/{id}`, `/v1/builds/{id}/log`, and
      the log SSE to public read for public/no-org builds, preserving
      404-on-private semantics
- [ ] A public/private access test suite for build jobs and logs paralleling
      the existing builder public-access tests

### Phase 25 — PyPI release (final phase)

**Open — scoped, not executed.** The software rename to cvcpkg is complete
(package name, CLI, server, recipe archive); the cy-pca org exists and #515
fully scoped the transfer against real GitHub state, confirming no PyPI
release or trusted publisher exists yet. The publish workflow is ready. The
concrete ordered checklist lives in
[Path to PyPI (release blockers)](#path-to-pypi-release-blockers) above; the
remaining items are exactly those six steps.

### Recipe wishlist

`recipes/` currently holds 749 recipes (195 native + 554 Python `-cpXXX`
columns across 163 package bases). The forward-looking wishlist — qemu stack,
shells, editors, the KDE tier ladder, SDL satellites, featured apps, and the
rest — is maintained in [new-dependencies.md](new-dependencies.md) rather
than inline here; strategic recipe items appear under their owning phases
(7/7.5/7.6/9/11/20). The category taxonomy from the old roadmap was never
implemented as written — the shipped browse surface is free-form recipe tags
plus the server's `/tags` page.

---

## Design docs

Everything under `docs/roadmap/` besides this file, with its current status.
"Historical" docs are kept for reference and their section numbers stay
frozen (code and other docs cite them).

| Doc | Status | Covers |
|---|---|---|
| [application-packaging.md](application-packaging.md) | Active (new in this consolidation) | Phase 19: bake mechanism ladders, installers, persistent state, code signing |
| [archive-format-handling.md](archive-format-handling.md) | Active | Single source of truth for the pack archive format (`--format`, per-platform defaults) |
| [cli-ux-recipe-first.md](cli-ux-recipe-first.md) | Active (new in this consolidation) | Phase 15: `~/.cvcpkg` home, prefix registries/databases, uninstall, air-gap export |
| [config-management.md](config-management.md) | Active (new in this consolidation) | Phase 23: state resources, check/apply, BYO assets, security + forensics |
| [cvcpkg-2.0.md](cvcpkg-2.0.md) | Historical | The 2.0 rewrite plan; §9 (ownership) still referenced by Phase 6 |
| [from-source-python-packages-plan.md](from-source-python-packages-plan.md) | Implemented (historical) | sdist-by-default from-source Python builds; hand-written exceptions |
| [hermetic-container-runtime.md](hermetic-container-runtime.md) | Active | Phase 7.6: hermetic Go + Incus VM-test substrate, `linux-containers` capability |
| [hermetic-native-toolchain.md](hermetic-native-toolchain.md) | Active | Phase 1.5/11: why and how to pin the native C/C++ toolchain |
| [native-toolchain-spec.md](native-toolchain-spec.md) | Active | The concrete gcc-toolchain recipe spec (GCC 14.2 + glibc 2.28 sysroot) |
| [new-dependencies.md](new-dependencies.md) | Active | The recipe wishlist / new-dependency intake |
| [platform-coverage-pypi-blockers.md](platform-coverage-pypi-blockers.md) | Active (needs re-triage) | Catalog-coverage gaps gating the PyPI announcement (snapshot: rev 216, 2026-06-25) |
| [remote-builders.md](remote-builders.md) | Complete (reference) | The builder protocol and fleet design, as built |
| [revision-bump-cascade.md](revision-bump-cascade.md) | Active | Content-hash cascade detection + supersedes/rebuild-reason provenance (planned) |
| [split-distribution.md](split-distribution.md) | Historical | Pre-2.0 split-distribution design; §-numbers frozen |
| [static-single-binary-python.md](static-single-binary-python.md) | Active | Phase 8/19: embedded-Python single binaries (cosmo APE, wasm) |

---

## Release model

Two channels, one shipped:

- **Live (shipped).** Everything published today is a live (untagged)
  package: immutable once published, visible in the catalog immediately,
  with the yank/unyank/nuke lifecycle and recipe push/pull sync. Revision
  bumps are automated (`pack --bump` and friends). Documented in
  [../release-channels.md](../release-channels.md).
- **LTS / tagged releases (plumbing only).** `release_tag` exists end to end
  — column, API filters, publish parameter, `/admin/releases` page, GC
  protection — but no server-side release has ever been cut; the admin
  Releases tab is read-only. The old v1.x tagged recipe-set releases ended
  at v1.6.1 (2026-05-31), and the release-branch candidate/freeze model from
  the original design is dead.

Still to build (the Phase 3 follow-up):

- [ ] Release creation/freeze tooling: stamp `release_tag` on a curated set
      of live bundles server-side
- [ ] Live→release promotion (curate + promote live recipes/bundles into a
      tagged release manifest — including the Phase 7 interpreter × wheel
      matrix freeze)
- [ ] Decide and document the candidate/freeze process that replaces the
      dead release-branch model
- [ ] A release-time signing policy (sign-all gate) if/when LTS releases are
      cut; signing is per-publisher opt-in today
- [ ] Finish the revision-bump cascade: content-hash cascade detection +
      supersedes/rebuild-reason provenance
      ([revision-bump-cascade.md](revision-bump-cascade.md))

---

## Design principles

1. **Simplicity over cleverness.** A graduate student should be able to
   understand the system in an afternoon. One CLI, one server, one database.
2. **Reproducibility is non-negotiable.** If a build worked yesterday, it
   must work next year. Pinned versions, checksums, immutable published
   bundles, signed packages.
3. **Cross-platform is a first-class citizen.** Every recipe builds on its
   declared platform matrix or clearly documents the restriction.
4. **No vendor lock-in.** Commodity hardware, open-source software, HTTP +
   JSON. Any client can interoperate.
5. **Security by default.** TLS everywhere, signed packages, a
   tamper-evident audit chain, role-based access control — without
   unnecessary complexity.
6. **The right auth for each audience.** HMAC-SHA256 tokens for machines
   (CI, builders, scripted publishes); delegated OIDC for humans. No
   bespoke account system.
7. **Community-first.** Recipes are YAML — no DSL to learn. Publishing is a
   single CLI command.
8. **Data-driven decisions.** Analytics and telemetry (always opt-in) point
   effort where it matters.

---

## License

cvcpkg is MIT-licensed ([LICENSE](../../LICENSE), copyright CyberPC Angel,
LLC). The extended license-choice rationale is preserved in this file's
pre-consolidation git history.

---

*This is a living document. Phase numbers are permanent; statuses and
checklists are updated as work lands.*
