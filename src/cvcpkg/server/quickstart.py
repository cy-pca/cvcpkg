# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""What kind of package a recipe is, for the package page's Quick Start.

The Quick Start used to tell every visitor to point CMake at the prefix.
That is right for a C/C++ library and wrong for most of the catalog: a
``-cp313`` Python column is used by importing it from the prefix's own
interpreter, a ``-src`` package is the upstream source other recipes build
from, and a tool is used by putting the prefix's ``bin/`` on PATH.

:func:`profile` reads that from the recipe -- its ``python:`` block, its
``package.files`` globs, its ``cmake_packages`` and tags -- and, when the
page has no recipe to read, from the name alone.  It never runs anything and
never guesses an import name the recipe does not ship.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: ``attrs-cp313``, ``numpy-cp313t``, ``torch-cp311-cuda``: one column of the
#: per-interpreter Python matrix (see docs/python-wheels.md).
_COLUMN_RE = re.compile(r"^(?P<base>.+?)-cp(?P<major>3)(?P<minor>\d+)(?P<ft>t?)(?P<variant>-.+)?$")

#: ``python311`` .. ``python313t``: a versioned CPython interpreter recipe.
_INTERPRETER_RE = re.compile(r"^python(?P<major>3)(?P<minor>\d+)(?P<ft>t?)$")

#: Tags that mark a package as something you run rather than link against.
_TOOL_TAGS = frozenset({"tools", "language", "compiler", "build-system", "build-tool", "host-tool"})

#: A command name as it appears under ``bin/`` (``7z`` included): no wildcard
#: leftovers, no trailing separator from a ``fc-*``-style prefix glob.
_COMMAND_RE = re.compile(r"^[A-Za-z0-9][\w.+-]*[A-Za-z0-9]$|^[A-Za-z0-9]$")

_WINDOWS_ONLY_SUFFIXES = (".exe", ".cmd", ".bat", ".dll", ".ps1", ".bin")

#: What a Windows build puts under ``bin/`` that is a library, not a command.
_BIN_LIBRARY_SUFFIXES = (".dll", ".lib", ".pdb")


@dataclass(frozen=True)
class QuickStartProfile:
    """How a package is used, as much as the recipe says.

    ``kind`` is one of ``library`` (the CMake flow), ``python`` (a
    per-interpreter column), ``interpreter`` (a versioned CPython),
    ``tool`` (commands under ``bin/``), ``source`` (staged upstream source)
    or ``files`` (anything else: fonts, certificate bundles, data).
    """

    kind: str
    #: ``python3.13`` / ``python3.13t``: the POSIX command of the interpreter.
    python_cmd: str = ""
    #: ``python.exe`` / ``python3.13t.exe``: the same interpreter on Windows,
    #: which sits at the prefix root rather than under ``bin/``.
    windows_exe: str = ""
    #: ``cp313`` / ``cp313t``.
    abi: str = ""
    #: The interpreter recipe a column depends on (``python313``).
    interpreter: str = ""
    #: The column's base name (``attrs`` for ``attrs-cp313``).
    base: str = ""
    #: What follows the ABI in a column's name (``-cuda`` for
    #: ``torch-cp311-cuda``): its other-interpreter siblings share it.
    variant: str = ""
    #: Top-level import name, from the ``site-packages/`` globs.
    module: str = ""
    #: Commands the package installs under ``bin/``.
    commands: tuple[str, ...] = ()
    #: Where a ``files`` package lands in the prefix.
    paths: tuple[str, ...] = ()
    #: The recipe's ``upstream_version``, for pinning a source package.
    upstream_version: str = ""


def _python_names(major: str, minor: str, ft: str) -> tuple[str, str, str, str]:
    """``(python_cmd, windows_exe, abi, interpreter)`` for CPython *major*.*minor*[t].

    CPython's own Windows layout names the default interpreter ``python.exe``
    and a free-threaded one ``python3.13t.exe``; the interpreter recipes keep
    that layout (see their ``platforms: [windows]`` globs).
    """
    version = f"{major}.{minor}{ft}"
    windows_exe = f"python{version}.exe" if ft else "python.exe"
    return f"python{version}", windows_exe, f"cp{major}{minor}{ft}", f"python{major}{minor}{ft}"


# Pushed recipes come from any publisher and nothing validates their shape, so
# every value read below is type-checked first: a wrong shape reads as absent
# rather than raising on a public page.  Only plain scalars are ever passed
# to str(), which would otherwise expand a nested YAML alias tree in full.


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _items(value: object) -> list:
    return value if isinstance(value, list) else []


def _text(value: object) -> str:
    return str(value) if isinstance(value, str | int | float) else ""


def _globs(package: dict) -> list[str]:
    out: list[str] = []
    for f in _items(package.get("files")):
        glob = f if isinstance(f, str) else _mapping(f).get("glob")
        if isinstance(glob, str) and glob:
            out.append(glob)
    return out


def _commands(globs: list[str]) -> tuple[str, ...]:
    """Command names from ``bin/<name>`` globs, in recipe order.

    ``bin/python3.13*`` (a name plus its suffixed variants) counts as
    ``python3.13``; ``bin/fc-*`` or ``bin/`` (a whole directory) names no
    single command and is skipped, as are the Windows-only ``.exe``/``.cmd``
    twins of a command already listed.
    """
    out: list[str] = []
    for g in globs:
        if not g.startswith("bin/"):
            continue
        name = g[len("bin/") :]
        if name.endswith("*"):
            name = name[:-1]
        if not name or name.lower().endswith(_WINDOWS_ONLY_SUFFIXES):
            continue
        if not _COMMAND_RE.match(name) or name in out:
            continue
        out.append(name)
    return tuple(out)


