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
from collections.abc import Mapping, MutableMapping

# The combined client+server binary (packaging/cvcpkg_launcher.py) picks the
# server or client CLI from the program name or this variable.
_ENTRY_ENV = "CVCPKG_ENTRY"

# PyInstaller (>= 6.9) onefile bootloader: a child started from
# ``sys.executable`` inherits ``_PYI_*`` variables and, by default, reuses the
# parent's unpacked ``_MEIPASS`` directory instead of unpacking its own.  This
# variable tells the child to ignore them and start as an independent instance.
_PYI_RESET_ENV = "PYINSTALLER_RESET_ENVIRONMENT"

# The onefile bootloader prepends its unpack directory (``sys._MEIPASS``) to the
# dynamic linker's search path and keeps the caller's value as ``<VAR>_ORIG``
# (unset when the caller had none).  ELF platforms use LD_LIBRARY_PATH, AIX
# LIBPATH; macOS and Windows are not modified by the bootloader.
_LIBPATH_VARS = ("LD_LIBRARY_PATH", "LIBPATH")


def is_frozen() -> bool:
    """True when running from the PyInstaller single-file binary."""
    return bool(getattr(sys, "frozen", False))


def restore_library_path(env: MutableMapping[str, str]) -> None:
    """Undo the frozen bootloader's library-path change in *env*, in place.

    Called on ``os.environ`` at frozen startup, so every program cvcpkg runs --
    build scripts, compilers, git, ssh, a child cvcpkg -- gets the caller's
    library path instead of the binary's bundled libssl/libz/libtinfo/... in
    front of the system's.  This process is unaffected: the dynamic linker read
    LD_LIBRARY_PATH when it was exec'd, and later dlopen()s keep using that.
    No-op for a pip/source install.
    """
    if not is_frozen():
        return
    meipass = getattr(sys, "_MEIPASS", "")
    for var in _LIBPATH_VARS:
        orig = env.pop(f"{var}_ORIG", None)
        if orig is not None:
            env[var] = orig
        elif meipass and env.get(var, "").split(os.pathsep)[0] == meipass:
            del env[var]  # the caller had none; the bootloader created it


def cvcpkg_argv(*args: str) -> list[str]:
    """The argv that runs ``cvcpkg <args>`` with the same cvcpkg as this process.

    ``[python, "-m", "cvcpkg", *args]`` for a pip/source install, and
    ``[binary, *args]`` for the frozen single binary.
    """
    if is_frozen():
        return [sys.executable, *args]
    return [sys.executable, "-m", "cvcpkg", *args]


def cvcpkg_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for a child started with :func:`cvcpkg_argv`.

    A copy of ``base`` (default: ``os.environ``).  For a pip/source install
    nothing is added.  For the frozen binary:

    * ``CVCPKG_ENTRY=client`` -- the child runs the *client* CLI even when this
      is the combined binary and its file name (which is what the child sees as
      its program name) would otherwise route it to ``cvcpkg-server``.
    * ``PYINSTALLER_RESET_ENVIRONMENT=1`` -- the child unpacks its own copy
      instead of borrowing this process's ``_MEIPASS``.  A borrowed unpack only
      works while ``_MEIPASS`` is on the library path, which
      :func:`restore_library_path` deliberately undoes at startup; a long-lived
      child (a fleet worker) must also not depend on this process's temporary
      directory, nor mix a replaced binary's code with the old unpack.
    """
    env = dict(os.environ if base is None else base)
    if is_frozen():
        env[_ENTRY_ENV] = "client"
        env[_PYI_RESET_ENV] = "1"
    return env
