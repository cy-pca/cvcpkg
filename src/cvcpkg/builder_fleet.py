"""Multi-homed builder fleet configuration.

Implements the *multi-server* half of the roadmap's "Multi-tenant / shared
builder fleet" item: one physical machine registers with and polls MORE THAN
ONE cvcpkg server, so the previously separate dev and prod fleets collapse into
a single machine driven by one config file and one service unit.

Rather than rewrite the (thread-heavy, single-server) ``cvcpkg builder run``
agent to juggle N servers in-process, a *supervisor* runs one single-server
worker per configured server. That keeps the proven agent untouched and gives
**secret isolation for free**: each worker process holds only its own server's
token and never sees another server/org's credentials or build outputs.

Config schema (``fleet.yaml``)::

    name: catx-03            # optional; per-server builder name defaults to
                             # "<name>-<server-host>"
    max_jobs: 4              # optional default, overridable per server
    work_dir: /var/lib/cvcpkg-builder   # optional base; each worker gets a
                                        # per-server subdirectory
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
    token: str
    serve: tuple[str, ...]
    name: str
    max_jobs: int = 1
    work_dir: str | None = None
    labels: tuple[str, ...] = ()
    platform: str | None = None
    arch: str | None = None

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


def _resolve_token(entry: dict, where: str) -> str:
    """Resolve a server's token from a literal ``token`` or a ``token_env``.

    ``token_env`` is preferred in practice so the secret stays out of the file;
    a literal ``token`` is accepted for convenience. Exactly one must yield a
    non-empty value.
    """
    literal = str(entry.get("token", "") or "")
    env_name = str(entry.get("token_env", "") or "")
    if literal:
        return literal
    if env_name:
        val = os.environ.get(env_name, "")
        if not val:
            raise FleetConfigError(
                f"{where}: token_env {env_name!r} is set but the environment "
                f"variable is empty or undefined"
            )
        return val
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
        token = _resolve_token(entry, where)
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


def worker_argv(fs: FleetServer) -> list[str]:
    """Build the ``cvcpkg builder run`` argv for a single-server worker.

    The served set maps to ``--org <serve[0]> --serve <rest…>`` — the agent
    always serves its ``--org`` and unions in each ``--serve``, so the worker
    reconstructs exactly ``fs.serve`` on the server.
    """
    serve = fs.serve or ("",)
    home, extras = serve[0], serve[1:]
    argv = [
        "builder",
        "run",
        "--server",
        fs.server,
        "--token",
        fs.token,
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
    if fs.platform:
        argv += ["--platform", fs.platform]
    if fs.arch:
        argv += ["--arch", fs.arch]
    if fs.work_dir:
        argv += ["--work-dir", fs.work_dir]
        argv += ["--pidfile", str(Path(fs.work_dir) / "cvcpkg-builder.pid")]
    return argv
