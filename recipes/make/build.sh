#!/usr/bin/env bash
# recipes/make/build.sh — build GNU Make from source.
#
# GNU Make 4.4.1 configures and builds with an existing make (the host
# bootstrap toolchain), the same way the other autotools host-tool
# recipes (m4, autoconf, ...) do -- or, on a host with no make at all
# (the Windows MSYS2 bootstrap), with GNU Make's own make-less build.sh.
# The resulting bin/make is what downstream recipes then use once
# CVC_INSTALL_DIR/bin is on PATH.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# Haiku ships no <ar.h>, so src/arscan.c fails to compile ("ar.h: No such
# file or directory"). NO_ARCHIVES compiles arscan.c (and its callers) as
# stubs, dropping support for updating .a archive members — a capability a
# build-tool make never exercises here. Must be defined for every TU, so it
# goes through CFLAGS.
#
# Haiku's struct dirent ends in a flexible `char d_name[]`, so src/dir.c's
# `sizeof (*d) - sizeof (d->d_name) + len` (sizing the mock dirent it hands to
# glob) fails with "invalid application of 'sizeof' to incomplete type
# 'char[]'". The offset of d_name is what that expression means; Haiku's
# compiler is GCC, so use its builtin rather than rely on <stddef.h> being
# included there.
case "$(uname -s)" in
    Haiku)
        export CFLAGS="${CFLAGS:-} -DNO_ARCHIVES"
        sed -i.orig 's/sizeof (\*d) - sizeof (d->d_name) + len/__builtin_offsetof (struct dirent, d_name) + len/' src/dir.c \
            && rm -f src/dir.c.orig
        grep -q '__builtin_offsetof (struct dirent, d_name)' src/dir.c \
            || { echo "make: the src/dir.c d_name sizing fix did not apply" >&2; exit 1; }
        ;;
esac

if command -v make >/dev/null 2>&1; then
    ./configure \
        --prefix="${CVC_INSTALL_DIR}" \
        --disable-nls

    make -j "${CVC_JOBS}"
    make install
else
    # No make to build make with. On Windows the MSYS2 bootstrap (recipes/msys2)
    # deliberately ships none -- make comes from THIS recipe -- so use GNU Make's
    # own make-less bootstrap script (it compiles from the configure-generated
    # build.cfg), then let that make build and install the real one.
    # Dependency tracking must be off here: config.status bootstraps the .deps
    # fragments by RUNNING make, and without one it dies "Something went wrong
    # bootstrapping makefile fragments". Hosts that have a make (every unix
    # builder) never reach this branch.
    ./configure \
        --prefix="${CVC_INSTALL_DIR}" \
        --disable-nls \
        --disable-dependency-tracking

    sh ./build.sh

    # Drive the real build + install with a COPY of the bootstrap binary. The
    # Makefile relinks make(.exe) in this directory, and on Windows (MSYS/Cygwin)
    # a process cannot fork once its own image is replaced -- running the fresh
    # ./make directly died mid-install with "dofork: child -1 - CreateProcessW
    # failed for ...\make.exe" / "gcc: No such file or directory". Invoke it by
    # ABSOLUTE path too: $(MAKE) is argv[0] verbatim, so a relative ./make
    # breaks the recursion as soon as it cd's into lib/.
    _exe=""
    [ -f make.exe ] && _exe=".exe"
    mkdir -p .cvcpkg-bootstrap
    cp "make${_exe}" ".cvcpkg-bootstrap/make${_exe}"
    _boot="$(pwd)/.cvcpkg-bootstrap/make"
    "${_boot}" -j "${CVC_JOBS}"
    "${_boot}" install
fi
