# recipes/msys2/build.ps1 — stage the MSYS2 bootstrap for Windows autotools builds.
#
# This is the MSYS shell + MSYS C compiler that Windows autotools builds run
# under. It is deliberately MINIMAL: the sha256-pinned MSYS2 `base` archive plus
# the MSYS-subsystem gcc (to build the MSYS host tools), perl (autoconf and
# automake are Perl) and diffutils (cmp/diff, used by configure scripts).
#
# Everything else comes from its own cvcpkg recipe, never from pacman: make, m4,
# autoconf, automake, libtool, and the MinGW-w64 gcc that builds the actual
# Windows libraries (mingw-w64-gcc). Keeping make and the autotools out of here
# is what makes the recipes' tool probes meaningful -- a pacman `base-devel`
# would satisfy them with ambient copies.
#
# The tree is staged at %CVC_INSTALL_DIR%\msys2, so a build dep on this recipe
# lands at <build prefix>\msys2, which env-windows.ps1's Get-CvcGitBash prefers
# over any ambient C:\msys64 (a GitHub-hosted image's is bare: no gcc, no make).
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'   # Invoke-WebRequest's progress bar is pathologically slow

if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }
if (-not $env:CVC_BUILD_DIR)   { throw 'CVC_BUILD_DIR must be set' }

$ver      = '20260927'
$tag      = '2026-09-27'
$expected = 'ad336cccfda47758b5e15cda993fbba421115cb0b126697daef1ee4dfe37209f'
$url      = "https://github.com/msys2/msys2-installer/releases/download/$tag/msys2-base-x86_64-$ver.sfx.exe"

# MSYS-subsystem packages layered on `base`. Nothing that cvcpkg ships itself.
$packages = @('gcc', 'perl', 'diffutils')

# ── 1. Fetch + verify the pinned base archive ────────────────────────
New-Item -ItemType Directory -Force -Path $env:CVC_BUILD_DIR, $env:CVC_INSTALL_DIR | Out-Null
$sfx = Join-Path $env:CVC_BUILD_DIR "msys2-base-x86_64-$ver.sfx.exe"
Write-Host "msys2: downloading $url"
Invoke-WebRequest -Uri $url -OutFile $sfx -UseBasicParsing
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $sfx).Hash.ToLower()
if ($actual -ne $expected) { throw "msys2: sha256 mismatch for $url`n  got      $actual`n  expected $expected" }

# ── 2. Extract (7-Zip SFX; it unpacks a single msys64\ root) ─────────
$extract = Join-Path $env:CVC_BUILD_DIR 'extract'
& $sfx -y "-o$extract" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "msys2: extracting $sfx failed (exit $LASTEXITCODE)" }
$root = Join-Path $env:CVC_INSTALL_DIR 'msys2'
if (Test-Path -LiteralPath $root) { Remove-Item -Recurse -Force -LiteralPath $root }
Move-Item -LiteralPath (Join-Path $extract 'msys64') -Destination $root
$bash = Join-Path $root 'usr\bin\bash.exe'
if (-not (Test-Path -LiteralPath $bash)) { throw "msys2: no bash.exe at $bash after extraction" }

# ── 3. Drive it in its own MSYS subsystem, isolated from the host PATH ──
# minimal PATH = this root's /usr/bin + System32: no MSVC, no Strawberry Perl, no
# Git-for-Windows MSYS runtime (a second msys-2.0.dll) leaking in.
$env:MSYSTEM         = 'MSYS'
$env:MSYS2_PATH_TYPE = 'minimal'
$env:CHERE_INVOKING  = '1'
Remove-Item Env:MSYS_NO_PATHCONV -ErrorAction SilentlyContinue

