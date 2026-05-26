# recipes/wasmer/build.ps1 — stage pre-built Wasmer C API on Windows.
$ErrorActionPreference = 'Stop'

$wasmerVer = '7.1.0'
$artifact  = 'wasmer-windows-amd64.tar.gz'
$url       = "https://github.com/wasmerio/wasmer/releases/download/v${wasmerVer}/${artifact}"
$dlPath    = Join-Path $env:CVC_BUILD_DIR $artifact
$extractDir = Join-Path $env:CVC_BUILD_DIR 'wasmer-extracted'

Write-Host "Downloading ${artifact}..."
Invoke-WebRequest -Uri $url -OutFile $dlPath -UseBasicParsing

# tar.gz — use tar on Windows 10+.
& tar xf $dlPath -C $env:CVC_BUILD_DIR
$src = $extractDir
if (-not (Test-Path $src)) { $src = $env:CVC_BUILD_DIR }

# Stage headers.
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\include" | Out-Null
Copy-Item "$src\include\*" "$env:CVC_INSTALL_DIR\include\" -Force -ErrorAction SilentlyContinue

# Stage libraries.
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\lib" | Out-Null
Copy-Item "$src\lib\*" "$env:CVC_INSTALL_DIR\lib\" -Force -ErrorAction SilentlyContinue

# Generate CMake config.
$cmakeDir = "$env:CVC_INSTALL_DIR\lib\cmake\wasmer"
New-Item -ItemType Directory -Force -Path $cmakeDir | Out-Null

@"
get_filename_component(_WASMER_PREFIX "`${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
add_library(wasmer::wasmer UNKNOWN IMPORTED)
find_library(_WASMER_LIB NAMES wasmer PATHS "`${_WASMER_PREFIX}/lib" NO_DEFAULT_PATH)
set_target_properties(wasmer::wasmer PROPERTIES
    IMPORTED_LOCATION "`${_WASMER_LIB}"
    INTERFACE_INCLUDE_DIRECTORIES "`${_WASMER_PREFIX}/include"
)
set(wasmer_FOUND TRUE)
unset(_WASMER_PREFIX)
unset(_WASMER_LIB)
"@ | Set-Content "$cmakeDir\wasmerConfig.cmake"

@"
set(PACKAGE_VERSION "$wasmerVer")
if("`${PACKAGE_FIND_VERSION}" VERSION_LESS_EQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_COMPATIBLE TRUE)
    if("`${PACKAGE_FIND_VERSION}" VERSION_EQUAL PACKAGE_VERSION)
        set(PACKAGE_VERSION_EXACT TRUE)
    endif()
endif()
"@ | Set-Content "$cmakeDir\wasmerConfigVersion.cmake"

Write-Host "Wasmer ${wasmerVer} C API staged to $env:CVC_INSTALL_DIR"
