# HaikuOS builder image — import guide

> ## STATUS: NOT KNOWN-BOOTABLE (2026-08-04)
>
> **No image produced by this recipe has been observed to boot to userland,
> and none has been observed to accept an SSH login.** Boot repair is in
> flight on a separate branch. Until that lands and someone posts an observed
> boot, treat everything below as *the intended import procedure*, not as a
> report of something that worked.
>
> Known-broken. These were found by inspection and by failed runs on the
> boot-repair branch (`fix/haiku-image-boot`), not by a successful boot:
>
> * **The partition table is wrong for any image ≥ 4 GiB**, and
>   `HAIKU_IMAGE_SIZE` here is 51200 MiB. Haiku's `src/tools/anyboot`
>   computed MBR extents in 32-bit, so the BFS extent was truncated mod 2^32
>   and the EFI entry pointed inside the BFS. That is fatal to firmware *and*
>   to the `losetup -P` this build uses to inject the SSH key.
> * **The SSH key injection has never been shown to work.** `bfs_shell` mounts
>   the volume at a fixed `/myfs` and starts at `/`, so the build's relative
>   `home/.ssh/authorized_keys` target resolved to nothing — and `bfs_shell`
>   prints an error but still exits 0, so nothing noticed. Haiku's `sshd_config`
>   also reads `config/settings/ssh/authorized_keys`, not `~/.ssh`. **Do not
>   assume a key is baked in**; check `access.ssh_pubkey_baked` and plan to
>   inject one yourself.
> * **Nothing in the image started `sshd`.** Haiku's openssh package ships no
>   launch job, and `UserBootscript` only runs inside a desktop session, which
>   a headless boot never starts.
>
> What *is* established, and is about the guest OS rather than about this
> image, is the disk-bus behaviour in "Disk bus" below: it was bisected live
> against Haiku r1/beta5 under Incus/QEMU. It says which bus Haiku can drive.
> It does not say this image boots.

A headless, pre-configured HaikuOS builder VM image. Haiku's `Installer` is
graphical-only, so this image is **built pre-installed**: the intent is that it
comes up with DHCP networking and OpenSSH already running, with no VGA/GUI
interaction at any point. See the status block above for how much of that has
actually been demonstrated.

## Where it lives

Everything is under one directory named after the package, with **role-based**
filenames, so the path is derivable from the package name alone — no version,
no guest arch, no upstream naming knowledge:

```
$CVCPKG_PREFIX/share/haiku-image/
├── image.yaml            canonical descriptor (schema_version 1)
├── image.env             the same facts as POSIX KEY=value, `. `-sourceable
├── disk.qcow2            the payload
├── SHA256SUMS            `sha256sum -c` format
├── README.md             this file
└── incus/
    ├── metadata.yaml     Incus/LXD image metadata
    └── metadata.tar.xz   what `incus image import` actually takes
```

Only **one** payload format ships. The anyboot hybrid ISO is the same bits as
the qcow2; recover it with one command if you need to `dd` it:

```sh
qemu-img convert -f qcow2 -O raw disk.qcow2 haiku-anyboot.iso
```

## Finding it — two equal contracts

**Preferred — the CLI.** No layout knowledge, no hashes, no paths:

```sh
export CVCPKG_PREFIX=/srv/cvcpkg/images     # honoured by every --prefix
cvcpkg install haiku-image
cvcpkg image verify haiku-image             # re-hash the bytes on disk

DISK=$(cvcpkg image path haiku-image)
META=$(cvcpkg image path haiku-image --role incus-metadata)
eval "$(cvcpkg image env haiku-image)"      # CVCPKG_IMAGE_* facts
```

`cvcpkg image ls` lists every image installed in the prefix; `cvcpkg image
info haiku-image --json` prints the descriptor; `cvcpkg image export
haiku-image --to /var/tmp` copies the disk out under a meaningful name.
`image path` exits **3** when the image is not installed and **4** when it has
no such role, so a script can branch without parsing output.

**Fallback — the deterministic path**, for a node where cvcpkg is not on PATH
or a human at 2am. Stable, documented, derivable from the package name:

```sh
$CVCPKG_PREFIX/share/haiku-image/disk.qcow2
( cd $CVCPKG_PREFIX/share/haiku-image && sha256sum -c SHA256SUMS )
. $CVCPKG_PREFIX/share/haiku-image/image.env   # paths relative to that dir
```

## Booting: never boot the master in place

qcow2 is a read-write format, so a VM booted directly off `disk.qcow2` mutates
the installed artifact and `cvcpkg image verify` starts failing. Use it as a
**backing file** and boot the overlay:

```sh
qemu-img create -f qcow2 -F qcow2 -b "$DISK" overlay.qcow2
```

