# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""cvcpkg — component package manager for libcvc-deps."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

# Keep in sync with [tool.poetry] version in pyproject.toml.  Used ONLY when no
# installed distribution metadata exists — i.e. the source tree is on
# PYTHONPATH rather than pip-installed.  That is the pip-free install route for
# platforms with no wheel of their own (see docs/haikuports-integration.md);
# without this fallback, importing cvcpkg at all died with
# PackageNotFoundError, one line into __init__.
_FALLBACK_VERSION = "2.0.2"

try:
    __version__ = _pkg_version("cvcpkg")
except PackageNotFoundError:  # source checkout, not an installed distribution
    __version__ = _FALLBACK_VERSION

__supported_schemas__ = {1, 2, 3}
