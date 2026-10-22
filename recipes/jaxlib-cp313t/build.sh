#!/usr/bin/env bash
# recipes/jaxlib-cp313t/build.sh — install the pinned prebuilt jaxlib 0.8.2
# wheel (source.type python_wheel; cvcpkg already fetched and sha256-verified
# it) into this column's site-packages, then prove the XLA extension loads.
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"

cvc_pip_install_wheel

# jaxlib 0.8.2's x86 build is compiled for AVX and refuses to import on a CPU
# without it (jaxlib/cpu_feature_guard.c).  On such a builder (lat) the check
# confirms the installed distribution and says plainly that nothing was
# imported, instead of failing a correct package on unsuitable hardware.
cvc_python_check "
import os
info = open('/proc/cpuinfo').read().split() if os.path.exists('/proc/cpuinfo') else ['avx']
if 'avx' not in info:
    import importlib.metadata as md
    assert md.version('jaxlib') == '0.8.2', md.version('jaxlib')
    print('jaxlib-cp313t: this CPU has no AVX; checked the installed wheel, did not import it')
else:
    import numpy, scipy, ml_dtypes
    import jaxlib
    from jaxlib import xla_client
    from jaxlib.version import __version__
    assert __version__ == '0.8.2', __version__
    print('jaxlib', __version__, '| xla_client', xla_client.__file__)
"
