# BSD Build VM Inventory

Incus cluster hosting BSD virtual machines for cross-platform building with the libcvc-deps/cvcpkg system.

## Cluster Infrastructure

| Property | Value |
|---|---|
| Platform | Incus 7.0.0 (Zabbly repo) on Ubuntu 22.04.5 LTS |
| Nodes | **star-01** (10.66.77.162, leader), **star-00** (10.66.77.217, standby) |
| Network | incusbr0 bridge, 10.99.0.1/24, IPv4 NAT |
| Storage | dir-backed pool "default" |
| Kernel | 5.15.0-179-generic (x86_64) |
| CPU | Intel Core i5-6500T @ 2.50GHz (4 cores per node) |

## Virtual Machines

### Production VMs

| | FreeBSD | OpenBSD | NetBSD |
|---|---|---|---|
| **VM Name** | freebsd-build | openbsd-build | netbsd-build |
| **OS Version** | FreeBSD 14.4-RELEASE | OpenBSD 7.7 | NetBSD 10.1 |
| **Host Node** | star-01 | star-00 | star-01 |
| **IP Address** | 10.99.0.139 | 10.99.0.219 | 10.99.0.41 |
| **vCPUs** | 4 | 4 | 4 |
| **Memory** | 4 GiB | 4 GiB | 4 GiB |
| **Disk (Incus)** | 50 GiB | 50 GiB | 50 GiB |
| **Disk (Guest)** | 47G avail (ZFS expanded) | ~5G partitioned (40G+ free) | 5.8G partitioned (40G+ free) |

### HaikuOS VM

| Property | Value |
|---|---|
| **VM Name** | haiku-build |
| **OS Version** | HaikuOS R1/beta5 (x86_64) |
| **Host Node** | star-00 |
| **IP Address** | (discover via VGA console `ifconfig`) |
| **vCPUs** | 4 |
| **Memory** | 4 GiB |
| **Disk (Incus)** | 50 GiB |
| **Console** | VGA only (`incus console haiku-build --type=vga`) |
| **SSH User** | user (password set via VGA Terminal) |
| **Package Mgr** | pkgman (HaikuPorts) |
| **Notes** | No Incus agent; no serial console; graphical install required |

### Validation VMs (created to test provisioning scripts)

| | FreeBSD | OpenBSD | NetBSD |
|---|---|---|---|
| **VM Name** | freebsd-test | openbsd-test | netbsd-test |
| **OS Version** | FreeBSD 14.4-RELEASE | OpenBSD 7.7 | NetBSD 10.1 |
| **Host Node** | star-01 | star-01 | star-01 |
| **IP Address** | 10.99.0.23 | 10.99.0.101 | 10.99.0.124 |
| **vCPUs** | 4 | 4 | 4 |
| **Memory** | 4 GiB | 4 GiB | 4 GiB |
| **Disk (Guest)** | 41G avail (ZFS) | 796M avail (sd0a) | 40G avail (dk1) |

## SSH Access

| | FreeBSD | OpenBSD | NetBSD |
|---|---|---|---|
| **SSH** | via `incus exec` | `ssh root@10.99.0.219` | `ssh root@10.99.0.41` |
| **Root Password** | (no password, use incus exec) | `build123` | `build1234567` |
| **User Account** | — | `builder` / `build123` | — |
| **Root SSH Login** | N/A | yes | yes |
| **Console Access** | `incus console freebsd-build` | `incus console openbsd-build` | `incus console netbsd-build` |
| **Serial Console** | auto | com0 @ 115200 | com0 @ 115200 |

## Build Toolchain Versions

| Tool | FreeBSD 14.4 | OpenBSD 7.7 | NetBSD 10.1 |
|---|---|---|---|
| **cmake** | 3.31.10 | 3.31.6 | 4.2.3 |
| **git** | 2.53.0 | 2.50.1 | 2.53.0 |
| **clang** | 19.1.7 | 16.0.6 | 19.1.7 |
| **gcc** | 14.2.0 (ports) | 11.2.0 | 14.3.0 (pkgsrc) |
| **ninja** | 1.13.2 | 1.11.1 | 1.13.2 |
| **python** | 3.11.15 | 3.12.11 | 3.13.12 |
| **pkg mgr** | pkg (55 pkgs) | pkg_add (47 pkgs) | pkgin (62 pkgs) |
| **autotools** | autoconf, automake, libtool | autoconf 2.72, automake 1.17 | autoconf 2.72, automake 1.18 |
| **extras** | pkgconf, curl, wget, gmake | pkgconf, curl, wget, gmake | pkgconf, curl, wget, gmake |

## Provisioning Scripts

All scripts are in `libcvc-deps/tools/vm-provisioning/`:

| Script | Purpose |
|---|---|
| `install-openbsd.exp` | Automated OpenBSD 7.7 install via serial console |
| `install-netbsd.exp` | Automated NetBSD 10.1 install (base + all sets) |
| `fix-netbsd-postinstall.exp` | ISO rescue: serial console, sshd, DHCP config |
| `netbsd-set-password.exp` | Disk single-user: set root password (argon2id) |
| `setup-freebsd-tools.sh` | Install FreeBSD build toolchain |
| `setup-openbsd-tools.sh` | Install OpenBSD build toolchain |
| `setup-netbsd-tools.sh` | Install NetBSD build toolchain |
| `provision-haiku.sh` | Create HaikuOS VM, download ISO, boot live system (manual graphical install) |
| `setup-haiku-tools.sh` | Install HaikuOS build toolchain (pkgman) |
