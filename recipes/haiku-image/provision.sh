#!/bin/bash
#
# One-command provisioner for the haiku-image builder VM.
#
# Haiku ships no text-mode installer and no serial console, so a builder VM is
# not "installed" — it is imported from this pre-built image and, if the image
# was published without a key, has an SSH public key injected into its BFS
# volume BEFORE first boot. This script does both, then boots the VM under
# Incus, waits for it, and verifies the login. It is the runnable form of the
# procedure documented in README.md ("Access" and "Incus (VM)").
#
# It is deliberately generic: everything hypervisor- and image-specific is read
# from the image descriptor the recipe ships (`cvcpkg image env haiku-image`),
# not hard-coded, so there is nothing site-specific to edit.
#
#   Boot bus, firmware, secureboot, RAM/CPU/disk floors, NIC model and the
#   login account all come from CVCPKG_IMAGE_* (see README.md "Disk bus" and
#   "Access"). The one thing the image cannot decide for you is the SSH key —
#   a published image trusts no key — so you pass --pubkey and, to write it
#   offline, --bfs-shell.
#
# Two things need privilege and are therefore LOCAL to the machine that holds
# the image file: the key injection loop-mounts the image and drives bfs_shell
# as root. Neither goes through the Incus API, so run this on the machine where
# the image file physically is.
#
# Usage:
#   bash provision.sh [options]
#
#   --image PATH      qcow2/raw image      (default: `cvcpkg image path haiku-image`)
#   --pubkey PATH     SSH public key to trust: a file path or the key text
#                     itself. Omit only if the image already carries a key.
#   --bfs-shell PATH  Haiku's bfs_shell, for offline key injection
#                     (default: `cvcpkg image path bfs-shell` / bfs_shell on PATH).
#                     Needs >= 1.0.0-beta.5+cvc.2 (older builds cannot read the
#                     host key file once relocated — see README.md).
#   --name NAME       Incus instance name  (default: haiku-builder)
#   --network NET     Incus managed bridge (default: incusbr0)
#   --ssh-user USER   login account        (default: from the descriptor, else 'user')
#   --disk SIZE       root volume          (default: descriptor floor, e.g. 10GiB)
#   --cpus N          vCPUs                (default: descriptor floor, else 4)
#   --memory SIZE     RAM                  (default: descriptor floor, else 4GiB)
#   --alias ALIAS     image store alias    (default: haiku-builder)
#   --timeout SECS    boot+SSH wait        (default: 600)
#   --inject-only     inject the key into --image and exit (do not import/boot).
#                     Use this to prepare an image you will boot with plain QEMU
#                     or another hypervisor per README.md.
#   --recreate        delete and rebuild the VM/image first
#   --help
#
# Examples:
#   # Full: inject a key, import, boot and verify under Incus.
#   cvcpkg install haiku-image bfs-shell
#   bash "$(dirname "$(cvcpkg image path haiku-image)")/provision.sh" \
#       --pubkey ~/.ssh/id_ed25519.pub
#
#   # Just write a key into an image you will boot with QEMU yourself:
#   bash provision.sh --image ./disk.qcow2 --pubkey ~/.ssh/id_ed25519.pub \
#       --bfs-shell ./bfs-tool/bin/bfs_shell --inject-only
#
set -euo pipefail

IMAGE=""
PUBKEY_IN=""
BFS_SHELL=""
VM_NAME="haiku-builder"
NETWORK="incusbr0"
SSH_USER=""
VM_DISK=""
VM_CPUS=""
VM_MEMORY=""
IMAGE_ALIAS="haiku-builder"
BOOT_TIMEOUT="600"
INJECT_ONLY=false
RECREATE=false
META=""

usage() { sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//;$d'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --image)       IMAGE="$2";        shift 2 ;;
        --pubkey)      PUBKEY_IN="$2";    shift 2 ;;
        --bfs-shell)   BFS_SHELL="$2";    shift 2 ;;
        --name)        VM_NAME="$2";      shift 2 ;;
        --network)     NETWORK="$2";      shift 2 ;;
        --ssh-user)    SSH_USER="$2";     shift 2 ;;
        --disk)        VM_DISK="$2";      shift 2 ;;
        --cpus)        VM_CPUS="$2";      shift 2 ;;
        --memory)      VM_MEMORY="$2";    shift 2 ;;
        --alias)       IMAGE_ALIAS="$2";  shift 2 ;;
        --timeout)     BOOT_TIMEOUT="$2"; shift 2 ;;
        --inject-only) INJECT_ONLY=true;  shift ;;
        --recreate)    RECREATE=true;     shift ;;
        -h|--help)     usage; exit 0 ;;
        *) echo "ERROR: unknown argument '$1' (try --help)" >&2; exit 2 ;;
    esac
