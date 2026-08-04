#!/usr/bin/env bash
# recipes/haiku-image/build.sh — build a headless HaikuOS builder image.
#
# THIS SCRIPT RUNS ON LINUX AND PRODUCES A HAIKU DISK IMAGE. It is not a Haiku
# package build; see the build block in recipe.yaml for why there is no
# `platform: haiku` matrix entry.
#
# Haiku's Installer is graphical-only and Haiku has no getty and no
# virtio-console, so there is no console to install through on a headless
# hypervisor. Instead of installing interactively we build a fully
# PRE-INSTALLED, pre-configured anyboot image from source:
#   1. preflight free disk, then clone haiku + buildtools (pinned)
#   2. build the cross-toolchain + a custom "builder-anyboot" jam profile
#      (UserBuildConfig: right-sized disk, toolchain, OpenSSH, hostname)
#   3. inject authorized_keys, an sshd_config, the haikuhost work dir and
#      UserBootscript into the image's BFS with Haiku's own bfs_shell (built
#      from the same tree)
#   4. emit ONE compressed, directly-bootable qcow2 + a checksum + import docs
#
# The result boots on DHCP with sshd listening and is driven from a Linux
# builder by cvcpkg.haikuhost. Import with an NVMe disk bus — the one setting
# that is mandatory; the NIC needs no special model. See README-import.md and
# the recipe notes for why.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
RECIPE_DIR="$(cd "$(dirname "$0")" && pwd)"
JOBS="${CVC_JOBS:-$(nproc 2>/dev/null || echo 4)}"

HAIKU_REF="${HAIKU_REF:-r1beta5}"
BUILDTOOLS_REF="${BUILDTOOLS_REF:-r1beta5}"
HAIKU_REPO="${HAIKU_REPO:-https://github.com/haiku/haiku.git}"
BUILDTOOLS_REPO="${BUILDTOOLS_REPO:-https://github.com/haiku/buildtools.git}"

# BFS size in MiB. Defaults to UserBuildConfig's own value when unset; see the
# HAIKU_IMAGE_SIZE_MB comment in recipe.yaml for the 10 GiB budget.
HAIKU_IMAGE_SIZE_MB="${HAIKU_IMAGE_SIZE_MB:-}"
HAIKU_EXTRA_PACKAGES="${HAIKU_EXTRA_PACKAGES:-}"

# What the server will accept on publish. Mirrors
# cvcpkg.server.limits.DEFAULT_MAX_UPLOAD_BYTES (4 GiB), which is now a
# configurable setting (CVCPKG_MAX_UPLOAD_BYTES / `cvcpkg server run
# --max-upload-bytes`) rather than a constant — so this is a WARNING, not a
# failure: an operator who raised the cap should not have the build fail.
HAIKU_PUBLISH_CAP_BYTES="${HAIKU_PUBLISH_CAP_BYTES:-4294967296}"

# ── 0. Disk preflight ───────────────────────────────────────────────────
# The Haiku tree (full history, ~2.5 GiB), buildtools, the generated cross
# GCC/binutils and the object tree together dwarf the 10 GiB image. Dying of
# ENOSPC at 90% of a 90-minute build wastes the whole toolchain step and shows
# up in the log as an unrelated compiler error, so check first and say the
# numbers out loud. HAIKU_SKIP_SPACE_CHECK=1 for a builder with an odd layout.
HAIKU_MIN_FREE_GIB="${HAIKU_MIN_FREE_GIB:-35}"
_free_gib() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {print int($4/1048576)}'; }
if [[ "${HAIKU_SKIP_SPACE_CHECK:-0}" != "1" ]]; then
    mkdir -p "${CVC_BUILD_DIR}" "${CVC_INSTALL_DIR}"
    for _dir in "${CVC_BUILD_DIR}" "${CVC_INSTALL_DIR}"; do
        _free="$(_free_gib "${_dir}")"
        [[ -n "${_free}" ]] || continue   # df failed; don't block on a probe
        # The install dir only ever holds the compressed qcow2 and the docs,
        # so it needs a fraction of the build dir's budget.
        _need="${HAIKU_MIN_FREE_GIB}"
        [[ "${_dir}" == "${CVC_INSTALL_DIR}" ]] && _need=6
        if (( _free < _need )); then
            echo "ERROR: ${_dir} has ${_free} GiB free, need ~${_need} GiB." >&2
            echo "       Point CVC_BUILD_DIR at a larger volume, or set" >&2
            echo "       HAIKU_SKIP_SPACE_CHECK=1 if df is lying about this mount." >&2
            exit 1
        fi
        echo "preflight: ${_dir} has ${_free} GiB free (need ~${_need} GiB)"
    done
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
        mtools qemu-utils zstd python3 2>/dev/null || true
