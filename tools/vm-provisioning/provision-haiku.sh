#!/bin/bash
#
# HaikuOS R1/beta5 VM provisioning on Incus.
#
# Unlike the BSDs, Haiku uses a graphical installer — there is no text-mode
# or serial console installer. This script handles:
#   Phase 1: VM creation, ISO download, write to disk, boot (automated)
#   Phase 2: Graphical installation via VGA console (MANUAL — user required)
#   Phase 3: Post-install SSH + build tools setup (automated)
#
# NOTE: Haiku R1/beta5 does not support virtio-scsi, so this script uses
# virtio-blk for disk I/O and writes the anyboot ISO directly to root.img
# instead of attaching it as a separate Incus disk device.
#
# Usage:
#   bash provision-haiku.sh [VM_NAME] [TARGET_NODE] [ISO_PATH]
#   bash provision-haiku.sh --post-install VM_NAME [IP]
#
# Examples:
#   bash provision-haiku.sh                          # haiku-build on any node
#   bash provision-haiku.sh haiku-test star-01
#   bash provision-haiku.sh haiku-build star-01 /tmp/haiku-r1beta5-x86_64-anyboot.iso
#   bash provision-haiku.sh --post-install haiku-build 10.99.0.50
#
# After running this script:
#   1. Connect to VGA console:  incus console VM_NAME --type=vga
#      (opens a SPICE viewer — install virt-viewer/remote-viewer)
#   2. In the Haiku live desktop, open Installer
#   3. Open DriveSetup, create BFS partition in the free space (~48GB)
#   4. Close DriveSetup, select source & destination, click "Begin"
#   5. After install completes, click "Restart"
#   6. Configure networking and SSH from Haiku Terminal
#   7. Run: bash provision-haiku.sh --post-install VM_NAME <IP>
#
set -euo pipefail

# --- Parse arguments ---
if [ "${1:-}" = "--post-install" ]; then
    POST_INSTALL=true
    VM_NAME="${2:-haiku-build}"
    shift 2 || true
else
    POST_INSTALL=false
    VM_NAME="${1:-haiku-build}"
    TARGET="${2:-}"
    ISO_PATH="${3:-/tmp/haiku-r1beta5-x86_64-anyboot.iso}"
fi

ISO_URL="https://ftp.osuosl.org/pub/haiku/r1beta5/haiku-r1beta5-x86_64-anyboot.iso"
ISO_SHA256="22ae312a38e98083718b6984186e753d15806bd6ea44542144fdcef42c4dcb69"

