#!/usr/bin/env bash
# recipes/numpy-cp313/build.sh — install the pinned cp313 NumPy wheel.
#
# The wheel is fetched and sha256-verified by cvcpkg (source.type
# python_wheel) before this runs, so the install is offline (--no-index).
set -euo pipefail

. "$(dirname "$0")/../_common/python-wheel.sh"

cvc_pip_install_wheel

# Exercise a real ufunc, not just the import: on a free-threaded build the
# import can succeed while the compiled loops are what actually have to be
# thread-safe.
cvc_python_check "
import numpy as np
a = np.arange(12, dtype=np.float64).reshape(3, 4)
assert a.sum() == 66.0, a.sum()
assert (a @ a.T).shape == (3, 3)
print('numpy', np.__version__, 'from', np.__file__)
"
