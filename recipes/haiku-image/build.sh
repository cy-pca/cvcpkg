#!/usr/bin/env bash
# recipes/haiku-image/build.sh — build a headless HaikuOS builder image.
#
# Haiku's Installer is graphical-only, so instead of installing interactively
# we build a fully pre-configured anyboot image from source:
#   1. clone haiku + buildtools (pinned)
#   2. build the cross-toolchain + a custom "builder-anyboot" jam profile
#      (UserBuildConfig: big disk, toolchain, OpenSSH, hostname)
#   3. inject an SSH authorized_keys + UserBootscript into the image's BFS
#      with Haiku's own bfs_shell (built from the same tree)
#   4. emit a portable qcow2 + the anyboot image + import docs
#
# Output is importable into Incus, LXD, Proxmox, or plain QEMU/libvirt with
# no VGA interaction — see README-import.md.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
RECIPE_DIR="$(cd "$(dirname "$0")" && pwd)"
JOBS="${CVC_JOBS:-$(nproc 2>/dev/null || echo 4)}"

HAIKU_REF="${HAIKU_REF:-r1beta5}"
BUILDTOOLS_REF="${BUILDTOOLS_REF:-r1beta5}"
HAIKU_REPO="${HAIKU_REPO:-https://github.com/haiku/haiku.git}"
BUILDTOOLS_REPO="${BUILDTOOLS_REPO:-https://github.com/haiku/buildtools.git}"

# ── 0. Disk preflight ───────────────────────────────────────────────────
# DEFENCE IN DEPTH, not the primary mechanism.  The primary one is
# `build.min_disk_gb: 35` in recipe.yaml, which the scheduler matches against
# each builder's advertised free space so this job is never dispatched
# somewhere it cannot fit.  This check stays because the scheduler can be
# wrong: its figure is up to one heartbeat old, a co-tenant job can eat the
# volume between dispatch and here, a builder predating disk-aware scheduling
# advertises nothing at all (unknown fails open by design), and --work-dir may
# point at a different volume than the one `df` sees here.  Failing in the
# first second with a legible message beats failing at 90% with ENOSPC from
# jam.  Keep the number in sync with recipe.yaml's min_disk_gb.
HAIKU_MIN_DISK_GB="${HAIKU_MIN_DISK_GB:-35}"
if [[ -z "${HAIKU_SKIP_SPACE_CHECK:-}" ]]; then
    # -P: POSIX output, so a long device name cannot wrap onto its own line
    # and shift the field we read.  Column 4 is available (not total) KiB.
    _avail_kb="$(df -Pk "${CVC_BUILD_DIR}" 2>/dev/null | awk 'NR==2 {print $4}')"
    _avail_gb=$(( ${_avail_kb:-0} / 1024 / 1024 ))
    if [[ -n "${_avail_kb}" ]] && (( _avail_gb < HAIKU_MIN_DISK_GB )); then
        echo "ERROR: ${CVC_BUILD_DIR} has ${_avail_gb} GiB free, need ~${HAIKU_MIN_DISK_GB} GiB." >&2
        echo "       Point CVC_BUILD_DIR at a larger volume, or set HAIKU_SKIP_SPACE_CHECK=1" >&2
        exit 1
    fi
    echo "cvcpkg: disk preflight OK - ${_avail_gb} GiB free (need ~${HAIKU_MIN_DISK_GB} GiB)"
fi

# ── 1. Host build dependencies (Debian/Ubuntu) ──────────────────────────
# Haiku's build needs a specific host toolchain plus xorriso/mtools for the
# image and qemu-img for the qcow2 conversion. Best-effort; if apt or sudo
# is unavailable the builder is expected to already carry these.
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y 2>/dev/null || true
    sudo apt-get install -y --no-install-recommends \
        git nasm bc autoconf automake texinfo flex bison gawk build-essential \
        unzip wget zip less zlib1g-dev libzstd-dev xorriso libtool gcc-multilib \
        mtools qemu-utils python3 2>/dev/null || true
fi

