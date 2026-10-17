# recipes/pegtl/check-install.ps1 — the post-install checks build.sh runs, for
# build.ps1 and build-wasm.ps1.  Dot-sourced; see build.sh for why the exported
# target must carry no link libraries or compile definitions.

function Test-PegtlInstall {
    $version = Join-Path $env:CVC_INSTALL_DIR 'include\tao\pegtl\version.hpp'
    if (-not (Select-String -Quiet -SimpleMatch -LiteralPath $version -Pattern "#define TAO_PEGTL_VERSION `"$env:CVC_VERSION`"")) {
        throw "pegtl: installed tao/pegtl/version.hpp is not $env:CVC_VERSION"
    }

    $config = Join-Path $env:CVC_INSTALL_DIR 'share\pegtl\cmake\pegtl-config.cmake'
    if (-not (Test-Path -LiteralPath $config)) { throw "pegtl: $config was not installed" }
    $fallback = Select-String -LiteralPath $config -Pattern 'INTERFACE_(LINK_LIBRARIES|COMPILE_DEFINITIONS)'
    if ($fallback) {
        $fallback | ForEach-Object { Write-Host $_.Line }
        throw 'pegtl: taocpp::pegtl picked up a filesystem fallback'
    }
}
