# recipes/openssh-win/build.ps1 — build the Win32-OpenSSH port from source with
# MSVC, linking cvcpkg's own OpenSSL + zlib (from CVC_DEPS_PREFIX) in place of
# the vcpkg-fetched LibreSSL/zlib the upstream build normally uses.
#
# We deliberately bypass upstream's Start-OpenSSHBuild, which drives vcpkg to
# fetch LibreSSL + zlib + libfido2 + libcbor.  Instead we:
#   1. import the MSVC dev environment (env-windows.ps1);
#   2. generate config.h from the checked-in VS template (Start-OpenSSHBootstrap
#      normally does this);
#   3. stage our libcrypto.lib / zs.lib where the solution's paths.targets links
#      them (SSLLib=libcrypto.lib, ZLibName=zs.lib, OpenSSH-Lib-Path);
#   4. msbuild only the core binaries, skipping ssh-sk-helper — the sole
#      consumer of libfido2/libcbor (FIDO2 security keys), which we don't ship —
#      and the unit tests.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
# env-windows.ps1 imports the MSVC dev env (cl/link via vcvars64) and validates
# CVC_SOURCE_DIR / CVC_BUILD_DIR / CVC_INSTALL_DIR.
. "$scriptDir\..\_common\env-windows.ps1"
if (-not $env:CVC_DEPS_PREFIX) { throw 'CVC_DEPS_PREFIX must be set (openssl + zlib deps)' }

$src    = $env:CVC_SOURCE_DIR
$dst    = $env:CVC_INSTALL_DIR
$deps   = $env:CVC_DEPS_PREFIX
$winDir = Join-Path $src 'contrib\win32\openssh'
$sln    = Join-Path $winDir 'Win32-OpenSSH.sln'
if (-not (Test-Path $sln)) { throw "solution not found: $sln" }

# ── Locate MSBuild (vcvars64 sets up cl/link but not msbuild) ──────────
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { $vswhere = "$env:ProgramFiles\Microsoft Visual Studio\Installer\vswhere.exe" }
$msbuild = & $vswhere -latest -products * -requires 'Microsoft.Component.MSBuild' `
    -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
if (-not $msbuild) { throw 'MSBuild.exe not found via vswhere.' }
Write-Host "Using MSBuild: $msbuild"

# ── config.h — the solution's build generates config.h at the source ROOT from
#    the VS template; includes.h (also at the root) picks up that root copy, not
#    the one under contrib\win32\openssh.  config.h.vs is written for LibreSSL,
#    which exports EVP_CIPHER_CTX_get_iv / _set_iv; OpenSSL 3.x does not, so patch
#    the TEMPLATE to turn those HAVE_ flags off (activating the openbsd-compat
#    fallbacks, whose primitives — EVP_CIPHER_CTX_iv / _iv_length / _iv_noconst —
#    our OpenSSL does provide), then materialise config.h at both locations. ────
$configVs = Join-Path $winDir 'config.h.vs'
if (Test-Path $configVs) {
    $cfg = Get-Content -Raw $configVs
    $cfg = $cfg -replace '(?m)^#define HAVE_EVP_CIPHER_CTX_GET_IV 1\s*$',
        '/* HAVE_EVP_CIPHER_CTX_GET_IV off for OpenSSL 3.x -> openbsd-compat */'
    $cfg = $cfg -replace '(?m)^#define HAVE_EVP_CIPHER_CTX_SET_IV 1\s*$',
        "/* HAVE_EVP_CIPHER_CTX_SET_IV off for OpenSSL 3.x */`r`n#define HAVE_EVP_CIPHER_CTX_IV 1`r`n#define HAVE_EVP_CIPHER_CTX_IV_NOCONST 1"
    Set-Content -LiteralPath $configVs -Value $cfg -NoNewline
    Copy-Item $configVs (Join-Path $winDir 'config.h') -Force
    Copy-Item $configVs (Join-Path $src 'config.h') -Force
}

# ── Stage our OpenSSL + zlib import libs where the solution links them.
#    paths.targets: OpenSSH-Lib-Path = $(SolutionDir)lib\ ; the vcxproj search
#    dir is $(OpenSSH-Lib-Path)$(Platform)\$(Configuration); it links
#    SSLLib=libcrypto.lib and (Release) ZLibName=zs.lib. ──────────────────
$libDir = Join-Path $winDir 'lib\x64\Release'
New-Item -ItemType Directory -Force -Path $libDir | Out-Null

$crypto = Get-ChildItem "$deps\lib\libcrypto*.lib" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $crypto) { throw "cvcpkg openssl import lib not found under $deps\lib (libcrypto*.lib)" }
Copy-Item $crypto.FullName (Join-Path $libDir 'libcrypto.lib') -Force

$zlib = Get-ChildItem "$deps\lib\zlib.lib", "$deps\lib\zs.lib", "$deps\lib\zlib*.lib", "$deps\lib\z.lib" `
    -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $zlib) { throw "cvcpkg zlib import lib not found under $deps\lib" }
