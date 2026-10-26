# recipes/wezterm/build.ps1 — build WezTerm from source on Windows (MSVC).
#
# cargo builds wezterm, wezterm-gui, wezterm-mux-server and strip-ansi-escapes
# from the wezterm-src source package (CVC_SOURCE_DIR, with patches\ applied)
# with the cvcpkg rust toolchain and crates, offline. WezTerm's
# .cargo\config links the MSVC CRT statically (+crt-static), and freetype,
# harfbuzz, cairo, libssh, libssh2,
# zlib and Lua are compiled in from bundled sources. OpenSSL is the one shared
# library: the prefix's (a runtime dependency), not the copy WezTerm would
# otherwise compile from openssl-src.
#
# The tree is laid out flat in bin\ the way upstream's portable zip is:
# wezterm.exe starts wezterm-gui.exe from its own directory, and wezterm-gui.exe
# loads conpty.dll + OpenConsole.exe, ANGLE's libEGL.dll / libGLESv2.dll and
# mesa\opengl32.dll from there too. ANGLE and Mesa are prebuilt DLLs WezTerm
# keeps in its source tree (assets\windows). conpty.dll + OpenConsole.exe are
# not WezTerm's: they come from the `conpty` runtime dependency, which installs
# a newer ConPTY into the same bin\ (see recipe.yaml).
#
# Nothing is downloaded: every crate is a cvcpkg package (the wezterm-crates
# set), every library comes from the prefix.
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\env-windows.ps1"    # cl.exe/link.exe/rc.exe on PATH, CVC_JOBS

$deps = $env:CVC_DEPS_PREFIX
$bld = if ($env:CVC_BUILD_PREFIX) { $env:CVC_BUILD_PREFIX } else { $deps }
$src = $env:CVC_SOURCE_DIR
$env:PATH = "$bld\bin;$deps\bin;$env:PATH"

# The toolchain must be the one cvcpkg installed, never a builder's rustup.
function Resolve-CvcPrefixTool([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "wezterm: $Name not on PATH -- is the 'rust' build dep in the closure?" }
    $path = $cmd.Source
    if (-not ($path.StartsWith($bld, 'OrdinalIgnoreCase') -or $path.StartsWith($deps, 'OrdinalIgnoreCase'))) {
        throw "wezterm: refusing $Name at $path -- outside the cvcpkg prefixes ($bld, $deps)"
    }
    return $path
}
$cargo = Resolve-CvcPrefixTool 'cargo'
$env:CARGO = $cargo
$env:RUSTC = Resolve-CvcPrefixTool 'rustc'
& $cargo --version
& $env:RUSTC --version

$env:CARGO_HOME = Join-Path $env:CVC_BUILD_DIR 'cargo-home'
$env:RUSTUP_HOME = Join-Path $env:CVC_BUILD_DIR 'rustup-home'
Remove-Item Env:\RUSTUP_TOOLCHAIN -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $env:CARGO_HOME, $env:RUSTUP_HOME | Out-Null
# In the source tree, where wezterm-gui's build.rs assumes it is: it copies
# assets\windows\{conhost,angle,mesa} into <source>\target\<profile> without
# looking at CARGO_TARGET_DIR. (CVC_SOURCE_DIR is this build's own copy.)
$env:CARGO_TARGET_DIR = Join-Path $src 'target'
$env:CARGO_NET_OFFLINE = 'true'

# Crates: each is a cvcpkg package staged at share\cargo\crates\ beside the
# wezterm-crates set, whose cargo-config.toml.in replaces crates.io and the one
# git dependency with that directory (docs/rust-crates.md). --offline makes a
# crate missing from it an error rather than a download.
$cratesSet = $null
foreach ($root in @($bld, $deps)) {
    $candidate = Join-Path $root 'share\cargo\sets\wezterm-crates'
    if (Test-Path -LiteralPath (Join-Path $candidate 'cargo-config.toml.in')) {
        $cratesSet = $candidate
        $cratesDir = Join-Path $root 'share\cargo\crates'
        break
    }
}
if (-not $cratesSet) { throw "wezterm: no wezterm-crates set in $bld or $deps -- is it in the closure?" }
# TOML string: forward slashes, which cargo accepts on Windows, need no escaping.
$cfg = (Get-Content -Raw (Join-Path $cratesSet 'cargo-config.toml.in')).Replace('@CRATES_DIR@', ($cratesDir -replace '\\', '/'))
Set-Content -LiteralPath (Join-Path $env:CARGO_HOME 'config.toml') -Value $cfg -Encoding utf8NoBOM
Write-Output "wezterm: crates from $cratesDir ($((Get-ChildItem -Directory $cratesDir).Count) packages)"

