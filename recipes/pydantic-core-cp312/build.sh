#!/usr/bin/env bash
# recipes/pydantic-core-cp312/build.sh — build pydantic-core 2.46.4 FROM SOURCE
# for the cp312 interpreter column of the per-interpreter wheel matrix.
#
# WHY FROM SOURCE: not to unvendor anything — _pydantic_core is a pyo3 cdylib
# that links nothing but libc — but because PyPI publishes no FreeBSD or NetBSD
# wheel, so on those platforms pydantic-core (and therefore pydantic, and
# therefore most of the API stack above it) did not exist at all. Compiling the
# sdist is the only route, and it needs cargo on the target, which recipes/rust
# gained in rust+cvc.2.
#
# WHAT THIS BUILD ACTUALLY IS: pydantic-core's PEP-517 backend is `maturin`, a
# RUST program, and the extension itself is Rust. Two Rust layers, both fed by
# the cvcpkg `rust` package — which is why this column exists on freebsd/netbsd
# and does not exist on openbsd.
#
# MECHANIC: `pip wheel --no-build-isolation --no-deps --no-index <sdist>` with
# maturin already provisioned into the prefix by the depends.build edges, then
# pip-install the resulting wheel into this recipe's staging prefix so the
# dist-info/RECORD/METADATA are real and pip's resolver later sees pydantic-core
# as satisfied. This script does NOT re-solve the build closure — it assumes the
# declared deps are present and fails loudly when one is missing.
#
# HERMETICITY, STATED PLAINLY: the pip layer is offline. The CARGO layer is not.
# The sdist ships Cargo.lock but no vendored crate sources, so cargo fetches from
# crates.io. Versions are lock-pinned; the network is the uncontrolled part. See
# the CVC_CARGO_HOME / CVC_CARGO_OFFLINE levers below.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # cc (cargo's linker), CVC_JOBS
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"           # cvc_python_exe, ...

# ── 1. Resolve the prefix interpreter for this column ───────────────────────
# python.interpreter/python.abi are exported by the builder; keep defaults so the
# script stays runnable standalone.
: "${CVC_PYTHON_ABI:=cp312}"
: "${CVC_PYTHON_INTERPRETER:=python312}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
# cvc_interp_version keeps the free-threaded suffix (python313t -> 3.13t), which
# is also the site-packages directory name — do not re-derive it from the ABI tag.
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "pydantic-core-cp312: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# cvc_python_exe runs the interpreter from CVC_DEPS_PREFIX, which imports only its
# OWN site-packages. The maturin column lands in CVC_BUILD_PREFIX, so
# --no-build-isolation cannot import it without this PYTHONPATH bridge.
# ${BLD}/bin first on PATH is what makes OUR maturin executable and OUR cargo win
# over anything the builder host has installed.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import maturin; print("pydantic-core-cp312: maturin backend", maturin.__file__)'

# The maturin SHIM is not enough: maturin/__init__.py runs `maturin pep517
# build-wheel`, i.e. it needs the executable on PATH. Resolve it here so a missing
# .data/scripts/ entry fails with a sentence instead of a FileNotFoundError from
# inside pip.
MATURIN_EXE="$(command -v maturin 2>/dev/null || true)"
[ -n "${MATURIN_EXE}" ] || {
    echo "pydantic-core-cp312: no 'maturin' executable on PATH — the maturin-cp312" >&2
    echo "  build dep supplies it in ${BLD}/bin; is it in the closure?" >&2
    exit 1
}
echo "pydantic-core-cp312: maturin $("${MATURIN_EXE}" --version) [${MATURIN_EXE}]"

