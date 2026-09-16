#!/usr/bin/env bash
# recipes/contourpy-cp311/build.sh — install the pinned prebuilt contourpy wheel
# (source.type python_wheel). cvcpkg fetched + sha256-verified the wheel into
# CVC_SOURCE_DIR; this installs it into the prefix's python311 site-packages.
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "
import contourpy
print('contourpy', contourpy.__version__, '| from', contourpy.__file__)
"