`image.yaml` flags this with `writable: false`. That flag is **advisory** —
file modes do not survive archive extraction, so nothing can enforce it.

## Access

- The build *attempts* to bake in `$HAIKU_BUILDER_SSH_PUBKEY`. **Assume it did
  not.** Read `access.ssh_pubkey_baked` out of `image.yaml`
  (`cvcpkg image info haiku-image --json`; it is deliberately not in
  `image.env`, which carries only the facts a provisioner acts on) — it is set
  from a read-back of the injected file, so `false` means the key really is
  not there, and `false` is
  what every build has produced so far (see the status block: the injection
  wrote to a path that does not exist in the mounted volume, and failed
  silently).
- Log in as `$CVCPKG_IMAGE_SSH_USER`, never as a name copied out of a
  document: `ssh "$CVCPKG_IMAGE_SSH_USER"@<ip>`. The build derives that name
  from the image and **omits** it when it could not, precisely so a stale
  literal cannot disagree with the account the image actually has. If it is
  unset, the build did not establish one and there is no safe guess.
- To inject a key yourself, mount the BFS partition with Haiku's `bfs_shell`
  (`losetup -f -P` on the raw image → `…p1`) and write
  **`/myfs/home/config/settings/ssh/authorized_keys`** (mode 600, its
  directory 700). Two traps, both of which have already cost a day:
  `bfs_shell` mounts at the fixed path `/myfs` and starts in `/`, so a bare
  relative `home/...` silently resolves to nothing *and still exits 0*; and
  Haiku's openssh ships an `sshd_config` whose `AuthorizedKeysFile` is
  `config/settings/ssh/authorized_keys`, so a key in `~/.ssh` is never read.
  Verify by reading the file back out and comparing bytes.

## Incus (VM)

`incus image import` takes a metadata **tarball**, not a bare `metadata.yaml`:

```sh
cvcpkg install haiku-image
cvcpkg image verify haiku-image
DISK=$(cvcpkg image path haiku-image)
META=$(cvcpkg image path haiku-image --role incus-metadata)
eval "$(cvcpkg image env haiku-image)"

incus image import "$META" "$DISK" --alias haiku-builder

# The disk bus is the ONE mandatory hypervisor setting. Take it from
# $CVCPKG_IMAGE_DISK_BUS (= nvme) rather than typing a constant; see
# "Disk bus" below for what the other buses do.
incus init haiku-builder haiku-b1 --vm \
    -c limits.cpu="$CVCPKG_IMAGE_CPU_MIN" \
    -c limits.memory="${CVCPKG_IMAGE_MEMORY_MIN_MIB}MiB" \
    -c security.secureboot="$CVCPKG_IMAGE_SECUREBOOT" \
    -c security.csm=false \
    -d root,size="${CVCPKG_IMAGE_DISK_MIN_GIB}GiB"
incus config device set haiku-b1 root io.bus="$CVCPKG_IMAGE_DISK_BUS"
incus start haiku-b1

# Find its address from the managed bridge lease, then:
ssh "$CVCPKG_IMAGE_SSH_USER"@<ip>
```

## LXD (VM)

Same as Incus with `lxc` in place of `incus`; `--role lxd-metadata` resolves to
the same tarball. Carry **all three** hypervisor settings across, not just the
bus: LXD's VM default is `security.secureboot=true`, and Haiku is not signed
for the Microsoft keys, so an otherwise-correct VM refuses to execute the
bootloader before the disk bus ever matters.

```sh
lxc image import "$META" "$DISK" --alias haiku-builder
lxc init haiku-builder haiku-b1 --vm \
    -c limits.cpu="$CVCPKG_IMAGE_CPU_MIN" \
    -c limits.memory="${CVCPKG_IMAGE_MEMORY_MIN_MIB}MiB" \
    -c security.secureboot="$CVCPKG_IMAGE_SECUREBOOT" \
    -c security.csm=false \
    -d root,size="${CVCPKG_IMAGE_DISK_MIN_GIB}GiB"
lxc config device set haiku-b1 root io.bus="$CVCPKG_IMAGE_DISK_BUS"
lxc start haiku-b1
```

`security.csm=false` keeps the firmware on OVMF/UEFI, which is what
`boot.firmware: uefi` in the descriptor means; turning CSM on swaps in SeaBIOS.
The recipe's own `test.vm` block drives **Incus only** — the LXD spelling above
is a transcription of the Incus one and has not been run.

## Disk bus — the one mandatory setting

`boot.disk_bus` is **`nvme`**, and it is not a preference. Bisected live
against Haiku r1/beta5 under Incus/QEMU:

