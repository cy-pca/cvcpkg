"""``cvcpkg init`` — scaffold a new recipe from a template.

Creates ``recipes/<name>/`` with a schema-valid ``recipe.yaml`` and the
matching build script(s) for a chosen build system (cmake, meson, or
autotools), giving recipe authors a working starting point.
"""

from __future__ import annotations

from pathlib import Path

import click

from cvcpkg.cli import cli

# ── Build-script templates ──────────────────────────────────────

_BUILD_SH_CMAKE = """\
#!/usr/bin/env bash
# recipes/{name}/build.sh — build {name} on Linux/macOS/BSD with CMake.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${{SCRIPT_DIR}}/../_common/env-${{CVC_PLATFORM}}.sh"

# Configure, build, and install with CMake.  Pass extra -D flags as needed.
cvc_cmake_build
"""

_BUILD_SH_MESON = """\
#!/usr/bin/env bash
# recipes/{name}/build.sh — build {name} on Linux/macOS/BSD with Meson.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${{SCRIPT_DIR}}/../_common/env-${{CVC_PLATFORM}}.sh"

export PKG_CONFIG_PATH="${{CVC_DEPS_PREFIX}}/lib/pkgconfig:${{PKG_CONFIG_PATH:-}}"

cd "${{CVC_SOURCE_DIR}}"
meson setup "${{CVC_BUILD_DIR}}" \\
    --prefix="${{CVC_INSTALL_DIR}}" \\
    --buildtype=release
ninja -C "${{CVC_BUILD_DIR}}" -j "${{CVC_JOBS}}"
ninja -C "${{CVC_BUILD_DIR}}" install
"""

_BUILD_SH_AUTOTOOLS = """\
#!/usr/bin/env bash
# recipes/{name}/build.sh — build {name} on Linux/macOS/BSD with Autotools.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${{SCRIPT_DIR}}/../_common/env-${{CVC_PLATFORM}}.sh"

export PKG_CONFIG_PATH="${{CVC_DEPS_PREFIX}}/lib/pkgconfig:${{PKG_CONFIG_PATH:-}}"

cd "${{CVC_SOURCE_DIR}}"
./configure --prefix="${{CVC_INSTALL_DIR}}"
make -j "${{CVC_JOBS}}"
make install
"""

_BUILD_PS1_CMAKE = """\
# recipes/{name}/build.ps1 — build {name} on Windows with CMake.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\\..\\_common\\env-windows.ps1"

# Configure, build, and install with CMake.  Pass extra -D flags as needed.
Invoke-CvcCMakeBuild @()
"""

_SH_TEMPLATES = {
    "cmake": _BUILD_SH_CMAKE,
    "meson": _BUILD_SH_MESON,
    "autotools": _BUILD_SH_AUTOTOOLS,
}

# Host tools each build system needs, in addition to a compiler.
_HOST_TOOLS = {
    "cmake": ["cmake", "ninja"],
    "meson": ["meson", "ninja", "pkg-config"],
    "autotools": ["make", "pkg-config"],
}


def _recipe_yaml(
    *,
    name: str,
    version: str,
    url: str,
    description: str,
    homepage: str,
    license_: str,
    build_system: str,
    windows: bool,
) -> str:
    host_tools = "\n".join(f"    - {t}" for t in _HOST_TOOLS[build_system])
    matrix = [
        "    - platform: linux\n      script: build.sh",
        "    - platform: macos\n      script: build.sh",
    ]
    if windows:
        matrix.append("    - platform: windows\n      script: build.ps1")
    matrix_str = "\n".join(matrix)

    return f"""\
schema_version: 1
recipe:
  name: {name}
  upstream_version: "{version}"
  cvc_revision: 1
  maintainer: "TODO your name"
  maintainer_email: "TODO you@example.com"
  homepage: {homepage}
  license: {license_}
  tags: []
  description: >-
    {description}

source:
  type: tarball
  url: {url}
  # Fill in the tarball's SHA-256 (64 hex chars) before publishing:
  # sha256: "0000000000000000000000000000000000000000000000000000000000000000"
  strip_components: 1

patches: []

depends:
  build: []
  runtime: []
  host_tools:
{host_tools}

build:
  matrix:
{matrix_str}

package:
  # Glob patterns (relative to the install prefix) selecting the files to
  # ship.  Adjust to match what this component actually installs.
  files:
    - include/
    - lib/
"""


@cli.command("init")
@click.argument("name")
@click.option(
    "--dir",
    "recipes_dir",
    type=click.Path(file_okay=False),
    default="recipes",
    show_default=True,
    help="Recipes directory to create the recipe in.",
)
@click.option(
    "--build-system",
    type=click.Choice(["cmake", "meson", "autotools"], case_sensitive=False),
    default="cmake",
    show_default=True,
    help="Build system the recipe's build script targets.",
)
@click.option("--version", "version", default="0.0.0", help="Upstream version.")
@click.option("--url", default="https://example.com/TODO-source.tar.gz", help="Source tarball URL.")
@click.option("--description", default="TODO one-line description.", help="Short description.")
@click.option("--homepage", default="https://example.com", help="Project homepage URL.")
@click.option("--license", "license_", default="TODO-SPDX", help="SPDX license expression.")
@click.option("--force", is_flag=True, help="Overwrite an existing recipe directory.")
def init(
    name: str,
    recipes_dir: str,
    build_system: str,
    version: str,
    url: str,
    description: str,
    homepage: str,
    license_: str,
    force: bool,
) -> None:
    """Scaffold a new recipe under recipes/NAME.

    Generates a schema-valid recipe.yaml plus build script(s) for the chosen
    build system, ready to fill in (source URL/SHA-256, dependencies, and the
    package file globs).

    \b
    Examples:
      cvcpkg init mylib
      cvcpkg init mylib --build-system meson --version 1.2.3 \\
          --url https://example.org/mylib-1.2.3.tar.gz
    """
    build_system = build_system.lower()
    if not name.replace("-", "").replace("_", "").isalnum() or not name[0].isalpha():
        raise click.ClickException(
            f"invalid recipe name {name!r}: use lowercase letters, digits, and hyphens "
            "(must start with a letter)"
        )

    target = Path(recipes_dir) / name
    if target.exists() and not force:
        raise click.ClickException(f"{target} already exists (use --force to overwrite)")

    windows = build_system == "cmake"
    target.mkdir(parents=True, exist_ok=True)

    recipe_yaml = _recipe_yaml(
        name=name,
        version=version,
        url=url,
        description=description,
        homepage=homepage,
        license_=license_,
        build_system=build_system,
        windows=windows,
    )
    (target / "recipe.yaml").write_text(recipe_yaml)

    build_sh = _SH_TEMPLATES[build_system].format(name=name)
    sh_path = target / "build.sh"
    sh_path.write_text(build_sh)
    sh_path.chmod(0o755)

    created = ["recipe.yaml", "build.sh"]
    if windows:
        (target / "build.ps1").write_text(_BUILD_PS1_CMAKE.format(name=name))
        created.append("build.ps1")

    click.echo(f"cvcpkg: scaffolded recipe {name!r} ({build_system}) in {target}")
    for f in created:
        click.echo(f"  {target / f}")
    click.echo("")
    click.echo("Next steps:")
    click.echo("  1. Set source.url and source.sha256 in recipe.yaml.")
    click.echo("  2. List dependencies under depends: and adjust package.files.")
    click.echo(f"  3. Validate: cvcpkg validate {target}")
