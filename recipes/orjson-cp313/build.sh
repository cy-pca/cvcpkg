#!/usr/bin/env bash
# recipes/orjson-cp313/build.sh — build orjson 3.11.8 FROM SOURCE for the cp313
# interpreter column, with maturin and cvcpkg's own Rust toolchain.
#
# WHY: orjson is a Rust (pyo3-ffi) JSON library that also compiles the vendored
# yyjson C sources through the cc crate. Pinned to 3.11.8, one patch behind
# 3.11.9: 3.11.9 raised rust-version to 1.95 and cvcpkg's rust is 1.90.0
# (3.11.8 declares 1.89). No cp313t column: upstream publishes none.
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

: "${CVC_PYTHON_ABI:=cp313}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "orjson-cp313: building with ${PY} (python${PYMM})"

# Bridge the build-only maturin column onto the interpreter's path; ${BLD}/bin
# first on PATH makes OUR maturin executable and OUR cargo win over the host's.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import maturin; print("orjson-cp313: maturin backend", maturin.__file__)'
MATURIN_EXE="$(command -v maturin 2>/dev/null || true)"
[ -n "${MATURIN_EXE}" ] || { echo "orjson-cp313: no 'maturin' executable on PATH (maturin-cp313 supplies it)" >&2; exit 1; }
echo "orjson-cp313: maturin $("${MATURIN_EXE}" --version) [${MATURIN_EXE}]"

# Refuse any cargo/rustc that did not come out of the cvcpkg prefixes: a builder's
# personal rustup must never compile a cvcpkg artifact.
_require_prefix_tool() {
    local tool="$1" resolved
    resolved="$(command -v "${tool}" 2>/dev/null || true)"
    [ -n "${resolved}" ] || { echo "orjson-cp313: ${tool} not on PATH — is the 'rust' build dep in the closure?" >&2; exit 1; }
    case "${resolved}" in
        "${BLD}"/*|"${DEPS}"/*) echo "${resolved}" ;;
        *) echo "orjson-cp313: refusing ${tool} at ${resolved} — outside the cvcpkg prefixes" >&2; exit 1 ;;
    esac
}
CARGO_BIN="$(_require_prefix_tool cargo)"
RUSTC_BIN="$(_require_prefix_tool rustc)"
[ -n "${CARGO_BIN}" ] && [ -n "${RUSTC_BIN}" ] || exit 1
export CARGO="${CARGO_BIN}" RUSTC="${RUSTC_BIN}"
echo "orjson-cp313: $("${CARGO_BIN}" --version) / $("${RUSTC_BIN}" --version)"

export CARGO_HOME="${CVC_CARGO_HOME:-${CVC_BUILD_DIR}/cargo-home}"
export RUSTUP_HOME="${CVC_BUILD_DIR}/rustup-home"
unset RUSTUP_TOOLCHAIN
mkdir -p "${CARGO_HOME}" "${RUSTUP_HOME}"
export CARGO_TARGET_DIR="${CVC_BUILD_DIR}/cargo-target"
export CARGO_BUILD_JOBS="${CVC_JOBS}"
export MATURIN_NO_INSTALL_RUST=1
# HERMETICITY: the orjson sdist VENDORS every crate (include/cargo, wired up by
# the sdist's .cargo/config.toml source replacement), so cargo needs no network.
# Enforce that: an unexpected crates.io fetch fails instead of passing silently.
export CARGO_NET_OFFLINE=true

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
[ -n "${WHEEL}" ] || { echo "orjson-cp313: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "orjson-cp313: built $(basename "${WHEEL}")"

"${PY}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

SITE_PACKAGES="${CVC_INSTALL_DIR}/lib/python${PYMM}/site-packages"
[ -d "${SITE_PACKAGES}" ] || { echo "orjson-cp313: no ${SITE_PACKAGES} after pip install" >&2; exit 1; }

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
import datetime, json, orjson
print("orjson", orjson.__version__, "->", orjson.__file__)
doc = {"a": [1, 2.5, None, True], "s": "ünïcode", "dt": datetime.datetime(2026, 10, 11, 12, 0, 0)}
raw = orjson.dumps(doc, option=orjson.OPT_SORT_KEYS)
assert json.loads(raw) == {"a": [1, 2.5, None, True], "dt": "2026-10-11T12:00:00", "s": "ünïcode"}, raw
assert orjson.loads(raw)["s"] == "ünïcode"
print("orjson dumps/loads OK:", raw)
PYCHECK

echo "orjson-cp313: build + verification complete"
