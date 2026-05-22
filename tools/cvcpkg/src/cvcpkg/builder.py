"""Recipe builder and packager for cvcpkg.

Implements the ``cvcpkg build`` and ``cvcpkg pack`` workflow described
in §7.4–7.5 of the split-distribution roadmap.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from cvcpkg.errors import CvcpkgError
from cvcpkg.platform import detect_platform


# ── Errors ──────────────────────────────────────────────────────

class RecipeError(CvcpkgError):
    """Problem loading or validating a recipe."""


class BuildError(CvcpkgError):
    """Build script exited with a non-zero status."""


class PackError(CvcpkgError):
    """Packaging stage failed (staging, manifest, or archiving)."""


# ── Data model ──────────────────────────────────────────────────

@dataclass
class SourceSpec:
    """Parsed ``source:`` block from recipe.yaml."""
    type: str  # tarball | git | vcpkg | brew | apt | vendored
    url: str = ""
    mirror: str = ""
    sha256: str = ""
    path: str = ""
    port: str = ""
    triplet: str = ""
    baseline: str = ""
    strip_components: int = 1

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
        )


@dataclass
class MatrixEntry:
    """One entry from ``build.matrix[]``."""
    platform: str
    script: str
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MatrixEntry:
        return cls(
            platform=d["platform"],
            script=d["script"],
            env=d.get("env", {}),
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

        return cls(
            name=recipe_block.get("name", recipe_dir.name),
            upstream_version=str(recipe_block.get("upstream_version", "0.0.0")),
            cvc_revision=int(recipe_block.get("cvc_revision", 1)),
            source=SourceSpec.from_dict(source_block),
            patches=raw.get("patches", []) or [],
            build_matrix=[MatrixEntry.from_dict(m) for m in build_block.get("matrix", [])],
            package_files=package_block.get("files", []),
            test_script=test_block.get("script") if test_block else None,
            raw=raw,
            recipe_dir=recipe_dir.resolve(),
        )


# ── Source fetching ─────────────────────────────────────────────

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch_tarball(source: SourceSpec, dest: Path) -> Path:
    """Download a tarball, verify SHA-256, and extract."""
    import urllib.request

    archive_path = dest / "source.tar.gz"
    urls = [u for u in (source.url, source.mirror) if u]
    if not urls:
        raise RecipeError("source.type=tarball but no URL specified")

    for url in urls:
        try:
            urllib.request.urlretrieve(url, archive_path)  # noqa: S310
            break
        except Exception:
            if url == urls[-1]:
                raise
            continue

    if source.sha256:
        actual = _sha256_file(archive_path)
        if actual != source.sha256:
            raise RecipeError(
                f"SHA-256 mismatch: expected {source.sha256}, got {actual}"
            )

    # Extract
    source_dir = dest / "src"
    source_dir.mkdir()
    with tarfile.open(archive_path) as tf:
        # Security: reject paths that escape the target directory
        for member in tf.getmembers():
            resolved = (source_dir / member.name).resolve()
            if not str(resolved).startswith(str(source_dir.resolve())):
                raise RecipeError(f"Tarball member escapes target: {member.name}")
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


def _resolve_vendored(source: SourceSpec, recipe_dir: Path) -> Path:
    """Locate vendored source tree relative to the repo."""
    if not source.path:
        raise RecipeError("source.type=vendored but no path specified")
    # Resolve relative to the repo root (parent of recipes/)
    repo_root = recipe_dir.parent.parent
    src = (repo_root / source.path).resolve()
    if not src.is_dir():
        raise RecipeError(f"Vendored source not found: {src}")
    return src


def fetch_source(recipe: Recipe, work_dir: Path) -> Path:
    """Fetch or locate the source tree for a recipe."""
    src = recipe.source
    if src.type == "tarball":
        return _fetch_tarball(src, work_dir)
    if src.type == "vendored":
        return _resolve_vendored(src, recipe.recipe_dir)
    if src.type in ("vcpkg", "brew", "apt"):
        # These are handled by the build script itself; return a
        # dummy source directory.
        dummy = work_dir / "src"
        dummy.mkdir(exist_ok=True)
        return dummy
    if src.type == "git":
        raise RecipeError("Git source fetching not yet implemented")
    raise RecipeError(f"Unknown source type: {src.type}")


# ── Patch application ──────────────────────────────────────────

def apply_patches(recipe: Recipe, source_dir: Path) -> None:
    """Apply patches listed in the recipe."""
    for patch_file in recipe.patches:
        patch_path = recipe.recipe_dir / patch_file
        if not patch_path.is_file():
            raise RecipeError(f"Patch file not found: {patch_path}")
        subprocess.run(
            ["patch", "-p1", "-i", str(patch_path)],
            cwd=source_dir,
            check=True,
        )


# ── Build execution ────────────────────────────────────────────

def _select_matrix_entry(recipe: Recipe, platform: str) -> MatrixEntry:
    """Pick the matrix entry matching the target platform."""
    for entry in recipe.build_matrix:
        if entry.platform == platform:
            return entry
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


def _build_env(ctx: BuildContext, matrix: MatrixEntry) -> dict[str, str]:
    """Construct the environment for the build script."""
    env = os.environ.copy()
    # Standard CVC env vars (§7.3 of the roadmap)
    env["CVC_PREFIX"] = str(ctx.install_dir)
    env["CVC_SOURCE_DIR"] = str(ctx.source_dir)
    env["CVC_BUILD_DIR"] = str(ctx.build_dir)
    env["CVC_INSTALL_DIR"] = str(ctx.install_dir)
    env["CVC_PLATFORM"] = ctx.platform
    env["CVC_CONFIG"] = ctx.config
    env["CVC_LINK"] = ctx.link
    env["CVC_COMPONENT"] = ctx.recipe.name
    env["CVC_VERSION"] = ctx.recipe.upstream_version

    build_type = "Release" if ctx.config == "release" else "Debug"
    env["CMAKE_BUILD_TYPE"] = build_type
    env.setdefault("BUILD_SHARED_LIBS", "ON" if ctx.link == "shared" else "OFF")

    # Merge matrix-entry env overrides
    env.update(matrix.env)
    return env


def run_build(ctx: BuildContext) -> None:
    """Execute the build script for the given context."""
    matrix = _select_matrix_entry(ctx.recipe, ctx.platform)
    script = ctx.recipe.recipe_dir / matrix.script

    if not script.is_file():
        raise BuildError(f"Build script not found: {script}")

    env = _build_env(ctx, matrix)

    # Determine the interpreter
    if script.suffix == ".sh":
        cmd = ["bash", str(script)]
    elif script.suffix == ".ps1":
        cmd = ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script)]
    else:
        raise BuildError(f"Unknown script type: {script.suffix}")

    ctx.build_dir.mkdir(parents=True, exist_ok=True)
    ctx.install_dir.mkdir(parents=True, exist_ok=True)

    print(f"cvcpkg: building {ctx.recipe.name} {ctx.recipe.full_version} "
          f"({ctx.platform}/{ctx.config}/{ctx.link})")
    print(f"cvcpkg: script: {script}")
    print(f"cvcpkg: install dir: {ctx.install_dir}")

    result = subprocess.run(
        cmd,
        cwd=ctx.build_dir,
        env=env,
    )
    if result.returncode != 0:
        raise BuildError(
            f"Build script for {ctx.recipe.name} exited with code {result.returncode}"
        )


# ── Test execution ──────────────────────────────────────────────

def run_test(ctx: BuildContext) -> None:
    """Run the recipe's test script if one exists."""
    if not ctx.recipe.test_script:
        return
    test_path = ctx.recipe.recipe_dir / ctx.recipe.test_script
    if not test_path.is_file():
        raise BuildError(f"Test script not found: {test_path}")

    env = os.environ.copy()
    env["CVC_PREFIX"] = str(ctx.install_dir)
    env["CVC_INSTALL_DIR"] = str(ctx.install_dir)

    print(f"cvcpkg: running test for {ctx.recipe.name}")
    result = subprocess.run(
        ["bash", str(test_path)],
        cwd=ctx.install_dir,
        env=env,
    )
    if result.returncode != 0:
        raise BuildError(
            f"Test for {ctx.recipe.name} failed with code {result.returncode}"
        )


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


