# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Shared CLI option decorators and utility functions."""

from __future__ import annotations

from pathlib import Path

import click

# ── Shared option decorators ────────────────────────────────────
#
# These are reusable click.option decorators applied to multiple
# commands.  Each produces a keyword argument of the same name in
# the decorated function.  "auto" sentinels are resolved at runtime
# by detect_platform() / detect_arch().

_VALID_PLATFORMS = [
    "auto",
    "any",
    "linux",
    "macos",
    "windows",
    "wasm",
    "wasi",
    "cosmo",
    "freebsd",
    "openbsd",
    "netbsd",
    "dragonflybsd",
]
_VALID_ARCHES = [
    "auto",
    "x86_64",
    "arm64",
    "riscv64",
    "ppc64le",
    "ppc64",
    "s390x",
    "noarch",
    "wasm32",
]

_platform_opt = click.option(
    "--platform",
    type=click.Choice(_VALID_PLATFORMS, case_sensitive=False),
    default="auto",
    help="Target platform.  'auto' detects the current OS.",
)
_config_opt = click.option(
    "--config",
    type=click.Choice(["release", "debug"], case_sensitive=False),
    default="release",
    help="Build configuration (maps to CMAKE_BUILD_TYPE).",
)
_link_opt = click.option(
    "--link",
    type=click.Choice(["shared", "static"], case_sensitive=False),
    default="shared",
    help="Link mode -- shared (.so/.dylib/.dll) or static (.a/.lib).",
)
_prefix_opt = click.option(
    "--prefix",
    type=click.Path(),
    default="./deps",
    help="Install prefix directory (will contain bin/, lib/, include/).",
)
_keep_build_opt = click.option(
    "--keep-build-dir",
    is_flag=True,
    help="Keep intermediate build directories for debugging.",
)
_recipes_dir_opt = click.option(
    "--recipes-dir",
    "recipes_dirs",
    type=click.Path(exists=True),
    multiple=True,
    help=(
        "Extra recipes/ directory to overlay on top of the default "
        "(bundled) recipes.  May be specified multiple times; later "
        "directories win on name conflicts."
    ),
)
_no_default_recipes_opt = click.option(
    "--no-default-recipes",
    is_flag=True,
    default=False,
    help="Ignore the auto-detected default recipes directory;"
    " use only explicit --recipes-dir paths.",
)
_maintainer_opt = click.option(
    "--maintainer",
    type=str,
    default="",
    help="Override the maintainer field in the package manifest.",
)
_local_opt = click.option(
    "--local",
    "local_mode",
    is_flag=True,
    envvar="CVCPKG_LOCAL",
    help=(
        "Use local/bundled recipes only — do not contact a package server.  "
        "Without --local, cvcpkg connects to the server specified by "
        "CVCPKG_SERVER_URL (default: cvcpkg.org) to fetch the latest "
        "recipes and catalog.  [env: CVCPKG_LOCAL]"
    ),
)


def _resolve_recipes_dirs(
    extra: tuple[str, ...] = (),
    *,
    no_default: bool = False,
) -> list[Path]:
    """Return the canonical list of recipe directories.

    Unless *no_default* is ``True``, starts with the bundled/default
    recipes from ``find_recipes_dir()`` and appends any explicit ``extra``
    overlay directories.  Then, still unless *no_default*, appends the
    current working directory's ``./recipes`` when it holds at least one
    ``*/recipe.yaml`` and isn't already listed — so a one-shot
    ``cvcpkg build <name>`` from a repo root finds the repo-local recipe with
    no ``--recipes-dir``.  Later entries win on name conflicts, so this CWD
    overlay (added last) overrides a same-named bundled recipe.

    ``--no-default-recipes`` suppresses BOTH the bundled default and the CWD
    auto-overlay (they are both auto-detected); only explicit ``--recipes-dir``
    paths are then used.
    """
    from cvcpkg.builder import RecipeError, cwd_recipes_overlay, find_recipes_dir

    dirs: list[Path] = []
    if not no_default:
        try:
            dirs.append(find_recipes_dir())
        except RecipeError:
            pass
    for d in extra:
        p = Path(d).resolve()
        if p not in dirs:
            dirs.append(p)
    if not no_default:
        cwd_overlay = cwd_recipes_overlay(dirs)
        if cwd_overlay is not None:
            dirs.append(cwd_overlay)
    if not dirs:
        raise click.ClickException("could not find recipes directory")
    return dirs


def _validate_org_slug(ctx: click.Context, param: click.Parameter, value: str) -> str:
    """Click callback that validates --org using GitHub username rules."""
    if not value:
        return value
    from cvcpkg.orgs import validate_org_slug

    err = validate_org_slug(value)
    if err:
        raise click.BadParameter(err)
    return value


def _human_size(n: int) -> str:
    """Format bytes as a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0  # type: ignore[assignment]
    return f"{n:.1f} PB"
