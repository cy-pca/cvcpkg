#!/usr/bin/env bash
# recipes/pillow-cp311/build.sh — install the pinned prebuilt Pillow wheel
# (source.type python_wheel). cvcpkg fetched + sha256-verified the wheel into
# CVC_SOURCE_DIR; this installs it (PIL/ + the vendored pillow.libs/) into the
# prefix's python311 site-packages. The check asserts the codecs the grl-snam
# imaging path needs are present — a codec-less Pillow must NOT ship.
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "
from PIL import Image, features
assert features.check('zlib'), 'Pillow: no zlib/PNG codec'
assert features.check('jpg'), 'Pillow: no JPEG codec'
print('Pillow', Image.__version__ if hasattr(Image, '__version__') else '', 'codecs OK (zlib+jpg) | from', Image.__file__)
"
