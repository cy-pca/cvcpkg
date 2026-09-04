# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Recipe builder and packager for cvcpkg.

Implements the ``cvcpkg build`` and ``cvcpkg pack`` workflow described
in §7.4-7.5 of the split-distribution roadmap.
"""

from __future__ import annotations

import hashlib
import os
import platform as _platform_module
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from cvcpkg.errors import CvcpkgError
from cvcpkg.heartbeat import unwatch, watch, watched
from cvcpkg.platform import detect_arch, detect_platform, lib_path_var

# ── Errors ──────────────────────────────────────────────────────


class RecipeError(CvcpkgError):
    """Problem loading or validating a recipe."""


class BuildError(CvcpkgError):
    """Build script exited with a non-zero status."""


class PackError(CvcpkgError):
    """Packaging stage failed (staging, manifest, or archiving)."""


@dataclass
class BuildFailure:
    """Record of a recipe that failed during ``build_all``."""

    recipe_name: str
    error: Exception
    skipped: bool = False  # True if skipped due to a failed dependency


class BuildAllResult(list):
    """List of ``BuildContext`` with an attached ``failures`` list."""

    failures: list[BuildFailure]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.failures: list[BuildFailure] = []


def qualified_name(name: str, org: str = "") -> str:
    """Return ``org/name`` for org-scoped packages, plain ``name`` otherwise."""
    return f"{org}/{name}" if org else name


# ── Data model ──────────────────────────────────────────────────


@dataclass
class SourceSpec:
    """Parsed ``source:`` block from recipe.yaml."""

    type: str  # tarball | git | vcpkg | brew | apt | vendored | prebuilt | none |
    #            python_wheel | python_sdist
    url: str = ""
    mirror: str = ""
    sha256: str = ""
    path: str = ""
    port: str = ""
    triplet: str = ""
    baseline: str = ""
    strip_components: int = 1
    base_url: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)
    # ── git sources ──
    # A tag is a MUTABLE ref, so it is never sufficient on its own: the recipe
    # pins the 40-hex commit and the fetch verifies HEAD against it.
    commit: str = ""
    # none | shallow | recursive.  Accepts the legacy booleans the schema used
    # to allow, where true meant "recursive".
    submodules: str = "none"
    # Optional path -> 40-hex map.  The superproject's gitlinks already pin
    # every submodule, so this is not what makes the build reproducible; it
    # exists so that a submodule moving shows up as a reviewable diff in the
    # recipe rather than silently riding in on a superproject bump.
    submodule_pins: dict[str, str] = field(default_factory=dict)
    # Clone depth; 0 means a full clone (needed by recipes whose build reads
    # git history, e.g. to derive a version).
    depth: int = 1

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SourceSpec:
        return cls(
            type=d["type"],
            url=d.get("url", ""),
            mirror=d.get("mirror", ""),
            sha256=d.get("sha256", ""),
            path=d.get("path", ""),
            port=d.get("port", ""),
            triplet=d.get("triplet", ""),
            baseline=d.get("baseline", ""),
            strip_components=d.get("strip_components", 1),
            base_url=d.get("base_url", ""),
            artifacts=d.get("artifacts", {}) or {},
            commit=d.get("commit", ""),
            submodules=_normalize_submodules(d.get("submodules")),
            submodule_pins=dict(d.get("submodule_pins", {}) or {}),
            depth=int(d.get("depth", 1)),
        )


def _normalize_submodules(value: Any) -> str:
    """Coerce a ``source.submodules`` value to ``none``/``shallow``/``recursive``.

    The schema originally typed this as a plain boolean, so accept that form
    too rather than breaking a recipe that used it: ``true`` is ``recursive``
    (the only useful meaning of "yes" for a nested project like Mitsuba) and
    ``false`` is ``none``.
    """
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "recursive" if value else "none"
    text = str(value).strip().lower()
    if text not in ("none", "shallow", "recursive"):
        raise RecipeError(f"source.submodules must be one of none/shallow/recursive, got {value!r}")
    return text


@dataclass
class PythonSpec:
    """Parsed ``python:`` block from recipe.yaml (Phase 7).

    Declares which cvcpkg interpreter a wheel/sdist recipe targets.  The
    ``abi`` is the CPython ABI tag the artifact is built for (``cp311`` …
    ``cp313t``); a trailing ``t`` marks the free-threaded (no-GIL) ABI.
    """

    interpreter: str = ""  # cvcpkg recipe name, e.g. "python313t"
    abi: str = ""  # wheel ABI tag, e.g. "cp313t", or "abi3"
    manylinux_min: str = ""  # e.g. "manylinux_2_28"
    build_isolation: bool = False
    build_requires: list[str] = field(default_factory=list)

    @property
    def stable_abi(self) -> bool:
        """True for a stable-ABI (``abi3``) wheel.

        One abi3 wheel serves every interpreter from ``interpreter`` upwards,
        so such a package collapses the matrix to a single column instead of
        one recipe per interpreter.
        """
        return self.abi == "abi3"

    @property
    def free_threaded(self) -> bool:
        """True for the GIL-disabled ABI (``cp313t``), which we test at -X gil=0.

        Never true for abi3: the 3.13 free-threaded build does not implement
        the stable ABI, so a stable-ABI wheel cannot cover cp313t.
        """
        return self.abi.endswith("t")

    @property
    def version_tag(self) -> str:
        """``cp313t`` -> ``3.13``: the X.Y the interpreter reports.

        Empty for abi3, which pins no single version by construction.
        """
        if self.stable_abi:
            return ""
        digits = "".join(c for c in self.abi if c.isdigit())
        if len(digits) < 3:
            return ""
        return f"{digits[0]}.{digits[1:]}"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PythonSpec:
        return cls(
            interpreter=d.get("interpreter", ""),
            abi=d.get("abi", ""),
            manylinux_min=d.get("manylinux_min", ""),
            build_isolation=bool(d.get("build_isolation", False)),
            build_requires=list(d.get("build_requires", []) or []),
        )


@dataclass
class MatrixEntry:
    """One entry from ``build.matrix[]``."""

    platform: str
    script: str
    env: dict[str, str] = field(default_factory=dict)
    host_platform: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MatrixEntry:
        return cls(
            platform=d["platform"],
            script=d["script"],
            env=d.get("env", {}),
            host_platform=d.get("host_platform"),
        )


@dataclass
class Recipe:
    """Parsed recipe.yaml."""

    name: str
    upstream_version: str
    cvc_revision: int
    source: SourceSpec
    patches: list[str]
    build_matrix: list[MatrixEntry]
    package_files: list[str]
    test_script: str | None
    raw: dict[str, Any]  # full parsed YAML for manifest generation
    recipe_dir: Path
    tags: list[str] = field(default_factory=list)
    kind: str = ""  # e.g. data, media, config, iso, image -- downstream hints
    # NOTE: "image" is not just a hint -- pack_recipe enforces the
    # share/<name>/ layout and a schema-valid image.yaml for it.
    cross_toolchain_targets: list[str] = field(default_factory=list)
    cross_toolchain_env: dict[str, str] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    # Virtual slots this package fills.  Every provider of a slot is mutually
    # exclusive with every other, so a group of n alternatives needs n
    # declarations instead of n*(n-1) pairwise `conflicts` entries — and cannot
    # be declared asymmetrically.
    provides: list[str] = field(default_factory=list)
    # Host capabilities that must ALL be present for this package to be
    # selectable by the resolver (e.g. ``[cuda]``).  Empty = universal.
    requires_capabilities: list[str] = field(default_factory=list)
    # Raw ``test.vm`` block (see cvcpkg.vmtest).  Held unparsed so the builder
    # never imports the hypervisor machinery for the ~all recipes without one.
    #
    # NOTE this is deliberately NOT folded into requires_capabilities above:
    # that key gates whether the resolver will SELECT the package, and an image
    # must stay installable on a host with no hypervisor.  test.vm carries its
    # own capability gate, which controls only whether the test runs.
    vm_test: dict[str, Any] | None = None
    python: PythonSpec | None = None

    @property
    def full_version(self) -> str:
        return f"{self.upstream_version}+cvc.{self.cvc_revision}"

    @classmethod
    def load(cls, recipe_dir: Path) -> Recipe:
        """Load and parse a recipe from a directory."""
        recipe_yaml = recipe_dir / "recipe.yaml"
        if not recipe_yaml.is_file():
            raise RecipeError(f"recipe.yaml not found in {recipe_dir}")

        with open(recipe_yaml) as f:
            raw = yaml.safe_load(f)

        recipe_block = raw.get("recipe", {})
        source_block = raw.get("source", {})
        build_block = raw.get("build", {})
        package_block = raw.get("package", {})
        test_block = raw.get("test", {})

        ct_block = raw.get("cross_toolchain", {})
        python_block = raw.get("python", {})

        return cls(
            name=recipe_block.get("name", recipe_dir.name),
            upstream_version=str(recipe_block.get("upstream_version", "0.0.0")),
            cvc_revision=int(recipe_block.get("cvc_revision", 1)),
            source=SourceSpec.from_dict(source_block),
            patches=raw.get("patches", []) or [],
            build_matrix=[MatrixEntry.from_dict(m) for m in build_block.get("matrix", [])],
            package_files=package_block.get("files", []),
            test_script=test_block.get("script") if test_block else None,
            vm_test=(test_block.get("vm") or None) if test_block else None,
            raw=raw,
            recipe_dir=recipe_dir.resolve(),
            tags=recipe_block.get("tags", []) or [],
            kind=recipe_block.get("kind", ""),
            cross_toolchain_targets=ct_block.get("target_platforms", []) or [],
            cross_toolchain_env=ct_block.get("env", {}) or {},
            conflicts=raw.get("conflicts", []) or [],
            provides=raw.get("provides", []) or [],
            requires_capabilities=raw.get("requires_capabilities", []) or [],
            python=PythonSpec.from_dict(python_block) if python_block else None,
        )


# ── Source fetching ─────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _source_cache_dir() -> Path | None:
    """Return the source tarball cache directory, or None if caching is disabled.

    Controlled by ``CVCPKG_SOURCE_CACHE_DIR`` (set to empty string to disable).
    Default: ``~/.cache/cvcpkg/sources``.
    """
    env = os.environ.get("CVCPKG_SOURCE_CACHE_DIR")
    if env is not None:
        if not env:
            return None  # explicitly disabled
        return Path(env)
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "cvcpkg" / "sources"
    return Path.home() / ".cache" / "cvcpkg" / "sources"


def _cache_key(source: SourceSpec) -> str:
    """Return a stable cache key for a tarball source spec."""
    if source.sha256:
        return source.sha256
    # Fallback: hash the URL so we still get some cache benefit
    return hashlib.sha256(source.url.encode()).hexdigest()


def _fetch_tarball(source: SourceSpec, dest: Path) -> Path:
    """Download an archive (tar.gz/.tar.xz/.tar.bz2/.zip), verify SHA-256, and extract.

    If a source cache directory is configured (default
    ``~/.cache/cvcpkg/sources``), verified archives are stored there
    keyed by SHA-256.  Subsequent builds skip the download entirely
    when the cached file is present and matches.
    """
    import urllib.error
    import urllib.request

    urls = [u for u in (source.url, source.mirror) if u]
    if not urls:
        raise RecipeError("source.type=tarball but no URL specified")

    # Detect archive kind from the primary URL so we know how to extract.
    # We keep the on-disk name suffix aligned with the URL suffix — the
    # sha256 cache key is content-addressed so the cached filename does
    # not need to reflect the format.
    _primary = urls[0].lower()
    _is_zip = _primary.endswith(".zip")
    _suffix = ".zip" if _is_zip else ".tar.gz"
    archive_path = dest / f"source{_suffix}"

    cache_dir = _source_cache_dir()
    cache_hit = False

    # Try the cache first
    if cache_dir is not None:
        key = _cache_key(source)
        cached = cache_dir / f"{key}{_suffix}"
        if cached.is_file():
            # Verify integrity when SHA-256 is known
            if source.sha256:
                actual = _sha256_file(cached)
                if actual == source.sha256:
                    shutil.copy2(str(cached), str(archive_path))
                    cache_hit = True
                # Mismatched cache entry -- re-download
            else:
                # No SHA-256 to check -- trust the cache
                shutil.copy2(str(cached), str(archive_path))
                cache_hit = True

    if not cache_hit:
        last_error: Exception | None = None
        for url in urls:
            try:
                urllib.request.urlretrieve(url, archive_path)  # noqa: S310
                last_error = None
                break
            except (urllib.error.URLError, OSError) as e:
                last_error = e
                if url != urls[-1]:
                    continue
        if last_error is not None:
            raise RecipeError(
                f"failed to download source from {urls}: {last_error}"
            ) from last_error

    if source.sha256:
        actual = _sha256_file(archive_path)
        if actual != source.sha256:
            raise RecipeError(f"SHA-256 mismatch: expected {source.sha256}, got {actual}")

    # Populate cache after successful verification
    if not cache_hit and cache_dir is not None:
        key = _cache_key(source)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / f"{key}{_suffix}"
        shutil.copy2(str(archive_path), str(cached))

    # Extract
    source_dir = dest / "src"
    source_dir.mkdir()
    if _is_zip:
        with zipfile.ZipFile(archive_path) as zf:
            # Security: reject paths that escape the target directory
            for name in zf.namelist():
                resolved = (source_dir / name).resolve()
                if not str(resolved).startswith(str(source_dir.resolve())):
                    raise RecipeError(f"Zip member escapes target: {name}")
            zf.extractall(source_dir)
    else:
        with tarfile.open(archive_path) as tf:
            # Security: reject paths that escape the target directory
            for member in tf.getmembers():
                resolved = (source_dir / member.name).resolve()
                if not str(resolved).startswith(str(source_dir.resolve())):
                    raise RecipeError(f"Tarball member escapes target: {member.name}")
            if sys.version_info >= (3, 12):
                tf.extractall(source_dir, filter="data")
            else:
                tf.extractall(source_dir)

    # If strip_components>0, move the inner directory up
    if source.strip_components > 0:
        children = list(source_dir.iterdir())
        if len(children) == 1 and children[0].is_dir():
            inner = children[0]
            stripped = dest / "stripped"
            shutil.move(str(inner), str(stripped))
            shutil.rmtree(source_dir)
            stripped.rename(source_dir)

    return source_dir


def _git_cache_dir() -> Path | None:
    """Bare-mirror cache directory for git sources, or None if disabled.

    Controlled by ``CVCPKG_GIT_CACHE`` (set to empty string to disable).
    Default: ``~/.cache/cvcpkg/git``, alongside the tarball source cache.
    """
    env = os.environ.get("CVCPKG_GIT_CACHE")
    if env is not None:
        if not env:
            return None  # explicitly disabled
        return Path(env)
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "cvcpkg" / "git"
    return Path.home() / ".cache" / "cvcpkg" / "git"


def _git_offline() -> bool:
    """True when ``CVCPKG_GIT_OFFLINE`` forbids network access.

    The tarball path is fully offline once its cache is warm; this is how the
    git path reaches parity.  It fails loudly rather than quietly reaching out,
    which is the only behaviour an air-gapped build can actually rely on.
    """
    val = os.environ.get("CVCPKG_GIT_OFFLINE", "").strip().lower()
    return val not in ("", "0", "false", "no", "off")


def _mirror_path(cache_dir: Path, url: str) -> Path:
    """Bare-mirror directory for *url* — readable slug plus a hash for uniqueness.

    Splits on both separators and caps the slug: a local Windows path has no
    forward slashes at all, so splitting on "/" alone turned the whole
    ``C:\\Users\\...`` path into the "name" and produced a mirror path long
    enough for git to fail mid-clone with "Filename too long". The hash is what
    guarantees uniqueness; the slug only has to be readable.
    """
    parts = [p for p in re.split(r"[\\/]+", url.rstrip("/\\")) if p]
    tail = parts[-1] if parts else "repo"
    if tail.endswith(".git"):
        tail = tail[:-4]
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", tail).strip("-")[:48] or "repo"
    digest = hashlib.sha256(url.encode()).hexdigest()[:12]
    return cache_dir / f"{slug}-{digest}.git"


def _run_git(args: list[str], cwd: Path | None = None, *, what: str) -> str:
    """Run git, returning stdout; raise RecipeError naming *what* on failure."""
    if shutil.which("git") is None:
        raise RecipeError("source.type=git requires git on PATH, and it was not found")
    proc = subprocess.run(  # noqa: S603
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RecipeError(f"{what} failed: git {' '.join(args)}\n{detail}")
    return proc.stdout.strip()


def _rmtree_force(path: Path) -> None:
    """Remove a tree, clearing the read-only bit git sets on its pack files.

    Windows refuses to unlink a read-only file, so a plain ``rmtree`` on a git
    mirror leaves a half-deleted directory behind — and the next clone then
    fails with "destination path already exists and is not an empty directory",
    which says nothing about the actual cause.
    """

    def _handle(func: Any, p: str, _exc: Any) -> None:
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_handle)
    else:
        shutil.rmtree(path, onerror=_handle)


def _update_mirror(url: str, mirror: Path, *, commit: str) -> None:
    """Ensure *mirror* is a bare clone of *url* containing *commit*.

    Offline, the mirror must already exist and already hold the commit; the
    error names the missing mirror and its URL, because "clone failed" without
    that is unactionable on a machine with no network.
    """
    offline = _git_offline()

    def _has_commit() -> bool:
        if not (mirror / "HEAD").is_file():
            return False
        proc = subprocess.run(  # noqa: S603
            ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
            cwd=str(mirror),
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0

    if _has_commit():
        return

    if offline:
        if not (mirror / "HEAD").is_file():
            raise RecipeError(
                f"CVCPKG_GIT_OFFLINE is set and no mirror exists for {url}\n"
                f"  expected bare mirror at: {mirror}\n"
                f"  warm it with a networked run, or point CVCPKG_GIT_CACHE at "
                f"a populated cache"
            )
        raise RecipeError(
            f"CVCPKG_GIT_OFFLINE is set and the mirror for {url} does not "
            f"contain commit {commit}\n"
            f"  mirror: {mirror}\n"
            f"  the pin moved since the mirror was warmed — re-warm it online"
        )

    if (mirror / "HEAD").is_file():
        # Mirror exists but predates this pin: fetch, don't re-clone.
        _run_git(
            ["fetch", "--prune", "origin", "+refs/*:refs/*"],
            cwd=mirror,
            what=f"updating git mirror for {url}",
        )
    else:
        # A directory with no HEAD is a half-written mirror from an interrupted
        # clone. Clear it rather than letting git refuse a non-empty target.
        _rmtree_force(mirror)
        mirror.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["clone", "--mirror", url, str(mirror)], what=f"cloning git mirror for {url}")

    if not _has_commit():
        raise RecipeError(
            f"commit {commit} is not present in {url} after fetching\n"
            f"  a pinned commit that the remote does not have is either a typo "
            f"or a force-push; cvcpkg will not fall back to a branch tip"
        )


def _submodule_state(work: Path) -> list[tuple[str, str]]:
    """Return sorted ``(path, sha)`` for every initialized submodule, recursively."""
    out = _run_git(
        ["submodule", "status", "--recursive"], cwd=work, what="reading submodule status"
    )
    state: list[tuple[str, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # " <sha> <path> (<describe>)" — a leading -/+/U marks un-init/modified.
        if line[0] in "-+U":
            line = line[1:]
        parts = line.split()
        if len(parts) >= 2:
            state.append((parts[1], parts[0]))
    return sorted(state)


def _tree_digest(commit: str, submodules: list[tuple[str, str]]) -> str:
    """A deterministic digest of exactly what was checked out.

    The superproject commit plus every submodule's (path, sha), sorted.  Two
    cold-cache runs of the same recipe produce the same digest, and "did the
    source change" is answerable without a network round-trip.

    Sorts its own input rather than trusting the caller to: a digest whose
    value depends on the order git happened to report submodules in would be
    reproducible only by accident.
    """
    h = hashlib.sha256()
    h.update(commit.encode())
    for path, sha in sorted(submodules):
        h.update(b"\0")
        h.update(path.encode())
        h.update(b"\0")
        h.update(sha.encode())
    return h.hexdigest()


def _fetch_git(source: SourceSpec, dest: Path) -> Path:
    """Clone a pinned git commit (optionally with submodules) into ``dest/src``.

    Meets the same contract the tarball path already meets — pinned, verified
    and reproducible offline once the mirror cache is warm:

    * the recipe pins a 40-hex ``commit`` and never a tag, because tags move;
    * the checkout is verified against that commit after the fact;
    * ``submodule_pins`` entries are checked, so a moved submodule is a loud
      failure rather than a silent difference;
    * a bare mirror under ``CVCPKG_GIT_CACHE`` makes re-runs offline-capable,
      and ``CVCPKG_GIT_OFFLINE=1`` refuses to touch the network at all.
    """
    if not source.url:
        raise RecipeError("source.type=git but no URL specified")
    if not source.commit:
        raise RecipeError(
            "source.type=git requires a 40-character `commit` — a tag is a "
            "mutable ref and cannot pin a build"
        )
    if not re.fullmatch(r"[0-9a-f]{40}", source.commit):
        raise RecipeError(
            f"source.commit must be a full 40-character lowercase SHA, got " f"{source.commit!r}"
        )

    work = dest / "src"
    cache_dir = _git_cache_dir()

    if cache_dir is not None:
        mirror = _mirror_path(cache_dir, source.url)
        _update_mirror(source.url, mirror, commit=source.commit)
        origin: str = str(mirror)
    else:
        if _git_offline():
            raise RecipeError(
                "CVCPKG_GIT_OFFLINE is set but the git mirror cache is "
                'disabled (CVCPKG_GIT_CACHE=""), so there is nothing to '
                "build from offline"
            )
        origin = source.url

    _run_git(["clone", "--no-checkout", origin, str(work)], what=f"cloning {source.url}")
    _run_git(
        ["checkout", "--detach", source.commit], cwd=work, what=f"checking out {source.commit}"
    )

    head = _run_git(["rev-parse", "HEAD"], cwd=work, what="verifying HEAD")
    if head != source.commit:
        raise RecipeError(f"checked-out HEAD {head} does not match pinned commit {source.commit}")

    if source.submodules != "none":
        if _git_offline():
            # Submodule URLs in .gitmodules are absolute upstreams, and cvcpkg
            # does not mirror them per-module yet, so there is nothing on disk
            # to fetch them from. Say so instead of failing later inside git.
            raise RecipeError(
                "CVCPKG_GIT_OFFLINE with source.submodules is not supported "
                "yet: submodules are fetched from their own upstream URLs and "
                "cvcpkg mirrors only the superproject. Warm the checkout "
                "online first, or vendor the tree."
            )
        # 'shallow' takes the top level only; 'recursive' follows the nesting.
        args = ["submodule", "update", "--init"]
        if source.submodules == "recursive":
            args.append("--recursive")
        # depth=0 means "full history", for a submodule whose build reads it.
        if source.depth:
            args += ["--depth", str(source.depth), "--recommend-shallow"]
        _run_git(args, cwd=work, what="initializing submodules")

    state = _submodule_state(work) if source.submodules != "none" else []

    # Verify declared pins. The gitlink already decides what gets checked out,
    # so a mismatch here means the RECIPE is stale relative to the superproject
    # bump — which is exactly the review signal the pins exist to produce.
    if source.submodule_pins:
        actual = dict(state)
        problems = []
        for path, want in sorted(source.submodule_pins.items()):
            got = actual.get(path)
            if got is None:
                problems.append(f"  {path}: declared in submodule_pins but not checked out")
            elif got != want:
                problems.append(f"  {path}: pinned {want}, superproject has {got}")
        if problems:
            raise RecipeError(
                "submodule pins do not match the superproject:\n"
                + "\n".join(problems)
                + "\nupdate source.submodule_pins to match, or move the commit pin back"
            )

    digest = _tree_digest(source.commit, state)
    (dest / "git-tree-digest.txt").write_text(digest + "\n", encoding="utf-8")

    # .git is deliberately KEPT. Plenty of CMake projects — Mitsuba among them —
    # derive a version string from `git describe` at configure time, and
    # deleting the metadata would break them in a way that looks like a build
    # bug rather than a fetch decision. The tree is pinned either way, so
    # keeping it costs reproducibility nothing.
    return work


def _resolve_vendored(source: SourceSpec, recipe_dir: Path) -> Path:
    """Locate vendored source tree relative to the repo."""
    if not source.path:
        raise RecipeError("source.type=vendored but no path specified")
    # First check _vendored/ sibling (used by remote builder recipe bundles)
    bundled = (recipe_dir.parent / "_vendored" / source.path).resolve()
    if bundled.is_dir():
        return bundled
    # Fall back to repo root (parent of recipes/) for local builds
    repo_root = recipe_dir.parent.parent
    src = (repo_root / source.path).resolve()
    if not src.is_dir():
        raise RecipeError(f"Vendored source not found: {src}")
    return src


def _resolve_artifact_entry(source: SourceSpec, key: str, entry: Any) -> tuple[str, str, str]:
    """Turn one ``artifacts`` map entry (bare filename or mapping) into
    ``(url, sha256, filename)``."""
    if isinstance(entry, str):
        filename = entry
        url = f"{source.base_url.rstrip('/')}/{filename}" if source.base_url else filename
        return url, source.sha256, filename

    filename = entry.get("file", "")
    url = entry.get("url", "")
    if not url:
        if not filename:
            raise RecipeError(f"artifact {key} has neither 'url' nor 'file'")
        if not source.base_url:
            raise RecipeError(f"artifact {key} uses 'file' but recipe has no base_url")
        url = f"{source.base_url.rstrip('/')}/{filename}"
    if not filename:
        filename = url.rsplit("/", 1)[-1]
    return url, entry.get("sha256", ""), filename


def _platform_wheel_keys(source: SourceSpec, platform: str, arch: str) -> list[str]:
    """Artifact keys carrying a wheel for *platform*/*arch*, in stable order.

    A column recipe pins exactly one wheel per platform, so this is the bare
    ``{platform}-{arch}`` key (or empty when only ``any``/top-level applies).
    ``{platform}-{arch}-cpNN`` sibling keys were the retired per-version
    fan-out shape — they are still collected here so the fetch layer can
    reject them loudly instead of silently installing an arbitrary one
    (recipes/_common only installs a single wheel now).
    """
    prefix = f"{platform}-{arch}"
    return sorted(k for k in source.artifacts if k == prefix or k.startswith(f"{prefix}-"))


#: Architecture spellings that mean the same machine.  Recipes are keyed on
#: cvcpkg's canonical names, but upstream download URLs (and therefore the
#: humans transcribing them) frequently use the other spelling — `wasmer`
#: ships a `linux-aarch64` artifact that would otherwise look like "no arm64
#: support at all".
_ARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "arm64": ("arm64", "aarch64"),
    "aarch64": ("aarch64", "arm64"),
    "x86_64": ("x86_64", "amd64"),
    "amd64": ("amd64", "x86_64"),
}


def _artifacts_cover(recipe: Recipe, platform: str, arch: str) -> bool:
    """Does *recipe* have a prebuilt artifact for *platform*/*arch*?

    Only meaningful for recipes that ship PREBUILT artifacts — a recipe built
    from source has no ``artifacts`` map and is always eligible, as is one
    keyed ``any`` (a pure-Python wheel).

    This exists because ``source.artifacts`` already IS the platform/arch
    constraint, and it is the only place that information lives: the recipe
    schema has no ``arch`` field, and duplicating the fact into one would just
    give it somewhere to drift from.  Without this filter a linux/arm64 run
    attempts every x86_64-only wheel — torch, triton, wand, the 14
    nvidia-*-cu12 CUDA redistributables — and each dies deep in the build with
    ``no artifact for linux-arm64``, which reads like breakage rather than
    "not applicable here".  27 recipes did exactly that on the first arm64
    populate.

    Deliberately NOT applied inside a direct ``cvcpkg build <name>``: asking
    for one recipe by name should still fail loudly, so a mistyped artifact
    key is an error rather than a silent no-op.
    """
    arts = getattr(recipe.source, "artifacts", None)
    if not arts:
        return True
    if "any" in arts:
        return True
    if not arch:
        return True
    for candidate in _ARCH_ALIASES.get(arch, (arch,)):
        prefix = f"{platform}-{candidate}"
        if any(k == prefix or k.startswith(f"{prefix}-") for k in arts):
            return True
    return False


def _resolve_artifact(source: SourceSpec, platform: str, arch: str) -> tuple[str, str, str]:
    """Resolve the ``artifacts`` entry for *platform*/*arch*.

    Returns ``(url, sha256, filename)``.  Entries may be a bare filename
    (joined onto ``base_url``) or a mapping carrying its own ``url``/``file``
    and ``sha256``.  A recipe with no ``artifacts`` map falls back to the
    top-level ``url``/``sha256`` — that is the ``platform: any`` case (a pure
    Python wheel is valid everywhere).
    """
    if not source.artifacts:
        if not source.url:
            raise RecipeError("no artifacts map and no source.url to fall back on")
        return source.url, source.sha256, source.url.rsplit("/", 1)[-1]

    key = f"{platform}-{arch}"
    entry = source.artifacts.get(key)
    if entry is None:
        # Accept the other common spelling of the same machine before giving
        # up (linux-aarch64 vs linux-arm64); see _ARCH_ALIASES.
        for _alias in _ARCH_ALIASES.get(arch, ()):
            entry = source.artifacts.get(f"{platform}-{_alias}")
            if entry is not None:
                key = f"{platform}-{_alias}"
                break
    if entry is None:
        # Platform-independent ('any') fallback: a recipe whose only artifact is
        # keyed ``any`` (a pure-Python ``py3-none-any`` wheel — valid on every
        # host) resolves for any concrete platform/arch, and for the synthetic
        # ``any``/``noarch`` build identity a noarch recipe is packaged under.
        entry = source.artifacts.get("any")
    if entry is None:
        available = ", ".join(sorted(source.artifacts)) or "(none)"
        raise RecipeError(f"no artifact for {key}; recipe provides: {available}")

    return _resolve_artifact_entry(source, key, entry)


def _download_pinned_wheel(url: str, sha256: str, filename: str, source_dir: Path) -> Path:
    """Download one pinned wheel into *source_dir*, sha256-verified, via cache.

    Phase 7 requires every wheel to be sha256-pinned, so a missing hash is an
    error, not a warning.
    """
    import urllib.error
    import urllib.request

    if not sha256:
        raise RecipeError(f"python_wheel artifact {filename} has no sha256 (pinning is required)")

    wheel_path = source_dir / filename
    cache_dir = _source_cache_dir()
    cached = cache_dir / f"{sha256}.whl" if cache_dir is not None else None
    if cached is not None and cached.is_file() and _sha256_file(cached) == sha256:
        shutil.copy2(str(cached), str(wheel_path))
        return wheel_path

    try:
        urllib.request.urlretrieve(url, wheel_path)  # noqa: S310
    except (urllib.error.URLError, OSError) as e:
        raise RecipeError(f"failed to download wheel from {url}: {e}") from e

    actual = _sha256_file(wheel_path)
    if actual != sha256:
        raise RecipeError(f"SHA-256 mismatch for {filename}: expected {sha256}, got {actual}")

    if cached is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        shutil.copy2(str(wheel_path), str(cached))
    return wheel_path


def _fetch_python_wheel(source: SourceSpec, dest: Path, platform: str, arch: str) -> Path:
    """Download and sha256-verify THE pinned wheel for platform/arch; do not
    unpack it.

    pip parses the compatibility tags out of the wheel *filename*, so the
    artifact keeps its upstream name.  Every python package is a
    per-interpreter column recipe pinning exactly one wheel per platform;
    ``{plat}-{arch}-cpNN`` sibling keys (the retired per-version fan-out
    shape) are a hard error — the single-wheel install helper would
    otherwise silently pick an arbitrary one.
    """
    source_dir = dest / "src"
    source_dir.mkdir(exist_ok=True)

    keys = _platform_wheel_keys(source, platform, arch)
    if len(keys) > 1:
        raise RecipeError(
            f"python_wheel artifacts carry per-interpreter sibling keys "
            f"{keys!r}: the per-version fan-out recipe shape is retired — "
            f"split the package into one -cpNN column recipe per interpreter"
        )
    if keys:
        url, sha256, filename = _resolve_artifact_entry(source, keys[0], source.artifacts[keys[0]])
        _download_pinned_wheel(url, sha256, filename, source_dir)
    else:
        # No platform-specific key: the ``any`` (noarch) or top-level single-wheel
        # fallback that _resolve_artifact already encodes.
        url, sha256, filename = _resolve_artifact(source, platform, arch)
        _download_pinned_wheel(url, sha256, filename, source_dir)
    return source_dir


def fetch_source(recipe: Recipe, work_dir: Path, *, platform: str = "", arch: str = "") -> Path:
    """Fetch or locate the source tree for a recipe.

    *platform*/*arch* select the entry from an ``artifacts`` map; they
    default to the host, which is what a native build wants.
    """
    src = recipe.source
    if src.type == "tarball":
        return _fetch_tarball(src, work_dir)
    if src.type == "vendored":
        return _resolve_vendored(src, recipe.recipe_dir)
    if src.type == "python_wheel":
        return _fetch_python_wheel(
            src, work_dir, platform or detect_platform(), arch or detect_arch()
        )
    if src.type == "python_sdist":
        # An sdist is just a tarball whose URL may be platform-resolved.
        url, sha256, _ = _resolve_artifact(
            src, platform or detect_platform(), arch or detect_arch()
        )
        if not sha256:
            raise RecipeError("python_sdist requires a sha256 (pinning is required)")
        return _fetch_tarball(replace(src, url=url, sha256=sha256, mirror=""), work_dir)
    if src.type in ("vcpkg", "brew", "apt", "prebuilt", "none"):
        # These are handled by the build script itself (or, for ``none``,
        # there is nothing to fetch: data-only recipes stage their payload
        # out-of-band and are packed with --from-prefix); return a dummy
        # source directory.
        dummy = work_dir / "src"
        dummy.mkdir(exist_ok=True)
        return dummy
    if src.type == "git":
        return _fetch_git(src, work_dir)
    raise RecipeError(f"Unknown source type: {src.type}")


# ── Patch application ──────────────────────────────────────────


def apply_patches(recipe: Recipe, source_dir: Path) -> None:
    """Apply patches listed in the recipe.

    Tries ``git apply`` first (widely available, tolerant of modern
    unified diffs) and falls back to ``patch -p1`` when git isn't on
    PATH or rejects the diff.  This ordering matters on Windows,
    where Strawberry Perl ships an ancient ``patch.exe`` (2.5.9,
    year 2003) that asserts out on any diff with git-mailbox headers
    or new-style hunk markers.
    """
    for patch_file in recipe.patches:
        patch_path = (recipe.recipe_dir / patch_file).resolve()
        # Security: ensure patch file doesn't escape the recipe directory
        if not str(patch_path).startswith(str(recipe.recipe_dir.resolve())):
            raise RecipeError(f"Patch path escapes recipe directory: {patch_file}")
        if not patch_path.is_file():
            raise RecipeError(f"Patch file not found: {patch_path}")

        attempts: list[list[str]] = []
        if shutil.which("git") is not None:
            attempts.append(["git", "apply", "-p1", "--whitespace=nowarn", str(patch_path)])
        attempts.append(["patch", "-p1", "-i", str(patch_path)])

        last_err = ""
        applied = False
        for cmd in attempts:
            result = subprocess.run(
                cmd,
                cwd=source_dir,
                check=False,
                capture_output=True,
            )
            if result.returncode == 0:
                applied = True
                break
            last_err = result.stderr.decode(errors="replace").strip() or last_err
        if not applied:
            raise RecipeError(f"Failed to apply patch {patch_file}: {last_err}")


# ── Build execution ────────────────────────────────────────────


def _select_matrix_entry(recipe: Recipe, platform: str, host_platform: str = "") -> MatrixEntry:
    """Pick the matrix entry matching the target platform.

    When *host_platform* is given (e.g. ``"linux"``, ``"windows"``),
    prefer entries whose ``host_platform`` matches.  This allows
    cross-compilation recipes (like wasm) to select the correct build
    script for the current host OS.

    Falls back to the first ``platform`` match when no
    ``host_platform`` match is found.

    Platform-independent recipes (``platform: any``) are matched when
    no exact platform entry exists.
    """
    # Normalize short platform names to recipe matrix names.
    _plat_aliases = {"win": "windows"}
    norm_platform = _plat_aliases.get(platform, platform)
    norm_host = _plat_aliases.get(host_platform, host_platform) if host_platform else ""

    fallback: MatrixEntry | None = None
    any_fallback: MatrixEntry | None = None
    for entry in recipe.build_matrix:
        if entry.platform in (platform, norm_platform):
            if norm_host and entry.host_platform in (host_platform, norm_host):
                return entry
            if fallback is None:
                fallback = entry
        elif entry.platform == "any" and any_fallback is None:
            any_fallback = entry
    if fallback is not None:
        return fallback
    if any_fallback is not None:
        return any_fallback
    raise RecipeError(
        f"No build matrix entry for platform '{platform}' in recipe '{recipe.name}'. "
        f"Available: {[e.platform for e in recipe.build_matrix]}"
    )


@dataclass
class BuildContext:
    """All paths and settings for a single build invocation."""

    recipe: Recipe
    platform: str
    config: str  # release | debug
    link: str  # shared | static
    prefix: Path
    source_dir: Path
    build_dir: Path
    install_dir: Path
    work_dir: Path
    keep_build_dir: bool = False
    host_platform: str = ""
    cross_toolchain_env: dict[str, str] = field(default_factory=dict)
    # Separate prefix for everything the build needs but the deliverable must
    # not ship: build-dependency closure (host tools, cross-toolchains, staged
    # source packages under ``src/<name>``).  Placement is decided by the
    # dependency *edge* -- ``depends.build`` lands here, ``depends.runtime``
    # lands in ``prefix``.  None => no separation (same as prefix).
    build_prefix: Path | None = None


# Platforms whose ldconfig(8) maintains a SYSTEM-WIDE hints cache that it
# rebuilds from scratch by default -- ``ldconfig dir...`` REPLACES the search
# path, and only ``ldconfig -m dir...`` merges into it.  A build step that runs
# ldconfig as root on these therefore rewrites machine-global state.
#
# OpenBSD is the platform this was found on.  Its /etc/rc seeds the hints with
# /usr/local/lib and /usr/X11R6/lib at boot; a bare ``ldconfig`` (the reflex
# that is correct on Linux, and common in install hooks) rebuilds them from the
# built-in /usr/lib alone, so both directories silently vanish and every package
# under /usr/local stops resolving -- including python's own libpython:
#
#   ld.so: python3.12: can't load library 'libpython3.12.so.0.0'
#
# FreeBSD has the same replace-by-default semantics for /var/run/ld-elf.so.hints
# and is covered pre-emptively.  NetBSD is deliberately absent: it ships no
# hints cache at all (no /var/run/ld.so.hints, no /etc/ld.so.conf), so there is
# nothing for a build to corrupt.
_LDCONFIG_HINTS_PLATFORMS = frozenset({"openbsd", "freebsd"})


def _install_ldconfig_shim(ctx: BuildContext) -> Path | None:
    """Stage a no-op ``ldconfig`` for the build to find ahead of the real one.

    cvcpkg never needs a build to touch the system hints cache: everything it
    installs is found through the prefix search paths this same function's
    caller exports (``LD_LIBRARY_PATH`` and friends) and, at run time, through
    RPATH.  Any ldconfig a build *does* run is therefore pure side effect on
    the shared builder VM -- at best littering the hints with the job's own
    scratch directories, at worst dropping the system ones.

    Both happened on the OpenBSD builder: libtool's ``finish_cmds`` is
    ``ldconfig -m $libdir``, which only ever appends, so it left seven dead
    ``.../cvcpkg-job-*/install/lib`` entries behind; something else ran a
    non-merging ldconfig that dropped /usr/local/lib outright.

    Shimming covers both without having to identify every caller, because
    libtool resolves ldconfig through ``PATH`` (its finish_cmds *appends*
    ``/sbin``, so a shim at the front wins).  Returns the shim directory, or
    None on platforms with no hints cache to protect.

    Read-only queries are forwarded to the real binary so anything genuinely
    inspecting the cache still works; every mutating form exits 0 silently.
    A build that calls ``/sbin/ldconfig`` by absolute path still bypasses this
    -- the release workflow re-merges the system directories before each build
    as a backstop for that case.
    """
    if ctx.platform not in _LDCONFIG_HINTS_PLATFORMS:
        return None
    # Cache-restore builds synthesise a context whose build_dir IS the prefix
    # (see the cache.restore branch of build_recipe).  No build script runs
    # there, so there is nothing to shim -- and writing into it would leave the
    # shim sitting in a deliverable.
    if ctx.build_dir in (ctx.prefix, ctx.install_dir):
        return None
    shim_dir = ctx.build_dir / ".cvcpkg-shims"
    try:
        shim_dir.mkdir(parents=True, exist_ok=True)
        shim = shim_dir / "ldconfig"
        shim.write_text(
            "#!/bin/sh\n"
            "# cvcpkg build shim -- see builder._install_ldconfig_shim.\n"
            "# Builds must not rewrite this machine's shared-library cache.\n"
            'case "${1:-}" in\n'
            '    -r) exec /sbin/ldconfig "$@" ;;\n'
            "esac\n"
            "exit 0\n"
        )
        shim.chmod(0o755)
    except OSError:
        # Never fail a build over the shim; the worst case is the old behaviour.
        return None
    return shim_dir


def _build_env(ctx: BuildContext, matrix: MatrixEntry) -> dict[str, str]:
    """Construct the environment for the build script."""
    env = os.environ.copy()
    # Standard CVC env vars (§7.3 of the roadmap)
    # Resolve symlinks so build systems that reject symlinked paths
    # (e.g. Qt6) work on macOS where /var -> /private/var.
    env["CVC_PREFIX"] = str(ctx.install_dir.resolve())
    env["CVC_SOURCE_DIR"] = str(ctx.source_dir.resolve())
    env["CVC_BUILD_DIR"] = str(ctx.build_dir.resolve())
    env["CVC_INSTALL_DIR"] = str(ctx.install_dir.resolve())
    env["CVC_PLATFORM"] = ctx.platform
    env["CVC_CONFIG"] = ctx.config
    env["CVC_LINK"] = ctx.link
    env["CVC_COMPONENT"] = ctx.recipe.name
    env["CVC_VERSION"] = ctx.recipe.upstream_version
    # The minted identity, for build scripts that must WRITE their own version
    # into a generated file (an image descriptor, a version header).  These
    # reflect the recipe as committed; `pack --bump` / `--cvc-revision` re-stamp
    # the revision AFTER the build script has run, so a bumped pack leaves such
    # a generated file at the committed revision.
    env["CVC_REVISION"] = str(ctx.recipe.cvc_revision)
    env["CVC_FULL_VERSION"] = ctx.recipe.full_version
    env["CVC_RECIPE_DIR"] = str(ctx.recipe.recipe_dir)

    # CVC_DEPS_PREFIX tells build.sh where to find previously-built
    # dependencies.  When building into a shared prefix this equals
    # install_dir; callers building into isolated per-component dirs
    # can override via the prefix field.
    env["CVC_DEPS_PREFIX"] = str(ctx.prefix)

    # Phase 7: wheel/sdist recipes tell _common/python-wheel.{sh,ps1} which
    # interpreter in the prefix to install into and test under.
    if ctx.recipe.python is not None:
        env["CVC_PYTHON_ABI"] = ctx.recipe.python.abi
        env["CVC_PYTHON_INTERPRETER"] = ctx.recipe.python.interpreter
        if ctx.recipe.python.manylinux_min:
            env["CVC_PYTHON_MANYLINUX_MIN"] = ctx.recipe.python.manylinux_min
        if ctx.recipe.python.free_threaded:
            # Belt and braces: a free-threaded child process must not silently
            # re-enable the GIL just because some extension asked for it.
            env["PYTHON_GIL"] = "0"
        # Every python package is a per-interpreter column recipe
        # (<name>-cpNNN[t]) installing only into its own interpreter's
        # site-packages — there is no cross-interpreter fan-out.

    build_type = "Release" if ctx.config == "release" else "Debug"
    env["CMAKE_BUILD_TYPE"] = build_type
    # The _common/env-*.sh scripts (and env-windows.ps1 via winhost) key off
    # CVC_BUILD_TYPE and re-derive CMAKE_BUILD_TYPE from it, defaulting to
    # Release when unset — without this export every recipe sourcing them
    # silently built Release regardless of --config.
    env["CVC_BUILD_TYPE"] = build_type
    env.setdefault("BUILD_SHARED_LIBS", "ON" if ctx.link == "shared" else "OFF")

    # Cross-compilation: set CVC_HOST_PLATFORM when the matrix
    # entry specifies a host_platform different from the target.
    if matrix.host_platform:
        env["CVC_HOST_PLATFORM"] = matrix.host_platform

    # Apply cross-toolchain environment variables.  Toolchain recipes
    # (e.g. emsdk) declare ``cross_toolchain.env`` entries that should
    # be set when building recipes for their target platforms.  The
    # builder installs toolchains into the prefix before target recipes
    # are built, so we point these env vars at the prefix.
    # Always override — the builder may have stale values (e.g. from
    # a systemd Environment= line) that must not shadow the resolved
    # per-build prefix path.
    # The build-dependency closure (host tools, cross-toolchains, staged source
    # packages) may live in a separate build prefix so the deliverable --prefix
    # ships only the runtime closure; fall back to the install prefix when the
    # separation is disabled.
    build_prefix = ctx.build_prefix or ctx.prefix
    if ctx.cross_toolchain_env:
        for var, tpl in ctx.cross_toolchain_env.items():
            env[var] = tpl.replace("${PREFIX}", str(build_prefix))

    # Build scripts get two search roots: CVC_DEPS_PREFIX (above) is the
    # install prefix holding the runtime closure (headers/libs to link);
    # CVC_BUILD_PREFIX holds the build closure -- host tools on PATH and
    # staged source packages at ``src/<name>`` (see _common/stage-source.sh).
    env["CVC_BUILD_PREFIX"] = str(build_prefix.resolve())

    # Ensure host tools (cmake, ninja, protoc, toolchains, ...) are found
    # before system versions.  The build prefix's bin comes first.
    bin_dirs: list[str] = []
    for _d in ((build_prefix / "bin"), (ctx.prefix / "bin"), (ctx.install_dir / "bin")):
        _s = str(_d.resolve())
        if _s not in bin_dirs:
            bin_dirs.append(_s)
    # Ahead of everything, including the prefix: this has to win over the real
    # /sbin/ldconfig that libtool appends to PATH for its finish_cmds.
    _shim_dir = _install_ldconfig_shim(ctx)
    if _shim_dir is not None:
        bin_dirs.insert(0, str(_shim_dir))
    existing_path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join(bin_dirs + ([existing_path] if existing_path else []))

    # Ensure shared-library dependencies installed in the prefix are
    # discoverable at build time.  Build steps may invoke tools (e.g.
    # gRPC running protoc) that link against shared libs from earlier
    # recipes -- including build-closure tools living in the build prefix.
    lib_dirs = [
        str((ctx.prefix / "lib").resolve()),
        str((ctx.install_dir / "lib").resolve()),
    ]
    _bp_lib = str((build_prefix / "lib").resolve())
    if _bp_lib not in lib_dirs:
        lib_dirs.insert(0, _bp_lib)
    if sys.platform == "darwin":
        existing = env.get("DYLD_LIBRARY_PATH", "")
        env["DYLD_LIBRARY_PATH"] = ":".join(lib_dirs + ([existing] if existing else []))
    elif ctx.platform not in ("wasm", "wasm-mt"):
        # Not always LD_LIBRARY_PATH: Haiku's runtime_loader reads LIBRARY_PATH
        # and ignores LD_LIBRARY_PATH outright, so the wrong name here would
        # leave the prefix's shared libs invisible to every build step.
        lib_var = lib_path_var(ctx.platform)
        existing = env.get(lib_var, "")
        env[lib_var] = ":".join(lib_dirs + ([existing] if existing else []))

    # Merge matrix-entry env overrides
    env.update(matrix.env)
    return env


def _find_patchelf(*prefixes: Path | None) -> str | None:
    """Find patchelf, preferring a cvcpkg-built copy under *prefixes*.

    Packaging rewrites installed ``.so`` RPATHs to ``$ORIGIN`` with patchelf
    (see :func:`_patch_elf_rpath`).  For the self-hosting goal — cvcpkg
    bootstraps all of its own build tooling — prefer the ``patchelf`` that
    cvcpkg built into the build/deps prefix (its ``bin/`` is populated when the
    ``patchelf`` recipe is bootstrapped as a host tool, see
    :func:`_bootstrap_host_tools`) over a system install.  Each prefix's
    ``bin/patchelf`` is tried in order; falls back to ``PATH`` otherwise.

    ``_patch_elf_rpath`` runs in the cvcpkg parent process, whose ``PATH``
    does not include the build prefix's ``bin`` (only the build *subprocess*
    gets that, via ``_build_env``), so the prefix must be probed explicitly.

    Returns the resolved patchelf path, or ``None`` when none is available.
    """
    for prefix in prefixes:
        if prefix is None:
            continue
        candidate = prefix / "bin" / "patchelf"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("patchelf")


# ELF platforms whose run-time linker expands ``$ORIGIN`` in RPATH, so the
# $ORIGIN rewrite in _patch_elf_rpath makes their shared bundles relocatable.
# OpenBSD is deliberately excluded: its ld.so does not implement $ORIGIN, so the
# rewrite would be silently ignored — relocatable OpenBSD bundles need a
# different mechanism if/when it becomes an active build target. macOS/Windows
# are handled separately (install_name / PATH-relative DLLs).
# Haiku qualifies too: its images are plain ELF, its runtime_loader honours
# DT_RPATH/DT_RUNPATH and expands $ORIGIN, and HaikuPorts ships the same
# patchelf 0.18.0 this rewrite drives.
_ELF_RPATH_PLATFORMS = frozenset({"linux", "freebsd", "netbsd", "haiku"})


def _patch_elf_rpath(install_dir: Path, patchelf: str | None = None) -> None:
    """Prepend ``$ORIGIN`` to the RPATH of shared libraries in *install_dir*.

    This makes ELF shared-library bundles relocatable without requiring
    LD_LIBRARY_PATH at runtime.  Applies to Linux AND the ELF BSDs whose
    run-time linker expands ``$ORIGIN`` (FreeBSD/GhostBSD, NetBSD,
    DragonflyBSD) — see ``_ELF_RPATH_PLATFORMS``.  Only runs when patchelf is
    available; silently skips otherwise.

    *patchelf* is the resolved patchelf binary to use — callers pass the
    cvcpkg-built one (via :func:`_find_patchelf`) so packaging does not depend
    on a system install.  When ``None`` it is looked up on ``PATH``.

    Crucially it PRESERVES any pre-existing ``$ORIGIN``-relative RPATH
    entries rather than clobbering them.  Python wheels bundle their native
    dependencies in a sibling directory and point at it with an RPATH like
    ``$ORIGIN/../../numpy.libs`` (numpy's OpenBLAS, scipy's libgfortran, …);
    the old blind ``--remove-rpath`` + ``--set-rpath $ORIGIN`` destroyed that
    link, so the bundled ``libscipy_openblas*.so`` became unreachable and
    ``import numpy`` failed with "cannot open shared object file".  Absolute
    (build-temp) RPATH entries are still dropped — only relocatable
    ``$ORIGIN``-relative ones are kept.
    """
    if patchelf is None:
        patchelf = shutil.which("patchelf")
    if not patchelf:
        return
    lib_dir = install_dir / "lib"
    if not lib_dir.is_dir():
        return
    for so in lib_dir.rglob("*.so*"):
        if not so.is_file() or so.is_symlink():
            continue
        existing = subprocess.run(
            [patchelf, "--print-rpath", str(so)],
            capture_output=True,
            text=True,
        ).stdout.strip()
        # Keep only relocatable ($ORIGIN-relative) entries, drop bare $ORIGIN
        # (re-added first) and any absolute build-temp paths.
        kept = [e for e in existing.split(":") if e.startswith("$ORIGIN") and e != "$ORIGIN"]
        new_rpath = ":".join(["$ORIGIN", *kept])
        subprocess.run(
            [patchelf, "--set-rpath", new_rpath, str(so)],
            capture_output=True,
        )


def _patch_macos_install_names(install_dir: Path) -> None:
    """Rewrite absolute build-tree install names to ``@rpath`` on macOS dylibs.

    The macOS analog of :func:`_patch_elf_rpath`.  autotools/libtool builds
    (e.g. ImageMagick) bake the absolute build-temp install prefix into a
    dylib's own install name (``LC_ID_DYLIB``) and into its references to
    sibling dylibs (``LC_LOAD_DYLIB``).  Once the bundle is unpacked elsewhere
    that path no longer exists, so anything linking the dylib records the dead
    path and fails under dyld ("Library not loaded:
    .../cvcpkg-<recipe>-XXXX/install/lib/...").  CMake builds already default to
    ``@rpath`` install names (``MACOSX_RPATH``), so this only rescues the
    autotools/hand-rolled ones.

    For every dylib in *install_dir*/lib it: sets the id to ``@rpath/<leaf>``;
    adds a ``@loader_path`` RPATH so the dylib finds its siblings next to itself
    (the ``$ORIGIN`` analog); and rewrites any absolute reference that points at
    another dylib IN THIS BUNDLE to ``@rpath/<leaf>``.  System references
    (/usr/lib, /System/...) are left untouched.  Only runs when
    ``install_name_tool``/``otool`` are available (any macOS host); silently
    skips otherwise.
    """
    install_name_tool = shutil.which("install_name_tool")
    otool = shutil.which("otool")
    if not install_name_tool or not otool:
        return
    lib_dir = install_dir / "lib"
    if not lib_dir.is_dir():
        return
    # Leaf names of every dylib the bundle ships (incl. version symlinks), so we
    # only rewrite references that resolve to one of OUR libraries.
    bundle_leaves = {p.name for p in lib_dir.rglob("*.dylib")}
    for dylib in lib_dir.rglob("*.dylib"):
        if not dylib.is_file() or dylib.is_symlink():
            continue
        subprocess.run(
            [install_name_tool, "-id", f"@rpath/{dylib.name}", str(dylib)],
            capture_output=True,
        )
        # Idempotent: -add_rpath errors (harmlessly) if @loader_path is present.
        subprocess.run(
            [install_name_tool, "-add_rpath", "@loader_path", str(dylib)],
            capture_output=True,
        )
        listing = subprocess.run(
            [otool, "-L", str(dylib)],
            capture_output=True,
            text=True,
        ).stdout
        # First line is the file path itself; the rest are dependent libraries.
        for line in listing.splitlines()[1:]:
            ref = line.strip().split(" ", 1)[0]
            if ref.startswith("/") and Path(ref).name in bundle_leaves:
                subprocess.run(
                    [install_name_tool, "-change", ref, f"@rpath/{Path(ref).name}", str(dylib)],
                    capture_output=True,
                )


def _make_relocatable(ctx: BuildContext) -> None:
    """Rewrite the installed tree so the shared bundle is relocatable.

    Consumers must load the libraries without LD_LIBRARY_PATH/DYLD_* —
    ``$ORIGIN`` RPATH on ELF (Linux + the ``$ORIGIN``-honouring BSDs + Haiku),
    ``@rpath`` install names on macOS.  (Windows resolves DLLs from the prefix
    bin dir on PATH, so it needs no rewrite.)  On ELF, prefer cvcpkg's own
    patchelf (bootstrapped into the build prefix as a host tool) over a system
    install, so packaging is self-hosting — see _find_patchelf and
    _bootstrap_host_tools.

    Split out of :func:`run_build` because it has to run for DELEGATED builds
    too, and those return early.
    """
    if ctx.link != "shared":
        return
    if ctx.platform == "macos":
        _patch_macos_install_names(ctx.install_dir)
    elif ctx.platform in _ELF_RPATH_PLATFORMS:
        patchelf = _find_patchelf(ctx.build_prefix, ctx.prefix)
        _patch_elf_rpath(ctx.install_dir, patchelf)


def run_build(
    ctx: BuildContext,
    log_callback: Callable[[str], None] | None = None,
) -> None:
    """Execute the build script for the given context.

    When *log_callback* is provided, subprocess stdout/stderr is
    captured line-by-line and forwarded to the callback in addition
    to being printed locally.  This enables remote builders to stream
    live build output to the server.
    """
    matrix = _select_matrix_entry(ctx.recipe, ctx.platform, ctx.host_platform)
    script = ctx.recipe.recipe_dir / matrix.script

    if not script.is_file():
        raise BuildError(f"Build script not found: {script}")

    # Windows-target cross builds from a WSL builder are delegated to
    # the Windows host through interop (the host's MSVC toolchain runs
    # the recipe's normal build.ps1) — see cvcpkg.winhost.
    from cvcpkg import winhost

    if winhost.should_delegate(ctx.platform, ctx.host_platform):
        winhost.run_winhost_build(ctx, matrix, script, log_callback=log_callback)
        return

    # Haiku-target builds are delegated over SSH to a Haiku host — cvcpkg
    # cannot run natively on Haiku (HaikuPorts has no pip and only
    # cryptography 3.4.8 against our >=41 floor), so the Haiku box is a
    # build target, never a builder.  See cvcpkg.haikuhost.  Both hooks sit
    # ahead of everything below because the local path would otherwise hand
    # the recipe's Haiku build.sh to the LOCAL toolchain and "succeed" —
    # producing Linux binaries in a haiku/x86_64 package.
    from cvcpkg import haikuhost

    if haikuhost.should_delegate(ctx.platform, ctx.host_platform):
        # run_haiku_build refuses (loudly) when no Haiku host is configured; it
        # never falls back to the local toolchain.
        haikuhost.run_haiku_build(ctx, matrix, script, log_callback=log_callback)
        # The delegated build ends with the install tree copied BACK here, and
        # a non-CMake recipe (no -DCMAKE_INSTALL_RPATH=$ORIGIN) leaves absolute
        # build-tree RPATHs in it, so the bundle is not relocatable until this
        # pass runs.  It runs on the Linux side, over the synced-back tree,
        # because patchelf edits ELF as data rather than executing it and Haiku
        # images are plain ELF64 — so the rewrite is identical wherever it is
        # performed, while staying in ONE place (the same _find_patchelf that
        # every other ELF platform uses) and requiring nothing of the Haiku box.
        # Doing it remotely would instead need patchelf installed there, an
        # extra round trip, and a second copy of this policy.
        # [unverified] patchelf's handling of Haiku-produced ELF has not been
        # exercised against a real Haiku host; _patch_elf_rpath is best-effort
        # (it ignores patchelf failures), so the worst case is the status quo.
        _make_relocatable(ctx)
        return

    env = _build_env(ctx, matrix)

    # Determine the interpreter
    if script.suffix == ".sh":
        interpreter = _find_bash()
        cmd = [interpreter, str(script)]
    elif script.suffix == ".ps1":
        interpreter = _find_pwsh(ctx.prefix)
        cmd = [interpreter, "-NoProfile", "-NonInteractive", "-File", str(script)]
    else:
        raise BuildError(f"Unknown script type: {script.suffix}")

    ctx.build_dir.mkdir(parents=True, exist_ok=True)
    ctx.install_dir.mkdir(parents=True, exist_ok=True)

    header = (
        f"cvcpkg: building {ctx.recipe.name} {ctx.recipe.full_version} "
        f"({ctx.platform}/{ctx.config}/{ctx.link})"
    )
    print(header)
    print(f"cvcpkg: script: {script}")
    print(f"cvcpkg: install dir: {ctx.install_dir}")
    if log_callback:
        log_callback(f"{header}\n")

    if log_callback:
        # Stream output line-by-line so it reaches the server in real time.
        proc = subprocess.Popen(
            cmd,
            cwd=ctx.build_dir.resolve(),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        _log_flush_bytes = 8192
        buf: list[str] = []
        buf_size = 0
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace")
            sys.stdout.write(line)
            buf.append(line)
            buf_size += len(line)
            if buf_size >= _log_flush_bytes:
                log_callback("".join(buf))
                buf.clear()
                buf_size = 0
        if buf:
            log_callback("".join(buf))
        returncode = proc.wait()
    else:
        # Local build — let output go straight to terminal.
        result = subprocess.run(
            cmd,
            cwd=ctx.build_dir.resolve(),
            env=env,
        )
        returncode = result.returncode

    if returncode != 0:
        raise BuildError(f"Build script for {ctx.recipe.name} exited with code {returncode}")

    # Make shared bundles relocatable: rewrite absolute build-tree paths so
    # consumers load the libraries without LD_LIBRARY_PATH/DYLD_*.
    _make_relocatable(ctx)


# ── Test execution ──────────────────────────────────────────────


def _find_bash() -> str:
    """Find a real bash executable, preferring Git Bash over WSL on Windows."""
    if sys.platform == "win32":
        # On Windows, shutil.which("bash") may return WSL bash which fails
        # if no distro is installed.  Prefer Git-for-Windows bash.
        git_bash = (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        )
        if git_bash.is_file():
            return str(git_bash)
    found = shutil.which("bash")
    if found:
        return found
    raise BuildError("bash not found on PATH")


def _find_pwsh(prefix: Path | None = None) -> str:
    """Find pwsh 7, preferring a cvcpkg-provided build under *prefix*.

    Windows recipe build scripts are ``#!/usr/bin/env pwsh`` and use
    pwsh-7-only syntax; the Windows Store alias ``pwsh.exe`` is not usable
    non-interactively.  Prefer the pwsh installed by the ``powershell`` cvcpkg
    recipe into the deps prefix (declare it as a ``host_tool``), so a bare
    build host needs no system pwsh.  Fall back to PATH otherwise -- e.g. when
    bootstrapping the ``powershell`` recipe itself, whose own build.ps1 must
    run under a pre-existing pwsh.
    """
    candidates: list[Path] = []
    if prefix is not None:
        if sys.platform == "win32":
            candidates += [
                prefix / "lib" / "powershell" / "pwsh.exe",
                prefix / "bin" / "pwsh.exe",
            ]
        else:
            candidates.append(prefix / "bin" / "pwsh")
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which("pwsh")
    if found:
        return found
    raise BuildError(
        "pwsh 7 not found -- add the 'powershell' recipe as a host_tool, "
        "or install pwsh 7 on PATH"
    )


def run_test(
    ctx: BuildContext,
    *,
    log_callback: Callable[[str], None] | None = None,
) -> None:
    """Run the recipe's test script if one exists."""
    if not ctx.recipe.test_script:
        return
    test_path = ctx.recipe.recipe_dir / ctx.recipe.test_script
    if not test_path.is_file():
        raise BuildError(f"Test script not found: {test_path}")

    # SSH-delegated Haiku builds: the install tree that came back holds Haiku
    # binaries this Linux-side process cannot execute, so running the test
    # script HERE would test nothing (or, worse, test the builder's own Linux
    # tools and pass).  The Haiku host is still reachable, so the test runs
    # where the artifact runs — and refuses loudly if it cannot.
    from cvcpkg import haikuhost as _haikuhost

    if _haikuhost.should_delegate(ctx.platform, ctx.host_platform):
        _haikuhost.run_haiku_test(ctx, test_path, log_callback=log_callback)
        return

    env = os.environ.copy()
    test_build_prefix = ctx.build_prefix or ctx.prefix
    env["CVC_PREFIX"] = ctx.install_dir.as_posix()
    env["CVC_INSTALL_DIR"] = ctx.install_dir.as_posix()
    env["CVC_DEPS_PREFIX"] = ctx.prefix.as_posix()
    env["CVC_BUILD_PREFIX"] = test_build_prefix.as_posix()
    env["CVC_PLATFORM"] = ctx.platform

    # Mark host-delegated Windows cross builds so test scripts can
    # skip host-only steps (the install tree contains Windows binaries
    # but this test process is running on the Linux side).
    from cvcpkg import winhost as _winhost

    if _winhost.should_delegate(ctx.platform, ctx.host_platform):
        env["CVC_WINHOST"] = "1"

    # Propagate cross-toolchain env vars (CVC_EMSDK_DIR, CVC_WASI_SDK_DIR,
    # etc.) so test scripts can use emcc/node/wasmtime to compile and run
    # cross-compiled test programs.
    if ctx.cross_toolchain_env:
        for var, tpl in ctx.cross_toolchain_env.items():
            env[var] = tpl.replace("${PREFIX}", str(ctx.prefix))

    # Ensure host tools built into the prefix (including cross-toolchain
    # binaries) are on PATH — mirrors _build_env() behaviour.
    #
    # The build prefix has to come first, exactly as it does in _build_env().
    # It was missing here, which meant a HOST TOOL was on PATH while a recipe
    # built and gone by the time its own test ran — so the test silently fell
    # through to whatever the system had. pcre2's test invokes a bare
    # `pkg-config` and got C:\Strawberry\perl\bin\pkg-config (a Perl script)
    # instead of the pkgconf cvcpkg had just staged, then died inside it:
    #
    #     Can't locate Pod/Usage.pm in @INC ... at /c/Strawberry/perl/bin/pkg-config
    #
    # A test that passes only because the machine happens to have a tool is
    # not testing the bundle, so this is worth keeping in lockstep.
    bin_dirs = [
        (test_build_prefix / "bin").as_posix(),
        (ctx.prefix / "bin").as_posix(),
        (ctx.install_dir / "bin").as_posix(),
    ]
    # Deduplicate while preserving order: build_prefix and prefix are the same
    # path when no separation is configured.
    bin_dirs = list(dict.fromkeys(bin_dirs))
    existing_path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join(bin_dirs + ([existing_path] if existing_path else []))

    # Ensure shared-library dependencies (e.g. abseil for protoc) are
    # discoverable at test time.  Include both the component's own lib
    # dir and the shared prefix where dependencies were installed.
    lib_dirs = [
        (ctx.install_dir / "lib").as_posix(),
        (ctx.prefix / "lib").as_posix(),
    ]
    if sys.platform == "darwin":
        existing = env.get("DYLD_LIBRARY_PATH", "")
        env["DYLD_LIBRARY_PATH"] = ":".join(lib_dirs + ([existing] if existing else []))
    else:
        # Haiku reads LIBRARY_PATH, not LD_LIBRARY_PATH — see lib_path_var.
        lib_var = lib_path_var(ctx.platform)
        existing = env.get(lib_var, "")
        env[lib_var] = ":".join(lib_dirs + ([existing] if existing else []))

    bash = _find_bash()
    header = f"cvcpkg: running test for {ctx.recipe.name}"
    print(header)
    if log_callback:
        log_callback(f"{header}\n")

    if log_callback:
        proc = subprocess.Popen(
            [bash, str(test_path)],
            cwd=ctx.install_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        _log_flush_bytes = 8192
        buf: list[str] = []
        buf_size = 0
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace")
            sys.stdout.write(line)
            buf.append(line)
            buf_size += len(line)
            if buf_size >= _log_flush_bytes:
                log_callback("".join(buf))
                buf.clear()
                buf_size = 0
        if buf:
            log_callback("".join(buf))
        returncode = proc.wait()
    else:
        returncode = subprocess.run(
            [bash, str(test_path)],
            cwd=ctx.install_dir,
            env=env,
        ).returncode
    if returncode != 0:
        raise BuildError(f"Test for {ctx.recipe.name} failed with code {returncode}")


def resolve_vm_test_image(ctx: BuildContext, image_name: str) -> Any:
    """Locate the image a ``test.vm`` block wants to boot.

    ``image: self`` resolves against the recipe's own STAGED tree — that is the
    whole point: the artifact under test is the one this build just produced,
    not whatever happens to be installed in the shared prefix.  A named image
    (a recipe testing against some other package's guest) resolves against the
    prefix, where its dependency was already installed.
    """
    from cvcpkg import images as _imgs

    candidates = [ctx.install_dir]
    if ctx.prefix != ctx.install_dir:
        candidates.append(ctx.prefix)
    for root in candidates:
        image_dir = root / _imgs.SHARE_DIR / image_name
        if (image_dir / _imgs.IMAGE_DESCRIPTOR).is_file():
            return _imgs.load_image(image_dir)
    looked = ", ".join(str(c / _imgs.SHARE_DIR / image_name) for c in candidates)
    raise BuildError(
        f"test.vm wants to boot image '{image_name}' but no {_imgs.IMAGE_DESCRIPTOR} "
        f"was found in: {looked}"
    )


def run_vm_test(
    ctx: BuildContext,
    *,
    log_callback: Callable[[str], None] | None = None,
) -> None:
    """Run the recipe's ``test.vm`` block, if it has one.

    Boots the just-built image in a throwaway VM and asserts against it.  A
    builder with no hypervisor SKIPS (reported, green); a guest that fails to
    boot or fails the guest-side script raises :class:`BuildError`, which is
    what makes an image recipe self-testing rather than trust-me.  The VM is
    destroyed on every exit path — see :mod:`cvcpkg.vmtest`.
    """
    if not ctx.recipe.vm_test:
        return
    from cvcpkg import vmtest as _vmtest

    def emit(message: str) -> None:
        print(message)
        if log_callback:
            log_callback(f"{message}\n")

    try:
        spec = _vmtest.VmTestSpec.from_dict(ctx.recipe.vm_test)
    except _vmtest.VmTestError as exc:
        raise BuildError(f"{ctx.recipe.name}: invalid test.vm block: {exc}") from exc
    assert spec is not None  # vm_test was non-empty

    image_name = ctx.recipe.name if spec.image == "self" else spec.image
    image = resolve_vm_test_image(ctx, image_name)

    script_path = ctx.recipe.recipe_dir / spec.script if spec.script else None

    try:
        result = _vmtest.run_vm_test(
            spec=spec,
            image=image,
            script_path=script_path,
            log=emit,
        )
    except _vmtest.VmTestError as exc:
        # Setup problems that are the recipe's fault (declared script missing,
        # no importer metadata) — a bug to fix, not an environment to tolerate.
        raise BuildError(f"{ctx.recipe.name}: VM test could not run: {exc}") from exc

    emit(_vmtest.format_result(image_name, result))
    if result.output:
        emit(result.output)
    if result.leaked:
        emit(
            f"cvcpkg: WARNING: the {image_name} test VM or its imported image may not "
            f"have been destroyed — look for INSTANCES and IMAGES named "
            f"{_vmtest.INSTANCE_PREFIX}* (a leaked image is a multi-gigabyte qcow2 "
            f"sitting in the daemon's store, so check both namespaces)"
        )
    if result.status == _vmtest.FAILED:
        raise BuildError(f"VM test for {ctx.recipe.name} failed: {result.reason}")


# ── Manifest generation ────────────────────────────────────────


def _file_list(root: Path) -> list[str]:
    """Recursively list all files under *root* as relative POSIX paths."""
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files.append(p.relative_to(root).as_posix())
    return files


def _total_size(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def generate_manifest(
    recipe: Recipe,
    install_dir: Path,
    platform: str,
    arch: str,
    config: str,
    link: str,
    *,
    maintainer: str = "",
    all_recipes: dict[str, Recipe] | None = None,
    org_slug: str = "",
    host_tool: bool | None = None,
) -> dict[str, Any]:
    """Generate a bundle manifest.yaml from the recipe + installed tree.

    When *all_recipes* is provided, ``recipe_sha256`` is a transitive
    dependency chain hash instead of a single-file hash.  This makes the
    hash sensitive to changes anywhere in the dependency tree.

    ``host_tool`` flags the bundle as a build-time host tool in the manifest.
    When left as ``None`` it is derived from the recipe (a recipe that
    declares ``cross_toolchain.target_platforms`` is a host tool); pass an
    explicit bool to override.
    """
    files = _file_list(install_dir)
    cmake_packages = recipe.raw.get("package", {}).get("cmake_packages", [])
    pkg_config = recipe.raw.get("package", {}).get("pkg_config", [])
    abi = recipe.raw.get("abi", {})

    # Use ``depends.runtime`` for the manifest (consumer-facing deps).
    # Falls back to ``depends.build`` for recipes that haven't adopted
    # the runtime/build split yet.
    depends_block = recipe.raw.get("depends", {})
    depends = depends_block.get("runtime", depends_block.get("build", []))

    # Normalize dep entries to dicts, filtering by target platform
    dep_list = []
    for d in depends:
        if isinstance(d, str):
            # Support "org/name" shorthand in string deps
            if "/" in d:
                dep_org, dep_name = d.split("/", 1)
                dep_list.append({"name": dep_name, "org": dep_org})
            else:
                dep_list.append({"name": d})
        else:
            plats = d.get("platforms")
            if plats and platform not in plats:
                continue
            # Don't write platforms into the manifest -- it's platform-specific
            entry: dict[str, str] = {"name": d["name"]}
            if d.get("org"):
                entry["org"] = d["org"]
            if d.get("version"):
                entry["version"] = d["version"]
            dep_list.append(entry)

    recipe_block = recipe.raw.get("recipe", {})
    description = recipe_block.get("description", "")
    built_at = datetime.now(timezone.utc).isoformat()
    is_host_tool = host_tool if host_tool is not None else bool(recipe.cross_toolchain_targets)
    manifest: dict[str, Any] = {
        "schema_version": 3,
        "bundle": {
            "name": recipe.name,
            "version": recipe.full_version,
            "upstream_version": recipe.upstream_version,
            "cvc_revision": recipe.cvc_revision,
            "platform": platform,
            "arch": arch,
            "build_type": config,
            "link": link,
            "abi": abi,
            **({"org": org_slug} if org_slug else {}),
            **({"host_tool": True} if is_host_tool else {}),
        },
        "contents": {
            "description": description,
            "files": files,
            "cmake_packages": cmake_packages,
            "pkg_config": pkg_config,
            # Carry the recipe's exclusivity slots into the bundle so an
            # installed prefix knows what it fills without needing the recipe.
            **({"provides": list(recipe.provides)} if recipe.provides else {}),
        },
        "dependencies": {
            "required": dep_list,
        },
        # Top-level virtual-package metadata (siblings of ``bundle:``): the
        # names this bundle satisfies and the host capabilities it requires.
        # Mirrors what BundleManifest.from_dict reads back, and is copied
        # verbatim into each catalog/index entry for capability-ranked
        # resolution.  ``contents.provides`` above is retained for the
        # installed-prefix view.
        **({"provides": list(recipe.provides)} if recipe.provides else {}),
        **(
            {"requires_capabilities": list(recipe.requires_capabilities)}
            if recipe.requires_capabilities
            else {}
        ),
        "integrity": {
            "sha256": "",
            "size_bytes": 0,
            "built_at": built_at,
        },
        "meta": {
            "recipe_sha256": (
                chain_hash(recipe, all_recipes, platform)
                if all_recipes
                else _sha256_file(recipe.recipe_dir / "recipe.yaml")
            ),
            "built_at": built_at,
            "maintainer": maintainer or recipe_block.get("maintainer", "Community"),
            "maintainer_email": recipe_block.get("maintainer_email", ""),
            "description": description,
            "homepage": recipe_block.get("homepage", ""),
            "license": recipe_block.get("license", ""),
            "tags": ",".join(recipe.tags) if recipe.tags else "",
            **({"kind": recipe.kind} if recipe.kind else {}),
        },
        "provenance": {
            "builder_hostname": _platform_module.node(),
            "builder_os": _platform_module.system(),
            "builder_os_version": _platform_module.version(),
            "builder_arch": _platform_module.machine(),
            "python_version": _platform_module.python_version(),
        },
    }
    return manifest


# ── Staging & archiving ─────────────────────────────────────────


def stage_bundle(
    install_dir: Path,
    manifest: dict[str, Any],
    staging_dir: Path,
    recipe_dir: Path | None = None,
    temp_prefixes: Sequence[Path | str] = (),
) -> None:
    """Copy the installed tree, manifest, and recipe into a staging directory.

    *temp_prefixes* names the build-time prefixes (deps / build-tool /
    install dirs) that will not exist wherever the bundle is unpacked;
    console-script shebangs pointing into them are rewritten to relocatable
    form — ``/usr/bin/env`` for POSIX text scripts (:func:`_rewrite_shebangs`),
    ``<launcher_dir>`` for the shebang embedded in Windows Scripts/*.exe
    launchers (:func:`_rewrite_exe_launchers`).  The rewrites run on the
    STAGED copy only: ``pack --from-prefix`` packages a tree the caller
    owns, which must come out of packing byte-identical.
    """
    # Copy entire install tree (preserve symlinks for toolchains like cosmocc)
    if install_dir.is_dir():
        shutil.copytree(install_dir, staging_dir, symlinks=True, dirs_exist_ok=True)

    _rewrite_shebangs(staging_dir, temp_prefixes)
    _rewrite_exe_launchers(staging_dir, temp_prefixes)

    # Write the manifest under a subdirectory named for the bundle itself.
    # Every bundle installs into the same shared prefix, and extraction is a
    # blind merge -- a flat share/libcvc-deps/manifest.yaml would be
    # overwritten by whichever bundle extracted last, leaving every other
    # co-installed package's manifest clobbered and unverifiable.
    meta_dir = staging_dir / "share" / "libcvc-deps" / manifest["bundle"]["name"]
    meta_dir.mkdir(parents=True, exist_ok=True)
    with open(meta_dir / "manifest.yaml", "w") as f:
        yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)

    # Include the recipe that produced this package
    if recipe_dir and recipe_dir.is_dir():
        dest_recipe = meta_dir / "recipe"
        dest_recipe.mkdir(parents=True, exist_ok=True)
        exts = {".yaml", ".sh", ".ps1", ".cmake", ".patch"}
        for f in sorted(recipe_dir.iterdir()):
            if f.is_file() and f.suffix in exts:
                shutil.copy2(f, dest_recipe / f.name)


def _archive_tar_gz(staging_dir: Path, output: Path) -> str:
    """Create a deterministic .tar.gz archive. Returns SHA-256."""
    import gzip
    import io

    # Use a two-step approach: write tar to memory, then gzip with
    # mtime=0 to ensure the gzip header is reproducible across machines.
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tf:
        for entry in sorted(staging_dir.rglob("*")):
            arcname = str(entry.relative_to(staging_dir))
            info = tf.gettarinfo(str(entry), arcname=arcname)
            # Zero timestamps for reproducibility
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            if info.isreg():
                with open(entry, "rb") as fobj:
                    tf.addfile(info, fobj)
            else:
                tf.addfile(info)
    with open(output, "wb") as f_out:
        with gzip.GzipFile(fileobj=f_out, mode="wb", mtime=0) as gz:
            gz.write(tar_buf.getvalue())
    return _sha256_file(output)


def _archive_zip(staging_dir: Path, output: Path) -> str:
    """Create a .zip archive. Returns SHA-256."""
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in sorted(staging_dir.rglob("*")):
            if entry.is_file():
                arcname = str(entry.relative_to(staging_dir))
                # Zero timestamp for reproducibility
                info = zipfile.ZipInfo(arcname, date_time=(2000, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(entry, "rb") as f:
                    zf.writestr(info, f.read())
    return _sha256_file(output)


def create_archive(
    staging_dir: Path,
    output_dir: Path,
    name: str,
    version: str,
    platform: str,
    arch: str,
    config: str,
    link: str,
) -> tuple[Path, str, int]:
    """Archive the staging directory. Returns (path, sha256, size)."""
    stem = f"{name}-{version}-{platform}-{arch}-{config}-{link}"
    output_dir.mkdir(parents=True, exist_ok=True)

    if platform == "windows":
        archive_path = output_dir / f"{stem}.zip"
        sha = _archive_zip(staging_dir, archive_path)
    else:
        archive_path = output_dir / f"{stem}.tar.gz"
        sha = _archive_tar_gz(staging_dir, archive_path)

    size = archive_path.stat().st_size
    return archive_path, sha, size


# ── High-level entry points ────────────────────────────────────


def _mkworkdir(prefix: str, root: Path | None = None) -> Path:
    """Create a temporary work directory.

    When *root* is given the directory is created under *root*
    (which is created if it doesn't exist).  Otherwise the system
    temp directory is used.
    """
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=prefix, dir=root))
    return Path(tempfile.mkdtemp(prefix=prefix))


def _incremental_root() -> Path:
    """Root directory for persisted incremental build trees.

    Used by ``cvcpkg build --incremental`` (see :func:`build_recipe`).  Honours
    ``CVCPKG_INCREMENTAL_DIR`` then ``XDG_CACHE_HOME``, defaulting to
    ``~/.cache/cvcpkg/incremental``.
    """
    env = os.environ.get("CVCPKG_INCREMENTAL_DIR")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "cvcpkg" / "incremental"
    return Path.home() / ".cache" / "cvcpkg" / "incremental"


def _incremental_work_dir(
    name: str, platform: str, config: str, link: str, root: Path | None = None
) -> Path:
    """Stable, reusable work dir keyed by (name, platform, config, link).

    Re-running a build with the same key reuses the persisted build tree so
    CMake recompiles only changed translation units.  The key includes
    platform/config/link so a mismatched build (e.g. a debug build after a
    release one) never reuses the wrong tree — it gets its own directory.

    *root* is the parent directory (``--work-dir`` when set); it defaults to
    :func:`_incremental_root`.
    """
    base = root if root is not None else _incremental_root()
    return base / f"{name}-{platform}-{config}-{link}"


def _write_bytes_preserving_mode(path: Path, data: bytes) -> None:
    """Write *data* to *path* even when the file is read-only.

    Dependency payloads legitimately ship read-only files -- perl installs
    ``lib/<ver>/<arch>/Config.pm`` mode 444 -- and a bare ``write_bytes``
    raises EACCES there.  Because prefix rewriting runs over a whole
    installed dependency tree, one such file aborted the entire dep install
    and took the build with it.

    The original mode is restored afterwards, so rewriting a baked path
    never silently leaves a file more writable than its package intended.
    """
    try:
        path.write_bytes(data)
        return
    except PermissionError:
        pass

    original = path.stat().st_mode
    try:
        path.chmod(original | stat.S_IWUSR)
        path.write_bytes(data)
    finally:
        try:
            path.chmod(original)
        except OSError:
            # Restoring the mode is best-effort: losing it must not mask a
            # successful write, and must not turn one unwritable file into a
            # failed dependency install.
            pass


def _write_text_preserving_mode(path: Path, text: str) -> None:
    """UTF-8 text form of :func:`_write_bytes_preserving_mode`."""
    _write_bytes_preserving_mode(path, text.encode("utf-8"))


def _merge_tree(src: Path, dst: Path) -> None:
    """Recursively merge *src* into *dst*, robust to symlinks and collisions.

    ``shutil.copytree`` cannot merge an install tree into a shared prefix:
    ``symlinks=False`` FOLLOWS links and dies on a dangling one (e.g. ncurses'
    ``lib/libpanel`` on macOS); ``symlinks=True`` recreates links with
    ``os.symlink`` and dies when the link ALREADY exists in *dst* (e.g.
    python311's ``bin/2to3`` after python312 installed it first). Handle both:
    copy symlinks *as* symlinks, replacing any existing dst entry; overwrite
    regular files; recurse into real directories.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        target = dst / entry.name
        if entry.is_symlink():
            if target.is_symlink() or target.exists():
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            os.symlink(os.readlink(entry), target)
        elif entry.is_dir():
            _merge_tree(entry, target)
        else:
            shutil.copy2(entry, target)


def _rewrite_pc_prefixes(target_dir: Path) -> None:
    """Rewrite ``prefix=`` lines in pkg-config ``.pc`` files under *target_dir*.

    When recipes are built into isolated per-component directories and
    then merged into a shared prefix, the ``.pc`` files retain the old
    ``prefix=`` path pointing at the (now deleted) temp directory.
    This helper rewrites the ``prefix=`` line to point at *target_dir*
    so that subsequent recipes find valid paths via ``pkg-config``.
    """
    target_str = str(target_dir)
    for pc_file in target_dir.rglob("*.pc"):
        try:
            text = pc_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines(keepends=True)
        rewritten = False
        for i, line in enumerate(lines):
            if line.startswith("prefix="):
                old_val = line[len("prefix=") :].strip()
                if old_val != target_str:
                    lines[i] = f"prefix={target_str}\n"
                    rewritten = True
                break  # only the first prefix= line matters
        if rewritten:
            _write_text_preserving_mode(pc_file, "".join(lines))


# Regex matching a cvcpkg per-component temp install directory, e.g.
#   /tmp/cvcpkg-automake-ab12cd34/install
#   /var/folders/xx/.../cvcpkg-openssl-7f8g9h0i/install
#   /home/joe/cvcpkg-builder/cvcpkg-prefix-zstd-tl1ob1i9
# The key pattern is a directory component starting with "cvcpkg-".
_TEMP_PREFIX_RE = re.compile(r"(?:/[^\s'\",:;)}\]]+)?/cvcpkg-[A-Za-z0-9_-]+-[A-Za-z0-9_]+/install")


# Data files that commonly bake the configure-time --prefix.  bin/ is scanned
# wholesale (interpreter scripts usually carry no extension), but elsewhere an
# extension allowlist keeps the reads bounded rather than slurping every byte of
# a multi-gigabyte prefix (llvm).  ``.pm`` matters because automake splits its
# baked libdir out into share/automake-X.Y/Automake/Config.pm, and ``.la`` because
# libtool archives carry an absolute ``libdir=``.
_BAKED_PATH_SUFFIXES = frozenset(
    {".pm", ".pl", ".la", ".pc", ".sh", ".m4", ".mk", ".am", ".in", ".cmake", ".py", ".conf"}
)

# Skip anything implausibly large for a script/data file; guards against reading
# a big binary that happens to match the allowlist.
_MAX_REWRITE_BYTES = 4 * 1024 * 1024


def _rewrite_script_prefixes(target_dir: Path) -> None:
    """Rewrite hardcoded temp-install paths in text files under *target_dir*.

    Autotools utilities (``aclocal``, ``automake``, ``libtoolize``, etc.)
    embed absolute ``--prefix`` paths at ``configure`` time.  When
    recipes are built into isolated temp directories and then merged to
    a shared prefix, these embedded paths become stale.

    Scans ``bin/`` plus files whose extension is in ``_BAKED_PATH_SUFFIXES``
    anywhere under *target_dir*, replacing cvcpkg temp install directories with
    *target_dir*.  Such a path always points at a deleted directory, so
    rewriting it is unambiguously correct.

    Looking beyond ``bin/`` matters: rewriting only ``bin/aclocal`` fixes
    aclocal but leaves ``automake`` broken, because its libdir lives in
    ``share/automake-X.Y/Automake/Config.pm`` and it reads ``$libdir/am/*.am``
    at runtime.
    """
    bin_dir = target_dir / "bin"
    target_str = str(target_dir)
    for path in target_dir.rglob("*"):
        if path.parent != bin_dir and path.suffix not in _BAKED_PATH_SUFFIXES:
            continue
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > _MAX_REWRITE_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        # Never touch binaries: the replacement changes length, which would
        # corrupt offsets.  Check the whole file -- a NUL can sit past any
        # fixed-size sniff window.
        if b"\x00" in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            # Decoding with errors="replace" and writing back would silently
            # replace undecodable bytes with U+FFFD, corrupting the file.
            continue
        # Replace via a function, not a string: re treats backslashes in a
        # replacement as escapes, so a Windows target_str (C:\Users\...) raises
        # "bad escape \U".  A callable's return value is used literally.
        new_text = _TEMP_PREFIX_RE.sub(lambda _m: target_str, text)
        if new_text != text:
            _write_text_preserving_mode(path, new_text)


# A path component created by any of cvcpkg's mkdtemp calls: builder jobs
# (cvcpkg-job-<recipe>-<id>), their prefixes (cvcpkg-prefix-<recipe>-<id>),
# local pack work dirs (cvcpkg-<recipe>-<id>), and the builder work root
# itself (/tmp/cvcpkg-builder).  Unlike _TEMP_PREFIX_RE this deliberately
# does NOT require a trailing /install: a pip shebang bakes the *deps
# prefix* (.../cvcpkg-job-x-y/bin/python3.11), which that regex misses.
_TEMP_SHEBANG_RE = re.compile(r"/cvcpkg-[^/]+/")

# Script dirs scanned for shebangs.  POSIX layouts only: a Windows console
# script is a Scripts/*.exe launcher whose interpreter path is embedded
# INSIDE the binary (pip/distlib append shebang + zip to a stub .exe);
# those get their own binary-aware pass, :func:`_rewrite_exe_launchers`.
# The ``#!`` sniff below skips them regardless (a PE starts ``MZ``).
_SHEBANG_DIRS = ("bin", "sbin", "libexec")

# pip's /bin/sh POLYGLOT wrapper, emitted instead of a plain ``#!`` whenever the
# interpreter path is too long for the kernel's shebang limit (~128 bytes on
# Linux) or contains a space::
#
#     #!/bin/sh
#     '''exec' /tmp/cvcpkg-builder/cvcpkg-job-<recipe>-<id>/.../bin/python3.11 "$0" "$@"
#     ' '''
#
# The interpreter is on line TWO, so the line-one rewrite below never saw it and
# passed the script through as a harmless ``#!/bin/sh``.
#
# Which form pip emits is decided by the TOTAL path length against the kernel's
# 128-byte BINPRM_BUF_SIZE, and that path is
# ``<work root>/cvcpkg-job-<name>-<id>/cvcpkg-prefix-<name>-<id>/bin/pythonX.Y``
# -- so it depends on the BUILDER as much as on the recipe.  The fleet's work
# roots differ by 15 characters (``/tmp/cvcpkg-builder/`` on star-*/rebota/lat
# vs ``/var/lib/cvcpkg-builder/cvcpkg-org/`` on prettyhatemachine), a window wide
# enough that one recipe emits the plain form on one host and the polyglot on
# another: torch-cp311 lands at 101 bytes on a /tmp builder, while
# torch-cp311-cuda -- five characters longer, and buildable ONLY on the CUDA
# host -- lands at 126 and goes polyglot.  Two corollaries: this is not a
# property of the recipe alone, and an audit over published artifacts
# UNDER-counts, because most packages were last built on a /tmp builder.
#
# Both readings survive substituting the interpreter token: sh parses ``'''exec'``
# as ``'' + 'exec'`` and execs the rest, while Python sees lines 2-3 as one
# triple-quoted string expression.
_SH_POLYGLOT_RE = re.compile(r"^('''exec' +)(\S+)( .*)$")


def _rewrite_sh_polyglot(
    path: Path,
    data: bytes,
    head_end: int,
    is_temp: Callable[[str], bool],
) -> bool:
    """Rewrite the interpreter inside pip's ``/bin/sh`` polyglot wrapper.

    *data* is the whole script, *head_end* the index of the first newline (end
    of the ``#!`` line).  Returns True when the file was rewritten.

    Only the interpreter TOKEN on line two is replaced, with the same
    ``/usr/bin/env <basename>`` form the plain-shebang path uses, so the sh
    reading (``exec /usr/bin/env python3.11 "$0" "$@"``) and the Python reading
    (lines 2-3 as one triple-quoted string) both keep working.
    """
    if head_end >= len(data):
        return False
    tail = data[head_end + 1 :]
    line2_end = tail.find(b"\n")
    if line2_end == -1:
        return False
    try:
        line2 = tail[:line2_end].decode("utf-8")
    except UnicodeDecodeError:
        return False
    m = _SH_POLYGLOT_RE.match(line2)
    if not m or not is_temp(m.group(2)):
        return False
    new_line2 = f"{m.group(1)}/usr/bin/env {m.group(2).rsplit('/', 1)[-1]}{m.group(3)}"
    _write_bytes_preserving_mode(
        path,
        data[: head_end + 1] + new_line2.encode() + tail[line2_end:],
    )
    return True


def _rewrite_shebangs(root: Path, temp_prefixes: Sequence[Path | str] = ()) -> None:
    """Rewrite build-prefix shebangs under *root*'s script dirs to env form.

    pip writes every console script (``bin/cython``, ``bin/pybind11-config``,
    ...) with a shebang naming the exact interpreter that ran the install —
    inside cvcpkg that is the job's ephemeral deps prefix::

        #!/tmp/cvcpkg-builder/cvcpkg-job-pybind11-cp311-abc123/bin/python3.11

    a path that exists on no consumer machine, so the staged script cannot
    execute from an installed prefix at all.  Recipes used to shim around it
    case-by-case (numpy fabricates a ``cython`` wrapper, contourpy sidesteps
    the dead ``pybind11-config`` via ``python -m pybind11 --pkgconfigdir``);
    rewriting here, on the staged tree, fixes every bundle in one place.

    The rewrite preserves the interpreter's basename and anchors it at env —
    ``#!/usr/bin/env python3.11`` — the same form the meson recipe has always
    used for its own wrapper.  The versioned name is shipped by the matching
    interpreter recipe, and the prefix ``bin/`` is on PATH both under an
    activated consumer prefix and inside a builder job (_build_env), so the
    script resolves its own column's interpreter in either context.

    A shebang is rewritten only when its interpreter clearly cannot survive
    packaging: it lives under one of *temp_prefixes* (the build's deps /
    build-tool / install prefixes) or under any ``cvcpkg-*`` directory
    component (every dir shape cvcpkg mkdtemps).  System interpreters
    (``#!/bin/sh``, ``#!/usr/bin/perl``) and already-relocatable env forms
    pass through untouched -- EXCEPT a ``#!/bin/sh`` that turns out to be
    pip's polyglot wrapper, whose real interpreter hides on line two
    (:func:`_rewrite_sh_polyglot`); that one is a build-prefix path like any
    other and is rewritten in place.  A matching shebang that carries interpreter
    *arguments* is left alone with a warning: Linux hands everything after
    the interpreter to it as ONE argument, so ``/usr/bin/env NAME ARG``
    would trade a broken path for a broken invocation.
    """
    prefixes = tuple(str(p).rstrip("/") for p in temp_prefixes if p)

    def _is_temp(interp: str) -> bool:
        if any(interp.startswith(p + "/") for p in prefixes):
            return True
        return _TEMP_SHEBANG_RE.search(interp) is not None

    count = 0
    for sub in _SHEBANG_DIRS:
        subdir = root / sub
        if not subdir.is_dir():
            continue
        for path in sorted(subdir.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                if path.stat().st_size > _MAX_REWRITE_BYTES:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if not data.startswith(b"#!"):
                continue
            newline_at = data.find(b"\n")
            head_end = newline_at if newline_at != -1 else len(data)
            try:
                line = data[2:head_end].decode("utf-8").strip()
            except UnicodeDecodeError:
                continue
            fields = line.split(None, 1)
            if not fields:
                continue
            interp = fields[0]
            if not _is_temp(interp):
                # Not a build-prefix interpreter on line one -- but pip may have
                # hidden one on line two behind a /bin/sh polyglot (see
                # _SH_POLYGLOT_RE).  Rewriting the token in place keeps both the
                # sh and the Python reading intact.
                rewritten = _rewrite_sh_polyglot(path, data, head_end, _is_temp)
                if rewritten:
                    count += 1
                continue
            if len(fields) > 1:
                print(
                    f"cvcpkg: WARNING: {path.relative_to(root)}: build-prefix shebang"
                    f" carries interpreter arguments, left unrewritten: #!{line}"
                )
                continue
            new_head = f"#!/usr/bin/env {interp.rsplit('/', 1)[-1]}".encode()
            rest = data[head_end:] if newline_at != -1 else b"\n"
            _write_bytes_preserving_mode(path, new_head + rest)
            count += 1
    if count:
        print(f"cvcpkg: rewrote {count} build-prefix shebang(s) to /usr/bin/env form")


# Script dirs scanned for Windows launcher .exes, and how far back from the
# appended archive the shebang line is searched for.  distlib reads the line
# into a MAX_PATH buffer, so a rewritten line must stay comfortably below
# 260 bytes -- ours is ~30.
_LAUNCHER_DIRS = ("Scripts", "bin")
_LAUNCHER_SHEBANG_WINDOW = 512
_LAUNCHER_MAX_SHEBANG = 250


def _win_norm(path_str: str) -> str:
    """Normalize a Windows-ish path for comparison: forward slashes, casefolded."""
    return path_str.replace("\\", "/").rstrip("/").casefold()


def _parse_launcher_shebang(seg: bytes) -> tuple[str, str] | None:
    """Split a launcher shebang segment into (interpreter, args).

    *seg* is the raw bytes between the stub PE image and the appended
    archive: ``#!<interpreter>[ args]\\r?\\n``.  The interpreter may be
    double-quoted (pip quotes paths containing spaces).  Returns None when
    the segment is not decodable as the UTF-8 line the stub itself expects.
    """
    if not seg.startswith(b"#!") or not seg.endswith(b"\n"):
        return None
    try:
        body = seg[2:].rstrip(b"\r\n").decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if body.startswith('"'):
        closing = body.find('"', 1)
        if closing == -1:
            return None
        return body[1:closing], body[closing + 1 :].strip()
    # Unquoted: the stub delimits the interpreter at the last ``.exe``, not
    # at whitespace, so mirror that rather than splitting on spaces.
    lowered = body.casefold()
    end = lowered.rfind(".exe")
    while end != -1:
        after = body[end + 4 : end + 5]
        if after in ("", " ", "\t"):
            return body[: end + 4], body[end + 4 :].strip()
        end = lowered.rfind(".exe", 0, end)
    # No .exe anywhere: /usr/bin/env form, or something we don't understand.
    fields = body.split(None, 1)
    if not fields:
        return None
    return fields[0], fields[1] if len(fields) > 1 else ""


def _rewrite_exe_launchers(root: Path, temp_prefixes: Sequence[Path | str] = ()) -> None:
    """Rewrite build-prefix interpreter paths inside staged Scripts/*.exe.

    The Windows sibling of :func:`_rewrite_shebangs`.  pip builds each
    console script as a launcher .exe of three concatenated segments
    (distlib's ``ScriptMaker._write_script``)::

        <stub PE image> #!C:\\...\\cvcpkg-job-x-y\\python\\python.exe\\r\\n <zip of __main__.py>

    The embedded path names the interpreter that ran the install -- the
    job's ephemeral build prefix -- so on any consumer machine the launcher
    exits without running anything, same disease as the POSIX shebangs but
    with the path INSIDE the binary.

    The rewrite is a pure splice.  The stub locates the archive FROM THE END
    of the file (end-of-central-directory scan; distlib writes the zip with
    offsets relative to its own start) and then takes the line immediately
    preceding it as the shebang, so the segment between PE image and archive
    may change length freely as long as it stays newline-terminated and
    adjacent.  Python's zipimport recomputes the same way on the child side.

    The replacement anchors at the stub's own location::

        #!<launcher_dir>\\..\\python.exe

    ``<launcher_dir>\\`` is a literal prefix the stub resolves against its
    own directory (distlib's SUPPORT_RELATIVE_PATH, in every launcher since
    0.3.0; verified present in the stubs pip 25.0.1 and 26.2.1 embed, and
    exercised end-to-end under wine).  From ``<prefix>\\Scripts`` that
    reaches the interpreter at the merged prefix root, where the Windows
    python recipes stage python.exe (``PC\\layout``) -- mirroring the POSIX
    pass's reliance on the sibling ``bin/`` interpreter, but with no PATH
    contract at all: the stub's alternative ``/usr/bin/env NAME`` form
    hard-errors when NAME is not on PATH, and nothing guarantees a consumer
    prefix ROOT (not ``Scripts``) is.  The interpreter basename is
    preserved (python.exe vs pythonw.exe, versioned names).

    Only launchers whose interpreter clearly cannot survive packaging are
    touched, by the same rules as the POSIX pass but compared with Windows
    semantics (either slash, case-insensitive).  Already-relocatable forms
    (``<launcher_dir>``, ``/usr/bin/env``) pass through, which also makes
    the rewrite idempotent.  A matching launcher carrying interpreter
    arguments is left alone with a warning, mirroring the POSIX pass.
    """
    import io

    prefixes = tuple(_win_norm(str(p)) for p in temp_prefixes if p)

    def _is_temp(interp: str) -> bool:
        n = _win_norm(interp)
        if any(n.startswith(p + "/") for p in prefixes):
            return True
        return _TEMP_SHEBANG_RE.search(n) is not None

    count = 0
    for sub in _LAUNCHER_DIRS:
        subdir = root / sub
        if not subdir.is_dir():
            continue
        for path in sorted(subdir.rglob("*.exe")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                if path.stat().st_size > _MAX_REWRITE_BYTES:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if not data.startswith(b"MZ"):
                continue
            try:
                zf = zipfile.ZipFile(io.BytesIO(data))
                names = zf.namelist()
                # min() over header offsets, not the EOCD's central-directory
                # offset: zipfile has already folded the prepended-data delta
                # into header_offset, so this is the archive start in file
                # coordinates regardless of how the writer counted offsets.
                arc_start = min(zi.header_offset for zi in zf.infolist())
            except Exception:
                continue  # no appended archive: a real tool .exe, not a launcher
            if "__main__.py" not in names or not data.startswith(b"PK\x03\x04", arc_start):
                continue
            window_at = max(0, arc_start - _LAUNCHER_SHEBANG_WINDOW)
            sheb_rel = data.rfind(b"#!", window_at, arc_start)
            if sheb_rel == -1:
                continue
            seg = data[sheb_rel:arc_start]
            parsed = _parse_launcher_shebang(seg)
            if parsed is None:
                continue
            interp, args = parsed
            if interp.startswith("<launcher_dir>") or interp == "/usr/bin/env":
                continue
            if not _is_temp(interp):
                continue
            if args:
                print(
                    f"cvcpkg: WARNING: {path.relative_to(root)}: build-prefix launcher"
                    f" carries interpreter arguments, left unrewritten: #!{interp} {args}"
                )
                continue
            basename = interp.replace("\\", "/").rsplit("/", 1)[-1]
            if not basename:
                continue
            # Anchor at the launcher's own directory and climb to the staged
            # root, where the interpreter lives in the merged install prefix.
            depth = len(path.parent.relative_to(root).parts)
            new_interp = "<launcher_dir>\\" + "..\\" * depth + basename
            nl = b"\r\n" if seg.endswith(b"\r\n") else b"\n"
            new_seg = b"#!" + new_interp.encode() + nl
            if len(new_seg) > _LAUNCHER_MAX_SHEBANG:
                print(
                    f"cvcpkg: WARNING: {path.relative_to(root)}: rewritten launcher"
                    f" shebang would exceed the stub's line buffer, left unrewritten"
                )
                continue
            new_data = data[:sheb_rel] + new_seg + data[arc_start:]
            try:
                bad = zipfile.ZipFile(io.BytesIO(new_data)).testzip()
            except Exception:
                bad = "<unreadable>"
            if bad is not None:
                print(
                    f"cvcpkg: WARNING: {path.relative_to(root)}: launcher rewrite"
                    f" produced an unreadable archive ({bad}), left unrewritten"
                )
                continue
            _write_bytes_preserving_mode(path, new_data)
            count += 1
    if count:
        print(f"cvcpkg: rewrote {count} launcher exe(s) to <launcher_dir> form")


def build_recipe(
    recipe_dir: Path,
    *,
    platform: str = "",
    config: str = "release",
    link: str = "shared",
    prefix: Path | None = None,
    keep_build_dir: bool = False,
    host_platform: str = "",
    work_dir_root: Path | None = None,
    cross_toolchain_env: dict[str, str] | None = None,
    build_prefix: Path | None = None,
    host_tools_prefix: Path | None = None,
    incremental: bool = False,
    log_callback: Callable[[str], None] | None = None,
) -> BuildContext:
    """Build a single recipe. Returns the BuildContext.

    *work_dir_root*, when given, is the parent directory in which the
    temporary work directory is created.  Otherwise the system temp
    directory (``$TMPDIR`` / ``/tmp``) is used.

    *incremental* keeps a STABLE work/build tree keyed by (recipe name,
    platform, config, link) under *work_dir_root* (or :func:`_incremental_root`
    when unset) and does NOT wipe the build dir afterwards, so a re-run
    recompiles only changed translation units.  An already-staged source tree
    is reused as-is (no re-fetch, no re-patch).  The default (non-incremental)
    path uses a fresh temp dir and cleans the build tree for a reproducible
    from-scratch build.

    *build_prefix* is the separate prefix holding the build-dependency
    closure (host tools, cross-toolchains, staged source packages) so the
    deliverable *prefix* ships only the runtime closure.  *host_tools_prefix*
    is a deprecated alias retained for callers written against the earlier
    host-tools-only separation.
    """
    if build_prefix is None:
        build_prefix = host_tools_prefix
    recipe = Recipe.load(recipe_dir)
    if not platform:
        platform = detect_platform()

    # Fail fast on a haiku job this builder cannot delegate.  run_build checks
    # again (it is the last line of defence, and other callers reach it
    # directly), but by then the source has been downloaded, extracted and
    # patched — minutes of work and a multi-gigabyte tree for a job whose
    # outcome was already decided by a missing CVCPKG_HAIKU_SSH.
    from cvcpkg import haikuhost as _haikuhost

    _haikuhost.ensure_delegatable(platform, host_platform)

    # wasm/wasi/cosmo only support static linking — shared libraries are
    # impossible in these environments.  Cosmopolitan produces one-file
    # Actually Portable Executables that statically link everything.
    if platform in ("wasm", "wasm-mt", "wasi", "cosmo"):
        link = "static"

    if incremental:
        work_dir = _incremental_work_dir(recipe.name, platform, config, link, work_dir_root)
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        work_dir = _mkworkdir(f"cvcpkg-{recipe.name}-", work_dir_root)

    # Publish liveness into the scratch root so the fleet's disk reaper can
    # tell an in-flight build from a leaked one.  Without it the tree looks
    # abandoned within minutes -- the top directory's mtime stops advancing as
    # soon as build/ and install/ exist -- and a long build gets rm -rf'd out
    # from under itself.  See cvcpkg/heartbeat.py and
    # scripts/reap-stale-build-dirs.sh.
    with watched(work_dir, label=recipe.name):
        install_dir = work_dir / "install"
        build_dir = work_dir / "build"

        # Incremental re-runs reuse an already-staged source tree (recorded in a
        # marker) rather than re-fetching/re-extracting and re-patching — the latter
        # would fail (extract into an existing dir, patch an already-patched tree).
        # Vendored/local sources always build in place: fetch_source returns the
        # repo tree itself, never a copy, so this is a no-op re-fetch for them.
        source_marker = work_dir / ".cvcpkg-source"
        source_dir: Path | None = None
        if incremental and source_marker.is_file():
            staged = Path(source_marker.read_text(encoding="utf-8").strip())
            if staged.is_dir():
                source_dir = staged
        if source_dir is None:
            source_dir = fetch_source(recipe, work_dir, platform=platform)
            if recipe.patches:
                apply_patches(recipe, source_dir)
            if incremental:
                source_marker.write_text(str(source_dir), encoding="utf-8")

        ctx = BuildContext(
            recipe=recipe,
            platform=platform,
            config=config,
            link=link,
            prefix=prefix or install_dir,
            source_dir=source_dir,
            build_dir=build_dir,
            install_dir=install_dir,
            work_dir=work_dir,
            keep_build_dir=keep_build_dir,
            host_platform=host_platform,
            cross_toolchain_env=cross_toolchain_env or {},
            build_prefix=build_prefix,
        )

        run_build(ctx, log_callback=log_callback)

        if recipe.test_script:
            run_test(ctx, log_callback=log_callback)

        # Boot the artifact we just built and assert against it, if the recipe
        # asked for that.  Runs AFTER the host-side test and BEFORE staging, so a
        # guest that cannot boot never becomes a bundle.
        if recipe.vm_test:
            run_vm_test(ctx, log_callback=log_callback)

        # If the caller supplied an explicit --prefix that differs from the
        # per-recipe isolated install_dir, mirror install_dir into it so the
        # user's prefix actually gets populated. Without this, `cvcpkg build
        # --prefix X` and the source-fallback install path leave X empty.
        if prefix is not None and prefix != install_dir and install_dir.is_dir():
            prefix.mkdir(parents=True, exist_ok=True)
            # Preserve symlinks (don't follow them): install trees contain relative
            # version symlinks (libfoo.dylib -> libfoo.N.dylib) and occasionally a
            # dangling one; following them duplicates content and crashes on broken
            # links. Matches the staging copy above.
            _merge_tree(install_dir, prefix)

        if not keep_build_dir and not incremental:
            # Clean up build dir but keep install.  Incremental builds deliberately
            # keep it so the next run recompiles only what changed.
            if build_dir.is_dir():
                shutil.rmtree(build_dir, ignore_errors=True)

        return ctx


def pack_recipe(
    recipe_dir: Path,
    *,
    platform: str = "",
    arch: str = "",
    config: str = "release",
    link: str = "shared",
    prefix: Path | None = None,
    output_dir: Path | None = None,
    keep_build_dir: bool = False,
    maintainer: str = "",
    work_dir_root: Path | None = None,
    log_callback: Callable[[str], None] | None = None,
    host_platform: str = "",
    cross_toolchain_env: dict[str, str] | None = None,
    cvc_revision: int | None = None,
) -> tuple[Path, str, int]:
    """Build + package a recipe. Returns (archive_path, sha256, size).

    *cvc_revision*, when set, overrides the recipe's committed revision for
    this bundle's manifest and archive name (``cvcpkg pack --bump``).  The
    recipe on disk is not modified.

    A platform-independent recipe (all matrix entries ``platform: any``) is
    always packaged as a single ``any``/``noarch`` bundle, regardless of the
    host it is built on and of the ``platform``/``arch`` the caller passes.  A
    build can't literally run "on any", so it still compiles/installs on a
    concrete host; only the resulting package's identity is noarch.  This is
    what lets ``builds submit-dag`` schedule such a recipe once and publish it
    once, rather than fanning out an arch-pinned bundle per host.
    """
    from cvcpkg.platform import detect_arch, detect_platform

    recipe = Recipe.load(recipe_dir)

    if not arch:
        arch = detect_arch()
    # The platform this bundle is being built FOR; empty means "this host".
    target_platform = platform or detect_platform()
    # The host we actually build on. "any" is not a real host, so fall back to
    # the detected platform for the compile/install step.
    build_platform = detect_platform() if target_platform == "any" else target_platform
    # The package identity follows the matrix entry that drives THIS build: a
    # target served by the `any` fallback publishes one noarch bundle shared by
    # every such target, a target with its own entry publishes under that name.
    is_any = _serves_target_via_any(recipe, target_platform, host_platform)
    pkg_platform = "any" if is_any else build_platform
    pkg_arch = "noarch" if is_any else arch

    if output_dir is None:
        output_dir = Path.cwd() / "dist"

    ctx = build_recipe(
        recipe_dir,
        platform=build_platform,
        config=config,
        link=link,
        prefix=prefix,
        keep_build_dir=keep_build_dir,
        work_dir_root=work_dir_root,
        log_callback=log_callback,
        host_platform=host_platform,
        cross_toolchain_env=cross_toolchain_env,
    )

    # Stamp the bundle at the caller-chosen revision (``--bump``).  Recipe is a
    # mutable dataclass, and full_version derives from cvc_revision, so this one
    # assignment flows into both the manifest and the archive filename below.
    if cvc_revision is not None:
        ctx.recipe.cvc_revision = cvc_revision

    # ── glibc floor gate (linux) ────────────────────────────────────────
    # glibc is backward- but not forward-compatible, so a bundle built on a
    # newer glibc than a consumer's simply refuses to start:
    #     version `GLIBC_2.38' not found (required by libpython3.13t.so.1.0)
    # On a heterogeneous fleet that is decided by WHICH BUILDER picked the job,
    # which makes it intermittent and very hard to attribute — it silently
    # broke the cp313t python column (built on the one 2.39 host, unusable on
    # the four 2.35 ones). Verify here, where the artifact and the cause are
    # both in hand, rather than at some consumer months later. This is the
    # auditwheel step of cvcpkg's manylinux analogue; the routing half lives in
    # submit-dag (jobs require a glibc<floor> builder capability).
    if pkg_platform == "linux":
        from cvcpkg import glibc as _glibc

        _ok, _req, _msg = _glibc.check_floor(ctx.install_dir)
        if not _ok:
            raise BuildError(f"{ctx.recipe.name}: glibc floor violation — {_msg}")
        if _req is not None:
            print(f"cvcpkg: glibc floor OK — {_msg}")

    # ── kind: image layout gate ─────────────────────────────────────────
    # An image package's staged tree is merged into a SHARED prefix, so a file
    # left at the root of the install dir lands at the prefix root under a
    # generic name (metadata.yaml, README.md) and the second image package
    # clobbers the first.  Confine every image to share/<name>/ and require a
    # schema-valid image.yaml there, so `cvcpkg image` can find it and a shell
    # consumer can derive the disk path from the package name alone.  Checked
    # against the REAL staged tree here — package.files is only declarative.
    if ctx.recipe.kind == "image":
        from cvcpkg import images as _images

        _img_errors = _images.check_staged_image_tree(ctx.install_dir, ctx.recipe.name)
        if _img_errors:
            raise BuildError(
                f"{ctx.recipe.name}: kind 'image' layout violation:\n  " + "\n  ".join(_img_errors)
            )
        print(f"cvcpkg: image layout OK — share/{ctx.recipe.name}/ (image.yaml validated)")

    manifest = generate_manifest(
        ctx.recipe,
        ctx.install_dir,
        pkg_platform,
        pkg_arch,
        ctx.config,
        ctx.link,
        maintainer=maintainer,
    )

    staging = ctx.work_dir / "staging"
    # parents/exist_ok: defensive against a work_dir that a /tmp reaper (or,
    # historically, a concurrent same-recipe job's over-broad cleanup) may
    # have disturbed — fail later in stage_bundle with a clearer cause than a
    # bare FileNotFoundError on mkdir.
    staging.mkdir(parents=True, exist_ok=True)
    stage_bundle(
        ctx.install_dir,
        manifest,
        staging,
        recipe_dir=ctx.recipe.recipe_dir,
        temp_prefixes=(ctx.prefix, ctx.build_prefix, ctx.install_dir),
    )

    archive_path, sha256, size = create_archive(
        staging,
        output_dir,
        ctx.recipe.name,
        ctx.recipe.full_version,
        pkg_platform,
        pkg_arch,
        ctx.config,
        ctx.link,
    )

    print(f"cvcpkg: packed {archive_path.name} ({size:,} bytes)")
    print(f"cvcpkg: sha256: {sha256}")

    # Cleanup
    if not keep_build_dir and ctx.work_dir.is_dir():
        shutil.rmtree(ctx.work_dir, ignore_errors=True)

    return archive_path, sha256, size


def pack_from_prefix(
    recipe_dir: Path,
    prefix: Path,
    *,
    platform: str = "",
    arch: str = "",
    config: str = "release",
    link: str = "shared",
    version_override: str = "",
    output_dir: Path | None = None,
    maintainer: str = "",
    org_slug: str = "",
    cvc_revision: int | None = None,
) -> tuple[Path, str, int]:
    """Package an already-installed prefix as if built by :func:`pack_recipe`.

    Downstream projects that build with their own toolchain (e.g. libcvc's
    CMake superbuild) can stage their install tree, then hand it to
    cvcpkg for archive layout, manifest generation, and sha256/size
    accounting — the same code path :func:`pack_recipe` and the server
    already trust. This avoids duplicating manifest schema knowledge in
    every downstream CI script.

    Args:
        recipe_dir: Directory containing ``recipe.yaml`` with the
            metadata (name, deps, cmake_packages, tags, …).
        prefix: Directory containing the already-installed tree
            (``prefix/bin``, ``prefix/lib``, ``prefix/include``, …).
        platform / arch: Auto-detected when empty. Downstream typically
            passes these explicitly to match the built binaries.
        config: Build config tag (``release`` / ``debug`` / …).
        link: ``shared`` or ``static``.
        version_override: If set, replaces ``recipe.upstream_version``
            in the emitted manifest and archive filename. Downstream
            projects that derive their version from git/CMake pass this
            so they don't need to edit the recipe on every release.
            The ``+cvc.<rev>`` suffix from the recipe is preserved.
        output_dir: Where to write the archive. Defaults to ``./dist``.
        maintainer: Overrides ``recipe.maintainer`` in the manifest.
        org_slug: Organisation slug recorded in the manifest.
        cvc_revision: If set, overrides the recipe's committed revision for
            this bundle (``cvcpkg pack --bump``).  Composes with
            ``version_override``: the upstream part is replaced and the
            revision is set independently.  The recipe on disk is untouched.

    Returns:
        ``(archive_path, sha256, size_bytes)`` — same shape as
        :func:`pack_recipe`.
    """
    from cvcpkg.platform import detect_arch

    if not prefix.is_dir():
        raise RecipeError(f"prefix directory does not exist: {prefix}")

    recipe = Recipe.load(recipe_dir)

    if version_override:
        # Preserve the recipe's ``+cvc.<rev>`` cvc_revision suffix so the
        # archive name and manifest stay consistent with the rest of the
        # ecosystem.
        recipe = replace(recipe, upstream_version=str(version_override))

    if cvc_revision is not None:
        recipe = replace(recipe, cvc_revision=cvc_revision)

    if not platform:
        platform = detect_platform()
    if not arch:
        arch = detect_arch()

    # wasm/wasi/cosmo never link shared; keep the invariant used by build_recipe.
    if platform in ("wasm", "wasm-mt", "wasi", "cosmo"):
        link = "static"

    if output_dir is None:
        output_dir = Path.cwd() / "dist"

    manifest = generate_manifest(
        recipe,
        prefix,
        platform,
        arch,
        config,
        link,
        maintainer=maintainer,
        org_slug=org_slug,
    )

    staging = Path(tempfile.mkdtemp(prefix=f"cvcpkg-pack-{recipe.name}-"))
    try:
        stage_bundle(
            prefix,
            manifest,
            staging,
            recipe_dir=recipe.recipe_dir,
            temp_prefixes=(prefix,),
        )
        archive_path, sha256, size = create_archive(
            staging,
            output_dir,
            recipe.name,
            recipe.full_version,
            platform,
            arch,
            config,
            link,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    print(f"cvcpkg: packed {archive_path.name} ({size:,} bytes)")
    print(f"cvcpkg: sha256: {sha256}")

    return archive_path, sha256, size


# ── Recipe listing / inspection ─────────────────────────────────

# ── Dependency resolution ───────────────────────────────────────


def _dep_qualified_name(d: str | dict) -> str:
    """Return the qualified name for a dependency entry.

    Supports:
    - Plain string: ``"zlib"``
    - Org-qualified string: ``"myorg/custom-lib"``
    - Dict with optional ``org``: ``{"name": "lib", "org": "myorg"}``
    """
    if isinstance(d, str):
        return d  # already plain or "org/name"
    org = d.get("org", "")
    return qualified_name(d["name"], org)


def _dep_names(recipe: Recipe, platform: str = "") -> list[str]:
    """Extract all dependency names from a recipe for build ordering.

    If *platform* is given, dependencies with a ``platforms`` list that
    does not include *platform* are skipped.

    Collects entries from ``depends.build`` (build-only),
    ``depends.runtime`` (consumer-facing), and ``depends.host_tools``
    so that all prerequisites are built before this recipe.

    Names are returned in qualified form (``org/name``) when the
    dependency specifies an organization.
    """
    depends = recipe.raw.get("depends", {})
    build_deps = depends.get("build", [])
    runtime_deps = depends.get("runtime", [])
    host_tools = depends.get("host_tools", [])
    names: list[str] = []
    for d in build_deps + runtime_deps:
        if isinstance(d, str):
            names.append(d)
        elif isinstance(d, dict):
            plats = d.get("platforms")
            if plats and platform and platform not in plats and "any" not in plats:
                continue
            names.append(_dep_qualified_name(d))
    for t in host_tools:
        if isinstance(t, str):
            names.append(t)
        elif isinstance(t, dict):
            names.append(_dep_qualified_name(t))
    return names


def _dep_names_for_role(recipe: Recipe, role: str, platform: str = "") -> set[str]:
    """Dependency names of *recipe* for a single role.

    *role* is one of ``build``, ``runtime`` or ``host_tools``.  Platform-scoped
    entries (``platforms: [...]``) that exclude *platform* are skipped, matching
    :func:`_dep_names`.
    """
    entries = recipe.raw.get("depends", {}).get(role, []) or []
    names: set[str] = set()
    for d in entries:
        if isinstance(d, str):
            names.add(d)
        elif isinstance(d, dict):
            plats = d.get("platforms")
            if plats and platform and platform not in plats and "any" not in plats:
                continue
            names.add(_dep_qualified_name(d))
    return names


def resolve_dep_closures(
    targets: list[str],
    all_recipes: dict[str, Recipe],
    platform: str = "",
) -> tuple[set[str], set[str]]:
    """Split the dependency graph of *targets* by placement role.

    Placement is decided by the dependency *edge*, not by what a package is --
    ``platform: any`` and "source" packages are not special.  Returns
    ``(runtime_closure, build_closure)``:

    - **runtime_closure** — reachable from a target through ``depends.runtime``
      edges only.  These ship, so they belong in the install prefix.
    - **build_closure** — reachable only by traversing at least one build edge
      (``depends.build`` / ``depends.host_tools``), plus everything those deps
      themselves need in order to function.  Build-time only, so they belong in
      the build prefix and are stripped on install unless kept.

    A package reachable both ways lands in the runtime closure (it ships
    regardless) and stays visible to the build.  *targets* appear in neither
    set: the caller builds them into the deliverable prefix.
    """

    def _runtime_of(name: str) -> set[str]:
        r = all_recipes.get(name)
        return _dep_names_for_role(r, "runtime", platform) if r else set()

    def _buildish_of(name: str) -> set[str]:
        r = all_recipes.get(name)
        if not r:
            return set()
        return _dep_names_for_role(r, "build", platform) | _dep_names_for_role(
            r, "host_tools", platform
        )

    # 1. Runtime closure: runtime edges only, from the targets.
    runtime_closure: set[str] = set()
    queue = [d for t in targets for d in _runtime_of(t)]
    while queue:
        n = queue.pop()
        if n in runtime_closure:
            continue
        runtime_closure.add(n)
        queue.extend(_runtime_of(n))

    # 2. Build seeds: build edges out of the targets and of anything that ships
    #    (a shipped package still has to be built).
    seeds: set[str] = set()
    for n in [*targets, *runtime_closure]:
        seeds |= _buildish_of(n)

    # 3. Build closure: everything reachable from a seed by any edge -- a build
    #    dep needs its own runtime deps present to run, and may need build deps
    #    of its own.
    build_closure: set[str] = set()
    queue = list(seeds)
    while queue:
        n = queue.pop()
        if n in build_closure:
            continue
        build_closure.add(n)
        queue.extend(_runtime_of(n) | _buildish_of(n))

    # Anything that ships wins: it is in the install prefix and still visible to
    # the build, so it must not be duplicated into the build prefix.
    build_closure -= runtime_closure
    build_closure -= set(targets)
    return runtime_closure, build_closure


def _discover_cross_toolchains(
    all_recipes: list[Recipe],
    target_platform: str,
    host_platform: str,
) -> list[Recipe]:
    """Find recipes that provide cross-compilation toolchains.

    Scans all recipes for ``cross_toolchain.target_platforms`` entries
    that include *target_platform*.  Only recipes that can build on
    the *host_platform* are returned.

    For example, the emsdk recipe declares
    ``cross_toolchain.target_platforms: [wasm]`` and has matrix entries
    for linux/macos/windows.  When building for wasm on linux, emsdk
    is discovered automatically.

    Returns toolchain recipes (not yet ordered).  The caller should
    pass the result through ``resolve_build_order`` with the host
    platform before building.
    """
    if target_platform == host_platform:
        return []

    toolchains: list[Recipe] = []
    for r in all_recipes:
        if target_platform not in r.cross_toolchain_targets:
            continue
        # Verify the toolchain can build on the host platform.
        if any(m.platform == host_platform for m in r.build_matrix):
            toolchains.append(r)
    return toolchains


def _collect_host_tools(
    target_recipes: list[Recipe],
    all_recipes: list[Recipe],
    target_platform: str,
    host_platform: str,
) -> list[Recipe]:
    """Identify host-tool recipes needed for cross-compilation.

    Combines two discovery mechanisms:

    1. **Toolchain discovery** — recipes that declare
       ``cross_toolchain.target_platforms`` matching the target are
       included automatically (e.g. emsdk for wasm).

    2. **Dependency walking** — transitive dependencies of target
       recipes that lack a target-platform matrix entry but have a
       host-platform entry are included as host tools.

    Returns a list of host-tool recipes (not yet ordered).  The caller
    should pass the result through ``resolve_build_order`` with the
    host platform before building.
    """
    if target_platform == host_platform:
        return []

    # 1. Toolchain discovery.
    toolchains = _discover_cross_toolchains(all_recipes, target_platform, host_platform)
    seen = {r.name for r in toolchains}

    # 2. Dependency-based discovery.
    all_by_name = {r.name: r for r in all_recipes}
    target_names = {r.name for r in target_recipes}

    needed_deps: set[str] = set()

    def _collect(name: str) -> None:
        if name in needed_deps:
            return
        needed_deps.add(name)
        if name not in all_by_name:
            return
        for dep in _dep_names(all_by_name[name], target_platform):
            _collect(dep)

    for r in target_recipes:
        for dep in _dep_names(r, target_platform):
            _collect(dep)

    for dep_name in needed_deps:
        if dep_name in target_names or dep_name in seen:
            continue
        if dep_name not in all_by_name:
            continue
        dep_recipe = all_by_name[dep_name]
        has_target = any(
            m.platform == target_platform or m.platform == "any" for m in dep_recipe.build_matrix
        )
        has_host = any(m.platform == host_platform for m in dep_recipe.build_matrix)
        if not has_target and has_host:
            toolchains.append(dep_recipe)
            seen.add(dep_name)

    return toolchains


def _bootstrap_host_tools(all_recipes: list[Recipe], platform: str, link: str) -> list[Recipe]:
    """Host tools cvcpkg must build to package its own bundles for *platform*.

    These are packaging-time tools the builder itself invokes (as opposed to
    tools a recipe declares) and that cvcpkg should self-host rather than take
    from the build machine.  Currently just ``patchelf`` for linux shared
    builds: :func:`_patch_linux_rpath` rewrites every installed ``.so`` RPATH to
    ``$ORIGIN`` so bundles are relocatable, and building patchelf ourselves
    keeps that step from depending on a system install.

    Returned recipes are appended to the host-tool set so they build into the
    build prefix — ahead of the target recipes that get relocated.  Returns an
    empty list when the platform/link needs no bootstrap tool or the recipe is
    unavailable (relocation then falls back to a system patchelf, if any).
    """
    if platform == "linux" and link == "shared":
        patchelf = next((r for r in all_recipes if r.name == "patchelf"), None)
        if patchelf is not None:
            return [patchelf]
    return []


def _detect_arch_for_platform(platform: str) -> str:
    """Return the architecture string for a given platform."""
    if platform == "any":
        return "noarch"
    if platform in ("wasm", "wasm-mt"):
        return "wasm32"
    from cvcpkg.platform import detect_arch

    return detect_arch()


def _serves_target_via_any(recipe: Recipe, platform: str, host_platform: str = "") -> bool:
    """True when *recipe*'s build for *platform* is driven by its ``any`` entry.

    Such a build publishes one noarch bundle shared by every target the ``any``
    entry serves; a target with its own matrix entry publishes under that
    target's own name.  See :func:`cvcpkg.platform.served_by_any_entry` for why
    this is decided per target rather than per recipe.
    """
    from cvcpkg.platform import served_by_any_entry

    del host_platform  # entry selection for identity keys off the target only
    return served_by_any_entry([m.platform for m in recipe.build_matrix], platform)


def _common_scripts_hash(recipes_dir: Path) -> str:
    """Compute a combined SHA-256 of all scripts in ``_common/``.

    Returns an empty string if the directory does not exist.  Files are
    processed in sorted order for determinism.
    """
    common = recipes_dir / "_common"
    if not common.is_dir():
        return ""
    h = hashlib.sha256()
    for p in sorted(common.iterdir()):
        if p.is_file():
            h.update(_sha256_file(p).encode())
    return h.hexdigest()


def chain_hash(
    recipe: Recipe,
    all_recipes: dict[str, Recipe],
    platform: str = "",
    *,
    _seen: set[str] | None = None,
    _common_hash: str | None = None,
) -> str:
    """Compute a transitive dependency chain hash for *recipe*.

    The hash covers the recipe's own ``recipe.yaml`` content, build
    scripts, patches, shared ``_common/`` helper scripts, and the
    ``recipe.yaml`` content of every transitive build dependency.  Two
    builds of the same recipe are binary-identical when their chain
    hashes match.

    Returns a hex-encoded SHA-256 digest.
    """
    if _seen is None:
        _seen = set()
    if recipe.name in _seen:
        return ""
    _seen.add(recipe.name)

    # Compute _common/ hash once and reuse for all recursive calls.
    if _common_hash is None:
        _common_hash = _common_scripts_hash(recipe.recipe_dir.parent)

    h = hashlib.sha256()
    # Include this recipe's own YAML content
    h.update(_sha256_file(recipe.recipe_dir / "recipe.yaml").encode())
    # Also include build scripts referenced by the recipe
    for me in recipe.build_matrix:
        script_path = recipe.recipe_dir / me.script
        if script_path.is_file():
            h.update(_sha256_file(script_path).encode())
    # Include patches
    for patch_name in recipe.patches:
        patch_path = recipe.recipe_dir / patch_name
        if patch_path.is_file():
            h.update(_sha256_file(patch_path).encode())
    # Include shared _common/ scripts
    if _common_hash:
        h.update(_common_hash.encode())
    # Recursively include dependency chain hashes (sorted for determinism)
    for dep_name in sorted(_dep_names(recipe, platform)):
        dep = all_recipes.get(dep_name)
        if dep is not None:
            dep_hash = chain_hash(
                dep, all_recipes, platform, _seen=_seen, _common_hash=_common_hash
            )
            if dep_hash:
                h.update(dep_hash.encode())
    return h.hexdigest()


def resolve_build_order(recipes: list[Recipe], platform: str = "") -> list[Recipe]:
    """Return *recipes* in topological (dependency-first) order.

    If *platform* is given, only dependencies that apply to that
    platform are considered when building the graph.

    Dependencies that are not in the candidate *recipes* list are
    silently skipped -- they are assumed to already exist in the
    prefix (e.g. host tools built automatically by ``build_all``
    before the target recipes).

    Raises ``RecipeError`` on dependency cycles.
    """
    by_name: dict[str, Recipe] = {r.name: r for r in recipes}
    visited: set[str] = set()
    in_stack: set[str] = set()
    order: list[Recipe] = []

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in in_stack:
            raise RecipeError(f"Dependency cycle detected involving '{name}'")
        if name not in by_name:
            # Not in our candidate set -- assumed pre-installed.
            return
        in_stack.add(name)
        for dep in _dep_names(by_name[name], platform):
            visit(dep)
        in_stack.discard(name)
        visited.add(name)
        order.append(by_name[name])

    for r in recipes:
        visit(r.name)
    return order


# ── Server cache helpers ────────────────────────────────────────


def _server_cache_probe(
    base_url: str,
    token: str,
    name: str,
    chain_hash_val: str,
    platform: str,
    arch: str,
    build_type: str,
    link: str,
    org: str,
) -> dict[str, Any] | None:
    """Query ``GET /v1/cache/status`` on the remote server.

    Returns the decoded JSON dict on cache hit, ``None`` on miss or
    any network error.
    """
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    params = urllib.parse.urlencode(
        {
            "name": name,
            "chain_hash": chain_hash_val,
            "platform": platform,
            "arch": arch,
            "build_type": build_type,
            "link": link,
            "org": org,
        }
    )
    url = f"{base_url.rstrip('/')}/v1/cache/status?{params}"
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = json.loads(resp.read())
            if data.get("hit"):
                return data
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        pass
    return None


def _server_cache_download(
    archive_url: str,
    token: str,
    dest: Path,
    expected_sha256: str = "",
) -> Path | None:
    """Download a cached archive from the server.

    Returns the local path on success, ``None`` on failure.
    """
    import urllib.error
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(archive_url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310
            with open(dest, "wb") as f:
                while chunk := resp.read(1 << 16):
                    f.write(chunk)
    except (urllib.error.URLError, OSError):
        if dest.is_file():
            dest.unlink(missing_ok=True)
        return None

    if expected_sha256:
        actual = _sha256_file(dest)
        if actual != expected_sha256:
            dest.unlink(missing_ok=True)
            return None
    return dest


def _server_cache_push(
    base_url: str,
    token: str,
    archive_path: Path,
    name: str,
    version: str,
    platform: str,
    arch: str,
    build_type: str,
    link: str,
    recipe_version: str,
    org: str,
) -> bool:
    """Publish an archive to the server cache via ``POST /v1/publish``.

    Returns ``True`` on success, ``False`` on failure.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    params = urllib.parse.urlencode(
        {
            "name": name,
            "version": version,
            "platform": platform,
            "arch": arch,
            "build_type": build_type,
            "link": link,
            "recipe_version": recipe_version,
            "org": org,
        }
    )
    url = f"{base_url.rstrip('/')}/v1/publish?{params}"

    # Build multipart form data manually (avoid extra dependencies).
    boundary = "----cvcpkg-upload-boundary"
    filename = archive_path.name
    body_prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    body_suffix = f"\r\n--{boundary}--\r\n".encode()

    file_data = archive_path.read_bytes()
    body = body_prefix + file_data + body_suffix

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:  # noqa: S310
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def build_all(
    recipes_dir: Path | list[Path],
    *,
    platform: str = "",
    arch: str = "",
    config: str = "release",
    link: str = "shared",
    prefix: Path | None = None,
    keep_build_dir: bool = False,
    per_component: bool = False,
    host_platform: str = "",
    shard: tuple[int, int] | None = None,
    keep_going: bool = False,
    no_cache: bool = False,
    force_clean: bool = False,
    server_cache_url: str = "",
    server_cache_token: str = "",
    server_cache_push: bool = False,
    no_server_cache: bool = False,
    server_cache_org: str = "",
    work_dir_root: Path | None = None,
    cleanup_work_dirs: bool = True,
    build_prefix: Path | None = None,
) -> list[BuildContext]:
    """Build every recipe in dependency order into a shared *prefix*.

    *recipes_dir* may be a single path or a list of paths.  When
    multiple directories are given, later directories override
    earlier ones on name collisions (with a warning).

    *build_prefix*, when given, receives the build-dependency closure (host
    tools / cross-toolchains, and source packages staged under ``src/<name>``)
    instead of *prefix*, so the deliverable ships only the runtime closure.
    Defaults to *prefix* (no separation).

    When *per_component* is ``True``, each recipe is built into its
    own isolated install directory while using the shared *prefix*
    for finding previously-built dependencies via ``CVC_DEPS_PREFIX``.
    After each build, the install directory is merged into *prefix*
    so subsequent recipes can find the new files.  The returned
    ``BuildContext.install_dir`` points to the isolated per-recipe
    directory (useful for packaging only that recipe's files).

    When *cleanup_work_dirs* is ``False`` (default ``True``),
    per-component work directories are preserved after a successful
    build.  Callers that need the ``install_dir`` for subsequent
    staging (e.g. ``pack-all``) should pass ``False`` and clean up
    the directories themselves after use.

    Only recipes with a matrix entry for *platform* are built.
    When cross-compiling (target platform differs from the native
    host), dependencies that lack a target matrix entry but have a
    host-platform entry are automatically built as host tools first
    (e.g. emsdk when building for wasm on linux).

    *host_platform*, when given, identifies the build host for
    cross-compilation.  When omitted it is auto-detected via
    ``detect_platform()``.  It is forwarded to
    ``_select_matrix_entry`` so the correct build script is chosen
    for the current host OS.

    *shard*, when given as ``(index, total)``, partitions the recipe
    list by name hash.  Only recipes assigned to this shard are
    packaged (returned in contexts), but their dependencies are still
    built so they can link correctly.

    When *keep_going* is ``True``, build failures are caught and
    recorded instead of aborting immediately.  Recipes whose
    dependencies failed are skipped.  The returned list contains
    only successfully-built recipes; failures are collected in a
    ``failures`` attribute on the returned list (a
    ``list[BuildFailure]``).

    When *no_cache* is ``True``, the local build cache is bypassed
    entirely -- no lookups and no stores.

    When *force_clean* is ``True``, cache lookups are skipped (every
    recipe is rebuilt from source) but results are still stored in
    the cache for future runs.

    When *server_cache_url* is given, the builder queries the server
    for cached artifacts before building from source.  On a server
    cache hit the archive is downloaded, restored locally, and the
    local cache is populated.  *server_cache_token* provides the
    bearer token for authenticated servers.

    When *server_cache_push* is ``True``, successful builds are
    published to the server cache via ``POST /v1/publish``.

    *no_server_cache* disables server cache entirely (both pull and
    push).  *server_cache_org* scopes cache queries to a specific
    organization.

    *work_dir_root*, when given, is the parent directory in which
    per-recipe temporary work directories are created.  Otherwise
    the system temp directory (``$TMPDIR`` / ``/tmp``) is used.
    This allows developers to point builds at fast dedicated storage
    instead of the default system temp location.
    """
    if isinstance(recipes_dir, list):
        all_recipes = load_all_recipes(recipes_dir)
    else:
        all_recipes = list_recipes(recipes_dir)
    # Filter to recipes that have a matrix entry for this platform.
    # Recipes with platform="any" are included in every target platform
    # build since they contain platform-independent content.
    if not platform:
        platform = detect_platform()

    # Auto-detect host platform for cross-compilation support.
    native_platform = detect_platform()
    if not host_platform:
        host_platform = native_platform

    # Fail fast, before the first fetch: a haiku target this builder cannot
    # delegate fails EVERY recipe in the run, so finding that out after
    # downloading and patching the first one wastes the whole difference.
    from cvcpkg import haikuhost as _haikuhost

    _haikuhost.ensure_delegatable(platform, host_platform)

    recipes = [
        r
        for r in all_recipes
        if any(m.platform == platform or m.platform == "any" for m in r.build_matrix)
        and _artifacts_cover(r, platform, arch)
    ]
    ordered = resolve_build_order(recipes, platform)

    # Identify host-tool recipes needed for cross-compilation.
    host_tool_recipes = _collect_host_tools(ordered, all_recipes, platform, host_platform)

    # Bootstrap cvcpkg's own packaging host tools (e.g. patchelf for linux
    # shared RPATH relocation) so packaging never depends on system installs.
    # These build into the build prefix before the target recipes they relocate.
    _existing_ht = {r.name for r in host_tool_recipes}
    for _bt in _bootstrap_host_tools(all_recipes, platform, link):
        if _bt.name not in _existing_ht:
            host_tool_recipes.append(_bt)
            _existing_ht.add(_bt.name)

    # Determine which recipes are assigned to this shard.
    if shard is not None:
        shard_idx, shard_total = shard
        assigned = {
            r.name
            for r in ordered
            if hashlib.md5(r.name.encode()).digest()[0] % shard_total == shard_idx
        }
        # Also include transitive deps of assigned recipes so they build.
        by_name = {r.name: r for r in ordered}
        needed: set[str] = set()

        def _collect_deps(name: str) -> None:
            if name in needed or name not in by_name:
                return
            needed.add(name)
            for dep in _dep_names(by_name[name], platform):
                _collect_deps(dep)

        for name in assigned:
            _collect_deps(name)
        # Build only what this shard needs, in topological order.
        ordered = [r for r in ordered if r.name in needed]
    else:
        assigned = {r.name for r in ordered}

    if prefix is None:
        prefix = _mkworkdir("cvcpkg-all-", work_dir_root)
        # A shared prefix we created ourselves is scratch, and it is the
        # longest-lived tree in the process: `cvcpkg build-all` merges every
        # component into it over hours.  Its top-level mtime stops advancing
        # once bin/ lib/ include/ exist, so without a heartbeat the disk reaper
        # reads it as abandoned and deletes the prefix the build is installing
        # into.  Released at the single return below.  If build_all raises
        # instead, the watch outlives the call: harmless, because it is one
        # entry on a shared daemon thread that drops any root once the
        # directory is gone, and `cvcpkg build-all` is a one-per-process CLI
        # entry point rather than something the long-lived builder daemon runs.
        watch(prefix, label="build-all")
    prefix = prefix.resolve()

    # Destination for the build-dependency closure (host tools / toolchains and
    # staged source packages).  Defaults to *prefix* -- i.e. no separation --
    # so callers that do not opt in keep the historical single-prefix layout.
    if build_prefix is not None:
        build_prefix = build_prefix.resolve()
    _bp = build_prefix or prefix
    _bp.mkdir(parents=True, exist_ok=True)

    # Placement by dependency edge applies here too.  build_all has no single
    # target, so the roots are the recipes nothing else depends on; anything
    # reachable from a root only via a build edge (e.g. a source package) is
    # build-time only and is staged into the build prefix rather than the
    # deliverable prefix.  With no separation this set is empty (legacy layout).
    _build_closure_names: set[str] = set()
    if build_prefix is not None and _bp != prefix:
        _by_name_all = {r.name: r for r in all_recipes}
        _depended: set[str] = set()
        for _r in all_recipes:
            _depended |= set(_dep_names(_r, platform))
        _roots = [r.name for r in all_recipes if r.name not in _depended]
        _, _build_closure_names = resolve_dep_closures(_roots, _by_name_all, platform)
        if _build_closure_names:
            print(f"cvcpkg: build closure -> {_bp}: {', '.join(sorted(_build_closure_names))}")

    # Prepare build cache (unless disabled).
    use_cache = not no_cache
    cache = None
    if use_cache:
        from cvcpkg.build_cache import BuildCache

        cache = BuildCache()
    all_recipe_map = {r.name: r for r in all_recipes}

    contexts: BuildAllResult = BuildAllResult()
    failures: list[BuildFailure] = []
    failed_names: set[str] = set()  # recipes that failed or were skipped
    cache_hits = 0

    # ── Build host tools for cross-compilation ──────────────────
    # When cross-compiling, host tools (e.g. emsdk for wasm) must be
    # built natively and installed into the prefix before any target
    # recipes.  They go through the same cache machinery as target
    # recipes but use the host platform for keys and build scripts.
    if host_tool_recipes:
        host_ordered = resolve_build_order(host_tool_recipes, host_platform)
        print(f"\ncvcpkg: building {len(host_ordered)} host tool(s) into the build prefix")
        for ht_recipe in host_ordered:
            ht_platform = host_platform
            ht_arch = _detect_arch_for_platform(ht_platform)

            # Cache lookup for the host tool.
            ht_chain_hash = ""
            ht_cached: Path | None = None
            if cache is not None:
                ht_chain_hash = chain_hash(ht_recipe, all_recipe_map, ht_platform)
                if not force_clean:
                    ht_cached = cache.lookup(ht_chain_hash, ht_platform, ht_arch, config, link)

            # Server cache lookup for host tool.
            ht_server_hit = False
            if (
                server_cache_url
                and not no_server_cache
                and not force_clean
                and ht_cached is None
                and ht_chain_hash
            ):
                probe = _server_cache_probe(
                    server_cache_url,
                    server_cache_token,
                    ht_recipe.name,
                    ht_chain_hash,
                    ht_platform,
                    ht_arch,
                    config,
                    link,
                    server_cache_org,
                )
                if probe is not None:
                    dl_url = probe.get("archive_url", "")
                    if dl_url:
                        if dl_url.startswith("/"):
                            dl_url = server_cache_url.rstrip("/") + dl_url
                        tmp_archive = Path(
                            tempfile.mktemp(
                                prefix=f"cvcpkg-srv-{ht_recipe.name}-",
                                suffix=".tar.gz",
                            )
                        )
                        result = _server_cache_download(
                            dl_url,
                            server_cache_token,
                            tmp_archive,
                            expected_sha256=probe.get("sha256", ""),
                        )
                        if result is not None:
                            ht_cached = result
                            ht_server_hit = True

            print(f"\ncvcpkg: == {ht_recipe.name} ({ht_recipe.full_version}) [host tool] ==")

            if ht_cached is not None:
                cache_hits += 1
                label = "server cache" if ht_server_hit else "cache"
                print(f"  <- {label} hit ({ht_chain_hash[:12]}...)")
                cache.restore(ht_cached, _bp)
                _rewrite_pc_prefixes(_bp)
                _rewrite_script_prefixes(_bp)
                # Populate local cache from server download.
                if ht_server_hit and cache is not None:
                    srv_restore = Path(tempfile.mkdtemp(prefix="cvcpkg-srv-restore-"))
                    try:
                        cache.restore(ht_cached, srv_restore)
                        cache.store(
                            srv_restore,
                            ht_recipe.name,
                            ht_recipe.full_version,
                            ht_chain_hash,
                            ht_platform,
                            ht_arch,
                            config,
                            link,
                        )
                    finally:
                        shutil.rmtree(srv_restore, ignore_errors=True)
                if ht_server_hit and ht_cached.is_file():
                    ht_cached.unlink(missing_ok=True)
            else:
                # Build host tool into isolated dir, then merge to prefix.
                ht_work = _mkworkdir(f"cvcpkg-{ht_recipe.name}-", work_dir_root)
                try:
                    ht_install = ht_work / "install"
                    ht_source = fetch_source(ht_recipe, ht_work)
                    if ht_recipe.patches:
                        apply_patches(ht_recipe, ht_source)
                    ht_ctx = BuildContext(
                        recipe=ht_recipe,
                        platform=ht_platform,
                        config=config,
                        link=link,
                        prefix=_bp,
                        source_dir=ht_source,
                        build_dir=ht_work / "build",
                        install_dir=ht_install,
                        work_dir=ht_work,
                        keep_build_dir=keep_build_dir,
                        build_prefix=build_prefix,
                    )
                    run_build(ht_ctx)
                    if ht_install.is_dir():
                        _merge_tree(ht_install, _bp)
                        _rewrite_pc_prefixes(_bp)
                        _rewrite_script_prefixes(_bp)
                    # Store in local cache.
                    if cache is not None and ht_chain_hash and ht_install.is_dir():
                        cache.store(
                            ht_install,
                            ht_recipe.name,
                            ht_recipe.full_version,
                            ht_chain_hash,
                            ht_platform,
                            ht_arch,
                            config,
                            link,
                        )
                    # Push to server cache.
                    if (
                        server_cache_push
                        and server_cache_url
                        and not no_server_cache
                        and cache is not None
                    ):
                        arc = cache.lookup(ht_chain_hash, ht_platform, ht_arch, config, link)
                        if arc is not None:
                            ok = _server_cache_push(
                                server_cache_url,
                                server_cache_token,
                                arc,
                                ht_recipe.name,
                                ht_recipe.full_version,
                                ht_platform,
                                ht_arch,
                                config,
                                link,
                                ht_chain_hash,
                                server_cache_org,
                            )
                            if ok:
                                print(f"  -> pushed to server cache ({ht_chain_hash[:12]}...)")
                except (BuildError, RecipeError):
                    if not keep_going:
                        raise
                    # Host tool failure is fatal for target recipes that
                    # depend on it, but keep-going lets us continue with
                    # recipes that don't need this tool.
                    print(f"\ncvcpkg: FAILED host tool {ht_recipe.name}")
                    failed_names.add(ht_recipe.name)
                    failures.append(
                        BuildFailure(
                            recipe_name=ht_recipe.name,
                            error=BuildError(f"host tool {ht_recipe.name} failed to build"),
                        )
                    )
                finally:
                    if not keep_build_dir and ht_work.is_dir():
                        shutil.rmtree(ht_work, ignore_errors=True)

    # ── Collect cross-toolchain env vars ─────────────────────────
    # Toolchain recipes (e.g. emsdk) declare ``cross_toolchain.env``
    # entries that should be set when building target-platform recipes.
    # Merge them all into a single dict that gets passed to every
    # target BuildContext.
    merged_toolchain_env: dict[str, str] = {}
    for ht in host_tool_recipes:
        for var, tpl in ht.cross_toolchain_env.items():
            merged_toolchain_env[var] = tpl

    # ── Build target-platform recipes ──────────────────────────
    for recipe in ordered:
        # Check if any dependency already failed.
        dep_names = _dep_names(recipe, platform)
        failed_deps = [d for d in dep_names if d in failed_names]
        if failed_deps and keep_going:
            msg = f"skipped (dependency failed: {', '.join(failed_deps)})"
            print(f"\ncvcpkg: == {recipe.name} ({recipe.full_version}) -- {msg} ==")
            failed_names.add(recipe.name)
            failures.append(
                BuildFailure(
                    recipe_name=recipe.name,
                    error=BuildError(msg),
                    skipped=True,
                )
            )
            continue

        is_assigned = recipe.name in assigned
        label = "" if is_assigned else " (dep)"
        print(f"\ncvcpkg: == {recipe.name} ({recipe.full_version}){label} ==")

        # When this target is served by the recipe's `any` entry, use
        # "any"/"noarch" so a single cache entry is shared across every target
        # that entry covers.  A target with its own entry keeps its own key.
        eff_platform = "any" if _serves_target_via_any(recipe, platform) else platform
        eff_arch = _detect_arch_for_platform(eff_platform)

        # Build cache lookup.
        recipe_chain_hash = ""
        cached_archive: Path | None = None
        if cache is not None:
            recipe_chain_hash = chain_hash(recipe, all_recipe_map, eff_platform)
            if not force_clean:
                cached_archive = cache.lookup(
                    recipe_chain_hash, eff_platform, eff_arch, config, link
                )

        # Server cache lookup (when local cache misses).
        server_hit = False
        use_server = (
            server_cache_url
            and not no_server_cache
            and not force_clean
            and cached_archive is None
            and recipe_chain_hash
        )
        if use_server:
            probe = _server_cache_probe(
                server_cache_url,
                server_cache_token,
                recipe.name,
                recipe_chain_hash,
                eff_platform,
                eff_arch,
                config,
                link,
                server_cache_org,
            )
            if probe is not None:
                dl_url = probe.get("archive_url", "")
                if dl_url:
                    # Make relative archive_url absolute.
                    if dl_url.startswith("/"):
                        dl_url = server_cache_url.rstrip("/") + dl_url
                    tmp_archive = Path(
                        tempfile.mktemp(
                            prefix=f"cvcpkg-srv-{recipe.name}-",
                            suffix=".tar.gz",
                        )
                    )
                    result = _server_cache_download(
                        dl_url,
                        server_cache_token,
                        tmp_archive,
                        expected_sha256=probe.get("sha256", ""),
                    )
                    if result is not None:
                        cached_archive = result
                        server_hit = True
                        print(f"  <- server cache hit ({recipe_chain_hash[:12]}...)")
                        # Populate local cache for future runs.
                        if cache is not None:
                            import shutil as _shutil_srv

                            srv_restore = Path(tempfile.mkdtemp(prefix="cvcpkg-srv-restore-"))
                            try:
                                cache.restore(tmp_archive, srv_restore)
                                cache.store(
                                    srv_restore,
                                    recipe.name,
                                    recipe.full_version,
                                    recipe_chain_hash,
                                    eff_platform,
                                    eff_arch,
                                    config,
                                    link,
                                )
                            finally:
                                _shutil_srv.rmtree(srv_restore, ignore_errors=True)

        # Build-closure recipes stage into the build prefix; everything else
        # into the deliverable prefix.
        _dest = _bp if recipe.name in _build_closure_names else prefix
        work_dir = prefix  # default; overridden for per_component builds
        try:
            if cached_archive is not None:
                # Cache hit -- restore artifacts instead of building.
                cache_hits += 1
                if not server_hit:
                    print(f"  <- cache hit ({recipe_chain_hash[:12]}...)")
                if per_component:
                    work_dir = _mkworkdir(f"cvcpkg-{recipe.name}-", work_dir_root)
                    install_dir = work_dir / "install"
                    cache.restore(cached_archive, install_dir)
                    ctx = BuildContext(
                        recipe=recipe,
                        platform=platform,
                        config=config,
                        link=link,
                        prefix=prefix,
                        source_dir=work_dir / "source",
                        build_dir=work_dir / "build",
                        install_dir=install_dir,
                        work_dir=work_dir,
                        keep_build_dir=keep_build_dir,
                        host_platform=host_platform,
                        cross_toolchain_env=merged_toolchain_env,
                        build_prefix=build_prefix,
                    )
                    if install_dir.is_dir():
                        # symlinks=True: preserve version/convenience symlinks and
                        # don't choke on dangling ones (e.g. ncurses' lib/libpanel
                        # on macOS). Matches the staging copy.
                        _merge_tree(install_dir, _dest)
                        _rewrite_pc_prefixes(_dest)
                        _rewrite_script_prefixes(_dest)
                else:
                    cache.restore(cached_archive, _dest)
                    _rewrite_pc_prefixes(_dest)
                    _rewrite_script_prefixes(_dest)
                    ctx = BuildContext(
                        recipe=recipe,
                        platform=platform,
                        config=config,
                        link=link,
                        prefix=prefix,
                        source_dir=prefix,
                        build_dir=prefix,
                        install_dir=prefix,
                        work_dir=prefix,
                        keep_build_dir=keep_build_dir,
                        host_platform=host_platform,
                        cross_toolchain_env=merged_toolchain_env,
                        build_prefix=build_prefix,
                    )
            elif per_component:
                work_dir = _mkworkdir(f"cvcpkg-{recipe.name}-", work_dir_root)
                install_dir = work_dir / "install"
                source_dir = fetch_source(recipe, work_dir)
                if recipe.patches:
                    apply_patches(recipe, source_dir)
                ctx = BuildContext(
                    recipe=recipe,
                    platform=platform,
                    config=config,
                    link=link,
                    prefix=prefix,
                    source_dir=source_dir,
                    build_dir=work_dir / "build",
                    install_dir=install_dir,
                    work_dir=work_dir,
                    keep_build_dir=keep_build_dir,
                    host_platform=host_platform,
                    cross_toolchain_env=merged_toolchain_env,
                    build_prefix=build_prefix,
                )
                run_build(ctx)
                if recipe.test_script:
                    run_test(ctx)
                if recipe.vm_test:
                    run_vm_test(ctx)
                # Merge this recipe's install into the shared prefix so
                # subsequent recipes can find it via CVC_DEPS_PREFIX.
                if install_dir.is_dir():
                    # Merge, not copytree: robust to dangling AND already-existing
                    # symlinks in the shared prefix (ncurses lib/libpanel;
                    # python bin/2to3 shared across python3xx versions).
                    _merge_tree(install_dir, _dest)
                    _rewrite_pc_prefixes(_dest)
                    _rewrite_script_prefixes(_dest)
                # Store in build cache.
                if cache is not None and recipe_chain_hash and install_dir.is_dir():
                    cache.store(
                        install_dir,
                        recipe.name,
                        recipe.full_version,
                        recipe_chain_hash,
                        eff_platform,
                        eff_arch,
                        config,
                        link,
                    )
                    # Push to server cache.
                    if (
                        server_cache_push
                        and server_cache_url
                        and not no_server_cache
                        and not server_hit
                    ):
                        arc = cache.lookup(recipe_chain_hash, eff_platform, eff_arch, config, link)
                        if arc is not None:
                            ok = _server_cache_push(
                                server_cache_url,
                                server_cache_token,
                                arc,
                                recipe.name,
                                recipe.full_version,
                                eff_platform,
                                eff_arch,
                                config,
                                link,
                                recipe_chain_hash,
                                server_cache_org,
                            )
                            if ok:
                                print(f"  -> pushed to server cache ({recipe_chain_hash[:12]}...)")
            else:
                ctx = build_recipe(
                    recipe.recipe_dir,
                    platform=platform,
                    config=config,
                    link=link,
                    prefix=_dest,
                    keep_build_dir=keep_build_dir,
                    host_platform=host_platform,
                    work_dir_root=work_dir_root,
                    cross_toolchain_env=merged_toolchain_env,
                    build_prefix=build_prefix,
                )
            # Only include assigned recipes in contexts (for packaging).
            if is_assigned:
                contexts.append(ctx)
        except (BuildError, RecipeError) as exc:
            if not keep_going:
                raise
            print(f"\ncvcpkg: FAILED {recipe.name}: {exc}")
            failed_names.add(recipe.name)
            failures.append(BuildFailure(recipe_name=recipe.name, error=exc))
        finally:
            # Clean up server-downloaded temp archive.
            if server_hit and cached_archive is not None and cached_archive.is_file():
                cached_archive.unlink(missing_ok=True)
            # Clean up per-component work directory (source, install, staging).
            # When cleanup_work_dirs is False the caller is responsible for
            # removing work directories after it is done with them (e.g.
            # pack-all needs the install_dir for staging).
            if (
                cleanup_work_dirs
                and per_component
                and not keep_build_dir
                and work_dir != prefix
                and work_dir.is_dir()
            ):
                shutil.rmtree(work_dir, ignore_errors=True)

    if failures:
        print(f"\ncvcpkg: {len(contexts)} succeeded, {len(failures)} failed:")
        for f in failures:
            status = "SKIPPED (dep)" if f.skipped else "FAILED"
            print(f"  {status}: {f.recipe_name} -- {f.error}")
    else:
        cache_msg = f" ({cache_hits} cache hits)" if cache_hits else ""
        print(f"\ncvcpkg: all {len(contexts)} components built into {prefix}{cache_msg}")

    # Work has stopped, so stop claiming the prefix is alive.  The final beat
    # unwatch() emits resets the tree's age to *now*, which is what the reaper
    # should count from if this process dies before anything cleans up.
    unwatch(prefix)

    # Attach failures to the returned list for callers to inspect.
    contexts.failures = failures
    return contexts


def find_recipes_dir() -> Path:
    """Locate the recipes/ directory.

    Search order:
    0. A frozen single-binary bundle (PyInstaller/onefile): recipes are shipped
       as data alongside the extracted package under ``sys._MEIPASS``.
    1. Bundled recipes shipped inside the installed package.
    2. Walk up from the package source to find a repo checkout.
    3. Fallback: recipes/ in the current working directory.
    """
    # 0. Frozen single-binary (PyInstaller extracts data to sys._MEIPASS). The
    #    bundle carries the standard recipes so a self-contained `cvcpkg` binary
    #    discovers them with no --recipes-dir. Both layouts are accepted:
    #    <_MEIPASS>/cvcpkg/recipes (data mirrors the package) and <_MEIPASS>/recipes.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        for cand in (Path(meipass) / "cvcpkg" / "recipes", Path(meipass) / "recipes"):
            if cand.is_dir() and (cand / "_common").is_dir():
                return cand
    # 1. Bundled recipes (installed via pip)
    bundled = Path(__file__).resolve().parent / "recipes"
    if bundled.is_dir() and (bundled / "_common").is_dir():
        return bundled
    # 2. Walk up from the cvcpkg package to find the repo
    pkg_dir = Path(__file__).resolve().parent
    for ancestor in pkg_dir.parents:
        candidate = ancestor / "recipes"
        if candidate.is_dir() and (candidate / "_common").is_dir():
            return candidate
    # 3. Fallback: CWD
    candidate = Path.cwd() / "recipes"
    if candidate.is_dir():
        return candidate
    raise RecipeError("Cannot locate recipes/ directory")


def cwd_recipes_overlay(existing: Iterable[Path] = ()) -> Path | None:
    """Return ``$CWD/recipes`` when it is a usable recipe overlay, else None.

    A repo root that carries a ``recipes/`` directory with at least one
    ``*/recipe.yaml`` is treated as an implicit ``--recipes-dir`` overlay, so a
    one-shot ``cvcpkg build <name>`` (or ``validate``) run from that root finds
    the repo-local recipe with no flag.  Placed LAST on the search path, it wins
    over the bundled default on name conflicts (later dirs win).

    Returns None when there is no such directory, it holds no recipe, or it is
    already present in *existing* (compared resolved, so it is never added
    twice — e.g. when ``find_recipes_dir()`` already resolved to it via its
    step-2 walk-up or step-3 CWD fallback).
    """
    cwd_recipes = (Path.cwd() / "recipes").resolve()
    if not cwd_recipes.is_dir():
        return None
    has_recipe = any(
        (child / "recipe.yaml").is_file() for child in cwd_recipes.iterdir() if child.is_dir()
    )
    if not has_recipe:
        return None
    if cwd_recipes in {Path(p).resolve() for p in existing}:
        return None
    return cwd_recipes


def list_recipes(recipes_dir: Path | None = None) -> list[Recipe]:
    """Load all recipes from a single recipes/ directory."""
    if recipes_dir is None:
        recipes_dir = find_recipes_dir()
    recipes = []
    for child in sorted(recipes_dir.iterdir()):
        recipe_yaml = child / "recipe.yaml"
        if child.is_dir() and recipe_yaml.is_file():
            recipes.append(Recipe.load(child))
    return recipes


def load_all_recipes(recipe_dirs: list[Path]) -> list[Recipe]:
    """Load recipes from multiple directories, with conflict detection.

    Directories listed later take precedence: if two directories both
    contain a recipe with the same name, the one from the later
    directory wins and a warning is printed.

    Raises ``RecipeError`` if *recipe_dirs* is empty.
    """
    if not recipe_dirs:
        raise RecipeError("No recipe directories specified")
    by_name: dict[str, Recipe] = {}
    for rdir in recipe_dirs:
        for child in sorted(rdir.iterdir()):
            recipe_yaml = child / "recipe.yaml"
            if child.is_dir() and recipe_yaml.is_file():
                recipe = Recipe.load(child)
                if recipe.name in by_name:
                    prev = by_name[recipe.name]
                    print(
                        f"cvcpkg: warning: recipe '{recipe.name}' from "
                        f"{recipe.recipe_dir} overrides {prev.recipe_dir}"
                    )
                by_name[recipe.name] = recipe
    return sorted(by_name.values(), key=lambda r: r.name)


def _slot_providers(slots: set[str], recipe_dirs: list[Path]) -> dict[str, set[str]]:
    """Map each slot in *slots* to the package names providing it."""
    providers: dict[str, set[str]] = {s: set() for s in slots}
    for rdir in recipe_dirs:
        if not rdir.is_dir():
            continue
        for child in sorted(rdir.iterdir()):
            if not (child / "recipe.yaml").is_file():
                continue
            try:
                r = Recipe.load(child)
            except Exception:
                continue
            for slot in slots.intersection(r.provides):
                providers[slot].add(r.name)
    return providers


def collect_recipe_conflicts(
    names: list[str],
    recipe_dirs: list[Path],
) -> dict[str, list[str]]:
    """Return a ``{package_name: [conflicting_package, ...]}`` mapping.

    Covers two sources:

    * explicit ``conflicts:`` on the recipes named in *names*;
    * ``provides:`` slots — every package providing a slot is mutually
      exclusive with every other provider of that slot.

    The slot form exists because explicit ``conflicts:`` must be declared
    symmetrically (each of a pair naming the other), and nothing enforces
    that.  A one-sided declaration only fires in one direction: this
    function loads recipes for the packages *being installed*, so if A
    declares B but B does not declare A, installing B onto an existing A
    sails through.  For a mutually exclusive *group* of n packages, the
    pairwise form needs n*(n-1) declarations all kept in sync by hand;
    a slot needs n, and cannot be asymmetric by construction.

    Packages whose recipe cannot be found in *recipe_dirs* are silently
    skipped so the function is safe to call when a recipe directory is
    not available.
    """
    conflicts: dict[str, list[str]] = {}
    slots_of: dict[str, set[str]] = {}

    for rdir in recipe_dirs:
        for name in names:
            recipe_yaml = rdir / name / "recipe.yaml"
            if recipe_yaml.is_file():
                try:
                    r = Recipe.load(rdir / name)
                except Exception:
                    continue
                if r.conflicts:
                    conflicts.setdefault(name, []).extend(r.conflicts)
                if r.provides:
                    slots_of.setdefault(name, set()).update(r.provides)

    # Resolve slots to their other providers.  Only scan every recipe when
    # something being installed actually claims a slot — most do not.
    wanted = {s for slots in slots_of.values() for s in slots}
    if wanted:
        providers = _slot_providers(wanted, recipe_dirs)
        for name, slots in slots_of.items():
            for slot in slots:
                for other in providers.get(slot, ()):
                    if other != name:
                        conflicts.setdefault(name, []).append(other)

    return {k: sorted(set(v)) for k, v in conflicts.items()}


# ── Revision bumping ───────────────────────────────────────────


def get_reverse_deps(
    recipes: list[Recipe],
    platform: str = "",
) -> dict[str, set[str]]:
    """Build a reverse dependency map: package → set of direct dependants.

    Each key is a recipe name; its value is the set of recipe names
    that list it as a build or host-tool dependency.
    """
    reverse: dict[str, set[str]] = {}
    for r in recipes:
        for dep_name in _dep_names(r, platform):
            reverse.setdefault(dep_name, set()).add(r.name)
    return reverse


def get_downstream(
    name: str,
    recipes: list[Recipe],
    platform: str = "",
) -> list[str]:
    """Return all recipes that transitively depend on *name*.

    The returned list is in topological (dependency-first) order so
    that a caller can bump revisions leaf-to-root.
    """
    reverse = get_reverse_deps(recipes, platform)
    visited: set[str] = set()
    result: list[str] = []

    def walk(n: str) -> None:
        for dependent in sorted(reverse.get(n, ())):
            if dependent not in visited:
                visited.add(dependent)
                walk(dependent)
                result.append(dependent)

    walk(name)
    return result


def _bump_revision_in_yaml(recipe_yaml: Path, new_revision: int) -> None:
    """Edit the ``cvc_revision`` field in a recipe.yaml in-place.

    Uses a regex replacement so comments and formatting are preserved.
    If there is no ``cvc_revision`` line, one is inserted after the
    ``upstream_version`` line.
    """
    import re

    text = recipe_yaml.read_text()
    pattern = re.compile(r"^(\s*cvc_revision\s*:\s*)\d+", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(rf"\g<1>{new_revision}", text)
    else:
        # Insert after upstream_version line
        uv_pattern = re.compile(r"^(\s*upstream_version\s*:.*\n)", re.MULTILINE)
        m = uv_pattern.search(text)
        if m:
            indent = re.match(r"\s*", m.group(1)).group()  # type: ignore[union-attr]
            insertion = f"{indent}cvc_revision: {new_revision}\n"
            text = text[: m.end()] + insertion + text[m.end() :]
        else:
            raise RecipeError(f"Cannot find upstream_version in {recipe_yaml}")
    recipe_yaml.write_text(text)


def rev_bump(
    name: str,
    recipes_dir: Path,
    *,
    platform: str = "",
    cascade: bool = True,
    revision_for: Callable[[Recipe], int] | None = None,
) -> list[tuple[str, int, int]]:
    """Bump ``cvc_revision`` for a recipe and its downstream dependents.

    Returns a list of ``(recipe_name, old_revision, new_revision)``
    tuples for every recipe that was modified.

    When *cascade* is ``True`` (the default), all transitive
    dependents also have their revisions bumped so that consumers
    pick up the patched dependency tree.

    By default each target is bumped by one.  Pass *revision_for* to choose
    the new revision per recipe — e.g. a published-aware resolver that returns
    ``max(recipe_floor, highest_published + 1)`` (``cvcpkg cascade-bump``).
    A target whose resolved revision does not exceed its current one is left
    untouched and omitted from the result (its committed revision is already
    unpublished, so no bump is needed to republish it).
    """
    recipes = list_recipes(recipes_dir)
    by_name = {r.name: r for r in recipes}

    if name not in by_name:
        raise RecipeError(f"Recipe '{name}' not found in {recipes_dir}")

    targets = [name]
    if cascade:
        targets.extend(get_downstream(name, recipes, platform))

    bumped: list[tuple[str, int, int]] = []
    for target_name in targets:
        recipe = by_name[target_name]
        old_rev = recipe.cvc_revision
        if revision_for is None:
            new_rev = old_rev + 1
        else:
            new_rev = revision_for(recipe)
            if new_rev <= old_rev:
                # Already unpublished at its committed revision — nothing to do.
                continue
        recipe_yaml = recipe.recipe_dir / "recipe.yaml"
        _bump_revision_in_yaml(recipe_yaml, new_rev)
        bumped.append((target_name, old_rev, new_rev))

    return bumped
