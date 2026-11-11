# recipes/mesa/build.ps1 — Mesa 25.3 software OpenGL (llvmpipe + softpipe)
# behind WGL on Windows, built with MSVC + Meson.
#
# What lands in the bundle is what an application needs to swap the Windows
# GDI OpenGL 1.1 path for llvmpipe: bin\opengl32.dll (a ~150 KB WGL/ICD loader
# stub) and bin\libgallium_wgl.dll (the gallium drivers, with LLVM linked in).
# Both must sit in the application's own directory, which Windows searches
# before System32, so the override applies to that process only.  This is the
# same pair mesa-dist-win's perappdeploy.cmd copies for a desktop-GL app.
#
# LLVM: MSVC cannot build the libLLVM DLL, so cvcpkg's llvm20 ships its
# component .lib files and libgallium_wgl.dll links them statically.  That
# makes llvm20 a build-only dependency here (it is a runtime one on the Unix
# platforms, where libgallium links libLLVM.so).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# Find a file in the dependency prefixes: the runtime closure first, then the
# build-only closure.  Never fall back to a host copy.
function Find-MesaDep([string]$Rel) {
    foreach ($root in @($env:CVC_DEPS_PREFIX, $env:CVC_BUILD_PREFIX) | Where-Object { $_ }) {
        $p = Join-Path $root $Rel
        if (Test-Path -LiteralPath $p) { return (Resolve-Path -LiteralPath $p).Path }
    }
    throw "cvcpkg: $Rel not found in CVC_DEPS_PREFIX or CVC_BUILD_PREFIX"
}

$flex       = Find-MesaDep 'bin\flex.exe'
$bison      = Find-MesaDep 'bin\bison.exe'
$llvmConfig = Find-MesaDep 'bin\llvm-config.exe'

# cvcpkg's flex and bison on Windows are winflexbison's win_flex and win_bison,
# installed as flex.exe and bison.exe.  Mesa's meson.build only adds
# --wincompat (generated scanners include <io.h> instead of <unistd.h>, which
# MSVC lacks) when it finds a program named win_flex, so name them that way.
# llvm-config is pinned the same way so a host LLVM on PATH cannot be picked.
# Meson native files want forward slashes.
$native = "$env:CVC_BUILD_DIR.native.ini"
$fwd = { param($p) $p -replace '\\', '/' }
@"
[binaries]
win_flex = '$(& $fwd $flex)'
win_bison = '$(& $fwd $bison)'
llvm-config = '$(& $fwd $llvmConfig)'
"@ | Set-Content -LiteralPath $native -Encoding ascii

# The code generators run on the interpreter meson runs on (find_installation
# ('python3') with no path).  meson.cmd runs the build prefix's python.exe,
# which is cvcpkg python311, with mako, markupsafe, pyyaml and packaging in its
# Lib\site-packages from their cp311 recipes.
#
# Off here, unlike the Unix builds: GLX, EGL, GBM, GLES (Windows uses WGL
# desktop GL only), expat (drirc's XML config is not built for Windows) and
# zstd (cvcpkg llvm20 is built without it on Windows; Mesa's shader cache
# falls back to zlib).
Invoke-CvcMesonBuild @(
    "--native-file=$native",
    '-Db_ndebug=true',
    '-Dcpp_rtti=false',
    '-Dplatforms=windows',
    '-Dgallium-drivers=llvmpipe,softpipe',
    '-Dvulkan-drivers=',
    '-Dvulkan-layers=',
    '-Dglx=disabled',
    '-Degl=disabled',
    '-Dgbm=disabled',
    '-Dgles1=disabled',
    '-Dgles2=disabled',
    '-Dopengl=true',
    '-Dllvm=enabled',
    '-Dshared-llvm=disabled',
    '-Dgallium-va=disabled',
    '-Dgallium-rusticl=false',
    '-Dvideo-codecs=',
    '-Dvalgrind=disabled',
    '-Dlibunwind=disabled',
    '-Dlmsensors=disabled',
    '-Dtools=',
    '-Dbuild-tests=false',
    '-Dzlib=enabled',
    '-Dzstd=disabled',
    '-Dexpat=disabled'
)

# Fail here, not in a consumer, if either half of the pair is missing: the
# stub alone loads and then cannot create a context.
foreach ($f in @('opengl32.dll', 'libgallium_wgl.dll')) {
    $p = Join-Path $env:CVC_INSTALL_DIR "bin\$f"
    if (-not (Test-Path -LiteralPath $p)) { throw "mesa: bin\$f was not installed" }
    Write-Host ("mesa: bin\{0} ({1:N0} bytes)" -f $f, (Get-Item -LiteralPath $p).Length)
}