done

WORK_DIR=""
cleanup() { [ -n "$WORK_DIR" ] && rm -rf "$WORK_DIR"; }
trap cleanup EXIT

# ── Resolve image + descriptor facts ────────────────────────────────────
# Everything the hypervisor needs is in the descriptor the recipe ships, so
# read it rather than hard-coding constants. `cvcpkg image env` prints the
# CVCPKG_IMAGE_* facts; it is optional — if cvcpkg is not on PATH the defaults
# below (which match the shipped image) apply, and --flags always win.
if command -v cvcpkg >/dev/null 2>&1; then
    [ -n "$IMAGE" ] || IMAGE="$(cvcpkg image path haiku-image 2>/dev/null || true)"
    [ -n "$META" ]  || META="$(cvcpkg image path haiku-image --role incus-metadata 2>/dev/null || true)"
    eval "$(cvcpkg image env haiku-image 2>/dev/null || true)"
fi

# bfs_shell is a regular package (cvcpkg install bfs-shell), NOT an image, so it
# lands at <prefix>/bin/bfs_shell — look there and on PATH.
if [ -z "$BFS_SHELL" ]; then
    if command -v bfs_shell >/dev/null 2>&1; then
        BFS_SHELL="$(command -v bfs_shell)"
    elif [ -n "${CVCPKG_PREFIX:-}" ] && [ -x "${CVCPKG_PREFIX}/bin/bfs_shell" ]; then
        BFS_SHELL="${CVCPKG_PREFIX}/bin/bfs_shell"
    fi
fi

# Descriptor floors → defaults (flags already set win; keep whatever is set).
VM_DISK="${VM_DISK:-${CVCPKG_IMAGE_DISK_MIN_GIB:+${CVCPKG_IMAGE_DISK_MIN_GIB}GiB}}"
VM_DISK="${VM_DISK:-10GiB}"
VM_CPUS="${VM_CPUS:-${CVCPKG_IMAGE_CPU_MIN:-4}}"
VM_MEMORY="${VM_MEMORY:-${CVCPKG_IMAGE_MEMORY_MIN_MIB:+${CVCPKG_IMAGE_MEMORY_MIN_MIB}MiB}}"
VM_MEMORY="${VM_MEMORY:-4GiB}"
SSH_USER="${SSH_USER:-${CVCPKG_IMAGE_SSH_USER:-user}}"
# nvme/UEFI/secureboot are MEASURED facts about Haiku, not preferences — see
# README.md "Disk bus — the one mandatory setting". Read them, do not invent.
DISK_BUS="${CVCPKG_IMAGE_DISK_BUS:-nvme}"
SECUREBOOT="${CVCPKG_IMAGE_SECUREBOOT:-false}"

command -v incus   >/dev/null 2>&1 || { [ "$INJECT_ONLY" = true ] || { echo "ERROR: incus not on PATH" >&2; exit 1; }; }
command -v qemu-img >/dev/null 2>&1 || { echo "ERROR: qemu-img not on PATH (install qemu-utils)" >&2; exit 1; }
[ -n "$IMAGE" ] || { echo "ERROR: no --image and 'cvcpkg image path haiku-image' found nothing. Pass --image." >&2; exit 2; }
[ -r "$IMAGE" ] || { echo "ERROR: image '$IMAGE' is not readable" >&2; exit 1; }

# Resolve the public key: accept a file path or the key text itself.
PUBKEY=""
if [ -n "$PUBKEY_IN" ]; then
    if [ -r "$PUBKEY_IN" ]; then PUBKEY="$(cat "$PUBKEY_IN")"; else PUBKEY="$PUBKEY_IN"; fi
    case "$PUBKEY" in
        ssh-*|ecdsa-*|sk-*) ;;
        *) echo "ERROR: --pubkey is neither a readable file nor an OpenSSH public key" >&2; exit 2 ;;
    esac
fi

IMG_FORMAT="$(qemu-img info "$IMAGE" | sed -n 's/^file format: *//p' | head -1)"
[ -n "$IMG_FORMAT" ] || { echo "ERROR: qemu-img could not identify '$IMAGE'" >&2; exit 1; }

