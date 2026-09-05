#!/usr/bin/env bash
# recipes/automake/build.sh — build GNU Automake from source.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

# GNU make + versioned autotools on BSD.
MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        if command -v gmake >/dev/null 2>&1; then
            MAKE=gmake
        fi
        # OpenBSD/NetBSD ports install autoconf as autoconf-<VER>.
        # Point configure at the latest ≥2.65 present on PATH so the
        # "Autoconf 2.65 or better is required" check passes.
        if [[ -z "${AUTOCONF:-}" ]]; then
            for candidate in autoconf-2.72 autoconf-2.71 autoconf-2.69 autoconf; do
                if command -v "${candidate}" >/dev/null 2>&1; then
                    export AUTOCONF="${candidate}"
                    break
                fi
            done
        fi
        if [[ -z "${AUTOM4TE:-}" ]]; then
            for candidate in autom4te-2.72 autom4te-2.71 autom4te-2.69 autom4te; do
                if command -v "${candidate}" >/dev/null 2>&1; then
                    export AUTOM4TE="${candidate}"
                    break
                fi
            done
        fi
        ;;
esac

# === DIAGNOSTIC PASS (cvc.2) — do not "fix" anything yet ===
# automake's rebuild fails on the dev fleet at configure's "whether autoconf
# works" self-test. The build-log tail never carries config.log, so the true
# autom4te error is invisible. This pass ADDS a GNU m4 to the closure but does
# NOT touch $M4, then prints exactly what the toolchain resolves to and dumps
# config.log on failure — so the next run reveals the ground truth (which m4
# autom4te tried, and whether the builder exports a stale M4) before any fix.
echo "===== automake m4 diagnostics ====="
echo "uname:            $(uname -srm)"
if [ -n "${M4+x}" ]; then echo "inherited M4:     SET=[${M4}]"; else echo "inherited M4:     <unset>"; fi
echo "CVC_BUILD_PREFIX: [${CVC_BUILD_PREFIX:-}]"
echo "CVC_DEPS_PREFIX:  [${CVC_DEPS_PREFIX:-}]"
echo "CVC_INSTALL_DIR:  [${CVC_INSTALL_DIR:-}]"
echo "PATH:             ${PATH}"
echo "command -v m4:      $(command -v m4 || echo none)"
echo "command -v gm4:     $(command -v gm4 || echo none)"
echo "command -v autoconf:$(command -v autoconf || echo none)"
echo "command -v autom4te:$(command -v autom4te || echo none)"
for r in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
    [ -n "${r}" ] && echo "stat ${r}/bin/m4:   $(ls -l "${r}/bin/m4" 2>&1)"
done
if command -v m4 >/dev/null 2>&1; then echo "m4 --version:       $(m4 --version 2>&1 | head -1)"; fi
_am="$(command -v autom4te 2>/dev/null || true)"
[ -n "${_am}" ] && echo "autom4te m4 line:   $(grep -m1 'my \$m4' "${_am}" 2>/dev/null || echo '?')"
echo "===== end diagnostics ====="

cd "${CVC_SOURCE_DIR}"

if ! ./configure --prefix="${CVC_INSTALL_DIR}"; then
    echo "===== configure FAILED ====="
    # Re-echo the key facts here so they survive the build-log tail -80 (the
    # pre-configure diagnostics scroll past it behind configure's own output).
    if [ -n "${M4+x}" ]; then echo "M4=[${M4}]"; else echo "M4=<unset>"; fi
    echo "command -v m4=$(command -v m4 || echo none)  gm4=$(command -v gm4 || echo none)"
    for r in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
        [ -n "${r}" ] && echo "stat ${r}/bin/m4: $(ls -l "${r}/bin/m4" 2>&1)"
    done
    echo "--- config.log: m4 / autom4te / autoconf-works lines ---"
    grep -nE "autom4te|need GNU m4|reload-state|autoconf works|cd conftest" config.log 2>/dev/null | head -30
    echo "--- config.log: context around the autoconf-works test ---"
    awk '/checking whether autoconf works/{c=NR} c&&NR>=c&&NR<=c+12{print}' config.log 2>/dev/null | head -15
    echo "===== end failure diagnostics ====="
    exit 1
fi

"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install
