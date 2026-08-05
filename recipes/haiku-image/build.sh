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

# ── Guest axes: SINGLE SOURCES, not descriptor literals ─────────────────
# Everything below drives BOTH a real build command and the generated
# descriptor, so image.yaml cannot describe a guest this script did not build.
# The rule for this file: a value that can be read off the artifact or off the
# build inputs is DERIVED; a value that cannot is written in the POLICY block
# in step 6 with its units and its evidence, and nowhere else.
HAIKU_ARCH="${HAIKU_ARCH:-x86_64}"   # -> ./configure --build-cross-tools AND image.guest_arch

# The jam profile, its output filename and the image's baked BFS size are read
# out of UserBuildConfig — the same file that is copied into the Haiku tree and
# that actually decides them. When the other worktree changes HAIKU_IMAGE_SIZE
# (51200 -> 10240 MiB) or renames the profile, this follows without an edit here.
UBC="${RECIPE_DIR}/UserBuildConfig"
[[ -r "${UBC}" ]] || { echo "missing ${UBC}" >&2; exit 1; }
HAIKU_PROFILE="$(sed -n 's/^[[:space:]]*DefineBuildProfile[[:space:]]\{1,\}\([A-Za-z0-9._-]\{1,\}\)[[:space:]]*:.*/\1/p' "${UBC}" | head -1)"
HAIKU_IMAGE_FILE="$(sed -n 's/^[[:space:]]*DefineBuildProfile[^"]*"\([^"]\{1,\}\)".*/\1/p' "${UBC}" | head -1)"
HAIKU_IMAGE_SIZE_MIB="$(sed -n 's/^[[:space:]]*HAIKU_IMAGE_SIZE[[:space:]]*=[[:space:]]*\([0-9]\{1,\}\).*/\1/p' "${UBC}" | head -1)"
[[ -n "${HAIKU_PROFILE}" && -n "${HAIKU_IMAGE_FILE}" ]] || {
    echo "could not read DefineBuildProfile out of ${UBC}" >&2; exit 1; }
# image.variant: the profile name minus its image-format suffix
# (builder-anyboot -> builder). Must match image-schema.yaml's ^[a-z][a-z0-9-]*$.
HAIKU_VARIANT="${HAIKU_PROFILE%%-*}"
echo "profile=${HAIKU_PROFILE} image=${HAIKU_IMAGE_FILE} arch=${HAIKU_ARCH} \
variant=${HAIKU_VARIANT} HAIKU_IMAGE_SIZE=${HAIKU_IMAGE_SIZE_MIB:-<unset>} MiB"

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
        --build-cross-tools "${HAIKU_ARCH}" \
        --cross-tools-source ../buildtools
fi

# ── 4. Build the custom anyboot image ───────────────────────────────────
# The revision is resolved in step 2 above: either from real hrev* tags
# (fetched from the official repo → describe computes it exactly) or, if those
# aren't reachable, from the HAIKU_REVISION fallback exported there. Haiku's
# build imports HAIKU_REVISION from the environment, so nothing extra is
# needed here — a correctly-set value flows straight into the package version.
jam -q -j"${JOBS}" "@${HAIKU_PROFILE}"

ANYBOOT="$(ls -1 generated*/"${HAIKU_IMAGE_FILE}" 2>/dev/null | head -1)"
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

# Whether the image really trusts a key. This becomes access.ssh_pubkey_baked
# in image.yaml, which a fleet operator reads to decide whether the VM is
# reachable — so it is set from a READ-BACK of the injected file, never from
# "the command exited 0" (bfs_shell's scripted session can exit 0 with a failed
# cp inside it).
SSH_PUBKEY_BAKED=false