# ── 2. Clone sources ────────────────────────────────────────────────────
cd "${CVC_BUILD_DIR}"
# Full single-branch clone of haiku (NOT --depth 1): the image build stamps
# a revision via `git describe --tags`, and the HaikuPorts binary packages
# depend on that exact `haiku` version (e.g. r1~beta5_hrev57937_5). A
# shallow clone has no tag history, so describe fails or produces a version
# lower than the packages require, deadlocking the package solver
# ("nothing provides haiku>=..."). The full branch history lets describe
# compute the real revision that matches the HaikuPorts repo.
[[ -d haiku ]]      || git clone --single-branch --branch "${HAIKU_REF}"  "${HAIKU_REPO}"      haiku
[[ -d buildtools ]] || git clone --depth 1 --branch "${BUILDTOOLS_REF}" "${BUILDTOOLS_REPO}" buildtools
# Revision determination. Haiku stamps a revision with
#   git describe --dirty --tags --match=hrev* --abbrev=1
# which needs hrev* tags. The GitHub mirror (github.com/haiku/haiku) carries
# NO tags at all — verified via `git ls-remote --tags` and the GitHub API —
# so describe finds nothing and determine_haiku_revision aborts ("you are
# using a Haiku clone without tags"). The hrev* tags live only on the
# official repo, so add it as a supplementary remote and fetch just those
# tags. Best effort: some networks block git.haiku-os.org.
git -C haiku remote add haiku-official \
    "${HAIKU_OFFICIAL_REPO:-https://git.haiku-os.org/haiku}" 2>/dev/null || true
git -C haiku fetch --quiet --no-tags haiku-official \
    "+refs/tags/hrev*:refs/tags/hrev*" 2>/dev/null || true
DESC="$(git -C haiku describe --tags --match='hrev*' --dirty --abbrev=1 2>/dev/null || true)"
echo "haiku git-describe(hrev*): ${DESC:-(none)}"

# If the tag fetch worked, let the build compute the revision itself (exact
# and self-updating as the branch advances). If it did NOT (no hrev tags
# reachable), pin HAIKU_REVISION to the revision the r1beta5 HaikuPorts
# packages actually require: version becomes r1~beta5_hrev57937_5, matching
# the observed constraint `haiku>=r1~beta5_hrev57937_5`. A bare/too-low value
# (e.g. plain hrev57937 → r1~beta5_hrev57937, which sorts BELOW _5) deadlocks
# the package solver with "nothing provides haiku>=...". Haiku's build imports
# HAIKU_REVISION from the environment, bypassing the describe path entirely.
if [[ -z "${DESC}" ]]; then
    export HAIKU_REVISION="${HAIKU_REVISION:-hrev57937_5}"
    echo "No hrev tags reachable — pinning HAIKU_REVISION=${HAIKU_REVISION}"
fi

# Drop in the builder-anyboot profile.
cp "${RECIPE_DIR}/UserBuildConfig" haiku/build/jam/UserBuildConfig

# Build Haiku's Jam and put it on PATH — configure builds the cross-tools
# with make, but the image build (@builder-anyboot) is driven by jam, which
# is NOT installed system-wide.
( cd buildtools/jam && make )
JAM_BIN="$(find "${CVC_BUILD_DIR}/buildtools/jam" -maxdepth 2 -type f -name jam -perm -u+x 2>/dev/null | head -1)"
[[ -n "${JAM_BIN}" ]] || { echo "jam did not build in buildtools/jam" >&2; exit 1; }
export PATH="$(dirname "${JAM_BIN}"):${PATH}"
echo "Using jam: ${JAM_BIN}"; jam -v 2>/dev/null || true

# ── 3. Configure (builds the cross-toolchain) ───────────────────────────
cd haiku
if [[ ! -e generated/build/BuildConfig && ! -e generated.x86_64/build/BuildConfig ]]; then
    ./configure -j"${JOBS}" \
        --build-cross-tools x86_64 \
        --cross-tools-source ../buildtools
fi

