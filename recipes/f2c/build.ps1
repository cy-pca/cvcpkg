# recipes/f2c/build.ps1 — build the netlib f2c translator on Windows.
#
# Upstream ships `makefile.vc` for MSVC nmake.  The build produces a
# single `f2c.exe` in the source tree; we copy that plus the shared
# `f2c.h` header into the install prefix.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# nmake writes .obj files next to sources, so mirror the tree into the
# (empty) build dir to keep the recipe cache pristine.
Copy-Item -Recurse -Force "$env:CVC_SOURCE_DIR\*" $env:CVC_BUILD_DIR
Push-Location $env:CVC_BUILD_DIR
try {
    Copy-Item -Force makefile.vc makefile

    # makefile.vc omits the tokdefs.h rule that makefile.u provides via
    # `grep -n . <tokens | sed ...`. Generate it here so nmake can find
    # it as an input to lex.obj / proc.obj.
    $lineNo = 0
    $defs = foreach ($line in Get-Content tokens) {
        $lineNo++
        if ($line.Trim().Length -gt 0) { "#define $line $lineNo" }
    }
    Set-Content -Path tokdefs.h -Value $defs -Encoding ascii

    Write-Host "cvcpkg: f2c -> nmake -f makefile.vc"
    & nmake /nologo -f makefile f2c.exe
    if ($LASTEXITCODE -ne 0) { throw "nmake failed" }

    $installBin = Join-Path $env:CVC_INSTALL_DIR 'bin'
    $installInc = Join-Path $env:CVC_INSTALL_DIR 'include'
    foreach ($d in @($installBin, $installInc)) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
    }
    Copy-Item -Force 'f2c.exe' $installBin
    Copy-Item -Force 'f2c.h'   $installInc
} finally {
    Pop-Location
}
