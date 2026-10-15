# recipes/lxml-cp311/build.ps1 — Windows from-source build of lxml 6.1.3 for the
# cp311 column, linking cvcpkg's own libxml2 + libxslt (see build.sh for the
# full why).
#
# DELTAS vs build.sh:
#   * MSVC comes from _common/env-windows.ps1 (Import-CvcMsvcEnv at dot-source).
#   * no rpath pass — PE has no RUNPATH. libxml2/libxslt DLLs are found the way
#     every other Windows bundle finds its siblings: out of the activated
#     prefix's bin/ on PATH.
#   * discovery is still pkg-config: lxml's setupinfo queries libxml-2.0 /
#     libxslt via PKG_CONFIG, and we pin PKG_CONFIG_PATH at the prefix so the
#     cvcpkg .pc files (and only those) are what it finds. STATIC_DEPS=false
#     keeps lxml from downloading + statically building its own copies.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"    # cl.exe on PATH, CMAKE_PREFIX_PATH
. "$scriptDir\..\_common\python-wheel.ps1"    # Get-CvcPythonExe

$py = Get-CvcPythonExe
$deps = $env:CVC_DEPS_PREFIX
$bld  = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $deps }
Write-Output "lxml-cp311: building with $py"

# Bridge BUILD-only python columns (setuptools/wheel) and put our bin/Scripts
# (which carry pkg-config.exe and the libxml2/libxslt DLLs) ahead of the host's.
$env:PATH = "$bld\bin;$bld\Scripts;$deps\bin;$deps\Scripts;$env:PATH"
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$bld\Lib\site-packages;$env:PYTHONPATH" }
                  else { "$bld\Lib\site-packages" }

# Hermetic libxml2/libxslt discovery via pkg-config pinned at the prefix.
$pcDirs = @("$deps\lib\pkgconfig", "$bld\lib\pkgconfig") | Where-Object { Test-Path $_ }
$env:PKG_CONFIG_PATH   = ($pcDirs -join [IO.Path]::PathSeparator)
$env:PKG_CONFIG_LIBDIR = ($pcDirs -join [IO.Path]::PathSeparator)
$env:STATIC_DEPS = 'false'
$pkgconfig = Get-Command pkg-config -ErrorAction SilentlyContinue
if (-not $pkgconfig) { throw "lxml-cp311: pkg-config not on PATH (pkg-config dep must be in the closure)" }
$env:PKG_CONFIG = $pkgconfig.Source
foreach ($mod in 'libxml-2.0', 'libxslt', 'libexslt') {
    & $pkgconfig.Source --exists $mod
    if ($LASTEXITCODE -ne 0) {
        throw "lxml-cp311: $mod.pc not found by pkg-config (PKG_CONFIG_LIBDIR=$env:PKG_CONFIG_LIBDIR)"
    }
}
Write-Output ("lxml-cp311: libxml-2.0 {0}, libxslt {1}" -f `
    (& $pkgconfig.Source --modversion libxml-2.0), (& $pkgconfig.Source --modversion libxslt))

# MSVC import-library name bridge.  pkg-config emits the Unix `-lNAME` spelling
# (e.g. libxml-2.0.pc -> `-lxml2`), which lxml's setup hands to MSVC as the
# library "xml2" -> it then searches for xml2.lib.  cvcpkg's libxml2 Windows
# bundle, however, ships its import lib as libxml2.lib, so the link fails.
# Bridge it with build-local aliases on the linker search path (LIB): for every
# lib*.lib in the deps prefix, provide the lib-prefix-stripped spelling MSVC
# expects.  This is contained to this build and never mutates the shared
# bundle.  (Our own libxslt/libexslt use OUTPUT_NAME xslt/exslt, so if they
# ship as xslt.lib the pass is a no-op for them; if they ship as libxslt.lib it
# aliases those too — correct either way.)
$linkCompat = Join-Path $env:CVC_BUILD_DIR 'linkcompat'
New-Item -ItemType Directory -Force -Path $linkCompat | Out-Null
foreach ($implib in Get-ChildItem (Join-Path $deps 'lib') -Filter 'lib*.lib' -ErrorAction SilentlyContinue) {
    $alias = Join-Path $linkCompat ($implib.BaseName.Substring(3) + '.lib')
    if (-not (Test-Path $alias)) { Copy-Item $implib.FullName $alias }
}
$env:LIB = "$linkCompat;$env:LIB"

$wheelhouse = Join-Path $env:CVC_BUILD_DIR 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "lxml-cp311: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter 'lxml-*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "lxml-cp311: no wheel produced under $wheelhouse" }
Write-Output "lxml-cp311: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "lxml-cp311: pip install failed ($LASTEXITCODE)" }

$sitePackages = Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages'
if (-not (Test-Path -LiteralPath $sitePackages)) {
    throw "lxml-cp311: no Lib\site-packages under $env:CVC_INSTALL_DIR after pip install"
}

# The staged extension resolves libxml2/libxslt DLLs off PATH (deps\bin, added
# above). Parse + a real XSLT transform proves the native xslt leg is linked.
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$sitePackages;$env:PYTHONPATH" } else { $sitePackages }
$check = @'
from lxml import etree
print("lxml", etree.__version__, "| libxml2", etree.LIBXML_VERSION, "| libxslt", etree.LIBXSLT_VERSION)
root = etree.fromstring("<doc><item>hi</item></doc>")
assert root.findtext("item") == "hi", "parse failed"
style = etree.fromstring(
    '<xsl:stylesheet version="1.0"'
    ' xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
    '<xsl:template match="/"><out><xsl:value-of select="//item"/></out>'
    '</xsl:template></xsl:stylesheet>')
out = etree.tostring(etree.XSLT(style)(root)).decode()
assert "<out>hi</out>" in out, "XSLT transform wrong: " + repr(out)
print("lxml-cp311 build + verification complete")
'@
& $py -c $check
if ($LASTEXITCODE -ne 0) { throw "lxml-cp311: verification failed ($LASTEXITCODE)" }
