# Image packages

An **image package** ships a guest disk — a VM image — instead of headers and
libraries. `haiku-image` is the first; `freebsd-image`, `netbsd-image` and
friends follow the same shape.

This document is the contract between an image recipe and the provisioning
script that consumes it. It describes **the packaging layer** — the layout, the
descriptor, the `cvcpkg image` surface, the `test.vm` hook — all of which are
implemented and unit-tested.

> **What is NOT claimed here.** No image package has been published, and
> `haiku-image`, the worked example throughout this document, **does not
> currently boot** — its anyboot partition table is truncated for any image
> ≥ 4 GiB, its SSH-key injection has never landed a key, and nothing in it
> started `sshd`. Repair is in flight on a separate branch. Every example
> below is a description of the contract, not a transcript of a working
> deployment: where a value is shown (`disk_bus: nvme`, `ssh_pubkey_baked`,
> a `qm`/`incus` command line), read
> `recipes/haiku-image/README-import.md`'s status block for what has actually
> been observed. The `test.vm` hook exists precisely because this document
> could not otherwise tell you that.

## The layout

Every image package installs into **one directory named after the package**,
with **role-based** filenames:

```
<prefix>/share/<package-name>/image.yaml            canonical descriptor
<prefix>/share/<package-name>/image.env             POSIX KEY=value shim
<prefix>/share/<package-name>/disk.qcow2            the payload
<prefix>/share/<package-name>/SHA256SUMS            `sha256sum -c` format
<prefix>/share/<package-name>/README.md             import guide
<prefix>/share/<package-name>/incus/metadata.yaml   importer metadata
<prefix>/share/<package-name>/incus/metadata.tar.xz what `incus image import` takes
```

Nothing at the prefix root. Nothing inside `share/libcvc-deps/`.

Two properties do the work:

* **The directory is the package name.** Package names are unique in the
  catalog keyspace, so an operator can co-install `haiku-image`,
  `freebsd-image` and `netbsd-image` and they cannot clobber each other —
  even though all three ship a file called `README.md`.
  *This is the bug that motivated the layout*: `haiku-image` used to stage
  `metadata.yaml` and `README-import.md` at the root of `CVC_INSTALL_DIR`,
  and cvcpkg merges a staged tree into the prefix preserving relative paths,
  so both landed at `$PREFIX/` under names that describe no particular guest.
* **The filenames are role-based.** `disk.qcow2`, not
  `haiku-builder-x86_64.qcow2`. A consumer derives the path from the package
  name alone — no version, no guest arch, no upstream naming knowledge.
  Guest axes live in the package NAME and in `image.yaml`, never in a
  filename:

  ```
  <guest-os>[-<variant>]-image[-<guest-arch>]
  haiku-image   freebsd-image   freebsd-image-arm64
  ```

`cvcpkg image export` restores a human-meaningful name on the way out
(`haiku-image-1.0.0-beta.5+cvc.1.qcow2`), which is where one belongs.

## Finding an installed image

### Preferred: the `cvcpkg image` group

In core, not an extra — the consumer is a `/bin/sh` script on a bare cluster
node. Discovery is a glob over `<prefix>/share/*/image.yaml`: no index, no
server call, no state file.

| Command | Does |
|---|---|
| `cvcpkg image ls [--json] [--guest-os OS]` | list installed images |
| `cvcpkg image path <name> [--role R]` | print ONE absolute path, nothing else |
| `cvcpkg image dir <name>` | print the image directory |
| `cvcpkg image info <name> [--json]` | show the descriptor |
| `cvcpkg image env <name>` | emit `CVCPKG_IMAGE_*` for `eval` |
| `cvcpkg image verify <name>` | re-hash the bytes **on disk** |
| `cvcpkg image export <name> --to DIR` | copy out of the prefix (reflink where possible) |
| `cvcpkg image test <name>` | boot it in a throwaway VM, assert, destroy (see below) |

Roles: `disk` (default), `descriptor`, `env`, `docs`, `checksums`,
`incus-metadata`, `lxd-metadata`.

Exit codes — `image path` is built for `$(...)` capture:

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | usage or I/O error |
| 3 | no such image installed in this prefix |
| 4 | the image has no artifact for that role |
| 5 | `verify` found a mismatch |
| 6 | `test` booted the image and the guest failed (a SKIP is still 0) |

