# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Remove installed bundles from a prefix (the inverse of installer.py).

An installed prefix keeps no per-package file record: extraction is a blind
merge, and the ``share/libcvc-deps/manifest.yaml`` each bundle carries is
clobbered by whichever bundle extracted last.  Until Phase 15's per-prefix
state database lands, the ground truth for "which files does package X own"
is X's *archive*: the lockfile records every bundle's sha256 + archive_url,
the download cache is content-addressed, and an archive's member list is
exactly what extraction materialized into the prefix.

Three consequences shape this module:

- A bundle built from source (``source-build`` in the lockfile) has no
  archive, hence no file list — it cannot be uninstalled file-by-file.
- Dependency edges come from each archive's embedded manifest, falling back
  to a local recipe's ``depends.runtime`` when no archive is cached.
- A path may be shipped by several bundles (extraction is last-writer-wins),
  so removal must skip paths a surviving package also owns, and the
  ``share/libcvc-deps/`` metadata slot — which *every* bundle writes — may
  only be deleted when no package survives.
"""

from __future__ import annotations

import re
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from cvcpkg import cache as cache_mod
from cvcpkg.errors import InstallError
from cvcpkg.lockfile import LockEntry, Lockfile

# Every bundle writes into this directory (manifest.yaml, recipe/, ...), so a
# member under it is shared metadata, not package payload: it is only safe to
# delete when the uninstall empties the prefix.
META_PREFIX = "share/libcvc-deps/"

# install_entry moves noarch python payloads from the Unix site dir to the one
# a python.org-layout Windows interpreter searches; deletion must follow.
_WIN_SITE_RE = re.compile(r"^lib/python[^/]+/site-packages/")


@dataclass
class InstalledPackage:
    """What uninstall could learn about one lockfile entry."""

    entry: LockEntry
    archive: Path | None = None  # cached archive, when present locally
    files: list[str] = field(default_factory=list)  # prefix-relative paths
    deps: list[str] = field(default_factory=list)  # required dep names
    provides: list[str] = field(default_factory=list)
    deps_known: bool = True

    @property
    def name(self) -> str:
        return self.entry.name

    @property
    def source_built(self) -> bool:
        return not self.entry.archive_url


def archive_filename(entry: LockEntry) -> str:
    """The cache filename for a lock entry's archive (basename of its URL)."""
    return entry.archive_url.rsplit("/", 1)[-1]


def effective_path(member: str, platform: str) -> str:
    """Map an archive member path to where install actually materialized it."""
    if platform == "windows":
        return _WIN_SITE_RE.sub("Lib/site-packages/", member)
    return member


def _validated(member: str) -> str | None:
    """Normalize an archive member path, or None for non-payload members.

    Mirrors the extractor's validation: absolute paths and ``..`` components
    are hostile and refuse the whole archive rather than one member.
    """
    name = member.replace("\\", "/")
    if name.startswith("./"):
        name = name[2:]
    if not name or name in (".", "/"):
        return None
    if name.startswith("/") or ".." in name.split("/"):
        raise InstallError(f"unsafe path in archive: {member}")
    return name


def read_archive(archive: Path, pkg_name: str) -> tuple[list[str], dict | None]:
    """Return ``(member paths, embedded manifest)`` from ONE pass over *archive*.

    Both are read together on purpose.  A ``.tar.gz``/``.tar.zst`` has to be
    decompressed from the start to reach any member, and the manifest sorts
    near the end of the tar (``share/`` follows ``bin/``, ``include/``,
    ``lib/``), so a separate manifest read costs a second full decompression
    of every bundle in the prefix — Qt6 and VTK make that very visible.

    Directory members are omitted: removal deletes files and then prunes
    emptied directories, so they carry no information.

    *pkg_name*'s manifest may sit at either of two paths depending on when
    the archive was built: ``share/libcvc-deps/<pkg_name>/manifest.yaml``
    (current) or the flat ``share/libcvc-deps/manifest.yaml`` predating that
    layout.  Unlike reading a merged prefix, a single archive belongs to
    exactly one bundle, so there is no clobbering risk in accepting either
    path without cross-checking the manifest's own name.
    """
    name = archive.name.lower()
    if name.endswith(".7z"):
        raise InstallError(
            f"cannot list files in {archive.name}: .7z archives are not supported for uninstall"
        )
    targets = {META_PREFIX + pkg_name + "/manifest.yaml", META_PREFIX + "manifest.yaml"}
    files: list[str] = []
    manifest: dict | None = None

    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                p = _validated(info.filename)
                if not p:
                    continue
                files.append(p)
                if p in targets and manifest is None:
                    manifest = _safe_yaml(zf.read(info))
        return files, manifest

    with tarfile.open(archive, mode="r|*") as tf:  # streaming: no seeking back
        for member in tf:
            if not (member.isreg() or member.issym() or member.islnk()):
                continue
            p = _validated(member.name)
            if not p:
                continue
            files.append(p)
            if p in targets and manifest is None and member.isreg():
                f = tf.extractfile(member)
                if f is not None:
                    manifest = _safe_yaml(f.read())
    return files, manifest


