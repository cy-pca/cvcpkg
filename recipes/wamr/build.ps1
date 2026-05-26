# recipes/wamr/build.ps1 — build WAMR from source on Windows.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

$wamrVer = '2.4.4'

# WAMR's product-mini has platform-specific CMakeLists.
$wamrCmakeDir = $env:CVC_SOURCE_DIR
$winPlatform  = Join-Path $env:CVC_SOURCE_DIR 'product-mini\platforms\windows'
if (Test-Path "$winPlatform\CMakeLists.txt") {
    $wamrCmakeDir = $winPlatform
}

& cmake -G Ninja `
    -S $wamrCmakeDir `
    -B $env:CVC_BUILD_DIR `
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR" `
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType" `
    '-DWAMR_BUILD_INTERP=1' `
    '-DWAMR_BUILD_FAST_INTERP=1' `
    '-DWAMR_BUILD_AOT=1' `
    '-DWAMR_BUILD_LIBC_BUILTIN=1' `
    '-DWAMR_BUILD_LIBC_WASI=1' `
    '-DWAMR_BUILD_LIB_WASI_THREADS=0'
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

& cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

# Stage manually — WAMR may not have a proper install target.
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\lib" | Out-Null
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\include" | Out-Null
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\bin" | Out-Null

# Copy libraries.
Get-ChildItem $env:CVC_BUILD_DIR -Recurse -Include 'vmlib*','iwasm*' -File |
    Where-Object { $_.Extension -in '.lib','.dll','.a' } |
    Copy-Item -Destination "$env:CVC_INSTALL_DIR\lib" -Force

# Copy iwasm CLI if built.
Get-ChildItem $env:CVC_BUILD_DIR -Recurse -Include 'iwasm.exe' -File |
    Copy-Item -Destination "$env:CVC_INSTALL_DIR\bin" -Force -ErrorAction SilentlyContinue

# Copy public headers.
$headerDir = Join-Path $env:CVC_SOURCE_DIR 'core\iwasm\include'
if (Test-Path $headerDir) {
    Copy-Item "$headerDir\*.h" "$env:CVC_INSTALL_DIR\include\" -Force
}

# Generate CMake config.
$cmakeDir = "$env:CVC_INSTALL_DIR\lib\cmake\iwasm"
New-Item -ItemType Directory -Force -Path $cmakeDir | Out-Null

@"
get_filename_component(_IWASM_PREFIX "`${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
add_library(iwasm::iwasm UNKNOWN IMPORTED)
find_library(_IWASM_LIB NAMES vmlib iwasm PATHS "`${_IWASM_PREFIX}/lib" NO_DEFAULT_PATH)
set_target_properties(iwasm::iwasm PROPERTIES
    IMPORTED_LOCATION "`${_IWASM_LIB}"
    INTERFACE_INCLUDE_DIRECTORIES "`${_IWASM_PREFIX}/include"
)
set(iwasm_FOUND TRUE)
unset(_IWASM_PREFIX)
unset(_IWASM_LIB)
"@ | Set-Content "$cmakeDir\iwasmConfig.cmake"

@"
set(PACKAGE_VERSION "$wamrVer")
if("`${PACKAGE_FIND_VERSION}" VERSION_LESS_EQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_COMPATIBLE TRUE)
    if("`${PACKAGE_FIND_VERSION}" VERSION_EQUAL PACKAGE_VERSION)
        set(PACKAGE_VERSION_EXACT TRUE)
    endif()
endif()
"@ | Set-Content "$cmakeDir\iwasmConfigVersion.cmake"

Write-Host "WAMR ${wamrVer} staged to $env:CVC_INSTALL_DIR"