`--prefix` honours **`CVCPKG_PREFIX`** (as does every other cvcpkg command), so
a provisioning script sets it once instead of threading `--prefix` everywhere.

`image verify` is **not** redundant with the installer's download-time sha256:
that covers the archive, once, when it was fetched. This covers a multi-gigabyte
payload months later, on the NFS mount where it actually lives.

### Fallback: the deterministic path

For a node where cvcpkg is not on `PATH`, or a human at 2am. Stable,
documented, derivable from the package name:

```sh
$CVCPKG_PREFIX/share/haiku-image/disk.qcow2
( cd $CVCPKG_PREFIX/share/haiku-image && sha256sum -c SHA256SUMS )
. $CVCPKG_PREFIX/share/haiku-image/image.env     # paths relative to that dir
```

`image.env` exists because an Incus cluster node reliably has neither `jq` nor
`yq` nor `pkg-config`, but every `/bin/sh` can source a `KEY=value` file.
`image.yaml` stays canonical; `image.env` is generated from the same facts.

## A generic provisioner

Zero hardcoded paths, zero hardcoded hashes, zero guest-specific constants —
one script drives haiku, freebsd, netbsd, windows:

```sh
#!/bin/sh
set -eu
PKG=${1:?package}; VM=${2:?vm name}
: "${CVCPKG_PREFIX:=/srv/cvcpkg/images}"; export CVCPKG_PREFIX

cvcpkg install "$PKG"
cvcpkg image verify "$PKG"

DISK=$(cvcpkg image path "$PKG")
META=$(cvcpkg image path "$PKG" --role incus-metadata)
eval "$(cvcpkg image env "$PKG")"

incus image import "$META" "$DISK" --alias "$PKG"
incus init "$PKG" "$VM" --vm \
    -c limits.cpu="$CVCPKG_IMAGE_CPU_MIN" \
    -c limits.memory="${CVCPKG_IMAGE_MEMORY_MIN_MIB}MiB" \
    -c security.secureboot="$CVCPKG_IMAGE_SECUREBOOT" \
    -d root,size="${CVCPKG_IMAGE_DISK_MIN_GIB}GiB"
incus config device set "$VM" root io.bus="$CVCPKG_IMAGE_DISK_BUS"
incus start "$VM"
```

`CVCPKG_IMAGE_DISK_BUS` is not decoration: Haiku **panics** on `virtio-scsi`,
which is Incus's default.

**Cross-node:** on the target node, `cvcpkg install <pkg> && cvcpkg image
export <pkg> --to /var/tmp/`, then `dd`/import from there.

**Never boot the master in place.** qcow2 is a read-write format, so a VM
booted directly off the installed `disk.qcow2` mutates it and `cvcpkg image
verify` starts failing. Use it as a backing file:

```sh
qemu-img create -f qcow2 -F qcow2 -b "$DISK" overlay.qcow2
```

`image.yaml` flags this with `writable: false`. That flag is **advisory** —
file modes do not survive archive extraction, so nothing can enforce it.

## Writing an image recipe

Set `recipe.kind: image`. It is **enforced, not a label**:

* `cvcpkg validate` requires every `package.files` entry to be under
  `share/<recipe.name>/`, including `share/<recipe.name>/image.yaml`;
* `cvcpkg pack` re-checks the **real staged tree** — nothing outside
  `share/<name>/`, a schema-valid `image.yaml` there, and every
  `disks[].file` present on disk.

Without that gate the convention is a comment, and image package #2 repeats
the prefix-root mistake.

`image.yaml` is generated by `build.sh`, never hand-written, and validated
against the bundled `image-schema.yaml`. Its `boot:` block exists so a
provisioning script has no guest-specific constants:

