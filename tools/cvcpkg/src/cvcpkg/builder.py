"""Recipe builder and packager for cvcpkg.

Implements the ``cvcpkg build`` and ``cvcpkg pack`` workflow described
in §7.4–7.5 of the split-distribution roadmap.
"""

from __future__ import annotations

import hashlib
import os
import platform as _platform_module
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
            tags=recipe_block.get("tags", []) or [],
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
    """Download a tarball, verify SHA-256, and extract.

    If a source cache directory is configured (default
    ``~/.cache/cvcpkg/sources``), verified tarballs are stored there
    keyed by SHA-256.  Subsequent builds skip the download entirely
    when the cached file is present and matches.
    """
    import urllib.error
    import urllib.request

    archive_path = dest / "source.tar.gz"
    urls = [u for u in (source.url, source.mirror) if u]
    if not urls:
        raise RecipeError("source.type=tarball but no URL specified")

    cache_dir = _source_cache_dir()
    cache_hit = False

    # Try the cache first
    if cache_dir is not None:
        key = _cache_key(source)
        cached = cache_dir / f"{key}.tar.gz"
        if cached.is_file():
            # Verify integrity when SHA-256 is known
            if source.sha256:
                actual = _sha256_file(cached)
                if actual == source.sha256:
                    shutil.copy2(str(cached), str(archive_path))
                    cache_hit = True
                # Mismatched cache entry — re-download
            else:
                # No SHA-256 to check — trust the cache
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
        cached = cache_dir / f"{key}.tar.gz"
        shutil.copy2(str(archive_path), str(cached))

    # Extract
    source_dir = dest / "src"
    source_dir.mkdir()
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
    if src.type in ("vcpkg", "brew", "apt", "prebuilt"):
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
        patch_path = (recipe.recipe_dir / patch_file).resolve()
        # Security: ensure patch file doesn't escape the recipe directory
        if not str(patch_path).startswith(str(recipe.recipe_dir.resolve())):
            raise RecipeError(f"Patch path escapes recipe directory: {patch_file}")
        if not patch_path.is_file():
            raise RecipeError(f"Patch file not found: {patch_path}")
        result = subprocess.run(
            ["patch", "-p1", "-i", str(patch_path)],
            cwd=source_dir,
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            raise RecipeError(
                f"Failed to apply patch {patch_file}: "
                f"{result.stderr.decode(errors='replace').strip()}"
            )


# ── Build execution ────────────────────────────────────────────


def _select_matrix_entry(recipe: Recipe, platform: str, host_platform: str = "") -> MatrixEntry:
    """Pick the matrix entry matching the target platform.

    When *host_platform* is given (e.g. ``"linux"``, ``"windows"``),
    prefer entries whose ``host_platform`` matches.  This allows
    cross-compilation recipes (like wasm) to select the correct build
    script for the current host OS.

    Falls back to the first ``platform`` match when no
    ``host_platform`` match is found.
    """
    fallback: MatrixEntry | None = None
    for entry in recipe.build_matrix:
        if entry.platform != platform:
            continue
        if host_platform and entry.host_platform == host_platform:
            return entry
        if fallback is None:
            fallback = entry
    if fallback is not None:
        return fallback
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
    env["CVC_RECIPE_DIR"] = str(ctx.recipe.recipe_dir)

    # CVC_DEPS_PREFIX tells build.sh where to find previously-built
    # dependencies.  When building into a shared prefix this equals
    # install_dir; callers building into isolated per-component dirs
    # can override via the prefix field.
    env["CVC_DEPS_PREFIX"] = str(ctx.prefix)

    build_type = "Release" if ctx.config == "release" else "Debug"
    env["CMAKE_BUILD_TYPE"] = build_type
    env.setdefault("BUILD_SHARED_LIBS", "ON" if ctx.link == "shared" else "OFF")

    # Cross-compilation: set CVC_HOST_PLATFORM when the matrix
    # entry specifies a host_platform different from the target.
    if matrix.host_platform:
        env["CVC_HOST_PLATFORM"] = matrix.host_platform

    # If building for wasm and emsdk was built into the shared prefix,
    # point CVC_EMSDK_DIR there so build scripts can find it.
    if ctx.platform == "wasm" and "CVC_EMSDK_DIR" not in env:
        emsdk_env = ctx.prefix / "emsdk_env.sh"
        if emsdk_env.is_file():
            env["CVC_EMSDK_DIR"] = str(ctx.prefix)

    # Ensure host tools built into the prefix (cmake, ninja, protoc,
    # etc.) are found before system versions.
    bin_dirs = [
        str((ctx.prefix / "bin").resolve()),
        str((ctx.install_dir / "bin").resolve()),
    ]
    existing_path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join(bin_dirs + ([existing_path] if existing_path else []))

    # Ensure shared-library dependencies installed in the prefix are
    # discoverable at build time.  Build steps may invoke tools (e.g.
    # gRPC running protoc) that link against shared libs from earlier
    # recipes.
    lib_dirs = [
        str((ctx.prefix / "lib").resolve()),
        str((ctx.install_dir / "lib").resolve()),
    ]
    if sys.platform == "darwin":
        existing = env.get("DYLD_LIBRARY_PATH", "")
        env["DYLD_LIBRARY_PATH"] = ":".join(lib_dirs + ([existing] if existing else []))
    elif ctx.platform != "wasm":
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = ":".join(lib_dirs + ([existing] if existing else []))

    # Merge matrix-entry env overrides
    env.update(matrix.env)
    return env


def run_build(ctx: BuildContext) -> None:
    """Execute the build script for the given context."""
    matrix = _select_matrix_entry(ctx.recipe, ctx.platform, ctx.host_platform)
    script = ctx.recipe.recipe_dir / matrix.script

    if not script.is_file():
        raise BuildError(f"Build script not found: {script}")

    env = _build_env(ctx, matrix)

    # Determine the interpreter
    if script.suffix == ".sh":
        interpreter = _find_bash()
        cmd = [interpreter, str(script)]
    elif script.suffix == ".ps1":
        interpreter = shutil.which("pwsh")
        if not interpreter:
            raise BuildError("pwsh not found on PATH — required for .ps1 build scripts")
        cmd = [interpreter, "-NoProfile", "-NonInteractive", "-File", str(script)]
    else:
        raise BuildError(f"Unknown script type: {script.suffix}")

    ctx.build_dir.mkdir(parents=True, exist_ok=True)
    ctx.install_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"cvcpkg: building {ctx.recipe.name} {ctx.recipe.full_version} "
        f"({ctx.platform}/{ctx.config}/{ctx.link})"
    )
    print(f"cvcpkg: script: {script}")
    print(f"cvcpkg: install dir: {ctx.install_dir}")

    result = subprocess.run(
        cmd,
        cwd=ctx.build_dir.resolve(),
        env=env,
    )
    if result.returncode != 0:
        raise BuildError(f"Build script for {ctx.recipe.name} exited with code {result.returncode}")


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