# access.ssh_user — the account a fleet operator will actually type. It used to
# be the literal `user`, which is a CLAIM ABOUT THE IMAGE that this script never
# checked. Haiku's build names its single interactive account from
# HAIKU_ROOT_USER_NAME, whose upstream default is `baron`; an image built
# without that pinned has no account called `user` at all, and the literal was
# simply false. It is now derived, best evidence first:
#   1. the artifact's own passwd file, read back out of the BFS with bfs_shell
#      (evidence — this is what the image HAS);
#   2. HAIKU_ROOT_USER_NAME as pinned in the UserBuildConfig this script copies
#      into the tree (intent — this is what the build ASKED for);
#   3. the fallback Haiku's own jam rules use, read out of the cloned tree as
#      `$(HAIKU_ROOT_USER_NAME:E=<name>)` rather than retyped here.
# If none of them yields a name, access.ssh_user is OMITTED and the build says
# so loudly. An absent key is a question the consumer is forced to answer
# (cvcpkg's provisioner refuses to run without --ssh-user); a wrong literal is a
# silent login failure on a guest with no out-of-band console.
SSH_USER=""
SSH_USER_SRC=""
SSH_USER_CFG="$(sed -n 's/^[[:space:]]*HAIKU_ROOT_USER_NAME[[:space:]]*=[[:space:]]*\([A-Za-z0-9._-]\{1,\}\).*/\1/p' "${UBC}" | head -1)"
# `|| true` is load-bearing: this file runs under `set -o pipefail`, and grep
# exits 1 on no-match / 2 on a missing directory, either of which would abort
# the whole image build over a source that is allowed to be absent.
SSH_USER_UPSTREAM="$( { grep -rho 'HAIKU_ROOT_USER_NAME:E=[A-Za-z0-9._-]\{1,\}' build/jam 2>/dev/null || true; } | sed 's/.*=//' | head -1)"

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
        # NOTE: the read-back path is Haiku's config/settings/ssh, NOT
        # ~/.ssh.  The openssh package ships an sshd_config whose
        # AuthorizedKeysFile names the former, so a key under ~/.ssh is
        # never read and every login fails with publickey denied.  An
        # earlier revision of this script read ~/.ssh back and would have
        # reported a confirmed key that sshd ignores.
        # fssh exits 0 even when every command failed, so verify by reading
        # the file back instead of trusting the exit status.
        if sudo "${BFS_SHELL}" "${BFSPART}" 2>/dev/null <<'BFSVERIFY' | grep -q 'ssh-'
cat /myfs/home/config/settings/ssh/authorized_keys
quit
BFSVERIFY
        then
            SSH_PUBKEY_BAKED=true
            echo "bfs_shell: authorized_keys injected into /boot/home/config/settings/ssh"
        elif [[ -z "${HAIKU_BUILDER_SSH_PUBKEY:-}" ]]; then
            echo "bfs_shell: no HAIKU_BUILDER_SSH_PUBKEY set — image ships with no key." >&2
        else
            echo "ERROR: bfs_shell injection failed; see ${INJECT_LOG}" >&2
            sudo losetup -d "${LOOP}" 2>/dev/null || true
            exit 1
        fi

        # (1) Read the account list out of the artifact. Haiku's passwd has
        # moved between releases and the anyboot's BFS root is /boot, so probe
        # the known locations instead of asserting one. The interactive account
        # is the entry whose home is under /boot/home — Haiku's default user is
        # uid 0, so "uid >= 1000" does NOT identify it (that check is only a
        # last resort for a differently-configured image).
        for pwcand in system/settings/etc/passwd boot/system/settings/etc/passwd \
                      system/data/etc/passwd etc/passwd; do
            rm -f "${INJ}/passwd.readback"
            sudo "${BFS_SHELL}" "${BFSPART}" <<BFSPW >/dev/null 2>&1 || true
cp ${pwcand} :${INJ}/passwd.readback
quit
BFSPW
            [[ -s "${INJ}/passwd.readback" ]] || continue
            SSH_USER="$(awk -F: '$6 ~ /^\/boot\/home/ { print $1; exit }' "${INJ}/passwd.readback")"
            [[ -n "${SSH_USER}" ]] || \
                SSH_USER="$(awk -F: '$3 + 0 >= 1000 { print $1; exit }' "${INJ}/passwd.readback")"
            if [[ -n "${SSH_USER}" ]]; then
                SSH_USER_SRC="image:${pwcand}"
                break
            fi
        done
        sudo losetup -d "${LOOP}" 2>/dev/null || true
    else
        echo "WARN: could not loop-mount the anyboot BFS partition; SSH key not injected." >&2
        echo "      Inject it post-import instead (see README-import.md)." >&2
    fi
else
    echo "WARN: bfs_shell not built; SSH key not injected (inject post-import)." >&2
fi