# ===========================================================================
# Stage the disk: optional offline SSH key injection, then qcow2
# ===========================================================================
STAGED_IMAGE="$IMAGE"
INJECTED=false
if [ -n "$PUBKEY" ]; then
    [ -n "$BFS_SHELL" ] && [ -x "$BFS_SHELL" ] || {
        echo "ERROR: --pubkey needs Haiku's bfs_shell to write the key offline." >&2
        echo "       Install it (cvcpkg install bfs-shell) or pass --bfs-shell PATH." >&2
        echo "       Linux cannot write BFS otherwise — its befs driver is read-only." >&2
        exit 1
    }
    command -v losetup >/dev/null 2>&1 || { echo "ERROR: losetup not on PATH (key injection is local-only)" >&2; exit 1; }
    if [ "$(id -u)" -ne 0 ] && ! sudo -n true >/dev/null 2>&1; then
        echo "ERROR: key injection loop-mounts the image and runs bfs_shell as root," >&2
        echo "       but this is not root and passwordless sudo is unavailable. Run it" >&2
        echo "       on the machine that holds the image, with root." >&2
        exit 1
    fi

    WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/haiku-provision.XXXXXX")"
    echo "==> Injecting SSH key offline with bfs_shell ..."
    # losetup -P exposes only a RAW disk's partition table, so convert first.
    if [ "$IMG_FORMAT" = "raw" ]; then
        cp --sparse=always "$IMAGE" "$WORK_DIR/disk.raw"
    else
        qemu-img convert -f "$IMG_FORMAT" -O raw "$IMAGE" "$WORK_DIR/disk.raw"
    fi
    printf '%s\n' "$PUBKEY" > "$WORK_DIR/authorized_keys"

    LOOP="$(sudo losetup -f -P --show "$WORK_DIR/disk.raw")"
    if [ ! -e "${LOOP}p1" ]; then
        echo "ERROR: no partition 1 on the image — not a haiku-image anyboot (MBR, BFS in p1)." >&2
        sudo losetup -d "$LOOP" 2>/dev/null || true
        exit 1
    fi

    # bfs_shell scripted-session conventions (same as the recipe's build.sh):
    #  * ':' prefixes a HOST path.
    #  * GUEST paths are absolute under /myfs — fssh mounts the volume there
    #    with cwd '/', so a bare `home/...` resolves to /home/... and is lost.
    #  * THE authorized_keys PATH IS config/settings/ssh, NOT ~/.ssh — Haiku's
    #    sshd_config sets AuthorizedKeysFile config/settings/ssh/authorized_keys.
    #  * modes must be set explicitly (fssh's cp drops the host mode) or
    #    OpenSSH's StrictModes ignores the key.
    #
    # A relocated bfs_shell needs a WRITABLE attribute sidecar for the host-file
    # cp; its baked default path does not exist here, so point it at scratch we
    # own. sudo scrubs the environment, so set it on the command via `sudo env`.
    # (bfs-shell >= 1.0.0-beta.5+cvc.2 honours this; see README.md.)
    ATTRS_DIR="$WORK_DIR/attrs"; mkdir -p "$ATTRS_DIR"
    bfs() { sudo env HAIKU_BUILD_ATTRIBUTES_DIR="$ATTRS_DIR" "$BFS_SHELL" "${LOOP}p1"; }

    # mkdir is not recursive; the dir usually already exists in a built image,
    # so run it in a throwaway session where "already exists" is harmless.
    bfs >/dev/null 2>&1 <<'MK' || true
mkdir /myfs/home/config/settings/ssh
sync
quit
MK
    # DO NOT gate on bfs_shell's exit status: fssh returns non-zero when its
    # final unmount reports "Unmounting FS failed: Device or resource busy"
    # even though the cp/chmod/sync succeeded. Correctness is established by the
    # read-back below, exactly as the recipe's own build.sh does it.
    bfs <<INJ >/dev/null 2>&1 || true
cp :${WORK_DIR}/authorized_keys /myfs/home/config/settings/ssh/authorized_keys
chmod 700 /myfs/home/config/settings/ssh
chmod 600 /myfs/home/config/settings/ssh/authorized_keys
sync
quit
INJ

    # Verify by reading the file BACK with a fresh session and matching the key
    # body. `cat` to stdout (not a host cp) needs no attribute sidecar and is
    # immune to loop-device read-back quirks, so it is the trustworthy check. A
    # bare presence test would pass on the EMPTY authorized_keys a keyless image
    # already ships, so match a unique slice of the key we wrote.
    KEY_BODY="$(printf '%s' "$PUBKEY" | awk '{print $2}')"
    KEY_BODY="${KEY_BODY:0:40}"
    if bfs 2>/dev/null <<'RB' | grep -qF "$KEY_BODY"