def run_test(ctx: BuildContext) -> None:
    """Run the recipe's test script if one exists."""
    if not ctx.recipe.test_script:
        return
    test_path = ctx.recipe.recipe_dir / ctx.recipe.test_script
    if not test_path.is_file():
        raise BuildError(f"Test script not found: {test_path}")

    env = os.environ.copy()
    env["CVC_PREFIX"] = ctx.install_dir.as_posix()
    env["CVC_INSTALL_DIR"] = ctx.install_dir.as_posix()
    env["CVC_DEPS_PREFIX"] = ctx.prefix.as_posix()

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
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = ":".join(lib_dirs + ([existing] if existing else []))

    bash = _find_bash()
    print(f"cvcpkg: running test for {ctx.recipe.name}")

    result = subprocess.run(
        [bash, str(test_path)],
        cwd=ctx.install_dir,
        env=env,
    )
    if result.returncode != 0:
        raise BuildError(f"Test for {ctx.recipe.name} failed with code {result.returncode}")


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
) -> dict[str, Any]:
    """Generate a bundle manifest.yaml from the recipe + installed tree.

    When *all_recipes* is provided, ``recipe_sha256`` is a transitive
    dependency chain hash instead of a single-file hash.  This makes the
    hash sensitive to changes anywhere in the dependency tree.
    """
    files = _file_list(install_dir)
    cmake_packages = recipe.raw.get("package", {}).get("cmake_packages", [])
    pkg_config = recipe.raw.get("package", {}).get("pkg_config", [])
    abi = recipe.raw.get("abi", {})
    depends = recipe.raw.get("depends", {}).get("build", [])

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
            # Don't write platforms into the manifest — it's platform-specific
            entry: dict[str, str] = {"name": d["name"]}
            if d.get("org"):
                entry["org"] = d["org"]
            if d.get("version"):
                entry["version"] = d["version"]
            dep_list.append(entry)

    recipe_block = recipe.raw.get("recipe", {})
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
            **({"org": org_slug} if org_slug else {}),
        },
        "depends": dep_list,
        "contents": {
            "files": files,
            "cmake_packages": cmake_packages,
            "pkg_config": pkg_config,
        },
        "meta": {
            "recipe_sha256": (
                chain_hash(recipe, all_recipes, platform)
                if all_recipes
                else _sha256_file(recipe.recipe_dir / "recipe.yaml")
            ),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "maintainer": maintainer or recipe_block.get("maintainer", "Community"),
            "maintainer_email": recipe_block.get("maintainer_email", ""),
            "description": recipe_block.get("description", ""),
            "homepage": recipe_block.get("homepage", ""),
            "license": recipe_block.get("license", ""),
            "tags": ",".join(recipe.tags) if recipe.tags else "",
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
) -> None:
    """Copy the installed tree, manifest, and recipe into a staging directory."""
    # Copy entire install tree
    if install_dir.is_dir():
        shutil.copytree(install_dir, staging_dir, dirs_exist_ok=True)

    # Write manifest
    meta_dir = staging_dir / "share" / "libcvc-deps"
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


