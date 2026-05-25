#!/bin/bash
#
# Fully automated FreeBSD 14.4 VM provisioning on Incus.
# Creates the VM from a pre-built cloud image, starts it,
# and installs the build toolchain.
#
# FreeBSD cloud images from images.linuxcontainers.org come
# pre-configured with DHCP, serial console, and root access
# via `incus exec`, so no manual install or ISO is needed.
#
# Usage:
#   bash provision-freebsd.sh [VM_NAME] [TARGET_NODE]
#
# Examples:
#   bash provision-freebsd.sh                     # freebsd-build on any node
#   bash provision-freebsd.sh freebsd-test star-00
#
# Environment overrides:
#   FREEBSD_IMAGE   - image alias (default: images:freebsd/14.4/default)
#   VM_CPUS         - vCPU count (default: 4)
#   VM_MEMORY       - memory size (default: 4GiB)
#   VM_DISK         - root disk size (default: 50GiB)
#
set -euo pipefail

VM_NAME="${1:-freebsd-build}"
TARGET="${2:-}"

FREEBSD_IMAGE="${FREEBSD_IMAGE:-images:freebsd/14.4/default}"
VM_CPUS="${VM_CPUS:-4}"
VM_MEMORY="${VM_MEMORY:-4GiB}"
VM_DISK="${VM_DISK:-50GiB}"

cleanup() {
    local rc=$?
    if [ $rc -ne 0 ]; then
        echo "ERROR: Provisioning failed (exit code $rc)."
        echo "  VM '$VM_NAME' may be left in a partial state."
        echo "  To clean up: incus delete $VM_NAME --force"
    fi
}
trap cleanup EXIT

echo "=== Provisioning FreeBSD VM: ${VM_NAME} ==="

# --- Create VM from remote image ---
TARGET_ARG=""
if [ -n "$TARGET" ]; then
    TARGET_ARG="--target $TARGET"
fi

echo "[1/4] Creating VM from ${FREEBSD_IMAGE} ..."
incus init "$FREEBSD_IMAGE" "$VM_NAME" --vm \
    -c limits.cpu="$VM_CPUS" \
    -c limits.memory="$VM_MEMORY" \
    -c security.secureboot=false \
    -d root,size="$VM_DISK" \
    $TARGET_ARG

echo "[2/4] Starting VM ..."
incus start "$VM_NAME"

echo "[3/4] Waiting for VM agent ..."
agent_ready=false
for i in $(seq 1 60); do
    if incus exec "$VM_NAME" -- true 2>/dev/null; then
        agent_ready=true
        echo "  Agent ready after $((i * 5)) seconds"
        break
    fi
    sleep 5
done

if [ "$agent_ready" != "true" ]; then
    echo "ERROR: VM agent not available after 5 minutes"
    exit 1
fi

echo "[4/4] Installing build tools ..."
incus exec "$VM_NAME" -- sh -c '
    set -e
    pkg install -y \
        cmake git ninja pkgconf curl wget gmake \
        autoconf automake libtool \
        python3 py311-pip \
        llvm18 gcc14

    echo ""
    echo "=== FreeBSD build tools installed ==="
    uname -sr
    cmake --version | head -1
    git --version
    ninja --version
    gcc14 --version | head -1
    clang --version | head -1
    python3 --version
'

IP=$(incus list "$VM_NAME" -f csv -c 4 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1)
echo ""
echo "=== FreeBSD VM ready ==="
echo "  Name:   ${VM_NAME}"
echo "  IP:     ${IP:-unknown}"
echo "  Access: incus exec ${VM_NAME} -- sh"
echo "  OS:     $(incus exec "$VM_NAME" -- uname -sr)"