def _safe_yaml(raw: bytes) -> dict | None:
    """Parse a manifest blob, treating anything unexpected as absent."""
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _manifest_deps_provides(manifest: dict) -> tuple[list[str], list[str]]:
    """Extract required-dep names and provides slots, tolerating legacy shapes."""
    from cvcpkg.manifest import BundleManifest

    try:
        m = BundleManifest.from_dict(manifest)
    except Exception:
        return [], []
    # The builder writes ``provides`` both top-level and under ``contents:``
    # (the installed-prefix view); from_dict only reads the former, and older
    # bundles may only carry the latter — accept either.
    provides = set(m.provides)
    contents = manifest.get("contents", {})
    if isinstance(contents, dict):
        extra = contents.get("provides", [])
        if isinstance(extra, list):
            provides.update(str(s) for s in extra)
    return [d.name for d in m.required_deps if d.name], sorted(provides)


def _recipe_dep_names(recipe_yaml: Path, platform: str) -> list[str] | None:
    """Fallback dep source for source-built entries: the local recipe."""
    try:
        raw = yaml.safe_load(recipe_yaml.read_text())
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(raw, dict):
        return None
    block = raw.get("depends") or {}
    if not isinstance(block, dict):
        return None
    deps = block.get("runtime", block.get("build", [])) or []
    names: list[str] = []
    for d in deps:
        if isinstance(d, str):
            names.append(d.split("/", 1)[-1])
        elif isinstance(d, dict) and d.get("name"):
            plats = d.get("platforms")
            if plats and platform not in plats:
                continue
            names.append(d["name"])
    return names


def load_installed(
    lock: Lockfile,
    cache_dir: Path,
    recipe_dirs: list[Path] | None = None,
) -> dict[str, InstalledPackage]:
    """Describe every lockfile entry from its cached archive.

    Only archives already in the cache are consulted — computing the
    dependency graph must not trigger downloads for packages that are not
    being removed.  Callers fetch the removal set's archives explicitly.
    """
    packages: dict[str, InstalledPackage] = {}
    for entry in lock.bundles:
        pkg = InstalledPackage(entry=entry)
        if entry.archive_url and entry.sha256:
            p = cache_mod.cache_path(cache_dir, entry.sha256, archive_filename(entry))
            if p.is_file():
                pkg.archive = p
                pkg.files, manifest = read_archive(p, entry.name)
                if manifest is not None:
                    pkg.deps, pkg.provides = _manifest_deps_provides(manifest)
                else:
                    pkg.deps_known = False
        if pkg.archive is None:
            pkg.deps_known = False
            for rdir in recipe_dirs or []:
                recipe_yaml = rdir / entry.name / "recipe.yaml"
                if recipe_yaml.is_file():
                    deps = _recipe_dep_names(recipe_yaml, lock.platform)
                    if deps is not None:
                        pkg.deps = deps
                        pkg.deps_known = True
                    break
        packages[entry.name] = pkg
    return packages


def fetch_removal_archive(pkg: InstalledPackage, lock: Lockfile, cache_dir: Path) -> None:
    """Ensure *pkg* has an archive and a file list, downloading if evicted.

    Removal targets, unlike bystanders, hard-require their archive: without
    its member list there is nothing safe to delete.  Reconstructs a catalog
    entry from the lockfile exactly like ``cvcpkg sync`` does.
    """
    if pkg.archive is not None:
        return
    from cvcpkg.installer import download_bundle
    from cvcpkg.manifest import CatalogEntry

    entry = pkg.entry
    cat_entry = CatalogEntry(
        name=entry.name,
        version=entry.version,
        upstream_version=entry.upstream_version,
        cvc_revision=1,
        platform=lock.platform,
        arch=lock.arch,
        build_type=lock.config,
        link=lock.link,
        sha256=entry.sha256,
        size_bytes=entry.size_bytes,
        archive_url=entry.archive_url,
        source_release=entry.source_release,
    )
    pkg.archive = download_bundle(cat_entry, cache_dir)
    pkg.files, manifest = read_archive(pkg.archive, entry.name)
    if manifest is not None:
        pkg.deps, pkg.provides = _manifest_deps_provides(manifest)
        pkg.deps_known = True