def build_recipe(
    recipe_dir: Path,
    *,
    platform: str = "",
    config: str = "release",
    link: str = "shared",
    prefix: Path | None = None,
    keep_build_dir: bool = False,
    host_platform: str = "",
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
        host_platform=host_platform,
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
    maintainer: str = "",
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
        ctx.recipe,
        ctx.install_dir,
        ctx.platform,
        arch,
        ctx.config,
        ctx.link,
        maintainer=maintainer,
    )

    staging = ctx.work_dir / "staging"
    staging.mkdir()
    stage_bundle(ctx.install_dir, manifest, staging, recipe_dir=ctx.recipe.recipe_dir)

    archive_path, sha256, size = create_archive(
        staging,
        output_dir,
        ctx.recipe.name,
        ctx.recipe.full_version,
        ctx.platform,
        arch,
        ctx.config,
        ctx.link,
    )

    print(f"cvcpkg: packed {archive_path.name} ({size:,} bytes)")
    print(f"cvcpkg: sha256: {sha256}")

    # Cleanup
    if not keep_build_dir and ctx.work_dir.is_dir():
        shutil.rmtree(ctx.work_dir, ignore_errors=True)

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
    """Extract build-dependency names from a recipe.

    If *platform* is given, dependencies with a ``platforms`` list that
    does not include *platform* are skipped.

    Both ``depends.build`` and ``depends.host_tools`` entries are
    returned so that host tools (cmake, ninja, etc.) are built before
    recipes that need them.

    Names are returned in qualified form (``org/name``) when the
    dependency specifies an organization.
    """
    depends = recipe.raw.get("depends", {})
    build_deps = depends.get("build", [])
    host_tools = depends.get("host_tools", [])
    names: list[str] = []
    for d in build_deps:
        if isinstance(d, str):
            names.append(d)
        elif isinstance(d, dict):
            plats = d.get("platforms")
            if plats and platform and platform not in plats:
                continue
            names.append(_dep_qualified_name(d))
    for t in host_tools:
        if isinstance(t, str):
            names.append(t)
        elif isinstance(t, dict):
            names.append(_dep_qualified_name(t))
    return names


def chain_hash(
    recipe: Recipe,
    all_recipes: dict[str, Recipe],
    platform: str = "",
    *,
    _seen: set[str] | None = None,
) -> str:
    """Compute a transitive dependency chain hash for *recipe*.

    The hash covers the recipe's own ``recipe.yaml`` content plus the
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
    # Recursively include dependency chain hashes (sorted for determinism)
    for dep_name in sorted(_dep_names(recipe, platform)):
        dep = all_recipes.get(dep_name)
        if dep is not None:
            dep_hash = chain_hash(dep, all_recipes, platform, _seen=_seen)
            if dep_hash:
                h.update(dep_hash.encode())
    return h.hexdigest()


def resolve_build_order(recipes: list[Recipe], platform: str = "") -> list[Recipe]:
    """Return *recipes* in topological (dependency-first) order.

    If *platform* is given, only dependencies that apply to that
    platform are considered when building the graph.

    Dependencies that are not in the candidate *recipes* list are
    silently skipped — they are assumed to be pre-installed in the
    prefix (e.g. emsdk built as a linux recipe before building wasm
    recipes).

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
            # Not in our candidate set — assumed pre-installed.
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