```yaml
schema_version: 1
image:
  package: haiku-image          # MUST equal the directory name
  version: 1.0.0-beta.5+cvc.1
  guest_os: haiku               # the GUEST, not the bundle's platform
  guest_arch: x86_64            # the GUEST, not the bundle's arch
  guest_release: r1beta5
  variant: builder
disks:
  # DERIVED: format and virtual_size_bytes are read back with `qemu-img info`
  # on the staged file, not asserted by the recipe.
  - {file: disk.qcow2, format: qcow2, role: root,
     virtual_size_bytes: 10741612544, sha256: "…"}
boot:
  firmware: uefi                # POLICY: hypervisor-default UEFI/OVMF, csm=false
  disk_bus: nvme                # MEASURED: bisected live — virtio-blk GP-faults
                                # before userland, virtio-scsi has no Haiku driver
  net_model: virtio-net         # MEASURED: stock virtio NIC holds a DHCP lease
  console: none                 # GUEST FACT: no out-of-band admin channel
  secureboot: false             # POLICY: not signed for the Microsoft keys
  cpu_min: 4                    # POLICY. UNITS: vCPUs
  memory_min_mib: 4096          # POLICY. UNITS: MiB
  disk_min_gib: 11              # DERIVED. UNITS: GiB.
                                # ceil(virtual_size_bytes / 1 GiB) — the
                                # hypervisor's hard floor, NOT headroom.
access: {ssh_user: baron, ssh_pubkey_baked: false}
                                # ILLUSTRATIVE VALUES, not a known-good pair.
                                # `baron` is Haiku's upstream default account
                                # name; `false` is what every haiku-image build
                                # has actually produced so far. Never copy an
                                # account name out of a document — see below.
                                # ssh_user is DERIVED from the image (Haiku
                                # names the account from HAIKU_ROOT_USER_NAME,
                                # whose upstream default is NOT `user`), and is
                                # OMITTED when the build cannot establish it —
                                # an absent key forces the consumer to pass one
                                # instead of failing to log in silently.
                                # access.work_dir (absolute, in the guest) is
                                # optional: set it only when the build tree
                                # does not belong at $HOME/cvcpkg-build. A
                                # consumer that can log in should ask the
                                # guest for $HOME instead — a field can drift
                                # from the artifact, $HOME cannot.
importers: {incus: incus/metadata.tar.xz, lxd: incus/metadata.tar.xz}
writable: false                 # advisory only
docs: README.md
```

Build scripts get `CVC_FULL_VERSION` and `CVC_REVISION` alongside
`CVC_VERSION` for writing the descriptor's `version`.

Other rules:

* **Ship one payload format.** A second copy of the same bits (an anyboot
  `.iso` beside the qcow2) doubles the bundle against the server's upload cap
  and `tar.gz` does no cross-file dedup. Document the one-command recovery
  (`qemu-img convert`) instead.
* **Do not use `provides:`/`conflicts:`** to make images mutually exclusive. A
  fleet operator legitimately wants several co-installed, and distinct package
  names already give distinct directories.
* **An image must never arrive as a transitive `depends` of a library** — it
  is installable only when explicitly named.
* **Do not gate an image install on host capabilities.** The machine that
  installs an image is often not the machine that boots it.

## Testing an image recipe: `test.vm`

cvcpkg has always had a test hook — `test.script`, a shell script run **on the
builder** after the build and before packing, with `CVC_PREFIX` /
`CVC_INSTALL_DIR` set. That is right for a library and worth nothing for an
image: the artifact is an opaque guest disk, so a script on the Linux builder
can prove the file exists and `qemu-img info` parses it, which is exactly the
"trust me, it boots" that shipped a descriptor claiming `disk_bus: virtio-blk`
— a value that general-protection-faults this guest before userland.

`test.vm` is a second hook alongside the first: cvcpkg boots the image the
recipe just staged in a **throwaway VM**, asserts, and destroys it.

```yaml
test:
  # script: test.sh          # optional, unchanged, still runs on the builder
  vm:
    requires_capabilities: [incus]   # skipped (green) without it
    hypervisors: [incus]             # incus | lxd — classic lxc cannot boot a VM
    image: self                      # the image THIS build produced
    connect: ssh                     # ssh | agent
    ssh:
      key_env: HAIKU_BUILDER_SSH_KEY # private half of the baked public key
    script: vm-test.sh               # runs INSIDE the guest
    boot_timeout_seconds: 720
    timeout_seconds: 1800
```

