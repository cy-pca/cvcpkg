# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Multi-homed builder fleet configuration.

Implements the *multi-server* half of the roadmap's "Multi-tenant / shared
builder fleet" item: one physical machine registers with and polls MORE THAN
ONE cvcpkg server, so the previously separate dev and prod fleets collapse into
a single machine driven by one config file and one service unit.

Rather than rewrite the (thread-heavy, single-server) ``cvcpkg builder run``
agent to juggle N servers in-process, a *supervisor* runs one single-server
worker per configured server. That keeps the proven agent untouched.

Tokens: each worker is handed only its own server's token, as ``CVCPKG_TOKEN``
in its environment (see :func:`worker_env`) -- never on its command line, where
``ps`` and ``/proc/<pid>/cmdline`` show it to every local user.  The other
servers' token variables are removed from the worker's environment, and the
worker removes its own from the environment its build scripts inherit (see
``cvcpkg.tokenenv``), so a recipe built for one server cannot read another
server's token from its environment.  That is hygiene, not a security
boundary: every worker runs as the supervisor's uid, and a build script can
read any same-uid process's environment and memory.  Servers or orgs that must
not be able to reach each other's credentials need one uid per worker (a
separate ``cvcpkg builder run`` service per server), not one fleet.

Config schema (``fleet.yaml``)::

    name: catx-03            # optional; per-server builder name defaults to
                             # "<name>-<server-host>"
    max_jobs: 4              # optional default, overridable per server
    work_dir: /var/lib/cvcpkg-builder   # optional base; each worker gets a
                                        # per-server subdirectory
    capabilities: [cuda]     # optional; host capabilities to advertise
                             # (merged with the worker's auto-detected set;
                             # see auto_capabilities), overridable per server
    cross_platforms: [haiku] # optional; extra target platforms this host can
                             # serve by delegating over SSH (haiku via
                             # cvcpkg.haikuhost) -> --cross-platform; per-server
    auto_capabilities: true  # optional; false passes --no-auto-capabilities
    advertise_free_disk: true  # optional; false passes --no-free-disk, so the
                               # scheduler treats this host's work-volume
                               # capacity as unknown
    servers:
      - server: https://cvcpkg.org
        token_env: CVCPKG_TOKEN_PROD    # or `token: <literal>`
        serve: ["", "cvc"]              # served namespaces ("" = public)
      - server: https://pkg.tx.wtf
        token_env: CVCPKG_TOKEN_DEV
        serve: ["", "cvc"]
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml


class FleetConfigError(ValueError):
    """Raised for a malformed or incomplete fleet config."""


@dataclass(frozen=True)
class FleetServer:
    """One (server, credentials, served-namespaces) target in a fleet."""

    server: str
    # Never in repr(): a FleetServer in a log line or a traceback must not
    # print the credential.
    token: str = field(repr=False)
    serve: tuple[str, ...]
    name: str
    max_jobs: int = 1
    work_dir: str | None = None
    labels: tuple[str, ...] = ()
    platform: str | None = None
    arch: str | None = None
    capabilities: tuple[str, ...] = ()
    # Extra target platforms this worker can serve by delegating over SSH
    # (e.g. ``haiku`` via cvcpkg.haikuhost) — advertised as ``--cross-platform``.
    cross_platforms: tuple[str, ...] = ()
    auto_capabilities: bool = True
    advertise_free_disk: bool = True
    # The environment variable the token was read from (``token_env``), or ""
    # for a literal ``token``.  The supervisor drops every server's variable
    # from every worker's environment; see worker_env.
    token_env: str = ""

    @property
    def host(self) -> str:
        return urlparse(self.server).hostname or self.server


@dataclass(frozen=True)
class FleetConfig:
    name: str
    servers: list[FleetServer] = field(default_factory=list)


def _slug_host(url: str) -> str:
    """A filesystem/name-safe slug for a server host (for per-server names)."""
    host = urlparse(url).hostname or url
    return "".join(c if (c.isalnum() or c in "-_") else "-" for c in host)


