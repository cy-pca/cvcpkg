#!/usr/bin/env pwsh
# recipes/cuda-toolkit/build.ps1 — lay out the NVIDIA CUDA toolkit (nvcc, cudart,
# CCCL, the MSBuild integration) from NVIDIA's own redist archives on Windows.
#
# Same contract as build.sh: `redistributable: false`, so this runs only on the
# machine installing the recipe; every archive comes from
# developer.download.nvidia.com and is checked against redist.lock before it is
# unpacked; NVIDIA's files are copied, never edited.
#
# Layout — the tree the NVIDIA installer writes to
# C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v<X.Y>, in its own
# subdirectory of the prefix:
#
#   cuda\bin\nvcc.exe (+ ptxas, fatbinary, nvlink, cudafe++, nvcc.profile, cudart64_12.dll)
#   cuda\nvvm\{bin\cicc.exe,bin\nvvm64_*.dll,libdevice}
#   cuda\include\                      cudart, CCCL, crt, profiler headers
#   cuda\lib\x64\                      cudart.lib, cudart_static.lib, cudadevrt.lib, cuda.lib
#   cuda\lib\cmake\                    CCCL package configs
#   cuda\extras\visual_studio_integration\MSBuildExtensions\
#   cuda\version.json, cuda\EULA.txt
#
# Consumers point CMake at it with -DCUDAToolkit_ROOT=<prefix>\cuda and
# -DCMAKE_CUDA_COMPILER=<prefix>\cuda\bin\nvcc.exe (Ninja), or set CUDA_PATH to
# <prefix>\cuda.  There is no <prefix>\bin\nvcc shim as on Linux: nvcc locates
# its toolkit from its own directory, and Windows has no exec-style wrapper
# CMake would accept as the CUDA compiler.  The Visual Studio generator also
# needs the MSBuildExtensions files copied into the VS BuildCustomizations
# directory (or -T cuda=<prefix>\cuda), which this recipe does not do: it never
# writes outside its prefix.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }
if (-not $env:CVC_BUILD_DIR)   { throw 'CVC_BUILD_DIR must be set' }

$lock = Join-Path $PSScriptRoot 'redist.lock'
$base = 'https://developer.download.nvidia.com/compute/cuda/redist'
$key  = 'windows-x86_64'
if ($env:PROCESSOR_ARCHITECTURE -and $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw "cuda-toolkit: no NVIDIA redist entries for $($env:PROCESSOR_ARCHITECTURE) in redist.lock"
}

$lines = Get-Content -LiteralPath $lock | Where-Object { $_ -and -not $_.StartsWith('#') }
$release = ($lines | Where-Object { $_ -match '^release\s' } | ForEach-Object { ($_ -split '\s+')[1] }) |
    Select-Object -First 1
if (-not $release) { throw 'cuda-toolkit: redist.lock has no release line' }

# Cache downloads by sha256 where cvcpkg keeps its own source cache
# (CVCPKG_SOURCE_CACHE_DIR; set it empty to disable).
if (Test-Path Env:CVCPKG_SOURCE_CACHE_DIR) {
    $cache = $env:CVCPKG_SOURCE_CACHE_DIR
} elseif ($env:XDG_CACHE_HOME) {
    $cache = Join-Path $env:XDG_CACHE_HOME 'cvcpkg/sources'
} else {
    $cache = Join-Path $HOME '.cache/cvcpkg/sources'
}

$root    = Join-Path $env:CVC_INSTALL_DIR 'cuda'
$extract = Join-Path $env:CVC_BUILD_DIR 'redist'
if (Test-Path -LiteralPath $extract) { Remove-Item -LiteralPath $extract -Recurse -Force }
New-Item -ItemType Directory -Force -Path $extract, $root | Out-Null

