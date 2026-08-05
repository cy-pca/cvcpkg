# recipes/fontconfig/build.ps1 — build Fontconfig on Windows via Meson + MSVC.
#
# The directory options are deliberately left at their defaults.  Upstream's
# meson.build already special-cases Windows for exactly these:
#
#     fc_cachedir   = 'LOCAL_APPDATA_FONTCONFIG_CACHE'
#     fc_fonts_paths = ['WINDOWSFONTDIR', 'WINDOWSUSERFONTDIR']
#
# Those are tokens fontconfig expands at runtime on the consumer's machine.
# Passing -Dcache-dir / -Dconfig-dir / -Dbaseconfig-dir here would replace
# them with a literal builder path and break relocation, which is the whole
# point of a cvcpkg bundle.
#
# -Dcache-build=disabled matches build.sh: a font cache generated on the
# builder describes the builder's fonts, not the consumer's.
# -Diconv is left alone (default disabled); upstream warns it is
# non-functional on Windows.  NLS is off because gettext is not in this
# recipe's closure.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# fontconfig generates src/fcobjshash.h with gperf.  There is no gperf cvcpkg
# recipe and Windows has no system gperf, so when it is missing meson falls
# back to upstream's subprojects/gperf.wrap — which needs network access at
# setup time.  Warn rather than throw: a builder that does have gperf on PATH
# (or a pre-populated subprojects/ tree) builds fine either way.
if (-not (Get-Command gperf -ErrorAction SilentlyContinue)) {
    Write-Host "cvcpkg: WARNING - gperf not on PATH; meson will fall back to subprojects/gperf.wrap (needs network)."
}

Invoke-CvcMesonBuild @(
    '-Dtests=disabled',
    '-Ddoc=disabled',
    '-Ddoc-txt=disabled',
    '-Ddoc-man=disabled',
    '-Ddoc-pdf=disabled',
    '-Ddoc-html=disabled',
    '-Dnls=disabled',
    '-Dcache-build=disabled',
    '-Dtools=enabled'
)