What cvcpkg does, in order: reap any `cvcpkg-vmtest-*` **instance and image** a
previous run left behind, `incus image import` the `importers.<hypervisor>`
metadata tarball plus `disks[].file`, `init` a VM sized from `boot:` (`cpu_min`,
`memory_min_mib`, `disk_min_gib`, `secureboot`), pin the root device to
`boot.disk_bus`, start it, wait for it to become reachable, run the guest
script, and **destroy the instance and the imported image**.

Guarantees, in the order they matter:

* **Skip, never fail, without a hypervisor.** Both gates —
  `requires_capabilities` and "is any of `hypervisors` present" — resolve to a
  reported SKIP, as does a missing SSH key. All three are checked *before* any
  hypervisor state exists. Advertising `lxc` alone also skips, with an
  explanation: classic LXC is daemonless and containers-only.
* **The VM and its imported image are always destroyed** — pass, guest-script
  failure, boot timeout, a hypervisor CLI that throws, `SystemExit`, Ctrl-C and
  SIGTERM. SIGINT/SIGTERM are converted to an exception so the `finally` runs
  at all; default SIGTERM disposition would kill the process and orphan the VM.
  The teardown *itself* then runs with SIGINT/SIGTERM **deferred and
  re-delivered afterwards**, because it is two deletes and a signal landing
  between them would strand the expensive half. Teardown draws on a *fresh*
  budget, so "we ran out of time" never means "we leaked a VM"; it never
  raises, so a broken log callback cannot abort it midway; and a delete that
  fails is reported rather than swallowed.
* **The image counts as much as the instance.** Each run copies a
  multi-gigabyte qcow2 into the daemon's store, and instances and images are
  *separate namespaces* — `incus list` never mentions an image. Anything that
  cleans up has to ask twice (`incus list` **and** `incus image list`), which
  is why a run that only knew about instances would quietly fill a builder's
  disk one image at a time.
* **A partially-failed import still counts.** `incus image import` can copy
  gigabytes and *then* fail, so teardown asks the daemon whether the alias
  exists rather than trusting the exit code. That is also what keeps the common
  case (a bad metadata tarball, where nothing was created) from printing a
  false leak warning.
* **A SIGKILL is covered too**, by the next run: instances *and* image aliases
  are both named `cvcpkg-vmtest-<pkg>-<owning-pid>-<random>` and each run reaps
  that prefix first — instances before images, since a daemon refuses to delete
  an image an instance still uses. The prefix is the only handle the reaper
  has, so it cannot touch a pre-existing instance or image. The pid is in the
  name because one builder host can run two cvcpkg builds against one daemon —
  something is only reaped when its owning process is gone, so a concurrent
  build's live VM (or the image it booted from) is never collateral.
* **Bounded time.** One wall-clock deadline covers the phase and every
  subprocess call draws its timeout from it, so a hung boot costs minutes, not
  a builder.

`connect: ssh` streams the script to `sh -s` over stdin — no scp, no writable
remote path, and no incus agent, which a non-Linux guest does not run.
`connect: agent` pushes the file and `exec`s it, and needs the agent.

The same engine is available by hand against an *installed* image, which is how
you reproduce a builder failure:

```sh
cvcpkg image test haiku-image --ssh-key ~/.ssh/haiku_builder
# exit 0 on pass OR skip; 6 on a real failure
```

Note `test.vm.requires_capabilities` is deliberately **not** the recipe-level
`requires_capabilities`. The latter gates whether the resolver will *select*
the package at all, and an image must stay installable on a host with no
hypervisor — carrying it to a host that has one is the point of the package.

### Status

The lifecycle, the gates, the deadline and the teardown are exercised by unit
tests against a mocked hypervisor (`tests/unit/test_vmtest.py`) and end to end
against a stub `incus`/`ssh` on `PATH`. They have **not** been run against a
live Incus daemon or a real Haiku guest: the command forms are Incus's
documented split-VM-image surface, not an observed session.

Concretely, `haiku-image`'s `test.vm` **skips everywhere today** — no builder in
the fleet advertises `incus` yet, and `HAIKU_BUILDER_SSH_KEY` is not plumbed
into CI. Wiring one builder with Incus plus that secret is what turns this from
a seam into a signal, and `vm-test.sh`'s Haiku-specific assertions (gcc/make
present, `UserBootscript` installed) are written from the recipe's own build
script and get their first real check on that run.
