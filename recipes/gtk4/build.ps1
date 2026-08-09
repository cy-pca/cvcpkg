# recipes/gtk4/build.ps1 — build GTK 4 on Windows via Meson + MSVC.
#
# This previously shelled out to gvsbuild, a third-party meta-builder installed
# with pipx that fetches and compiles the ENTIRE GTK stack from its own sources.
# That made the Windows bundle non-hermetic (network access and an unpinned
# toolchain at build time) and, worse, meant the glib/cairo/pango/gdk-pixbuf
# packages this recipe declares as dependencies were not the ones actually
# linked: gvsbuild built and shipped its own copies, so the declared dependency
# graph was decorative on Windows.
#
# GTK 4 builds with Meson under MSVC exactly as it does on POSIX, so this now
# mirrors build.sh — every dependency resolves from the cvcpkg prefix through
# PKG_CONFIG_PATH, which Invoke-CvcMesonBuild wires up.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# GTK's meson build imports the Python 'packaging' module, same as glib's.
& python -m pip install --quiet packaging 2>$null

Invoke-CvcMesonBuild @(
    # Win32 (GDI/Direct2D) is GTK's native Windows backend. Wayland and X11 are
    # POSIX display servers, pinned off so meson cannot auto-enable one from a
    # stray dependency in the prefix.
    '-Dwin32-backend=true',
    '-Dwayland-backend=false',
    '-Dx11-backend=false',
    # Match build.sh: no demos/tests/docs in a packaged bundle.
    '-Dbuild-tests=false',
    '-Dbuild-examples=false',
    '-Dbuild-demos=false',
    '-Dbuild-testsuite=false',
    '-Dintrospection=disabled',
    '-Ddocumentation=false',
    '-Dman-pages=false',
    # GTK 4.16 split the old -Dprint-backends string option into per-backend
    # features. The built-in "file" backend is always compiled; CUPS and CPDB
    # are Unix printing stacks with no Windows presence.
    '-Dprint-cups=disabled',
    '-Dprint-cpdb=disabled',
    # GStreamer is not a declared dependency of this recipe on any platform, so
    # do not let meson pick up a system copy.
    '-Dmedia-gstreamer=disabled'
)
