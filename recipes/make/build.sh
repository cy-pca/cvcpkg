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
case "$(uname -s)" in
    Haiku)
        export CFLAGS="${CFLAGS:-} -DNO_ARCHIVES"
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
    # build.cfg) and let the fresh make install itself. Dependency tracking
    # must be off here: config.status bootstraps the .deps fragments by RUNNING
    # make, and without one it dies "Something went wrong bootstrapping makefile
    # fragments". Hosts that have a make (every unix builder) never reach this.
    ./configure \
        --prefix="${CVC_INSTALL_DIR}" \
        --disable-nls \
        --disable-dependency-tracking

    sh ./build.sh
    # By ABSOLUTE path: $(MAKE) is argv[0] verbatim on this build, so a
    # relative ./make breaks the recursive install the moment it cd's into
    # lib/ ("/bin/sh: ./make: No such file or directory").
    "$(pwd)/make" install
fi
