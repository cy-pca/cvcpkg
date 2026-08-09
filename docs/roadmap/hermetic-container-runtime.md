# Hermetic Container Runtime — Incus as a Dependency, Not a Capability

**Status:** proposed · **Motivation:** the VM-test substrate is the last build input that floats to whatever the host admin installed.

## The problem

`incus`, `lxd` and classic `lxc` are **host capabilities**. `src/cvcpkg/platform.py`
probes for them, and a recipe's `test.vm` block gates on the result:

```yaml
test:
  vm:
    requires_capabilities: [incus]      # skipped — never failed — without one
    script: vm-test.sh
```

The probes are careful about the right things. They prove *usability* rather than
presence (a builder outside the `incus-admin` group has every binary on `PATH` and
would still burn each job routed to it), and they disambiguate the overloaded
`lxc` name — LXD's CLI entry point is a binary literally called `lxc`, while classic
LXC ships no plain `lxc` at all. None of that is the problem.

The problem is what the capability *cannot* express:

- **Provisioning happens out of band.** A builder runs VM tests only because an
  admin ran `apt install incus` and edited group membership. cvcpkg can detect
  the result but cannot cause it, describe it, or verify a builder was set up the
  way another builder was.
- **The version is whatever the distro shipped.** The probe answers yes/no; it
  cannot pin. An image tested against Incus 6.x on one builder and something
  older on another has not been tested reproducibly — and `test.vm` exists
  precisely to make image packages trustworthy.
