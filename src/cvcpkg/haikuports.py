# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""HaikuPorts integration — draft a ``.recipe`` from a cvcpkg ``recipe.yaml``.

`HaikuPorts <https://github.com/haikuports/haikuports>`_ is Haiku's ports
tree.  A port is a *bash script* — ``<category>/<port>/<port>-<version>.recipe``
— that ``haikuporter`` sources for its uppercase variables and then calls back
into for its ``PATCH()``/``BUILD()``/``INSTALL()``/``TEST()`` phases.

This module converts one cvcpkg recipe into a **draft** of that file, for a
Haiku developer to finish and run under their own ``haikuporter``.  Draft is
the operative word, and it is a design constraint rather than a disclaimer:

* The tool stops at a file on disk.  Nothing here opens a pull request, and
  there is deliberately no code path that could: HaikuPorts' pull-request
  template opens with the checkbox *"You are not a robot."*, followed by *"The
  modified recipe was confirmed to build on your Haiku machine."*, and only the
  person at the keyboard can attest to either.  Whether a draft is ever
  upstreamed is entirely that person's decision, under their own name.
* ``COPYRIGHT`` (year + holder, no e-mail addresses) has no counterpart anywhere
  in cvcpkg's schema.  It is emitted empty with a ``# TODO(human):`` — the same
  hand-off ``haikuporter/tools/cargo-to-recipe.sh`` makes.
* Correct ``PROVIDES``/``REQUIRES`` are a property of the *installed tree*, not
  of the recipe source: ``haikuporter``'s ``Policy.py`` cross-checks every
  ``cmd:`` against ``bin/``, every ``lib:`` against the real SONAMEs, and every
  ``REQUIRES`` against ELF ``DT_NEEDED``.  So :func:`draft_recipe` takes a
  second argument, :class:`InstallFacts`, harvested from a real Haiku build by
  :func:`scan_install_tree`.  Without it those blocks degrade to TODOs instead
  of being guessed.
* ``BUILD()``/``INSTALL()`` bodies are never machine-translated.  cvcpkg builds
  fat, relocatable, ``$ORIGIN``-rpath'd prefixes in FHS layout; a Haiku port
  installs one shared library into packagefs' fixed layout (``develop/headers``,
  ``develop/lib``, ``data/``) and splits ``_devel`` off with ``packageEntries``.
  The two models disagree, so the phases are emitted as loud, non-executing
  stubs with the cvcpkg build script quoted underneath as reference.

The companion pieces are ``integrations/haikuports/lint-draft.sh`` (runs
HaikuPorts' *own* ``haikuporter --lint``, the way their ``lint-new-recipes.sh``
does) and :mod:`cvcpkg.cli._haiku` (the ``cvcpkg haiku`` command group).
See ``docs/haikuports-integration.md``.