# ── 3. Pin the Rust toolchain to the one cvcpkg built ──────────────────────
# The failure this guards against is silent and expensive: a builder with a
# personal rustup gets picked up, the artifact is compiled by an untracked
# toolchain, and nothing in the manifest says so. Resolve cargo/rustc and REFUSE
# anything that did not come out of the cvcpkg prefixes.
_require_prefix_tool() {
    local tool="$1" resolved
    resolved="$(command -v "${tool}" 2>/dev/null || true)"
    [ -n "${resolved}" ] || {
        echo "pydantic-core-cp312: ${tool} not on PATH — is the 'rust' build dep in the closure?" >&2
        exit 1
    }
    case "${resolved}" in
        "${BLD}"/*|"${DEPS}"/*) echo "${resolved}" ;;
        *)
            echo "pydantic-core-cp312: refusing ${tool} at ${resolved} — it is outside the" >&2
            echo "  cvcpkg prefixes (${BLD}, ${DEPS}). A system rustup must not build cvcpkg artifacts." >&2
            exit 1
            ;;
    esac
}
# The helper's `exit 1` only leaves its command substitution, so `set -e` is what
# actually aborts the build; assert non-empty too rather than relying on that.
CARGO_BIN="$(_require_prefix_tool cargo)"
RUSTC_BIN="$(_require_prefix_tool rustc)"
[ -n "${CARGO_BIN}" ] && [ -n "${RUSTC_BIN}" ] || exit 1
export CARGO="${CARGO_BIN}"
export RUSTC="${RUSTC_BIN}"
echo "pydantic-core-cp312: cargo $("${CARGO_BIN}" --version) [${CARGO_BIN}]"
echo "pydantic-core-cp312: rustc $("${RUSTC_BIN}" --version) [${RUSTC_BIN}]"

# Keep cargo's state inside the build tree: a $HOME/.cargo on the builder would
# otherwise be both an input (a stale or poisoned registry) and an output. The
# RUSTUP_* pins and the RUSTUP_TOOLCHAIN unset close the same door for a shim.
export CARGO_HOME="${CVC_CARGO_HOME:-${CVC_BUILD_DIR}/cargo-home}"
export RUSTUP_HOME="${CVC_BUILD_DIR}/rustup-home"
unset RUSTUP_TOOLCHAIN
mkdir -p "${CARGO_HOME}" "${RUSTUP_HOME}"
# Build artifacts out of CVC_SOURCE_DIR so the extracted sdist stays pristine.
# NOTE this does NOT disturb the sdist's own .cargo/config.toml, which carries
# the `-undefined dynamic_lookup` link args pyo3 needs on macOS — cargo reads
# that from the manifest directory, not from CARGO_TARGET_DIR.
export CARGO_TARGET_DIR="${CVC_BUILD_DIR}/cargo-target"
# maturin's bootstrap backend asks pip for `puccinialin` (a rustup installer) when
# cargo is missing from PATH. It is not missing — but say so explicitly, so the
# hook can never pull a package cvcpkg's graph knows nothing about.
export MATURIN_NO_INSTALL_RUST=1

# The one non-hermetic step in this recipe, made explicit rather than implicit.
if [ -n "${CVC_CARGO_OFFLINE:-}" ]; then
    export CARGO_NET_OFFLINE=true
    echo "pydantic-core-cp312: CARGO_NET_OFFLINE=true — ${CARGO_HOME} must already"
    echo "  contain every crate in Cargo.lock, or cargo will fail rather than fetch."
else
    export CARGO_NET_OFFLINE=false
    echo "pydantic-core-cp312: NOTE — cargo will FETCH crates.io. This sdist ships"
    echo "  Cargo.lock but does not vendor crate sources, so the build needs network"
    echo "  access. Versions are lock-pinned. Set CVC_CARGO_HOME to a pre-seeded"
    echo "  registry plus CVC_CARGO_OFFLINE=1 to build air-gapped."
fi

# ── 4. Build the wheel from the sdist (pip offline, no build isolation) ────
# --no-deps so pip never reaches past cvcpkg's graph; --no-index so pip cannot
# resolve anything over the network; --no-build-isolation so the PEP-517 backend
# is the maturin already staged in the prefix rather than a fresh PyPI download.
WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY}" -m pip wheel \
    --no-deps \
    --no-build-isolation \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

# Plain glob rather than `find -print -quit`: OpenBSD's find has no -quit, and a
# `find | head` pipeline trips `set -o pipefail` when head closes the pipe early.
shopt -s nullglob
_wheels=( "${WHEELHOUSE}"/*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "pydantic-core-cp312: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "pydantic-core-cp312: built $(basename "${WHEEL}")"

# ── 5. Install into THIS recipe's staging prefix ──────────────────────────
# stage_bundle ships the ENTIRE CVC_INSTALL_DIR tree (package.files is not a
# filter), so installing --prefix into the initially-empty per-recipe dir IS the
# packaging contract: nothing but this package can land there.
"${PY}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

SITE_PACKAGES=""
for _cand in "${CVC_INSTALL_DIR}"/lib/python*/site-packages \
             "${CVC_INSTALL_DIR}"/lib64/python*/site-packages \
             "${CVC_INSTALL_DIR}"/Lib/site-packages; do
    if [ -d "${_cand}" ]; then SITE_PACKAGES="${_cand}"; break; fi
done
[ -n "${SITE_PACKAGES}" ] || {
    echo "pydantic-core-cp312: no site-packages under ${CVC_INSTALL_DIR} after pip install" >&2
    ls -la "${CVC_INSTALL_DIR}" >&2 || true
    exit 1
}
echo "pydantic-core-cp312: staged into ${SITE_PACKAGES}"

# No rpath pass here, deliberately: _pydantic_core has no external NEEDED library
# to find (cryptography and cffi do, which is why they patchelf and this does
# not).
command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── 6. Verification ────────────────────────────────────────────────────────
# Prove the extension loads AND validates — an import alone would not
# distinguish a working pyo3 build from one whose schema compiler is broken.
export PYTHONPATH="${SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"
# A free-threaded column must be proven WITHOUT the GIL. pydantic-core has no
# cp313t column today, but keep the branch so adding one later cannot quietly
# skip the assertion.
PYARGS=()
if cvc_python_is_free_threaded; then
    export PYTHON_GIL=0
    PYARGS+=(-X gil=0)
fi
"${PY}" ${PYARGS[@]+"${PYARGS[@]}"} - <<'PYCHECK'
import sys, sysconfig

if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "GIL re-enabled at runtime; no-GIL support unproven"
    print("GIL disabled:", not sys._is_gil_enabled())

import pydantic_core
from pydantic_core import _pydantic_core, core_schema, SchemaValidator, ValidationError

assert _pydantic_core.__file__.endswith((".so", ".pyd", ".dylib")), _pydantic_core.__file__
print("pydantic_core", pydantic_core.__version__, "->", _pydantic_core.__file__)

# typing_extensions is a declared runtime edge; prove it resolves from the prefix
# rather than only happening to be present on the builder.
import typing_extensions
print("typing_extensions:", typing_extensions.__file__)

# Compile and run a real schema: this exercises the Rust validator end to end,
# which an import would not.
v = SchemaValidator(core_schema.typed_dict_schema({
    "name": core_schema.typed_dict_field(core_schema.str_schema()),
    "count": core_schema.typed_dict_field(core_schema.int_schema()),
}))
assert v.validate_python({"name": "cvcpkg", "count": "7"}) == {"name": "cvcpkg", "count": 7}
try:
    v.validate_python({"name": "cvcpkg", "count": "not-an-int"})
except ValidationError:
    pass
else:
    raise AssertionError("validator accepted a bad int — the Rust core is not doing the work")
print("pydantic_core round-trip OK")
PYCHECK

echo "pydantic-core-cp312: build + verification complete"
