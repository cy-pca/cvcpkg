#!/usr/bin/env bash
# recipes/wand-cp313t/build.sh — install the pure-Python Wand wheel (cp313t) and
# make it bind the cvcpkg ImageMagick's libMagickWand hermetically.
#
# The wheel is fetched + sha256-verified by cvcpkg (source.type python_wheel)
# before this runs, so cvc_pip_install_wheel installs offline (--no-index) into
# the cvcpkg python313t's site-packages inside the prefix.
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"

cvc_pip_install_wheel

# ── Hermetic MagickWand binding ──
#
# Wand (ctypes) locates libMagickWand-7.Q16HDRI + ImageMagick's config via the
# MAGICK_HOME env var, else falls through to the system library path. Ship a
# .pth that sets MAGICK_HOME to the running interpreter's prefix (where the
# cvcpkg imagemagick lives) unless already set, so `import wand` binds the cvcpkg
# ImageMagick — not the host's — from any interpreter using this prefix,
# including VolRover's embedded Python. sys.prefix is computed at runtime, so it
# stays correct after the relocatable bundle is unpacked anywhere.
_site="$(find "${CVC_INSTALL_DIR}" -maxdepth 3 -type d -name 'site-packages' -print -quit)"
[ -n "${_site}" ] || { echo "wand: no site-packages under ${CVC_INSTALL_DIR}" >&2; exit 1; }
cat > "${_site}/wand_cvcpkg_magick_home.pth" <<'PTH'
import os, sys; os.environ.setdefault('MAGICK_HOME', sys.prefix)
PTH

# Self-check: drive a real MagickWand op against the cvcpkg imagemagick in the
# dep closure. (.pth files are processed only for site dirs, not the PYTHONPATH
# entry cvc_python_check adds, so set MAGICK_HOME explicitly here — the shipped
# .pth does the same job at import time once installed.)
export MAGICK_HOME="${CVC_DEPS_PREFIX}"
cvc_python_check "
import wand, wand.api
from wand.version import VERSION, MAGICK_VERSION
from wand.image import Image
with Image(width=8, height=8, background='red') as img:
    # MIFF is ImageMagick's native format — always built in (no codec delegate),
    # so this verifies the MagickWand binding without depending on which image
    # coders (jpeg/png/webp) the resolved imagemagick happens to ship.
    blob = img.make_blob('MIFF')
assert blob and len(blob) > 0, 'wand produced no image data'
lib = wand.api.libmagick._name
assert lib.startswith('${CVC_DEPS_PREFIX}'), 'NOT hermetic: bound ' + lib
print('Wand', VERSION, 'bound', MAGICK_VERSION)
print('libMagickWand:', lib)
"
