"""Cross-server (federated) dependency resolution.

A dependency may name a federated registry host (see :mod:`cvcpkg.refs`).  When
the resolver meets such a dependency it fetches that package's metadata — and
later its archive — from the registry the local ``registries.yaml`` maps the
host to, using that registry's token.  A host that is not configured is refused
(the config is the trust allowlist), so a recipe can never steer the resolver at
an arbitrary host.

The default rule for a *remote* package's own dependencies: they resolve against
**that remote's** registry unless they carry their own ``server`` — each edge is
self-contained (its public deps arrive there via populate), and only explicit
``cvc://`` references cross a boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cvcpkg.config import Registry, registry_for
from cvcpkg.refs import DepRef, parse_dep_ref


class FederationError(RuntimeError):
    """A federated dependency could not be resolved (un-allowlisted host, etc.)."""


@dataclass
class ResolvedNode:
    """One node of a resolved federated dependency closure."""

    ref: DepRef
    server: str  # host the node was resolved on ("" = local)
    base_url: str  # concrete registry base URL used
    token: str = ""  # credential used (redacted in repr)
    bundles: list = field(default_factory=list)

    def __repr__(self) -> str:  # never leak the token
        return (
            f"ResolvedNode(name={self.ref.name!r}, org={self.ref.org!r}, "
            f"server={self.server!r}, base_url={self.base_url!r}, "
            f"bundles={len(self.bundles)})"
        )


def _default_http_get(url: str, token: str, timeout: float = 30.0) -> dict:
    import httpx

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def _registry_for(host: str, config_dir: Path | None) -> Registry:
    reg = registry_for(host, config_dir)
    if reg is None:
        raise FederationError(
            f"dependency references registry host {host!r}, which is not in the "
            f"federation allowlist; add it to registries.yaml (host -> url + token)"
        )
    return reg


def resolve_federated(
    root: object,
    *,
    local_url: str,
    local_token: str = "",
    config_dir: Path | None = None,
    http_get=_default_http_get,
) -> list[ResolvedNode]:
    """Resolve the transitive dependency closure of *root* across registries.

    Returns nodes in dependency order (dependencies before dependents).  *root*
    may be a str / dict / DepRef.  ``local_url``/``local_token`` are used for
    unqualified nodes; a node with a ``server`` is fetched from its allowlisted
    registry.  A remote node's own deps default to the remote's registry.
    """
    root_ref = parse_dep_ref(root)
    seen: dict[tuple[str, str, str], ResolvedNode] = {}
    order: list[ResolvedNode] = []

    def visit(ref: DepRef, parent_server: str) -> None:
        # A remote package's bare deps resolve on the remote; an explicit
        # server on the dep always wins.
        server = ref.server or parent_server
        key = (server, ref.org, ref.name)
        if key in seen:
            return

        if server:
            reg = _registry_for(server, config_dir)
            base_url, token = reg.url, reg.token
        else:
            base_url, token = local_url.rstrip("/"), local_token

        url = f"{base_url}/v1/packages/{ref.name}"
        if ref.org:
            url += f"?org={ref.org}"
        data = http_get(url, token)
        bundles = data.get("packages") or data.get("bundles") or []

        node = ResolvedNode(ref=ref, server=server, base_url=base_url, token=token, bundles=bundles)
        seen[key] = node

        required: list = []
        if bundles:
            required = bundles[0].get("required_deps") or []
        for d in required:
            try:
                child = parse_dep_ref(d)
            except ValueError:
                continue
            visit(child, server)

        order.append(node)  # post-order: deps land before dependents

    visit(root_ref, "")
    return order
