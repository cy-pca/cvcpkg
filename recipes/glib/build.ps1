# recipes/glib/build.ps1 — build GLib on Windows via Meson + MSVC.
#
# GLib 2.x has good MSVC support via Meson.  We disable Linux-only
# features (SELinux, libmount, dtrace, sysprof) and introspection
# (requires Python GObject headers not available here).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# GLib's meson build requires the Python 'packaging' module in the interpreter
# meson runs on.  meson.cmd runs the build prefix's python.exe (python312), and
# packaging-cp312 (recipe.yaml) installs into its Lib\site-packages.

# GLib requires libintl, which cvcpkg's proxy-libintl provides here.  Meson's
# dependency('intl') has no pkg-config method: it looks for libintl.h and
# intl.lib on the compiler's own search path, so put the deps prefix on it.
$env:INCLUDE = "$env:CVC_DEPS_PREFIX\include;$env:INCLUDE"
$env:LIB = "$env:CVC_DEPS_PREFIX\lib;$env:LIB"

Invoke-CvcMesonBuild @(
    '-Dtests=false',
    '-Dglib_debug=disabled',
    '-Dnls=disabled',
    '-Dman-pages=disabled',
    '-Dselinux=disabled',
    '-Dlibmount=disabled',
    '-Dintrospection=disabled',
    '-Ddtrace=false',
    '-Dsysprof=disabled'
)

# bin/gdbus-codegen finds its codegen package at bin/../share/glib-2.0 and,
# when that lookup fails, falls back to the build's datadir, which meson writes
# in as an absolute path.  2.80.5+cvc.4's Windows bundle shipped the builder's:
#     path = os.path.join('C:/Users/jmr/cvcpkg-builder/cvcpkg-job-glib-6g8q6v_y/cvcpkg-glib-hm_lc9i4/install/share', 'glib-2.0')
# a directory no consumer has, and in a builder job's deps prefix the
# temp-path rewrite turned it into 'C:C:\Users\...\share', which Python cannot
# parse (gtk4 on dev, job #23592).  The fallback now looks next to the script's
# real location, so a symlinked or copied gdbus-codegen still finds codegen.
$codegen = Join-Path $env:CVC_INSTALL_DIR 'bin\gdbus-codegen'
$text = [System.IO.File]::ReadAllText($codegen)
$fallback = "(?m)^(\s*)path = os\.path\.join\('[^']*', 'glib-2\.0'\)"
$hits = [regex]::Matches($text, $fallback).Count
if ($hits -ne 1) { throw "gdbus-codegen: expected one datadir fallback line, found $hits" }
$text = [regex]::Replace($text, $fallback,
    '${1}path = os.path.join(os.path.dirname(os.path.realpath(__file__)), ''..'', ''share'', ''glib-2.0'')')
[System.IO.File]::WriteAllText($codegen, $text, (New-Object System.Text.UTF8Encoding $false))

# meson installs bin/glib-gettextize (a /bin/sh autotools helper) only when it
# finds an sh on PATH, and the bash completions only with a bash: Git Bash on a
# dev box, not on the builder that built cvc.4, whose bundle has neither.  Drop
# both so the payload does not depend on the host's PATH.  glib-gettextize
# would not work from a bundle anyway: it bakes the install dir into prefix= and
# datadir=, and with -Dnls=disabled the gettext files it copies (share/glib-2.0/
# gettext) are never installed.
foreach ($extra in @('bin\glib-gettextize', 'share\bash-completion')) {
    $p = Join-Path $env:CVC_INSTALL_DIR $extra
    if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Recurse -Force }
}

# Nothing installed may still name this build's install or deps prefix: those
# directories are gone once the job ends.  Binaries (any NUL byte) are skipped.
$prefixForms = foreach ($p in @($env:CVC_INSTALL_DIR, $env:CVC_DEPS_PREFIX)) {
    if (-not $p) { continue }
    $p = $p.TrimEnd('\', '/')
    $p; ($p -replace '\\', '/'); (ConvertTo-CvcMsysPath $p)
}
$prefixForms = @($prefixForms | Sort-Object -Unique)
$leftover = Get-ChildItem -LiteralPath $env:CVC_INSTALL_DIR -Recurse -File | Where-Object {
    $bytes = [System.IO.File]::ReadAllBytes($_.FullName)
    if ([Array]::IndexOf($bytes, [byte]0) -ge 0) { return $false }
    $t = [System.Text.Encoding]::UTF8.GetString($bytes)
    foreach ($form in $prefixForms) {
        if ($t.IndexOf($form, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    }
    $false
}
if ($leftover) {
    $leftover | ForEach-Object { Write-Host "  $($_.FullName)" }
    throw 'glib: installed files still name the build''s install or deps prefix (listed above)'
}
