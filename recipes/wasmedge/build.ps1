# recipes/wasmedge/build.ps1 — build WasmEdge from source on Windows.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

# In-place fix-up of cmake/Helper.cmake: relax -Werror -> -Wno-error so
# the build doesn't fail on warnings emitted by newer compilers.  Done
# here instead of via a patch file because Strawberry Perl's bundled
# patch.exe 2.5.9 (year 2003) asserts out on any modern unified diff.
$helperCmake = Join-Path (Get-Location) 'cmake\Helper.cmake'
if (Test-Path $helperCmake) {
    (Get-Content $helperCmake -Raw) -replace '(?m)^(\s*)-Werror\s*$', '$1-Wno-error' |
        Set-Content -Encoding ASCII $helperCmake
}

# LLVM + LLD for AOT/JIT (-DWASMEDGE_USE_LLVM=ON) come from the cvcpkg llvm18
# recipe, a windows build dependency of this one (see recipe.yaml).  Look for
# its CMake package configs in the dependency prefixes; nothing on the host
# (C:\Program Files\LLVM, Chocolatey, ...) is consulted.  The llvm18 bundle is
# LLVM's static component libraries (.lib), so wasmedge.dll links them in and
# does not need llvm18 at run time.
function Find-CvcLlvmRoot {
    foreach ($root in @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ } | Select-Object -Unique) {
        if ((Test-Path -LiteralPath (Join-Path $root 'lib\cmake\llvm\LLVMConfig.cmake')) -and
            (Test-Path -LiteralPath (Join-Path $root 'lib\cmake\lld\LLDConfig.cmake'))) {
            return $root
        }
    }
    return $null
}

if ($env:CVC_LINK -eq 'static') {
    # Static builds stay interpreter-only.  WasmEdge 0.17.0 bundles LLVM into
    # its static archive with Unix-only logic (cmake/Helper.cmake names
    # liblld*.a and lib<component>.a and needs zstd), and a static cvcpkg
    # build uses the static CRT (/MT) while the llvm18 bundle is /MD.
    Write-Host 'wasmedge: static link -- building without LLVM (no AOT/JIT)'
    $llvmArgs = @('-DWASMEDGE_USE_LLVM=OFF')
} else {
    $llvmRoot = Find-CvcLlvmRoot
    if (-not $llvmRoot) {
        throw ("wasmedge: LLVM/LLD CMake configs (lib\cmake\llvm, lib\cmake\lld) not found " +
               "under CVC_BUILD_PREFIX='$env:CVC_BUILD_PREFIX' or CVC_DEPS_PREFIX='$env:CVC_DEPS_PREFIX'" +
               " -- is the llvm18 build dependency staged?")
    }
    $llvmRootFwd = $llvmRoot -replace '\\', '/'
    Write-Host "wasmedge: LLVM AOT enabled, cvcpkg llvm18 at $llvmRoot"
    $llvmArgs = @(
        '-DWASMEDGE_USE_LLVM=ON',
        "-DLLVM_DIR=$llvmRootFwd/lib/cmake/llvm",
        "-DLLD_DIR=$llvmRootFwd/lib/cmake/lld"
    )
}

# Link-aware build args.  WasmEdge 0.17.0's split shared library on
# Windows is broken upstream: with BUILD_SHARED_LIBS=ON every component
# (wasmedgeLoaderFileMgr, ...) becomes its own DLL, and those reference
# internal symbols (WasmEdge::MMap, WasmEdge::Allocator) that aren't
# exported, producing LNK2019.  For shared builds we therefore build the
# components (and the fetched fmt/spdlog) as static libraries and ship
# the one monolithic wasmedge.dll that WASMEDGE_BUILD_SHARED_LIB links
# from them -- upstream's own Windows configuration.  The later
# -DBUILD_SHARED_LIBS=OFF overrides the ON that Invoke-CvcCMakeBuild
# passes for a shared build.  For static builds we just disable the
# shared lib.
$linkArgs = if ($env:CVC_LINK -eq 'static') {
    @(
        '-DWASMEDGE_BUILD_SHARED_LIB=OFF',
        '-DWASMEDGE_BUILD_STATIC_LIB=ON'
    )
} else {
    @(
        '-DBUILD_SHARED_LIBS=OFF',
        '-DWASMEDGE_BUILD_SHARED_LIB=ON',
        '-DWASMEDGE_BUILD_STATIC_LIB=OFF'
    )
}

$commonArgs = @(
    '-DWASMEDGE_BUILD_TESTS=OFF',
    '-DWASMEDGE_BUILD_TOOLS=ON',
    '-DWASMEDGE_BUILD_PLUGINS=OFF'
) + $linkArgs + $llvmArgs