# Merge the directory $src into $dst, failing if two archives disagree about a
# file rather than keeping whichever was unpacked last.
function Merge-Into([string]$src, [string]$dst) {
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    $srcFull = (Resolve-Path -LiteralPath $src).Path
    foreach ($f in Get-ChildItem -LiteralPath $srcFull -Recurse -File) {
        $rel = $f.FullName.Substring($srcFull.Length).TrimStart('\', '/')
        $target = Join-Path $dst $rel
        if (Test-Path -LiteralPath $target) {
            if ((Get-FileHash -LiteralPath $target).Hash -ne (Get-FileHash -LiteralPath $f.FullName).Hash) {
                throw "cuda-toolkit: $rel differs between components"
            }
            continue
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $f.FullName -Destination $target
    }
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$components = [ordered]@{ cuda = [ordered]@{ name = 'CUDA SDK'; version = $release } }

foreach ($line in $lines) {
    $cols = $line -split '\s+', 6
    if ($cols[0] -ne $key) { continue }
    $comp, $ver, $sha, $path, $name = $cols[1], $cols[2], $cols[3], $cols[4], $cols[5]
    $file = Split-Path -Leaf $path

    $archive = if ($cache) { Join-Path $cache "$sha.zip" } else { '' }
    if ($archive -and (Test-Path -LiteralPath $archive) -and
        (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -eq $sha) {
        Write-Host "cuda-toolkit: $comp $ver (cached)"
    } else {
        $archive = Join-Path $env:CVC_BUILD_DIR $file
        Write-Host "cuda-toolkit: downloading $base/$path"
        Invoke-WebRequest -Uri "$base/$path" -OutFile $archive -UseBasicParsing -MaximumRetryCount 3
        $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $sha) {
            throw "cuda-toolkit: sha256 mismatch for ${file}: expected $sha, got $actual"
        }
        if ($cache) {
            New-Item -ItemType Directory -Force -Path $cache | Out-Null
            Copy-Item -LiteralPath $archive -Destination (Join-Path $cache "$sha.zip") -Force
        }
    }

    [System.IO.Compression.ZipFile]::ExtractToDirectory($archive, $extract)
    $top = Join-Path $extract ([IO.Path]::GetFileNameWithoutExtension($file))
    if (-not (Test-Path -LiteralPath $top)) { throw "cuda-toolkit: $file did not unpack to $(Split-Path -Leaf $top)\" }
    foreach ($entry in Get-ChildItem -LiteralPath $top) {
        switch ($entry.Name) {
            { $_ -in 'bin', 'include', 'lib', 'nvvm' } { Merge-Into $entry.FullName (Join-Path $root $entry.Name) }
            'visual_studio_integration' {
                Merge-Into $entry.FullName (Join-Path $root 'extras/visual_studio_integration')
            }
            # The same EULA ships in every archive.
            'LICENSE' { Copy-Item -LiteralPath $entry.FullName -Destination (Join-Path $root 'EULA.txt') -Force }
            default { throw "cuda-toolkit: unexpected $($entry.Name) in $file; teach build.ps1 where it goes" }
        }
    }
    $components[$comp] = [ordered]@{ name = $name; version = $ver }
}

# NVIDIA's installer format; FindCUDAToolkit reads it.
$components | ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath (Join-Path $root 'version.json') -Encoding utf8NoBOM

foreach ($f in 'bin/nvcc.exe', 'bin/nvcc.profile', 'bin/ptxas.exe', 'bin/fatbinary.exe', 'bin/nvlink.exe',
               'bin/cudart64_12.dll', 'nvvm/bin/cicc.exe', 'nvvm/libdevice/libdevice.10.bc',
               'include/cuda_runtime.h', 'include/cuda.h', 'include/crt/host_config.h',
               'include/thrust/version.h', 'include/nv/target',
               'lib/x64/cudart_static.lib', 'lib/x64/cudadevrt.lib', 'lib/x64/cuda.lib',
               "extras/visual_studio_integration/MSBuildExtensions/CUDA $($release -replace '\.\d+$', '').props") {
    if (-not (Test-Path -LiteralPath (Join-Path $root $f))) {
        throw "cuda-toolkit: $f missing from the staged toolkit"
    }
}
if ($IsWindows -or -not (Test-Path Variable:IsWindows)) {
    & (Join-Path $root 'bin/nvcc.exe') --version
    if ($LASTEXITCODE -ne 0) { throw 'cuda-toolkit: nvcc --version failed' }
}
Write-Host "cuda-toolkit: CUDA $release staged under $root"
