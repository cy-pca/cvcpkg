# packaging/cvcpkg.spec — PyInstaller build for a self-contained `cvcpkg` binary.
#
# Produces a single executable that bundles the CPython runtime, cvcpkg + its
# dependency closure, and the STANDARD RECIPES — so the binary discovers recipes
# with no --recipes-dir (find_recipes_dir() checks sys._MEIPASS first).
#
# Two variants, selected by the CVCPKG_BUNDLE_SERVER env var at build time:
#
#   client (default) — the CLI (install / build / validate). Omits the server
#     extra (fastapi/uvicorn/...) to stay lean.
#       pip install -e ".[remote,signing,validate]" pyinstaller
#       pyinstaller --clean --noconfirm packaging/cvcpkg.spec
#       ./dist/cvcpkg --version && ./dist/cvcpkg validate recipes
#
#   combined (CVCPKG_BUNDLE_SERVER=1) — ONE multi-call binary that is both
#     `cvcpkg` and `cvcpkg-server`, dispatched by argv[0] (busybox-style; ship a
#     `cvcpkg-server` symlink, or set CVCPKG_ENTRY=server). Includes the server.
#       pip install -e ".[production,signing,validate]" pyinstaller
#       CVCPKG_BUNDLE_SERVER=1 pyinstaller --clean --noconfirm packaging/cvcpkg.spec
#       ./dist/cvcpkg --version
#       ln -sf cvcpkg dist/cvcpkg-server && ./dist/cvcpkg-server run

import os
import pathlib

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = pathlib.Path(SPECPATH).parent
BUNDLE_SERVER = bool(os.environ.get("CVCPKG_BUNDLE_SERVER"))

datas = [
    # The standard recipes, bundled + discoverable by default.
    (str(ROOT / "recipes"), "cvcpkg/recipes"),
    # JSON schemas (`cvcpkg validate`) and server landing assets.
    (str(ROOT / "src" / "cvcpkg" / "schemas"), "cvcpkg/schemas"),
    (str(ROOT / "src" / "cvcpkg" / "server" / "assets"), "cvcpkg/server/assets"),
]
# cvcpkg reads its own version via importlib.metadata; bundle the .dist-info.
datas += copy_metadata("cvcpkg")

# Lazily-imported deps PyInstaller's static analysis doesn't follow.
#
# httpx, cryptography and jsonschema are here because cvcpkg reaches them
# through cvcpkg.optional's guards — `require_httpx()` /
# `require_cryptography()` / `require_jsonschema()`, a CALL, not an `import
# httpx` statement modulegraph can see.  A standalone binary is a fixed
# closure, so leaving them out silently ships a `cvcpkg` whose publish /
# builder / signing / validate commands all report a missing extra.  All three
# must also be installed in the build env (`pip install
# '.[remote,signing,validate]'`).
hiddenimports = [
    "jsonschema",
    "httpx",
    "cryptography",
    "cryptography.exceptions",
    "cryptography.hazmat.primitives.serialization",
    "cryptography.hazmat.primitives.asymmetric.ed25519",
]

if BUNDLE_SERVER:
    # Combined multi-call binary: include the server and bundle its DB migrations
    # + alembic.ini (both resolved from sys._MEIPASS at runtime; see
    # cvcpkg.server.cli._alembic_config).
    entry = str(ROOT / "packaging" / "cvcpkg_launcher.py")
    excludes = []
    datas += [
        (str(ROOT / "src" / "cvcpkg" / "migrations"), "cvcpkg/migrations"),
        (str(ROOT / "alembic.ini"), "."),
    ]
    # Server deps with dynamic (driver/dialect/protocol) imports PyInstaller misses.
    hiddenimports += [
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "fastapi",
        "starlette",
        "asyncpg",
        "alembic",
        "pydantic",
        "sqlalchemy.dialects.postgresql",
        "sqlalchemy.dialects.postgresql.asyncpg",
        "sqlalchemy.dialects.sqlite",
        "aiosqlite",
    ]
    hiddenimports += collect_submodules("cvcpkg.server")
else:
    # Lean client CLI: entry is cvcpkg/__main__.py; the server extra is excluded.
    entry = str(ROOT / "src" / "cvcpkg" / "__main__.py")
    excludes = ["fastapi", "uvicorn", "starlette", "asyncpg", "alembic"]

a = Analysis(
    [entry],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="cvcpkg",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
