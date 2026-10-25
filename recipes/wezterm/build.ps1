# recipes/wezterm/build.ps1 — build WezTerm from source on Windows (MSVC).
#
# cargo builds wezterm, wezterm-gui, wezterm-mux-server and strip-ansi-escapes
# from the wezterm-src source package (CVC_SOURCE_DIR, with patches\ applied)
# with the cvcpkg rust toolchain. WezTerm's .cargo\config links the MSVC CRT
# statically (+crt-static), and freetype, harfbuzz, cairo, libssh, libssh2,
# zlib and Lua are compiled in from bundled sources. OpenSSL is the one shared
# library: the prefix's (a runtime dependency), not the copy WezTerm would
# otherwise compile from openssl-src.
#
# The tree is laid out flat in bin\ the way upstream's portable zip is:
# wezterm.exe starts wezterm-gui.exe from its own directory, and wezterm-gui.exe
# loads conpty.dll + OpenConsole.exe, ANGLE's libEGL.dll / libGLESv2.dll and
# mesa\opengl32.dll from there too. ANGLE and Mesa are prebuilt DLLs WezTerm
# keeps in its source tree (assets\windows); the ConPTY pair comes from
# Microsoft's package instead of assets\windows\conhost (see below).
#
# HERMETICITY: as on linux, Cargo.lock pins every crate but cargo FETCHES them;
# CVC_CARGO_HOME + CVC_CARGO_OFFLINE=1 build from a pre-seeded cargo home.
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

$env:CARGO_HOME = if ($env:CVC_CARGO_HOME) { $env:CVC_CARGO_HOME }
                  else { Join-Path $env:CVC_BUILD_DIR 'cargo-home' }
$env:RUSTUP_HOME = Join-Path $env:CVC_BUILD_DIR 'rustup-home'
Remove-Item Env:\RUSTUP_TOOLCHAIN -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $env:CARGO_HOME, $env:RUSTUP_HOME | Out-Null
# In the source tree, where wezterm-gui's build.rs assumes it is: it copies
# assets\windows\{conhost,angle,mesa} into <source>\target\<profile> without
# looking at CARGO_TARGET_DIR. (CVC_SOURCE_DIR is this build's own copy.)
$env:CARGO_TARGET_DIR = Join-Path $src 'target'
if ($env:CVC_CARGO_OFFLINE) { $env:CARGO_NET_OFFLINE = 'true' }
else {
    $env:CARGO_NET_OFFLINE = 'false'
    Write-Output 'wezterm: NOTE - cargo will FETCH the crates Cargo.lock pins (see the header).'
}

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
    & $cargo build --release --locked --jobs $env:CVC_JOBS `
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

# ConPTY: Microsoft.Windows.Console.ConPTY 1.25 from nuget.org, not the
# February 2024 build in assets\windows\conhost, which drops the APC and DCS
# sequences a program writes -- no kitty graphics and no Sixel from any Windows
# program reached WezTerm through it. (WezTerm's main branch bundles a 2025
# ConPTY for the same reason.) Keep $conptyVer/$conptySha in step with
# recipe.yaml.
$conptyVer = '1.25.260930003'
$conptySha = '02b07b349af66d801159bdf9e440d4a1ce78bb951f37fc8609731665afdae7ee'
$conptyUrl = "https://api.nuget.org/v3-flatcontainer/microsoft.windows.console.conpty/$conptyVer/microsoft.windows.console.conpty.$conptyVer.nupkg"
# A .nupkg is a zip; Expand-Archive only takes the .zip extension.
$nupkg = Join-Path $env:CVC_BUILD_DIR "conpty.$conptyVer.zip"
Write-Host "Downloading $conptyUrl ..."
Invoke-WebRequest -Uri $conptyUrl -OutFile $nupkg -UseBasicParsing
$actual = (Get-FileHash $nupkg -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $conptySha) { throw "sha256 mismatch for ${conptyUrl}: got $actual expected $conptySha" }
$conpty = Join-Path $env:CVC_BUILD_DIR 'conpty'
Expand-Archive -Path $nupkg -DestinationPath $conpty -Force
# The x64 pair goes flat beside wezterm-gui.exe; this conpty.dll starts the
# OpenConsole.exe next to it.
Copy-Item -Force (Join-Path $conpty 'runtimes\win-x64\native\conpty.dll') $bin
Copy-Item -Force (Join-Path $conpty 'build\native\runtimes\x64\OpenConsole.exe') $bin

# Licences: WezTerm's, the embedded fonts', ANGLE's (from the source tree) and
# ConPTY's (beside this script; Microsoft's package carries only an SPDX id).
$doc = Join-Path $env:CVC_INSTALL_DIR 'share\doc\wezterm'
New-Item -ItemType Directory -Force -Path $doc | Out-Null
foreach ($f in 'LICENSE.md', 'assets\fonts\LICENSE_OFL.txt', 'assets\fonts\LICENSE_POWERLINE_EXTRA.txt',
               'licenses\ANGLE.md') {
    Copy-Item -Force (Join-Path $src $f) $doc
}
Copy-Item -Force (Join-Path $PSScriptRoot 'licenses\ConPTY-LICENSE.txt') $doc
# WezTerm's note on where its opengl32.dll came from and Mesa's licence terms.
Copy-Item -Force (Join-Path $src 'assets\windows\mesa\README.md') (Join-Path $doc 'Mesa-README.md')

& (Join-Path $bin 'wezterm.exe') --version
if ($LASTEXITCODE -ne 0) { throw "staged wezterm.exe failed to run" }
