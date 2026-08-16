# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""``cvcpkg generate`` — derive a recipe from an existing buildable project.

``cvcpkg init`` scaffolds a recipe from a template and leaves every field as
a TODO.  ``generate`` starts from a project that already builds: it detects
the build system, reads whatever metadata that build system already
declares (name, version, description, homepage, licence, dependencies) and
emits a recipe with those fields filled in.

Supported build systems: CMake, Autotools, Meson, plain Makefile, and Python
packaging (PEP 621 ``pyproject.toml``, Poetry, ``setup.cfg``).

Two rules shape the output:

* **Never emit a dependency that does not resolve.**  ``cvcpkg validate``
  fails a recipe naming a dependency that is neither a recipe nor a
  ``provides`` slot, so a guessed name would produce a recipe that cannot
  even be validated.  Detected dependencies are matched against the real
  recipe set; anything unmatched is emitted as a commented suggestion.
* **Mark what was guessed.**  Anything the project did not actually state
  is written as a ``TODO`` so it shows up in review rather than silently
  shipping a wrong value.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import click

from cvcpkg.cli import cli
from cvcpkg.cli._helpers import _resolve_recipes_dirs

# ── Detection ───────────────────────────────────────────────────

#: Marker files, in precedence order.  Python comes first deliberately: a
#: scikit-build/pybind11 project has a CMakeLists.txt *and* a pyproject.toml,
#: and the supported way to build it is the Python one, which drives CMake
#: itself.  Picking cmake there would produce a recipe that builds the
#: extension but never installs the importable package.
_DETECTORS: list[tuple[str, tuple[str, ...]]] = [
    ("python", ("pyproject.toml", "setup.py", "setup.cfg")),
    ("cmake", ("CMakeLists.txt",)),
    ("meson", ("meson.build",)),
    ("autotools", ("configure.ac", "configure.in", "configure")),
    ("make", ("GNUmakefile", "Makefile", "makefile")),
]


def detect_build_system(project: Path) -> str | None:
    """Return the build system driving *project*, or None if unrecognised."""
    for system, markers in _DETECTORS:
        if any((project / m).is_file() for m in markers):
            return system
    return None


# ── Detected metadata ───────────────────────────────────────────


@dataclass
class ProjectInfo:
    """What a project's own build files say about it."""

    name: str = ""
    version: str = ""
    description: str = ""
    homepage: str = ""
    license: str = ""
    #: Dependency names as the upstream build system spells them
    #: (CMake package names, pkg-config module names, PyPI names).
    deps: list[str] = field(default_factory=list)
    #: Human-readable notes about what could not be determined.
    warnings: list[str] = field(default_factory=list)


def _read(path: Path, limit: int = 512 * 1024) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _strip_cmake_comments(text: str) -> str:
    return re.sub(r"(?m)#.*$", "", text)


#: Comparison operators that may appear in a pkg-config module list.
_PKG_OPS = {">=", "<=", "==", "!=", ">", "<", "="}


def _pkgconfig_modules(spec: str) -> list[str]:
    """Extract module names from a pkg-config module list.

    The syntax interleaves names and version constraints -- ``zlib >= 1.2
    libpng``, or ``zlib>=1.2, libpng`` -- so a naive whitespace split yields
    ``1.2`` as if it were a package.  Drop each operator and the version
    token that follows it.
    """
    tokens = [t for t in re.split(r"[\s,]+", spec.strip()) if t]
    modules: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in _PKG_OPS:
            skip_next = True  # the version that this operator constrains
            continue
        token = token.strip("[]\"'")
        if not token or token.startswith("$"):
            continue
        # Attached form: "zlib>=1.2" -> "zlib"
        name = re.split(r"[<>=!]", token, 1)[0].strip()
        if name:
            modules.append(name)
    return modules


