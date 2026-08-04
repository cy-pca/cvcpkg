"""Drift guard: the canonical platform/arch vocabulary vs. every shipped schema.

This test exists because of a bug that shipped and survived: ``dragonflybsd``
was added to :data:`cvcpkg.platform.CANONICAL_PLATFORMS`, to the CLI's
``--platform`` choices and to the builder's ``_ELF_RPATH_PLATFORMS``, but to
NONE of the six JSON-Schema platform enums.  The result is a platform that
``detect_platform()`` happily emits and ``cvcpkg validate`` then rejects: a
DragonflyBSD host could build, but no recipe could name the platform it was
building for.  Nothing failed at the point of the mistake — the schemas live in
YAML and are only exercised by ``validate``, which never sees a platform that no
recipe uses yet.  So the first symptom is a red CI run in a downstream repo,
months later, on the day someone finally writes ``platform: dragonflybsd``.

The fix is not "remember to update seven places"; it is this file.  Adding a
platform to CANONICAL_PLATFORMS without teaching the schemas about it now fails
here, in a fast unit test, with a message naming the exact schema and site.

Two invariants are checked, in both directions:

1. *Completeness* — every canonical platform is spellable at every enum site,
   except where a site is deliberately narrower (``host_platform`` cannot be
   ``wasm``: nobody builds ON WebAssembly).  Exemptions are explicit, named and
   justified below, and are themselves guarded: an exemption may not name a
   platform ``detect_platform()`` can actually return.
2. *Soundness* — no schema enum may invent a platform tag that is not
   canonical, which is how typos ("dragonfly" vs "dragonflybsd") become a
   recipe that validates but resolves against an empty catalog.

The same two invariants are checked for :data:`cvcpkg.platform.CANONICAL_ARCHES`
against every schema *arch* enum, because the drift had already happened there
too: ``manifest-schema.yaml`` accepted only ``[x86_64, arm64]`` while cvcpkg
minted ``noarch`` for every ``platform: any`` bundle (and canonicalised
riscv64/ppc64le/ppc64/s390x/wasm32), so those manifests were unvalidatable.
"""

from __future__ import annotations

import sys

import pytest

from cvcpkg.cli._helpers import _VALID_PLATFORMS
from cvcpkg.platform import (
    ARCH_ALIASES,
    CANONICAL_ARCHES,
    CANONICAL_PLATFORMS,
    detect_platform,
    normalize_arch,
)
from cvcpkg.validation import load_schema

# Platforms that are not real build hosts — they are cross-compilation targets
# (wasm/wasi/cosmo), an ABI flavour of another host (windows-gnu, built from a
# Linux or Windows host), or the noarch sentinel.  A site restricted to genuine
# hosts is allowed to omit these.
_NOT_A_HOST = frozenset({"any", "wasm", "wasi", "cosmo", "windows-gnu"})

# ``any`` is the platform-independent *recipe* sentinel (build once, valid
# everywhere).  In a list-of-platforms field it would be a no-op — a dependency
# or cross-toolchain target that applies "to any platform" is expressed by
# omitting the key — so those sites legitimately omit it.
_ANY_IS_MEANINGLESS = frozenset({"any"})

# Every platform enum in every shipped schema:
#   (schema kind, dotted path to the enum, platforms the site may omit)
# The path is walked with plain dict indexing so a renamed/moved site fails
# loudly here rather than silently checking nothing.
_ENUM_SITES = [
    (
        "recipe",
        "$defs.dep_entry.properties.platforms.items.enum",
        _ANY_IS_MEANINGLESS,
    ),
    (
        "recipe",
        "$defs.matrix_entry.properties.platform.enum",
        frozenset(),
    ),
    (
        "recipe",
        "$defs.matrix_entry.properties.host_platform.enum",
        _NOT_A_HOST,
    ),
    (
        "recipe",
        "$defs.cross_toolchain_block.properties.target_platforms.items.enum",
        _ANY_IS_MEANINGLESS,
    ),
    (
        "components",
        "$defs.component.properties.platforms.items.enum",
        frozenset(),
    ),
    (
        "manifest",
        "$defs.bundle_block.properties.platform.enum",
        frozenset(),
    ),
]

