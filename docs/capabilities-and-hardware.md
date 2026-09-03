# Capabilities and hardware-aware selection

A *capability* is a named, yes/no host feature — `cuda`, `incus`, `lxd`, `lxc`,
plus the glibc floor names on linux builders. Capabilities drive two decisions,
with the same recipe key (`requires_capabilities:`) feeding both:

1. **Install-time selection** — the resolver only picks packages the current
   host can actually use, and prefers the most hardware-specific provider of a
   virtual slot.
2. **Build-time routing** — the scheduler only dispatches a job to a builder
   that advertises every capability the recipe requires.

## Virtual slots and install-time peer selection

A recipe can declare `provides:` — virtual slot names it fills (see
[mutual-exclusion.md](mutual-exclusion.md) for the exclusivity side). A
requested name `N` resolves against the union of bundles literally named `N`
and bundles whose `provides:` lists `N`. That is how a CPU/GPU pair works:

```yaml
# recipes/torch-cp311-cuda/recipe.yaml
provides: ["torch-cp311"]
requires_capabilities: ["cuda"]
```

`cvcpkg install torch-cp311` then considers both `torch-cp311` (CPU) and
`torch-cp311-cuda` as candidates. Selection is two steps:

- **Filter**: candidates whose `requires_capabilities` are not a subset of the
  host's capability set are dropped. On a machine without CUDA, the `-cuda`
  peer is simply never in the running.
- **Rank**: survivors keep the usual version/`recommended:` ordering, then a
  stable re-sort floats candidates requiring *more* capabilities to the front.
  So on a CUDA host, `torch-cp311-cuda` beats the plain build; elsewhere the
  CPU build wins by default.

If a name is requested and *every* candidate was filtered out, the resolver
fails with the missing capabilities spelled out:

```
cannot satisfy requirements:
  no provider for 'torch-cp311-cuda' meets required capabilities (host is missing: cuda)
```

## How the host's capabilities are determined

`cvcpkg.platform.host_capabilities()` probes once per process and caches the
result. Every probe answers "can this user drive it right now", not "is a
binary installed", and each subprocess probe is capped at 5 seconds so a wedged
daemon degrades to "capability absent" instead of hanging the CLI.

| capability | probe |
|---|---|
| `cuda` | `nvcc` on `PATH`, or `$CUDA_PATH`/`$CUDA_HOME/bin/nvcc(.exe)`. Deliberately **not** `nvidia-smi` or a loadable libcuda — those ship with the driver, and the question is "can this host *compile* CUDA", not "does it have a GPU". |
| `incus` | `incus info` exits 0 — proves the client exists, incusd is running, and this user may open its socket. |
| `lxd` | `lxc info` exits 0 **and** the dump reports `server: lxd` (rejects an `lxc` compatibility shim fronting Incus); unrecognised output falls back to whether LXD's own daemon binary is installed. |
| `lxc` | classic LXC: `lxc-create`, `lxc-start` and `lxc-ls` all present, `lxc-ls -1` exits 0, and a non-root user holds subuid/subgid delegation. |

`incus`, `lxd` and `lxc` are three distinct capabilities on purpose: the REST
CLIs and the classic `lxc-*` tools share no command surface, so a job's harness
targets exactly one of them.

### The `CVCPKG_CAPABILITIES` override

When set, `CVCPKG_CAPABILITIES` is authoritative and probing is skipped
entirely — its comma-separated value is the capability set, and an empty
string means "no capabilities". This is how CI and tests inject capabilities,
and the escape hatch for a misconfigured host:

```sh
CVCPKG_CAPABILITIES=cuda cvcpkg install torch-cp311   # force the -cuda peer
CVCPKG_CAPABILITIES=     cvcpkg install torch-cp311   # force the CPU peer
```

## Builder-side routing

`cvcpkg builds submit-dag` copies each recipe's top-level
`requires_capabilities` onto its build jobs (linux jobs also pick up a glibc
floor capability unless the recipe sets `prebuilt_payload: true`). The
scheduler then dispatches a job only to a builder advertising **all** of the
job's required capabilities. Advertising a capability never *reserves* a
builder for capability jobs — it only adds eligibility.

A builder's advertised set is the same host probe, plus explicit flags:

```sh
cvcpkg builder run --capability cuda            # merged with auto-detection
cvcpkg builder run --no-auto-capabilities \
                   --capability incus           # advertise ONLY what is listed
```

`--no-auto-capabilities` advertises only the explicit `--capability` flags —
it skips `host_capabilities()` entirely, which is the *only* reader of
`CVCPKG_CAPABILITIES` in the codebase, so it also bypasses that override (the
env var replaces probing only when auto-detection is enabled) and glibc-floor
advertising. On linux, builders
additionally advertise the glibc floors they can produce for (a 2.35 machine
satisfies the 2.35, 2.38 and 2.39 floors); those names are builder-only and
never part of the install-side set.

### Held jobs and `unschedulable`

A job whose capabilities no *online* builder currently satisfies stays
`pending` — a capable builder registering or coming back picks it up. But a job
that no *registered* builder (online or offline) can ever serve is reaped after
a grace period (`CVCPKG_UNSCHEDULABLE_TTL`, default 1800 s): it is marked
`unschedulable`, its downstream dependents are cancelled, and a
`build.unschedulable` webhook event fires with an error like:

```
no registered builder for linux/x86_64 with capability cuda
```

The check is *joint*: one builder must cover the job's platform/arch **and**
advertise every required capability — a CUDA builder on a different platform
does not make a job schedulable. `submit-dag` runs the same check up front
against `/v1/builders` and skips doomed combos with a
"no registered builder advertises the required capability" message; pass
`--allow-unschedulable` to submit them anyway and let the server's reaper
decide.

One related-but-different key: an image recipe's `vm_test` block has its *own*
`requires_capabilities` (typically `[incus]`). That gates only the in-VM test —
it is skipped with a reason on a builder without a hypervisor, never failed —
and it must not gate the install, since carrying an image to a host with no
hypervisor is the point.

## Disk-aware scheduling

`build.min_disk_gb` is the quantitative sibling of a capability — declared per
recipe, for genuinely heavy builds only (a VM image, a full LLVM tree):

```yaml
build:
  min_disk_gb: 35   # e.g. haiku-image
```

Builders advertise `free_disk_gb`, measured on the **work volume** (the
directory job trees are created in; the system temp dir when `--work-dir` is
unset), truncated to whole GiB so 34.9 free never satisfies a 35 GiB need. It
is seeded at registration and re-measured on every heartbeat, because unlike a
capability it is a measurement one job can move by tens of GiB. Within a single
scheduler tick the figure is also debited per dispatched job, so two 35 GiB
jobs cannot both pass against the same 40 GiB.

The filter fails **open** on unknown: a builder advertising no figure — an
agent older than the field, or one started with
`cvcpkg builder run --no-free-disk` (for hosts where `statvfs` lies, e.g. a
bind-mounted or network work dir) — matches every job, exactly like the rest of
a mixed-version fleet. And a job nothing currently has room for is *never*
reaped as unschedulable: free disk is transient, so the job stays `pending`
while `submit-dag` is where the operator is told up front
("no registered builder has enough free disk").
