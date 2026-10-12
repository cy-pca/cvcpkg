#!/usr/bin/env bash
# recipes/nh3-cp312/build.sh — build nh3 0.3.6 FROM SOURCE for the cp312
# interpreter column, with maturin and cvcpkg's own Rust toolchain.
#
# WHY: nh3 is a pyo3 binding to the Rust `ammonia` HTML sanitizer. pyo3 is
# configured with abi3-py38, so each column compiles the same stable-ABI
# extension (python.abi: abi3) — as recipes/cryptography-cp31X do. There is no
# cp313t column: the free-threaded build has no stable ABI, and upstream's
# first free-threaded wheel is cp314t.
#
# MECHANIC (as recipes/pydantic-core-cp312): `pip wheel --no-build-isolation
# --no-deps --no-index <sdist>` with maturin staged by the depends.build edges,
# then pip-install the wheel into this recipe's (empty) staging prefix.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # cc (cargo's linker), CVC_JOBS
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"           # cvc_python_exe, ...

: "${CVC_PYTHON_ABI:=abi3}"
: "${CVC_PYTHON_INTERPRETER:=python312}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "nh3-cp312: building with ${PY} (python${PYMM})"

# Bridge the build-only maturin column onto the interpreter's path; ${BLD}/bin
# first on PATH makes OUR maturin executable and OUR cargo win over the host's.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import maturin; print("nh3-cp312: maturin backend", maturin.__file__)'
MATURIN_EXE="$(command -v maturin 2>/dev/null || true)"
[ -n "${MATURIN_EXE}" ] || { echo "nh3-cp312: no 'maturin' executable on PATH (maturin-cp312 supplies it)" >&2; exit 1; }
echo "nh3-cp312: maturin $("${MATURIN_EXE}" --version) [${MATURIN_EXE}]"

# Refuse any cargo/rustc that did not come out of the cvcpkg prefixes: a builder's
# personal rustup must never compile a cvcpkg artifact.
_require_prefix_tool() {
    local tool="$1" resolved
    resolved="$(command -v "${tool}" 2>/dev/null || true)"
    [ -n "${resolved}" ] || { echo "nh3-cp312: ${tool} not on PATH — is the 'rust' build dep in the closure?" >&2; exit 1; }
    case "${resolved}" in
        "${BLD}"/*|"${DEPS}"/*) echo "${resolved}" ;;
        *) echo "nh3-cp312: refusing ${tool} at ${resolved} — outside the cvcpkg prefixes" >&2; exit 1 ;;
    esac
}
CARGO_BIN="$(_require_prefix_tool cargo)"
RUSTC_BIN="$(_require_prefix_tool rustc)"
[ -n "${CARGO_BIN}" ] && [ -n "${RUSTC_BIN}" ] || exit 1
export CARGO="${CARGO_BIN}" RUSTC="${RUSTC_BIN}"
echo "nh3-cp312: $("${CARGO_BIN}" --version) / $("${RUSTC_BIN}" --version)"

export CARGO_HOME="${CVC_CARGO_HOME:-${CVC_BUILD_DIR}/cargo-home}"
export RUSTUP_HOME="${CVC_BUILD_DIR}/rustup-home"
unset RUSTUP_TOOLCHAIN
mkdir -p "${CARGO_HOME}" "${RUSTUP_HOME}"
export CARGO_TARGET_DIR="${CVC_BUILD_DIR}/cargo-target"
export CARGO_BUILD_JOBS="${CVC_JOBS}"
export MATURIN_NO_INSTALL_RUST=1
# HERMETICITY: the sdist ships Cargo.lock but not the crate sources, so cargo
# fetches crates.io (versions lock-pinned). CVC_CARGO_HOME + CVC_CARGO_OFFLINE=1
# force an offline build from a pre-seeded registry.
if [ -n "${CVC_CARGO_OFFLINE:-}" ]; then export CARGO_NET_OFFLINE=true; else export CARGO_NET_OFFLINE=false; fi
echo "nh3-cp312: CARGO_NET_OFFLINE=${CARGO_NET_OFFLINE}"

WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY}" -m pip wheel \
    --no-deps \
    --no-build-isolation \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

shopt -s nullglob
_wheels=( "${WHEELHOUSE}"/*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "nh3-cp312: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "nh3-cp312: built $(basename "${WHEEL}")"

"${PY}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

SITE_PACKAGES="${CVC_INSTALL_DIR}/lib/python${PYMM}/site-packages"
[ -d "${SITE_PACKAGES}" ] || { echo "nh3-cp312: no ${SITE_PACKAGES} after pip install" >&2; exit 1; }

# No rpath pass: the extension links nothing but libc/libm (no external NEEDED).
command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# Verification against the runtime closure only. Inline rather than
# cvc_python_check, which derives the site-packages dir from CVC_PYTHON_ABI and
# so cannot handle an abi3 column.
unset PYTHONPATH
export PYTHONPATH="${SITE_PACKAGES}"
PYARGS=()
if [ "${CVC_PYTHON_INTERPRETER%t}" != "${CVC_PYTHON_INTERPRETER}" ]; then
    export PYTHON_GIL=0
    PYARGS+=(-X gil=0)
fi
"${PY}" ${PYARGS[@]+"${PYARGS[@]}"} - <<'PYCHECK'
import sys, sysconfig
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "GIL re-enabled at runtime; no-GIL support unproven"
import nh3
from nh3 import nh3 as _ext
print("nh3", nh3.__file__, "->", _ext.__file__)
out = nh3.clean("<b>ok</b><script>alert(1)</script><a href='javascript:x'>l</a>")
assert out == "<b>ok</b><a rel=\"noopener noreferrer\">l</a>", out
assert nh3.is_html("<p>x</p>") and not nh3.is_html("plain")
print("nh3.clean OK:", out)
PYCHECK

echo "nh3-cp312: build + verification complete"
