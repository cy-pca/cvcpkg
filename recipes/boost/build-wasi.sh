#!/usr/bin/env bash
# recipes/boost/build-wasi.sh — cross-compile Boost to wasm32-wasi via wasi-sdk.
#
# Consumers: `cgal` (header-only Boost: graph, property_map, variant,
# multiprecision, iterator) and libcvc, which links thread, date_time, regex,
# filesystem and program_options.
#
# The wasi exclusion list is the wasm one plus four.  wasm32-wasip1 has no
# signals, no process control and no dynamic loading, so on top of the wasm
# exclusions we also drop:
#
#   contract   — its thread-safe assertion mode wants real threads.
#   test       — the execution monitor needs sigaction(); wasi-libc has no
#                <signal.h> handlers at all.
#   locale     — depends on std::locale facets and ICU/iconv backends that
#                wasi-libc + libc++ only stub out.
#   wave       — not needed by any consumer, and heavy.
#
# thread and filesystem build and work (checked under wasmtime):
#   thread     — wasi-libc's single-threaded pthreads give working mutexes and
#                condition variables; boost::thread's constructor throws
#                thread_resource_error (EAGAIN), as on any host out of threads.
#                graph, serialization and type_erasure link Boost::thread, so
#                excluding it failed the configure.
#   filesystem — paths resolve through the runtime's preopened directories
#                (`wasmtime run --dir host::/guest`); current_path() is ENOSYS.
#
# C++ exceptions (filesystem_error, program_options errors) are real here:
# env-wasi.sh builds the column with wasm exception handling.
#
# Unlike the wasm build we do NOT define BOOST_HAS_PTHREADS: Boost.Config
# finds wasi-libc's <pthread.h> on its own.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

# Boost.Container's bundled dlmalloc maps memory with mmap(), which wasip1 does
# not have (wasi-libc's <sys/mman.h> is an #error without the mman emulation,
# and a consumer would then need -lwasi-emulated-mman).  HAVE_MMAP=0 makes it
# grow the heap with sbrk() alone, which wasi-libc has; sbrk cannot shrink it.
#
# Two objects need a wasi-libc emulation header to compile: Boost.Chrono's
# process_cpu_clocks.cpp reads times() (process clocks, emulated with wall
# time) and Boost.Iostreams' mapped_file.cpp calls mmap() (emulated by
# reading the file into memory; MAP_SHARED writes fail).  Each object is only
# pulled into a link that uses process_cpu_clock or mapped_file, and that link
# then needs -lwasi-emulated-process-clocks or -lwasi-emulated-mman.
export CFLAGS="${CFLAGS} -DHAVE_MMAP=0 -DMORECORE_CANNOT_TRIM=1"
export CXXFLAGS="${CXXFLAGS} -DHAVE_MMAP=0 -DMORECORE_CANNOT_TRIM=1 -D_WASI_EMULATED_PROCESS_CLOCKS -D_WASI_EMULATED_MMAN"

cvc_cmake_build \
    -DBOOST_ENABLE_CMAKE=ON \
    -DBUILD_TESTING=OFF \
    -DBOOST_INSTALL_LAYOUT=system \
    -DBOOST_EXCLUDE_LIBRARIES="context;coroutine;fiber;stacktrace;asio;cobalt;log;process;beast;contract;test;locale;wave"