Copy-Item $zlib.FullName (Join-Path $libDir 'zs.lib') -Force

# Expose our headers/libs on the compiler/linker search env too.
$env:INCLUDE = "$deps\include;$env:INCLUDE"
$env:LIB     = "$libDir;$deps\lib;$env:LIB"

# MSBuild rebuilds the compiler's INCLUDE/LIB from the toolset per project, so
# the env vars above are not reliably honoured for headers.  A Directory.Build.props
# (auto-imported by every project in the solution) prepends our dep dirs to each
# project's AdditionalIncludeDirectories / AdditionalLibraryDirectories, which is
# how the upstream build normally surfaces its vcpkg_installed crypto.
$props = @"
<Project>
  <ItemDefinitionGroup>
    <ClCompile>
      <AdditionalIncludeDirectories>$deps\include;%(AdditionalIncludeDirectories)</AdditionalIncludeDirectories>
      <!-- The Win32-OpenSSH port targets LibreSSL / the OpenSSL 1.1 low-level
           API (EC_KEY_*, RSA_*, PEM_write_bio_*).  Building it against our
           OpenSSL 3.x turns those into C4996 deprecation errors even though the
           functions are still present.  Suppress the deprecation markers so the
           1.1-era API compiles; the symbols resolve against our libcrypto. -->
      <PreprocessorDefinitions>OPENSSL_SUPPRESS_DEPRECATED;%(PreprocessorDefinitions)</PreprocessorDefinitions>
    </ClCompile>
    <Link>
      <AdditionalLibraryDirectories>$libDir;$deps\lib;%(AdditionalLibraryDirectories)</AdditionalLibraryDirectories>
    </Link>
  </ItemDefinitionGroup>
</Project>
"@
Set-Content -LiteralPath (Join-Path $winDir 'Directory.Build.props') -Value $props -Encoding UTF8

# ── Build the core binaries only.  Named project targets pull in their lib
#    dependencies (libssh, openbsd_compat, posix_compat) automatically; we
#    omit ssh-sk-helper (libfido2/libcbor) and the unit tests. ────────────
# The core binaries live in the solution's "core" folder, so msbuild targets
# must be folder-qualified (core\ssh, not ssh).
$targets = @(
    'core\ssh', 'core\sshd', 'core\scp', 'core\sftp', 'core\sftp-server',
    'core\ssh-add', 'core\ssh-agent', 'core\ssh-keygen', 'core\ssh-keyscan',
    'core\ssh-shellhost', 'core\sshd-session', 'core\sshd-auth',
    'core\ssh-pkcs11-helper'
) -join ';'

$jobs = if ($env:CVC_JOBS) { $env:CVC_JOBS } else { [Environment]::ProcessorCount }
$common = @('/p:Configuration=Release', '/p:Platform=x64', "/m:$jobs", '/nologo', '/verbosity:minimal')

# The projects request Spectre-mitigated CRT libraries (matching upstream's
# hardened build).  Try that first; if the builder lacks them (MSB8040), fall
# back to an unmitigated build so the recipe still works on stock toolchains.
# Installing "MSVC v143 - VS 2022 C++ x64/x86 Spectre-mitigated libs" on the
# builder makes the hardened path succeed (see vm-provisioning docs).
& $msbuild $sln "/t:$targets" '/p:SpectreMitigation=Spectre' @common
if ($LASTEXITCODE -ne 0) {
    Write-Host "cvcpkg: Spectre-mitigated build failed (Spectre-mitigated CRT likely not installed); retrying without Spectre mitigation."
    & $msbuild $sln "/t:$targets" '/p:SpectreMitigation=false' @common
    if ($LASTEXITCODE -ne 0) { throw "msbuild failed with exit code $LASTEXITCODE" }
}

# ── Stage built binaries + our openssl/zlib runtime DLLs + sample config ──
$bin = Join-Path $src 'bin\x64\Release'
if (-not (Test-Path $bin)) { $bin = Join-Path $src 'bin\x64' }
if (-not (Test-Path $bin)) { throw "built binaries not found (looked in $src\bin\x64\Release and \bin\x64)" }
New-Item -ItemType Directory -Force -Path "$dst\bin", "$dst\etc\ssh" | Out-Null
Copy-Item "$bin\*.exe" "$dst\bin\" -Force
# Runtime crypto/zlib DLLs so the package is self-contained.
Copy-Item "$deps\bin\libcrypto*.dll" "$dst\bin\" -Force -ErrorAction SilentlyContinue
Copy-Item "$deps\bin\zlib*.dll", "$deps\bin\z.dll" "$dst\bin\" -Force -ErrorAction SilentlyContinue
$sample = Join-Path $winDir 'sshd_config_default'
if (Test-Path $sample) { Copy-Item $sample "$dst\etc\ssh\sshd_config.sample" -Force }

Write-Host "Win32-OpenSSH core binaries built from source and staged to $dst"