def build_all(
    recipes_dir: Path | list[Path],
    *,
    platform: str = "",
    config: str = "release",
    link: str = "shared",
    prefix: Path | None = None,
    keep_build_dir: bool = False,
    per_component: bool = False,
    host_platform: str = "",
    shard: tuple[int, int] | None = None,
    keep_going: bool = False,
) -> list[BuildContext]:
    """Build every recipe in dependency order into a shared *prefix*.

    *recipes_dir* may be a single path or a list of paths.  When
    multiple directories are given, later directories override
    earlier ones on name collisions (with a warning).

    When *per_component* is ``True``, each recipe is built into its
    own isolated install directory while using the shared *prefix*
    for finding previously-built dependencies via ``CVC_DEPS_PREFIX``.
    After each build, the install directory is merged into *prefix*
    so subsequent recipes can find the new files.  The returned
    ``BuildContext.install_dir`` points to the isolated per-recipe
    directory (useful for packaging only that recipe's files).

    Only recipes with a matrix entry for *platform* are built.
    Cross-platform dependencies (e.g. emsdk for wasm builds) are
    assumed to be pre-installed in the prefix.

    *host_platform*, when given, is forwarded to
    ``_select_matrix_entry`` so the correct build script is chosen
    for the current host OS (relevant for cross-compilation targets
    like wasm).

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
    """
    if isinstance(recipes_dir, list):
        all_recipes = load_all_recipes(recipes_dir)
    else:
        all_recipes = list_recipes(recipes_dir)
    # Filter to recipes that have a matrix entry for this platform
    if not platform:
        platform = detect_platform()
    recipes = [r for r in all_recipes if any(m.platform == platform for m in r.build_matrix)]
    ordered = resolve_build_order(recipes, platform)

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
        prefix = Path(tempfile.mkdtemp(prefix="cvcpkg-all-"))
    prefix = prefix.resolve()

    contexts: BuildAllResult = BuildAllResult()
    failures: list[BuildFailure] = []
    failed_names: set[str] = set()  # recipes that failed or were skipped
    for recipe in ordered:
        # Check if any dependency already failed.
        dep_names = _dep_names(recipe, platform)
        failed_deps = [d for d in dep_names if d in failed_names]
        if failed_deps and keep_going:
            msg = f"skipped (dependency failed: {', '.join(failed_deps)})"
            print(f"\ncvcpkg: == {recipe.name} ({recipe.full_version}) — {msg} ==")
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
        try:
            if per_component:
                work_dir = Path(tempfile.mkdtemp(prefix=f"cvcpkg-{recipe.name}-"))
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
                )
                run_build(ctx)
                if recipe.test_script:
                    run_test(ctx)
                # Merge this recipe's install into the shared prefix so
                # subsequent recipes can find it via CVC_DEPS_PREFIX.
                if install_dir.is_dir():
                    shutil.copytree(install_dir, prefix, dirs_exist_ok=True)
                if not keep_build_dir:
                    build_dir = work_dir / "build"
                    if build_dir.is_dir():
                        shutil.rmtree(build_dir, ignore_errors=True)
            else:
                ctx = build_recipe(
                    recipe.recipe_dir,
                    platform=platform,
                    config=config,
                    link=link,
                    prefix=prefix,
                    keep_build_dir=keep_build_dir,
                    host_platform=host_platform,
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

    if failures:
        print(f"\ncvcpkg: {len(contexts)} succeeded, {len(failures)} failed:")
        for f in failures:
            status = "SKIPPED (dep)" if f.skipped else "FAILED"
            print(f"  {status}: {f.recipe_name} — {f.error}")
    else:
        print(f"\ncvcpkg: all {len(contexts)} components built into {prefix}")

    # Attach failures to the returned list for callers to inspect.
    contexts.failures = failures
    return contexts


def find_recipes_dir() -> Path:
    """Locate the recipes/ directory.

    Search order:
    1. Bundled recipes shipped inside the installed package.
    2. Walk up from the package source to find a repo checkout.
    3. Fallback: recipes/ in the current working directory.
    """
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
