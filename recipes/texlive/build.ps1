# recipes/texlive/build.ps1 — stage TeX Live 2025 (pdfTeX, LuaTeX and LaTeX) on
# Windows from TeX Live's own frozen repository.
#
# Same method as build.sh: the pinned texlive.tlpdb pins every container,
# tl-mirror.pl builds a verified local mirror of the collections' closure, and
# TeX Live's installer installs offline from it. Both run under the cvcpkg perl
# (Strawberry Perl, a build dependency), not the tlperl inside install-tl.zip,
# which is deleted before the installer runs. install-tl.zip is still the
# installer used here because it carries the Windows tar.exe and xz.exe that
# install-tl unpacks containers with.
#
# The installed tree does contain tlperl: install-tl always installs the
# tlperl.windows package, because TeX Live's Windows script wrappers (fmtutil,
# updmap, tlmgr, ...) run through it at run time. That is payload, not build.
#
# Layout: lib\texlive is the TeX Live tree; bin\<tool>.cmd shims call
# lib\texlive\bin\windows\<tool>.exe (no dependable symlinks for unprivileged
# users). kpathsea finds the tree from the real executable, so the prefix
# relocates.
$ErrorActionPreference = 'Stop'

# Keep in step with build.sh and recipe.yaml's source block.
$base = 'https://ftp.math.utah.edu/pub/tex/historic/systems/texlive/2025/tlnet-final'
$tlpdbSha512 = '2fb7adc54cff58fa55c5b7c1bd3034e1efd849d0b73535c8dcdf0d8a36f66998b7a578ae036ef24354641be5f3738b0ad1323b7318581d88a0e271d1ac12cb2b'
$installerSha512 = '4571b00c2ca493b594097355735eb20a07c7ca52871515419ecef4951a59e4d4d24318edd92ccb917a6dc8883366d8930183c8d3173e23d3bd33fdf2a8ca8964'

$collections = @(
    'collection-basic', 'collection-latex', 'collection-latexrecommended',
    'collection-latexextra', 'collection-pictures', 'collection-fontsrecommended',
    'collection-langenglish'
)
# install-tl installs these whatever the selection: InstallExtraRequiredPackages
# (plus luatex on Windows), tlperl/tlgs for windows binaries, and
# collection-wintools whenever binary_windows is set.
$extra = @('texlive-scripts', 'kpathsea', 'hyphen-base', 'luatex',
           'tlperl.windows', 'tlgs.windows', 'collection-wintools')

$tools = @(
    'tex', 'etex', 'pdftex', 'pdflatex', 'latex',
    'luatex', 'luahbtex', 'lualatex', 'dvilualatex', 'texlua', 'texluac',
    'kpsewhich', 'kpseaccess', 'kpsestat', 'kpsereadlink',
    'bibtex', 'bibtex8', 'bibtexu', 'makeindex',
    'dvips', 'dvipdfmx', 'xdvipdfmx', 'extractbb', 'ebb',
    'mf', 'mktexpk', 'mktextfm', 'mktexlsr', 'texhash', 'mktexfmt',
    'fmtutil', 'fmtutil-sys', 'fmtutil-user', 'updmap', 'updmap-sys', 'updmap-user', 'tlmgr'
)

# The cvcpkg perl: its real perl.exe in the build prefix (bin\perl.cmd is only
# a launcher), else whatever `perl` resolves to on PATH.
$perl = $null
foreach ($root in @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX)) {
    if ($root) {
        $cand = Join-Path $root 'lib\strawberry\perl\bin\perl.exe'
        if (Test-Path $cand) { $perl = $cand; break }
    }
}
if (-not $perl) {
    $cmd = Get-Command perl -ErrorAction SilentlyContinue
    if ($cmd) { $perl = $cmd.Source }
}
if (-not $perl) { throw 'perl is required: the cvcpkg perl recipe is a build dependency' }
Write-Host "perl: $perl"

$w = Join-Path $env:CVC_BUILD_DIR 'texlive-work'
New-Item -ItemType Directory -Force -Path $w | Out-Null

function Get-Pinned([string]$rel, [string]$dest, [string]$sha512) {
    $ok = (Test-Path $dest) -and ((Get-FileHash $dest -Algorithm SHA512).Hash.ToLower() -eq $sha512)
    if (-not $ok) {
        Write-Host "Downloading $base/$rel ..."
        & curl.exe -fSL --retry 4 --retry-delay 3 -o $dest "$base/$rel"
        if ($LASTEXITCODE -ne 0) { throw "download failed: $base/$rel" }
    }
    $got = (Get-FileHash $dest -Algorithm SHA512).Hash.ToLower()
    if ($got -ne $sha512) { throw "sha512 mismatch for ${rel}: expected $sha512, got $got" }
    Write-Host "sha512 OK: $rel"
}

