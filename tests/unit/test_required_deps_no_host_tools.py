"""Guard: host tools must never leak into a bundle's consumer-facing required_deps.

The manifest generator picks a recipe's consumer-facing deps as
``depends.get("runtime", depends.get("build", []))`` (see
``cvcpkg.builder`` manifest assembly).  A recipe that lists build-time host
tools (cmake/ninja/nasm/…) under ``depends.build`` but omits a
``depends.runtime`` key therefore bakes those tools into every bundle's
``required_deps``.

That is harmless on platforms where the host tools are published (the
resolver just installs them wastefully) but *fatal* on cross-only platforms
such as ``wasm-mt`` where no host-tool bundle exists — the resolver reports
``no candidate for 'cmake'`` and the whole closure fails to resolve.

This test replicates the generator's selection and fails if any real recipe
would emit a known host tool as a runtime dependency.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_RECIPES = Path(__file__).resolve().parents[2] / "recipes"

# Build-time tools that must never appear as a consumer runtime dependency.
_HOST_TOOLS = {
    "cmake",
    "ninja",
    "nasm",
    "yasm",
    "meson",
    "autoconf",
    "automake",
    "libtool",
    "make",
    "pkg-config",
    "pkgconf",
    "patchelf",
}


def _dep_name(d: object) -> str:
    if isinstance(d, str):
        return d.split("/", 1)[1] if "/" in d else d
    if isinstance(d, dict):
        return str(d.get("name", ""))
    return ""


# Recipes with the same host-tool leak that are NOT yet fixed. The fix (an explicit
# ``runtime: []``) is trivial and identical for all of them, but each requires a
# cvc_revision bump + fleet rebuild to republish the corrected manifest, and these
# few currently fail to rebuild on the fleet for unrelated, pre-existing reasons —
# so bumping them re-triggers those failures. They are blocked on dedicated
# build-repair tasks; only once a recipe's fleet build is green can its leak fix
# land and it drop off this list.
#
#   automake            — autoconf's self-test runs `autoconf -o /dev/null`, whose
#                         autom4te resolves m4 as `$M4 || /usr/bin/m4` (never PATH);
#                         builders lack /usr/bin/m4 or ship non-GNU BSD m4, so the
#                         rebuild fails (build-on-dev #9688/#9692).
#   wayland-protocols   — build-depend on the `wayland` recipe, whose own build
#   xkbcommon             fails on linux/freebsd (build-on-dev #9691/#9695); their
#                         rebuild can't succeed until wayland's does.
#
# The leak is harmless on every platform these recipes target (their host tools —
# cmake/ninja/nasm/… — are published there, so the resolver just installs them
# wastefully); it is only fatal on cross-only platforms like wasm-mt, and none of
# these are in the wasm-mt demo closure. Shrink this list as each build is repaired;
# do NOT add to it — a new leaking recipe must be fixed, not deferred.
_DEFERRED_LEAKS: set[str] = {
    "automake",
    "wayland-protocols",
    "xkbcommon",
}


def _recipe_files() -> list[Path]:
    return sorted(p for p in _RECIPES.glob("*/recipe.yaml") if p.parent.name not in _DEFERRED_LEAKS)


@pytest.mark.parametrize("recipe_path", _recipe_files(), ids=lambda p: p.parent.name)
def test_required_deps_have_no_host_tools(recipe_path: Path) -> None:
    raw = yaml.safe_load(recipe_path.read_text()) or {}
    depends = raw.get("depends", {})
    if not isinstance(depends, dict):
        return
    # Mirror the manifest generator's selection exactly.
    consumer = depends.get("runtime", depends.get("build", []))
    if not isinstance(consumer, list):
        return
    leaked = sorted({_dep_name(d) for d in consumer} & _HOST_TOOLS)
    assert not leaked, (
        f"{recipe_path.parent.name}: host tool(s) {leaked} would leak into "
        f"required_deps. Move them under depends.host_tools (or add an "
        f"explicit depends.runtime) so they don't ship as consumer deps."
    )
