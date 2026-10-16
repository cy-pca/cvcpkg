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

# fontconfig generates src/fcobjshash.h with gperf, cvcpkg's gperf recipe
# (depends.build).  Invoke-CvcMesonBuild's --wrap-mode=nofallback makes a
# missing gperf fail setup; meson used to fall back to upstream's
# subprojects/gperf.wrap, a git clone during the build.
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

# Fonts installed into the same prefix (liberation-fonts, carlito-fonts) live
# in share\fonts. The DLL already finds etc\fonts from its own location (see
# DllMain in src/fccfg.c), so a <dir> relative to fonts.conf finds them
# wherever the prefix is unpacked, as build.sh does on Unix.
$fcConf = Join-Path $env:CVC_INSTALL_DIR 'etc\fonts\fonts.conf'
if (Test-Path $fcConf) {
    $lines = [System.IO.File]::ReadAllLines($fcConf)
    $out = New-Object System.Collections.Generic.List[string]
    $done = $false
    foreach ($l in $lines) {
        if (-not $done -and $l -match '^(\s*)<dir prefix="xdg">fonts</dir>') {
            $out.Add($Matches[1] + '<dir prefix="relative">../../share/fonts</dir>')
            $done = $true
        }
        $out.Add($l)
    }
    if (-not $done) { throw 'fonts.conf: no <dir prefix="xdg">fonts</dir> anchor' }
    [System.IO.File]::WriteAllLines($fcConf, [string[]]$out)
}
