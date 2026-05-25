# VM Provisioning Scripts

Automated provisioning scripts for creating build VMs on an Incus cluster.
Each script is fully self-contained: it creates the VM, installs the OS,
configures networking/SSH, and installs the build toolchain.

These VMs are used for cross-platform building with the libcvc-deps/cvcpkg system.
Supported platforms: FreeBSD, OpenBSD, NetBSD, and HaikuOS.

## Prerequisites

- Incus cluster with at least one node
- `expect` installed on the Incus host(s) (for OpenBSD and NetBSD)
- Internet access (ISOs and packages are downloaded automatically)

## Cluster Setup

Two-node Incus cluster on Ubuntu 22.04 (Zabbly repo, Incus 7.0.x):
- **star-01** (10.66.77.162) — database-leader
- **star-00** (10.66.77.217) — database-standby
- Network: `incusbr0` bridge (10.99.0.1/24, NAT)
- Storage: dir-backed pool "default"

## Quick Start

Each provisioning script takes optional arguments for VM name and target node,
making it easy to create multiple instances or test on different nodes:

```bash
# FreeBSD — uses pre-built cloud image, no ISO needed
bash provision-freebsd.sh [VM_NAME] [TARGET_NODE]
bash provision-freebsd.sh                          # freebsd-build on any node
bash provision-freebsd.sh freebsd-test star-00      # named VM on specific node

# OpenBSD — downloads ISO, runs automated installer
expect provision-openbsd.exp [VM_NAME] [TARGET_NODE] [ISO_PATH]
expect provision-openbsd.exp                        # openbsd-build, downloads ISO
expect provision-openbsd.exp openbsd-test star-01   # named VM on specific node

# NetBSD — multi-phase: install, rescue-fix config, set password, install tools
expect provision-netbsd.exp [VM_NAME] [TARGET_NODE] [ISO_PATH]
expect provision-netbsd.exp                         # netbsd-build, downloads ISO
expect provision-netbsd.exp netbsd-test star-00     # named VM on specific node

# HaikuOS — hybrid: automated VM creation + manual graphical install + automated tools
bash provision-haiku.sh [VM_NAME] [TARGET_NODE] [ISO_PATH]
bash provision-haiku.sh                             # haiku-build on any node
bash provision-haiku.sh --post-install haiku-build 10.99.0.50  # post-install with IP
```

## VM Defaults

| Property | Value |
|---|---|
| vCPUs | 4 |
| Memory | 4 GiB |
| Root Disk | 50 GiB |
| Network | DHCP via incusbr0 |

## VM Summary

| VM | OS | Credentials | Key Build Tools |
|---|---|---|---|
| freebsd-build | FreeBSD 14.4 | `incus exec` (no password) | cmake, clang 19, gcc14, ninja, python3, autotools |
| openbsd-build | OpenBSD 7.7 | root/build123, user builder/build123 | cmake, clang 16, GCC 11, ninja, python3, autotools |
| netbsd-build | NetBSD 10.1 | root/build1234567 | cmake 4.2, clang 19, gcc14, ninja, python 3.13, autotools |
| haiku-build | HaikuOS R1/beta5 | user (set password via VGA) | cmake, gcc, clang 18, ninja, python3.11, autotools |

## Scripts

### Provisioning (fully automated, end-to-end)

| Script | Description |
|---|---|
| `provision-freebsd.sh` | Create FreeBSD VM from cloud image + install tools |
| `provision-openbsd.exp` | Create VM, download ISO, install OpenBSD, install tools |
| `provision-netbsd.exp` | Create VM, download ISO, install NetBSD (3 phases), install tools |
| `provision-haiku.sh` | Create VM, download ISO, boot live system (manual graphical install required), post-install SSH+tools |

### Standalone (for manual use or re-running individual steps)

| Script | Description |
|---|---|
| `install-openbsd.exp` | OpenBSD installer only (VM must exist with ISO attached) |
| `install-netbsd.exp` | NetBSD installer only (VM must exist with ISO attached) |
| `fix-netbsd-postinstall.exp` | NetBSD post-install config fix (serial, sshd, DHCP) |
| `netbsd-set-password.exp` | Set NetBSD root password via single-user boot |
| `setup-freebsd-tools.sh` | Install FreeBSD build toolchain |
| `setup-openbsd-tools.sh` | Install OpenBSD build toolchain |
| `setup-netbsd-tools.sh` | Install NetBSD build toolchain |
| `setup-haiku-tools.sh` | Install HaikuOS build toolchain (pkgman) |

## Key Lessons Learned

- **OpenBSD**: Must `set tty com0` at `boot>` prompt for serial console; username `build` is reserved; use `G` for GPT partitioning
- **NetBSD boot menu**: SPACE stops countdown, then `3\r` (with RETURN) selects boot prompt; after `consdev com0` the bootloader redraws the banner — must wait for `\n> ` before sending more commands
- **NetBSD installer**: Curses-based menus require timing-based `send` with `sleep` between keystrokes; "b" = default partition sizes (not "a")
- **NetBSD post-install**: Menu letters: a=network, b=timezone, c=shell, d=password, e=binary_pkgs, f=pkgsrc, g=sshd, o=add_user, x=finish
- **NetBSD rescue**: ISO boots to read-only root; must `mount -t tmpfs tmpfs /tmp` for scratch; `fsck -y` before mounting installed partition
- **NetBSD password**: Uses argon2id by default; externally-generated bcrypt ($2b$) hashes don't work; use `passwd` in single-user mode
- **FreeBSD**: Cloud images from linuxcontainers.org work out of the box — no ISO or installer needed
- **TCL/expect escaping**: Use `{...}` braces around shell commands with `$`, `[`, `]` to prevent TCL substitution
- **HaikuOS**: No serial console installer — must use VGA console (SPICE) for graphical install; no Incus agent so IP must be discovered via ARP or manual `ifconfig`; **does NOT support virtio-scsi** (kernel panic at boot) — must use `io.bus=virtio-blk` for disk I/O; anyboot ISO must be DD'd to root.img and partition table converted to GPT (`sgdisk -g`) since it cannot be attached as a separate Incus disk device; virtio-net NIC may need manual DHCP (`ifconfig /dev/net/virtio_net/0 auto`); ISO must be the `anyboot` variant; OSUOSL mirror is reliable (`ftp.osuosl.org`); default user is "user" with empty password
