# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Guarded imports for cvcpkg's optional dependencies.

cvcpkg's *core* install is deliberately two distributions — ``click`` and
``PyYAML`` — because that is the whole client.  Traced at run time, ``cvcpkg
install`` (fetch catalog, resolve, download, verify sha256, extract, write
lockfile/CMake config/activation scripts), ``cvcpkg build`` and the recipe
tooling load nothing else: HTTP on the install path is ``urllib.request`` in
:mod:`cvcpkg.storage`, and integrity is ``hashlib.sha256``.

Everything heavier belongs to a *role* rather than to the client:

* ``httpx`` — talking to a cvcpkg-server's HTTP API (publish, recipe
  push/pull, builds, search, webhooks, admin, the builder agent).
* ``cryptography`` — Ed25519 signing and signature verification, which is
  opt-in (``--verify-signatures`` / ``--require-signatures``).
* ``sqlalchemy`` (with ``greenlet``) — the server's database backend.
* ``jsonschema`` — ``cvcpkg validate``, which checks a recipe against the
  bundled JSON-Schemas; nothing on the install path reads them.

Each of those lives behind an extra, so a plain ``pip install cvcpkg``
succeeds on platforms where the heavy wheels do not exist.  Haiku is the
worked example: its ports tree has click and pyyaml, but pins
``cryptography`` at 3.4.8 against a ``>=41`` floor, ships ``sqlalchemy``
1.3.24 against a ``^2.0`` floor, and has no ``greenlet`` or ``httpx`` port
at all — so four mandatory distributions the client never imports were what
stopped the install, not the code.

Every one of those imports is already lazy (function-local, or a module
imported inside a command), so a missing extra can only ever be discovered
at the moment a command actually needs it.  This module turns that moment
into the message shape ``cvcpkg-server run`` has always used for uvicorn::

    <dist> is required <purpose>. Install it with: pip install 'cvcpkg[<extra>]'

instead of a ``ModuleNotFoundError`` traceback.
"""

from __future__ import annotations

from types import ModuleType

import click

from cvcpkg.errors import CvcpkgError


class MissingDependencyError(CvcpkgError, click.ClickException):
    """An optional dependency is not installed for the command being run.

    Deliberately both a :class:`~cvcpkg.errors.CvcpkgError` and a
    :class:`click.ClickException`: programmatic callers already catch the
    former, and the latter is what makes Click print one clean ``Error: …``
    line and exit 1 instead of dumping a traceback — the same treatment
    ``cvcpkg-server run`` gives a missing uvicorn.
    """

    def __init__(self, dist: str, purpose: str, extra: str) -> None:
        # ClickException owns .message and .show(); initialize through it.
        click.ClickException.__init__(
            self,
            f"{dist} is required {purpose}. Install it with: pip install 'cvcpkg[{extra}]'",
        )
        self.dist = dist
        self.extra = extra


def require_httpx(extra: str = "remote") -> ModuleType:
    """Return the :mod:`httpx` module, or raise :class:`MissingDependencyError`.

    Call sites replace a bare ``import httpx`` with ``httpx = require_httpx()``
    so the rest of the function is untouched.

    *extra* names the extra to suggest — the one whose entry point the caller
    belongs to (``publish`` for the publishing commands, ``builder`` for the
    agent, ``remote`` for everything else that speaks to a registry).  All of
    them resolve to the same wheel; naming the caller's own role just means a
    publisher is told to install ``cvcpkg[publish]`` rather than something
    that sounds like it belongs to somebody else.
    """
    try:
        import httpx
    except ImportError:
        raise MissingDependencyError(
            "httpx", "to talk to a cvcpkg server over HTTP", extra
        ) from None
    return httpx


def require_jsonschema() -> ModuleType:
    """Return the :mod:`jsonschema` module, or raise :class:`MissingDependencyError`.

    ``cvcpkg validate`` is the one piece of the recipe tooling that is not
    click + PyYAML: it checks ``recipe.yaml`` / ``components.yaml`` against the
    bundled Draft 2020-12 schemas.  Installing a package never validates one,
    so jsonschema stays out of the core and behind ``[validate]`` — but
    ``cvcpkg init`` ends by telling you to run ``cvcpkg validate
    recipes/NAME``, so the command has to answer with the extra to install
    rather than a ``ModuleNotFoundError`` traceback.
    """
    try:
        import jsonschema
    except ImportError:
        raise MissingDependencyError(
            "jsonschema", "to validate recipes against their schema", "validate"
        ) from None
    return jsonschema


def require_cryptography(purpose: str = "for package signing") -> None:
    """Raise :class:`MissingDependencyError` unless ``cryptography`` is present.

    A guard rather than an accessor: :mod:`cvcpkg.signing` imports specific
    names out of ``cryptography.hazmat`` in a dozen places, so the useful
    thing to centralize is the *check* and its message.
    """
    try:
        import cryptography  # noqa: F401
    except ImportError:
        raise MissingDependencyError("cryptography", purpose, "signing") from None


def require_sqlalchemy() -> None:
    """Raise :class:`MissingDependencyError` unless ``sqlalchemy`` is present.

    Called at the top of the server's database modules, whose SQLAlchemy
    imports are module-level: without this, ``cvcpkg-server token create
    --database-url …`` on a core install dies with a bare
    ``ModuleNotFoundError: No module named 'sqlalchemy'``.
    """
    try:
        import sqlalchemy  # noqa: F401
    except ImportError:
        raise MissingDependencyError(
            "sqlalchemy", "for the cvcpkg-server database backend", "server"
        ) from None


def require_pydantic() -> None:
    """Raise :class:`MissingDependencyError` unless ``pydantic`` is present.

    Companion to :func:`require_sqlalchemy` for :mod:`cvcpkg.server.models`,
    whose ``from pydantic import BaseModel, Field`` is module-level.  That
    module is pulled in by every state-directory command that never touches a
    database — ``cvcpkg-server bootstrap``, ``token create``, ``audit log`` and
    ``audit verify`` all import it for the ``TokenRole`` / ``AuditAction``
    enums — so without this they are the one server path that still ends in a
    bare ``ModuleNotFoundError: No module named 'pydantic'``.
    """
    try:
        import pydantic  # noqa: F401
    except ImportError:
        raise MissingDependencyError(
            "pydantic", "for the cvcpkg-server API models", "server"
        ) from None