- **It is the same gap we have already closed four times.** Python got hermetic
  interpreters (Phase 7), Haskell gets its own GHC (Phase 7.5, "Our GHC, Our
  ABI"), WASM gets a bundled Emscripten SDK (`new-dependencies.md` §1), and
  `hermetic-native-toolchain.md` makes the same argument for `CC`/`CXX`: *"same
  source + same flags" does NOT yield the same binary if the toolchain can
  differ.* The container runtime is now the odd one out.

Today `vmtest.py` has exactly one consumer (`haiku-image`), so the blast radius is
small. That changes as image packages grow, which is the moment to fix it.

## Why it has not happened: we have no Go

Incus and LXD are written in Go, and **cvcpkg has no Go toolchain.** The word
"Go" appears exactly once in this entire roadmap — Phase 20, on `verlihub`:

> the TLS-proxy feature requires a **Go toolchain** and should stay off by default.

That is a feature already deferred because of this gap. Phase 4's language list
names Fortran, Rust, Python, Julia and Haskell and omits Go entirely. So the work
splits cleanly in two, and the first half is worth doing on its own merits.

## Layer 1 — a hermetic Go toolchain (Phase 7.6)

A `go` recipe pinning a specific toolchain per platform, declared by consumers
through `depends.host_tools`.

Go is **much easier than Haskell** and should not be sequenced behind it:

- **No bootstrap problem in practice.** Go has been self-hosting since 1.5, so a
  from-source build needs a Go to build Go — but upstream publishes official
  per-platform binary tarballs. SHA256-pin and mirror them, exactly as
  `new-dependencies.md` §1 does for the Emscripten SDK. No `ghc-bootstrap` dance.
- **No per-closure ABI hashing.** The thing that makes Haskell hard (Phase 7.5's
  central warning) simply does not exist here. Go links statically by default and
  produces one self-contained binary.

The one genuinely new problem is **module fetching**. `go build` wants
`proxy.golang.org`, and cvcpkg builds must be offline and reproducible — the Go
analogue of the `python_sdist` offline problem. Two options, and Incus happens to
hand us the good one:

- `GOFLAGS=-mod=vendor` + `GOPROXY=off` against a vendored dependency tree.
  **Incus release tarballs already bundle a complete dependency tree**, so the
  flagship consumer needs no module proxy at all.
- For recipes without a vendored tree, a `go mod vendor` step at recipe-authoring
  time, committing the hash — deferred until something needs it.

**`CGO_ENABLED` composes with the native toolchain.** Incus needs cgo (liblxc,
cowsql), so a Go build is only as hermetic as its C compiler — this layer inherits
whatever `hermetic-native-toolchain.md` decides, and is not fully hermetic before
it lands.

## Layer 2 — Incus recipes

Build from the **release tarball**, not the git tree: upstream bundles the
dependency tree plus local copies of `libraft` and `libcowsql`, which removes two
recipes that would otherwise be painful (cowsql is a Canonical-dqlite fork; raft
is a consensus library with its own build system).

Requirements, from upstream's install docs:

| Need | Status in catalog |
|---|---|
| Go ≥ **1.25.12** (module `github.com/lxc/incus/v7`) | **new** — Layer 1 |
| `liblxc` ≥ **5.0.0** + headers | **new** — the other Go-free C recipe |
| `libcap`, `libacl`, `libattr`, `libudev`, `libuv` | **new** — 5 recipes |
| `libsqlite3`, `liblz4`, `xz`, `zstd` | have (`sqlite`, `lz4`, `xz`, `zstd`) |
| `make`, `pkg-config`, `libtool`, `autoconf`/`automake` | have |
| `libraft`, `libcowsql` | bundled in the release tarball |
| runtime helpers: `dnsmasq`, `rsync`, `squashfs-tools`, `tar`, `tcl`, `nftables` | mixed — see below |

So roughly **eight new C recipes plus `go` and `incus`** — a real but bounded
chunk, and every one of those C libraries is independently useful.

### Incus, not LXD

Package **Incus only**, and say so explicitly:

- **Incus is Apache-2.0.** LXD was relicensed to **AGPLv3 under a Canonical CLA**
  in December 2023, and is now a mix of AGPLv3 (Canonical's contributions) and
  Apache-2.0 (community contributions) **with no per-file metadata saying which
  is which**. For a catalog that redistributes built binaries, that is an
  unresolvable provenance question, and AGPL network-service terms are a decision
  no packaging roadmap should make silently.
- Debian is deprecating LXD in favour of Incus for trixie, so Incus is also the
  better default target.
- The existing `lxd` capability probe stays regardless — detecting a runtime we
  do not ship costs nothing and keeps existing fleet routing working.

## The honest limit: the capability narrows, it does not disappear

This is the part to get right before writing any recipe. **A hermetic Incus does
not eliminate the capability**, because the parts a package cannot ship are
exactly the parts the capability attests:

- **The daemon is Linux-only.** Upstream is unambiguous: *"The Incus daemon only
  works on Linux."* Clients exist for macOS and Windows. So a `depends` entry
  could never be platform-neutral.
- **It needs contiguous sub-UID/sub-GID ranges of ≥ 10M for root.** That is host
  configuration in `/etc/subuid` and `/etc/subgid` — no package can provide it.
  Note the existing probe *already* tests precisely this for classic LXC
  (`TestClassicLxcDelegation`), which is the strongest evidence the substrate
  check is the durable half.
- **It needs kernel namespaces and cgroup delegation**, and `nftables` programs
  the kernel's packet filter.
- **It needs a running privileged daemon** on a socket the build account can open.

So the model should split rather than flip:

```yaml
# what cvcpkg can ship — pinned, versioned, reproducible
depends:
  host_tools: [incus]

# what only the host can attest — narrowed to the substrate
test:
  vm:
    requires_capabilities: [linux-containers]
```

The capability stops meaning *"did an admin install and configure a container
manager"* and starts meaning *"can this kernel host containers"*. That is a
strictly better contract: it removes the half that can be packaged and keeps the
half that genuinely cannot, and the remaining check is one a builder either
satisfies structurally or does not.

## Payoff

- **Reproducible VM tests.** Every builder runs the same pinned Incus, so a
  `test.vm` pass means the same thing fleet-wide.
- **Builder provisioning collapses to `cvcpkg install incus`** plus a subuid/kernel
  check the fleet can report on — replacing an undocumented apt-and-group ritual
  that is currently tribal knowledge.
- **Go unlocks more than Incus.** `verlihub`'s TLS proxy (Phase 20) stops being
  "off by default", and a large ecosystem of Go CLI tooling becomes packageable
  at all.
- **Feeds Phase 23** (cvcpkg as a build & configuration-management system), where
  containers are the natural build substrate rather than an optional extra.

## Blocker: `host_tools` cannot express a version

Everything above assumes a recipe can say *"build against Incus 6.x"*. It cannot,
and the gap is wider than it first looks. Pinning is the entire point of this
work — an unpinned hermetic Incus is just the capability model with extra steps —
so this is a prerequisite, not a nice-to-have.

**The schema is the cheap half.** `depends.host_tools` is a bare
`array[string]`, while `depends.build` and `depends.runtime` accept a `dep_entry`
(`name`, `version`, `platforms`). Curiously the *code* already reads dict entries
— `builder.py::_dep_names` and `validation.py` both branch on
`isinstance(entry, dict)` — so only the schema rejects them, and `cvcpkg validate`
would today fail a recipe the builder would happily accept. Letting `host_tools`
take a `dep_entry` also buys `platforms`, which a Linux-only `incus` needs.

**There is a latent bug next door.** In `builder.py::_dep_names`, the
build/runtime loop skips entries whose `platforms` exclude the target; the
`host_tools` loop immediately beneath it does not. A dict host tool carrying
`platforms` would be requested on every platform.

**Enforcement is the substantive half.** Phase 1.5 §8 records dependency version
ranges as done, and they are — on the **install** path: recipe `depends.runtime`
→ manifest → resolver `satisfies()`, with constraint intersection and conflict
rejection. Host tools are on the **build** path, which has no equivalent. The
manifest carries `depends.runtime` only (host tools are stripped on install), so
a host tool resolves purely **by name** and the build gets whatever version that
builder last built. Two builders can satisfy the same recipe with different
Incus versions and nothing notices — which is precisely the non-determinism this
document set out to remove.

**And a pin needs provenance to be verifiable.** If the artifact does not record
which host tool built it, the pin cannot be audited after the fact — the same
argument `hermetic-native-toolchain.md` makes for the compiler, and a natural fit
with Phase 16.

## Sequencing

0. **`host_tools` version constraints** — the prerequisite above. Schema plus the
   platform-filter fix is small; build-time constraint enforcement is the real
   work and gates any meaningful pin.
1. **Layer 1 (`go` recipe) is independent** — it needs nothing from Phase 7.5 and
   can land whenever. It immediately unblocks the Phase 20 verlihub note.
2. **`liblxc` + the five C libraries** are ordinary recipes, parallel to Layer 1.
3. **`incus`** needs both, and is only *fully* hermetic once
   `hermetic-native-toolchain.md` pins `CC`/`CXX` (cgo).
4. **The capability split** (`incus` → `linux-containers`) is a separate,
   behaviour-visible change to `platform.py` and the recipe schema; do it last,
   once something can actually declare `host_tools: [incus]`.

## Open questions

- Is `linux-containers` the right capability name, or should the substrate check
  decompose further (`subuid-delegation`, `cgroup-delegation`, `kernel-netns`)?
  The classic-LXC probe already distinguishes delegation from presence, so the
  finer split may already be half-built.
- Does `incus` belong in `host_tools` (build/test tooling), or is a VM-test
  substrate a third kind of dependency alongside build and runtime?
- Should the client/daemon split into separate packages, so macOS and Windows can
  install an `incus-client` that talks to a remote Linux daemon?

## Related

- [`hermetic-native-toolchain.md`](hermetic-native-toolchain.md) — the same
  argument for `CC`/`CXX`; cgo makes Incus depend on its outcome.
- [`new-dependencies.md`](new-dependencies.md) §1 — the bundled-Emscripten-SDK
  precedent for shipping a pinned upstream toolchain tarball.
- `CVCPKG-ROADMAP.md` Phase 7 / 7.5 — hermetic Python and "Our GHC, Our ABI".
- `CVCPKG-ROADMAP.md` Phase 10 — the capability/peer-provider model this narrows.
- `CVCPKG-ROADMAP.md` Phase 20 — the deferred verlihub TLS proxy that Layer 1
  unblocks.
