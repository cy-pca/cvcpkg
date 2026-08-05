#!/usr/bin/env bash
# recipes/haiku-image/build.sh — build a headless HaikuOS builder image.
#
# Haiku's Installer is graphical-only, so instead of installing interactively
# we build a fully pre-configured anyboot image from source:
#   1. clone haiku + buildtools (pinned)
#   2. patch the tree, then build the cross-toolchain + a custom
#      "builder-anyboot" jam profile (UserBuildConfig: a 10 GiB BFS, an
#      ACTIVATED toolchain, OpenSSH, Haiku's TLS, hostname, `user` account)
#   3. inject an SSH authorized_keys + system launch jobs into the image's BFS
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

# ── 2b. Patch the haiku source tree ─────────────────────────────────────
# NOTE: these are deliberately NOT in recipe.yaml's `patches:` list. That
# list is applied by cvcpkg to the directory fetch_source() returns, and for
# `source.type: prebuilt` (which this recipe is, because it clones its own
# sources above) fetch_source() returns an EMPTY dummy dir — so cvcpkg would
# try to patch nothing, fail, and abort the build. Verified: both
# `git apply -p1` and `patch -p1` exit 1 with "No such file or directory" in
# that dummy dir. The clone above is the only tree these can apply to, so
# build.sh applies them here. Keep them idempotent: the build dir is reused
# across incremental re-runs, so a second run must not fail on an
# already-patched tree.
for p in "${RECIPE_DIR}"/*.patch; do
    [[ -e "$p" ]] || continue
    if patch -p1 -d haiku --dry-run --silent -R -i "$p" >/dev/null 2>&1; then
        echo "patch already applied, skipping: $(basename "$p")"
        continue
    fi
    echo "Applying patch: $(basename "$p")"
    patch -p1 -d haiku -i "$p"
done

# Drop in the builder-anyboot profile.
cp "${RECIPE_DIR}/UserBuildConfig" haiku/build/jam/UserBuildConfig

# ── 2c. Package-name preflight ──────────────────────────────────────────
# jam resolves image package names ONLY against the literal <name>-<version>
# lines in haiku/build/jam/repositories/* (rule AddRepositoryPackage). It does
# not query HaikuPorts over the network and does not honour `provides:`. An
# unknown name is SILENTLY SKIPPED — build/jam/ImageRules:1130 is an Echo and
# a `continue`, never an Exit — and `jam -q` does not suppress Echo but a few
# such lines are invisible in a multi-thousand-line log.
#
# That is exactly how a "builder" image shipped with no python3 and no
# compiler: `python3` (not a Haiku package — they are python3.10 ... 3.14) plus
# cmake/ninja/patchelf/rsync were all dropped on the floor. The failure only
# became visible after the ~90-minute cross-toolchain build, at boot.
#
# So validate here, BEFORE ./configure, where a typo costs seconds.
HAIKU_PKG_ARCH="${HAIKU_PKG_ARCH:-x86_64}"
_avail="$(sed -nE 's/^[[:space:]]+([A-Za-z0-9._+]+)-[0-9~].*$/\1/p' \
    "haiku/build/jam/repositories/HaikuPorts/${HAIKU_PKG_ARCH}" \
    "haiku/build/jam/repositories/Haiku" 2>/dev/null | LC_ALL=C sort -u)"
[[ -n "${_avail}" ]] || {
    echo "ERROR: no build-time package manifests under haiku/build/jam/repositories" >&2
    exit 1
}
# Every AddHaikuImageSystemPackages argument in the profile. Comments are
# stripped first (the list is commented inline); keep the list plain — this
# does not understand FFilterByBuildFeatures syntax (`gmp@!gcc2`, `@{ }@`).
_want="$(awk '
    { sub(/#.*/, "") }
    /AddHaikuImageSystemPackages/ { collecting = 1; sub(/.*AddHaikuImageSystemPackages/, "") }
    collecting {
        line = $0
        if (index(line, ";") > 0) { sub(/;.*/, "", line); collecting = 0 }
        print line
    }
' haiku/build/jam/UserBuildConfig | tr -s ' \t' '\n' \
    | grep -E '^[A-Za-z0-9._+]+$' | LC_ALL=C sort -u)"
_missing=""
for _p in ${_want}; do
    printf '%s\n' "${_avail}" | grep -qxF "${_p}" || _missing="${_missing} ${_p}"
done
if [[ -n "${_missing}" ]]; then
    echo "ERROR: these image packages are in no build-time package repository:${_missing}" >&2
    for _p in ${_missing}; do
        _near="$(printf '%s\n' "${_avail}" | grep -iE "^${_p%%[0-9]*}" | tr '\n' ' ' || true)"
        [[ -n "${_near}" ]] && echo "       ${_p} -> did you mean: ${_near}" >&2
    done
    echo "       jam would SKIP these silently (build/jam/ImageRules:1130) and" >&2
    echo "       ship a toolchain-less image after the cross-toolchain build." >&2
    exit 1
fi
echo "package preflight ok: $(printf '%s\n' "${_want}" | grep -c .) names resolve"

# Build Haiku's Jam and put it on PATH — configure builds the cross-tools
# with make, but the image build (@builder-anyboot) is driven by jam, which
# is NOT installed system-wide.
( cd buildtools/jam && make )
JAM_BIN="$(find "${CVC_BUILD_DIR}/buildtools/jam" -maxdepth 2 -type f -name jam -perm -u+x 2>/dev/null | head -1)"
[[ -n "${JAM_BIN}" ]] || { echo "jam did not build in buildtools/jam" >&2; exit 1; }
JAM_DIR="$(dirname "${JAM_BIN}")"
export PATH="${JAM_DIR}:${PATH}"
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