# Every arch enum in every shipped schema, same tuple shape as _ENUM_SITES.
# Only the manifest names an arch today; a new site must be added here so the
# manifest's [x86_64, arm64] drift cannot recur elsewhere.
_ARCH_ENUM_SITES = [
    (
        "manifest",
        "$defs.bundle_block.properties.arch.enum",
        frozenset(),
    ),
]

# sys.platform spellings detect_platform() must map onto a canonical tag.  Any
# tag reachable from a real host has to be spellable at EVERY enum site,
# exemptions notwithstanding — that is the dragonflybsd bug stated as a rule.
_HOST_SYS_PLATFORMS = [
    "linux",
    "linux2",
    "darwin",
    "win32",
    "cygwin",
    "freebsd14",
    "openbsd7",
    "netbsd10",
    "dragonfly6",
    "haiku",
    "haiku1",
    "haikuR1~beta5",
]


def _enum_at(kind: str, path: str) -> list[str]:
    """Return the enum list at *path* in the *kind* schema.

    Raises with the full path when a segment is missing, so a schema
    refactor that moves an enum out from under this guard is a loud
    failure instead of a silently skipped assertion.
    """
    node = load_schema(kind)
    for segment in path.split("."):
        assert isinstance(node, dict) and segment in node, (
            f"{kind}-schema.yaml has no '{path}' (stopped at '{segment}') — "
            f"the enum moved or was renamed; update _ENUM_SITES"
        )
        node = node[segment]
    assert isinstance(node, list) and node, f"{kind}-schema.yaml:{path} is not a non-empty enum"
    return node


def _detected_platforms() -> set[str]:
    """Every canonical tag detect_platform() can return on a real host."""
    found = set()
    original = sys.platform
    try:
        for spelling in _HOST_SYS_PLATFORMS:
            sys.platform = spelling  # type: ignore[misc]
            found.add(detect_platform())
    finally:
        sys.platform = original  # type: ignore[misc]
    return found


@pytest.mark.parametrize("kind,path,exempt", _ENUM_SITES, ids=lambda v: str(v)[:60])
def test_every_canonical_platform_is_spellable(kind, path, exempt):
    """CANONICAL_PLATFORMS ⊆ enum, modulo the site's documented exemptions."""
    enum = set(_enum_at(kind, path))
    missing = (CANONICAL_PLATFORMS - exempt) - enum
    assert not missing, (
        f"{kind}-schema.yaml:{path} is missing canonical platform(s) "
        f"{sorted(missing)} — cvcpkg can emit them but validate rejects them"
    )


@pytest.mark.parametrize("kind,path,exempt", _ENUM_SITES, ids=lambda v: str(v)[:60])
def test_schema_enum_invents_no_platform(kind, path, exempt):
    """enum ⊆ CANONICAL_PLATFORMS — a schema may not coin its own tag."""
    enum = set(_enum_at(kind, path))
    unknown = enum - CANONICAL_PLATFORMS
    assert not unknown, (
        f"{kind}-schema.yaml:{path} accepts non-canonical platform(s) "
        f"{sorted(unknown)} — a recipe using one would validate but never "
        f"resolve; add it to CANONICAL_PLATFORMS or fix the spelling"
    )


def test_exemptions_never_hide_a_real_host():
    """An exemption may not name a platform a real host detects as.

    Without this, the guard could be silenced by simply exempting the platform
    that was forgotten — exactly the failure mode it exists to prevent.
    """
    detected = _detected_platforms()
    for kind, path, exempt in _ENUM_SITES:
        bogus = exempt & detected
        assert not bogus, (
            f"{kind}-schema.yaml:{path} exempts {sorted(bogus)}, but "
            f"detect_platform() returns those on a real host"
        )