| `io.bus` | What happens |
|---|---|
| `virtio-blk` | **General protection fault (vector 0xd)** in `virtio_pci notify_queue()` → kdebug. The VM never reaches userland. |
| `virtio-scsi` (the hypervisor default) | Haiku has **no virtio-scsi driver**. The disk never appears; the boot loader dies with `InitialDeviceScan: No such file or directory` and a `vfs_mount_boot_file_system` panic. |
| **`nvme`** | **The only one that gets anywhere.** The disk enumerates and Haiku reaches userland. |

Haiku's NVMe driver is native and pre-dates its virtio work, which is why it
is the solid one. Earlier revisions of this file said `virtio-blk`, inferred
from "Haiku panics on virtio-scsi" without anyone checking that virtio-blk
works — it does not. Read the bus from `$CVCPKG_IMAGE_DISK_BUS`.

Scope of that last row, because it has been over-read once already: it is a
statement about **Haiku's driver support**, established against Haiku r1/beta5
under Incus/QEMU. It is *not* a statement that an image built by this recipe
boots — see the status block at the top; it does not. Picking `nvme` removes
the bus from the list of suspects, nothing more.

**Networking needs no such treatment.** The stock virtio NIC works and holds a
DHCP lease; a claim that `virtio_net` page-faults was retracted — it was a
symptom of the disk bug above, observed in an already-degraded boot with no
usable root device.

**Firmware stays the hypervisor's default UEFI/OVMF**, with
`security.csm=false` and `security.secureboot=false`. That is the firmware the
bus bisection was carried out under. Turning CSM on swaps OVMF for SeaBIOS and
replaces the known-good combination.

## Proxmox VE

> Both of Proxmox's first-class disk buses are the two that do **not** boot
> this guest: `--scsi0` is virtio-scsi and `--virtio0` is virtio-blk. An
> earlier version of this section used `--virtio0` — directly contradicting
> the table above — so the disk has to be attached as an NVMe device through
> `--args`. **This translation has not itself been bisected**; the table above
> was established under Incus/QEMU. Boot it once before trusting it.

```sh
DISK=$(cvcpkg image export haiku-image --to /var/tmp)   # copy out of the prefix
eval "$(cvcpkg image env haiku-image)"
# --bios ovmf is boot.firmware: uefi.  pre-enrolled-keys=0 is
# boot.secureboot: false — it is qm's default today, but state it, because the
# GUI's default is the opposite and Haiku is not signed for the MS keys.
qm create 9000 --name haiku-builder \
    --memory "$CVCPKG_IMAGE_MEMORY_MIN_MIB" --cores "$CVCPKG_IMAGE_CPU_MIN" \
    --machine q35 --bios ovmf --net0 "${CVCPKG_IMAGE_NET_MODEL#virtio-},bridge=vmbr0" \
    --efidisk0 local-lvm:0,efitype=4m,pre-enrolled-keys=0
qm importdisk 9000 "$DISK" local-lvm          # -> unused0
# Attach as NVMe, not as --virtio0/--scsi0 (see the disk-bus table above).
# Confirm the LV name first — `qm config 9000` prints it; with an efidisk
# already created the imported volume is usually vm-9000-disk-1, not -0.
qm set 9000 --args "-drive file=/dev/pve/vm-9000-disk-1,if=none,id=nvm0,format=raw \
                    -device nvme,drive=nvm0,serial=haiku0"
qm start 9000
```

Two consequences of going through `--args` that Proxmox will not warn you
about: the disk stays `unused0` in `qm config`, so the GUI shows a VM with no
boot disk and `--boot order=` cannot name it (leave the boot order alone and
let OVMF find the NVMe device itself); and PVE's backup/migration tooling does
not know about a device it did not attach. If that is unacceptable, this guest
is a poor fit for Proxmox — Incus and plain QEMU both express `nvme` as a
first-class device.

## Plain QEMU

Every one of the three descriptor facts has to be spelled out by hand here.
`if=virtio` is virtio-blk, which GP-faults this guest; `-bios` is not how you
get a UEFI machine with non-secureboot firmware; and `-nographic` wires up a
serial console this guest does not have.

```sh
eval "$(cvcpkg image env haiku-image)"
qemu-img create -f qcow2 -F qcow2 -b "$DISK" haiku-b1.qcow2

# firmware: uefi + secureboot: false -> the PLAIN OVMF build via pflash, not
# OVMF_CODE_4M.secboot.fd / .ms.fd, and with a WRITABLE private copy of the
# variable store (the shipped VARS file must not be edited in place).
cp /usr/share/OVMF/OVMF_VARS_4M.fd ./haiku-b1_VARS.fd    # path is distro-specific

qemu-system-x86_64 -machine q35 \
    -m "$CVCPKG_IMAGE_MEMORY_MIN_MIB" -smp "$CVCPKG_IMAGE_CPU_MIN" \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
    -drive if=pflash,format=raw,file=./haiku-b1_VARS.fd \
    -drive file=haiku-b1.qcow2,if=none,id=nvm0,format=qcow2 \
    -device nvme,drive=nvm0,serial=haiku0 \
    -netdev user,id=n0,hostfwd=tcp::2222-:22 \
    -device "${CVCPKG_IMAGE_NET_MODEL},netdev=n0" \
    -display none
# then: ssh -p 2222 "$CVCPKG_IMAGE_SSH_USER"@localhost
```