# ── 5. Inject SSH key + boot jobs via bfs_shell ─────────────────────────
# bfs_shell is a host tool that reads/writes BFS volumes directly. Build it
# from the same tree, then edit the image's BFS partition.
jam -q -j"${JOBS}" "<build>bfs_shell" 2>/dev/null || jam -q -j"${JOBS}" bfs_shell 2>/dev/null || true
BFS_SHELL="$(find generated* -type f -name bfs_shell -perm -u+x 2>/dev/null | head -1)"

INJ="${CVC_BUILD_DIR}/inject"
mkdir -p "${INJ}"
printf '%s\n' "${HAIKU_BUILDER_SSH_PUBKEY:-}" > "${INJ}/authorized_keys"
cp "${RECIPE_DIR}/launch-sshd"          "${INJ}/launch-sshd"
cp "${RECIPE_DIR}/cvcpkg-start-sshd.sh" "${INJ}/cvcpkg-start-sshd.sh"

if [[ -n "${BFS_SHELL}" ]]; then
    # The anyboot has an MBR; bfs_shell needs the BFS partition. Loop-mount
    # with -P to expose it (needs privilege — the star Linux builders have
    # passwordless sudo, same as the BSD provisioners).
    LOOP="$(sudo losetup -f -P --show "${ANYBOOT}" 2>/dev/null || true)"
    BFSPART=""
    [[ -n "${LOOP}" && -e "${LOOP}p1" ]] && BFSPART="${LOOP}p1"
    if [[ -n "${BFSPART}" ]]; then
        # bfs_shell scripted session. Two path conventions apply:
        #
        #  * Host paths are prefixed with ':'  (command_cp.cpp:880).
        #  * GUEST paths are NOT relative to the BFS root. fssh mounts the
        #    volume at the FIXED path /myfs and sets cwd to "/"
        #    (src/tools/fs_shell/fssh.cpp:48,104,1605), so a bare `home/.ssh`
        #    resolves to /home/.ssh — which does not exist. Every guest path
        #    below is therefore absolute under /myfs. Getting this wrong is
        #    invisible: fssh prints "Failed to stat()" and still exits 0, so
        #    the `|| echo WARN` below never fires and the image ships with no
        #    authorized_keys at all.
        #
        # mkdir is not recursive, so create each target dir explicitly (their
        # parents all exist in a stock image). /myfs/system/settings/ssh
        # normally exists too — it is where the openssh package's sshd_config
        # lands — but it is created anyway so a missing one cannot turn the
        # start-sshd copy below into a silent no-op. Run in a throwaway session
        # so "already exists" noise cannot be mistaken for a copy failure.
        sudo "${BFS_SHELL}" "${BFSPART}" >/dev/null 2>&1 <<'BFSMKDIR' || true
mkdir /myfs/home/config/settings/ssh
mkdir /myfs/system/settings/launch
mkdir /myfs/system/settings/ssh
sync
quit
BFSMKDIR
        # OpenSSH refuses a group/world-writable ~/.ssh or authorized_keys
        # under StrictModes, and fssh's cp does not carry the host mode over,
        # so set the modes explicitly (fssh does have chmod — `help` lists it).
        #
        # `|| true`: fssh routinely ends a WRITE session with
        # "Unmounting FS failed: Device or resource busy" and exit 1 even
        # though the explicit `sync` above already flushed everything
        # (verified: a fresh mount reads the file back). Under `set -o
        # pipefail` that would abort a perfectly good build, so the exit
        # status is discarded here and correctness is established by the
        # read-back below instead.
        #
        # THE authorized_keys PATH IS NOT ~/.ssh. Haiku's openssh package
        # ships an sshd_config whose only non-default directive is
        #
        #     AuthorizedKeysFile	config/settings/ssh/authorized_keys
        #
        # (it follows Haiku's ~/config/settings convention). A key written to
        # /boot/home/.ssh/authorized_keys is never read: sshd runs, offers
        # publickey, and answers every attempt with "Permission denied
        # (publickey,...)". Verified both ways on a booted VM.
        INJECT_LOG="${CVC_BUILD_DIR}/bfs-inject.log"
        sudo "${BFS_SHELL}" "${BFSPART}" <<BFSCMDS 2>&1 | tee "${INJECT_LOG}" || true
cp :${INJ}/authorized_keys /myfs/home/config/settings/ssh/authorized_keys
cp :${INJ}/launch-sshd /myfs/system/settings/launch/sshd
cp :${INJ}/cvcpkg-start-sshd.sh /myfs/system/settings/ssh/cvcpkg-start-sshd.sh
chmod 700 /myfs/home/config/settings/ssh
chmod 600 /myfs/home/config/settings/ssh/authorized_keys
chmod 644 /myfs/system/settings/launch/sshd
chmod 755 /myfs/system/settings/ssh/cvcpkg-start-sshd.sh
ls /myfs/home/config/settings/ssh
ls /myfs/system/settings/launch
sync
quit
BFSCMDS
        # fssh exits 0 even when every command failed, so verify by reading
        # the file back instead of trusting the exit status.
        if sudo "${BFS_SHELL}" "${BFSPART}" 2>/dev/null <<'BFSVERIFY' | grep -q 'ssh-'
cat /myfs/home/config/settings/ssh/authorized_keys
quit
BFSVERIFY
        then
            echo "bfs_shell: authorized_keys injected into /boot/home/config/settings/ssh"
        elif [[ -z "${HAIKU_BUILDER_SSH_PUBKEY:-}" ]]; then
            echo "bfs_shell: no HAIKU_BUILDER_SSH_PUBKEY set — image ships with no key." >&2
        else
            echo "ERROR: bfs_shell injection failed; see ${INJECT_LOG}" >&2
            sudo losetup -d "${LOOP}" 2>/dev/null || true
            exit 1
        fi
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
