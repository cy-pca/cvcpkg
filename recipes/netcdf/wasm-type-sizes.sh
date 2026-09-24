#!/usr/bin/env bash
# recipes/netcdf/wasm-type-sizes.sh — pre-seeded wasm32 (ILP32) type sizes for
# netCDF's check_type_size. Sourced by build-wasm.sh and build-wasi.sh (wasm and
# wasi are both wasm32/ILP32, so the sizes are identical). Defines WASM_TYPE_SIZES.
#
# WHY: under emscripten, check_type_size's INFO-size scan reads the .js wrapper
# while the size marker lives in the .wasm, so it returns EMPTY and SIZEOF_* end up
# undefined -> netCDF's dutil.c fails to compile ("undeclared identifier
# 'SIZEOF_INT'"). We deliberately do NOT fix this with
# CMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY: a static-lib try_compile never
# links, so every link-based check_function_exists false-positives -> netCDF
# wrongly detects a PARALLEL HDF5 (finds H5Pget_fapl_mpio) and then does
# find_package(MPI REQUIRED), which fails on wasm. Instead we keep an executable
# try_compile (so the HDF5 symbol probes stay correct = serial) and hand netCDF the
# sizes directly: HAVE_SIZEOF_* makes CMake skip each probe, SIZEOF_* supplies the
# value. Types that don't exist on wasm32-clang (schar / __int64 / bare uint64) are
# intentionally left unseeded so their probes still run and leave them undefined.
WASM_TYPE_SIZES=(
    -DHAVE_SIZEOF_CHAR=1                -DSIZEOF_CHAR=1
    -DHAVE_SIZEOF_UCHAR=1               -DSIZEOF_UCHAR=1
    -DHAVE_SIZEOF__BOOL=1               -DSIZEOF__BOOL=1
    -DHAVE_SIZEOF_SHORT=1               -DSIZEOF_SHORT=2
    -DHAVE_SIZEOF_USHORT=1              -DSIZEOF_USHORT=2
    -DHAVE_SIZEOF_UNSIGNED_SHORT_INT=1  -DSIZEOF_UNSIGNED_SHORT_INT=2
    -DHAVE_SIZEOF_INT=1                 -DSIZEOF_INT=4
    -DHAVE_SIZEOF_UINT=1                -DSIZEOF_UINT=4
    -DHAVE_SIZEOF_UNSIGNED_INT=1        -DSIZEOF_UNSIGNED_INT=4
    -DHAVE_SIZEOF_LONG=1                -DSIZEOF_LONG=4
    -DHAVE_SIZEOF_LONG_LONG=1           -DSIZEOF_LONG_LONG=8
    -DHAVE_SIZEOF_LONGLONG=1            -DSIZEOF_LONGLONG=8
    -DHAVE_SIZEOF_UNSIGNED_LONG_LONG=1  -DSIZEOF_UNSIGNED_LONG_LONG=8
    -DHAVE_SIZEOF_ULONGLONG=1           -DSIZEOF_ULONGLONG=8
    -DHAVE_SIZEOF_FLOAT=1               -DSIZEOF_FLOAT=4
    -DHAVE_SIZEOF_DOUBLE=1              -DSIZEOF_DOUBLE=8
    -DHAVE_SIZEOF_SIZE_T=1              -DSIZEOF_SIZE_T=4
    -DHAVE_SIZEOF_SSIZE_T=1             -DSIZEOF_SSIZE_T=4
    -DHAVE_SIZEOF_PTRDIFF_T=1           -DSIZEOF_PTRDIFF_T=4
    -DHAVE_SIZEOF_UINTPTR_T=1           -DSIZEOF_UINTPTR_T=4
    -DHAVE_SIZEOF_OFF_T=1               -DSIZEOF_OFF_T=8
    -DHAVE_SIZEOF_OFF64_T=1             -DSIZEOF_OFF64_T=8
    -DHAVE_SIZEOF_INT64_T=1             -DSIZEOF_INT64_T=8
    -DHAVE_SIZEOF_UINT64_T=1            -DSIZEOF_UINT64_T=8
)