# Resolve access.ssh_user from whatever evidence we got, loudest disagreement
# first. The artifact always wins over the build config: if the pin did not
# land in the image, the image is what an operator will have to log into.
if [[ -n "${SSH_USER}" && -n "${SSH_USER_CFG}" && "${SSH_USER}" != "${SSH_USER_CFG}" ]]; then
    echo "WARN: UserBuildConfig pins HAIKU_ROOT_USER_NAME=${SSH_USER_CFG} but the" >&2
    echo "      built image's passwd has '${SSH_USER}'. The pin did NOT land." >&2
    echo "      image.yaml will advertise the account the image really has." >&2
fi
if [[ -z "${SSH_USER}" && -n "${SSH_USER_CFG}" ]]; then
    SSH_USER="${SSH_USER_CFG}"
    SSH_USER_SRC="UserBuildConfig:HAIKU_ROOT_USER_NAME"
    echo "NOTE: could not read the image's passwd; taking ssh_user from the" >&2
    echo "      UserBuildConfig pin (${SSH_USER}) — intent, not verified." >&2
fi
if [[ -z "${SSH_USER}" && -n "${SSH_USER_UPSTREAM}" ]]; then
    SSH_USER="${SSH_USER_UPSTREAM}"
    SSH_USER_SRC="haiku-tree-default:HAIKU_ROOT_USER_NAME:E="
    echo "NOTE: UserBuildConfig pins no HAIKU_ROOT_USER_NAME; using Haiku's own" >&2
    echo "      default account name from the source tree (${SSH_USER})." >&2
fi
if [[ -z "${SSH_USER}" ]]; then
    echo "WARN: could not determine the image's login account from the artifact," >&2
    echo "      from UserBuildConfig, or from the Haiku tree. image.yaml will" >&2
    echo "      OMIT access.ssh_user rather than guess; consumers must pass one" >&2
    echo "      explicitly (cvcpkg's provisioner refuses to run without it)." >&2
else
    echo "access.ssh_user=${SSH_USER} (source: ${SSH_USER_SRC})"
fi

# ── 6. Stage outputs into share/<package-name>/ ─────────────────────────
# cvcpkg merges a bundle's staged tree into the prefix preserving relative
# paths, so anything staged at the ROOT of CVC_INSTALL_DIR lands at the ROOT of
# a SHARED prefix. This recipe used to stage metadata.yaml and README-import.md
# there — names that describe no particular guest — so a second image package
# would have collided with it on both. Everything now lives in one directory
# named after the package (unique in the catalog keyspace), with ROLE-based
# filenames so a consumer derives the path from the package name alone:
#
#   $PREFIX/share/haiku-image/disk.qcow2
#   $PREFIX/share/haiku-image/image.yaml   (canonical descriptor)
#   $PREFIX/share/haiku-image/image.env    (same facts, `. `-sourceable)
#
# Only ONE payload format is shipped. The anyboot .iso used to be staged
# alongside the qcow2; it is the same bits, tar.gz does no cross-file dedup, and
# it doubled the bundle against the server's upload cap. Recover it with:
#   qemu-img convert -f qcow2 -O raw disk.qcow2 haiku-anyboot.iso
# The package name IS the directory name — that is the addressing key that
# makes $PREFIX/share/<pkg>/disk.qcow2 derivable from the package name alone
# (image-schema.yaml requires image.package to equal it). Take it from the
# recipe directory rather than retyping it, and fail on a mismatch with
# recipe.yaml so renaming one without the other stops here instead of at a
# consumer resolving a path that does not exist.
PKG_NAME="$(basename "${RECIPE_DIR}")"
RECIPE_NAME="$(sed -n 's/^[[:space:]]*name:[[:space:]]*\([A-Za-z0-9._-]\{1,\}\).*/\1/p' \
                   "${RECIPE_DIR}/recipe.yaml" | head -1)"
[[ -z "${RECIPE_NAME}" || "${RECIPE_NAME}" == "${PKG_NAME}" ]] || {
    echo "recipe.yaml name '${RECIPE_NAME}' != recipe dir '${PKG_NAME}'" >&2; exit 1; }
IMGDIR="${CVC_INSTALL_DIR}/share/${PKG_NAME}"
mkdir -p "${IMGDIR}/incus"

# Role-based staged filenames. Declared once and used for the copy, the
# descriptor, image.env and SHA256SUMS, so those four cannot disagree.
DISK_FILE=disk.qcow2
DOCS_FILE=README.md
INCUS_META=incus/metadata.tar.xz
CHECKSUMS_FILE=SHA256SUMS

