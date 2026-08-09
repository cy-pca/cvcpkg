#!/usr/bin/env bash
# recipes/semantic-version-cp313/build.sh — install the pinned wheel.
#
# Pure-Python leaf of the Rust build-backend chain: setuptools-rust imports
# semantic_version at module scope, so it has to be in the prefix before
# setuptools-rust (hence maturin, hence bcrypt) can be used.
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
# Exercise the two entry points setuptools-rust actually calls, not just the
# import: SimpleSpec/Version are what rustc_info.py uses to compare the detected
# rustc against a recipe's rust-version bound.
cvc_python_check "
from semantic_version import SimpleSpec, Version
assert Version('1.90.0') in SimpleSpec('>=1.64.0')
assert Version('1.63.0') not in SimpleSpec('>=1.64.0')
"
