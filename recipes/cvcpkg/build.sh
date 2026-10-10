#!/usr/bin/env bash
# recipes/cvcpkg/build.sh — freeze the cvcpkg CLI into one self-contained bin/cvcpkg.
#
# The same build as .github/workflows/cvcpkg-standalone.yml (PyInstaller over
# packaging/cvcpkg.spec), but from cvcpkg's own recipe graph instead of pip and
# PyPI: the interpreter, PyInstaller and the bootloader it compiled, and every
# package frozen in are cvcpkg builds, and pip never touches the network
# (--no-index).  The result is the package /install.sh and /install.ps1 install.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

PYVER=3.12
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"

# Every dependency is a build edge, so it is staged into the build prefix; a
# remote builder installs the whole closure into one shared prefix instead.
# Look in both.
PY=""
for p in "${BLD}" "${DEPS}"; do
    if [ -x "${p}/bin/python${PYVER}" ]; then
        PY="${p}/bin/python${PYVER}"
        break
    fi
done
[ -n "${PY}" ] || { echo "cvcpkg: python${PYVER} not found under ${BLD} or ${DEPS}" >&2; exit 1; }
SITE="${BLD}/lib/python${PYVER}/site-packages:${DEPS}/lib/python${PYVER}/site-packages"
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
# PyInstaller freezes whatever the interpreter can import, and the prefix's
# python3.12 also searches the builder's ~/.local/lib/python3.12/site-packages,
# AHEAD of its own site-packages.  A builder that pip-installed cvcpkg there
# would leak its own copies (and stray optional imports: brotli, sqlalchemy)
# into the binary.  Freeze from the prefix only.
export PYTHONNOUSERSITE=1
echo "cvcpkg: freezing cvcpkg ${CVC_VERSION} with ${PY}"

# ── 1. Install cvcpkg into a scratch prefix ─────────────────────────────────
# The spec copies cvcpkg's .dist-info into the binary (copy_metadata), and that
# is where `cvcpkg --version` reads its version, so cvcpkg has to be installed,
# not just importable from src/.  The in-tree backend (pep517-backend/, over
# poetry-core-cp312) builds it.
STAGE="${CVC_BUILD_DIR}/stage"
PYTHONPATH="${SITE}" "${PY}" -m pip install \
    --no-index \
    --no-deps \
    --no-build-isolation \
    --no-compile \
    --prefix "${STAGE}" \
    "${CVC_SOURCE_DIR}"
export PYTHONPATH="${STAGE}/lib/python${PYVER}/site-packages:${SITE}"

# ── 2. Freeze ────────────────────────────────────────────────────────────────
"${PY}" -m PyInstaller --clean --noconfirm \
    --distpath "${CVC_BUILD_DIR}/dist" \
    --workpath "${CVC_BUILD_DIR}/pyinstaller" \
    "${CVC_SOURCE_DIR}/packaging/cvcpkg.spec"
BIN="${CVC_BUILD_DIR}/dist/cvcpkg"
[ -x "${BIN}" ] || { echo "cvcpkg: PyInstaller produced no ${BIN}" >&2; exit 1; }

# ── 3. Verify what will actually ship ───────────────────────────────────────
# Run the binary with the build environment stripped: the library paths and
# PYTHONPATH set above would let a binary that is missing a library or a module
# pass here and fail on a user's machine.
bare() {
    env -u PYTHONPATH -u PYTHONHOME -u LD_LIBRARY_PATH -u DYLD_LIBRARY_PATH \
        -u LIBRARY_PATH "$@"
}

# The version comes from the bundled .dist-info, so this also proves the
# metadata made it in, and that the tarball is the release the recipe claims.
VERSION_OUT="$(bare "${BIN}" --version)"
echo "cvcpkg: ${VERSION_OUT}"
case "${VERSION_OUT}" in
    *" ${CVC_VERSION}") ;;
    *) echo "cvcpkg: expected version ${CVC_VERSION}, the binary says '${VERSION_OUT}'" >&2; exit 1 ;;
esac

# Validate a bundled recipe from a directory with no ./recipes, so the recipes,
# the schemas (and jsonschema) all have to come out of the executable itself.
EMPTY="${CVC_BUILD_DIR}/empty"
mkdir -p "${EMPTY}"
(cd "${EMPTY}" && bare "${BIN}" validate recipes/zlib)

# The extras are imported lazily (cvcpkg.optional) and degrade quietly when
# missing, so --version and validate would pass without them.  A frozen
# `builder run` without websockets falls back to HTTP long-poll with one log
# line.  Check the archive itself.  (A listing to a file, not a pipe: grep -q
# exiting early would SIGPIPE the viewer.)
"${PY}" -m PyInstaller.utils.cliutils.archive_viewer -r -b "${BIN}" >"${CVC_BUILD_DIR}/archive.txt"
for mod in websockets.sync.client httpx cryptography.hazmat.primitives.asymmetric.ed25519 \
    jsonschema yaml click; do
    grep -Eq "^[[:space:]]*${mod//./\\.}\$" "${CVC_BUILD_DIR}/archive.txt" \
        || { echo "cvcpkg: ${mod} is not bundled in ${BIN}" >&2; exit 1; }
done

# ── 4. Stage ─────────────────────────────────────────────────────────────────
mkdir -p "${CVC_INSTALL_DIR}/bin"
cp "${BIN}" "${CVC_INSTALL_DIR}/bin/cvcpkg"
chmod 755 "${CVC_INSTALL_DIR}/bin/cvcpkg"
echo "cvcpkg: staged ${CVC_INSTALL_DIR}/bin/cvcpkg"