def parse_cmake(project: Path) -> ProjectInfo:
    """Read ``project()`` and ``find_package()`` out of CMakeLists.txt."""
    info = ProjectInfo()
    text = _strip_cmake_comments(_read(project / "CMakeLists.txt"))

    m = re.search(r"\bproject\s*\(([^)]*)\)", text, re.IGNORECASE | re.DOTALL)
    if m:
        body = m.group(1)
        tokens = body.split()
        if tokens:
            info.name = tokens[0].strip("\"'")
        v = re.search(r"\bVERSION\s+([0-9][0-9A-Za-z.+-]*)", body, re.IGNORECASE)
        if v:
            info.version = v.group(1)
        d = re.search(r'\bDESCRIPTION\s+"([^"]*)"', body, re.IGNORECASE)
        if d:
            info.description = d.group(1).strip()
        h = re.search(r'\bHOMEPAGE_URL\s+"?([^"\s]+)"?', body, re.IGNORECASE)
        if h:
            info.homepage = h.group(1)

    # A CMake variable rather than a literal ("project(${FOO})") tells us
    # nothing; better to say so than to write '${FOO}' into the recipe.
    if info.name.startswith("$"):
        info.warnings.append(f"project() name is a CMake variable ({info.name}); using directory name")
        info.name = ""
    if info.version.startswith("$"):
        info.version = ""

    for dep in re.findall(r"\bfind_package\s*\(\s*([A-Za-z_][A-Za-z0-9_.+-]*)", text, re.IGNORECASE):
        if dep.lower() not in {"python", "python3", "pythoninterp", "pythonlibs", "threads"}:
            info.deps.append(dep)
    for mod in re.findall(
        r"\bpkg_check_modules\s*\(\s*\w+\s+((?:REQUIRED|QUIET|IMPORTED_TARGET|\s)*)([^)]*)\)",
        text,
        re.IGNORECASE,
    ):
        info.deps.extend(_pkgconfig_modules(mod[1]))
    return info


def parse_autotools(project: Path) -> ProjectInfo:
    """Read AC_INIT and the PKG_CHECK_MODULES/AC_CHECK_LIB probes."""
    info = ProjectInfo()
    src = ""
    for candidate in ("configure.ac", "configure.in"):
        if (project / candidate).is_file():
            src = _read(project / candidate)
            break

    if not src:
        # Only a generated ./configure — it still carries the substituted
        # PACKAGE_NAME/PACKAGE_VERSION near the top.
        conf = _read(project / "configure", limit=64 * 1024)
        n = re.search(r"^PACKAGE_NAME=['\"]([^'\"]*)['\"]", conf, re.M)
        v = re.search(r"^PACKAGE_VERSION=['\"]([^'\"]*)['\"]", conf, re.M)
        u = re.search(r"^PACKAGE_URL=['\"]([^'\"]*)['\"]", conf, re.M)
        if n:
            info.name = n.group(1)
        if v:
            info.version = v.group(1)
        if u:
            info.homepage = u.group(1)
        if not info.name:
            info.warnings.append("no configure.ac; could not read package name from ./configure")
        return info

    # AC_INIT(package, version, [bug-report], [tarname], [url]) -- the URL is
    # the *fifth* argument; [tarname] sits between it and the bug address.
    m = re.search(r"AC_INIT\s*\(([^)]*)\)", src, re.DOTALL)
    if m:
        args = [a.strip().strip("[]").strip() for a in m.group(1).split(",")]
        if args:
            info.name = args[0]
        if len(args) > 1:
            info.version = args[1]
        # Take the URL positionally when it is there, but fall back to any
        # argument that looks like one -- the optional args are routinely
        # left empty ("AC_INIT([x],[1],,,[http://…])").
        for arg in args[4:5] or []:
            if arg.startswith("http"):
                info.homepage = arg
        if not info.homepage:
            for arg in args[2:]:
                if arg.startswith("http"):
                    info.homepage = arg
                    break

    for mods in re.findall(r"PKG_CHECK_MODULES\s*\([^,]*,\s*\[?([^\],)]*)", src):
        info.deps.extend(_pkgconfig_modules(mods))
    for lib in re.findall(r"AC_CHECK_LIB\s*\(\s*\[?([A-Za-z0-9_+-]+)", src):
        info.deps.append(lib)
    return info


