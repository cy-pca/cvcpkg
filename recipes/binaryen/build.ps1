# recipes/binaryen/build.ps1 — build binaryen 129 on Windows with MSVC:
# lib/binaryen.lib (static: binaryen builds no DLL with MSVC), its headers, and
# wasm-opt, wasm-as and wasm-dis, as build.sh stages them.
#
# The CRT follows CVC_LINK as every cvcpkg MSVC build's does (/MD for shared,
# /MT for static): binaryen's CMakeLists.txt sets /MT itself for every non-Debug
# build, as a plain variable that a -D can't override, so that line is taken
# out of the source tree first.  libcvc links /MD, and an /MT library in an /MD
# program fails the link (LNK2038).
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$lists = Join-Path $env:CVC_SOURCE_DIR 'CMakeLists.txt'
$text = Get-Content -Raw $lists
$pinned = 'set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")'
if ($text.Contains($pinned)) {
    $text = $text.Replace($pinned, '# (cvcpkg: the CRT is CMAKE_MSVC_RUNTIME_LIBRARY from the command line)')
    [System.IO.File]::WriteAllText($lists, $text)
}

$origPath = $env:PATH
$env:PATH = ($env:PATH -split ';' |
    Where-Object { $_ -notmatch '(?i)\\msys64\\' -and $_ -notmatch '(?i)\\msys32\\' }) -join ';'
try {
    & cmake -G Ninja `
        -S $env:CVC_SOURCE_DIR `
        -B $env:CVC_BUILD_DIR `
        "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR" `
        "-DCMAKE_BUILD_TYPE=$cmakeBuildType" `
        "-DCMAKE_MSVC_RUNTIME_LIBRARY=$msvcRuntime" `
        '-DCMAKE_POLICY_VERSION_MINIMUM=3.5' `
        '-DCMAKE_C_COMPILER=cl' `
        '-DCMAKE_CXX_COMPILER=cl' `
        '-DBUILD_STATIC_LIB=ON' `
        '-DBUILD_TESTS=OFF' `
        '-DBUILD_FUZZTEST=OFF' `
        '-DBUILD_TOOLS=ON' `
        '-DBYN_ENABLE_LTO=OFF' `
        '-DENABLE_WERROR=OFF' `
        '-DINSTALL_LIBS=ON'
    if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

    # At most 4 at once: binaryen's passes are heavy translation units for cl, and a builder
    # with many cores and less memory ran out partway (dev job 25587 stopped at 86 of 279).
    $jobs = [Math]::Min([int]$env:CVC_JOBS, 4)
    & cmake --build $env:CVC_BUILD_DIR -j $jobs --target binaryen wasm-opt wasm-as wasm-dis
    if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }
} finally {
    $env:PATH = $origPath
}

$lib = Join-Path $env:CVC_INSTALL_DIR 'lib'
$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
$inc = Join-Path $env:CVC_INSTALL_DIR 'include'
$cxxInc = Join-Path $inc 'binaryen'
New-Item -ItemType Directory -Force -Path $lib, $bin, $cxxInc | Out-Null

Copy-Item (Join-Path $env:CVC_BUILD_DIR 'lib\binaryen.lib') $lib -Force
foreach ($tool in 'wasm-opt', 'wasm-as', 'wasm-dis') {
    Copy-Item (Join-Path $env:CVC_BUILD_DIR "bin\$tool.exe") $bin -Force
}

$src = Join-Path $env:CVC_SOURCE_DIR 'src'
Copy-Item (Join-Path $src 'binaryen-c.h'), (Join-Path $src 'wasm-delegations.def') $inc -Force
Get-ChildItem $src -Recurse -File -Include '*.h', '*.def', '*.inc' | ForEach-Object {
    $rel = $_.FullName.Substring($src.Length).TrimStart('\')
    $dst = Join-Path $cxxInc $rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
    Copy-Item $_.FullName $dst -Force
}
Copy-Item (Join-Path $env:CVC_SOURCE_DIR 'third_party\FP16\include\*') $cxxInc -Recurse -Force

$cmakeDir = Join-Path $lib 'cmake\binaryen'
New-Item -ItemType Directory -Force -Path $cmakeDir | Out-Null
@'
# binaryen::binaryen, the static binaryen.lib.  Its include directories are
# include (binaryen-c.h, the C API) and include/binaryen (src/'s tree, the C++
# API: "wasm.h", "pass.h", "parser/wat-parser.h", ...).  BUILD_STATIC_LIBRARY
# keeps binaryen-c.h's declarations from dllexport.
get_filename_component(_BINARYEN_PREFIX "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
if(NOT TARGET binaryen::binaryen)
  add_library(binaryen::binaryen STATIC IMPORTED)
  set_target_properties(binaryen::binaryen PROPERTIES
    IMPORTED_LOCATION "${_BINARYEN_PREFIX}/lib/binaryen.lib"
    IMPORTED_LINK_INTERFACE_LANGUAGES CXX
    INTERFACE_INCLUDE_DIRECTORIES "${_BINARYEN_PREFIX}/include;${_BINARYEN_PREFIX}/include/binaryen"
    INTERFACE_COMPILE_DEFINITIONS BUILD_STATIC_LIBRARY
    INTERFACE_COMPILE_FEATURES cxx_std_20)
endif()
set(binaryen_FOUND TRUE)
set(binaryen_VERSION 129)
unset(_BINARYEN_PREFIX)
'@ | Set-Content -Encoding ascii (Join-Path $cmakeDir 'binaryenConfig.cmake')
@'
set(PACKAGE_VERSION "129")
if(PACKAGE_FIND_VERSION VERSION_LESS_EQUAL PACKAGE_VERSION)
  set(PACKAGE_VERSION_COMPATIBLE TRUE)
  if(PACKAGE_FIND_VERSION VERSION_EQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_EXACT TRUE)
  endif()
endif()
'@ | Set-Content -Encoding ascii (Join-Path $cmakeDir 'binaryenConfigVersion.cmake')

Write-Host "binaryen 129 staged to $env:CVC_INSTALL_DIR"
