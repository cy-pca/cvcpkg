#!/usr/bin/env pwsh
# recipes/msvc/build.ps1 — lay out the MSVC toolset and the Windows SDK from
# Microsoft's own Visual Studio packages, in the prefix, with no installer.
#
# `redistributable: false`, so this runs only on the machine installing the
# recipe, after the user accepted Microsoft's licenses (CVCPKG_ACCEPT_MSVC_LICENSE,
# which `cvcpkg install` sets only on an explicit yes). Every payload comes
# from download.visualstudio.microsoft.com and is checked against msvc.lock
# before it is unpacked; Microsoft's files are copied, never edited (vctip.exe,
# the compiler-usage telemetry helper, is the one file removed).
#
# Layout — Visual Studio's own, rooted at <prefix>\msvc:
#
#   VC\Tools\MSVC\<toolset>\{bin\Hostx64\x64,include,lib\x64,...}   from the VSIX packages
#   VC\Redist\MSVC\<redist>\x64\Microsoft.VC143.CRT\              redistributable CRT DLLs
#   VC\Auxiliary\Build\{vcvars64,vcvarsall}.bat                     stand-ins (nvcc looks here)
#   Windows Kits\10\{Include,Lib,bin}\<sdk>\                        from `msiexec /a` on the SDK msi files
#   setup_x64.ps1, setup_x64.bat                                    PATH/INCLUDE/LIB/LIBPATH + vcvars variables
#   msvc.json                                                       what was installed, from which lock
#
# It must not need a compiler -- providing one is its job -- so unlike other
# Windows recipes it does not dot-source _common/env-windows.ps1 (whose
# Import-CvcMsvcEnv throws when no MSVC is installed).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is many times slower with the progress bar
Set-StrictMode -Version Latest

if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }
if (-not $env:CVC_BUILD_DIR)   { throw 'CVC_BUILD_DIR must be set' }
if ($env:PROCESSOR_ARCHITECTURE -and $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw "msvc: msvc.lock covers x64 hosts only, not $($env:PROCESSOR_ARCHITECTURE)"
}

$lockPath = Join-Path $PSScriptRoot 'msvc.lock'
$lines = Get-Content -LiteralPath $lockPath | Where-Object { $_ -and -not $_.StartsWith('#') }
# The values on the first '<key> ...' line, always as an array (callers index it).
function Get-LockValue([string]$key) {
    $line = $lines | Where-Object { $_ -match "^$key\s" } | Select-Object -First 1
    if (-not $line) { throw "msvc: msvc.lock has no '$key' line" }
    return , @($line -split '\s+' | Select-Object -Skip 1)
}
$release  = (Get-LockValue 'release')[0]
$toolset  = (Get-LockValue 'toolset')[0]
$clPkgVer = (Get-LockValue 'toolset')[1]
$redist   = (Get-LockValue 'redist')[0]
$sdk      = (Get-LockValue 'sdk')[0]
$licenses = @($lines | Where-Object { $_ -match '^license\s' } | ForEach-Object { ($_ -split '\s+')[1] })

# The license gate.  `cvcpkg install` asks the user (or takes
# --accept-license msvc) and exports this only on an explicit yes; checking it
# here as well keeps `cvcpkg build msvc` and any other way in behind the same gate.
if (@('1', 'y', 'yes', 'true') -notcontains "$env:CVCPKG_ACCEPT_MSVC_LICENSE".Trim().ToLowerInvariant()) {
    throw ("msvc: Microsoft's packages are downloaded under the Visual Studio Build Tools and " +
           "Windows SDK licenses:`n  " + ($licenses -join "`n  ") +
           "`ncvcpkg does not accept a license for you. Once you have read and accept the terms, " +
           "install with --accept-license msvc or set CVCPKG_ACCEPT_MSVC_LICENSE=1.")
}

# Payloads: <kind> <sha256> <size> <url> <name, may contain spaces>.
$payloads = foreach ($line in $lines) {
    $cols = $line -split '\s+', 5
    if ($cols[0] -notin 'vsix', 'msi', 'cab') { continue }
    [pscustomobject]@{ Kind = $cols[0]; Sha = $cols[1]; Size = [int64]$cols[2]; Url = $cols[3]; Name = $cols[4] }
}
if (-not $payloads) { throw 'msvc: msvc.lock lists no payloads' }