# ── 4. Build the custom anyboot image ───────────────────────────────────
# The revision is resolved in step 2 above: either from real hrev* tags
# (fetched from the official repo → describe computes it exactly) or, if those
# aren't reachable, from the HAIKU_REVISION fallback exported there. Haiku's
# build imports HAIKU_REVISION from the environment, so nothing extra is
# needed here — a correctly-set value flows straight into the package version.
jam -q -j"${JOBS}" @builder-anyboot

ANYBOOT="$(ls -1 generated*/haiku-builder-anyboot.iso 2>/dev/null | head -1)"
[[ -n "${ANYBOOT}" ]] || ANYBOOT="$(ls -1 generated*/*anyboot*.iso 2>/dev/null | head -1)"
[[ -n "${ANYBOOT}" ]] || { echo "anyboot image not produced by jam" >&2; ls -R generated* | head -50; exit 1; }
echo "Built anyboot: ${ANYBOOT}"

# ── 5. Inject SSH key + UserBootscript via bfs_shell ────────────────────
# bfs_shell is a host tool that reads/writes BFS volumes directly. Build it
# from the same tree, then edit the image's BFS partition.
jam -q -j"${JOBS}" "<build>bfs_shell" 2>/dev/null || jam -q -j"${JOBS}" bfs_shell 2>/dev/null || true
BFS_SHELL="$(find generated* -type f -name bfs_shell -perm -u+x 2>/dev/null | head -1)"

INJ="${CVC_BUILD_DIR}/inject"
mkdir -p "${INJ}"
printf '%s\n' "${HAIKU_BUILDER_SSH_PUBKEY:-}" > "${INJ}/authorized_keys"
cp "${RECIPE_DIR}/UserBootscript" "${INJ}/UserBootscript"

if [[ -n "${BFS_SHELL}" ]]; then
    # The anyboot has an MBR; bfs_shell needs the BFS partition. Loop-mount
    # with -P to expose it (needs privilege — the star Linux builders have
    # passwordless sudo, same as the BSD provisioners).
    LOOP="$(sudo losetup -f -P --show "${ANYBOOT}" 2>/dev/null || true)"
    BFSPART=""
    [[ -n "${LOOP}" && -e "${LOOP}p1" ]] && BFSPART="${LOOP}p1"
    if [[ -n "${BFSPART}" ]]; then
        # bfs_shell scripted session: host paths are prefixed with ':'.
        sudo "${BFS_SHELL}" "${BFSPART}" <<BFSCMDS || echo "WARN: bfs_shell injection failed" >&2
mkdir home/.ssh
cp :${INJ}/authorized_keys home/.ssh/authorized_keys
cp :${INJ}/UserBootscript home/config/settings/boot/UserBootscript
sync
quit
BFSCMDS
        sudo losetup -d "${LOOP}" 2>/dev/null || true
    else
        echo "WARN: could not loop-mount the anyboot BFS partition; SSH key not injected." >&2
        echo "      Inject it post-import instead (see README-import.md)." >&2
    fi
else
    echo "WARN: bfs_shell not built; SSH key not injected (inject post-import)." >&2
fi

# ── 6. Stage outputs: portable qcow2 + anyboot + docs ───────────────────
mkdir -p "${CVC_INSTALL_DIR}"
if command -v qemu-img >/dev/null 2>&1; then
    qemu-img convert -f raw -O qcow2 "${ANYBOOT}" "${CVC_INSTALL_DIR}/haiku-builder.qcow2"
else
    cp "${ANYBOOT}" "${CVC_INSTALL_DIR}/haiku-builder.qcow2"  # raw fallback (still qemu-bootable)
fi
cp "${ANYBOOT}" "${CVC_INSTALL_DIR}/haiku-builder-anyboot.iso"
cp "${RECIPE_DIR}/metadata.yaml"     "${CVC_INSTALL_DIR}/metadata.yaml"
cp "${RECIPE_DIR}/README-import.md"  "${CVC_INSTALL_DIR}/README-import.md"

echo "Haiku builder image staged to ${CVC_INSTALL_DIR}:"
ls -lh "${CVC_INSTALL_DIR}"
