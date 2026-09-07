#!/usr/bin/env bash
# recipes/automake/build.sh — build GNU Automake from source.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

# Prefer the autoconf/autom4te cvcpkg installed into this build's closure over
# anything on the host. That bundle is now relocatable (see recipes/autoconf),
# and using it avoids the OpenBSD/NetBSD trap where the ports `autoconf` is a
# version-dispatch wrapper that aborts unless AUTOCONF_VERSION is exported —
# which made automake's configure report "Autoconf 2.65 or better is required".
for _root in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
    [[ -n "${_root}" ]] || continue
    [[ -z "${AUTOCONF:-}" && -x "${_root}/bin/autoconf" ]] && export AUTOCONF="${_root}/bin/autoconf"
    [[ -z "${AUTOM4TE:-}" && -x "${_root}/bin/autom4te" ]] && export AUTOM4TE="${_root}/bin/autom4te"
done

# GNU make on BSD; and, as a last resort where the closure lacks autoconf, fall
# back to the ports-versioned autotools names.
MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        if command -v gmake >/dev/null 2>&1; then
            MAKE=gmake
        fi
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

# --- openbsd diagnostics (cvc.6): automake's configure reports "autoconf is
# installed... no" on openbsd though autoconf 2.72+cvc.2 is in the closure.
# Show what AUTOCONF resolved to, whether the script actually runs, and dump the
# config.log check region on failure. Cheap; harmless on the green platforms. ---
_ac_show="${AUTOCONF:-autoconf}"
_ac_path="$(command -v "${_ac_show}" 2>/dev/null || echo NOTFOUND)"
echo "[automake diag] AUTOCONF=[${AUTOCONF:-unset}] -> ${_ac_path}"
echo "[automake diag] AUTOM4TE=[${AUTOM4TE:-unset}]  M4=[${M4:-unset}]"
echo "[automake diag] CVC_BUILD_PREFIX=[${CVC_BUILD_PREFIX:-}] CVC_DEPS_PREFIX=[${CVC_DEPS_PREFIX:-}]"
if [ "${_ac_path}" != "NOTFOUND" ]; then
    echo "[automake diag] shebang: $(head -1 "${_ac_path}" 2>&1)"
    echo "[automake diag] \$AUTOCONF --version (exit shown):"
    "${_ac_show}" --version 2>&1 | head -3 || true
    echo "[automake diag]   -> exit ${PIPESTATUS[0]:-?}"
fi

cd "${CVC_SOURCE_DIR}"

if ! ./configure --prefix="${CVC_INSTALL_DIR}"; then
    echo "===== configure FAILED (automake) ====="
    grep -nE "autoconf is installed|autoconf works|Autoconf 2.65|AUTOCONF|autom4te|version" config.log 2>/dev/null | head -20 || true
    echo "===== end config.log excerpt ====="
    exit 1
fi

"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install