def generate_manifest(recipe: Recipe, install_dir: Path,
                      platform: str, arch: str,
                      config: str, link: str) -> dict[str, Any]:
    """Generate a bundle manifest.yaml from the recipe + installed tree."""
    files = _file_list(install_dir)
    cmake_packages = recipe.raw.get("package", {}).get("cmake_packages", [])
    pkg_config = recipe.raw.get("package", {}).get("pkg_config", [])
    abi = recipe.raw.get("abi", {})
    depends = recipe.raw.get("depends", {}).get("build", [])

    # Normalize dep entries to dicts
    dep_list = []
    for d in depends:
        if isinstance(d, str):
            dep_list.append({"name": d})
        else:
            dep_list.append(d)

    manifest: dict[str, Any] = {
        "schema_version": 3,
        "bundle": {
            "name": recipe.name,
            "version": recipe.full_version,
            "upstream_version": recipe.upstream_version,
            "cvc_revision": recipe.cvc_revision,
            "platform": platform,
            "arch": arch,
            "config": config,
            "link": link,
            "abi": abi,
        },
        "depends": dep_list,
        "contents": {
            "files": files,
            "cmake_packages": cmake_packages,
            "pkg_config": pkg_config,
        },
        "meta": {
            "recipe_sha256": _sha256_file(recipe.recipe_dir / "recipe.yaml"),
            "built_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    return manifest


# ── Staging & archiving ─────────────────────────────────────────

def stage_bundle(install_dir: Path, manifest: dict[str, Any],
                 staging_dir: Path) -> None:
    """Copy the installed tree and manifest into a staging directory."""
    # Copy entire install tree
    if install_dir.is_dir():
        shutil.copytree(install_dir, staging_dir, dirs_exist_ok=True)

    # Write manifest
    manifest_dir = staging_dir / "share" / "libcvc-deps"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    with open(manifest_dir / "manifest.yaml", "w") as f:
        yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)


def _archive_tar_gz(staging_dir: Path, output: Path) -> str:
    """Create a deterministic .tar.gz archive. Returns SHA-256."""
    with tarfile.open(output, "w:gz") as tf:
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


def create_archive(staging_dir: Path, output_dir: Path,
                   name: str, version: str,
                   platform: str, arch: str,
                   config: str, link: str) -> tuple[Path, str, int]:
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

def build_recipe(
    recipe_dir: Path,
    *,
    platform: str = "",
    config: str = "release",
    link: str = "shared",
    prefix: Path | None = None,
    keep_build_dir: bool = False,
) -> BuildContext:
    """Build a single recipe. Returns the BuildContext."""
    recipe = Recipe.load(recipe_dir)
    if not platform:
        platform = detect_platform()

    work_dir = Path(tempfile.mkdtemp(prefix=f"cvcpkg-{recipe.name}-"))
    install_dir = prefix or (work_dir / "install")
    build_dir = work_dir / "build"

    source_dir = fetch_source(recipe, work_dir)
    if recipe.patches:
        apply_patches(recipe, source_dir)

    ctx = BuildContext(
        recipe=recipe,
        platform=platform,
        config=config,
        link=link,
        prefix=install_dir,
        source_dir=source_dir,
        build_dir=build_dir,
        install_dir=install_dir,
        work_dir=work_dir,
        keep_build_dir=keep_build_dir,
    )

    run_build(ctx)

    if recipe.test_script:
        run_test(ctx)

    if not keep_build_dir:
        # Clean up build dir but keep install
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
) -> tuple[Path, str, int]:
    """Build + package a recipe. Returns (archive_path, sha256, size)."""
    from cvcpkg.platform import detect_arch

    if not arch:
        arch = detect_arch()
    if output_dir is None:
        output_dir = Path.cwd() / "dist"

    ctx = build_recipe(
        recipe_dir,
        platform=platform,
        config=config,
        link=link,
        prefix=prefix,
        keep_build_dir=keep_build_dir,
    )

    manifest = generate_manifest(
        ctx.recipe, ctx.install_dir,
        ctx.platform, arch, ctx.config, ctx.link,
    )

    staging = ctx.work_dir / "staging"
    staging.mkdir()
    stage_bundle(ctx.install_dir, manifest, staging)

    archive_path, sha256, size = create_archive(
        staging, output_dir,
        ctx.recipe.name, ctx.recipe.full_version,
        ctx.platform, arch, ctx.config, ctx.link,
    )

    print(f"cvcpkg: packed {archive_path.name} ({size:,} bytes)")
    print(f"cvcpkg: sha256: {sha256}")

    # Cleanup
    if not keep_build_dir and ctx.work_dir.is_dir():
        shutil.rmtree(ctx.work_dir, ignore_errors=True)

    return archive_path, sha256, size


# ── Recipe listing / inspection ─────────────────────────────────

def find_recipes_dir() -> Path:
    """Locate the recipes/ directory relative to the repo root."""
    # Walk up from the cvcpkg package to find the repo
    pkg_dir = Path(__file__).resolve().parent
    for ancestor in pkg_dir.parents:
        candidate = ancestor / "recipes"
        if candidate.is_dir() and (candidate / "_common").is_dir():
            return candidate
    # Fallback: CWD
    candidate = Path.cwd() / "recipes"
    if candidate.is_dir():
        return candidate
    raise RecipeError("Cannot locate recipes/ directory")


def list_recipes(recipes_dir: Path | None = None) -> list[Recipe]:
    """Load all recipes from the recipes/ directory."""
    if recipes_dir is None:
        recipes_dir = find_recipes_dir()
    recipes = []
    for child in sorted(recipes_dir.iterdir()):
        recipe_yaml = child / "recipe.yaml"
        if child.is_dir() and recipe_yaml.is_file():
            recipes.append(Recipe.load(child))
    return recipes