`-display none`, not `-nographic`: `boot.console` is `none` because Haiku's
serial port is kernel-debug only, so `-nographic` buys you no shell — and it
takes away the VGA console, which is the only place a boot failure is visible.
Swap `-display none` for `-vga std` (default) and watch it if the guest does
not come up.

The OVMF file names above are Debian/Ubuntu's (`ovmf` package, 4 MB build).
Fedora ships `/usr/share/edk2/ovmf/OVMF_CODE.fd`; check your distro. Take the
**non-`secboot`, non-`ms`** variant — that is what `boot.secureboot: false`
means.

*Verified locally on QEMU 8.2.2: this `-machine q35` + pflash-OVMF +
`-device nvme` command line assembles and `info qtree` shows the NVMe
controller bound to `nvm0`. That is a check of the command form only — no
Haiku image has been booted with it.*

## libvirt

**libvirt cannot give you the bus the descriptor asks for.** A domain's
`<target bus=...>` accepts `virtio`, `scsi`, `sata`, `ide`, `usb` — there is
no `nvme`, and `<disk type='nvme'>` is something else entirely (VFIO
pass-through of a *physical* host NVMe device, not an emulated controller).
Of the buses libvirt does offer, `virtio` and `scsi` are the two the table
above rules out. `sata`/`ide` are not in that table at all: Haiku has an AHCI
driver so they may well work, but nobody has tried it with this image and it
diverges from `boot.disk_bus`, so it is a guess, not an instruction.

The only way through is the QEMU escape hatch, which requires the
`xmlns:qemu` namespace on `<domain>` and is opaque to libvirt's own device
model (no hotplug, no `virsh domblklist`, no migration checks):

```xml
<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
  ...
  <os firmware='efi'>
    <!-- firmware feature secure-boot MUST be off; see boot.secureboot -->
    <firmware><feature enabled='no' name='secure-boot'/></firmware>
  </os>
  <qemu:commandline>
    <qemu:arg value='-drive'/>
    <qemu:arg value='file=/var/lib/libvirt/images/haiku-b1.qcow2,if=none,id=nvm0,format=qcow2'/>
    <qemu:arg value='-device'/>
    <qemu:arg value='nvme,drive=nvm0,serial=haiku0'/>
  </qemu:commandline>
</domain>
```

**This XML has not been run.** If you are not already committed to libvirt,
use plain QEMU or Incus, both of which express `nvme` natively.

## Cross-node

Do not share a prefix path between the machine that installed the image and
the machine that boots it:

```sh
cvcpkg install haiku-image
cvcpkg image export haiku-image --to /var/tmp    # -> haiku-image-<version>.qcow2
```

then `dd`/`scp`/import from there.

## Notes

- **Disk size:** do not read a number out of this file. `boot.disk_min_gib` is
  computed by the build as `ceil(virtual_size_bytes / 1 GiB)` off the image it
  just produced, and exported as `$CVCPKG_IMAGE_DISK_MIN_GIB`; this note used
  to say "~50 GB, which is why `disk_min_gib` is 50" and was wrong twice over
  (a 51204 MiB image does not fit in a 50 GiB volume at all, and
  `HAIKU_IMAGE_SIZE` has since changed). It is a hard floor: a hypervisor
  refuses a root volume smaller than the image's virtual size. It is **not**
  headroom — the guest's usable space was fixed at build time by
  `HAIKU_IMAGE_SIZE` because BFS can't be grown afterwards, so a larger volume
  gives the guest nothing. For more space, rebuild with a larger
  `HAIKU_IMAGE_SIZE` or attach a second BFS-formatted disk as scratch.
- **Login account:** likewise, use `$CVCPKG_IMAGE_SSH_USER` rather than a name
  from any document. The build derives it from the image (falling back to
  `HAIKU_ROOT_USER_NAME` and then to Haiku's own default), and it is
  **omitted** from the descriptor when the build could not establish it —
  in which case there is no safe guess and you must supply one explicitly.
- **No serial shell:** Haiku's serial port is kernel-debug only
  (`boot.console: none`), so SSH is the only admin channel — make sure
  networking/keys are correct before relying on a headless deploy.
