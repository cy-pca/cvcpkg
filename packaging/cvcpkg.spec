# packaging/cvcpkg.spec — PyInstaller build for a self-contained `cvcpkg` CLI.
#
# Produces a single executable that bundles the CPython runtime, cvcpkg + its
# CLI dependency closure, and the STANDARD RECIPES — so the binary discovers
# recipes with no --recipes-dir (find_recipes_dir() checks sys._MEIPASS first).
#
# Build (from the repo root, in a venv):
#   pip install -e . jsonschema pyinstaller   # jsonschema is a validate-time dep
#   pyinstaller --clean --noconfirm packaging/cvcpkg.spec
#   ./dist/cvcpkg --version
#   ./dist/cvcpkg validate recipes            # recipes discovered from the bundle
#
# This is the CLIENT CLI (install / build / validate). It deliberately omits the
# server extra (fastapi/uvicorn/...); run the server from the container image.

import pathlib

from PyInstaller.utils.hooks import copy_metadata

ROOT = pathlib.Path(SPECPATH).parent

datas = [
    # The standard recipes, bundled + discoverable by default.
    (str(ROOT / "recipes"), "cvcpkg/recipes"),
    # JSON schemas (`cvcpkg validate`) and server landing assets.
    (str(ROOT / "src" / "cvcpkg" / "schemas"), "cvcpkg/schemas"),
    (str(ROOT / "src" / "cvcpkg" / "server" / "assets"), "cvcpkg/server/assets"),
]
# cvcpkg reads its own version via importlib.metadata; bundle the .dist-info.
datas += copy_metadata("cvcpkg")

a = Analysis(
    [str(ROOT / "src" / "cvcpkg" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    # Lazily-imported deps PyInstaller's static analysis doesn't follow.
    hiddenimports=["jsonschema"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Server-only deps: the CLI lazy-imports the server, so a client binary does
    # not need them. Excluding keeps the binary lean and the build self-contained.
    excludes=["fastapi", "uvicorn", "starlette", "asyncpg", "alembic"],
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