# ===========================================================================
# POST-INSTALL: Enable SSH and install build tools
# ===========================================================================
if [ "$POST_INSTALL" = true ]; then
    echo "=== Post-install setup for ${VM_NAME} ==="

    # Haiku has no Incus agent, so 'incus list' cannot detect the IP.
    # We find the IP by looking up the VM's MAC in the ARP table or
    # via the user-supplied IP argument.
    IP="${3:-}"
    MAC=$(incus config get "$VM_NAME" volatile.eth0.hwaddr 2>/dev/null || true)

    if [ -z "$IP" ] && [ -n "$MAC" ]; then
        echo "[1/4] Discovering VM IP via ARP (MAC=$MAC) ..."
        IP=$(arp -an 2>/dev/null | grep -i "$MAC" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' || true)
    fi

    if [ -z "$IP" ]; then
        echo "ERROR: Could not determine VM IP automatically."
        echo ""
        echo "  Haiku has no Incus agent, so IP detection requires ARP."
        echo "  You can find the IP from within the Haiku VM:"
        echo "    incus console $VM_NAME --type=vga"
        echo "  Then in Haiku's Terminal: ifconfig"
        echo ""
        echo "  Re-run with the IP:"
        echo "    bash provision-haiku.sh --post-install $VM_NAME <IP>"
        exit 1
    fi
    echo "  VM IP: $IP"

    echo "[2/4] Checking SSH connectivity ..."
    if timeout 5 bash -c "echo | nc -w 3 $IP 22" 2>/dev/null | grep -q SSH; then
        echo "  SSH already running on $IP:22"
    else
        echo ""
        echo "  *** SSH is not yet enabled on the Haiku VM. ***"
        echo ""
        echo "  Connect to VGA console and open Terminal:"
        echo "    incus console $VM_NAME --type=vga"
        echo ""
        echo "  In the Haiku Terminal, run:"
        echo "    pkgman install openssh"
        echo "    ssh-keygen -A"
        echo "    /boot/system/bin/sshd"
        echo "    mkdir -p ~/config/settings/boot/launch"
        echo "    ln -sf /boot/system/bin/sshd ~/config/settings/boot/launch/"
        echo ""
        echo "  After enabling SSH, re-run:"
        echo "    bash provision-haiku.sh --post-install $VM_NAME $IP"
        exit 1
    fi

    echo "[3/4] Setting password for 'user' account via SSH ..."
    # Haiku's default user is "user" with empty password.
    # sshd may reject empty-password logins; set a password first via VGA.
    echo "  (If SSH auth fails, set a password via VGA console first:"
    echo "   passwd user)"

    echo "[4/4] Installing build tools via SSH ..."
    ssh -o StrictHostKeyChecking=no user@"$IP" << 'TOOLS_EOF'
pkgman install -y cmake git ninja pkgconf curl wget make autoconf automake libtool python3.11 gcc

echo ""
echo "=== HaikuOS build tools installed ==="
uname -a
cmake --version | head -1
git --version
python3 --version 2>&1 || python3.11 --version 2>&1
gcc --version | head -1
df -h /boot
TOOLS_EOF

    echo ""
    echo "=== HaikuOS VM ready ==="
    echo "  Name:   $VM_NAME"
    echo "  IP:     $IP"
    echo "  SSH:    ssh user@$IP"
    echo "  OS:     HaikuOS R1/beta5 (x86_64)"
    exit 0
fi

# ===========================================================================
# PHASE 1: Create VM, write ISO to disk, and boot
# ===========================================================================
echo "=== Provisioning HaikuOS VM: ${VM_NAME} ==="

# --- Download ISO if needed ---
if [ ! -f "$ISO_PATH" ]; then
    echo "[1/5] Downloading Haiku R1/beta5 ISO ..."
    curl -sL -o "$ISO_PATH" "$ISO_URL"
else
    echo "[1/5] Using existing ISO: $ISO_PATH"
fi

# Verify ISO size (should be ~1.4GB)
ISO_SIZE=$(stat -c%s "$ISO_PATH" 2>/dev/null || stat -f%z "$ISO_PATH" 2>/dev/null)
if [ "$ISO_SIZE" -lt 1000000000 ]; then
    echo "ERROR: ISO too small (${ISO_SIZE} bytes) — download may have failed"
    exit 1
fi

# Verify checksum
echo "  Verifying SHA256 checksum ..."
ACTUAL_SHA=$(sha256sum "$ISO_PATH" | awk '{print $1}')
if [ "$ACTUAL_SHA" != "$ISO_SHA256" ]; then
    echo "ERROR: SHA256 mismatch!"
    echo "  Expected: $ISO_SHA256"
    echo "  Got:      $ACTUAL_SHA"
    exit 1
fi
echo "  Checksum OK"

# --- Create VM ---
echo "[2/5] Creating VM ..."
TARGET_ARG=""
if [ -n "${TARGET:-}" ]; then
    TARGET_ARG="--target $TARGET"
fi

# CRITICAL: Haiku R1/beta5 does not support virtio-scsi (kernel panic).
# We must use virtio-blk for the root disk (io.bus=virtio-blk).
incus init "$VM_NAME" --vm --empty \
    -c limits.cpu=4 \
    -c limits.memory=4GiB \
    -c security.secureboot=false \
    -d root,size=50GiB \
    $TARGET_ARG

incus config device set "$VM_NAME" root io.bus=virtio-blk

# --- Write ISO to root disk ---
# Haiku's kernel lacks virtio-scsi drivers, so attaching the ISO as a
# separate Incus disk device does not work (the kernel cannot read it).
# Instead, we DD the anyboot ISO directly to root.img and convert the
# partition table from MBR to GPT for UEFI boot.
echo "[3/5] Writing ISO to root disk ..."

# Determine root.img path (requires running on or having access to the
# target node's filesystem)
LOCATION=$(incus info "$VM_NAME" | grep "^Location:" | awk '{print $2}')
POOL_PATH="/var/lib/incus/storage-pools/default/virtual-machines/${VM_NAME}"
ROOT_IMG="${POOL_PATH}/root.img"

if [ "$(hostname)" = "$LOCATION" ]; then
    # Running on the target node
    sudo dd if="$ISO_PATH" of="$ROOT_IMG" bs=4M conv=notrunc status=progress
    echo "[4/5] Converting partition table to GPT ..."
    sudo sgdisk -g "$ROOT_IMG"
else
    echo "  VM is on node '$LOCATION' but we are on '$(hostname)'."
    echo "  The ISO must be written on the target node."
    echo ""
    echo "  Copy the ISO to the target node and run:"
    echo "    sudo dd if=<ISO_PATH> of=$ROOT_IMG bs=4M conv=notrunc status=progress"
    echo "    sudo sgdisk -g $ROOT_IMG"
    echo ""
    echo "  Then start the VM:"
    echo "    incus start $VM_NAME"
    echo ""
    echo "  Alternatively, re-run this script on node '$LOCATION'."
    exit 0
fi

# --- Start VM ---
echo "[5/5] Starting VM (boots into Haiku live desktop) ..."
incus start "$VM_NAME"

echo ""
echo "============================================================"
echo "  HaikuOS VM created and booting from disk."
echo ""
echo "  The anyboot ISO has been written to the root disk. Haiku will"
echo "  boot into a live desktop from the 1.4GB BFS partition, with"
echo "  ~48GB of free space available for a permanent installation."
echo ""
echo "  NEXT STEPS (graphical install required):"
echo ""
echo "  1. Connect to the VGA console:"
echo "       incus console $VM_NAME --type=vga"
echo "     (requires remote-viewer / virt-viewer installed)"
echo ""
echo "  2. In the Haiku live desktop:"
echo "     a. Open Installer (should appear automatically)"
echo "     b. Click 'Set up partitions...' to open DriveSetup"
echo "     c. Select the free space on the disk"
echo "     d. Create a new BFS partition (use all remaining space)"
echo "     e. Close DriveSetup"
echo "     f. In Installer: select source and target partition"
echo "     g. Click 'Begin' and wait for installation"
echo "     h. Click 'Restart' when done"
echo ""
echo "  3. Configure networking (after install or in live desktop):"
echo "     In Haiku Terminal:"
echo "       ifconfig /dev/net/virtio_net/0 up"
echo "       ifconfig /dev/net/virtio_net/0 auto"
echo "       ifconfig | grep 'inet addr'"
echo ""
echo "  4. Enable SSH:"
echo "     In Haiku Terminal:"
echo "       pkgman install openssh"
echo "       ssh-keygen -A"
echo "       /boot/system/bin/sshd"
echo "       mkdir -p ~/config/settings/boot/launch"
echo "       ln -sf /boot/system/bin/sshd ~/config/settings/boot/launch/"
echo ""
echo "  5. Install build tools automatically:"
echo "       bash provision-haiku.sh --post-install $VM_NAME <IP>"
echo "============================================================"
