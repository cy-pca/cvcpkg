# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Keep cvcpkg credentials out of the environment of the programs cvcpkg runs.

A builder runs third-party code -- every recipe's build and test scripts, and
whatever they fetch -- with the builder's own environment.  Anything in that
environment is readable by the build (``env``, ``/proc/self/environ``) and
routinely ends up in a build log, which the builder streams to the server.  A
bearer token there is a token handed to every recipe the builder builds:

* ``cvcpkg builder run`` reads its token from ``CVCPKG_TOKEN`` (or an env
  file, which loads into the environment), so without a scrub every build
  script it starts inherits the publisher token it runs under.
* ``cvcpkg builder fleet`` resolves each server's token from a ``token_env``
  variable (``CVCPKG_TOKEN_PROD``, ``CVCPKG_TOKEN_DEV`` ...).  Its workers used
  to inherit the supervisor's whole environment, so a recipe built for one
  server could read every other server's token too.

:func:`scrub_token_env` removes them.  What counts as a credential:

* any variable named ``CVCPKG_...TOKEN...`` (``CVCPKG_TOKEN``,
  ``CVCPKG_TOKEN_PROD``, ``CVCPKG_ADMIN_TOKEN``, ``CVCPKG_SERVER_CACHE_TOKEN``
  ...) -- nothing a recipe legitimately needs is named that way;
* any variable the caller names (a fleet's ``token_env`` names, which can be
  anything);
* any variable whose *value* is one of the caller's secrets, whatever it is
  called.

Third-party credentials a recipe may genuinely use (``GITHUB_TOKEN`` for an
API rate limit, say) are deliberately left alone: they are not cvcpkg's to
withhold.

This is hygiene, not a sandbox.  A build script runs as the builder's uid and
can read the builder's own ``/proc/<pid>/environ`` and memory; isolation
between servers or orgs that do not trust each other needs a separate uid per
worker (one service unit each).
"""

from __future__ import annotations

from collections.abc import Iterable, MutableMapping

# Set by ``cvcpkg builder fleet`` on each worker: the names of the token
# variables the worker must drop from its own environment at startup (after the
# root group has loaded env files, which may define them again).
SCRUB_NAMES_ENV = "CVCPKG_BUILDER_SCRUB_ENV"


def is_token_env_name(name: str) -> bool:
    """True for a variable name cvcpkg treats as one of its credentials."""
    upper = name.upper()
    return upper.startswith("CVCPKG_") and "TOKEN" in upper


def split_names(raw: str | None) -> list[str]:
    """Parse a :data:`SCRUB_NAMES_ENV` value (comma-separated names)."""
    return [n.strip() for n in (raw or "").split(",") if n.strip()]


def scrub_token_env(
    env: MutableMapping[str, str],
    *,
    names: Iterable[str] = (),
    secrets: Iterable[str] = (),
) -> dict[str, str]:
    """Remove cvcpkg credentials from *env* in place; return what was removed.

    Removes every ``CVCPKG_...TOKEN...`` variable, every variable in *names*,
    and every variable whose value is one of *secrets* (empty secrets are
    ignored -- they would match every empty variable).  Works on
    ``os.environ`` as well as on a plain dict.
    """
    wanted = {n.upper() for n in names if n}
    values = {s for s in secrets if s}
    removed: dict[str, str] = {}
    for key in list(env):
        value = env.get(key, "")
        if is_token_env_name(key) or key.upper() in wanted or (values and value in values):
            removed[key] = value
            del env[key]
    return removed