def dependent_closure(targets: set[str], packages: dict[str, InstalledPackage]) -> set[str]:
    """Targets plus every installed package that transitively depends on one.

    A dependency edge counts whether it names the package itself or a virtual
    slot the package ``provides`` (e.g. depending on ``python`` reaches
    ``python313``).
    """
    provided_by: dict[str, set[str]] = {}
    for name, pkg in packages.items():
        provided_by.setdefault(name, set()).add(name)
        for slot in pkg.provides:
            provided_by.setdefault(slot, set()).add(name)

    dependents: dict[str, set[str]] = {name: set() for name in packages}
    for name, pkg in packages.items():
        for dep in pkg.deps:
            for provider in provided_by.get(dep, ()):  # unknown deps are system-level
                if provider != name:
                    dependents[provider].add(name)

    closure = set(targets)
    stack = list(targets)
    while stack:
        for dep in dependents.get(stack.pop(), ()):
            if dep not in closure:
                closure.add(dep)
                stack.append(dep)
    return closure


@dataclass
class RemovalPlan:
    """Which paths uninstall will delete, and which it deliberately keeps."""

    # package name -> prefix-relative paths to delete (deduplicated in order)
    remove: dict[str, list[str]] = field(default_factory=dict)
    # paths kept because a surviving package also ships them
    shared_kept: list[str] = field(default_factory=list)
    # share/libcvc-deps/ members kept because packages survive
    metadata_kept: list[str] = field(default_factory=list)


def plan_removal(
    removal: dict[str, InstalledPackage],
    kept: dict[str, InstalledPackage],
    platform: str,
) -> RemovalPlan:
    """Decide the deletable path set for *removal*, protecting *kept*.

    Kept-package file lists are best-effort (cached archives only): overlaps
    between co-installed packages are supposed to be declared ``conflicts:``
    and blocked at install time, so this is a second net, not the primary
    defence.
    """
    kept_files: set[str] = set()
    for pkg in kept.values():
        for f in pkg.files:
            kept_files.add(effective_path(f, platform))

    plan = RemovalPlan()
    claimed: set[str] = set()
    for name, pkg in removal.items():
        paths: list[str] = []
        for member in pkg.files:
            path = effective_path(member, platform)
            if path in claimed:
                continue
            if path.startswith(META_PREFIX):
                if kept:
                    plan.metadata_kept.append(path)
                    continue
            elif path in kept_files:
                plan.shared_kept.append(path)
                continue
            claimed.add(path)
            paths.append(path)
        plan.remove[name] = paths
    return plan


@dataclass
class RemovalResult:
    """Outcome of applying a removal plan to the filesystem."""

    removed: int = 0
    absent: int = 0
    dirs_pruned: int = 0
    # (path, reason) for paths that could not be removed
    failed: list[tuple[str, str]] = field(default_factory=list)


def execute_removal(prefix: Path, paths: list[str]) -> RemovalResult:
    """Delete *paths* under *prefix* and prune emptied directories.

    Symlinks are unlinked, never followed.  Nothing here raises: a path
    already gone is counted (the user may have deleted it by hand), and one
    that cannot be removed — permission denied, or replaced by a directory
    since install — is recorded and reported rather than aborting the run.
    Stopping midway would strand the prefix with files removed and the
    lockfile not yet rewritten; finishing and reporting keeps the two
    consistent and lets a re-run converge.
    """
    result = RemovalResult()
    parents: set[Path] = set()
    for rel in paths:
        target = prefix / rel
        if not (target.is_symlink() or target.exists()):
            result.absent += 1
            continue
        try:
            target.unlink()
        except IsADirectoryError:
            result.failed.append((rel, "is a directory, not the file the bundle shipped"))
            continue
        except OSError as exc:
            result.failed.append((rel, str(exc)))
            continue
        result.removed += 1
        parents.add(target.parent)

    for d in sorted(parents, key=lambda p: len(p.parts), reverse=True):
        cur = d
        while cur != prefix and prefix in cur.parents:
            try:
                cur.rmdir()  # fails on a non-empty dir, which is the stop condition
            except OSError:
                break
            result.dirs_pruned += 1
            cur = cur.parent
    return result
