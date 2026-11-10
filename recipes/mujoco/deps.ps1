# recipes/mujoco/deps.ps1 — fill MuJoCo's dependency cache from deps.lock
# (Windows twin of deps.sh; dot-sourced by build.ps1 and build-wasm.ps1).
#
# Invoke-MjFetchDeps reads deps.lock beside this file and, for every line,
# downloads the archive, refuses it unless its sha256 matches, and stores it as
# <cache>\<library-name>-<git-commit>.tar.gz — the name upstream's
# cmake/FindOrFetch.cmake looks for under MUJOCO_CMAKE_DEP_CACHE.
# MarchingCubeCpp is also extracted (upstream fetches it with a bare
# FetchContent_Declare that only honours FETCHCONTENT_SOURCE_DIR_MARCHINGCUBECPP).
# It returns @{ Cache = ...; MarchingCubeCpp = ... } with forward-slash paths
# (CMake treats backslashes in -D values as escapes).
#
# Assert-MjNoGitFetch fails the build when CMake fell back to a network clone
# anyway; Install-MjLicenses copies MuJoCo's LICENSE plus every dependency's
# licence text.

$script:MjDepsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
# Windows' own bsdtar, by full path: Git for Windows puts GNU tar on PATH, and
# GNU tar reads "C:\..." as host "C" (a remote archive) and fails.
$script:MjTar = Join-Path $env:SystemRoot 'System32\tar.exe'
$script:MjCurl = Join-Path $env:SystemRoot 'System32\curl.exe'

function Read-MjDepsLock {
    param([string]$Lock = (Join-Path $script:MjDepsDir 'deps.lock'))
    foreach ($line in Get-Content -LiteralPath $Lock) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith('#')) { continue }
        $f = $t -split '\s+'
        if ($f.Count -ne 4) { throw "deps.lock: malformed line: $t" }
        [pscustomobject]@{ Name = $f[0]; Commit = $f[1]; Sha256 = $f[2].ToLower(); Url = $f[3] }
    }
}

function Invoke-MjFetchDeps {
    param(
        [string]$Lock = (Join-Path $script:MjDepsDir 'deps.lock'),
        [string]$Cache = (Join-Path $env:CVC_BUILD_DIR 'mujoco-dep-cache')
    )
    New-Item -ItemType Directory -Force -Path $Cache | Out-Null
    $mcDir = ''
    foreach ($d in (Read-MjDepsLock -Lock $Lock)) {
        $dest = Join-Path $Cache "$($d.Name)-$($d.Commit).tar.gz"
        $ok = (Test-Path -LiteralPath $dest) -and
              ((Get-FileHash -Algorithm SHA256 -LiteralPath $dest).Hash.ToLower() -eq $d.Sha256)
        if (-not $ok) {
            Write-Host "mujoco: fetching $($d.Name) @ $($d.Commit)"
            $part = "$dest.part"
            # curl, not Invoke-WebRequest: gitlab.com (Eigen) answers PowerShell's
            # browser-like User-Agent with a Cloudflare challenge page.
            & $script:MjCurl -fsSL --retry 5 --retry-delay 3 -o $part $d.Url
            if ($LASTEXITCODE -ne 0) { throw "mujoco: download failed for $($d.Url) (curl exit $LASTEXITCODE)" }
            $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $part).Hash.ToLower()
            if ($actual -ne $d.Sha256) {
                Remove-Item -Force -LiteralPath $part
                throw "mujoco: sha256 mismatch for $($d.Url)`n  expected $($d.Sha256)`n  got      $actual"
            }
            Move-Item -Force -LiteralPath $part -Destination $dest
        }
        if ($d.Name -eq 'marchingcubecpp') {
            $mcDir = Join-Path $Cache 'src\marchingcubecpp'
            if (Test-Path -LiteralPath $mcDir) { Remove-Item -Recurse -Force -LiteralPath $mcDir }
            New-Item -ItemType Directory -Force -Path $mcDir | Out-Null
            & $script:MjTar -xzf $dest -C $mcDir --strip-components=1
            if ($LASTEXITCODE -ne 0) { throw "mujoco: could not extract $dest" }
        }
    }
    @{
        Cache           = ($Cache -replace '\\', '/')
        MarchingCubeCpp = ($mcDir -replace '\\', '/')
    }
}