# ── 1. pinned package database and installer ──────────────────────────
$tlpdb = Join-Path $w 'texlive.tlpdb'
Get-Pinned 'tlpkg/texlive.tlpdb' $tlpdb $tlpdbSha512
$zip = Join-Path $w 'install-tl.zip'
Get-Pinned 'install-tl.zip' $zip $installerSha512
$instRoot = Join-Path $w 'installer'
if (Test-Path $instRoot) { Remove-Item -Recurse -Force $instRoot }
Expand-Archive -Path $zip -DestinationPath $instRoot -Force
$itl = (Get-ChildItem $instRoot -Directory -Filter 'install-tl-*' | Select-Object -First 1).FullName
if (-not $itl -or -not (Test-Path (Join-Path $itl 'install-tl'))) { throw 'no install-tl in install-tl.zip' }
# The build runs on the cvcpkg perl only.
$bundled = Join-Path $itl 'tlpkg\tlperl'
if (Test-Path $bundled) { Remove-Item -Recurse -Force $bundled }

# ── 2. local mirror, every container checked against the tlpdb ────────
$mirror = Join-Path $w 'mirror'
& $perl (Join-Path $PSScriptRoot 'tl-mirror.pl') --tlpdb $tlpdb --base $base --dest $mirror `
    --arch windows @collections @extra
if ($LASTEXITCODE -ne 0) { throw 'tl-mirror.pl failed' }

# ── 3. install-tl, offline, from the mirror ───────────────────────────
$texdir = Join-Path $env:CVC_INSTALL_DIR 'lib\texlive'
if (Test-Path $texdir) { Remove-Item -Recurse -Force $texdir }
$t = $texdir -replace '\\', '/'
$profileLines = @(
    'selected_scheme scheme-custom',
    "TEXDIR $t",
    "TEXMFLOCAL $t/texmf-local",
    "TEXMFSYSVAR $t/texmf-var",
    "TEXMFSYSCONFIG $t/texmf-config",
    'TEXMFHOME ~/texmf',
    'TEXMFVAR ~/.texlive2025/texmf-var',
    'TEXMFCONFIG ~/.texlive2025/texmf-config',
    'binary_windows 1'
) + ($collections | ForEach-Object { "$_ 1" }) + @(
    'instopt_adjustpath 0',
    'instopt_adjustrepo 0',
    'instopt_letter 0',
    'instopt_portable 1',
    'instopt_write18_restricted 1',
    'tlpdbopt_autobackup 0',
    'tlpdbopt_create_formats 1',
    'tlpdbopt_desktop_integration 0',
    'tlpdbopt_file_assocs 0',
    'tlpdbopt_generate_updmap 0',
    'tlpdbopt_install_docfiles 0',
    'tlpdbopt_install_srcfiles 0',
    'tlpdbopt_post_code 1',
    'tlpdbopt_w32_multi_user 0'
)
# instopt_portable 1: on Windows install-tl forces tlpdbopt_desktop_integration
# to 1 whatever the profile says ("always menu shortcuts") and, unless the
# install is portable, runs create_uninstaller -- Start-menu shortcuts and an
# HKCU uninstall entry in the BUILDER's account (USERPROFILE above does not
# redirect the shell folders).  Portable skips all of that; its only effect on
# the tree is texmf.cnf/texmfcnf.lua pointing the per-user trees into the
# install, which step 4 takes back out.
$profilePath = Join-Path $w 'texlive.profile'
[System.IO.File]::WriteAllLines($profilePath, [string[]]$profileLines)

# A scratch home keeps %USERPROFILE%\.texlive2025 out of the builder account,
# and no inherited TeX variable may steer the install.
$scratchHome = Join-Path $w 'home'
New-Item -ItemType Directory -Force -Path $scratchHome | Out-Null
foreach ($v in 'TEXMFCNF', 'TEXMFHOME', 'TEXMFVAR', 'TEXMFCONFIG', 'TEXINPUTS', 'TEXMFLOCAL') {
    Remove-Item "Env:$v" -ErrorAction SilentlyContinue
}
$env:USERPROFILE = $scratchHome
$env:HOME = $scratchHome

# install-tl's TLWinGoo requires Win32::Shortcut when it loads, for desktop and
# Start-menu shortcuts.  Strawberry Perl 5.40's portable zip does not ship it
# ("Can't locate Win32/Shortcut.pm in @INC", windows populate 2026-10-19), and
# this portable install creates no shortcut (see instopt_portable above), so a
# build-time stub satisfies the require and the two constants TLWinGoo imports;
# its new() dies if anything does try.  It is not installed, and the tree's own
# tlperl carries the real module.
& $perl -MWin32::Shortcut -e 1 2>$null
if ($LASTEXITCODE -ne 0) {
    $stubLib = Join-Path $w 'perl-stub'
    New-Item -ItemType Directory -Force -Path (Join-Path $stubLib 'Win32') | Out-Null
    $stub = @'
package Win32::Shortcut;
# cvcpkg build-time stub (recipes/texlive/build.ps1): install-tl creates no
# shortcut in this build, but TLWinGoo requires the module when it loads.
use strict;
use warnings;
use Exporter 'import';
our @EXPORT_OK = qw(SW_SHOWNORMAL SW_SHOWMINNOACTIVE);
use constant SW_SHOWNORMAL      => 1;
use constant SW_SHOWMINNOACTIVE => 7;
sub new { die "Win32::Shortcut is a build-time stub; shortcuts are not created\n" }
1;
'@
    [System.IO.File]::WriteAllText((Join-Path $stubLib 'Win32\Shortcut.pm'), $stub)
    $env:PERL5LIB = if ($env:PERL5LIB) { "$stubLib;$env:PERL5LIB" } else { $stubLib }
    Write-Host "perl has no Win32::Shortcut; using the build-time stub in $stubLib"
}

& $perl (Join-Path $itl 'install-tl') -profile $profilePath -repository $mirror `
    -no-verify-downloads -no-interaction -no-gui