fi

# ── 2. Clone sources ────────────────────────────────────────────────────
cd "${CVC_BUILD_DIR}"
echo "Haiku ref: ${HAIKU_REF}   buildtools ref: ${BUILDTOOLS_REF}"
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
#
# NOTE: this fallback is r1beta5-specific. Moving HAIKU_REF to a post-beta5
# commit (see recipe.yaml) means the tag fetch has to work, or this constant
# has to move with it.
if [[ -z "${DESC}" ]]; then
    export HAIKU_REVISION="${HAIKU_REVISION:-hrev57937_5}"
    echo "No hrev tags reachable — pinning HAIKU_REVISION=${HAIKU_REVISION}"
fi

# Drop in the builder-anyboot profile.
cp "${RECIPE_DIR}/UserBuildConfig" haiku/build/jam/UserBuildConfig

# Validate the env overrides BEFORE any of them is written. What follows is
# generated text that jam then executes, which is the one place a stray
# character in an env var would become arbitrary build-time code — and
# validating up front also means a bad value cannot leave a half-written,
# syntactically broken UserBuildConfig behind.
if [[ -n "${HAIKU_IMAGE_SIZE_MB}" ]]; then
    [[ "${HAIKU_IMAGE_SIZE_MB}" =~ ^[1-9][0-9]*$ ]] || {
        echo "ERROR: HAIKU_IMAGE_SIZE_MB must be a positive integer (MiB), got '${HAIKU_IMAGE_SIZE_MB}'" >&2
        exit 1
    }
fi
for _pkg in ${HAIKU_EXTRA_PACKAGES}; do
    [[ "${_pkg}" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]] || {
        echo "ERROR: bad package name in HAIKU_EXTRA_PACKAGES: '${_pkg}'" >&2
        exit 1
    }
done

# Apply the overrides by APPENDING a second profile block rather than editing
# the first: jam takes the last assignment, so the checked-in UserBuildConfig
# stays a valid standalone file that builds with its own defaults.
if [[ -n "${HAIKU_IMAGE_SIZE_MB}" || -n "${HAIKU_EXTRA_PACKAGES}" ]]; then
    {
        echo ""
        echo "# ── Appended by build.sh from the recipe's env overrides ──"
        echo "switch \$(HAIKU_BUILD_PROFILE) {"
        echo "    case \"builder-anyboot\" : {"
        if [[ -n "${HAIKU_IMAGE_SIZE_MB}" ]]; then
            echo "        HAIKU_IMAGE_SIZE = ${HAIKU_IMAGE_SIZE_MB} ;"
        fi
        if [[ -n "${HAIKU_EXTRA_PACKAGES}" ]]; then
            echo "        AddHaikuImageSystemPackages ${HAIKU_EXTRA_PACKAGES} ;"
        fi
        echo "    }"
        echo "}"
    } >> haiku/build/jam/UserBuildConfig
fi
echo "── effective UserBuildConfig overrides ──"
echo "  HAIKU_IMAGE_SIZE_MB=${HAIKU_IMAGE_SIZE_MB:-(UserBuildConfig default)}"
echo "  HAIKU_EXTRA_PACKAGES=${HAIKU_EXTRA_PACKAGES:-(none)}"

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
echo "Built anyboot: ${ANYBOOT} ($(du -h "${ANYBOOT}" | cut -f1) on disk)"

# ── 5. Inject SSH key, sshd_config, work dir + UserBootscript ───────────
# bfs_shell is a host tool that reads/writes BFS volumes directly. Build it
# from the same tree, then edit the image's BFS partition. This is what makes
# the image PRE-configured rather than merely pre-installed — with no console
# and no cloud-init on Haiku, anything not written here can never be written.
jam -q -j"${JOBS}" "<build>bfs_shell" 2>/dev/null || jam -q -j"${JOBS}" bfs_shell 2>/dev/null || true
BFS_SHELL="$(find generated* -type f -name bfs_shell -perm -u+x 2>/dev/null | head -1)"