# Cache downloads by sha256 where cvcpkg keeps its own source cache
# (CVCPKG_SOURCE_CACHE_DIR; set it empty to disable).
if (Test-Path Env:CVCPKG_SOURCE_CACHE_DIR) {
    $cache = $env:CVCPKG_SOURCE_CACHE_DIR
} elseif ($env:XDG_CACHE_HOME) {
    $cache = Join-Path $env:XDG_CACHE_HOME 'cvcpkg/sources'
} else {
    $cache = Join-Path $HOME '.cache/cvcpkg/sources'
}

$root  = Join-Path $env:CVC_INSTALL_DIR 'msvc'
$dl    = Join-Path $env:CVC_BUILD_DIR 'payloads'
$sdkDl = Join-Path $env:CVC_BUILD_DIR 'sdk-installers'
foreach ($d in $dl, $sdkDl) {
    if (Test-Path -LiteralPath $d) { Remove-Item -LiteralPath $d -Recurse -Force }
}
if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
New-Item -ItemType Directory -Force -Path $dl, $sdkDl, $root | Out-Null
$rootFull = (Resolve-Path -LiteralPath $root).Path

# Return a local, sha256-verified copy of $p.
function Get-Payload($p) {
    $ext = [IO.Path]::GetExtension($p.Name)
    $cached = if ($cache) { Join-Path $cache "$($p.Sha)$ext" } else { '' }
    if ($cached -and (Test-Path -LiteralPath $cached) -and
        (Get-FileHash -LiteralPath $cached -Algorithm SHA256).Hash -eq $p.Sha) {
        return $cached
    }
    $file = Join-Path $dl "$($p.Sha)$ext"
    Write-Host ("msvc: downloading {0} ({1:N1} MB)" -f $p.Name, ($p.Size / 1MB))
    Invoke-WebRequest -Uri $p.Url -OutFile $file -UseBasicParsing -MaximumRetryCount 3 -RetryIntervalSec 5
    $actual = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $p.Sha) { throw "msvc: sha256 mismatch for $($p.Name): expected $($p.Sha), got $actual" }
    if ($cache) {
        New-Item -ItemType Directory -Force -Path $cache | Out-Null
        Copy-Item -LiteralPath $file -Destination $cached -Force
    }
    return $file
}

