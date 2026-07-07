# recipes/msys2/build.ps1 — install MSYS2 base + MinGW-w64 toolchain.
#
# Only base-devel (make, patch, diffutils, POSIX utilities) and the
# MinGW-w64 GCC toolchain are installed via pacman.  Autotools (m4,
# autoconf, automake, libtool), meson, ninja, and pkg-config are
# provided by their own cvcpkg recipes and must be declared as
# host_tools in the calling recipe.yaml — do NOT install them here.
#
# If MSYS2 is already installed at $CVC_DEPS_PREFIX\msys2 the
# installer is skipped and the existing installation is updated.
# A small version marker is written to cvcpkg-version.txt so
# downstream recipes can confirm the recipe ran.
#
# Get-CvcGitBash (env-windows.ps1) finds bash.exe by checking (1)
# CVC_MSYS2_DIR, (2) CVC_DEPS_PREFIX\msys2, (3) C:\msys64.
$ErrorActionPreference = 'Stop'

$msys2Ver     = '20250221'
$installerUrl = "https://github.com/msys2/msys2-installer/releases/download/$msys2Ver/msys2-x86_64-$msys2Ver.exe"
$msys2Root    = Join-Path $env:CVC_DEPS_PREFIX 'msys2'
$installer    = Join-Path $env:CVC_BUILD_DIR   "msys2-installer-$msys2Ver.exe"
$bash         = Join-Path $msys2Root 'usr\bin\bash.exe'

# ── 1. Install MSYS2 if not already present ──────────────────────────
if (-not (Test-Path $bash)) {
    Write-Host "Downloading MSYS2 $msys2Ver installer (~90 MB)..."
    Invoke-WebRequest -Uri $installerUrl -OutFile $installer -UseBasicParsing

    Write-Host "Installing MSYS2 to $msys2Root ..."
    # 'in' subcommand = non-interactive install; --root sets the target dir.
    $proc = Start-Process -FilePath $installer `
        -ArgumentList "in --confirm-command --accept-messages --root `"$msys2Root`"" `
        -Wait -PassThru -NoNewWindow
    if ($proc.ExitCode -ne 0) {
        throw "MSYS2 installer exited with code $($proc.ExitCode)"
    }
    Write-Host "MSYS2 base installation complete."
} else {
    Write-Host "MSYS2 already installed at $msys2Root, skipping installer."
}

# ── Helper: run a bash command and throw on failure ──────────────────
function Invoke-Bash([string]$Cmd) {
    Write-Host "bash -lc `"$Cmd`""
    $env:MSYSTEM = 'MINGW64'
    & $bash -lc $Cmd
    if ($LASTEXITCODE -ne 0) { throw "bash command failed: $Cmd" }
}

# ── 2. System update ──────────────────────────────────────────────────
# Two-pass update is required: the first pass may replace pacman itself.
Write-Host "Updating MSYS2 package database (pass 1)..."
Invoke-Bash 'pacman --noconfirm -Syuu 2>&1 | head -50 || true'

Write-Host "Updating MSYS2 package database (pass 2)..."
Invoke-Bash 'pacman --noconfirm -Syuu 2>&1 | head -50 || true'

# ── 3. Install base development packages ─────────────────────────────
# Only the MSYS2 base environment and the MinGW-w64 GCC toolchain.
# Do NOT add meson, ninja, pkg-config, or autotools here — those are
# managed by their own cvcpkg recipes.
$pkgs = @(
    'base-devel',                    # make, patch, diffutils, findutils, tar, ...
    'mingw-w64-x86_64-toolchain'     # gcc, g++, ld, ar, ranlib, strip, ...
)
Write-Host "Installing MSYS2 base packages: $($pkgs -join ', ')..."
Invoke-Bash ("pacman --noconfirm -S --needed " + ($pkgs -join ' '))

# ── 4. Write version marker ───────────────────────────────────────────
$marker = Join-Path $msys2Root 'cvcpkg-version.txt'
Set-Content -Path $marker -Value $msys2Ver -NoNewline

Write-Host "MSYS2 $msys2Ver ready at $msys2Root"
& $bash --version | Select-Object -First 1