def _module(globs: list[str]) -> str:
    """The top-level import name a Python column installs, or ``""``.

    Read from its ``site-packages/`` globs: ``bs4/`` -> ``bs4``,
    ``six.py`` -> ``six``, ``pybullet.cpython-*.so`` -> ``pybullet``, and a
    namespace package keeps the levels below it (``google/protobuf/`` ->
    ``google.protobuf``) since ``import google`` alone proves nothing.
    Metadata (``*.dist-info``) and wildcards are skipped; a private
    ``_name`` is used only when nothing public is shipped.
    """
    public: list[str] = []
    private: list[str] = []
    for g in globs:
        _, sep, rest = g.partition("site-packages/")
        if not sep or not rest:
            continue
        parts = rest.split("/")
        first = parts[0]
        if first.endswith((".dist-info", ".egg-info", ".data", ".libs", ".pth")):
            continue
        is_dir = len(parts) > 1
        if not is_dir:
            if first.endswith(".py"):
                first = first[: -len(".py")]
            elif ".cpython-" in first or first.endswith((".so", ".pyd")):
                first = first.split(".", 1)[0]
            else:
                continue
        if not first.isidentifier():
            continue
        # A namespace directory (google/, ruamel/) carries its real package
        # further down: google/cloud/storage/ -> google.cloud.storage.
        dotted = first
        for part in parts[1:-1] if is_dir else ():
            if not part.isidentifier():
                break
            dotted += "." + part
        bucket = private if first.startswith("_") else public
        if dotted not in bucket:
            bucket.append(dotted)
    return (public or private or [""])[0]


def _top_paths(globs: list[str]) -> tuple[str, ...]:
    out: list[str] = []
    for g in globs:
        path = g.split("*", 1)[0]
        if path and path not in out:
            out.append(path)
    return tuple(out[:3])


def _from_name(name: str) -> QuickStartProfile:
    """The profile the name alone supports (no recipe on hand)."""
    if name.endswith("-src"):
        return QuickStartProfile(kind="source")
    m = _COLUMN_RE.match(name)
    if m:
        cmd, exe, abi, interp = _python_names(m["major"], m["minor"], m["ft"])
        return QuickStartProfile(
            kind="python",
            python_cmd=cmd,
            windows_exe=exe,
            abi=abi,
            interpreter=interp,
            base=m["base"],
            variant=m["variant"] or "",
        )
    m = _INTERPRETER_RE.match(name)
    if m:
        cmd, exe, abi, interp = _python_names(m["major"], m["minor"], m["ft"])
        return QuickStartProfile(
            kind="interpreter", python_cmd=cmd, windows_exe=exe, abi=abi, interpreter=interp
        )
    return QuickStartProfile(kind="library")


def profile(name: str, doc: dict | None = None) -> QuickStartProfile:
    """Classify *name* for the Quick Start, from its parsed recipe *doc*.

    Without a recipe (a package published from prebuilt artifacts, or a page
    the visitor may not read the recipe of) the name decides, and anything
    the name does not mark as Python or source keeps the CMake flow.
    """
    guess = _from_name(name)
    if not isinstance(doc, dict):
        return guess

    recipe = _mapping(doc.get("recipe"))
    package = _mapping(doc.get("package"))
    tags = {t for t in _items(recipe.get("tags")) if isinstance(t, str)}
    globs = _globs(package)
    commands = _commands(globs)
    upstream = _text(recipe.get("upstream_version"))
    has_cmake = any(_text(_mapping(c).get("name")) for c in _items(package.get("cmake_packages")))

    if guess.kind == "python":
        # The python: block's abi is authoritative; the name only suggested it.
        python_block = _mapping(doc.get("python"))
        m = re.fullmatch(r"cp(3)(\d+)(t?)", _text(python_block.get("abi")))
        if m:
            cmd, exe, abi, interp = _python_names(*m.groups())
        else:
            cmd, exe, abi, interp = (
                guess.python_cmd,
                guess.windows_exe,
                guess.abi,
                guess.interpreter,
            )
        interp = _text(python_block.get("interpreter")) or interp
        return QuickStartProfile(
            kind="python",
            python_cmd=cmd,
            windows_exe=exe,
            abi=abi,
            interpreter=interp,
            base=guess.base,
            variant=guess.variant,
            module=_module(globs),
            commands=commands,
        )
    if guess.kind == "interpreter":
        return guess
    if globs and all(g.startswith("src/") for g in globs):
        return QuickStartProfile(kind="source", upstream_version=upstream)
    if has_cmake:
        return QuickStartProfile(kind="library")
    # A Windows build's runtime DLLs sit under bin/ too (openmp's libomp.dll);
    # they make nothing to run.
    runnable = commands or any(
        g.startswith("bin/") and not g.lower().endswith(_BIN_LIBRARY_SUFFIXES) for g in globs
    )
    if tags & _TOOL_TAGS and "libraries" not in tags and runnable:
        return QuickStartProfile(kind="tool", commands=commands)
    # Headers make a library.  lib/ alone does only when nothing is run: a
    # database server ships lib/ plugins and runtime libraries beside the
    # commands that are the point of it.
    if any(g.startswith("include/") for g in globs) or (
        not commands and any(g.startswith("lib/") for g in globs)
    ):
        return QuickStartProfile(kind="library")
    if commands:
        return QuickStartProfile(kind="tool", commands=commands)
    if globs:
        return QuickStartProfile(kind="files", paths=_top_paths(globs))
    # No package.files at all: nothing to say beyond what the name implies.
    return guess