cat /myfs/home/config/settings/ssh/authorized_keys
quit
RB
    then
        INJECTED=true
        echo "    key injected into /boot/home/config/settings/ssh/authorized_keys (verified)"
    else
        echo "ERROR: key injection could not be verified — the staged image trusts no key." >&2
        sudo losetup -d "$LOOP" 2>/dev/null || true
        exit 1
    fi
    sudo losetup -d "$LOOP" 2>/dev/null || true

    if [ "$INJECT_ONLY" = true ]; then
        # Write the keyed image back in the caller's format, next to --image.
        OUT="${IMAGE%.*}.keyed.qcow2"
        qemu-img convert -f raw -O qcow2 "$WORK_DIR/disk.raw" "$OUT"
        echo "==> Wrote keyed image: $OUT"
        echo "    Boot it with your hypervisor per README.md (bus=$DISK_BUS, UEFI, secureboot=$SECUREBOOT)."
        exit 0
    fi
    qemu-img convert -f raw -O qcow2 "$WORK_DIR/disk.raw" "$WORK_DIR/disk.qcow2"
    STAGED_IMAGE="$WORK_DIR/disk.qcow2"
elif [ "$INJECT_ONLY" = true ]; then
    echo "ERROR: --inject-only needs --pubkey (there is nothing to inject otherwise)." >&2
    exit 2
fi

# Incus keys the VM-vs-container decision off the qcow2 magic bytes AND a
# literal `.qcow2` extension, so make sure the staged path is qcow2.
if [ "$IMG_FORMAT" != "qcow2" ] && [ "$STAGED_IMAGE" = "$IMAGE" ]; then
    [ -n "$WORK_DIR" ] || WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/haiku-provision.XXXXXX")"
    qemu-img convert -f "$IMG_FORMAT" -O qcow2 "$IMAGE" "$WORK_DIR/disk.qcow2"
    STAGED_IMAGE="$WORK_DIR/disk.qcow2"
fi
case "$STAGED_IMAGE" in
    *.qcow2) ;;
    *) [ -n "$WORK_DIR" ] || WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/haiku-provision.XXXXXX")"
       ln -sf "$(readlink -f "$STAGED_IMAGE")" "$WORK_DIR/disk.qcow2"
       STAGED_IMAGE="$WORK_DIR/disk.qcow2" ;;
esac

if [ -z "$PUBKEY" ]; then
    echo "NOTE: no --pubkey given — importing as-is. This only works if the image"
    echo "      already carries a key; a keyless haiku-image can never be logged into"
    echo "      (its sshd disables password auth). See README.md 'Access'."
fi

# ===========================================================================
# Import into Incus, create + boot the VM
# ===========================================================================
echo "==> Importing image (alias: $IMAGE_ALIAS) ..."
if incus image info "$IMAGE_ALIAS" >/dev/null 2>&1 && [ "$RECREATE" != true ]; then
    echo "    alias '$IMAGE_ALIAS' present — reusing (--recreate to re-import)"
else
    if [ -n "$META" ] && [ -r "$META" ] && [ "$INJECTED" != true ]; then
        # Unmodified image: import the recipe's own split-image metadata tarball.
        incus image import "$META" "$STAGED_IMAGE" --alias "$IMAGE_ALIAS" --reuse
    else
        # We rewrote the disk (key injection) or have no metadata tarball, so
        # synthesise the minimal metadata Incus needs for a VM image.
        METADIR="$(mktemp -d "${TMPDIR:-/tmp}/haiku-meta.XXXXXX")"
        cat > "$METADIR/metadata.yaml" <<META_YAML
architecture: x86_64
creation_date: $(date +%s)
properties:
  description: HaikuOS builder image
  os: Haiku
  variant: builder
META_YAML
        tar -C "$METADIR" -cJf "$METADIR/metadata.tar.xz" metadata.yaml
        incus image import "$METADIR/metadata.tar.xz" "$STAGED_IMAGE" --alias "$IMAGE_ALIAS" --reuse
        rm -rf "$METADIR"
    fi
    incus image info "$IMAGE_ALIAS" | grep -qi 'Type:.*VIRTUAL-MACHINE' || {
        echo "ERROR: '$IMAGE_ALIAS' imported as a CONTAINER image, not a VM image" >&2
        echo "       (the staged disk is not qcow2)." >&2
        exit 1
    }
fi

