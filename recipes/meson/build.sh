#!/usr/bin/env bash
# recipes/meson/build.sh — install Meson into the prefix on Linux and macOS.
#
# Meson is a pure-Python application.  We copy its package tree into
# lib/meson/ and create a thin wrapper script in bin/meson.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"

MESON_LIB="${CVC_INSTALL_DIR}/lib/meson"
mkdir -p "${CVC_INSTALL_DIR}/bin" "${MESON_LIB}"

# Copy the meson package and entry point.
cp -r "${CVC_SOURCE_DIR}/mesonbuild" "${MESON_LIB}/"
cp    "${CVC_SOURCE_DIR}/meson.py"   "${MESON_LIB}/"

# Create a wrapper script.
cat > "${CVC_INSTALL_DIR}/bin/meson" <<'WRAPPER'
#!/usr/bin/env python3
import sys, os
meson_lib = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "meson")
sys.path.insert(0, meson_lib)
from mesonbuild.mesonmain import main
sys.exit(main())
WRAPPER
chmod +x "${CVC_INSTALL_DIR}/bin/meson"

echo "meson installed to ${CVC_INSTALL_DIR}"
