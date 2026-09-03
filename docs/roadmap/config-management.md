# Roadmap: cvcpkg as a build & configuration-management system

> Extracted from `CVCPKG-ROADMAP.md` Phase 23 ("cvcpkg as a Build &
> Configuration-Management System") during the docs consolidation.
> The tracking checkboxes remain in [CVCPKG-ROADMAP.md](CVCPKG-ROADMAP.md).

Status: **Not started** (planned; required before the PyPI release).
[`README.md`](../../README.md) carries a user-facing summary of this design
("cvcpkg as a build & configuration system") and labels it planned there
too — this document is the full design it points at.

This phase depends on the per-prefix state database
(`share/cvcpkg/prefix.db`) and a DB-backed `uninstall` command from the
CLI UX phase — see [cli-ux-recipe-first.md](cli-ux-recipe-first.md).
`prefix.db` does not exist yet; `cvcpkg uninstall` shipped ahead of it
(#522) as an archive-derived command, which is enough to build on but not
the DB-backed version this phase's state teardown needs (see
[Prerequisites](#prerequisites--what-exists-today)).

---

## The idea

cvcpkg is not only for publishing packages. With a recipe set it should
work as a **general build system and configuration-management tool**:
installing a recipe applies state and runs its dependency recipes;
uninstalling tears it down; a machine's configuration is a **dependency
graph of recipes**. The pitch is "SaltStack, but cleaner — because it is
integrated into a holistic, cross-platform, content-addressed package
manager instead of bolted onto one."

The phase formalizes that pattern, plus **BYO (bring-your-own)** recipes
for assets cvcpkg cannot legally redistribute. It is grounded in a survey
of the field (researched 2026-07-18); the honest failure modes are stated,
not glossed.

## Positioning — and the honest limits

Exactly two architectures unify packaging and configuration, and they made
opposite bets:

| Camp | Examples | Bet |
|------|----------|-----|
| **A — functional/immutable** | Nix/NixOS, Guix | The system is a pure function of its inputs; config files and services are *build outputs*; install paths are content-addressed; rollback is a symlink swap; removal is reference-counted GC. Gets atomicity, generations, and real rollback — paid for with a purity model and a learning curve that would violate cvcpkg's "a graduate student understands it in an afternoon" principle. |
| **B — resource model over a mutable OS** | PowerShell DSC, `winget configure`, Portage | Declared resources with idempotency checks applied to a mutable host. Microsoft's `winget configure` is literally this pitch already shipped. |

cvcpkg's mutable install prefixes plus arbitrary `build.sh`/`build.ps1`
sit structurally in **camp B**, so camp B's failure modes are the ones
cvcpkg will actually hit. We adopt camp B, with the best camp-A ideas
grafted on where the prefix boundary makes them cheap (per-prefix
generations; the content-addressed cache already re-materializes any prior
state offline).

**Four walls every camp-B tool hits, stated honestly:**

1. **Arbitrary scripts are not idempotent.** Being "in a package manager"
   does not fix this; even `dpkg`/`rpm` maintainer scripts are *required*
   to be idempotent and are a notorious breakage source.
2. **Teardown is authoritative inside the prefix, best-effort outside.**
   Files cvcpkg tracks, it can remove; state it reaches out to mutate
   (services, registry, `/etc`, the desktop) it can only revert with a
   hand-written, drift-prone inverse — exactly as Salt/Chef/Ansible do.
   Even NixOS does not revert side effects (a DB migration survives a
   rollback), and is deprecating its own unstructured activation scripts
   for being "unsandboxed, un-rolled-back, order-dependent."
3. **Apply is on-demand and non-atomic**, not a continuous enforcement
   daemon. cvcpkg will be Ansible-shaped (corrects drift when you run it),
   not Puppet-shaped (self-heals every 30 min). A half-failed apply leaves
   a half-configured host — `winget configure` documents exactly this. We
   do not market it as enforcement.
4. **A single topological pass need not converge** (Salt's classic two-run
   problem), and cross-referencing state recipes surfaces dependency
   cycles as hard errors.

## The state contract (Get / Test / Set)

The load-bearing lesson from every tool that survived: **idempotency is a
per-resource contract, never a free property of the engine.** DSC states
it best as a wire-level `Get`/`Test`/`Set` triple — the engine runs `Test`
first and calls `Set` only when non-compliant.

- **Typed `state:` resources** in the recipe — `file`, `symlink`,
  `template`, `env`, `service`, `registry-key` (Windows), `user` — each
  with built-in Get/Test/Set, so the common 90% is declarative,
  verifiable, and **auto-reversible**: capture the prior value before
  `Set`, store it in `prefix.db`, replay on uninstall (the MDM
  removal-semantics model).
- **A labeled imperative escape hatch** — a recipe-supplied `script:` +
  **`teardown:`** slot for anything the built-ins do not cover. Recipes
  with a `script:` effect but no `teardown:` are **labeled non-revertible
  in status output** (NixOS-style honesty about the imperative 10%); apply
  scripts are contractually required to be idempotent and are re-checked
  via content-hash (`run_onchange` semantics).
- **Three modes, no daemon** — `cvcpkg check` (audit/report-only, per the
  DSC "Audit" mode that survived two Microsoft generations), `cvcpkg
  apply` (apply + monitor), and autocorrect left to an *external* loop
  (cron/CI). Explicitly no resident agent and no pull server — DSC's LCM
  and pull server are being retired; ship an honest CLI with
  machine-readable exit codes and let a scheduler own the loop.
- **On Windows, delegate rather than reimplement** — DSC v3 resources are
  now an open executable-plus-JSON-manifest protocol; a `state:` entry can
  invoke them instead of cvcpkg re-growing the entire Registry/Service
  resource zoo.
- **Never enforce fields you did not declare** — drift correction touches
  only resources in the applied generation's manifest (the Kubernetes
  field-ownership / ostree `/etc`-merge lesson); user modifications
  outside the declared set are sacred.

## BYO — bring-your-own, non-redistributable assets

The cleanest prior art is Gentoo, because it splits into *separate axes*
what every other tool conflates. A recipe should be able to point at an
asset the archive cannot host (a licensed installer, retail game data, a
client's proprietary blob) and have the *user* supply it:

- **`source.type: byo`** (and/or a `restrict: [fetch, mirror]` axis
  split): declares the artifact **cannot be rehosted** *and* **cannot be
  auto-fetched** — two distinct permissions, as Gentoo's
  `RESTRICT="mirror"` vs `RESTRICT="fetch"` proves.
- **A `pkg_nofetch`-style instructions phase** — when the asset is
  missing, print machine-authored acquisition instructions *at the moment
  it is needed*, not buried in a wiki.
- **Verification invariant to provenance** — a mandatory, pre-published
  `sha256` (+ `size` as a cheap second signal) checked identically whether
  the file was hand-dropped or fetched; there is **no "skip because the
  user supplied it."** Steal `game-data-packager`'s trick of checksumming
  the **extracted assets, not the container**, so a CD, a GOG installer,
  and a Steam depot (three bitstreams → one asset set) all verify.
- **A search path** — `~/.cvcpkg/distfiles`, a `CVCPKG_DISTFILES` env
  var, plus explicit `--asset path=…`. This maps directly onto the
  existing airgap / licensed-client-host / patch-recipe workflow.
- **License-acceptance gating** — a `license.eula: true` axis requiring
  explicit opt-in (Gentoo `ACCEPT_LICENSE` / `@EULA`), so a EULA'd asset
  never installs silently. Composes with the `redistributable: false`
  flag from the first-party/featured-recipes phase, which hard-blocks
  *publishing* the built artifact.

## Security — the config channel is a C2 channel

Once install applies state, recipes execute arbitrary scripts, often as
root. The defining incident is SaltStack CVE-2020-11651/-11652 (2020):
two auth-bypass bugs turned exposed salt-masters into fleet-wide remote
root within ~72 hours — LineageOS, Ghost, and a DigiCert CT-log signing
key among the fallout. The structural lesson is permanent: **a config
master is a pre-authorized root-execution channel to every node; its auth
boundary is a fleet-wide C2 boundary.** Supply-chain history adds the rest
(event-stream's maintainer-trust transfer, Codecov's key-in-a-public-
artifact, the xz backdoor where *the built tarball ≠ the audited repo*,
Birsan's dependency confusion).

- **Signing ≠ safety, and static Ed25519 ≠ survivable compromise.** cvcpkg
  has the *identity* half (see [Prerequisites](#prerequisites--what-exists-today)).
  It lacks (a) **TUF-style rotation / thresholds / expiry** so a stolen
  key is not game-over — note PyPI *abandoned* forcing developer key
  management in favor of OIDC Trusted Publishers because key custody "did
  not survive contact with real maintainers" — and (b) a
  **client-verifiable transparency log**: the existing chained-hash audit
  log is server-side, so a compromised server can push unlogged state.
  Sigstore's Rekor is the shape to match (client checks an inclusion proof
  before apply).
- **Mandatory hash pinning in apply-mode** — reject unpinned
  fetch-at-install; TOFU is not enough (the Codecov/xz shape).
- **Namespace-scoped resolution, never "highest version wins across trust
  domains"** — private/org names must never be shadowed by public ones
  (the dependency-confusion antidote; composes with the federation-
  topology phase).
- **Sandbox everything up to apply; treat apply as privileged.**
  Fetch / build / template-render can be hermetic (no net, restricted
  FS — the Nix/Bazel ceiling); a root `apply`/`teardown` cannot be
  sandboxed by definition. Confine it with OS-level MAC
  (seccomp/AppArmor) around a *declared surface manifest* ("this recipe
  may touch `/etc`, install unit X, open port N"), and make fleet-wide
  state changes a **two-person-reviewed, transparency-logged** operation
  (the GitOps model: signed, approved manifest in a repo).
- **Secrets are references, never embedded** — resolved at apply-time from
  an external store (SOPS/age or Vault), never baked into a recipe,
  lockfile, cache, or the audit log; no plaintext temp files during apply
  (the Ansible-Vault CVE shape).
- **Integrity-protect local state** — the lockfile and
  `prefix.db`/`local.db` decide what uninstall reverts and what reconcile
  re-applies; unprotected, a local attacker retargets teardown or hides an
  install. Sign/HMAC the local state; detect out-of-band edits.

## Longevity — will a recipe fleet still be healthy in five years?

The instructive finding: three of the four major CM ecosystems were
damaged by *ownership events*, not technical failure — Salt (Broadcom
gutted maintenance; repo killed on a week's notice), Chef (binaries went
proprietary → the Cinc community rebuild), Puppet (Perforce moved to
private repos + a node-count EULA → the OpenVox fork). The recipes
survived; the **engine and distribution channel** rotted.

- cvcpkg **owns its engine**, which removes vendor-rot risk but transfers
  the whole burden to cvcpkg's own compatibility discipline.
- **A written recipe-schema versioning + dated-deprecation policy**
  (Ansible's ~6-month deprecation floor is the best-in-class model;
  Salt's cliff-edge is the anti-model). A survival trait, not
  bureaucracy — and old lockfiles must stay installable from the
  content-addressed store (the property that let Cinc/OpenVox exist).
- **Shrink the shell surface over time** — grow typed resources for the
  common cases (each with generated verify/teardown), keep shell as the
  *labeled* escape hatch. Arbitrary shell is non-idempotent, statically
  unverifiable, and irreversible; every CM tool made this same
  shell→typed migration.
- **CI the idempotency contract** — run `apply` twice on a clean image
  (second run must be a no-op) **and** once on a *dirty* aged snapshot
  (the "worked in staging, diverged in prod" failure class is structural
  for mutate-in-place CM; Knight Capital is the extreme form). cvcpkg
  recipes are already CI-built, so the marginal cost is low — a genuine
  edge over Ansible-without-Molecule.

> **Market note.** The industry moved from mutate-in-place CM toward
> immutable images + orchestration — but CM persists exactly where
> cvcpkg's users live: HPC, scientific computing, bare-metal, air-gapped,
> licensed, and lab/workstation fleets — long-lived, heterogeneous
> machines that cannot be treated as cattle. The winning pattern in that
> niche is *hybrid*: a known base plus a narrow, verifiable delta. So
> cvcpkg's CM mode should be "install content-addressed artifacts + a
> small declared config delta," not "run arbitrary mutation scripts" —
> the closer the delta is to package semantics (files with owners and
> hashes), the more the fleet behaves immutably even while technically
> mutated in place.

## Forensics — a genuinely superior paper trail

When a production system breaks or is breached, most CM tools provide a
poor record: Salt's job cache defaults to **24-hour** retention, PuppetDB's
reports to **14 days** — against a 2024–2025 median attacker dwell time of
11–14 days. `ansible-playbook` records nothing centrally. And none of the
mainstream CM tools makes its record tamper-evident (their history sits in
ordinary mutable databases). `rpm -Va` trusts a local mutable DB an
attacker with root simply edits. cvcpkg already owns the two hardest
prerequisites — a signing infrastructure and a server-side chained log —
so a genuinely better story is within reach. This is a real
differentiator; lean into it.

- **Local append-only transaction journal (the keystone)** — one
  hash-chained JSONL per machine: every install / uninstall / apply /
  revert / reconcile appends `{seq, wall+monotonic time, uid/euid +
  SUDO_USER + SSH_CONNECTION (the "by whom, from where" local CM logs
  never capture), recipe name/version/hash, artifact sha256s, per-file
  pre/post hashes, exit status, prev-record-hash}` (the CloudTrail
  digest-chain pattern, machine-scoped). `fsync` on append, never
  rewrite, **retain by default forever** — explicitly contrasted with
  Salt's 24 h against a 14-day dwell time.
- **Cross-anchor the local chain to the server audit log (and vice
  versa)** — on each server contact the client submits its chain head;
  the server appends it to its chained log and returns *its* head, which
  the client records next. Rewriting a machine's history then requires
  rewriting the server's log, which every other client's checkpoint makes
  detectable — the Certificate-Transparency / Rekor witness pattern,
  built from parts cvcpkg already has. For air-gapped/BYO hosts: export
  signed checkpoints with each patch-recipe transfer, and optionally
  FSS-style forward-secure sealing of journal segments.
- **Per-package installed-file manifests, signed** — the prefix-state
  file-tracking table (see [cli-ux-recipe-first.md](cli-ux-recipe-first.md)),
  but with the manifest **embedded in the Ed25519-signed package** and
  verified against *that* rather than a local mutable DB (structurally
  immune to the `/var/lib/rpm` tamper problem). Enables uninstall,
  ownership queries, drift detection, and NSRL-style known-good filtering
  at file granularity ("these 40,000 hashes are files we shipped; these 3
  are not").
- **Generation snapshots that recover content, not just hashes** — every
  CM transaction is an immutable generation (resolved state set + recipe
  graph); **store the pre-mutation content of every overwritten file in
  the existing content-addressed cache** (they are just blobs). This
  out-does Nix (recoverable before-state, not merely a logged hash) *and*
  covers the mutable system state Nix/ostree refuse to manage. Gives
  `cvcpkg diff --gen 41 --gen 47` and makes revert "re-apply the previous
  generation."
- **`local.db` is a rebuildable index, not the source of truth** —
  derived from journal + manifests, reconstructible via `cvcpkg
  rebuild-index`; store the journal chain head in it so index/journal
  divergence is itself a detection signal.
- **DFIR-friendly output** — document the journal format and expose it as
  an osquery table and a plaso/Timesketch-ingestible timeline, so
  investigators meet cvcpkg inside the tooling they already use. *Honest
  limit stated in the docs:* none of this constrains a root attacker
  *going forward* — only forwarding/anchoring frequency bounds the
  rewriteable window — but the pre-compromise record is provably intact
  and post-compromise divergence is detectable, which no mainstream CM
  tool offers.

## Worked recipe examples

Illustrative sketches — the schema is indicative, not final — showing the
initialize-proprietary-software-then-legally-modify pattern. **Every
recipe declares its license and redistributability explicitly.**

### `quake-data-retail` — a BYO recipe

The user supplies their own purchased game data; cvcpkg never fetches or
hosts it:

```yaml
recipe: { name: quake-data-retail, upstream_version: "1.0", cvc_revision: 1 }
license: "id Software EULA (proprietary)"     # NOT ours to license
redistributable: false                        # hard-blocks publishing the artifact
source:
  type: byo                                   # user-supplied
  restrict: [fetch, mirror]                   # cannot auto-fetch, cannot rehost
  asset: { file: "pak0.pak", sha256: "<retail pak0 digest>", size: 18689235 }
license_gate: { eula: true }                  # must be explicitly accepted
nofetch: |                                    # printed when the asset is missing
  Copy pak0.pak and pak1.pak from your purchased Quake (Steam/GOG/CD)
  into ~/.cvcpkg/distfiles/ , or pass --asset pak0.pak=/path/to/pak0.pak
package: { files: ["id1/*.pak"] }
```

### `arcane-dimensions` — explicitly-licensed, fetch-at-install

The map pack grants electronic redistribution but only *unaltered*, so
cvcpkg fetches the byte-identical original and does not repackage it:

```yaml
recipe: { name: arcane-dimensions, upstream_version: "1.8", cvc_revision: 1 }
license: "AD readme grant: electronic distribution, unaltered, no charge, readme included"
redistributable: false            # host the recipe, not the bytes
source:
  type: byo
  restrict: [mirror]              # may auto-fetch, may NOT rehost
  asset: { url: "https://www.simonoc.com/files/ad/ad_v1_8_final.zip",
           sha256: "<ad zip digest>" }
package: { files: ["**"] }        # installed byte-identical, readme included
```

### `lab-quake-server` — a state/config recipe composing the above

Initialize proprietary/licensed data, then legally layer a first-party
config on top:

```yaml
recipe: { name: lab-quake-server, upstream_version: "1.0", cvc_revision: 1 }
license: "MIT (CyberPC Angel, LLC) — our config only; deps carry their own"
depends:
  runtime: [ fteqw-sv, ktx, quake-data-retail ]   # engine + mod + BYO data
state:
  - service: { name: "quakeworld", exec: "${CVC_PREFIX}/bin/fteqw-sv +exec server.cfg" }
  - template: { src: "server.cfg.j2", dest: "${CVC_PREFIX}/id1/server.cfg" }   # OUR config
  - file: { path: "/etc/systemd/system/qw.service", mode: "0644", from: "qw.service" }
teardown: |                        # explicit inverse for the out-of-prefix unit
  systemctl disable --now qw.service 2>/dev/null || true
  rm -f /etc/systemd/system/qw.service
```

Installing `lab-quake-server` triggers its dependency recipes (engine,
mod, and the BYO data whose EULA the operator accepted), then applies the
typed `state:` resources (auto-reverted on uninstall) and the one
labeled-imperative systemd unit (reverted by its `teardown:`).

## Prerequisites — what exists today

Verified against the code as of the docs consolidation (2026-08):

**Already shipped:**

- **Ed25519 signing** — `src/cvcpkg/signing.py`: keypair generation,
  detached base64url signatures, fingerprints, and a trusted-keys dir;
  CLI surface is `cvcpkg key generate|list|import|export`, `cvcpkg sign`,
  and `cvcpkg verify-sig`. The installer verifies catalog-carried
  signatures at install time (opt-in verify, strict require mode).
- **Server-side hash-chained audit log** — `src/cvcpkg/server/audit.py`:
  each entry carries the SHA-256 of the previous entry (`prev_sha256`)
  plus a `verify_chain()` walk; on the DB backend, mutation and audit row
  commit as one transaction under an append lock so the chain cannot
  fork. This is the "server-side, therefore not client-verifiable" log
  the transparency-log item above upgrades.
- **Org namespaces** — `src/cvcpkg/orgs.py` slug validation and the
  `/v1/orgs` server API (create/list/detail/members); org-qualified
  package names (`myorg/zlib`) are honored in install resolution and
  catalog lookups. This is the substrate for namespace-scoped
  resolution.
- **Signed-bundle file lists** — every bundle's `manifest.yaml` must
  carry a non-empty `files:` list (`contents_block` in
  `src/cvcpkg/schemas/manifest-schema.yaml`; parsed into
  `BundleManifest.files`), and the manifest ships inside the archive the
  detached Ed25519 signature covers — the raw material for the signed
  installed-file manifests above.

**Not yet built:**

- **`cvcpkg uninstall` exists (#522) but not as this phase needs it.** It
  removes files derived from the bundle archive the lockfile names, with
  `--cascade`/`--dry-run` and dependent-aware refusal — but there is no
  `check` or `apply`, no state-resource concept for it to tear down, no
  teardown hook, and no drift detection on modified files (the closest
  thing today, `cvcpkg verify`, checks prefix integrity against the
  lockfile, not per-resource state). Those all wait on `prefix.db`.
- **No per-prefix state database** — nothing like `prefix.db`/`local.db`
  exists in the client; installed-file tracking, the ops journal, and
  generations all land with the CLI UX phase
  ([cli-ux-recipe-first.md](cli-ux-recipe-first.md)).
- **No `state:`, `source.type: byo`, `restrict:`, `license_gate:`, or
  `redistributable:` fields** in `recipe-schema.yaml` today — the recipe
  schema currently has only a `license` SPDX-expression string — and no
  `CVCPKG_DISTFILES` env var in `src/cvcpkg/config.py`.

## Delivery

- **Integration tests exemplifying the pattern** — alongside the existing
  `tests/integration/test_source_recipe_workflow.py`,
  `test_platform_any.py`, and `TestLocalBuildMode`
  (`test_source_fallback.py`): a fully-local (no-server)
  build→install→**apply**→verify→**uninstall**→verify-clean lifecycle; an
  idempotency test (apply twice → second run is a no-op; apply on a dirty
  snapshot); a BYO test (missing asset prints instructions and fails;
  supplied asset with correct hash succeeds; wrong hash is rejected); and
  a teardown test (uninstall reverts both typed state and the
  labeled-imperative `teardown:`).
- **README section** — done: [`README.md`](../../README.md) has a
  "cvcpkg as a build & configuration system" section covering the
  recipes-as-state model, the Get/Test/Set contract,
  `check`/`apply`/`uninstall`, BYO assets, and — stated plainly — the
  four honest limits above, so the docs never over-promise
  reconciliation, atomicity, or script reversibility.