# ── MSVC: unpack each VSIX's Contents/ into the root ─────────────────
# A VSIX is an OPC zip whose part names are URI-escaped.  Two packages may ship
# the same file only if it is byte-identical; anything else is a lock bug.
Add-Type -AssemblyName System.IO.Compression.FileSystem
foreach ($p in $payloads | Where-Object Kind -eq 'vsix') {
    $file = Get-Payload $p
    $zip = [System.IO.Compression.ZipFile]::OpenRead($file)
    try {
        foreach ($e in $zip.Entries) {
            if (-not $e.FullName.StartsWith('Contents/') -or $e.FullName.EndsWith('/')) { continue }
            $rel = [Uri]::UnescapeDataString($e.FullName.Substring('Contents/'.Length))
            $dst = [IO.Path]::GetFullPath((Join-Path $rootFull $rel))
            if (-not $dst.StartsWith($rootFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                throw "msvc: $($p.Name) entry $($e.FullName) escapes the install root"
            }
            if (Test-Path -LiteralPath $dst) {
                $s = $e.Open()
                try { $h = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($s)) } finally { $s.Dispose() }
                if ($h -ne (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash) {
                    throw "msvc: $rel differs between packages ($($p.Name))"
                }
                continue
            }
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($e, $dst, $false)
        }
    } finally {
        $zip.Dispose()
    }
    Write-Host "msvc: unpacked $($p.Name)"
}

# ── Windows SDK: an administrative image of each installer ───────────
# `msiexec /a` extracts an installer's files under TARGETDIR (here, the root,
# giving Windows Kits\10\...) without installing or registering anything.  The
# msi files read their cabinets from their own directory, by name.
foreach ($p in $payloads | Where-Object { $_.Kind -in 'msi', 'cab' }) {
    Copy-Item -LiteralPath (Get-Payload $p) -Destination (Join-Path $sdkDl $p.Name) -Force
}
foreach ($p in $payloads | Where-Object Kind -eq 'msi') {
    $msi = Join-Path $sdkDl $p.Name
    $log = Join-Path $env:CVC_BUILD_DIR ("msiexec-{0}.log" -f $p.Sha.Substring(0, 12))
    $proc = Start-Process -FilePath "$env:SystemRoot\System32\msiexec.exe" -Wait -PassThru -NoNewWindow `
        -ArgumentList "/a `"$msi`" /quiet /qn /norestart TARGETDIR=`"$rootFull`" /L*v `"$log`""
    if ($proc.ExitCode -ne 0) {
        if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 40 | Write-Host }
        throw "msvc: msiexec /a $($p.Name) failed with exit code $($proc.ExitCode) (log: $log)"
    }
    Write-Host "msvc: extracted $($p.Name)"
}
# An administrative image keeps a copy of each .msi at its root.
Get-ChildItem -LiteralPath $rootFull -Filter '*.msi' -File | Remove-Item -Force

$vcTools = Join-Path $rootFull "VC\Tools\MSVC\$toolset"
$kits    = Join-Path $rootFull 'Windows Kits\10'
Remove-Item -LiteralPath (Join-Path $vcTools 'bin\Hostx64\x64\vctip.exe') -Force -ErrorAction SilentlyContinue

foreach ($f in "$vcTools\bin\Hostx64\x64\cl.exe", "$vcTools\bin\Hostx64\x64\link.exe",
               "$vcTools\bin\Hostx64\x64\lib.exe", "$vcTools\bin\Hostx64\x64\1033\clui.dll",
               "$vcTools\include\vector", "$vcTools\include\omp.h",
               "$vcTools\lib\x64\msvcrt.lib", "$vcTools\lib\x64\msvcprt.lib", "$vcTools\lib\x64\vcruntime.lib",
               "$vcTools\lib\x64\oldnames.lib", "$vcTools\lib\x64\libcmt.lib", "$vcTools\lib\x64\libomp.lib",
               "$rootFull\VC\Redist\MSVC\$redist\x64\Microsoft.VC143.CRT\vcruntime140.dll",
               "$kits\Include\$sdk\um\windows.h", "$kits\Include\$sdk\ucrt\stdio.h",
               "$kits\Include\$sdk\shared\winapifamily.h", "$kits\Lib\$sdk\um\x64\kernel32.lib",
               "$kits\Lib\$sdk\ucrt\x64\ucrt.lib", "$kits\bin\$sdk\x64\rc.exe", "$kits\bin\$sdk\x64\mt.exe") {
    if (-not (Test-Path -LiteralPath $f)) { throw "msvc: $($f.Substring($rootFull.Length + 1)) missing from the staged toolset" }
}

# ── Setup scripts: relocatable, everything relative to their own directory ──
$setupPs1 = @"
# Generated by cvcpkg recipes/msvc/build.ps1.  Run it:  & <prefix>\msvc\setup_x64.ps1
# Puts MSVC $toolset (x64) and the Windows SDK $sdk on PATH and sets INCLUDE,
# LIB and LIBPATH -- replacing any a Visual Studio vcvars already set, so the
# two never mix -- plus the variables vcvars64.bat sets that tools look for.
# Only process environment variables change, so running it is enough; it
# defines no variables in the caller's scope.
& { param(`$root)
`$vcTools = Join-Path `$root 'VC\Tools\MSVC\$toolset'
`$kits    = Join-Path `$root 'Windows Kits\10'
`$sdk     = '$sdk'
`$env:CVC_MSVC_ROOT        = `$root
`$env:VCINSTALLDIR         = "`$root\VC\"
`$env:VCToolsInstallDir    = "`$vcTools\"
`$env:VCToolsVersion       = '$toolset'
`$env:VCToolsRedistDir     = "`$root\VC\Redist\MSVC\$redist\"
`$env:WindowsSdkDir        = "`$kits\"
`$env:WindowsSDKVersion    = "`$sdk\"
`$env:WindowsSdkBinPath    = "`$kits\bin\"
`$env:WindowsSdkVerBinPath = "`$kits\bin\`$sdk\"
`$env:UniversalCRTSdkDir   = "`$kits\"
`$env:UCRTVersion          = `$sdk
`$env:VSCMD_ARG_HOST_ARCH  = 'x64'
`$env:VSCMD_ARG_TGT_ARCH   = 'x64'
`$env:Platform             = 'x64'
`$env:INCLUDE = @("`$vcTools\include", "`$kits\Include\`$sdk\ucrt", "`$kits\Include\`$sdk\shared",
                 "`$kits\Include\`$sdk\um", "`$kits\Include\`$sdk\winrt", "`$kits\Include\`$sdk\cppwinrt") -join ';'
`$env:EXTERNAL_INCLUDE = `$env:INCLUDE
`$env:LIB     = @("`$vcTools\lib\x64", "`$kits\Lib\`$sdk\ucrt\x64", "`$kits\Lib\`$sdk\um\x64") -join ';'
`$env:LIBPATH = "`$vcTools\lib\x64"
`$bins = @("`$vcTools\bin\Hostx64\x64", "`$kits\bin\`$sdk\x64", "`$kits\bin\`$sdk\x64\ucrt")
`$env:PATH = (`$bins + @(`$env:PATH -split ';' | Where-Object { `$_ -and `$bins -notcontains `$_ })) -join ';'
} `$PSScriptRoot
"@
Set-Content -LiteralPath (Join-Path $rootFull 'setup_x64.ps1') -Value $setupPs1 -Encoding utf8NoBOM

$setupBat = @"
@echo off
rem Generated by cvcpkg recipes/msvc/build.ps1.  call <prefix>\msvc\setup_x64.bat
rem Puts MSVC $toolset (x64) and the Windows SDK $sdk on PATH and sets INCLUDE,
rem LIB and LIBPATH (replacing any already set), plus the vcvars64.bat variables.
set "CVC_MSVC_ROOT=%~dp0"
if "%CVC_MSVC_ROOT:~-1%"=="\" set "CVC_MSVC_ROOT=%CVC_MSVC_ROOT:~0,-1%"
set "VCINSTALLDIR=%CVC_MSVC_ROOT%\VC\"
set "VCToolsVersion=$toolset"
set "VCToolsInstallDir=%CVC_MSVC_ROOT%\VC\Tools\MSVC\$toolset\"
set "VCToolsRedistDir=%CVC_MSVC_ROOT%\VC\Redist\MSVC\$redist\"
set "WindowsSdkDir=%CVC_MSVC_ROOT%\Windows Kits\10\"
set "WindowsSDKVersion=$sdk\"
set "WindowsSdkBinPath=%CVC_MSVC_ROOT%\Windows Kits\10\bin\"
set "WindowsSdkVerBinPath=%CVC_MSVC_ROOT%\Windows Kits\10\bin\$sdk\"
set "UniversalCRTSdkDir=%CVC_MSVC_ROOT%\Windows Kits\10\"
set "UCRTVersion=$sdk"
set "VSCMD_ARG_HOST_ARCH=x64"
set "VSCMD_ARG_TGT_ARCH=x64"
set "Platform=x64"
set "INCLUDE=%VCToolsInstallDir%include;%WindowsSdkDir%Include\$sdk\ucrt;%WindowsSdkDir%Include\$sdk\shared;%WindowsSdkDir%Include\$sdk\um;%WindowsSdkDir%Include\$sdk\winrt;%WindowsSdkDir%Include\$sdk\cppwinrt"
set "EXTERNAL_INCLUDE=%INCLUDE%"
set "LIB=%VCToolsInstallDir%lib\x64;%WindowsSdkDir%Lib\$sdk\ucrt\x64;%WindowsSdkDir%Lib\$sdk\um\x64"
set "LIBPATH=%VCToolsInstallDir%lib\x64"
set "PATH=%VCToolsInstallDir%bin\Hostx64\x64;%WindowsSdkVerBinPath%x64;%WindowsSdkVerBinPath%x64\ucrt;%PATH%"
exit /b 0
"@
Set-Content -LiteralPath (Join-Path $rootFull 'setup_x64.bat') -Value ($setupBat -replace "`r?`n", "`r`n") -Encoding ascii

# vcvars stand-ins where Visual Studio keeps the real ones: nvcc (given -ccbin
# ...\bin\Hostx64\x64) and other tools locate VC\Auxiliary\Build\vcvars*.bat
# relative to cl.exe.  x64 host and target only.
$aux = Join-Path $rootFull 'VC\Auxiliary\Build'
New-Item -ItemType Directory -Force -Path $aux | Out-Null
Set-Content -LiteralPath (Join-Path $aux 'vcvars64.bat') -Encoding ascii -Value (
    "@echo off`r`nrem cvcpkg msvc: stands in for Visual Studio's vcvars64.bat`r`ncall `"%~dp0..\..\..\setup_x64.bat`"`r`n")
Set-Content -LiteralPath (Join-Path $aux 'vcvarsall.bat') -Encoding ascii -Value (
    "@echo off`r`nrem cvcpkg msvc: stands in for Visual Studio's vcvarsall.bat (x64 only)`r`n" +
    "if /i `"%~1`"==`"x64`" goto ok`r`nif /i `"%~1`"==`"amd64`" goto ok`r`n" +
    "echo vcvarsall.bat: this cvcpkg msvc toolset is x64 host and target only, not '%~1' 1>&2`r`nexit /b 1`r`n" +
    ":ok`r`ncall `"%~dp0..\..\..\setup_x64.bat`"`r`n")

[ordered]@{
    visual_studio = $release
    toolset       = $toolset
    cl_package    = $clPkgVer
    redist        = $redist
    windows_sdk   = $sdk
    licenses      = $licenses
    lock_sha256   = (Get-FileHash -LiteralPath $lockPath -Algorithm SHA256).Hash.ToLowerInvariant()
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $rootFull 'msvc.json') -Encoding utf8NoBOM

# ── Smoke test: compile, link and run with nothing but this toolset ──
# PATH is cut down to Windows itself so a Visual Studio on the machine cannot
# stand in for a missing piece.
$saved = @{ PATH = $env:PATH; INCLUDE = $env:INCLUDE; LIB = $env:LIB; LIBPATH = $env:LIBPATH }
try {
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    & (Join-Path $rootFull 'setup_x64.ps1')
    $cl = (Get-Command cl.exe).Source
    if (-not $cl.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) { throw "msvc: cl.exe resolved to $cl" }
    $t = Join-Path $env:CVC_BUILD_DIR 'smoke'
    New-Item -ItemType Directory -Force -Path $t | Out-Null
    Push-Location $t
    try {
        Set-Content -LiteralPath 'hello.cpp' -Encoding ascii -Value @'
#include <windows.h>
#include <cstdio>
#include <string>
#include <vector>
int main() {
    std::vector<std::string> v{"cvcpkg", "msvc"};
    std::printf("%s %s %d %lu\n", v[0].c_str(), v[1].c_str(), _MSC_FULL_VER, GetCurrentProcessId());
    return 0;
}
'@
        Set-Content -LiteralPath 'hello.rc' -Encoding ascii -Value "STRINGTABLE`r`nBEGIN`r`n  1 `"cvcpkg msvc`"`r`nEND`r`n"
        & rc.exe /nologo /fo hello.res hello.rc
        if ($LASTEXITCODE -ne 0) { throw 'msvc: rc.exe failed' }
        foreach ($crt in 'MD', 'MT') {
            & cl.exe /nologo /EHsc /std:c++17 /W3 "/$crt" hello.cpp hello.res user32.lib "/Fe:hello_$crt.exe"
            if ($LASTEXITCODE -ne 0) { throw "msvc: cl /$crt failed" }
        }
        # The /MD program runs on the redist DLLs this recipe ships, not on
        # whatever VC++ runtime the machine has.
        Copy-Item (Join-Path $rootFull "VC\Redist\MSVC\$redist\x64\Microsoft.VC143.CRT\*.dll") -Destination $t
        foreach ($crt in 'MD', 'MT') {
            $out = & ".\hello_$crt.exe"
            if ($LASTEXITCODE -ne 0 -or "$out" -notmatch '^cvcpkg msvc 19') { throw "msvc: hello_$crt.exe printed '$out'" }
            Write-Host "msvc: smoke /$crt -> $out"
        }
    } finally {
        Pop-Location
    }
} finally {
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k], 'Process') }
}

Write-Host "msvc: MSVC $toolset (VS $release) and Windows SDK $sdk staged under $rootFull"
