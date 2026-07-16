"""``cvcpkg cpkg`` — resolve cvcpkg prebuilt binaries for cpkg.lua build scripts.

Bridges cvcpkg to `cpkg <https://getcpkg.net/>`_ (a Lua + Ninja C/C++ project
tool).  ``cvcpkg cpkg deps <name>`` installs a pinned prebuilt binary from the
cvcpkg archive into a project-local prefix and prints a machine-readable
description of it (a Lua table by default, or JSON) that the companion
``integrations/cpkg/cvcpkg.lua`` shim wires into cpkg's ``add_dependency()``.

Design: installation reuses ``cvcpkg install`` verbatim (resolution, download,
signature checks, mirrors) via a subprocess, so there is no duplicated install
logic here — this command only adds the prefix scan + serialisation.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from cvcpkg import cpkg as _cpkg
from cvcpkg.cli import cli


@cli.group("cpkg")
def cpkg_group() -> None:
    """Integration with the cpkg (getcpkg.net) C/C++ project tool."""


@cpkg_group.command("deps")
@click.argument("components", nargs=-1)
@click.option(
    "--prefix",
    required=True,
    type=click.Path(),
    help="Project-local directory to install the dependencies into.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["lua", "json"]),
    default="lua",
    help="Output format the cpkg.lua shim (or another consumer) parses.",
)
@click.option("--release", default=None, help="Install from a specific cvcpkg release tag.")
@click.option("--arch", default=None, help="Override the target architecture.")
@click.option(
    "--server",
    envvar="CVCPKG_SERVER_URL",
    default=None,
    help="cvcpkg-server URL.  [env: CVCPKG_SERVER_URL]",
)
@click.option(
    "--token",
    envvar="CVCPKG_TOKEN",
    default=None,
    help="Bearer token for private/org packages.  [env: CVCPKG_TOKEN]",
)
@click.option(
    "--require-signatures",
    is_flag=True,
    default=False,
    help="Fail unless every installed archive carries a valid signature.",
)
@click.option(
    "--no-install",
    is_flag=True,
    default=False,
    help="Skip installation and only scan an already-populated prefix.",
)
def cpkg_deps(
    components: tuple[str, ...],
    prefix: str,
    fmt: str,
    release: str | None,
    arch: str | None,
    server: str | None,
    token: str | None,
    require_signatures: bool,
    no_install: bool,
) -> None:
    """Install COMPONENTS into --prefix and emit a cpkg-consumable description.

    Examples:

      # In a cpkg.lua workflow (usually called via cvcpkg.lua):
      cvcpkg cpkg deps boost hdf5 --prefix ./cvcpkg_deps

      # Just describe an existing prefix, no install:
      cvcpkg cpkg deps --prefix ./cvcpkg_deps --no-install
    """
    prefix_path = Path(prefix)

    if not no_install:
        if not components:
            raise click.ClickException("no components given (use --no-install to scan only)")
        cmd = [sys.executable, "-m", "cvcpkg", "install", *components, "--prefix", str(prefix_path)]
        if release:
            cmd += ["--release", release]
        if arch:
            cmd += ["--arch", arch]
        if server:
            cmd += ["--server", server]
        if token:
            cmd += ["--token", token]
        if require_signatures:
            cmd += ["--require-signatures"]
        # Let install's own progress/errors go to stderr so stdout stays a
        # clean, parseable Lua/JSON document for the cpkg.lua shim.
        result = subprocess.run(cmd, stdout=sys.stderr.fileno())
        if result.returncode != 0:
            raise click.ClickException(
                f"cvcpkg install failed (exit {result.returncode}) for: {', '.join(components)}"
            )

    if not prefix_path.is_dir():
        raise click.ClickException(f"prefix does not exist: {prefix_path}")

    info = _cpkg.scan_prefix(prefix_path)
    out = _cpkg.to_lua(info) if fmt == "lua" else _cpkg.to_json(info)
    click.echo(out, nl=False)
