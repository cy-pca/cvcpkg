#!/usr/bin/env bash
# recipes/iniconfig/build.sh — install the pinned noarch iniconfig wheel into the
# reference interpreter's site-packages, then fan it into every interpreter.
# A build-time lint/test tool for cvcpkg recipes; not shipped to users.
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import iniconfig; print('iniconfig', getattr(__import__('iniconfig'), '__version__', 'ok'))"
