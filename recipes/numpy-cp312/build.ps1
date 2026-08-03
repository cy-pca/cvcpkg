# recipes/numpy-cp312/build.ps1 — Windows: DEFERRED (loud stub).
#
# numpy is now built FROM SOURCE (see build.sh). `source.type` is recipe-wide,
# so this recipe can no longer carry a prebuilt-wheel Windows column. The
# from-source Windows port (MSVC + meson against cvcpkg OpenBLAS + DLL discovery
# via os.add_dll_directory) is a tracked fast-follow; until it lands, numpy-cp312
# does not build on Windows. Fail loudly rather than silently ship nothing.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Write-Error "numpy-cp312: Windows from-source build not yet implemented (see build.sh; MSVC/meson/OpenBLAS port pending). Linux/macOS/BSD build from build.sh."
exit 1
