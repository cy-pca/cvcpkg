# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""cvcpkg — component package manager for libcvc-deps."""

from importlib.metadata import version as _pkg_version

__version__ = _pkg_version("cvcpkg")
__supported_schemas__ = {1, 2, 3}