echo "==> Creating VM '$VM_NAME' ..."
if incus info "$VM_NAME" >/dev/null 2>&1 && [ "$RECREATE" = true ]; then
    incus delete "$VM_NAME" --force
fi
if ! incus info "$VM_NAME" >/dev/null 2>&1; then
    incus init "$IMAGE_ALIAS" "$VM_NAME" --vm \
        -c limits.cpu="$VM_CPUS" \
        -c limits.memory="$VM_MEMORY" \
        -c security.secureboot="$SECUREBOOT" \
        -c security.csm=false \
        -d root,size="$VM_DISK" \
        -d root,io.bus="$DISK_BUS"
fi

# These are boot-time hardware facts about Haiku, not tunables. See README.md:
# io.bus=nvme (virtio-blk GP-faults, virtio-scsi has no driver), OVMF/UEFI with
# CSM off, secureboot off (Haiku is unsigned). Networking uses the plain virtio
# NIC, which DHCPs fine.
if [ "$(incus info "$VM_NAME" | sed -n 's/^Status: *//p')" = "RUNNING" ]; then
    incus stop "$VM_NAME" --force
fi
incus config device set "$VM_NAME" root io.bus="$DISK_BUS" size="$VM_DISK" 2>/dev/null \
    || incus config device override "$VM_NAME" root io.bus="$DISK_BUS" size="$VM_DISK"
incus config set "$VM_NAME" security.csm=false 2>/dev/null || true
incus config set "$VM_NAME" security.secureboot="$SECUREBOOT"
incus config device remove "$VM_NAME" eth0 >/dev/null 2>&1 || true
incus config device add "$VM_NAME" eth0 nic network="$NETWORK" name=eth0

echo "==> Starting VM ..."
incus start "$VM_NAME"

echo "==> Waiting for a DHCP lease and SSH (timeout ${BOOT_TIMEOUT}s) ..."
# Haiku runs no Incus agent, but `incus info` still reports the address Incus
# reads off the host side of the tap, and the managed bridge's lease table is a
# second source. Poll both.
reported_ip() {
    incus info "$VM_NAME" 2>/dev/null | awk '
        /^    [^ ].*:$/ { d=$1; sub(/:$/,"",d); next }
        d=="eth0" && $1=="inet:" { ip=$2; sub(/\/.*/,"",ip); print ip; exit }'
}
lease_ip() {
    incus network list-leases "$NETWORK" -f csv 2>/dev/null | awk -F, \
        -v n="$VM_NAME" '$1==n { print $3; exit }'
}
port_open() { timeout 5 bash -c "exec 3<>/dev/tcp/$1/22" 2>/dev/null; }

IP=""; SSH_UP=false
DEADLINE=$(( $(date +%s) + BOOT_TIMEOUT ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    [ -n "$IP" ] || IP="$(lease_ip || true)"
    [ -n "$IP" ] || IP="$(reported_ip || true)"
    if [ -n "$IP" ] && port_open "$IP"; then SSH_UP=true; echo "    SSH answering on ${IP}:22"; break; fi
    sleep 5
done
if [ "$SSH_UP" != true ]; then
    echo "" >&2
    echo "ERROR: '$VM_NAME' did not answer on port 22 within ${BOOT_TIMEOUT}s." >&2
    echo "  Console log (Haiku has no serial shell — this is the kernel ring buffer):" >&2
    incus console "$VM_NAME" --show-log 2>/dev/null | tail -40 | sed 's/^/    /' >&2 || true
    exit 1
fi

echo "==> Verifying key-based login as '$SSH_USER' ..."
if ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
       -o ConnectTimeout=10 "${SSH_USER}@${IP}" 'uname -a' >/dev/null 2>&1; then
    echo "    key-based login OK"
else
    echo "    key-based login not confirmed from here — fine if the matching PRIVATE"
    echo "    key is not on THIS host (e.g. it lives on a delegating builder). Test"
    echo "    from wherever the private half lives: ssh ${SSH_USER}@${IP}"
fi

echo ""
echo "=== HaikuOS builder VM ready ==="
echo "  Name:  $VM_NAME"
echo "  IP:    $IP ($NETWORK)"
echo "  SSH:   ssh ${SSH_USER}@${IP}"
echo "  Disk:  $VM_DISK on io.bus=$DISK_BUS   (nvme is REQUIRED — see README.md)"
echo "  Boot:  OVMF/UEFI, secureboot=$SECUREBOOT, csm=false"
echo "  Log:   incus console $VM_NAME --show-log"