No network and no subprocess live in this module; it is pure text and pure
filesystem inspection, exactly like :mod:`cvcpkg.cpkg`.
"""

from __future__ import annotations

import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cvcpkg.errors import CvcpkgError

#: Marker every unfillable field carries.  Grep-able, and deliberately the same
#: token in the recipe body, the stderr checklist and the docs.
TODO = "# TODO(human):"

#: Literal tab — HaikuPorts indents inside multi-line strings with tabs.
TAB = "\t"


class HaikuPortsError(CvcpkgError):
    """A cvcpkg recipe could not be expressed as a HaikuPorts recipe."""


class ConversionRefusedError(HaikuPortsError):
    """The recipe has a shape this converter will not pretend to handle."""


# ── Curated tables ──────────────────────────────────────────────
#
# These are *data*, verified against the live trees, not heuristics.  Getting
# them wrong is visible to reviewers (PR checklist items 3 and 5), so nothing
# here is inferred at run time.

#: The well-known Haiku licence names (``/system/data/licenses`` on a Haiku
#: box; the ``waddlesplash/haiku-licenses`` repo in HaikuPorts CI).  Anything
#: not in this set has to ship as a port-local ``licenses/<Name>`` file.
HAIKU_LICENSES = frozenset(
    {
        "Anti-Grain Geometry",
        "Apache v2",
        "Artistic",
        "Artistic v2.0",
        "BSD (2-clause)",
        "BSD (3-clause)",
        "BSD (4-clause)",
        "Be Sample Code License",
        "Bullet",
        "CDDL v1",
        "CQuantizer",
        "DEC",
        "GLUT (Mark Kilgard)",
        "GNU GPL font exception",
        "GNU GPL v1",
        "GNU GPL v2",
        "GNU GPL v3",
        "GNU LGPL v2",
        "GNU LGPL v2.1",
        "GNU LGPL v3",
        "GPC",
        "Intel (2xxx firmware)",
        "Intel (ACPICA)",
        "Intel (firmware)",
        "LibHTTPd",
        "LibJPEG",
        "LibPNG",
        "MAPM",
        "MIT",
        "MIT (no promotion)",
        "MPL v1.1",
        "MPL v2.0",
        "Marvell (firmware)",
        "OpenGroup",
        "PDFlib Lite",
        "Public Domain",
        "Ralink (firmware)",
        "SGI Free B",
        "SIL Open Font License v1.1",
        "Zlib",
    }
)

#: SPDX identifier -> Haiku licence name.  Haiku's list is human-style, not
#: SPDX ("Apache v2", not "Apache-2.0"), despite the wiki suggesting otherwise.
SPDX_TO_HAIKU = {
    "0BSD": "Public Domain",
    "Apache-2.0": "Apache v2",
    "Artistic-1.0-Perl": "Artistic",
    "Artistic-2.0": "Artistic v2.0",
    "BSD-2-Clause": "BSD (2-clause)",
    "BSD-3-Clause": "BSD (3-clause)",
    "BSD-4-Clause": "BSD (4-clause)",
    "CDDL-1.0": "CDDL v1",
    "GPL-1.0-only": "GNU GPL v1",
    "GPL-1.0-or-later": "GNU GPL v1",
    "GPL-2.0-only": "GNU GPL v2",
    "GPL-2.0-or-later": "GNU GPL v2",
    "GPL-3.0-only": "GNU GPL v3",
    "GPL-3.0-or-later": "GNU GPL v3",
    "IJG": "LibJPEG",
    "LGPL-2.0-only": "GNU LGPL v2",
    "LGPL-2.0-or-later": "GNU LGPL v2",
    "LGPL-2.1-only": "GNU LGPL v2.1",
    "LGPL-2.1-or-later": "GNU LGPL v2.1",
    "LGPL-3.0-only": "GNU LGPL v3",
    "LGPL-3.0-or-later": "GNU LGPL v3",
    "Libpng": "LibPNG",
    "MIT": "MIT",
    "MPL-1.1": "MPL v1.1",
    "MPL-2.0": "MPL v2.0",
    "OFL-1.1": "SIL Open Font License v1.1",
    "SGI-B-2.0": "SGI Free B",
    "Unlicense": "Public Domain",
    "Zlib": "Zlib",
}

#: cvcpkg recipe name -> HaikuPorts ``<category>/<port>``.  Every entry was read
#: off the live master tree of haikuports (3,524 port directories); the category
#: mirrors Gentoo's and is *not* derivable from the name (zlib is ``sys-libs``,
#: not ``dev-libs``), which is exactly why this is a table.
KNOWN_PORTS: dict[str, str] = {
    "abseil": "dev-cpp/abseil-cpp",
    "aspell": "app-text/aspell",
    "assimp": "media-libs/assimp",
    "autoconf": "dev-build/autoconf",
    "automake": "dev-build/automake",
    "bison": "sys-devel/bison",
    "boost": "dev-libs/boost",
    "bzip2": "app-arch/bzip2",
    "c-ares": "net-dns/c_ares",
    "cairo": "x11-libs/cairo",
    "cgal": "sci-mathematics/cgal",
    "cmake": "dev-build/cmake",
    "curl": "net-misc/curl",
    "dav1d": "media-libs/dav1d",
    "expat": "dev-libs/expat",
    "ffmpeg": "media-video/ffmpeg",
    "fftw3": "sci-libs/fftw",
    "flac": "media-libs/flac",
    "flex": "sys-devel/flex",
    "fontconfig": "media-libs/fontconfig",
    "freetype": "media-libs/freetype",
    "fribidi": "dev-libs/fribidi",
    "gdk-pixbuf": "x11-libs/gdk-pixbuf",
    "gettext": "sys-devel/gettext",
    "glew": "media-libs/glew",
    "glfw": "media-libs/glfw",
    "glib": "dev-libs/glib",
    "glu": "sys-libs/glu",
    "gmp": "dev-libs/gmp",
    "graphene": "media-libs/graphene",
    "grpc": "net-libs/grpc",
    "gsl": "sci-libs/gsl",
    "gstreamer": "media-libs/gstreamer",
    "gtk4": "x11-libs/gtk4",
    "harfbuzz": "media-libs/harfbuzz",
    "hdf5": "sci-libs/hdf5",
    "iconv": "dev-libs/libiconv",
    "imagemagick": "media-gfx/imagemagick",
    "imgui": "gui-libs/imgui",
    "jam": "sys-devel/jam",
    "krb5": "net-misc/krb5",
    "lame": "media-sound/lame",
    "libepoxy": "media-libs/libepoxy",
    "libffi": "dev-libs/libffi",
    "libgeos": "sci-libs/geos",
    "libglvnd": "sys-libs/libglvnd",
    "libice": "x11-libs/libice",
    "libogg": "media-libs/libogg",
    "libopus": "media-libs/opus",
    "libpng": "media-libs/libpng",
    "libsamplerate": "media-libs/libsamplerate",
    "libsm": "x11-libs/libsm",
    "libsndfile": "media-libs/libsndfile",
    "libspatialindex": "sci-libs/libspatialindex",
    "libtool": "dev-build/libtool",
    "libunistring": "dev-libs/libunistring",
    "libvorbis": "media-libs/libvorbis",
    "libvpx": "media-libs/libvpx",
    "libwebp": "media-libs/libwebp",
    "libx11": "x11-libs/libx11",
    "libxau": "x11-libs/libxau",
    "libxcb": "x11-libs/libxcb",
    "libxcursor": "x11-libs/libxcursor",
    "libxdmcp": "x11-libs/libxdmcp",
    "libxext": "x11-libs/libxext",
    "libxfixes": "x11-libs/libxfixes",
    "libxi": "x11-libs/libxi",
    "libxinerama": "x11-libs/libxinerama",
    "libxml2": "dev-libs/libxml2",
    "libxrandr": "x11-libs/libxrandr",
    "libxrender": "x11-libs/libxrender",
    "libxscrnsaver": "x11-libs/libxscrnsaver",
    "libxt": "x11-libs/libxt",
    "libxtst": "x11-libs/libxtst",
    "llvm": "sys-devel/llvm",
    "lua": "dev-lang/lua",
    "lz4": "app-arch/lz4",
    "m4": "sys-devel/m4",
    "make": "sys-devel/make",
    "meson": "dev-build/meson",
    "miniupnpc": "net-libs/miniupnpc",
    "mpfr": "dev-libs/mpfr",
    "nasm": "dev-lang/nasm",
    "ncurses": "sys-libs/ncurses",
    "ninja": "dev-build/ninja",
    "openal-soft": "media-libs/openal",
    "openblas": "sci-libs/openblas",
    "openssh": "net-misc/openssh",
    "openssl": "dev-libs/openssl",
    "opusfile": "media-libs/opusfile",
    "pango": "x11-libs/pango",
    "patchelf": "dev-util/patchelf",
    "pcre2": "dev-libs/libpcre2",
    "perl": "dev-lang/perl",
    "physfs": "dev-games/physfs",
    "pixman": "x11-libs/pixman",
    "pkg-config": "dev-util/pkgconfig",
    "portaudio": "media-libs/portaudio",
    "protobuf": "dev-libs/protobuf",
    "python": "dev-lang/python",
    "qt6": "dev-qt/qt6-base",
    "re2": "dev-libs/re2",
    "readline": "sys-libs/readline",
    "rust": "dev-lang/rust",
    "sdl3": "media-libs/libsdl3",
    "sqlite": "dev-db/sqlite",
    "swig": "dev-lang/swig",
    "tiff": "media-libs/tiff",
    "vorbis-tools": "media-sound/vorbis_tools",
    "wayland": "dev-libs/wayland",
    "wayland-protocols": "dev-libs/wayland-protocols",
    "x264": "media-libs/x264",
    "x265": "media-libs/x265",
    "xcb-proto": "x11-proto/xcb_proto",
    "xcb-util-keysyms": "x11-libs/xcb-util-keysyms",
    "xkbcommon": "x11-libs/libxkbcommon",
    "xtrans": "x11-libs/xtrans",
    "xz": "app-arch/xz_utils",
    "yaml": "dev-libs/libyaml",
    "yaml-cpp": "dev-cpp/yaml_cpp",
    "zlib": "sys-libs/zlib",
    "zstd": "app-arch/zstd",
}

#: Categories *proposed* for cvcpkg packages that HaikuPorts does not carry at
#: all.  These are the interesting ones — see the candidate table in
#: ``docs/haikuports-integration.md`` — but the category is our suggestion, not
#: an observation, so a draft using one also gets a TODO to confirm it against
#: Gentoo's overlay (https://gpo.zugaina.org), as the PR checklist requires.
PROPOSED_PORTS: dict[str, str] = {
    "joltphysics": "sci-physics/joltphysics",
    "lerc": "sci-libs/lerc",
    "levmar": "sci-libs/levmar",
    "log4cplus": "dev-libs/log4cplus",
    "nfft3": "sci-libs/nfft3",
    "vcglib": "sci-libs/vcglib",
    "vtk": "sci-visualization/vtk",
}

#: cvcpkg build dependency -> the ``cmd:`` resolvable a HaikuPorts recipe would
#: name in ``BUILD_PREREQUIRES``.  Anything not listed here is treated as a
#: library dependency (``BUILD_REQUIRES``) instead.
TOOL_COMMANDS: dict[str, str] = {
    "autoconf": "cmd:autoconf",
    "automake": "cmd:automake",
    "bison": "cmd:bison",
    "cmake": "cmd:cmake",
    "flex": "cmd:flex",
    "gettext": "cmd:msgfmt",
    "libtool": "cmd:libtoolize",
    "m4": "cmd:m4",
    "make": "cmd:make",
    "meson": "cmd:meson",
    "nasm": "cmd:nasm",
    "ninja": "cmd:ninja",
    "patchelf": "cmd:patchelf",
    "perl": "cmd:perl",
    "pkg-config": "cmd:pkg_config",
    "python": "cmd:python3",
    "python3": "cmd:python3",
    "swig": "cmd:swig",
}

#: Libraries the base ``haiku`` package already provides, so they must never be
#: turned into a ``lib:`` REQUIRES entry of their own.
BASE_SYSTEM_LIBS = frozenset(
    {
        "libbe",
        "libbnetapi",
        "libbsd",
        "libdevice",
        "libgame",
        "libgcc_s",
        "libgnu",
        "libmedia",
        "libnetwork",
        "libpackage",
        "libroot",
        "libstdc++",
        "libsupc++",
        "libtextencoding",
        "libtracker",
        "libtranslation",
    }
)

#: Source types that cannot become a HaikuPorts recipe, and why.
REFUSED_SOURCE_TYPES: dict[str, str] = {
    "python_wheel": (
        "HaikuPorts models Python packages as one dev-python/<name> port with a "
        "PYTHON_VERSIONS bash array fanning out to sibling packages, built with "
        "setup.py into lib/pythonX.Y/vendor-packages — never from a wheel.  "
        "cvcpkg's per-interpreter -cpXXX columns are the opposite shape, and "
        "cp313t has no Haiku counterpart"
    ),
    "python_sdist": (
        "HaikuPorts models Python packages as one dev-python/<name> port with a "
        "PYTHON_VERSIONS bash array; cvcpkg's per-interpreter -cpXXX columns do "
        "not translate (see python_wheel)"
    ),
    "prebuilt": (
        "the source is a prebuilt binary; HaikuPorts builds every port from "
        "source under haikuporter and SOURCE_URI must point at real sources"
    ),
    "vendored": (
        "the source is vendored in this repository, so there is no upstream "
        "SOURCE_URI — and SOURCE_URI is a required HaikuPorts field"
    ),
    "vcpkg": "the source is a vcpkg port reference, not an upstream archive",
    "brew": "the source is a Homebrew formula reference, not an upstream archive",
    "apt": "the source is an apt package reference, not an upstream archive",
}

#: The mandated field order (HaikuPorter-Guidelines "ordering").  Used by
#: :func:`lint_draft` — reviewers check this, and it costs nothing to be right.
FIELD_ORDER: tuple[str, ...] = (
    "SUMMARY",
    "DESCRIPTION",
    "HOMEPAGE",
    "COPYRIGHT",
    "LICENSE",
    "REVISION",
    "SOURCE_URI",
    "CHECKSUM_SHA256",
    "SOURCE_FILENAME",
    "SOURCE_DIR",
    "PATCHES",
    "ADDITIONAL_FILES",
    "ARCHITECTURES",
    "SECONDARY_ARCHITECTURES",
    "PROVIDES",
    "REQUIRES",
    "CONFLICTS",
    "REPLACES",
    "SUPPLEMENTS",
    "FRESHENS",
    "SUMMARY_devel",
    "PROVIDES_devel",
    "REQUIRES_devel",
    "BUILD_REQUIRES",
    "BUILD_PREREQUIRES",
    "TEST_REQUIRES",
)

_PYTHON_COLUMN_RE = re.compile(r"-cp3\d{1,2}t?$")
_LIB_FILE_RE = re.compile(r"^(lib[^/]+?)\.so(?:\.(\d[\w.]*))?$")


# ── Install-tree facts (the build evidence) ─────────────────────


@dataclass
class LibFact:
    """One shared library observed in a real Haiku install tree."""

    name: str  # "libz"
    version: str = ""  # "1.3.1", from the versioned filename
    compat: str = ""  # "1", from the SONAME's major


@dataclass
class InstallFacts:
    """What a real Haiku build actually installed.

    This is the evidence ``haikuporter``'s ``Policy.py`` checks a recipe
    against, so it is also the only honest source for ``PROVIDES``/``REQUIRES``.
    :func:`draft_recipe` accepts ``None`` and degrades those blocks to TODOs
    rather than inventing them.
    """

    origin: str = ""
    commands: list[str] = field(default_factory=list)
    libraries: list[LibFact] = field(default_factory=list)
    needed: list[str] = field(default_factory=list)

    @property
    def own_lib_names(self) -> set[str]:
        return {lib.name for lib in self.libraries}


def _elf_dynamic(path: Path) -> tuple[str, list[str]]:
    """Return ``(SONAME, [DT_NEEDED…])`` for an ELF file.

    ``("", [])`` for anything that is not a parseable ELF object.  Haiku is ELF
    and its ``runtime_loader`` uses the same dynamic section as Linux, so the
    install tree that ``haikuhost`` copies back reads with this directly.
    """
    try:
        blob = path.read_bytes()
    except OSError:
        return "", []
    if len(blob) < 64 or blob[:4] != b"\x7fELF":
        return "", []

    is64 = blob[4] == 2
    endian = "<" if blob[5] == 1 else ">"
    try:
        if is64:
            e_phoff, e_phentsize, e_phnum = (
                struct.unpack_from(endian + "Q", blob, 32)[0],
                struct.unpack_from(endian + "H", blob, 54)[0],
                struct.unpack_from(endian + "H", blob, 56)[0],
            )
        else:
            e_phoff, e_phentsize, e_phnum = (
                struct.unpack_from(endian + "I", blob, 28)[0],
                struct.unpack_from(endian + "H", blob, 42)[0],
                struct.unpack_from(endian + "H", blob, 44)[0],
            )

        loads: list[tuple[int, int, int]] = []  # (vaddr, offset, filesz)
        dyn: tuple[int, int] | None = None  # (offset, filesz)
        for i in range(e_phnum):
            off = e_phoff + i * e_phentsize
            if is64:
                p_type = struct.unpack_from(endian + "I", blob, off)[0]
                p_offset, p_vaddr = struct.unpack_from(endian + "QQ", blob, off + 8)
                p_filesz = struct.unpack_from(endian + "Q", blob, off + 32)[0]
            else:
                p_type = struct.unpack_from(endian + "I", blob, off)[0]
                p_offset, p_vaddr = struct.unpack_from(endian + "II", blob, off + 4)
                p_filesz = struct.unpack_from(endian + "I", blob, off + 16)[0]
            if p_type == 1:  # PT_LOAD
                loads.append((p_vaddr, p_offset, p_filesz))
            elif p_type == 2:  # PT_DYNAMIC
                dyn = (p_offset, p_filesz)
        if dyn is None:
            return "", []

        def vaddr_to_off(vaddr: int) -> int | None:
            for base, off, size in loads:
                if base <= vaddr < base + size:
                    return off + (vaddr - base)
            return None

        step = 16 if is64 else 8
        fmt = endian + ("qQ" if is64 else "iI")
        entries: list[tuple[int, int]] = []
        strtab_vaddr = 0
        pos = dyn[0]
        end = dyn[0] + dyn[1]
        while pos + step <= min(end, len(blob)):
            tag, val = struct.unpack_from(fmt, blob, pos)
            pos += step
            if tag == 0:  # DT_NULL
                break
            if tag == 5:  # DT_STRTAB
                strtab_vaddr = val
            elif tag in (1, 14):  # DT_NEEDED, DT_SONAME
                entries.append((tag, val))
        if not strtab_vaddr:
            return "", []
        strtab = vaddr_to_off(strtab_vaddr)
        if strtab is None:
            return "", []

        def cstr(offset: int) -> str:
            start = strtab + offset
            stop = blob.find(b"\0", start)
            return blob[start:stop].decode("utf-8", "replace") if stop != -1 else ""

        soname = ""
        needed: list[str] = []
        for tag, val in entries:
            if tag == 14:
                soname = cstr(val)
            else:
                needed.append(cstr(val))
        return soname, needed
    except (struct.error, IndexError, ValueError):
        return "", []


def scan_install_tree(root: Path) -> InstallFacts:
    """Harvest :class:`InstallFacts` from a Haiku install tree.

    *root* is the ``CVC_INSTALL_DIR`` a Haiku build produced — the directory
    :mod:`cvcpkg.haikuhost` copies back off the Haiku box.  Pure filesystem
    inspection; nothing is executed and nothing is downloaded.
    """
    root = Path(root)
    facts = InstallFacts(origin=str(root))

    bindir = root / "bin"
    if bindir.is_dir():
        facts.commands = sorted(p.name for p in bindir.iterdir() if p.is_file())

    libs: dict[str, LibFact] = {}
    needed: set[str] = set()
    for libdir in (root / "lib", root / "lib64", root / "develop" / "lib"):
        if not libdir.is_dir():
            continue
        for entry in sorted(libdir.iterdir()):
            m = _LIB_FILE_RE.match(entry.name)
            if not m:
                continue
            name, version = m.group(1), m.group(2) or ""
            fact = libs.setdefault(name, LibFact(name=name))
            if len(version) > len(fact.version):
                fact.version = version
            if entry.is_symlink() or not entry.is_file():
                continue
            soname, dt_needed = _elf_dynamic(entry)
            needed.update(dt_needed)
            if soname:
                sm = _LIB_FILE_RE.match(soname)
                if sm and sm.group(2):
                    fact.compat = sm.group(2).split(".")[0]

    for cmd in facts.commands:
        _, dt_needed = _elf_dynamic(bindir / cmd)
        needed.update(dt_needed)

    facts.libraries = [libs[k] for k in sorted(libs)]
    own = facts.own_lib_names
    facts.needed = sorted(
        n
        for n in needed
        if (base := _LIB_FILE_RE.match(n)) and base.group(1) not in own | BASE_SYSTEM_LIBS
    )
    return facts


# ── Name / licence / text helpers ───────────────────────────────


def resolvable(name: str) -> str:
    """Normalise a name for a ``.PackageInfo`` entity.

    The grammar forbids ``-`` in entity names, so ``c-ares`` becomes
    ``c_ares`` and ``cmd:i586-pc-haiku-gcc`` becomes ``cmd:i586_pc_haiku_gcc``.
    """
    return name.replace("-", "_")


def port_for(cvc_name: str) -> tuple[str, str, bool]:
    """Map a cvcpkg recipe name to ``(category, port, verified)``.

    ``verified`` is ``True`` only when the port was read off the live
    HaikuPorts tree.  A proposed category (a port HaikuPorts does not carry)
    or a fallback returns ``False``, which makes the caller emit a TODO.
    """
    if cvc_name in KNOWN_PORTS:
        cat, port = KNOWN_PORTS[cvc_name].split("/", 1)
        return cat, port, True
    if cvc_name in PROPOSED_PORTS:
        cat, port = PROPOSED_PORTS[cvc_name].split("/", 1)
        return cat, port, False
    return "TODO-category", resolvable(cvc_name), False


def haiku_licenses(spdx: str | None) -> tuple[list[str], list[str]]:
    """Translate an SPDX expression into ``(licence names, TODO notes)``.

    Haiku's licence list is human-style and curated, not SPDX, so anything
    unmapped is passed through verbatim as the name of a port-local
    ``licenses/<Name>`` file the human still has to add.
    """
    notes: list[str] = []
    if not spdx:
        return [], [f"{TODO} LICENSE is empty — read the upstream licence and fill it in."]

    cleaned = spdx.replace("(", " ").replace(")", " ")
    dual = " OR " in cleaned
    ids = [t for t in re.split(r"\s+(?:AND|OR|WITH)\s+", cleaned) if t.strip()]

    names: list[str] = []
    for ident in ids:
        ident = ident.strip()
        mapped = SPDX_TO_HAIKU.get(ident)
        if mapped:
            if mapped not in names:
                names.append(mapped)
        else:
            if ident not in names:
                names.append(ident)
            notes.append(
                f"{TODO} '{ident}' has no well-known Haiku licence name.  Either map it "
                f"to one of {sorted(HAIKU_LICENSES)[:3]}… or drop the verbatim licence "
                f"text into licenses/{ident} in the port directory and keep this name."
            )
    if dual:
        notes.append(
            f"{TODO} the SPDX expression '{spdx}' is a choice (OR).  HaikuPorts LICENSE "
            "lists the licences that apply — pick the one you are shipping under."
        )
    if any(i.endswith("-or-later") for i in ids):
        notes.append(
            f"{TODO} '-or-later' collapses to the base Haiku licence name above; confirm "
            "that is what upstream's COPYING actually says."
        )
    return names, notes


def summary_from(description: str | None, port: str) -> tuple[str, list[str]]:
    """Derive a HaikuPorts SUMMARY from cvcpkg's one-line ``description``.

    haikuporter hard-fails a SUMMARY that is missing, multi-line, starts with
    the port name, does not start with a capital, ends in a full stop, has
    fewer than three words, or exceeds 80 characters (the wiki asks for 70).
    We satisfy what we can and report the rest.
    """
    notes: list[str] = []
    if not description:
        return "", [f"{TODO} SUMMARY is empty — cvcpkg's recipe has no description."]

    text = description.strip().split("\n")[0]
    # cvcpkg descriptions conventionally lead with "<name> — " / "<name>: ".
    text = re.sub(r"^\s*\S+\s*(?:—|--|-|:)\s+", "", text, count=1)
    # First sentence only; SUMMARY is one line.
    text = re.split(r"(?<=[a-z0-9)])\.\s+", text)[0]
    text = text.rstrip().rstrip(".").strip()
    if text and text[0].islower():
        text = text[0].upper() + text[1:]

    if text.lower().startswith(port.lower()):
        notes.append(
            f"{TODO} SUMMARY must not start with the port name ('{port}') — "
            "haikuporter rejects it.  Reword."
        )
    if len(text.split()) < 3:
        notes.append(f"{TODO} SUMMARY needs at least three words; haikuporter rejects it.")
    if len(text) > 70:
        notes.append(
            f"{TODO} SUMMARY is {len(text)} chars; haikuporter's hard limit is 80 and the "
            "guidelines ask for 70 or fewer.  Shorten it."
        )
    return text, notes


def wrap_description(description: str | None) -> tuple[str, list[str]]:
    """Wrap cvcpkg's description as a HaikuPorts DESCRIPTION body.

    Lines are at most 100 characters and continue with a backslash; the text is
    never indented, because indentation breaks HaikuDepot's layout.  cvcpkg has
    no field that *is* a DESCRIPTION, so this always carries a TODO: HaikuDepot
    full-text search only sees the summary and description, and the guidelines
    ask for two or three sentences seeded with likely search terms.
    """
    note = (
        f"{TODO} DESCRIPTION is cvcpkg's one-line description, which is really a "
        "SUMMARY.  HaikuPorts wants 2-3 sentences written for HaikuDepot's full-text "
        "search — expand this by hand."
    )
    if not description:
        return "", [f"{TODO} DESCRIPTION is empty — write 2-3 sentences by hand."]

    words = description.strip().split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > 98 and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return " \\\n".join(lines), [note]


def _interpolate_version(url: str, version: str) -> str:
    """Replace the literal upstream version in *url* with ``$portVersion``."""
    if not version or version not in url:
        return url
    out = ""
    rest = url
    while version in rest:
        head, rest = rest.split(version, 1)
        token = "${portVersion}" if rest[:1].isalnum() or rest[:1] == "_" else "$portVersion"
        out += head + token
    return out + rest


def _archive_top_dir(url: str, version: str) -> str:
    """Guess the archive's top-level directory from its basename.

    ``zlib-1.3.1.tar.gz`` -> ``zlib-1.3.1``; ``CGAL-6.0.1.tar.xz`` -> ``CGAL-6.0.1``.
    A guess, and labelled as one by the caller — some archives unpack into a
    directory unrelated to their filename.
    """
    base = url.rstrip("/").rsplit("/", 1)[-1]
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tgz", ".tbz2", ".zip", ".tar"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


# ── The draft ───────────────────────────────────────────────────


@dataclass
class HaikuDraft:
    """A generated ``.recipe`` plus everything the human still owes."""

    port: str
    category: str
    version: str
    text: str
    todos: list[str] = field(default_factory=list)
    grounded: bool = False  # were PROVIDES/REQUIRES derived from a real build?

    @property
    def filename(self) -> str:
        return f"{self.port}-{self.version}.recipe"

    @property
    def relpath(self) -> str:
        """Path inside a haikuports checkout, e.g. ``sys-libs/zlib/zlib-1.3.1.recipe``."""
        return f"{self.category}/{self.port}/{self.filename}"


def _multiline(name: str, entries: list[str]) -> str:
    """Render a HaikuPorts multi-line variable with tab-indented entries."""
    if not entries:
        return f'{name}=""'
    body = "".join(f"{TAB}{e}\n" for e in entries)
    return f'{name}="\n{body}{TAB}"'


def _sorted_resolvables(entries: list[str], *, first: str = "") -> list[str]:
    """Alphabetical, keeping ``haiku``/``haiku_devel`` — and the port's own
    self-provide — first, which is what the guidelines ask for and what the
    tree actually does (see ``sys-libs/zlib``).
    """
    pinned = [
        e for e in entries if e.split()[0] in ("haiku", "haiku_devel") or e.split()[0] == first
    ]
    rest = sorted({e for e in entries if e not in pinned})
    return sorted(set(pinned)) + rest


def _refuse_if_unconvertible(recipe: Mapping[str, Any]) -> None:
    meta = recipe.get("recipe") or {}
    source = recipe.get("source") or {}
    name = str(meta.get("name") or "")

    if _PYTHON_COLUMN_RE.search(name):
        raise ConversionRefusedError(
            f"{name}: cvcpkg's per-interpreter Python columns have no HaikuPorts "
            "counterpart.  HaikuPorts builds one dev-python/<name> port with a "
            "PYTHON_VERSIONS array over its own python3.10-3.14 slots, and cp313t "
            "has no counterpart at all.  Transcribing the column matrix would "
            "produce one bogus port per interpreter.  Refusing."
        )
    stype = str(source.get("type") or "")
    if stype in REFUSED_SOURCE_TYPES:
        raise ConversionRefusedError(
            f"{name}: cannot convert source type '{stype}' — "
            f"{REFUSED_SOURCE_TYPES[stype]}.  Refusing."
        )
    if (meta.get("kind") or "") == "iso" or name == "haiku-image":
        raise ConversionRefusedError(
            f"{name}: this recipe builds a Haiku *image* on a Linux host — it is not "
            "a Haiku package and has no HaikuPorts counterpart.  Refusing."
        )
    if not source.get("url"):
        raise ConversionRefusedError(
            f"{name}: no source.url, so there is nothing to put in SOURCE_URI "
            "(a required HaikuPorts field).  Refusing."
        )


def _banner(cvc_name: str, grounded: bool) -> list[str]:
    rule = "# " + "=" * 76
    lines = [
        rule,
        "# DRAFT — machine-assisted, NOT A FINISHED PORT.",
        "#",
        f"# Generated by `cvcpkg haiku draft-recipe {cvc_name}` from that recipe's",
        "# recipes/<name>/recipe.yaml.  This is a deterministic transcription of the",
        "# fields cvcpkg already knows; it is a starting point, not a port.",
        "#",
        "# Before this builds under your own haikuporter:",
        "#   1. Fill in every `TODO(human):` below — COPYRIGHT and the BUILD()/",
        "#      INSTALL() bodies above all.",
        "#   2. Build it with `haikuporter -S <port>` on a real Haiku machine and fix",
        "#      what the policy checker says.",
        "#   3. Run `haikuporter --lint <port>` (integrations/haikuports/lint-draft.sh).",
        "#",
        "# Whether this ever goes upstream is entirely your call.  If you decide to",
        "# send it: DELETE THIS BANNER, open the PR yourself under your own name",
        "# through the web UI so the PR template is not bypassed, leave the checklist",
        "# intact, and say in the body that the metadata was transcribed by a tool and",
        "# that you built and tested it by hand.",
        "#",
        "# cvcpkg never opens a HaikuPorts pull request.  There is no code path here",
        "# that could.",
    ]
    if not grounded:
        lines += [
            "#",
            "# NO BUILD EVIDENCE: this draft was generated without --install-tree, so",
            "# PROVIDES/REQUIRES could not be derived from real binaries and are TODOs.",
            "# haikuporter's Policy.py checks those against the installed bin/ and lib/",
            "# contents, so they cannot be guessed from recipe metadata.",
        ]
    lines.append(rule)
    return lines


def draft_recipe(
    recipe: Mapping[str, Any],
    *,
    facts: InstallFacts | None = None,
    build_script: str | None = None,
    revision: int = 1,
) -> HaikuDraft:
    """Convert a parsed cvcpkg ``recipe.yaml`` into a :class:`HaikuDraft`.

    *facts* is the install tree of a **real Haiku build** (see
    :func:`scan_install_tree`); without it the resolvable blocks degrade to
    TODOs.  *build_script* is the text of the cvcpkg ``build.sh``, quoted into
    the ``BUILD()`` stub as reference — never translated.

    Raises :class:`ConversionRefusedError` for recipe shapes that cannot honestly
    become a port (Python interpreter columns, prebuilt/vendored sources, the
    Haiku image builder).
    """
    _refuse_if_unconvertible(recipe)

    meta = recipe.get("recipe") or {}
    source = recipe.get("source") or {}
    depends = recipe.get("depends") or {}
    cvc_name = str(meta["name"])
    version = str(meta.get("upstream_version") or "")
    category, port, verified = port_for(cvc_name)
    entity = resolvable(port)

    todos: list[str] = []
    out: list[str] = _banner(cvc_name, facts is not None)

    if not verified:
        todos.append(
            f"{TODO} category '{category}' is a proposal, not an observation.  Confirm "
            "it against Gentoo's overlay (https://gpo.zugaina.org) — the PR checklist "
            "asks reviewers to check this."
        )
    else:
        todos.append(
            f"{TODO} HaikuPorts ALREADY CARRIES {category}/{port}.  Read its existing "
            "recipe first: it is the reviewed, Haiku-correct version of this port and "
            "very likely a better starting point than this draft.  Then decide whether "
            "your change is a version bump (edit + git mv the recipe, reset REVISION) "
            "or a new slot alongside the old one (boost1.91, hdf5_103, yaml_cpp0.8) — "
            "downstream consumers you cannot see may depend on the current ABI."
        )
    if port != cvc_name:
        todos.append(
            f"{TODO} port renamed '{cvc_name}' -> '{port}' (HaikuPorts names are "
            "lower-case with underscores, and slotted majors are common: boost1.91, "
            "hdf5_103, yaml_cpp0.8).  Confirm the name and whether this should be a "
            "new slot rather than a bump."
        )

    # ── SUMMARY / DESCRIPTION / HOMEPAGE / COPYRIGHT / LICENSE ──
    summary, notes = summary_from(meta.get("description"), port)
    todos += notes
    out.append(f'SUMMARY="{summary}"')

    body, notes = wrap_description(meta.get("description"))
    todos += notes
    if body and body == summary:
        todos.append(
            f"{TODO} DESCRIPTION came out byte-identical to SUMMARY, which haikuporter "
            "rejects outright.  There is exactly one usable sentence in cvcpkg's recipe; "
            "the rest has to be written."
        )
    out.append(f'DESCRIPTION="{body}"')

    homepage = str(meta.get("homepage") or "")
    if not homepage:
        todos.append(f"{TODO} HOMEPAGE is required and cvcpkg's recipe has none.")
    else:
        todos.append(
            f"{TODO} verify HOMEPAGE's trailing slash with `curl --head {homepage}` — "
            "the guidelines require it to match exactly what the server serves."
        )
    out.append(f'HOMEPAGE="{homepage}"')

    out.append('COPYRIGHT=""')
    todos.append(
        f"{TODO} COPYRIGHT is REQUIRED and cvcpkg has no field for it.  Transcribe "
        "'<year(s)> <holder>' from the upstream COPYING/LICENSE/source headers, one "
        "per line.  haikuporter rejects '@', the word 'copyright', '(c)' and '©'.  "
        "Do not invent this."
    )

    names, notes = haiku_licenses(meta.get("license"))
    todos += notes
    out.append(
        _multiline("LICENSE", names) if len(names) > 1 else f'LICENSE="{names[0] if names else ""}"'
    )

    out.append(f'REVISION="{revision}"')
    todos.append(
        f"{TODO} REVISION is 1 because this is a new port.  It is NOT cvcpkg's "
        f"cvc_revision ({meta.get('cvc_revision')}): HaikuPorts bumps REVISION only "
        "when the built package's contents change, never for a cosmetic recipe edit, "
        "and it resets on a version bump."
    )

    # ── Sources ────────────────────────────────────────────────
    uris = [_interpolate_version(str(source["url"]), version)]
    if source.get("mirror"):
        uris.append(_interpolate_version(str(source["mirror"]), version))
    out.append(_multiline("SOURCE_URI", uris) if len(uris) > 1 else f'SOURCE_URI="{uris[0]}"')
    if len(uris) > 1:
        todos.append(
            f"{TODO} every SOURCE_URI must serve the *same* file — check the mirror's "
            "checksum matches before keeping it."
        )

    sha = str(source.get("sha256") or "")
    out.append(f'CHECKSUM_SHA256="{sha}"')
    if not sha:
        todos.append(f"{TODO} CHECKSUM_SHA256 is empty — `sha256sum` the archive.")

    top = _archive_top_dir(str(source["url"]), version)
    default_top = f"{port}-{version}"
    if top and top != default_top:
        out.append(f'SOURCE_DIR="{_interpolate_version(top, version)}"')
        todos.append(
            f"{TODO} SOURCE_DIR was guessed from the archive filename ('{top}').  "
            "haikuporter defaults to $portVersionedName; verify against the real "
            "archive and delete the line if it matches."
        )

    patches = list(recipe.get("patches") or [])
    if patches:
        todos.append(
            f"{TODO} this recipe carries {len(patches)} cvcpkg patch(es) "
            f"({', '.join(patches)}).  They are `patch -p1` diffs motivated by "
            "NetBSD/wasm/toolchain portability, not by Haiku, and HaikuPorts wants a "
            f"`git format-patch` series at patches/{port}-$portVersion.patchset applied "
            "with `git am`.  Decide per patch whether it is relevant on Haiku, convert "
            "the ones that are, then add the PATCHES= line."
        )

    out.append("")
    out.append('ARCHITECTURES="x86_64"')
    todos.append(
        f"{TODO} ARCHITECTURES is x86_64 because that is the only Haiku target cvcpkg "
        'builds and tests.  Widen it to "all !x86_gcc2" only if you have actually '
        'built it elsewhere.  SECONDARY_ARCHITECTURES="x86" (and the matching '
        "$secondaryArchSuffix on every resolvable) is deliberately omitted rather "
        "than guessed — add it if this port should be hybrid-capable."
    )

    # ── Resolvables ────────────────────────────────────────────
    out.append("")
    provides = [f"{entity} = $portVersion"]
    requires = ["haiku"]
    devel_provides = [f"{entity}_devel = $portVersion"]
    devel_requires = [f"{entity} == $portVersion base"]

    if facts is not None:
        if facts.libraries:
            out.append('libVersion="$portVersion"')
            out.append('libVersionCompat="$libVersion compat >= ${libVersion%%.*}"')
            out.append("")
        for lib in facts.libraries:
            key = resolvable(lib.name)
            if lib.version and lib.compat:
                spec = f"{lib.version} compat >= {lib.compat}"
            elif lib.version:
                spec = f"{lib.version} compat >= {lib.version.split('.')[0]}"
            else:
                spec = "$libVersionCompat"
            provides.append(f"lib:{key} = {spec}")
            devel_provides.append(f"devel:{key} = {spec}")
        for cmd in facts.commands:
            provides.append(f"cmd:{resolvable(cmd)}")
        for need in facts.needed:
            m = _LIB_FILE_RE.match(need)
            if m:
                requires.append(f"lib:{resolvable(m.group(1))}")
        todos.append(
            f"{TODO} PROVIDES/REQUIRES were derived from the install tree at "
            f"{facts.origin}.  Re-check them with `haikuporter -S {port}` on Haiku — "
            "Policy.py is the authority, not this tool."
        )
    else:
        todos.append(
            f"{TODO} PROVIDES needs one `lib:<soname>` per installed shared library "
            "(with `= <soversion> compat >= <major>`, tracking the SONAME, not the "
            "port version) and one `cmd:<name>` per bin/ entry; REQUIRES needs one "
            "`lib:` per ELF DT_NEEDED that is not in the base system.  Build on Haiku "
            "and re-run with --install-tree, or write them by hand."
        )

    out.append(_multiline("PROVIDES", _sorted_resolvables(provides, first=entity)))
    out.append(_multiline("REQUIRES", _sorted_resolvables(requires)))

    if facts is not None and facts.libraries:
        out.append("")
        out.append(f'SUMMARY_devel="{summary} (development files)"')
        out.append(
            _multiline(
                "PROVIDES_devel", _sorted_resolvables(devel_provides, first=f"{entity}_devel")
            )
        )
        out.append(_multiline("REQUIRES_devel", _sorted_resolvables(devel_requires)))
    else:
        todos.append(
            f"{TODO} a library port needs a _devel sibling (SUMMARY_devel, "
            "PROVIDES_devel with devel:<soname>, REQUIRES_devel with "
            f"'{entity} == $portVersion base') and `packageEntries devel $developDir` "
            "in INSTALL().  Not emitted because there is no library evidence."
        )

    # ── Build dependencies ─────────────────────────────────────
    build_requires = ["haiku_devel"]
    build_prereqs = ["cmd:gcc", "cmd:ld"]
    unmapped: list[str] = []

    def _dep_names(section: str) -> list[str]:
        out_names: list[str] = []
        for dep in depends.get(section) or []:
            out_names.append(dep if isinstance(dep, str) else str(dep.get("name") or ""))
        return [d for d in out_names if d]

    for dep in _dep_names("build") + _dep_names("host_tools") + _dep_names("runtime"):
        if dep in TOOL_COMMANDS:
            build_prereqs.append(TOOL_COMMANDS[dep])
            continue
        if dep in KNOWN_PORTS:
            build_requires.append(f"{resolvable(KNOWN_PORTS[dep].split('/', 1)[1])}_devel")
        else:
            unmapped.append(dep)

    out.append("")
    out.append(_multiline("BUILD_REQUIRES", _sorted_resolvables(build_requires)))
    out.append(_multiline("BUILD_PREREQUIRES", _sorted_resolvables(build_prereqs)))
    todos.append(
        f"{TODO} BUILD_PREREQUIRES must name every command the phases actually run "
        "(cmd:make, cmd:sed, cmd:pkg_config …).  Only the ones visible in cvcpkg's "
        "depends: are listed; add the rest once BUILD() is written."
    )
    if unmapped:
        todos.append(
            f"{TODO} these cvcpkg dependencies have no known HaikuPorts port: "
            f"{', '.join(sorted(set(unmapped)))}.  Find them in the tree (they may be "
            "named differently), replace them with a Haiku equivalent, or port them "
            "first — a port whose dependencies are not in the tree cannot be merged."
        )
    if any(d in _dep_names("build") for d in ("cmake", "ninja")):
        todos.append(
            f"{TODO} haikuporter's cmake() wrapper hard-errors without an explicit "
            "CMAKE_BUILD_TYPE, and cross-checks Release/RelWithDebInfo against whether "
            "defineDebugInfoPackage was declared."
        )

    # ── Phases ─────────────────────────────────────────────────
    out.append("")
    out.append(f"{TODO} declare a debuginfo package once the library names are known:")
    out.append(f'# defineDebugInfoPackage {entity} "$libDir"/libFOO.so.$libVersion')

    out.append("")
    out += _phase_stub("BUILD", cvc_name, build_script)
    out.append("")
    out += _phase_stub("INSTALL", cvc_name, None)
    if (recipe.get("test") or {}).get("script"):
        out.append("")
        out += _phase_stub("TEST", cvc_name, None)
    todos.append(
        f"{TODO} BUILD()/INSTALL() are stubs that exit 1 on purpose.  They are not "
        "translated because cvcpkg's model (FHS prefix, static+shared, "
        "$ORIGIN rpath, cvc_rewrite_install_paths) is the opposite of a Haiku port's "
        "(packagefs' fixed develop/headers + develop/lib layout, shared only, static "
        "libs removed, prepareInstalledDevelLib/fixPkgconfig/fixCMake/packageEntries).  "
        "Drop the $ORIGIN rpath and the relocation pass entirely — they read as a "
        "foreign smell to reviewers and packagefs does not need them."
    )

    text = "\n".join(out).rstrip() + "\n"
    return HaikuDraft(
        port=port,
        category=category,
        version=version,
        text=text,
        todos=todos,
        grounded=facts is not None,
    )


def _phase_stub(phase: str, cvc_name: str, build_script: str | None) -> list[str]:
    """A phase function that is obviously unfinished and refuses to run."""
    lines = [f"{phase}()", "{"]
    lines.append(f"{TAB}{TODO} write {phase}() against Haiku's layout and verify it on")
    lines.append(f"{TAB}# a Haiku machine.  See haikuporter/generic/generic_lib-1.2.3.recipe")
    lines.append(f"{TAB}# for the idiom ($cmakeDirArgs / runConfigure / $jobArgs, then")
    lines.append(f"{TAB}# prepareInstalledDevelLib + fixPkgconfig + packageEntries devel).")
    if build_script:
        lines.append(f"{TAB}#")
        lines.append(f"{TAB}# For reference only — cvcpkg's recipes/{cvc_name}/build.sh:")
        for raw in build_script.splitlines():
            if raw.startswith("#!") or not raw.strip():
                continue
            lines.append(f"{TAB}#   {raw.rstrip()}"[:99].rstrip())
    lines.append(f"{TAB}#")
    lines.append(f'{TAB}echo "TODO(human): {phase}() is not implemented" >&2')
    lines.append(f"{TAB}exit 1")
    lines.append("}")
    return lines


# ── Local lint (HaikuPorts' own rules, run before a human looks) ─


def lint_draft(text: str, *, port: str = "") -> list[str]:
    """Check a draft against the rules HaikuPorts machine-enforces.

    Mirrors ``.github/lint-new-recipes.sh`` (trailing whitespace) and the hard
    ``sysExit`` checks in haikuporter's ``Port.validateRecipeFile``, plus the
    field ordering reviewers ask about.  Returns a list of problems; empty means
    the *format* is clean — it says nothing about whether the port builds.
    """
    problems: list[str] = []
    lines = text.split("\n")

    outstanding = text.count(TODO)
    if outstanding:
        problems.append(
            f"{outstanding} unresolved '{TODO}' marker(s) — this is still a draft, " "not a recipe"
        )

    for i, line in enumerate(lines, 1):
        if line != line.rstrip():
            problems.append(f"line {i}: trailing whitespace (HaikuPorts CI fails on this)")
        if len(line) > 100 and "://" not in line and "CHECKSUM" not in line:
            problems.append(f"line {i}: {len(line)} chars, over the 100-character limit")

    def value(name: str) -> str | None:
        m = re.search(rf'^{name}="((?:[^"\\]|\\.)*)"', text, re.MULTILINE | re.DOTALL)
        return m.group(1) if m else None

    for required in ("SUMMARY", "DESCRIPTION", "HOMEPAGE", "REVISION", "SOURCE_URI"):
        if value(required) is None:
            problems.append(f"{required} is missing (required by RecipeAttributes.py)")
    if not re.search(r"^ARCHITECTURES=", text, re.MULTILINE):
        problems.append("ARCHITECTURES is missing (required by RecipeAttributes.py)")

    summary = value("SUMMARY")
    if summary is not None:
        if not summary:
            problems.append("SUMMARY is empty")
        else:
            if "\n" in summary:
                problems.append("SUMMARY must be a single line")
            if len(summary) > 80:
                problems.append(f"SUMMARY is {len(summary)} chars; haikuporter's limit is 80")
            if summary.endswith("."):
                problems.append("SUMMARY must not end with a full stop")
            if summary[0].islower():
                problems.append("SUMMARY must start with a capital letter")
            if summary.count(" ") < 2:
                problems.append("SUMMARY must contain at least three words")
            if port and summary.lower().startswith(port.lower()):
                problems.append(f"SUMMARY must not start with the port name '{port}'")

    description = value("DESCRIPTION")
    if description is not None and summary is not None and description == summary:
        problems.append("DESCRIPTION must not be identical to SUMMARY")

    if not value("LICENSE") and not re.search(r'^LICENSE="\n', text, re.MULTILINE):
        problems.append("LICENSE is empty (reviewers check this; PR checklist item 3)")

    copyright_ = value("COPYRIGHT")
    if not copyright_:
        problems.append("COPYRIGHT is empty (reviewers check this; PR checklist item 3)")
    if copyright_:
        low = copyright_.lower()
        if "@" in copyright_:
            problems.append("COPYRIGHT must not contain an e-mail address")
        if "copyright" in low or "(c)" in low or "©" in copyright_:
            problems.append("COPYRIGHT must not contain 'copyright', '(c)' or '©'")

    order = [f for f in FIELD_ORDER if re.search(rf"^{f}=", text, re.MULTILINE)]
    positions = [text.index(f"\n{f}=") if f"\n{f}=" in text else text.index(f"{f}=") for f in order]
    if positions != sorted(positions):
        problems.append("fields are not in the order the HaikuPorter guidelines prescribe")

    return problems