if ($LASTEXITCODE -ne 0) { throw "install-tl failed ($LASTEXITCODE)" }

$bin = Join-Path $texdir 'bin\windows'
if (-not (Test-Path (Join-Path $bin 'pdflatex.exe'))) { throw 'install-tl did not produce bin\windows\pdflatex.exe' }

# ── 4. make the tree relocatable and point it at the pinned repository ─
# TEXDIR is kpathsea's $SELFAUTOPARENT; install-tl wrote it as an absolute path.
$cnf = Join-Path $texdir 'texmf.cnf'
$text = [System.IO.File]::ReadAllText($cnf)
foreach ($form in @($t, $texdir)) { $text = $text.Replace($form, '$SELFAUTOPARENT') }
# Undo the portable install's per-user redirection (see instopt_portable), so
# TEXMFHOME/TEXMFVAR/TEXMFCONFIG are kpathsea's ~/texmf, ~/.texlive2025/... as on
# the other platforms; the dropped lines are exactly the ones install-tl adds.
$portableLines = @('TEXMFHOME = $TEXMFLOCAL', 'TEXMFVAR = $TEXMFSYSVAR',
                   'TEXMFCONFIG = $TEXMFSYSCONFIG', 'ASYMPTOTE_HOME = $TEXMFCONFIG/asymptote')
$text = (($text -split "`r?`n") | Where-Object { $portableLines -notcontains $_.Trim() }) -join "`n"
$lua = Join-Path $texdir 'texmfcnf.lua'
if (Test-Path $lua) {
    $luaDrop = @('TEXMFHOME = "$TEXMFLOCAL",', 'TEXMFVAR = "$TEXMFSYSVAR",', 'TEXMFCONFIG = "$TEXMFSYSCONFIG",')
    $luaText = ([System.IO.File]::ReadAllLines($lua) | Where-Object { $luaDrop -notcontains $_.Trim() }) -join "`n"
    [System.IO.File]::WriteAllText($lua, $luaText + "`n")
}
[System.IO.File]::WriteAllText($cnf, $text)
if ($text.Contains(($env:CVC_INSTALL_DIR -replace '\\', '/')) -or $text.Contains($env:CVC_INSTALL_DIR)) {
    throw 'texmf.cnf still names the staging prefix'
}
& (Join-Path $bin 'tlmgr.bat') option repository $base
if ($LASTEXITCODE -ne 0) { throw 'tlmgr option repository failed' }
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $texdir 'install-tl.log'), (Join-Path $texdir 'tlpkg\texlive.profile')
Get-ChildItem (Join-Path $texdir 'texmf-var\web2c') -Recurse -Filter '*.log' | Remove-Item -Force
$backups = Join-Path $texdir 'tlpkg\backups'
if (Test-Path $backups) { Remove-Item -Recurse -Force $backups }
# Generated files carry provenance comments naming the build job's paths
# ("Generated by <job>/install-tl-*/install-tl", "% <job>/texmf-var/...map:").
# They live in texmf-dist\web2c and texmf-var; rewrite them so the bundle
# names no build machine, then insist nothing outside a comment still does.
$itlScript = (Join-Path $itl 'install-tl')
$forms = @(@($itlScript, 'install-tl'), @(($itlScript -replace '\\', '/'), 'install-tl'),
           @($texdir, '<texlive>'), @($t, '<texlive>'))