def parse_meson(project: Path) -> ProjectInfo:
    """Read project() and dependency() out of meson.build."""
    info = ProjectInfo()
    text = re.sub(r"(?m)#.*$", "", _read(project / "meson.build"))

    m = re.search(r"\bproject\s*\(\s*'([^']+)'", text)
    if m:
        info.name = m.group(1)
    v = re.search(r"\bversion\s*:\s*'([^']+)'", text)
    if v:
        info.version = v.group(1)
    lic = re.search(r"\blicense\s*:\s*'([^']+)'", text)
    if lic:
        info.license = lic.group(1)
    for dep in re.findall(r"\bdependency\s*\(\s*'([^']+)'", text):
        info.deps.append(dep)
    return info


def _parse_pyproject(path: Path, info: ProjectInfo) -> bool:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        info.warnings.append("tomllib unavailable; pyproject.toml not parsed")
        return False
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        info.warnings.append(f"could not parse pyproject.toml: {exc}")
        return False

    proj = data.get("project")
    if isinstance(proj, dict):  # PEP 621
        info.name = str(proj.get("name", "") or "")
        info.version = str(proj.get("version", "") or "")
        info.description = str(proj.get("description", "") or "")
        lic = proj.get("license")
        if isinstance(lic, str):
            info.license = lic
        elif isinstance(lic, dict):
            info.license = str(lic.get("text", "") or "")
        urls = proj.get("urls")
        if isinstance(urls, dict) and urls:
            for key in ("Homepage", "homepage", "Repository", "Source"):
                if key in urls:
                    info.homepage = str(urls[key])
                    break
            else:
                info.homepage = str(next(iter(urls.values())))
        for req in proj.get("dependencies", []) or []:
            name = re.split(r"[\s<>=!~;\[]", str(req).strip(), 1)[0]
            if name:
                info.deps.append(name)
        if not info.version and "version" in (proj.get("dynamic") or []):
            info.warnings.append("project.version is dynamic; set upstream_version by hand")
        return bool(info.name)

    poetry = data.get("tool", {}).get("poetry")
    if isinstance(poetry, dict):
        info.name = str(poetry.get("name", "") or "")
        info.version = str(poetry.get("version", "") or "")
        info.description = str(poetry.get("description", "") or "")
        info.license = str(poetry.get("license", "") or "")
        info.homepage = str(poetry.get("homepage", "") or poetry.get("repository", "") or "")
        for name in (poetry.get("dependencies") or {}):
            if name.lower() != "python":
                info.deps.append(name)
        return bool(info.name)
    return False


def parse_python(project: Path) -> ProjectInfo:
    """Read PEP 621 / Poetry / setup.cfg metadata."""
    info = ProjectInfo()
    pyproject = project / "pyproject.toml"
    if pyproject.is_file() and _parse_pyproject(pyproject, info):
        return info

    cfg = project / "setup.cfg"
    if cfg.is_file():
        import configparser

        parser = configparser.ConfigParser()
        try:
            parser.read(cfg, encoding="utf-8")
        except (OSError, configparser.Error) as exc:
            info.warnings.append(f"could not parse setup.cfg: {exc}")
        if parser.has_section("metadata"):
            meta = parser["metadata"]
            info.name = meta.get("name", "")
            info.version = meta.get("version", "")
            info.description = meta.get("description", "")
            info.license = meta.get("license", "")
            info.homepage = meta.get("url", "")
        if parser.has_section("options"):
            for line in (parser["options"].get("install_requires", "") or "").splitlines():
                dep = re.split(r"[\s<>=!~;\[]", line.strip(), 1)[0]
                if dep:
                    info.deps.append(dep)
        if info.name:
            return info

    if (project / "setup.py").is_file():
        # setup.py metadata is arbitrary code; running it to find out is not
        # something a generator should do to a user's checkout.
        text = _read(project / "setup.py")
        n = re.search(r"""\bname\s*=\s*['"]([^'"]+)['"]""", text)
        v = re.search(r"""\bversion\s*=\s*['"]([^'"]+)['"]""", text)
        if n:
            info.name = n.group(1)
        if v:
            info.version = v.group(1)
        info.warnings.append(
            "metadata read statically from setup.py (not executed); double-check it"
        )
    return info


