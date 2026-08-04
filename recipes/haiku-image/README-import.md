# HaikuOS builder image — import guide

A headless, **pre-installed and pre-configured** HaikuOS VM image. Haiku's
`Installer` is graphical-only and Haiku has neither a serial getty nor a
virtio-console driver, so there is no console to install through on a headless
hypervisor — this image is therefore built already installed. Boot it and it
comes up with DHCP networking, `sshd` listening on the baked-in key, the build
toolchain present, and `/boot/home/cvcpkg-build` ready for jobs.

(A stock Haiku live ISO boots and gets a lease, but runs no `sshd` — port 22 is
closed on it. There is no console to fix that from either, which is the whole
reason this image exists. If you can ping a Haiku VM and not SSH to it, check
first whether it booted the ISO rather than an installed system.)

It is a **build target**, not a builder: cvcpkg itself cannot run on Haiku
(no pip, no greenlet/httpx, cryptography 3.4.8 against a >=41.0 floor), so a
Linux builder drives this VM over SSH via `cvcpkg.haikuhost`:

```sh
export CVCPKG_HAIKU_SSH=user@<ip>
export CVCPKG_HAIKU_SSH_KEY=~/.ssh/haiku-builder
# CVCPKG_HAIKU_WORKDIR defaults to /boot/home/cvcpkg-build, which this image
# already creates on every boot.
```

## Files

| File | What |
|------|------|
| `haiku-builder.qcow2` | The bootable disk. qcow2 with compressed clusters — directly usable, no decompression step. 10 GiB virtual, ~1 GiB on disk. |
| `haiku-builder.qcow2.sha256` | Checksum of the above. |
| `metadata.yaml` | Incus/LXD image metadata. |

## ⚠ One hypervisor setting is mandatory

**Disk bus must be `nvme`.** Haiku R1/beta5's `virtio_block` takes a general
protection fault (vector 0xd) inside `virtio_pci notify_queue()` and drops into
kdebug before userland; `virtio-scsi` has no Haiku driver at all, so the disk is
simply not visible. Bisected on a live VM: `nvme` is the only bus that boots
into userland. There is no in-image workaround — the drivers live inside the
read-only `haiku` .hpkg — and it is expected to go away on a post-beta5 ref, see
the `HAIKU_REF` comment in `recipe.yaml`.

**The NIC does not need changing.** Leave the managed virtio NIC alone. An
earlier revision of this file called an emulated NIC mandatory, on the strength
of a `virtio_net_ioctl` page fault (vector 0xe) seen in kdebug. That was
observed in an already-degraded boot — the same VM had no usable root device —
and does not reproduce in a healthy one: the live `haiku-build` VM on star-00
holds a DHCP lease right now over a stock Incus virtio NIC. Two consequences:

- Do not go looking for an emulated NIC on Incus. You would not find one: the
  `nic` device type only accepts `io.bus=virtio|usb`, and the NIC is hot-plugged
  over QMP *after* the VM starts, so it never appears in the generated QEMU
  config that `raw.qemu.conf` patches. The `raw.qemu.conf` snippet this file
  used to prescribe could not have taken effect on any Incus version.
- On hypervisors where the model *is* a flag (plain QEMU, libvirt, Proxmox),
  the examples below now say `virtio-net-pci`, to match the one configuration
  that has actually been observed working. `e1000` (Haiku's `ipro1000` driver,
  present in the base image) is a fine fallback — [unverified] either way on
  those hypervisors specifically; only Incus was tested.

**Firmware: leave it at the default.** The verified-good configuration is
UEFI/OVMF with `security.csm=false` and `security.secureboot=false`, which is
what Incus gives a VM out of the box. The VM boots
`BdsDxe: loading Boot0002 "UEFI QEMU NVMe Ctrl …"` — turning CSM *on* is a
change away from the configuration that is known to work, not towards it.

## Access

- Log in as **`user`**: `ssh user@<ip>`. That is the builder account — Haiku
  has no `useradd` and effectively runs single-user as uid 0, so `user` *is*
  the build user.
- The image trusts the SSH public key baked in at build time
  (`$HAIKU_BUILDER_SSH_PUBKEY`). Password authentication is disabled.
- If the image was built without a key (public builds), inject one before
  first boot:

  ```sh
  # Expose the qcow2's BFS partition, then write the key with Haiku's own
  # bfs_shell (built in the haiku tree as generated*/…/bfs_shell).
  sudo modprobe nbd max_part=8
  sudo qemu-nbd --connect=/dev/nbd0 haiku-builder.qcow2
  sudo bfs_shell /dev/nbd0p1 <<'EOF'
  mkdir home/.ssh
  cp :/path/to/id_ed25519.pub home/.ssh/authorized_keys
  sync
  quit
  EOF
  sudo qemu-nbd --disconnect /dev/nbd0
  ```

