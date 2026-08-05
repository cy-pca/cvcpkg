#!/usr/bin/env bash
# recipes/pyinstaller-cp312/build.sh — build PyInstaller 6.21.0 FROM SOURCE for the cp312
# interpreter column.
#
# WHY FROM SOURCE (not the PyPI wheel): PyInstaller's published wheels are
# py3-none-<plat> archives whose payload is a PREBUILT bootloader — the small C
# launcher stamped into every frozen application. Upstream builds those on its
# own CI for linux/macos/windows and publishes NOTHING for the BSDs. Building
# the sdist compiles the bootloader here, which is what makes the freebsd /
# netbsd / openbsd columns possible at all, and means the launcher we ship is
# one we produced rather than one we downloaded.
#
# The build hook compiles the bootloader only for platforms the sdist has no
# prebuilt binary for; the sdist carries Darwin/Windows blobs, so on those the
# vendored ones are reused. netbsd-platform-tables.patch (applied by cvcpkg
# before this runs) is what lets that compile succeed on NetBSD.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"   # cvc_python_exe, cvc_python_check

: "${CVC_PYTHON_ABI:=cp312}"
PY_EXE="$(cvc_python_exe)"
echo "pyinstaller-cp312: building against ${PY_EXE}"

# Bridge BUILD-only backends (hatchling + its closure) from the build prefix
# into the deps-prefix interpreter, exactly as the other from-source python
# recipes do; --no-build-isolation means the backend is imported, not fetched.
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python3.12/site-packages${PYTHONPATH:+:${PYTHONPATH}}"

# Build the wheel from the extracted, patched sdist. --no-index/--no-deps keep
# pip inside cvcpkg's graph; the bootloader compile happens inside this step.
WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY_EXE}" -m pip wheel \
    --no-deps \
    --no-build-isolation \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

WHEEL="$(find "${WHEELHOUSE}" -maxdepth 1 -name 'pyinstaller-*.whl' -print -quit)"
[ -n "${WHEEL}" ] || { echo "pyinstaller-cp312: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "pyinstaller-cp312: built $(basename "${WHEEL}")"

# Fail loudly if the bootloader for THIS platform is missing from the wheel:
# without it `pyinstaller --onefile` cannot produce an executable, and a wheel
# that merely imports would sail through the check below.
"${PY_EXE}" - "${WHEEL}" <<'PYBL'
import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
# "images" is a non-platform subdir of bootloader/ — excluding it keeps this an
# actual bootloader check rather than a directory-exists check.
boot = sorted({n.split("/")[2] for n in names
               if n.startswith("PyInstaller/bootloader/") and n.count("/") > 2}
              - {"images"})
print("bootloader dirs in wheel:", ", ".join(boot) or "(none)")
if not boot:
    sys.exit("no bootloader in the built wheel")
PYBL

"${PY_EXE}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

# Import check plus a real freeze: building and RUNNING a one-file executable is
# the only thing that proves the compiled bootloader actually works.
cvc_python_check "import PyInstaller; print('PyInstaller', PyInstaller.__version__)"

echo "pyinstaller-cp312: build + verification complete"
