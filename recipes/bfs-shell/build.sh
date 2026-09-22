#!/usr/bin/env bash
# Build Haiku's host-side bfs_shell from the pinned Haiku source tree.
#
# bfs_shell is a HOST tool (<build>bfs_shell): it manipulates a BFS filesystem
# inside a disk image on the build/host machine. We build ONLY that jam target,
# not the OS image. Haiku's ./configure still needs a cross-tools target to
# produce a usable BuildConfig, so the cross-toolchain build is the long pole;
# there is no lighter host-only configure that yields <build> tools.
set -euo pipefail
: "${CVC_BUILD_DIR:?}"; : "${CVC_INSTALL_DIR:?}"
JOBS="${CVC_JOBS:-$(nproc 2>/dev/null || echo 4)}"
HAIKU_REF="${HAIKU_REF:-r1beta5}"
BUILDTOOLS_REF="${BUILDTOOLS_REF:-r1beta5}"
HAIKU_ARCH="${HAIKU_ARCH:-x86_64}"
HAIKU_REPO="${HAIKU_REPO:-https://github.com/haiku/haiku.git}"
BUILDTOOLS_REPO="${BUILDTOOLS_REPO:-https://github.com/haiku/buildtools.git}"

cd "${CVC_BUILD_DIR}"
[[ -d haiku ]]      || git clone --single-branch --branch "${HAIKU_REF}"      "${HAIKU_REPO}"      haiku
[[ -d buildtools ]] || git clone --depth 1 --branch "${BUILDTOOLS_REF}" "${BUILDTOOLS_REPO}" buildtools

# Haiku's own Jam (configure invokes it; also our target driver).
( cd buildtools/jam && make )
JAM_BIN="$(find "${CVC_BUILD_DIR}/buildtools/jam" -maxdepth 2 -type f -name jam -perm -u+x 2>/dev/null | head -1)"
[[ -n "${JAM_BIN}" ]] || { echo "jam did not build in buildtools/jam" >&2; exit 1; }

cd haiku
if [[ ! -e generated/build/BuildConfig && ! -e generated.${HAIKU_ARCH}/build/BuildConfig ]]; then
    # --build-cross-tools is required for a usable BuildConfig even for <build>
    # host tools; it compiles the cross gcc (the slow step).
    ./configure -j"${JOBS}" --build-cross-tools "${HAIKU_ARCH}" \
        --cross-tools-source "${CVC_BUILD_DIR}/buildtools"
fi

"${JAM_BIN}" -q -j"${JOBS}" "<build>bfs_shell" 2>/dev/null \
    || "${JAM_BIN}" -q -j"${JOBS}" bfs_shell
BFS_SHELL="$(find generated* -type f -name bfs_shell -perm -u+x 2>/dev/null | head -1)"
[[ -n "${BFS_SHELL}" ]] || { echo "bfs_shell not produced by jam" >&2; exit 1; }

install -Dm755 "${BFS_SHELL}" "${CVC_INSTALL_DIR}/bin/bfs_shell"

# bfs_shell links libroot_build.so (Haiku's host libroot). Ship it and point
# bfs_shell at it with an $ORIGIN-relative RPATH: provisioning runs bfs_shell
# under sudo (losetup needs root), and sudo drops LD_LIBRARY_PATH, so a plain
# lib/ next to it is not enough — the RPATH must be baked in.
LIBROOT="$(find "${CVC_BUILD_DIR}/haiku/generated" -name libroot_build.so -type f 2>/dev/null | head -1)"
[[ -n "${LIBROOT}" ]] || { echo "libroot_build.so not found" >&2; exit 1; }
install -Dm755 "${LIBROOT}" "${CVC_INSTALL_DIR}/lib/libroot_build.so"
# cvcpkg ships its own patchelf; fall back to a system one.
PATCHELF="$(command -v patchelf || echo patchelf)"
"${PATCHELF}" --set-rpath '$ORIGIN/../lib' "${CVC_INSTALL_DIR}/bin/bfs_shell"

echo "staged bfs_shell -> ${CVC_INSTALL_DIR}/bin/bfs_shell (+ lib/libroot_build.so)"