def _resolve_token(entry: dict, where: str) -> tuple[str, str]:
    """Resolve a server's token from a literal ``token`` or a ``token_env``.

    ``token_env`` is preferred in practice so the secret stays out of the file;
    a literal ``token`` is accepted for convenience. Exactly one must yield a
    non-empty value.  Returns ``(token, token_env)``; ``token_env`` is the
    variable's name, or "" for a literal.
    """
    literal = str(entry.get("token", "") or "")
    env_name = str(entry.get("token_env", "") or "")
    if literal:
        return literal, ""
    if env_name:
        val = os.environ.get(env_name, "")
        if not val:
            raise FleetConfigError(
                f"{where}: token_env {env_name!r} is set but the environment "
                f"variable is empty or undefined"
            )
        return val, env_name
    raise FleetConfigError(f"{where}: each server needs a 'token' or 'token_env'")


def _normalize_serve(raw) -> tuple[str, ...]:
    """Normalize the served set to a de-duplicated, order-stable tuple.

    Accepts a list of strings (``""`` = public). Defaults to public-only.
    """
    if raw is None:
        return ("",)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise FleetConfigError("'serve' must be a list of namespace strings")
    out: list[str] = []
    for ns in raw:
        s = "" if ns is None else str(ns)
        if s not in out:
            out.append(s)
    return tuple(out) or ("",)


def parse_fleet_config(data: dict) -> FleetConfig:
    """Parse an already-loaded fleet-config mapping into a FleetConfig."""
    if not isinstance(data, dict):
        raise FleetConfigError("fleet config must be a mapping")
    fleet_name = str(data.get("name", "") or "").strip()
    if not fleet_name:
        fleet_name = os.uname().nodename if hasattr(os, "uname") else "builder"
    default_max_jobs = int(data.get("max_jobs", 1) or 1)
    base_work_dir = data.get("work_dir")
    default_labels = tuple(str(x) for x in (data.get("labels") or []))
    default_capabilities = tuple(str(x) for x in (data.get("capabilities") or []))
    default_cross_platforms = tuple(str(x) for x in (data.get("cross_platforms") or []))
    default_auto_caps = bool(data.get("auto_capabilities", True))
    default_free_disk = bool(data.get("advertise_free_disk", True))

    servers_raw = data.get("servers")
    if not isinstance(servers_raw, list) or not servers_raw:
        raise FleetConfigError("fleet config needs a non-empty 'servers' list")

    seen_servers: set[str] = set()
    servers: list[FleetServer] = []
    for i, entry in enumerate(servers_raw):
        where = f"servers[{i}]"
        if not isinstance(entry, dict):
            raise FleetConfigError(f"{where}: must be a mapping")
        server = str(entry.get("server", "") or "").rstrip("/")
        if not server:
            raise FleetConfigError(f"{where}: missing 'server' URL")
        if server in seen_servers:
            raise FleetConfigError(f"{where}: duplicate server {server!r}")
        seen_servers.add(server)
        token, token_env = _resolve_token(entry, where)
        serve = _normalize_serve(entry.get("serve"))
        name = str(entry.get("name", "") or "")
        if not name:
            base = fleet_name or "builder"
            name = f"{base}-{_slug_host(server)}"
        work_dir = entry.get("work_dir")
        if work_dir is None and base_work_dir:
            work_dir = str(Path(base_work_dir) / _slug_host(server))
        servers.append(
            FleetServer(
                server=server,
                token=token,
                serve=serve,
                name=name,
                max_jobs=int(entry.get("max_jobs", default_max_jobs) or default_max_jobs),
                work_dir=str(work_dir) if work_dir else None,
                labels=tuple(str(x) for x in (entry.get("labels") or default_labels)),
                platform=(str(entry["platform"]) if entry.get("platform") else None),
                arch=(str(entry["arch"]) if entry.get("arch") else None),
                capabilities=tuple(
                    str(x) for x in (entry.get("capabilities") or default_capabilities)
                ),
                cross_platforms=tuple(
                    str(x) for x in (entry.get("cross_platforms") or default_cross_platforms)
                ),
                auto_capabilities=bool(entry.get("auto_capabilities", default_auto_caps)),
                advertise_free_disk=bool(entry.get("advertise_free_disk", default_free_disk)),
                token_env=token_env,
            )
        )
    return FleetConfig(name=fleet_name, servers=servers)