# Install-MjTsc: put a TypeScript compiler on PATH for the wasm link.  See
# mj_provide_tsc in deps.sh: upstream links with emscripten's --emit-tsd, which
# runs `tsc`; the emsdk bundle has no TypeScript (an emscripten devDependency),
# and emscripten falls back to `tsc` on PATH.  On Windows that is a tsc.cmd
# launcher running the pinned tarball's bin/tsc with the emsdk's node.  Call it
# after env-wasm.ps1, which sets EMSDK_NODE.
$script:MjTsUrl = 'https://registry.npmjs.org/typescript/-/typescript-5.9.3.tgz'
$script:MjTsSha256 = '10e108c9cf7d5f2879053dff18515fb405abf2ccef63eaaf017d9c571687a1d3'
function Install-MjTsc {
    $dir = Join-Path $env:CVC_BUILD_DIR 'mujoco-tools\typescript'
    $bin = Join-Path $dir 'bin'
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    $tgz = Join-Path $dir 'typescript-5.9.3.tgz'
    & $script:MjCurl -fsSL --retry 5 --retry-delay 3 -o $tgz $script:MjTsUrl
    if ($LASTEXITCODE -ne 0) { throw "mujoco: download failed for $script:MjTsUrl" }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $tgz).Hash.ToLower()
    if ($actual -ne $script:MjTsSha256) { throw "mujoco: sha256 mismatch for $script:MjTsUrl (got $actual)" }
    $pkg = Join-Path $dir 'package'
    if (Test-Path -LiteralPath $pkg) { Remove-Item -Recurse -Force -LiteralPath $pkg }
    & $script:MjTar -xzf $tgz -C $dir
    if ($LASTEXITCODE -ne 0) { throw "mujoco: could not extract $tgz" }
    $node = if ($env:EMSDK_NODE) { $env:EMSDK_NODE } else { 'node' }
    $cmd = "@`"$node`" `"$(Join-Path $pkg 'bin\tsc')`" %*`r`n"
    [System.IO.File]::WriteAllText((Join-Path $bin 'tsc.cmd'), $cmd, (New-Object System.Text.ASCIIEncoding))
    $env:PATH = "$bin;$env:PATH"
    Write-Host "mujoco: tsc $(& (Join-Path $bin 'tsc.cmd') --version) for --emit-tsd"
}

# Upstream prints "Using FetchContent for <lib>: <git url>" when no cached
# tarball was found and it is about to clone.
function Assert-MjNoGitFetch {
    param([string]$Log)
    $hits = Select-String -LiteralPath $Log -SimpleMatch 'mujoco::FindOrFetch: Using FetchContent for'
    if ($hits) {
        $hits | ForEach-Object { Write-Host $_.Line }
        throw 'mujoco: CMake cloned a dependency that deps.lock does not pin'
    }
}

function Install-MjLicenses {
    param(
        [string]$Destination,
        [string]$Cache,
        [string]$Lock = (Join-Path $script:MjDepsDir 'deps.lock')
    )
    $tp = Join-Path $Destination 'third_party'
    New-Item -ItemType Directory -Force -Path $tp | Out-Null
    Copy-Item -Force -LiteralPath (Join-Path $env:CVC_SOURCE_DIR 'LICENSE') -Destination (Join-Path $Destination 'LICENSE')
    $tmp = Join-Path $env:CVC_BUILD_DIR 'mujoco-license-scratch'
    foreach ($d in (Read-MjDepsLock -Lock $Lock)) {
        if (Test-Path -LiteralPath $tmp) { Remove-Item -Recurse -Force -LiteralPath $tmp }
        New-Item -ItemType Directory -Force -Path $tmp | Out-Null
        & $script:MjTar -xzf (Join-Path $Cache "$($d.Name)-$($d.Commit).tar.gz") -C $tmp --strip-components=1
        if ($LASTEXITCODE -ne 0) { throw "mujoco: could not extract $($d.Name)" }
        $found = @(Get-ChildItem -LiteralPath $tmp -File | Where-Object {
            $_.Name -match '^(LICEN[CS]E|COPYING)' -or $_.Name -match '-LICENSE$'
        })
        if (-not $found -and $d.Name -eq 'marchingcubecpp') {
            # Public domain, declared in its README.
            $found = @(Get-Item -LiteralPath (Join-Path $tmp 'README.md'))
        }
        if (-not $found) { throw "mujoco: no licence text found for $($d.Name)" }
        foreach ($f in $found) {
            Copy-Item -Force -LiteralPath $f.FullName -Destination (Join-Path $tp "$($d.Name)-$($f.Name)")
        }
    }
    Remove-Item -Recurse -Force -LiteralPath $tmp
}