def parse_make(project: Path) -> ProjectInfo:
    """A plain Makefile declares almost nothing; take what little there is."""
    info = ProjectInfo()
    for candidate in ("GNUmakefile", "Makefile", "makefile"):
        path = project / candidate
        if path.is_file():
            text = _read(path)
            v = re.search(r"(?m)^\s*(?:VERSION|PACKAGE_VERSION)\s*[:?]?=\s*(\S+)", text)
            if v:
                info.version = v.group(1)
            n = re.search(r"(?m)^\s*(?:PACKAGE|PACKAGE_NAME|PROG|TARGET)\s*[:?]?=\s*(\S+)", text)
            if n:
                info.name = n.group(1)
            break
    info.warnings.append(
        "plain Makefile: no standard metadata, and the install step is a guess "
        "(make install DESTDIR=...) -- check build.sh before using it"
    )
    return info


_PARSERS = {
    "cmake": parse_cmake,
    "autotools": parse_autotools,
    "meson": parse_meson,
    "python": parse_python,
    "make": parse_make,
}


# ── Dependency name mapping ─────────────────────────────────────

#: Upstream spelling -> cvcpkg recipe name, for cases where lowercasing is
#: not enough.  Only entries that differ are listed; everything else falls
#: back to a lowercase match against the real recipe set.
_DEP_ALIASES = {
    "png": "libpng",
    "libpng16": "libpng",
    "jpeg": "libjpeg-turbo",
    "libjpeg": "libjpeg-turbo",
    "tiff": "libtiff",
    "libtiff-4": "libtiff",
    "z": "zlib",
    "zlib": "zlib",
    "bzip2": "bzip2",
    "bz2": "bzip2",
    "lzma": "xz",
    "liblzma": "xz",
    "openssl": "openssl",
    "ssl": "openssl",
    "crypto": "openssl",
    "libssl": "openssl",
    "libcrypto": "openssl",
    "curl": "curl",
    "libcurl": "curl",
    "libxml-2.0": "libxml2",
    "libxml2": "libxml2",
    "freetype2": "freetype",
    "yaml-0.1": "libyaml",
    "sqlite3": "sqlite",
    "hdf5": "hdf5",
    "boost": "boost",
    "eigen3": "eigen",
    "gsl": "gsl",
    "fftw3": "fftw3",
    "protobuf": "protobuf",
    "grpc++": "grpc",
    "zstd": "zstd",
    "libzstd": "zstd",
}


def _known_recipe_names(recipes_dirs: tuple[str, ...], no_default: bool) -> set[str]:
    """Names cvcpkg can actually resolve, so we never emit a dangling dep."""
    try:
        from cvcpkg.builder import list_recipes

        dirs = _resolve_recipes_dirs(recipes_dirs, no_default=no_default)
    except Exception:
        return set()

    names: set[str] = set()
    for d in dirs:
        try:
            for r in list_recipes(Path(d)):
                names.add(r.name)
                for slot in getattr(r, "provides", None) or []:
                    names.add(str(slot))
        except Exception:
            continue
    return names


def map_dependencies(raw: list[str], known: set[str]) -> tuple[list[str], list[str]]:
    """Split detected dependency names into (resolved, unresolved).

    Resolved names are safe to write into ``depends:``; unresolved ones are
    returned so the caller can leave them as commented hints.
    """
    resolved: list[str] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    for dep in raw:
        base = dep.strip()
        if not base or base in seen:
            continue
        seen.add(base)
        lowered = base.lower()
        candidate = _DEP_ALIASES.get(lowered, lowered)
        if not known:
            # No recipe set to check against: be conservative and suggest
            # everything rather than emit deps that may not exist.
            unresolved.append(f"{base} -> {candidate}?")
        elif candidate in known:
            if candidate not in resolved:
                resolved.append(candidate)
        else:
            unresolved.append(base)
    return resolved, unresolved


# ── Source block ────────────────────────────────────────────────


