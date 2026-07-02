# recipes/openblas/build.ps1 — build OpenBLAS on Windows with MSVC.
#
# OpenBLAS has first-class CMake support and builds cleanly with
# MSVC + Ninja.  We disable the built-in test executables (they
# require a Fortran compiler and getopt.h) and only build BLAS + LAPACK
# using the bundled f2c'd sources.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DNOFORTRAN=1',
    '-DBUILD_WITHOUT_LAPACK=OFF',
    '-DC_LAPACK=ON',
    '-DBUILD_TESTING=OFF',
    '-DUSE_THREAD=1',
    '-DDYNAMIC_ARCH=OFF',
    '-DTARGET=GENERIC',
    '-DCMAKE_C_FLAGS=/wd4013 /wd4133 /wd4244 /wd4267 /wd4996 /D_CRT_SECURE_NO_WARNINGS'
)