- Host keys are generated on **first boot**, so every clone gets its own
  identity — expect a `known_hosts` entry per VM.
- `/boot/home/cvcpkg-boot.log` is the first thing to read over SSH when a VM
  misbehaves; `UserBootscript` truncates it each boot.

## Incus (VM)

```sh
incus image import metadata.yaml haiku-builder.qcow2 --alias haiku-builder

incus init haiku-builder haiku-b1 --vm \
    -c limits.cpu=4 -c limits.memory=4GiB -d root,size=10GiB

# MANDATORY: nvme, not the default virtio-scsi (see above).
incus config device set haiku-b1 root io.bus=nvme

# The NIC needs nothing: the managed virtio NIC gets a DHCP lease as-is, and
# Incus offers no emulated model anyway (nic devices take io.bus=virtio|usb).
# Firmware likewise stays at the default UEFI/OVMF, security.csm=false.

incus start haiku-b1
# Find its address from the managed bridge lease, then:
ssh user@<ip>
```

## LXD (VM)

Identical with `lxc` in place of `incus`.

## Proxmox VE

```sh
qm create 9000 --name haiku-builder --memory 4096 --cores 4 \
    --machine q35 --bios ovmf --efidisk0 local-lvm:0,efitype=4m \
    --net0 virtio,bridge=vmbr0
qm importdisk 9000 haiku-builder.qcow2 local-lvm
# Proxmox has no native nvme bus, so attach it through -args. The disk is
# imported as an unused volume; name it in the drive= below.
qm set 9000 --args '-drive file=/dev/local-lvm/vm-9000-disk-1,if=none,id=nvme0 -device nvme,drive=nvme0,serial=haiku'
qm start 9000
```

## Plain QEMU

```sh
qemu-system-x86_64 -machine q35 -m 4096 -smp 4 \
    -bios /usr/share/OVMF/OVMF_CODE.fd \
    -drive file=haiku-builder.qcow2,if=none,format=qcow2,id=nvme0 \
    -device nvme,drive=nvme0,serial=haiku \
    -netdev user,id=n0,hostfwd=tcp::2222-:22 -device virtio-net-pci,netdev=n0 \
    -nographic
# then: ssh -p 2222 user@localhost
```

libvirt has no `bus='nvme'` for emulated disks, so under libvirt the disk goes
in via a `<qemu:commandline>` block with the same arguments as above; the
interface is an ordinary `<model type='virtio'/>`.

## Notes

- **Disk size:** the BFS partition is 10 GiB (`HAIKU_IMAGE_SIZE` in
  `UserBuildConfig`) — roughly 3 GiB of system and toolchain, ~7 GiB of
  working room for the source tree, build dir and deps prefix that
  `haikuhost` stages per job. BFS **cannot be grown after the fact**, and
  growing the qcow2 does not grow the filesystem inside it. If a build needs
  more, attach a second BFS-formatted disk as scratch and point
  `CVCPKG_HAIKU_WORKDIR` at it; only rebuild with a larger
  `HAIKU_IMAGE_SIZE_MB` if that is genuinely not enough.
- **Old job dirs:** `haikuhost` deliberately keeps the remote work dir when a
  build fails, so you can inspect it. Each new job then trims the *finished*
  ones back to the newest few (`CVCPKG_HAIKU_KEEP_JOBS`, default 3), and
  `UserBootscript` sweeps `/boot/home/cvcpkg-build/jobs/*` older than 7 days —
  copy anything you care about out before then.
- **Sharing one box between builders is safe:** a running job holds a
  `.cvcpkg-job` marker in its own dir and no reaper will touch a marked dir,
  so a second builder's housekeeping cannot delete a live build's source and
  build tree. The marker expires after `CVCPKG_HAIKU_JOB_TTL` (default 24 h)
  so a builder killed mid-job costs one undeletable dir for a day, not
  forever. If you ever have to clear one by hand, delete the marker file, not
  the job dir, and let the next job reap it.
- **No cron:** Haiku has none. `~/config/settings/boot/UserBootscript` is the
  only recurring hook; anything that must survive a reboot goes there.
- **No serial shell:** Haiku's serial port is kernel-debug only, so SSH is the
  only admin channel — get networking and keys right before relying on a
  headless deploy.
