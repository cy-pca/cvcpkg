#!/usr/bin/env bash
# recipes/matplotlib-cp311/build.sh — install the pinned prebuilt matplotlib
# wheel (source.type python_wheel). cvcpkg fetched + sha256-verified it into
# CVC_SOURCE_DIR; this installs it into the prefix's python311 site-packages and
# smoke-tests the Agg renderer (the check the from-source recipe carried).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig = plt.figure()
fig.add_subplot(111).plot([0, 1], [0, 1])
fig.canvas.draw()
print('matplotlib', matplotlib.__version__, 'Agg render OK | from', matplotlib.__file__)
"
