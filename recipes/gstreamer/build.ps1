# recipes/gstreamer/build.ps1 — build GStreamer on Windows via Meson + MSVC.
#
# Builds GStreamer core + base + good + bad; disables ugly/libav,
# bindings, docs, tests, and Linux-specific plugins.  The win32
# and directsound plugins are included automatically when available.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcMesonBuild @(
    # The monorepo defaults build-tools-source to 'subproject', which on Windows
    # pulls win-flex-bison-binaries, win-nasm and win-pkgconfig through wraps;
    # --wrap-mode=nofallback refuses them (job 22968).  Use the cvcpkg flex,
    # bison and pkg-config the recipe declares, found on PATH.
    '-Dbuild-tools-source=system',
    '-Dbase=enabled',
    '-Dgood=enabled',
    '-Dugly=disabled',
    '-Dbad=enabled',
    # nvcodec's Windows path needs gst-plugins-bad's d3d11 library types
    # (GstD3D11Device, GST_CAPS_FEATURE_MEMORY_D3D11_MEMORY), which come out
    # undeclared under MSVC 19.44 although d3d11 configures; the NVIDIA
    # hardware codecs are optional here, so leave the plugin out.
    '-Dgst-plugins-bad:nvcodec=disabled',
    '-Dlibav=disabled',
    '-Ddevtools=disabled',
    '-Dges=disabled',
    '-Drtsp_server=disabled',
    '-Drs=disabled',
    '-Dvaapi=disabled',
    '-Dgst-examples=disabled',
    '-Dpython=disabled',
    '-Dsharp=disabled',
    '-Dtls=disabled',
    '-Dlibnice=disabled',
    '-Dqt5=disabled',
    '-Dqt6=disabled',
    '-Dwebrtc=disabled',
    '-Dintrospection=disabled',
    '-Dnls=disabled',
    '-Dorc=disabled',
    '-Ddoc=disabled',
    '-Dgtk_doc=disabled',
    '-Dtests=disabled',
    '-Dexamples=disabled',
    '-Dtools=enabled',
    '-Dgpl=disabled'
)