INJ="${CVC_BUILD_DIR}/inject"
mkdir -p "${INJ}"
printf '%s\n' "${HAIKU_BUILDER_SSH_PUBKEY:-}" > "${INJ}/authorized_keys"
if [[ -z "${HAIKU_BUILDER_SSH_PUBKEY:-}" ]]; then
    echo "WARN: HAIKU_BUILDER_SSH_PUBKEY is empty — the image will trust no key." >&2
    echo "      Inject one before first boot (see README-import.md)." >&2
fi
cp "${RECIPE_DIR}/UserBootscript" "${INJ}/UserBootscript"

# The builder account IS Haiku's stock `user` (uid 0, home /boot/home). Haiku
# has no useradd and its multi-user support is incomplete — everything runs as
# uid 0 — so creating a second "builder" account would produce an account sshd
# cannot usefully log in as. `user` is the builder user; that is what
# CVCPKG_HAIKU_SSH=user@host means.
cat > "${INJ}/sshd_config" <<'SSHDCONF'
# Generated by recipes/haiku-image/build.sh — cvcpkg haikuhost build target.
#
# StrictModes off because bfs_shell has no chmod: the injected
# /boot/home/.ssh lands with whatever mode the offline copy gives it, and
# sshd's mode check would then refuse the only key the image has — on a box
# with no serial getty and no virtio-console, i.e. unrecoverably. The
# ownership StrictModes protects is meaningless here anyway: Haiku is
# effectively single-user and everything runs as uid 0.
StrictModes no

# Key-only. The stock `user` account has NO password set, and leaving password
# auth enabled on an image that is meant to be cloned is how a build VM
# becomes someone else's build VM.
PubkeyAuthentication yes
PasswordAuthentication no
PermitEmptyPasswords no
AuthorizedKeysFile .ssh/authorized_keys

# haikuhost drives this box entirely over exec channels (no pty, no
# forwarding); keeping the surface that small costs nothing.
X11Forwarding no
AllowTcpForwarding no

# haikuhost jobs can compile for a long time between bytes on the control
# connection. Do not let a quiet link be mistaken for a dead one.
ClientAliveInterval 60
ClientAliveCountMax 30
SSHDCONF

if [[ -n "${BFS_SHELL}" ]]; then
    # The anyboot has an MBR; bfs_shell needs the BFS partition. Loop-mount
    # with -P to expose it (needs privilege — the star Linux builders have
    # passwordless sudo, same as the BSD provisioners).
    LOOP="$(sudo losetup -f -P --show "${ANYBOOT}" 2>/dev/null || true)"
    BFSPART=""
    [[ -n "${LOOP}" && -e "${LOOP}p1" ]] && BFSPART="${LOOP}p1"
    if [[ -n "${BFSPART}" ]]; then
        # Two bfs_shell sessions, deliberately. Its mkdir is not recursive and
        # some of these parents already exist in a stock image (home/config/…)
        # while others do not (home/.ssh), so the mkdirs WILL report errors —
        # and if a session aborts on the first error, a "directory exists"
        # would silently swallow every copy after it, shipping an image that
        # looks built and trusts no key. Doing the mkdirs in a throwaway
        # session first means only the copies decide success.
        #
        # Scripted-session syntax: host paths are prefixed with ':'. The volume
        # root IS /boot, so `home/...` is /boot/home/... .
        sudo "${BFS_SHELL}" "${BFSPART}" <<'BFSMKDIR' >/dev/null 2>&1 || true
mkdir home/.ssh
mkdir home/config
mkdir home/config/settings
mkdir home/config/settings/boot
mkdir home/cvcpkg-build
mkdir home/cvcpkg-build/jobs
mkdir system
mkdir system/settings
mkdir system/settings/ssh
sync
quit
BFSMKDIR
        sudo "${BFS_SHELL}" "${BFSPART}" <<BFSCMDS || echo "WARN: bfs_shell injection failed" >&2
cp :${INJ}/authorized_keys home/.ssh/authorized_keys
cp :${INJ}/UserBootscript home/config/settings/boot/UserBootscript
cp :${INJ}/sshd_config system/settings/ssh/sshd_config
sync
quit
BFSCMDS
        # Read the three files back. This recipe has never been published, so
        # do not take "the command exited 0" for "the image is configured" —
        # a mis-injected image boots fine and is simply unreachable forever.
        echo "── injected files as seen from inside the BFS ──"
        sudo "${BFS_SHELL}" "${BFSPART}" <<'BFSVERIFY' || true
