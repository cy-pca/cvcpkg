# HaikuOS builder image — import guide

A headless, pre-configured HaikuOS builder VM image. Haiku's `Installer` is
graphical-only, so this image is **built pre-installed** — boot it and it
comes up with DHCP networking and OpenSSH already running (Haiku
socket-activates `sshd` and auto-configures DHCP). No VGA/GUI interaction is
needed at any point.

## Files

| File | What |
|------|------|
| `haiku-builder.qcow2` | Bootable disk image (qcow2). Use with QEMU/libvirt/Proxmox, or import into Incus/LXD. |
| `haiku-builder-anyboot.iso` | The raw anyboot image (hybrid ISO). `dd` to a disk, or attach as a raw disk. |
| `metadata.yaml` | Incus/LXD image metadata. |

## Access

- The image trusts the SSH public key baked in at build time
  (`$HAIKU_BUILDER_SSH_PUBKEY`). Log in as **`user`**: `ssh user@<ip>`.
- If the image was built without a key (public builds), inject one before
  first boot with Haiku's `bfs_shell` on the BFS partition
  (`losetup -f -P haiku-builder-anyboot.iso` → `…p1`), writing
  `home/.ssh/authorized_keys` — or set a password once via the VGA console.

## Incus (VM)

```sh
# Import the disk as a VM image.
incus image import metadata.yaml haiku-builder.qcow2 --alias haiku-builder

# Launch a VM. Haiku needs virtio-blk (not the default virtio-scsi) and an
# emulated NIC path Haiku can drive; virtio-net works on Haiku.
incus init haiku-builder haiku-b1 --vm \
    -c limits.cpu=4 -c limits.memory=4GiB -d root,size=50GiB
incus config device set haiku-b1 root io.bus=virtio-blk
incus start haiku-b1

# Find its address from the managed bridge lease, then:
ssh user@<ip>
```

## LXD (VM)

Same as Incus with `lxc` in place of `incus`:

```sh
lxc image import metadata.yaml haiku-builder.qcow2 --alias haiku-builder
lxc init haiku-builder haiku-b1 --vm -c limits.cpu=4 -c limits.memory=4GiB
lxc config device set haiku-b1 root io.bus=virtio-blk
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

- **Disk size:** the baked BFS partition is ~50 GB (`HAIKU_IMAGE_SIZE`).
  BFS can't be grown after the fact, so resize the *image* is not enough —
  rebuild with a larger `HAIKU_IMAGE_SIZE` if you need more, or attach a
  second BFS-formatted disk as scratch.
- **No serial shell:** Haiku's serial port is kernel-debug only, so SSH is
  the only admin channel — make sure networking/keys are correct before
  relying on a headless deploy.