# For static builds we need a post-configure patch: WasmEdge's
# combine-static-libraries custom command creates one working
# directory per input library named 'objs/<target>'.  When it
# encounters CMake ALIAS targets like fmt::fmt the ':' in the
# directory name is rejected by Windows filesystem ("Error creating
# directory objs/fmt::fmt").  Rewrite the generated .bat files to
# use '__' in place of '::' before invoking the build step.
if ($env:CVC_LINK -eq 'static') {
    # Configure only.
    $configureArgs = @(
        '-G', 'Ninja',
        '-S', $env:CVC_SOURCE_DIR,
        '-B', $env:CVC_BUILD_DIR,
        "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR",
        "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
        "-DBUILD_SHARED_LIBS=$buildSharedLibs",
        "-DCMAKE_MSVC_RUNTIME_LIBRARY=$msvcRuntime",
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"
    ) + $commonArgs
    & cmake @configureArgs
    if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

    # Rewrite '::' → '__' in any combine-static-libs helper batch file.
    Get-ChildItem -Path $env:CVC_BUILD_DIR -Recurse -Filter '*.bat' |
        Where-Object { (Get-Content $_.FullName -Raw) -match '::' } |
        ForEach-Object {
            $fixed = (Get-Content $_.FullName -Raw) -replace '::', '__'
            Set-Content -Encoding ASCII -Path $_.FullName -Value $fixed
            Write-Host "cvcpkg: sanitised colons in $($_.FullName)"
        }

    & cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
    if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

    & cmake --install $env:CVC_BUILD_DIR
    if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }
} else {
    Invoke-CvcCMakeBuild $commonArgs
}

# -- Checks on a shared build: the DLL, its imports, and AOT end to end -------
if ($env:CVC_LINK -ne 'static') {
    $bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
    $dll = Join-Path $bin 'wasmedge.dll'
    $wasmedge  = Join-Path $bin 'wasmedge.exe'
    $wasmedgec = Join-Path $bin 'wasmedgec.exe'   # only built with WASMEDGE_USE_LLVM=ON
    foreach ($required in @($dll, (Join-Path $env:CVC_INSTALL_DIR 'lib\wasmedge.lib'), $wasmedge, $wasmedgec)) {
        if (-not (Test-Path -LiteralPath $required)) { throw "wasmedge: $required was not installed" }
    }

    # llvm18 is a build-only dependency: LLVM must be linked INTO wasmedge.dll,
    # never imported from an LLVM DLL that the bundle's consumers would not have.
    $imports = & dumpbin /nologo /dependents $dll
    if ($LASTEXITCODE -ne 0) { throw "wasmedge: dumpbin /dependents $dll failed" }
    $imports = @($imports | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '(?i)^[^\s\\/:]+\.dll$' })
    Write-Host "wasmedge: wasmedge.dll imports: $($imports -join ', ')"
    $llvmDlls = @($imports | Where-Object { $_ -match '(?i)^(LLVM|libclang|LTO|Remarks)' })
    if ($llvmDlls.Count -gt 0) { throw "wasmedge: wasmedge.dll imports [$($llvmDlls -join ', ')] from llvm18" }

    # AOT smoke test: compile a tiny module with wasmedgec (LLVM codegen, then
    # LLD's COFF linker into a native .dll) and run the result in AOT mode.  The
    # module exports add(i32, i32) -> i32.  Runs under CVC_BUILD_DIR, with the
    # dependency prefixes on PATH for DLLs wasmedge.dll imports (zlib1.dll).
    $smoke = Join-Path $env:CVC_BUILD_DIR 'cvc-aot-smoke'
    New-Item -ItemType Directory -Force -Path $smoke | Out-Null
    $wasm = Join-Path $smoke 'add.wasm'
    $aot  = Join-Path $smoke 'add-aot.dll'
    [IO.File]::WriteAllBytes($wasm, [byte[]](
        0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00,               # magic, version 1
        0x01, 0x07, 0x01, 0x60, 0x02, 0x7f, 0x7f, 0x01, 0x7f,         # type: (i32 i32) -> i32
        0x03, 0x02, 0x01, 0x00,                                       # func 0 has type 0
        0x07, 0x07, 0x01, 0x03, 0x61, 0x64, 0x64, 0x00, 0x00,         # export "add" = func 0
        0x0a, 0x09, 0x01, 0x07, 0x00, 0x20, 0x00, 0x20, 0x01, 0x6a, 0x0b  # local.get 0/1, i32.add
    ))
    $origPath = $env:PATH
    $depBins = @(@($env:CVC_DEPS_PREFIX, $env:CVC_BUILD_PREFIX) | Where-Object { $_ } |
        Select-Object -Unique | ForEach-Object { Join-Path $_ 'bin' })
    $env:PATH = (@($bin) + $depBins + @($env:PATH)) -join ';'
    try {
        & $wasmedgec $wasm $aot
        if ($LASTEXITCODE -ne 0) { throw "wasmedge: wasmedgec failed to AOT-compile $wasm" }
        if (-not (Test-Path -LiteralPath $aot)) { throw "wasmedge: wasmedgec produced no $aot" }
        $out = & $wasmedge --run-mode=aot --reactor $aot add 2 3
        if ($LASTEXITCODE -ne 0) { throw "wasmedge: running the AOT module failed" }
        $out = ($out | Out-String).Trim()
        if ($out -ne '5') { throw "wasmedge: AOT add(2, 3) returned '$out', expected 5" }
        Write-Host 'wasmedge: AOT smoke test passed (wasmedgec -> native .dll, add(2, 3) = 5)'
    } finally {
        $env:PATH = $origPath
    }
}