def load_fleet_config(path: str | Path) -> FleetConfig:
    """Load and validate a fleet config from a YAML file."""
    p = Path(path)
    if not p.is_file():
        raise FleetConfigError(f"fleet config not found: {p}")
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as exc:
        raise FleetConfigError(f"{p}: invalid YAML: {exc}") from exc
    return parse_fleet_config(data)


def worker_env(
    fs: FleetServer,
    servers: list[FleetServer] | tuple[FleetServer, ...],
    base: Mapping[str, str],
) -> dict[str, str]:
    """The environment for *fs*'s worker: *base* with only *fs*'s token in it.

    Starting from *base* (the supervisor's environment, as ``cvcpkg_env()``
    returns it), removes every server's ``token_env`` variable, every
    ``CVCPKG_...TOKEN...`` variable and any variable holding any fleet token,
    then sets ``CVCPKG_TOKEN`` -- which ``builder run --token`` reads -- to
    *fs*'s token.  The token never goes on the worker's argv, where any local
    user can read it in ``ps`` / ``/proc/<pid>/cmdline``.

    The worker also gets the ``token_env`` names in
    :data:`cvcpkg.tokenenv.SCRUB_NAMES_ENV`: it loads the default env files at
    startup (``/etc/cvcpkg/env`` ...), which may define them again, and drops
    them -- and its own ``CVCPKG_TOKEN`` -- from the environment its build
    scripts inherit (``builder run``).
    """
    from cvcpkg.tokenenv import SCRUB_NAMES_ENV, scrub_token_env

    names = sorted({s.token_env for s in servers if s.token_env})
    env = dict(base)
    scrub_token_env(env, names=names, secrets=[s.token for s in servers])
    env["CVCPKG_TOKEN"] = fs.token
    if names:
        env[SCRUB_NAMES_ENV] = ",".join(names)
    else:
        env.pop(SCRUB_NAMES_ENV, None)
    return env


def worker_argv(fs: FleetServer) -> list[str]:
    """Build the ``cvcpkg builder run`` argv for a single-server worker.

    The served set maps to ``--org <serve[0]> --serve <rest…>`` — the agent
    always serves its ``--org`` and unions in each ``--serve``, so the worker
    reconstructs exactly ``fs.serve`` on the server.

    No ``--token``: the worker reads its token from ``CVCPKG_TOKEN``, which
    :func:`worker_env` sets.
    """
    serve = fs.serve or ("",)
    home, extras = serve[0], serve[1:]
    argv = [
        "builder",
        "run",
        "--server",
        fs.server,
        "--name",
        fs.name,
        "--org",
        home,
        "--max-jobs",
        str(fs.max_jobs),
    ]
    for ns in extras:
        argv += ["--serve", ns]
    for label in fs.labels:
        argv += ["--label", label]
    for cap in fs.capabilities:
        argv += ["--capability", cap]
    for cp in fs.cross_platforms:
        argv += ["--cross-platform", cp]
    if not fs.auto_capabilities:
        argv += ["--no-auto-capabilities"]
    if not fs.advertise_free_disk:
        argv += ["--no-free-disk"]
    if fs.platform:
        argv += ["--platform", fs.platform]
    if fs.arch:
        argv += ["--arch", fs.arch]
    if fs.work_dir:
        argv += ["--work-dir", fs.work_dir]
        argv += ["--pidfile", str(Path(fs.work_dir) / "cvcpkg-builder.pid")]
    return argv
