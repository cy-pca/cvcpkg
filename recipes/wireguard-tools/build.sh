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

# wireguard-tools ships a GNU Makefile; the BSD `make` on freebsd/openbsd
# cannot parse it.  Use gmake unless the native `make` is already GNU make
# (linux, macOS ship GNU make as `make`).
MAKE="${MAKE:-make}"
if ! "${MAKE}" --version 2>/dev/null | grep -qi 'gnu make'; then
    if command -v gmake >/dev/null 2>&1; then
        MAKE=gmake
    else
        echo "wireguard-tools: need GNU make (gmake) on this platform" >&2
        exit 1
    fi
fi

# The Makefile's install target runs `install -v ...`, and OpenBSD's install(1)
# has no -v ("install: unknown option -- v").  Where -v is refused, put a shim
# that drops it ahead of the system install on PATH.
_probe="$(mktemp -d)"
if ! install -v -d "${_probe}/d" >/dev/null 2>&1; then
    mkdir -p "${_probe}/shim"
    _real_install="$(command -v install)"
    cat > "${_probe}/shim/install" <<SH
#!/bin/sh
n=\$#
while [ "\$n" -gt 0 ]; do
    a=\$1; shift; n=\$((n - 1))
    [ "\$a" = -v ] || set -- "\$@" "\$a"
done
exec ${_real_install} "\$@"
SH
    chmod 0755 "${_probe}/shim/install"
    export PATH="${_probe}/shim:${PATH}"
fi

# `install` depends on the `wg` target, so this both builds and installs.
# WITH_WGQUICK/WITH_BASHCOMPLETION are forced on (they otherwise auto-enable
# only when their target dirs already exist, which they don't in a fresh
# prefix).  All *DIR knobs are pinned under the prefix for relocatability.
"${MAKE}" -j "${CVC_JOBS}" \
    PREFIX="${CVC_INSTALL_DIR}" \
    SYSCONFDIR="${CVC_INSTALL_DIR}/etc" \
    SYSTEMDUNITDIR="${CVC_INSTALL_DIR}/lib/systemd/system" \
    WITH_WGQUICK=yes \
    WITH_BASHCOMPLETION=yes \
    install

echo "wireguard-tools: installed wg + wg-quick into ${CVC_INSTALL_DIR}"
