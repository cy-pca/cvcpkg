#!/usr/bin/env bash
# recipes/wireguard-tools/build.sh — build the WireGuard userspace tools.
#
# wireguard-tools is a plain Makefile in src/ that auto-detects the platform
# from `uname -s`, so one script serves linux/macos/freebsd/openbsd (each
# built natively on its own builder).  There are no external dependencies —
# it bundles its own crypto.
#
# The Makefile's install target has two host-polluting defaults we override
# so everything lands under the prefix (never /etc or the system systemd
# dir): SYSCONFDIR defaults to /etc, and SYSTEMDUNITDIR resolves via
# `pkg-config systemd` to an absolute /usr path when systemd.pc is present.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}/src"

# `install` depends on the `wg` target, so this both builds and installs.
# WITH_WGQUICK/WITH_BASHCOMPLETION are forced on (they otherwise auto-enable
# only when their target dirs already exist, which they don't in a fresh
# prefix).  All *DIR knobs are pinned under the prefix for relocatability.
make -j "${CVC_JOBS}" \
    PREFIX="${CVC_INSTALL_DIR}" \
    SYSCONFDIR="${CVC_INSTALL_DIR}/etc" \
    SYSTEMDUNITDIR="${CVC_INSTALL_DIR}/lib/systemd/system" \
    WITH_WGQUICK=yes \
    WITH_BASHCOMPLETION=yes \
    install

echo "wireguard-tools: installed wg + wg-quick into ${CVC_INSTALL_DIR}"