def _git(project: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def detect_source(project: Path, info: ProjectInfo) -> list[str]:
    """Return the YAML lines for the recipe's ``source:`` block."""
    if (project / ".git").exists():
        url = _git(project, "remote", "get-url", "origin")
        commit = _git(project, "rev-parse", "HEAD")
        tag = _git(project, "describe", "--tags", "--exact-match")
        if url:
            if url.startswith("git@"):  # scp-style -> https, so anyone can fetch
                url = re.sub(r"^git@([^:]+):", r"https://\1/", url)
            lines = ["source:", "  type: git", f"  url: {url}"]
            if commit:
                lines.append(f"  commit: {commit}")
            if tag and not info.version:
                info.version = tag.lstrip("v")
            lines.append("  submodules: false")
            return lines
        info.warnings.append("git checkout has no 'origin' remote; source left as a TODO")

    return [
        "source:",
        "  # TODO point this at a fetchable release. 'vendored' builds the",
        "  # tree at 'path' (relative to the recipes dir); switch to",
        "  # 'tarball' with a url + sha256 to publish reproducibly.",
        "  type: tarball",
        "  url: https://example.com/TODO-source.tar.gz",
        '  # sha256: "0000000000000000000000000000000000000000000000000000000000000000"',
        "  strip_components: 1",
    ]


# ── Build scripts ───────────────────────────────────────────────

_SH_HEADER = """\
#!/usr/bin/env bash
# recipes/{name}/build.sh -- generated by 'cvcpkg generate' from a {system} project.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${{SCRIPT_DIR}}/../_common/env-${{CVC_PLATFORM}}.sh"
"""

_SH_BODY = {
    "cmake": """
# cvc_cmake_build configures, builds and installs with the prefix, build type
# and link mode cvcpkg selected.  Append -D flags as extra arguments.
cvc_cmake_build
""",
    "meson": """
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${PKG_CONFIG_PATH:-}"

cd "${CVC_SOURCE_DIR}"
meson setup "${CVC_BUILD_DIR}" \\
    --prefix="${CVC_INSTALL_DIR}" \\
    --buildtype=release
ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install
""",
    "autotools": """
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${PKG_CONFIG_PATH:-}"

cd "${CVC_SOURCE_DIR}"
# A git checkout has no ./configure until autogen/autoreconf has run.
if [ ! -x ./configure ]; then
    if [ -x ./autogen.sh ]; then ./autogen.sh; else autoreconf -fi; fi
fi
./configure --prefix="${CVC_INSTALL_DIR}" \\
    --with-sysroot="${CVC_DEPS_PREFIX}"
make -j "${CVC_JOBS}"
make install
""",
    "make": """
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${PKG_CONFIG_PATH:-}"

cd "${CVC_SOURCE_DIR}"
# TODO a plain Makefile has no agreed install convention.  This assumes
# 'make install' honours PREFIX; if it uses DESTDIR, or installs nothing at
# all, adjust here (and check what lands in package.files).
make -j "${CVC_JOBS}" PREFIX="${CVC_INSTALL_DIR}"
make install PREFIX="${CVC_INSTALL_DIR}"
""",
    "python": """
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
export CMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}:${CMAKE_PREFIX_PATH:-}"

cd "${CVC_SOURCE_DIR}"
# --no-deps: cvcpkg resolves the dependency closure itself, so pip must not
# reach out to PyPI mid-build and install a second, unmanaged copy.
"${CVC_PYTHON:-python3}" -m pip install . \\
    --no-deps \\
    --no-build-isolation \\
    --prefix="${CVC_INSTALL_DIR}"
""",
}

# NOTE: these are substituted with str.replace, not str.format -- PowerShell
# is full of literal braces and every one of them would have to be doubled.
_PS1_CMAKE = """\
# recipes/@NAME@/build.ps1 -- generated by 'cvcpkg generate'.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @()
"""

_PS1_PYTHON = """\
# recipes/@NAME@/build.ps1 -- generated by 'cvcpkg generate'.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\\..\\_common\\env-windows.ps1"

Push-Location $env:CVC_SOURCE_DIR
& python -m pip install . --no-deps --no-build-isolation --prefix="$env:CVC_INSTALL_DIR"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
Pop-Location
"""

#: Host tools each build system needs beyond a compiler.
_HOST_TOOLS = {
    "cmake": ["cmake", "ninja"],
    "meson": ["meson", "ninja", "pkg-config"],
    "autotools": ["make", "pkg-config"],
    "make": ["make", "pkg-config"],
    "python": ["python"],
}

#: Which systems have a usable Windows story out of the box.
_WINDOWS_CAPABLE = {"cmake", "python"}

#: What each build system typically installs, as package.files globs.
_PACKAGE_FILES = {
    "cmake": ["include/", "lib/"],
    "meson": ["include/", "lib/"],
    "autotools": ["include/", "lib/"],
    "make": ["include/", "lib/", "bin/"],
    "python": ["lib/python*/site-packages/", "Lib/site-packages/"],
}


# ── Recipe rendering ────────────────────────────────────────────


def render_recipe(
    *,
    name: str,
    system: str,
    info: ProjectInfo,
    source_lines: list[str],
    resolved: list[str],
    unresolved: list[str],
) -> str:
    """Render the recipe.yaml text for a detected project."""

    def todo(value: str, hint: str) -> str:
        return value if value else f"TODO {hint}"

    lines = [
        "schema_version: 1",
        "",
        "# Generated by 'cvcpkg generate' from a detected " + system + " project.",
        "# Every TODO below is something the project did not declare.",
        "",
        "recipe:",
        f"  name: {name}",
        f'  upstream_version: "{info.version or "0.0.0"}"',
        "  cvc_revision: 1",
        f'  maintainer: "{todo("", "your name")}"',
        f'  maintainer_email: "{todo("", "you@example.com")}"',
    ]
    if info.homepage:
        lines.append(f"  homepage: {info.homepage}")
    else:
        lines.append("  # homepage: https://example.com")
    lines.append(f'  license: "{info.license or "TODO-SPDX"}"')
    lines.append("  tags: []")
    desc = info.description.replace("\n", " ").strip() or f"TODO one-line description of {name}."
    lines += ["  description: >-", f"    {desc}", ""]

    lines += source_lines + ["", "patches: []", "", "depends:"]

    if resolved:
        lines.append("  build:")
        lines += [f"    - name: {d}" for d in resolved]
    else:
        lines.append("  build: []")
    lines.append("  runtime: []")

    if unresolved:
        lines.append(
            "  # Detected in the project but not matched to a cvcpkg recipe."
        )
        lines.append(
            "  # Add a recipe for each one you need, then move it up into build:."
        )
        lines += [f"  #   - {d}" for d in unresolved]

    lines.append("  host_tools:")
    lines += [f"    - {t}" for t in _HOST_TOOLS[system]]
    lines.append("")

    lines.append("build:")
    lines.append("  matrix:")
    lines.append("    - platform: linux")
    lines.append("      script: build.sh")
    lines.append("    - platform: macos")
    lines.append("      script: build.sh")
    if system in _WINDOWS_CAPABLE:
        lines.append("    - platform: windows")
        lines.append("      script: build.ps1")
    else:
        lines.append(
            f"    # No windows entry: a {system} build needs a POSIX shell. Add a"
        )
        lines.append("    # build.ps1 (or an MSYS2 wrapper) and a windows row when ready.")
    lines.append("")

    lines.append("package:")
    lines.append("  # Globs relative to the install prefix. Narrow these to what the")
    lines.append("  # component really installs before publishing.")
    lines.append("  files:")
    lines += [f"    - {g}" for g in _PACKAGE_FILES[system]]
    lines.append("")
    return "\n".join(lines)


# ── Command ─────────────────────────────────────────────────────


@cli.command("generate")
@click.argument(
    "project_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    required=False,
)
@click.option("--name", "name_opt", default="", help="Recipe name (default: the project's own).")
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
    type=click.Choice(["auto", "cmake", "autotools", "meson", "make", "python"]),
    default="auto",
    show_default=True,
    help="Override build-system detection.",
)
@click.option(
    "--recipes-dir",
    "known_dirs",
    type=click.Path(),
    multiple=True,
    help="Recipe directory to resolve detected dependencies against (repeatable).",
)
@click.option(
    "--no-default-recipes",
    is_flag=True,
    help="Do not consult the default recipe set when resolving dependencies.",
)
@click.option("--dry-run", is_flag=True, help="Print the recipe instead of writing it.")
@click.option("--force", is_flag=True, help="Overwrite an existing recipe directory.")
def generate(
    project_dir: str,
    name_opt: str,
    recipes_dir: str,
    build_system: str,
    known_dirs: tuple[str, ...],
    no_default_recipes: bool,
    dry_run: bool,
    force: bool,
) -> None:
    """Generate a recipe from an existing buildable project.

    Detects the project's build system -- CMake, Autotools, Meson, a plain
    Makefile, or Python packaging -- reads the metadata it already declares,
    and writes a recipe with those fields filled in and a matching build
    script.  Anything the project did not state is left as a TODO.

    Detected dependencies are matched against the recipes cvcpkg can actually
    resolve; unmatched ones are listed as comments rather than written into
    depends:, so the result validates as generated.

    \b
    Examples:
      cvcpkg generate ../mylib                 # detect and write recipes/mylib
      cvcpkg generate . --dry-run              # show what it would write
      cvcpkg generate ../old-proj --build-system autotools
      cvcpkg generate ../pytool --name pytool --dir ./my-recipes
    """
    project = Path(project_dir).resolve()

    system = build_system if build_system != "auto" else detect_build_system(project)
    if system is None:
        raise click.ClickException(
            f"could not detect a build system in {project}.\n"
            "Looked for: pyproject.toml/setup.py, CMakeLists.txt, meson.build, "
            "configure.ac, Makefile.\n"
            "Pass --build-system to force one, or use 'cvcpkg init' for a blank recipe."
        )

    info = _PARSERS[system](project)

    name = name_opt or info.name or project.name
    name = name.strip().lower().replace("_", "-").replace(" ", "-")
    if not re.match(r"^[a-z][a-z0-9.-]*$", name):
        raise click.ClickException(
            f"derived recipe name {name!r} is not usable; pass --name explicitly"
        )

    known = _known_recipe_names(known_dirs, no_default_recipes)
    resolved, unresolved = map_dependencies(info.deps, known)
    source_lines = detect_source(project, info)  # may fill in info.version from a tag

    recipe_text = render_recipe(
        name=name,
        system=system,
        info=info,
        source_lines=source_lines,
        resolved=resolved,
        unresolved=unresolved,
    )

    build_sh = _SH_HEADER.format(name=name, system=system) + _SH_BODY[system]
    build_ps1 = ""
    if system in _WINDOWS_CAPABLE:
        template = _PS1_PYTHON if system == "python" else _PS1_CMAKE
        build_ps1 = template.replace("@NAME@", name)

    click.echo(f"cvcpkg: detected a {system} project in {project}")
    if info.version:
        click.echo(f"  version:      {info.version}")
    if resolved:
        click.echo(f"  dependencies: {', '.join(resolved)}")
    if unresolved:
        click.echo(f"  unmatched:    {', '.join(unresolved)} (left as comments)")
    for warn in info.warnings:
        click.echo(f"  note: {warn}", err=True)

    if dry_run:
        click.echo("")
        click.echo(f"--- recipes/{name}/recipe.yaml ---")
        click.echo(recipe_text)
        click.echo(f"--- recipes/{name}/build.sh ---")
        click.echo(build_sh)
        if build_ps1:
            click.echo(f"--- recipes/{name}/build.ps1 ---")
            click.echo(build_ps1)
        return

    target = Path(recipes_dir) / name
    if target.exists() and not force:
        raise click.ClickException(f"{target} already exists (use --force to overwrite)")
    target.mkdir(parents=True, exist_ok=True)

    (target / "recipe.yaml").write_text(recipe_text, encoding="utf-8")
    sh_path = target / "build.sh"
    sh_path.write_text(build_sh, encoding="utf-8", newline="\n")
    sh_path.chmod(0o755)
    written = ["recipe.yaml", "build.sh"]
    if build_ps1:
        (target / "build.ps1").write_text(build_ps1, encoding="utf-8")
        written.append("build.ps1")

    click.echo("")
    click.echo(f"cvcpkg: wrote recipe {name!r} to {target}")
    for f in written:
        click.echo(f"  {target / f}")
    click.echo("")
    click.echo("Next steps:")
    click.echo("  1. Resolve the TODOs in recipe.yaml (source, maintainer, licence).")
    click.echo("  2. Narrow package.files to what this component installs.")
    click.echo(f"  3. Validate:  cvcpkg validate {target}")
    click.echo(f"  4. Build it:  cvcpkg build {name} --recipes-dir {recipes_dir} --prefix ./deps")