# POLICY CONSTANTS — the values that genuinely CANNOT be read off the artifact.
# Each one is a decision or a measured guest behaviour, with its units stated.
# The rule this file follows: if a value is not in this block, it is derived;
# if it IS in this block, it needs evidence. They are declared here, ahead of
# every consumer, and interpolated into image.yaml, image.env AND the Incus
# metadata below — those three used to carry independent hand-typed copies.
BOOT_FIRMWARE=uefi         # POLICY: the hypervisor's default UEFI/OVMF, with
                           # security.csm=false — the combination the disk-bus
                           # bisection below was carried out under.
BOOT_DISK_BUS=nvme         # MEASURED, see the note rendered into image.yaml.
BOOT_NET_MODEL=virtio-net  # MEASURED: a stock virtio NIC holds a DHCP lease.
BOOT_CONSOLE=none          # GUEST FACT: Haiku's serial port is kernel-debug
                           # only, so there is no out-of-band admin channel.
BOOT_SECUREBOOT=false      # POLICY: Haiku is not signed for the Microsoft keys.
BOOT_CPU_MIN=4             # POLICY. UNITS: vCPUs.
BOOT_MEMORY_MIN_MIB=4096   # POLICY. UNITS: MiB.
IMG_GUEST_OS=haiku         # IDENTITY: what this recipe exists to build.
IMG_WRITABLE=false         # ADVISORY, see the note in image.yaml.

# qemu-img is required, not optional: without it the fallback wrote a RAW image
# to a path named .qcow2, so image.yaml's declared format would be a lie and
# `incus image import` would take the wrong branch. Step 1 installs qemu-utils.
command -v qemu-img >/dev/null 2>&1 || {
    echo "qemu-img not found — cannot produce a qcow2 (install qemu-utils)" >&2; exit 1; }
qemu-img convert -f raw -O qcow2 "${ANYBOOT}" "${IMGDIR}/${DISK_FILE}"

# Disk facts are READ BACK OFF THE PRODUCED FILE, not asserted. The previous
# version said `format: qcow2` as a literal and computed the virtual size from
# `wc -c` on the anyboot via the standing assumption "the anyboot is raw" — both
# are claims about a file we can simply ask about.
QEMU_IMG_JSON="$(qemu-img info --output=json "${IMGDIR}/${DISK_FILE}")"
_qi() {  # _qi <top-level key> -> value, unquoted
    printf '%s' "${QEMU_IMG_JSON}" | python3 -c \
        'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1" 2>/dev/null \
    || printf '%s' "${QEMU_IMG_JSON}" \
       | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p" | head -1
}
DISK_FORMAT="$(_qi format)"
DISK_VIRTUAL_SIZE="$(_qi virtual-size)"
[[ "${DISK_FORMAT}" == "qcow2" ]] || {
    echo "qemu-img reports format '${DISK_FORMAT}' for ${DISK_FILE}, expected qcow2" >&2; exit 1; }
[[ "${DISK_VIRTUAL_SIZE}" =~ ^[0-9]+$ ]] || {
    echo "could not read virtual-size from qemu-img info" >&2; exit 1; }

# boot.disk_min_gib is DERIVED, and it is a hypervisor requirement, not taste:
# `incus init -d root,size=N` fails if N is smaller than the image's virtual
# size, so the floor is exactly ceil(virtual_size / GiB). It is NOT "how much
# room the builder wants" — the guest's usable space is fixed at build time by
# HAIKU_IMAGE_SIZE because BFS cannot be grown after the fact, so a bigger
# volume buys the guest nothing.
#
# This used to be the literal `50`, derived by hand from HAIKU_IMAGE_SIZE =
# 51200 MiB. The literal does not move when UserBuildConfig does: with
# HAIKU_IMAGE_SIZE = 10240 MiB it tells every operator to allocate 5x the disk,
# and it would have gone on saying 50 forever.
DISK_MIN_GIB=$(( (DISK_VIRTUAL_SIZE + 1073741823) / 1073741824 ))
# Consistency check against UserBuildConfig, NOT a second source of truth. The
# BFS partition lives inside the anyboot, so the baked size can never legally
# exceed the image's virtual size; if it appears to, the artifact and the build
# config are out of sync and the ARTIFACT wins — it is the file the operator
# will actually import. Overriding with the config value here would put the
# stale-constant bug straight back in.
if [[ "${HAIKU_IMAGE_SIZE_MIB}" =~ ^[0-9]+$ ]]; then
    BFS_GIB=$(( (HAIKU_IMAGE_SIZE_MIB + 1023) / 1024 ))
    if (( DISK_MIN_GIB < BFS_GIB )); then
        echo "WARN: UserBuildConfig bakes a ${BFS_GIB} GiB BFS but the produced image's" >&2
        echo "      virtual size is only ${DISK_MIN_GIB} GiB — these cannot both be true." >&2
        echo "      Using the artifact's ${DISK_MIN_GIB} GiB; check that jam rebuilt the image." >&2
    fi
