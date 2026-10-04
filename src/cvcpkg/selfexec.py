# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Run *this* cvcpkg again as a child process.

cvcpkg ships two ways, and they need different command lines to start a copy
of themselves:

* **pip / source install** -- ``sys.executable`` is a Python interpreter, so the
  child is ``python -m cvcpkg <args>``.
* **PyInstaller single-file binary** (``packaging/cvcpkg.spec``) -- ``sys.frozen``
  is set and ``sys.executable`` *is* cvcpkg: the binary hands its argv straight
  to the click CLI.  ``<binary> -m cvcpkg ...`` is therefore parsed as a cvcpkg
  option and rejected with ``No such option '-m'``, so the child must be
  ``<binary> <args>``.

Getting that wrong is not a soft failure.  ``cvcpkg builder fleet`` restarts a
worker that exits, so a frozen fleet whose workers die on argv parsing
crash-loops every few seconds and never registers a builder.  Every place that
re-invokes cvcpkg goes through :func:`cvcpkg_argv` / :func:`cvcpkg_env` so the
two forms cannot drift apart again.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

# The combined client+server binary (packaging/cvcpkg_launcher.py) picks the
# server or client CLI from the program name or this variable.
_ENTRY_ENV = "CVCPKG_ENTRY"

# PyInstaller (>= 6.9) onefile bootloader: a child started from
# ``sys.executable`` inherits ``_PYI_*`` variables and, by default, reuses the
# parent's unpacked ``_MEIPASS`` directory instead of unpacking its own.  This
# variable tells the child to ignore them and start as an independent instance.
_PYI_RESET_ENV = "PYINSTALLER_RESET_ENVIRONMENT"


def is_frozen() -> bool:
    """True when running from the PyInstaller single-file binary."""
    return bool(getattr(sys, "frozen", False))


def cvcpkg_argv(*args: str) -> list[str]:
    """The argv that runs ``cvcpkg <args>`` with the same cvcpkg as this process.

    ``[python, "-m", "cvcpkg", *args]`` for a pip/source install, and
    ``[binary, *args]`` for the frozen single binary.
    """
    if is_frozen():
        return [sys.executable, *args]
    return [sys.executable, "-m", "cvcpkg", *args]


def cvcpkg_env(
    base: Mapping[str, str] | None = None,
    *,
    independent: bool = False,
) -> dict[str, str]:
    """The environment for a child started with :func:`cvcpkg_argv`.

    A copy of ``base`` (default: ``os.environ``).  For a pip/source install
    nothing is added.  For the frozen binary:

    * ``CVCPKG_ENTRY=client`` -- the child runs the *client* CLI even when this
      is the combined binary and its file name (which is what the child sees as
      its program name) would otherwise route it to ``cvcpkg-server``.
    * ``independent=True`` adds ``PYINSTALLER_RESET_ENVIRONMENT=1`` so the child
      unpacks its own copy instead of borrowing the parent's ``_MEIPASS``.  Use
      it for long-lived children such as builder workers: they must not depend
      on the parent's temporary directory staying in place, and a worker
      restarted after the binary was replaced on disk must not mix the new
      binary's Python code with the old one's unpacked libraries and recipes.
      A short child the parent waits for can leave it off and skip the unpack.
    """
    env = dict(os.environ if base is None else base)
    if is_frozen():
        env[_ENTRY_ENV] = "client"
        if independent:
            env[_PYI_RESET_ENV] = "1"
    return env