def test_detected_platforms_validate_everywhere():
    """Anything detect_platform() returns must be usable at every enum site.

    This is the dragonflybsd/haiku bug in its most direct form: a host that
    cvcpkg can run on must be a host that recipes, components tables and
    manifests can all name.
    """
    detected = _detected_platforms()
    assert "haiku" in detected, "detect_platform() no longer recognises Haiku"
    for kind, path, _exempt in _ENUM_SITES:
        enum = set(_enum_at(kind, path))
        missing = detected - enum
        assert not missing, (
            f"{kind}-schema.yaml:{path} cannot express {sorted(missing)}, "
            f"which detect_platform() returns on a real host"
        )


def test_cli_offers_every_detectable_platform():
    """``--platform`` must accept anything ``--platform auto`` could resolve to.

    ``auto`` resolves through detect_platform(), so a tag the CLI cannot be
    told explicitly is a tag no one can cross-target or re-drive a failed
    build with.
    """
    missing = _detected_platforms() - set(_VALID_PLATFORMS)
    assert not missing, f"cli/_helpers.py:_VALID_PLATFORMS is missing {sorted(missing)}"


def test_cli_platform_choices_are_canonical():
    """No non-canonical spelling in the CLI choice list ('auto' aside)."""
    unknown = set(_VALID_PLATFORMS) - CANONICAL_PLATFORMS - {"auto"}
    assert not unknown, f"cli/_helpers.py:_VALID_PLATFORMS has non-canonical {sorted(unknown)}"


# ── the same guard, for arches ──────────────────────────────────


@pytest.mark.parametrize("kind,path,exempt", _ARCH_ENUM_SITES, ids=lambda v: str(v)[:60])
def test_every_canonical_arch_is_spellable(kind, path, exempt):
    """CANONICAL_ARCHES ⊆ enum, modulo the site's documented exemptions."""
    enum = set(_enum_at(kind, path))
    missing = (CANONICAL_ARCHES - exempt) - enum
    assert not missing, (
        f"{kind}-schema.yaml:{path} is missing canonical arch(es) "
        f"{sorted(missing)} — cvcpkg can mint them but validate rejects them"
    )


@pytest.mark.parametrize("kind,path,exempt", _ARCH_ENUM_SITES, ids=lambda v: str(v)[:60])
def test_schema_enum_invents_no_arch(kind, path, exempt):
    """enum ⊆ CANONICAL_ARCHES — a schema may not coin its own arch tag."""
    enum = set(_enum_at(kind, path))
    unknown = enum - CANONICAL_ARCHES
    assert not unknown, (
        f"{kind}-schema.yaml:{path} accepts non-canonical arch(es) "
        f"{sorted(unknown)} — a bundle carrying one would validate but never "
        f"resolve; add it to CANONICAL_ARCHES or fix the spelling"
    )


def test_noarch_bundles_are_spellable():
    """The concrete regression: a ``platform: any`` bundle is published as
    platform=any / arch=noarch, so both sentinels must validate."""
    for kind, path, _exempt in _ENUM_SITES:
        if kind != "manifest":
            continue
        assert "any" in _enum_at(kind, path)
    for kind, path, _exempt in _ARCH_ENUM_SITES:
        assert "noarch" in _enum_at(kind, path), (
            f"{kind}-schema.yaml:{path} cannot express 'noarch', the arch every "
            f"platform-independent bundle is published with"
        )


def test_arch_aliases_normalize_into_the_enum():
    """Every raw spelling cvcpkg normalises must land on a spellable arch.

    ``amd64``/``aarch64``/``x64`` reach the publisher from BSD and macOS hosts;
    an alias whose canonical form no schema accepts is the same failure as a
    missing enum entry, one indirection further away.
    """
    for kind, path, _exempt in _ARCH_ENUM_SITES:
        enum = set(_enum_at(kind, path))
        for raw in ARCH_ALIASES:
            canonical = normalize_arch(raw)
            assert canonical in enum, (
                f"{kind}-schema.yaml:{path} cannot express {canonical!r}, which "
                f"normalize_arch({raw!r}) returns"
            )