fi
echo "disk_min_gib=${DISK_MIN_GIB} GiB (derived from virtual-size ${DISK_VIRTUAL_SIZE} B;\
 HAIKU_IMAGE_SIZE=${HAIKU_IMAGE_SIZE_MIB:-<unset>} MiB)"

cp "${RECIPE_DIR}/README-import.md" "${IMGDIR}/${DOCS_FILE}"

# The Incus/LXD metadata carries its OWN copy of the guest axes (architecture,
# release, variant) — a third place for them to go stale, after image.yaml and
# image.env. The committed file is a template; the axes are overwritten here
# from the same variables the descriptor uses, and the result is checked, so a
# `release: r1beta5` left behind after HAIKU_REF moved cannot ship.
# creation_date is deliberately NOT touched: it is a fixed placeholder so the
# bundle stays byte-reproducible (set it at import time if you need provenance).
sed -e "s/^architecture:.*/architecture: ${HAIKU_ARCH}/" \
    -e "s/^  release:.*/  release: ${HAIKU_REF}/" \
    -e "s/^  variant:.*/  variant: ${HAIKU_VARIANT}/" \
    -e "s/^  description:.*/  description: ${IMG_GUEST_OS} ${HAIKU_REF} headless ${HAIKU_VARIANT} (${HAIKU_ARCH})/" \
    "${RECIPE_DIR}/metadata.yaml" > "${IMGDIR}/incus/metadata.yaml"
for _k in "architecture: ${HAIKU_ARCH}" "release: ${HAIKU_REF}" "variant: ${HAIKU_VARIANT}"; do
    grep -q "^ *${_k}\$" "${IMGDIR}/incus/metadata.yaml" || {
        echo "incus metadata.yaml did not take '${_k}' — template shape changed?" >&2
        exit 1; }
done

# `incus image import` takes a metadata TARBALL, not a bare metadata.yaml — the
# old README told operators to pass the .yaml, which does not work. Build the
# tarball with metadata.yaml at its root.
tar -C "${IMGDIR}/incus" -cJf "${IMGDIR}/${INCUS_META}" metadata.yaml

# Hash the big file ONCE and reuse the digest for both image.yaml and
# SHA256SUMS (the small files are hashed in the same pass below).
DISK_SHA256="$(cd "${IMGDIR}" && sha256sum "${DISK_FILE}" | cut -d' ' -f1)"