# OpenSSL from the prefix. WezTerm's async_ossl turns on openssl's `vendored`
# feature on Windows; OPENSSL_NO_VENDOR overrides it, so openssl-sys links the
# prefix's import libraries and the prefix's DLLs load beside wezterm*.exe.
$env:OPENSSL_DIR = $deps
$env:OPENSSL_NO_VENDOR = '1'
if (-not (Test-Path -LiteralPath (Join-Path $deps 'include\openssl\ssl.h'))) {
    throw "wezterm: $deps\include\openssl\ssl.h missing -- is the openssl dep in the closure?"
}

# advapi32.lib on every link. Cargo.lock pins git2 0.16 / libgit2-sys 0.14.2,
# whose build script never asks for advapi32 (the libgit2-sys 0.16.2 fix is
# not reachable from git2 0.16): it relied on Rust's std linking it, which
# the std of Rust 1.90 no longer does. With Rust 1.90 the
# link of wezterm-version's build script fails on OpenProcessToken,
# CopySid, CryptAcquireContextA and the rest (LNK2019). RUSTFLAGS replaces
# .cargo\config's target rustflags rather than adding to them, so it repeats
# +crt-static.
$env:RUSTFLAGS = '-C target-feature=+crt-static -C link-arg=advapi32.lib'

# distro-defaults: check_for_updates defaults to false -- a pinned package
# should not tell its user to go and download another version.
Push-Location $src
try {
    & $cargo build --release --locked --offline --jobs $env:CVC_JOBS `
        -p wezterm -p wezterm-gui -p wezterm-mux-server -p strip-ansi-escapes `
        --features wezterm-gui/distro-defaults
    if ($LASTEXITCODE -ne 0) { throw "wezterm: cargo build failed ($LASTEXITCODE)" }
} finally { Pop-Location }

$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
New-Item -ItemType Directory -Force -Path (Join-Path $bin 'mesa') | Out-Null
$release = Join-Path $env:CARGO_TARGET_DIR 'release'
foreach ($exe in 'wezterm', 'wezterm-gui', 'wezterm-mux-server', 'strip-ansi-escapes') {
    Copy-Item -Force (Join-Path $release "$exe.exe") $bin
}
Copy-Item -Force (Join-Path $src 'assets\windows\angle\libEGL.dll') $bin
Copy-Item -Force (Join-Path $src 'assets\windows\angle\libGLESv2.dll') $bin
Copy-Item -Force (Join-Path $src 'assets\windows\mesa\opengl32.dll') (Join-Path $bin 'mesa')

# Licences: WezTerm's, the embedded fonts' and ANGLE's, from the source tree.
# (ConPTY's ships with the conpty package.)
$doc = Join-Path $env:CVC_INSTALL_DIR 'share\doc\wezterm'
New-Item -ItemType Directory -Force -Path $doc | Out-Null
foreach ($f in 'LICENSE.md', 'assets\fonts\LICENSE_OFL.txt', 'assets\fonts\LICENSE_POWERLINE_EXTRA.txt',
               'licenses\ANGLE.md') {
    Copy-Item -Force (Join-Path $src $f) $doc
}
# WezTerm's note on where its opengl32.dll came from and Mesa's licence terms.
Copy-Item -Force (Join-Path $src 'assets\windows\mesa\README.md') (Join-Path $doc 'Mesa-README.md')

& (Join-Path $bin 'wezterm.exe') --version
if ($LASTEXITCODE -ne 0) { throw "staged wezterm.exe failed to run" }
