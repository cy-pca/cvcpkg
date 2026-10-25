# recipes/pynacl-cp311/build.ps1 — build pynacl 1.6.2 FROM SOURCE on Windows.
#
# Hand-written (it was generated; recipe.yaml no longer carries the generator
# marker, so a regeneration keeps it).  PyNaCl's setup builds its bundled
# libsodium with ./configure + make unless SODIUM_INSTALL=system, and a
# Windows builder has neither (job 23107: "The 'make' utility is missing from
# PATH").  The bundled libsodium ships an MSVC project, so build that with
# msbuild as a static library and hand it to PyNaCl's cffi build as the
# "system" libsodium.  It is linked statically, as the bundled build is on
# Linux and macOS, so there is no libsodium DLL to ship.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"   # vcvars64 (cl, msbuild) on PATH

$py = Get-CvcPythonExe
Write-Output "pynacl-cp311: building with $py"

# Bridge the build-only PEP-517 backend (depends.build -> CVC_BUILD_PREFIX) onto
# the interpreter's import path; --no-build-isolation cannot fetch it.
if ($env:CVC_BUILD_PREFIX) {
    $sp = Join-Path $env:CVC_BUILD_PREFIX 'Lib\site-packages'
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$sp;$env:PYTHONPATH" } else { $sp }
}

# ── 1. The bundled libsodium, static, with MSVC ─────────────────────────────
$sodium = Join-Path $env:CVC_SOURCE_DIR 'src\libsodium'
$msvc = Join-Path $sodium 'builds\msvc'
# ReleaseLIB compiles against the static CRT (/MT); the extension module links
# the DLL CRT (/MD), so build the static library against /MD as well.
$props = Join-Path $msvc 'properties\ReleaseLIB.props'
$text = [System.IO.File]::ReadAllText($props)
$text = $text.Replace('<RuntimeLibrary>MultiThreaded</RuntimeLibrary>',
                      '<RuntimeLibrary>MultiThreadedDLL</RuntimeLibrary>')
[System.IO.File]::WriteAllText($props, $text)

$msbuild = (Get-Command msbuild.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $msbuild) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path $vswhere) {
        $msbuild = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild `
            -find 'MSBuild\**\Bin\amd64\MSBuild.exe' | Select-Object -First 1
    }
}
if (-not $msbuild) { throw "pynacl-cp311: MSBuild.exe not found (vcvars64 or vswhere)" }
& $msbuild (Join-Path $msvc 'vs2022\libsodium\libsodium.vcxproj') `
    /p:Configuration=ReleaseLIB /p:Platform=x64 /m /nologo /v:minimal
if ($LASTEXITCODE -ne 0) { throw "pynacl-cp311: libsodium msbuild failed ($LASTEXITCODE)" }
$lib = Get-ChildItem -Path (Join-Path $sodium 'bin\x64\Release') -Recurse -Filter 'libsodium.lib' -File |
    Select-Object -First 1
if (-not $lib) { throw "pynacl-cp311: libsodium.lib not found under $sodium\bin\x64\Release" }
Write-Output "pynacl-cp311: libsodium static library $($lib.FullName)"

# cl and link read extra options from the CL and LINK variables, which survive
# setuptools re-running vcvarsall for the extension build.
$env:SODIUM_INSTALL = 'system'
$env:PYNACL_SODIUM_STATIC = '1'
$env:CL = "/I`"$(Join-Path $sodium 'src\libsodium\include')`""
$env:LINK = "/LIBPATH:`"$($lib.DirectoryName)`""

# ── 2. Build the wheel and install it ───────────────────────────────────────
$root = if ($env:CVC_BUILD_DIR) { $env:CVC_BUILD_DIR } else { $env:CVC_SOURCE_DIR }
$wheelhouse = Join-Path $root 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "pynacl-cp311: pip wheel failed ($LASTEXITCODE)" }
Remove-Item Env:CL, Env:LINK -ErrorAction SilentlyContinue

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "pynacl-cp311: no wheel produced under $wheelhouse" }
Write-Output "pynacl-cp311: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "pynacl-cp311: pip install failed ($LASTEXITCODE)" }

# The check must exercise the runtime closure, not the build-only backend:
# sign and verify, and a public-key box round trip, through the static libsodium.
$env:PYTHONPATH = ''
Invoke-CvcPythonCheck @'
import nacl, nacl.bindings
from nacl.signing import SigningKey
from nacl.public import PrivateKey, Box
sk = SigningKey.generate()
assert sk.verify_key.verify(sk.sign(b'cvcpkg')) == b'cvcpkg'
a, b = PrivateKey.generate(), PrivateKey.generate()
assert Box(b, a.public_key).decrypt(Box(a, b.public_key).encrypt(b'cvcpkg')) == b'cvcpkg'
print('pynacl', nacl.__version__, 'sign/verify and box round trip OK')
'@