$genDirs = @((Join-Path $texdir 'texmf-dist\web2c'), (Join-Path $texdir 'texmf-var'))
# Text files only, as build.sh's `grep -I`: the formats fmtutil dumps (tex.fmt,
# mf.base, ...) are binary and record their input files' paths, which only
# diagnostics read, and a text round trip would corrupt them.  A NUL in the
# first 8000 bytes marks a file binary, as grep decides it.
$genFiles = Get-ChildItem $genDirs -Recurse -File | Where-Object {
    $fs = [System.IO.File]::OpenRead($_.FullName)
    try { $buf = New-Object byte[] 8000; $n = $fs.Read($buf, 0, $buf.Length) } finally { $fs.Dispose() }
    [Array]::IndexOf($buf, [byte]0, 0, $n) -lt 0
}
foreach ($f in $genFiles) {
    $raw = [System.IO.File]::ReadAllText($f.FullName)
    $new = $raw
    foreach ($pair in $forms) { $new = $new.Replace($pair[0], $pair[1]) }
    if ($new -ne $raw) { [System.IO.File]::WriteAllText($f.FullName, $new) }
}
$leftover = $genFiles |
    Select-String -SimpleMatch -Pattern $env:CVC_INSTALL_DIR, ($env:CVC_INSTALL_DIR -replace '\\', '/'),
                                        $env:CVC_BUILD_DIR, ($env:CVC_BUILD_DIR -replace '\\', '/') |
    Where-Object { $_.Line -notmatch '^\s*(%|#|--)' }
if ($leftover) { $leftover | ForEach-Object { Write-Host $_ }; throw 'staging paths left in the TeX Live tree' }

# ── 5. bin\ shims ─────────────────────────────────────────────────────
$prefixBin = Join-Path $env:CVC_INSTALL_DIR 'bin'
New-Item -ItemType Directory -Force -Path $prefixBin | Out-Null
foreach ($tool in $tools) {
    foreach ($ext in '.exe', '.bat') {
        if (Test-Path (Join-Path $bin "$tool$ext")) {
            $shim = "@`"%~dp0..\lib\texlive\bin\windows\$tool$ext`" %*`r`n"
            [System.IO.File]::WriteAllText((Join-Path $prefixBin "$tool.cmd"), $shim)
            break
        }
    }
}

# ── 6. prove it: the staged pdflatex builds a document ────────────────
$smoke = Join-Path $w 'smoke'
if (Test-Path $smoke) { Remove-Item -Recurse -Force $smoke }
New-Item -ItemType Directory -Force -Path $smoke | Out-Null
$doc = @'
\documentclass{article}
\usepackage[T1]{fontenc}
\usepackage{lmodern,amsmath,tikz,pgfplots,tcolorbox,booktabs,hyperref}
\pgfplotsset{compat=1.17}
\begin{document}
\begin{tcolorbox}cvcpkg texlive smoke test: $\int_0^1 x\,\mathrm{d}x=\tfrac12$\end{tcolorbox}
\begin{tikzpicture}\begin{axis}[width=5cm]\addplot{x^2};\end{axis}\end{tikzpicture}
\end{document}
'@
[System.IO.File]::WriteAllText((Join-Path $smoke 'smoke.tex'), $doc)
Push-Location $smoke
try {
    & (Join-Path $bin 'pdflatex.exe') -interaction=nonstopmode -halt-on-error smoke.tex | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path 'smoke.pdf')) {
        Get-Content smoke.log -Tail 40 | Write-Host
        throw 'pdflatex smoke test failed'
    }
} finally { Pop-Location }
$verOut = & (Join-Path $bin 'pdflatex.exe') --version
Write-Host ($verOut | Select-Object -First 1)
Remove-Item -Recurse -Force $mirror, $instRoot, $smoke