ls home/.ssh
ls home/config/settings/boot
ls system/settings/ssh
ls home/cvcpkg-build
quit
BFSVERIFY
        sudo losetup -d "${LOOP}" 2>/dev/null || true
    else
        echo "WARN: could not loop-mount the anyboot BFS partition; image NOT configured." >&2
        echo "      Inject it post-import instead (see README-import.md)." >&2
    fi
else
    echo "WARN: bfs_shell not built; image NOT configured (inject post-import)." >&2
fi

# ── 6. Stage the output: one compressed, bootable qcow2 + checksum + docs ─
# Only ONE disk artifact ships. The raw anyboot is byte-for-byte the same
# installation, and staging both would double a multi-gigabyte download for a
# file nobody needs once the image is pre-installed.
#
# Two compressions stack here and both matter:
#   * qemu-img convert skips the unallocated (zero) blocks of a freshly
#     created BFS, so a 10 GiB volume with ~3 GiB used converts to ~3 GiB.
#   * `-c` compresses the qcow2's clusters IN THE FILE, so the result is
#     still directly bootable and importable — no decompression step for the
#     consumer — and the bundle's own tar.gz has nothing left to squeeze.
#     zstd clusters where qemu-img supports them (qemu >= 5.1), zlib where it
#     does not; a plain sparse convert is the last resort.
mkdir -p "${CVC_INSTALL_DIR}"
QCOW="${CVC_INSTALL_DIR}/haiku-builder.qcow2"
if command -v qemu-img >/dev/null 2>&1; then
    rm -f "${QCOW}"
    qemu-img convert -f raw -O qcow2 -c -o compression_type=zstd "${ANYBOOT}" "${QCOW}" 2>/dev/null \
        || { rm -f "${QCOW}"; qemu-img convert -f raw -O qcow2 -c "${ANYBOOT}" "${QCOW}"; } \
        || { rm -f "${QCOW}"; qemu-img convert -f raw -O qcow2 "${ANYBOOT}" "${QCOW}"; }
    qemu-img info "${QCOW}" || true
else
    # Raw fallback: still qemu-bootable, just big. `cp --sparse=always` keeps
    # the holes so the builder's own disk survives it.
    echo "WARN: qemu-img not found — staging an uncompressed raw image." >&2
    cp --sparse=always "${ANYBOOT}" "${QCOW}"
fi

( cd "${CVC_INSTALL_DIR}" && sha256sum haiku-builder.qcow2 > haiku-builder.qcow2.sha256 )
cp "${RECIPE_DIR}/metadata.yaml"     "${CVC_INSTALL_DIR}/metadata.yaml"
cp "${RECIPE_DIR}/README-import.md"  "${CVC_INSTALL_DIR}/README-import.md"

# Free the ~10 GiB raw anyboot now that it has been converted. The generated
# tree is the builder's, not ours, but this one file is big enough to matter
# to whatever runs next on the same volume.
rm -f "${ANYBOOT}"

echo "Haiku builder image staged to ${CVC_INSTALL_DIR}:"
ls -lh "${CVC_INSTALL_DIR}"

# Publish-cap check. A VM disk is orders of magnitude larger than anything
# else in the catalog, so say plainly whether this bundle can be published
# before an operator discovers it from a 413 an hour later. A warning, not a
# failure: the cap is a server setting now, not a constant.
STAGED_BYTES="$(du -sb "${CVC_INSTALL_DIR}" | cut -f1)"
echo "staged size: $((STAGED_BYTES / 1048576)) MiB (server cap: $((HAIKU_PUBLISH_CAP_BYTES / 1048576)) MiB)"
if (( STAGED_BYTES > HAIKU_PUBLISH_CAP_BYTES )); then
    echo "WARN: this bundle exceeds the server's default upload cap." >&2
    echo "      Raise it (CVCPKG_MAX_UPLOAD_BYTES=8GB, or 'cvcpkg server run" >&2
    echo "      --max-upload-bytes 8GB' — see cvcpkg.server.limits), or rebuild" >&2
    echo "      with a smaller HAIKU_IMAGE_SIZE_MB / fewer HAIKU_EXTRA_PACKAGES" >&2
    echo "      and let UserBootscript's first-boot pkgman top-up fill the gap." >&2
fi
