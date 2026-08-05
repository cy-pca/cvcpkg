# HaikuOS builder image — import guide

A headless, pre-configured HaikuOS builder VM image. Haiku's `Installer` is
graphical-only, so this image is **built pre-installed** — boot it and it
comes up with DHCP networking (Haiku brings that up itself) and `sshd`
listening. Stock Haiku does **not** start `sshd` — not via socket activation,
not via anything else — so this recipe bakes in a `launch_daemon` job that
does; see [Access](#access). No VGA/GUI interaction is needed at any point.

## Files

| File | What |
|------|------|
| `haiku-builder.qcow2` | Bootable disk image (qcow2). Use with QEMU/libvirt/Proxmox, or import into Incus/LXD. |
| `haiku-builder-anyboot.iso` | The raw anyboot image (hybrid ISO). `dd` to a disk, or attach as a raw disk. |
| `metadata.yaml` | Incus/LXD image metadata. |

## Access

- The image trusts the SSH public key baked in at build time
  (`$HAIKU_BUILDER_SSH_PUBKEY`). Log in as **`user`**: `ssh user@<ip>`.
  That is the image's only account: uid 0, gid 0, home `/boot/home`, shell
  `/bin/bash`. It is named by `HAIKU_ROOT_USER_NAME` in the recipe's
  `UserBuildConfig`; leaving that unset gets you Haiku's `baron` fallback
  and `Invalid user user` from `sshd`.
- **The authorized_keys path is NOT `~/.ssh`.** Haiku's openssh package ships
  an `sshd_config` whose only non-default directive is

      AuthorizedKeysFile	config/settings/ssh/authorized_keys

  so the key must be at `/boot/home/config/settings/ssh/authorized_keys`
  (mode 600, its directory 700). A key in `~/.ssh/authorized_keys` is simply
  never read and every login fails with `Permission denied (publickey,...)`.
- **Nothing in Haiku starts sshd.** The openssh package ships no
  `data/launch/sshd` job, and `~/config/settings/boot/UserBootscript` only
  runs inside a desktop session, which a headless boot never starts. The
  image therefore carries a launch_daemon SYSTEM-context job at
  `/boot/system/settings/launch/sshd` that runs
  `/boot/system/settings/ssh/cvcpkg-start-sshd.sh` (`ssh-keygen -A`, then
  `sshd -D -e`). Measured: port 22 is open ~20 s after power-on, and
  survives a reboot.
- If the image was built without a key (public builds), inject one before
  first boot with Haiku's `bfs_shell` on the BFS partition
  (`losetup -f -P haiku-builder-anyboot.iso` → `…p1`), writing
  `/myfs/home/config/settings/ssh/authorized_keys` — `bfs_shell` mounts the
  volume at the fixed path `/myfs` and starts in `/`, so a bare
  `home/config/...` silently resolves to nothing (it prints an error but
  still exits 0). Or set a password once via the VGA console.

## Incus (VM)

```sh
# Import the disk as a VM image.
incus image import metadata.yaml haiku-builder.qcow2 --alias haiku-builder

# Launch a VM. Verified working: UEFI/OVMF with secureboot and CSM off, the
# root disk on io.bus=nvme, and the stock virtio NIC (Haiku DHCPs on it).
incus init haiku-builder haiku-b1 --vm \
    -c limits.cpu=4 -c limits.memory=4GiB \
    -c security.secureboot=false -c security.csm=false \
    -d root,size=11GiB
incus config device set haiku-b1 root io.bus=nvme
incus start haiku-b1

# Find its address from the managed bridge lease, then:
ssh user@<ip>
```

## LXD (VM)

Same as Incus with `lxc` in place of `incus` — including `io.bus=nvme` and the
firmware settings, which are what was actually verified:

```sh
lxc image import metadata.yaml haiku-builder.qcow2 --alias haiku-builder
lxc init haiku-builder haiku-b1 --vm \
    -c limits.cpu=4 -c limits.memory=4GiB \
    -c security.secureboot=false -c security.csm=false \
    -d root,size=11GiB
lxc config device set haiku-b1 root io.bus=nvme
lxc start haiku-b1
```

## Proxmox VE

```sh
# Create a VM shell (q35 + OVMF/UEFI), then import the disk.
qm create 9000 --name haiku-builder --memory 4096 --cores 4 \
    --machine q35 --bios ovmf --net0 virtio,bridge=vmbr0 \
    --efidisk0 local-lvm:0,efitype=4m
qm importdisk 9000 haiku-builder.qcow2 local-lvm
qm set 9000 --scsihw virtio-scsi-pci --virtio0 local-lvm:vm-9000-disk-1
qm set 9000 --boot order=virtio0
qm start 9000
```

## Plain QEMU / libvirt

```sh
qemu-system-x86_64 -machine q35 -m 4096 -smp 4 \
    -bios /usr/share/OVMF/OVMF_CODE.fd \
    -drive file=haiku-builder.qcow2,if=virtio,format=qcow2 \
    -netdev user,id=n0,hostfwd=tcp::2222-:22 -device virtio-net,netdev=n0 \
    -nographic
# then: ssh -p 2222 user@localhost
```

## Notes

- **Only the Incus stanza has actually been run** (the LXD one is the same
  commands under the other binary name). The Proxmox and plain-QEMU recipes
  above are written to the same requirements — UEFI/OVMF firmware, a disk bus
  Haiku's boot loader can read, a NIC Haiku can DHCP on — but have not been
  exercised. Treat them as a starting point, not a verified procedure.
- **Disk size:** the baked BFS partition is 10 GiB (`HAIKU_IMAGE_SIZE=10240`),
  of which ~7.1 GiB is free on a fresh boot. BFS can't be grown after the
  fact, and resizing the *image* is not enough — rebuild with a larger
  `HAIKU_IMAGE_SIZE` if you need more, or attach a second BFS-formatted disk
  as scratch. Give the VM a root disk at least as large as the image.
- **Baked-in toolchain:** gcc 13.3.0, binutils 2.42, GNU make 4.1, jam,
  autotools, m4/patch/pkgconfig/bison/flex/nasm, git 2.45.2, perl 5.40.0,
  python3.10 (`python3`), and Haiku's own `haiku_devel` headers — all
  ACTIVATED in `/boot/system/packages`, not staged in `/boot/_packages_`.
  `python3 -c "import sys; print(sys.platform)"` reports **`haiku1`**.
- **Not included:** `ninja` — its HaikuPorts build requires
  `haiku >= r1~beta6_hrev59866_5` and this image is beta5. `cmake` (4.1.6),
  `patchelf` and `rsync` are NOT baked in but DO install at runtime with
  `pkgman install -y cmake patchelf rsync` (verified on a booted image;
  a reboot activates them). This is a **manual step** — the image has no
  first-boot top-up automation, by design: a `launch_daemon` job that would
  have run this on first boot failed reproducibly and was not shipped.
- **No serial shell:** Haiku's serial port is kernel-debug only, so SSH is
  the only admin channel — make sure networking/keys are correct before
  relying on a headless deploy.
