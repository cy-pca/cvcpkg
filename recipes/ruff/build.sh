#!/usr/bin/env bash
# recipes/ruff/build.sh — install the pinned ruff binary wheel into the
# python311 site-packages (proxy module) and bin/ (the Rust binary). A
# build-time linter for cvcpkg recipes; not shipped to users.
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
# Prove the binary actually runs, not just that the proxy module imports.
"${CVC_INSTALL_DIR}/bin/ruff" --version
cvc_python_check "import ruff"
