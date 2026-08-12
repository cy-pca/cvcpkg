# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Load cvcpkg settings — chiefly secrets — from an env file.

``--token`` is accepted by 63 options across the CLI, and every one of them puts
the secret in ``argv``.  On Linux ``/proc/<pid>/cmdline`` is world-readable, so
any local user can read a builder's bearer token straight out of ``ps``; Task
Manager's command-line column does the same on Windows.  Exporting
``CVCPKG_TOKEN`` avoids argv but only moves the problem: the value still has to
be written somewhere to get there, which in practice meant a plaintext literal
in the launcher script (the fleet's ``start-builder.bat`` carried a live
publisher token inline for exactly this reason).

An env file gives the secret a home with its own permissions.  cvcpkg reads it
before Click resolves any ``envvar=``, so **every** existing ``--token`` /
``CVCPKG_TOKEN`` option is served by it without changing a single command.

Precedence, most specific first::

    --token on the command line     (still works; still visible in ps)
    CVCPKG_TOKEN already exported   (a real env var beats a file)
    the env file
    (option default / error if required)

That order is the dotenv convention and the one that keeps this change
backward-compatible: a file can only *supply* a value nobody else set, so no
existing deployment changes behaviour by adding one.  ``override=True`` inverts
it for the rare caller that wants the file to win.

Search order when no ``--env-file`` is given (first file to define a key wins,
and none of them override a real environment variable)::

    $CVCPKG_ENV_FILE                     explicit, wins over the rest
    ./.cvcpkg.env                        project-local
    ~/.config/cvcpkg/env                 per-user   ($XDG_CONFIG_HOME honoured)
    /etc/cvcpkg/env                      system-wide (POSIX)
    %APPDATA%\\cvcpkg\\env                 per-user   (Windows)
    %PROGRAMDATA%\\cvcpkg\\env             system-wide (Windows)

The format is deliberately dumb — ``KEY=VALUE``, one per line, ``#`` comments,
optional ``export`` prefix, optional quoting.  There is **no** shell expansion,
no ``$VAR`` interpolation and no command substitution: a file whose whole job is
to hold credentials should not be able to execute anything, and a token
containing ``$`` must survive verbatim.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = [
    "EnvFileError",
    "candidate_env_files",
    "load_env_file",
    "load_default_env_files",
    "parse_env_file",
]


class EnvFileError(ValueError):
    """Raised for a malformed env file."""


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}


def _unquote(raw: str, path: str, lineno: int) -> str:
    """Strip surrounding quotes from a value and resolve escapes.

    Double quotes resolve ``\\n``/``\\t``/``\\r``/``\\\\``/``\\"``; single quotes are
    literal throughout (so a token full of backslashes needs no thought).  An
    unquoted value is taken verbatim up to an unquoted ``#`` comment.
    """
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        body = raw[1:-1]
        if raw[0] == "'":
            return body
        out: list[str] = []
        i = 0
        while i < len(body):
            ch = body[i]
            if ch == "\\" and i + 1 < len(body):
                nxt = body[i + 1]
                if nxt in _ESCAPES:
                    out.append(_ESCAPES[nxt])
                    i += 2
                    continue
            out.append(ch)
            i += 1
        return "".join(out)
    if raw[:1] in ("'", '"'):
        raise EnvFileError(f"{path}:{lineno}: unterminated quote")
    # Unquoted: a ' #' starts a trailing comment.  A '#' with no preceding
    # space is kept, so tokens like 'abc#def' survive.
    cut = raw.find(" #")
    if cut != -1:
        raw = raw[:cut]
    return raw.strip()