# ── 6a. Descriptor values ───────────────────────────────────────────────
# Everything the descriptor says is resolved into a shell variable HERE, once,
# and both image.yaml and image.env are rendered from these same variables.
# The two files used to carry independent hand-typed copies of every value —
# so `disk_min_gib: 50` and `CVCPKG_IMAGE_DISK_MIN_GIB=50` were two separate
# lies that had to be found and fixed twice.
#
# CVC_FULL_VERSION is the recipe's committed <upstream>+cvc.<rev>. NOTE: `pack
# --bump` / `--cvc-revision` re-stamp the revision AFTER this script has run, so
# packing an image with a bump would leave image.version one revision behind the
# bundle. Pack image recipes at their committed revision. The no-CVC_FULL_VERSION
# fallback is COMPOSED from recipe.yaml rather than being a literal that freezes
# at whatever the version happened to be the day it was typed.
IMG_VERSION="${CVC_FULL_VERSION:-}"
if [[ -z "${IMG_VERSION}" ]]; then
    _uv="$(sed -n 's/^[[:space:]]*upstream_version:[[:space:]]*"\{0,1\}\([0-9][^"#[:space:]]*\).*/\1/p' \
                "${RECIPE_DIR}/recipe.yaml" | head -1)"
    _rv="$(sed -n 's/^[[:space:]]*cvc_revision:[[:space:]]*\([0-9]\{1,\}\).*/\1/p' \
                "${RECIPE_DIR}/recipe.yaml" | head -1)"
    [[ -n "${_uv}" && -n "${_rv}" ]] || {
        echo "CVC_FULL_VERSION unset and recipe.yaml has no readable version" >&2; exit 1; }
    IMG_VERSION="${_uv}+cvc.${_rv}"
    echo "NOTE: CVC_FULL_VERSION unset; composed ${IMG_VERSION} from recipe.yaml" >&2
fi

# access.ssh_user is emitted only when step 5 actually established it. An
# ABSENT key is a question the consumer must answer; a guessed literal is a
# silent login failure on a guest with no console.
if [[ -n "${SSH_USER}" ]]; then
    SSH_USER_YAML="  # DERIVED, never a literal: Haiku names this account from
  # HAIKU_ROOT_USER_NAME and its upstream default is not 'user'.
  # source: ${SSH_USER_SRC}
  ssh_user: ${SSH_USER}"
    SSH_USER_ENV="CVCPKG_IMAGE_SSH_USER=${SSH_USER}"
else
    SSH_USER_YAML="  # ssh_user is deliberately ABSENT: this build could not read the login
  # account out of the artifact, out of UserBuildConfig, or out of the Haiku
  # tree, and a guess here is a silent login failure on a guest with no
  # out-of-band console. Pass one explicitly (--ssh-user)."
    SSH_USER_ENV="# CVCPKG_IMAGE_SSH_USER unset: see image.yaml's access: block."
fi

# ── 6b. image.yaml — the canonical descriptor ───────────────────────────
# Every field here replaces a constant a provisioning script would otherwise
# hardcode (disk bus, firmware, minimum sizes, ssh user, importer path). Every
# VALUE here is either a ${VAR} resolved above from the artifact/build inputs,
# or one of the labelled POLICY constants — there are no bare literals left in
# this heredoc except the schema version and the schema's own enum spellings.
cat > "${IMGDIR}/image.yaml" <<YAML
# Generated by recipes/haiku-image/build.sh — do not edit.
schema_version: 1
image:
  package: ${PKG_NAME}
  version: ${IMG_VERSION}
  guest_os: ${IMG_GUEST_OS}
  guest_arch: ${HAIKU_ARCH}
  guest_release: ${HAIKU_REF}
  variant: ${HAIKU_VARIANT}
disks:
  # file/format/virtual_size_bytes/sha256 are all read back off the staged
  # artifact (qemu-img info + sha256sum), never asserted.
  - file: ${DISK_FILE}
    format: ${DISK_FORMAT}
    role: root
    virtual_size_bytes: ${DISK_VIRTUAL_SIZE}
    sha256: "${DISK_SHA256}"
boot:
  firmware: ${BOOT_FIRMWARE}
  # NVMe. Not virtio-blk, not virtio-scsi. This is the single most important
  # line in the file, and it was bisected live against Haiku r1/beta5 under
  # Incus/QEMU rather than inferred:
  #   virtio-blk  -> general protection fault (vector 0xd) in virtio_pci
  #                  notify_queue(); the VM never reaches userland.
  #   virtio-scsi -> no Haiku driver at all; the disk does not appear and the
  #                  boot loader dies in vfs_mount_boot_file_system.
  #   nvme        -> boots into userland. Haiku's NVMe driver is native and
  #                  pre-dates its virtio work.
  # An earlier revision said virtio-blk here, inferred from "Haiku panics on
  # virtio-scsi" without anyone checking that virtio-blk works. It does not.
  disk_bus: ${BOOT_DISK_BUS}
  net_model: ${BOOT_NET_MODEL}
  # Haiku's serial port is kernel-debug only: there is NO out-of-band admin
  # channel. Networking and keys must be right before a headless deploy.
  console: ${BOOT_CONSOLE}
  secureboot: ${BOOT_SECUREBOOT}
  # POLICY. UNITS: vCPUs.
  cpu_min: ${BOOT_CPU_MIN}
  # POLICY. UNITS: MiB.
  memory_min_mib: ${BOOT_MEMORY_MIN_MIB}
  # DERIVED. UNITS: GiB. ceil(virtual_size_bytes / 1 GiB) — a hypervisor
  # refuses a root volume smaller than the image's virtual size, so this is a
  # hard floor. It is NOT extra headroom: the guest's usable space was fixed
  # at build time by HAIKU_IMAGE_SIZE (${HAIKU_IMAGE_SIZE_MIB:-?} MiB) because
  # BFS cannot be grown, so a larger volume gives the guest nothing.
  disk_min_gib: ${DISK_MIN_GIB}
access:
${SSH_USER_YAML}
  # Set from a READ-BACK of the injected file, never from an exit status.
  ssh_pubkey_baked: ${SSH_PUBKEY_BAKED}
importers:
  incus: ${INCUS_META}
  lxd: ${INCUS_META}
# ADVISORY: qcow2 is a read-write format, so booting this master in place
# mutates it and breaks \`cvcpkg image verify\`. Boot an overlay instead:
#   qemu-img create -f qcow2 -F qcow2 -b ${DISK_FILE} overlay.qcow2
# File modes cannot enforce this — they do not survive archive extraction.
writable: ${IMG_WRITABLE}
docs: ${DOCS_FILE}
YAML

# ── 6c. image.env — the same facts, flattened ───────────────────────────
# An Incus cluster node reliably has neither jq nor yq nor pkg-config, but every
# /bin/sh can source KEY=value. Paths are RELATIVE to this directory so it stays
# correct after a copy; \`cvcpkg image env\` regenerates the same keys with
# absolute paths.
#
# Rendered from the SAME variables as image.yaml above. It used to be a second
# hand-typed copy of every value, which is how it came to say
# CVCPKG_IMAGE_DISK_MIN_GIB=50 next to a disk_min_gib: 50 that was also wrong —
# one lie in two places. Keys with no value are omitted, matching
# cvcpkg.images.env_map, so a consumer's \`:-\` default still fires.
cat > "${IMGDIR}/image.env" <<ENV
# Generated by recipes/haiku-image/build.sh — do not edit.
# Paths are relative to this file's directory:
#   cd \$PREFIX/share/${PKG_NAME} && . ./image.env
CVCPKG_IMAGE_NAME=${PKG_NAME}
CVCPKG_IMAGE_VERSION=${IMG_VERSION}
CVCPKG_IMAGE_DISK=${DISK_FILE}
CVCPKG_IMAGE_DISK_FORMAT=${DISK_FORMAT}
CVCPKG_IMAGE_DISK_BUS=${BOOT_DISK_BUS}
CVCPKG_IMAGE_FIRMWARE=${BOOT_FIRMWARE}
CVCPKG_IMAGE_NET_MODEL=${BOOT_NET_MODEL}
CVCPKG_IMAGE_CONSOLE=${BOOT_CONSOLE}
CVCPKG_IMAGE_SECUREBOOT=${BOOT_SECUREBOOT}
CVCPKG_IMAGE_CPU_MIN=${BOOT_CPU_MIN}
CVCPKG_IMAGE_MEMORY_MIN_MIB=${BOOT_MEMORY_MIN_MIB}
CVCPKG_IMAGE_DISK_MIN_GIB=${DISK_MIN_GIB}
${SSH_USER_ENV}
CVCPKG_IMAGE_GUEST_OS=${IMG_GUEST_OS}
CVCPKG_IMAGE_GUEST_ARCH=${HAIKU_ARCH}
CVCPKG_IMAGE_GUEST_RELEASE=${HAIKU_REF}
CVCPKG_IMAGE_VARIANT=${HAIKU_VARIANT}
CVCPKG_IMAGE_WRITABLE=${IMG_WRITABLE}
CVCPKG_IMAGE_INCUS_METADATA=${INCUS_META}
CVCPKG_IMAGE_LXD_METADATA=${INCUS_META}
CVCPKG_IMAGE_DOCS=${DOCS_FILE}
CVCPKG_IMAGE_CHECKSUMS=${CHECKSUMS_FILE}
ENV

# SHA256SUMS in `sha256sum -c` format. Deliberately duplicates the descriptor's
# digest: this serves a zero-dependency check after the payload has been copied
# to another machine, the descriptor serves the CLI. Reuses the digest computed
# above so the multi-gigabyte file is read once.
( cd "${IMGDIR}" && {
    printf '%s  %s\n' "${DISK_SHA256}" "${DISK_FILE}"
    sha256sum "${DOCS_FILE}" image.yaml image.env incus/metadata.yaml "${INCUS_META}"
  } > "${CHECKSUMS_FILE}" )

echo "Haiku builder image staged to ${IMGDIR}:"
ls -lhR "${IMGDIR}"
( cd "${IMGDIR}" && sha256sum -c "${CHECKSUMS_FILE}" )