function Invoke-MsysBash {
    param([string]$Cmd, [int]$Attempts = 1, [switch]$AllowFailure)
    for ($i = 1; $i -le $Attempts; $i++) {
        Write-Host "msys2: bash -lc `"$Cmd`"$(if ($Attempts -gt 1) { " (attempt $i/$Attempts)" })"
        & $bash -lc $Cmd
        $rc = $LASTEXITCODE
        if ($rc -eq 0) { return }
    }
    if (-not $AllowFailure) { throw "msys2: command failed (exit $rc): $Cmd" }
    Write-Host "msys2: (tolerated exit $rc)"
}

# First login runs MSYS2's post-install (home dir, pacman keyring init).
Invoke-MsysBash 'true'
# pacman's free-space check cannot resolve mount points on some Windows volumes
# (setup-msys2 disables it for the same reason).
Invoke-MsysBash "sed -i 's/^CheckSpace/#CheckSpace/' /etc/pacman.conf"

# ── 4. Bring the base current, then add the MSYS toolchain ───────────
# Pass 1 may replace the MSYS2 runtime itself, after which pacman deliberately
# kills every process of this root (this bash included) -- so its exit status is
# not meaningful. Pass 2 must then succeed.
Invoke-MsysBash "pacman -Syuu --noconfirm --overwrite '*'" -Attempts 2 -AllowFailure
Invoke-MsysBash "pacman -Syuu --noconfirm --overwrite '*'" -Attempts 3
Invoke-MsysBash ("pacman -S --needed --noconfirm " + ($packages -join ' ')) -Attempts 3

# Leave nothing running out of the tree and nothing transient in it: stop the
# keyring's gpg-agent, then drop its sockets and the downloaded package cache.
Invoke-MsysBash 'gpgconf --homedir /etc/pacman.d/gnupg --kill all' -AllowFailure
Invoke-MsysBash 'rm -f /etc/pacman.d/gnupg/S.* && rm -rf /var/cache/pacman/pkg/*'

# ── 5. Prove the toolchain this recipe exists to provide ─────────────
# (No double quotes inside these command strings, so they reach bash intact
# whatever pwsh's native-argument-passing mode is.)
Invoke-MsysBash "gcc --version | head -n1 && printf 'int main(void){return 0;}\n' > /tmp/cvc-cc-probe.c && gcc -std=gnu17 -o /tmp/cvc-cc-probe /tmp/cvc-cc-probe.c && /tmp/cvc-cc-probe && rm -f /tmp/cvc-cc-probe*"
Invoke-MsysBash "perl -Mstrict -Mwarnings -e 'print qq(perl ), `$^V, qq(\n)' && cmp --version | head -n1"
Invoke-MsysBash "command -v make >/dev/null 2>&1 && echo 'msys2: note: bootstrap ships a make' || echo 'msys2: no make in the bootstrap (expected: make comes from the cvcpkg make recipe)'"

# ── 6. Make the tree survive the bundle round trip ───────────────────
# The Windows bundle is a zip, and two things in a live MSYS2 root do not come
# back out of one. msys2 +cvc.2 shipped both, and the INSTALLED copy broke:
#
#  * Empty directories are dropped. /tmp and /dev (with shm, mqueue) vanished,
#    so bash warned "could not find /tmp, please create!" (here-documents need
#    it), and 01-devices.post, unable to mkdir /dev/shm under a /dev that no
#    longer existed, printed "Creating /dev/shm directory failed." on STDOUT.
#    env-windows.ps1's tool probes read that stdout as their answer, so they
#    reported gcc missing from a tree that contains it:
#      MSYS build tools not found on PATH (bash: ...\prefix\msys2\usr\bin\bash.exe): gcc
#    A placeholder file in every empty directory keeps each one in the archive.
#  * /etc/mtab is a Cygwin symlink: a "!<symlink>" cookie file carrying the
#    SYSTEM attribute. A login re-creates it (03-mtab.post), and extracting the
#    bundle over that again is refused, because CreateFile(CREATE_ALWAYS) is
#    denied on an existing SYSTEM file:
#      PermissionError: [Errno 13] Permission denied: '...\prefix\msys2\etc\mtab'
#    That is every install-deps after the first in a shared prefix. Ship no
#    mtab at all; 03-mtab.post makes the link on first login.
$mtab = Join-Path $root 'etc\mtab'
if (Test-Path -LiteralPath $mtab) { Remove-Item -LiteralPath $mtab -Force }
$tmpDir = Join-Path $root 'tmp'
foreach ($d in $tmpDir, (Join-Path $root 'dev\shm'), (Join-Path $root 'dev\mqueue')) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
Get-ChildItem -LiteralPath $tmpDir -Force | Remove-Item -Recurse -Force
$emptyDirs = @(Get-ChildItem -LiteralPath $root -Recurse -Directory -Force |
    Where-Object { -not (Get-ChildItem -LiteralPath $_.FullName -Force | Select-Object -First 1) })
foreach ($d in $emptyDirs) {
    New-Item -ItemType File -Force -Path (Join-Path $d.FullName '.cvcpkg-keep') | Out-Null
}
Write-Host "msys2: kept $($emptyDirs.Count) empty directories (placeholder .cvcpkg-keep); /etc/mtab left to first login"

Set-Content -LiteralPath (Join-Path $root 'cvcpkg-version.txt') -Value $ver -NoNewline -Encoding ascii
Write-Host "msys2: bootstrap $ver ready at $root (MSYS packages: $($packages -join ', '))"