def parse_env_file(text: str, *, path: str = "<env>") -> dict[str, str]:
    """Parse env-file *text* into an ordered mapping.

    Ignores blank lines and ``#`` comments; accepts an ``export`` prefix so the
    same file can be ``source``d by a shell.  A later assignment to the same key
    wins, matching how a shell would read it top to bottom.
    """
    out: dict[str, str] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export ") or stripped.startswith("export\t"):
            stripped = stripped[len("export") :].lstrip()
        key, sep, raw = stripped.partition("=")
        if not sep:
            raise EnvFileError(f"{path}:{lineno}: expected KEY=VALUE, got {line.strip()!r}")
        key = key.strip()
        if not key or not (key[0].isalpha() or key[0] == "_"):
            raise EnvFileError(f"{path}:{lineno}: invalid variable name {key!r}")
        if not all(c.isalnum() or c == "_" for c in key):
            raise EnvFileError(f"{path}:{lineno}: invalid variable name {key!r}")
        out[key] = _unquote(raw.strip(), path, lineno)
    return out


def _warn_if_world_readable(path: Path) -> None:
    """Warn when a secrets file is readable beyond its owner.

    Advisory only — ssh refuses to run in this situation, but cvcpkg is often
    driven from CI images and shared builders where tightening the mode is not
    the operator's call at that moment.  Failing closed would strand exactly the
    hosts this feature is meant to help.
    """
    if os.name == "nt":
        return  # POSIX bits are not meaningful on NTFS ACLs
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & 0o077:
        print(
            f"cvcpkg: WARNING: {path} is readable by group/other (mode "
            f"{mode & 0o777:04o}); it holds secrets — chmod 600 it",
            file=sys.stderr,
        )


def load_env_file(
    path: str | os.PathLike[str],
    *,
    override: bool = False,
    required: bool = True,
) -> list[str]:
    """Load *path* into :data:`os.environ`; return the names actually applied.

    With *override* false (the default) an existing environment variable is left
    alone, so a file never silently displaces something the operator exported or
    passed on the command line.  A missing file raises unless *required* is
    false, which is what the default-search path uses.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        if required:
            raise EnvFileError(f"env file not found: {p}") from None
        return []
    except OSError as e:
        raise EnvFileError(f"cannot read env file {p}: {e}") from None

    _warn_if_world_readable(p)
    applied: list[str] = []
    for key, value in parse_env_file(text, path=str(p)).items():
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        applied.append(key)
    return applied


def candidate_env_files() -> list[Path]:
    """Default env-file locations, most specific first.

    Only ``$CVCPKG_ENV_FILE`` is guaranteed to exist if named; the rest are
    probed and skipped when absent.
    """
    out: list[Path] = []
    explicit = os.environ.get("CVCPKG_ENV_FILE", "").strip()
    if explicit:
        out.append(Path(explicit))
    out.append(Path.cwd() / ".cvcpkg.env")
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    out.append((Path(xdg) if xdg else Path.home() / ".config") / "cvcpkg" / "env")
    if os.name == "nt":
        for var in ("APPDATA", "PROGRAMDATA"):
            base = os.environ.get(var, "").strip()
            if base:
                out.append(Path(base) / "cvcpkg" / "env")
    else:
        out.append(Path("/etc/cvcpkg/env"))
    return out


def load_default_env_files(*, override: bool = False) -> list[str]:
    """Load every default env file that exists; return the names applied.

    Earlier (more specific) files win: each is loaded with the *previous* files'
    keys already in the environment, so a project-local ``.cvcpkg.env`` shadows
    ``/etc/cvcpkg/env`` without either having to know about the other.  An
    unreadable or malformed file is reported and skipped rather than killing an
    unrelated command — the value it would have supplied is simply absent, and
    the option that needed it fails with its own (clearer) error.

    Returns variable names rather than paths so the caller can undo exactly what
    was added (see the root group's ``--env-file`` callback).
    """
    applied: list[str] = []
    for candidate in candidate_env_files():
        try:
            if not candidate.is_file():
                continue
            applied.extend(load_env_file(candidate, override=override, required=False))
        except (EnvFileError, OSError) as e:
            print(f"cvcpkg: WARNING: ignoring env file: {e}", file=sys.stderr)
            continue
    return applied
