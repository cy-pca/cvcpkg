# recipes/_common/stage-source.ps1 — source-recipe staging helper (Windows).
#
# PowerShell counterpart to stage-source.sh.  A source recipe declares
# `platform: any` and stages its (patched) source tree into the canonical
# layout so downstream recipes can compile it:
#
#     $env:CVC_INSTALL_DIR\src\$env:CVC_COMPONENT\
#
# Usage in a source recipe's build.ps1:
#
#     . "$PSScriptRoot\..\_common\stage-source.ps1"
#     Invoke-CvcStageSource                      # stage the whole tree
#     Invoke-CvcStageSource include,src          # or only these subpaths
#
# Downstream recipes locate a staged source with:
#     $src = Get-CvcSourceDirOf 'mysource'

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-CvcStageSource {
    param([string[]] $Items = @())

    foreach ($v in 'CVC_SOURCE_DIR', 'CVC_INSTALL_DIR', 'CVC_COMPONENT') {
        if (-not (Test-Path "env:$v")) { throw "$v must be set" }
    }

    $dest = Join-Path (Join-Path $env:CVC_INSTALL_DIR 'src') $env:CVC_COMPONENT
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null

    if ($Items.Count -eq 0) {
        Copy-Item -Recurse -Force (Join-Path $env:CVC_SOURCE_DIR '*') $dest
    }
    else {
        foreach ($item in $Items) {
            $srcPath = Join-Path $env:CVC_SOURCE_DIR $item
            if (Test-Path $srcPath) {
                $target = Join-Path $dest $item
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
                Copy-Item -Recurse -Force $srcPath $target
            }
            else {
                Write-Warning "Invoke-CvcStageSource: '$item' not found under CVC_SOURCE_DIR"
            }
        }
    }

    Write-Output "staged source for $env:CVC_COMPONENT -> $dest"
}

function Get-CvcSourceDirOf {
    param([Parameter(Mandatory = $true)][string] $Name)
    if (-not (Test-Path 'env:CVC_DEPS_PREFIX')) { throw 'CVC_DEPS_PREFIX must be set' }
    return (Join-Path (Join-Path $env:CVC_DEPS_PREFIX 'src') $Name)
}
