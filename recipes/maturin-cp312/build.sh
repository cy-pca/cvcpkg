#!/usr/bin/env bash
# recipes/maturin-cp312/build.sh — build maturin 1.14.1 FROM SOURCE for the
# cp312 interpreter column.
#
# WHAT MATURIN IS HERE FOR: cryptography and pydantic-core declare
# `build-backend = "maturin"`, so maturin has to exist in the build prefix before
# either of them can be built at all. It is not a pure-Python backend: the shim in
# maturin/__init__.py runs `maturin pep517 build-wheel -i <sys.executable>`, i.e.
# it shells out to a compiled Rust executable that this recipe produces.
#
# HOW IT BOOTSTRAPS: maturin's pyproject sets backend-path = ["maturin"] and
# build-backend = "bootstrap". bootstrap.py is setuptools.build_meta plus one
# hook — get_requires_for_build_wheel() returns ["puccinialin"] (a rustup
# installer) when `cargo` is missing from PATH and MATURIN_NO_INSTALL_RUST is
# unset. We do BOTH: cargo comes from the cvcpkg `rust` package on PATH, and
# MATURIN_NO_INSTALL_RUST=1 is exported, so that hook can never fire and pip can
# never be asked for a package outside cvcpkg's graph. setup.py then builds the
# binary through setuptools-rust's RustBin with `--no-default-features --locked`.
#
# HERMETICITY, STATED PLAINLY: the pip layer is offline (--no-index,
# --no-build-isolation, --no-deps). The CARGO layer is not. maturin's sdist
# carries Cargo.lock but no vendored crate sources, so cargo fetches from
# crates.io. Versions are lock-pinned, the network is the only thing that is not
# controlled. See the CVC_CARGO_HOME / CVC_CARGO_OFFLINE levers below.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # CC/CXX for cargo's linker, CVC_JOBS
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"           # cvc_python_exe, ...

# ── 1. Resolve the prefix interpreter for this column ───────────────────────
: "${CVC_PYTHON_ABI:=cp312}"
: "${CVC_PYTHON_INTERPRETER:=python312}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "maturin-cp312: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# Same mechanic as every other from-source column: depends.build python packages
# land in CVC_BUILD_PREFIX, and --no-build-isolation imports straight off
# sys.path, so PYTHONPATH has to reach them. The BLD/bin ahead of DEPS/bin is what
# puts OUR cargo in front of anything the builder host has.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import setuptools, setuptools_rust, wheel; print("maturin-cp312: setuptools", setuptools.__version__, "+ setuptools_rust + wheel")'

# ── 3. Pin the Rust toolchain to the one cvcpkg built ──────────────────────
# The failure this guards against is silent and expensive: a builder with a
# personal rustup gets picked up, the artifact is compiled by an untracked
# toolchain, and nothing in the manifest says so. Resolve cargo/rustc and REFUSE
# anything that did not come out of the cvcpkg prefixes.
_require_prefix_tool() {
    local tool="$1" resolved
    resolved="$(command -v "${tool}" 2>/dev/null || true)"
    [ -n "${resolved}" ] || {
        echo "maturin-cp312: ${tool} not on PATH — is the 'rust' build dep in the closure?" >&2
        exit 1
    }
    case "${resolved}" in
        "${BLD}"/*|"${DEPS}"/*) echo "${resolved}" ;;
        *)
            echo "maturin-cp312: refusing ${tool} at ${resolved} — it is outside the cvcpkg" >&2
            echo "  prefixes (${BLD}, ${DEPS}). A system rustup must not build cvcpkg artifacts." >&2
            exit 1
            ;;
    esac
}
# The helper's `exit 1` only leaves its command substitution, so `set -e` is
# what actually aborts the build; assert non-empty too rather than relying on
# that one subtlety.
CARGO_BIN="$(_require_prefix_tool cargo)"
RUSTC_BIN="$(_require_prefix_tool rustc)"
[ -n "${CARGO_BIN}" ] && [ -n "${RUSTC_BIN}" ] || exit 1
export CARGO="${CARGO_BIN}"    # setuptools_rust reads $CARGO (build.py: os.getenv("CARGO"))
export RUSTC="${RUSTC_BIN}"
echo "maturin-cp312: cargo $("${CARGO_BIN}" --version) [${CARGO_BIN}]"
echo "maturin-cp312: rustc $("${RUSTC_BIN}" --version) [${RUSTC_BIN}]"

# Keep cargo's state inside the build tree: a $HOME/.cargo on the builder would
# otherwise be both an input (a stale or poisoned registry) and an output (a
# growing cache the build never cleans up). RUSTUP_* are pinned at empty
# directories for the same reason, and RUSTUP_TOOLCHAIN is dropped so an inherited
# value cannot redirect a shim we do not expect to exist.
export CARGO_HOME="${CVC_CARGO_HOME:-${CVC_BUILD_DIR}/cargo-home}"
export RUSTUP_HOME="${CVC_BUILD_DIR}/rustup-home"
unset RUSTUP_TOOLCHAIN
mkdir -p "${CARGO_HOME}" "${RUSTUP_HOME}"
# Build artifacts out of CVC_SOURCE_DIR so the extracted sdist stays pristine.
export CARGO_TARGET_DIR="${CVC_BUILD_DIR}/cargo-target"

# maturin's bootstrap hook installs rustup via puccinialin when cargo is missing.
# cargo is NOT missing, but set this anyway: the hook is also consulted by pip
# when it validates build requirements, and an accidental "yes" there would pull a
# package from PyPI that cvcpkg's graph knows nothing about.
export MATURIN_NO_INSTALL_RUST=1

# The one non-hermetic step in this recipe, made explicit rather than implicit.
if [ -n "${CVC_CARGO_OFFLINE:-}" ]; then
    export CARGO_NET_OFFLINE=true
    echo "maturin-cp312: CARGO_NET_OFFLINE=true — ${CARGO_HOME} must already contain"
    echo "  every crate in Cargo.lock, or cargo will fail rather than fetch."
else
    export CARGO_NET_OFFLINE=false
    echo "maturin-cp312: NOTE — cargo will FETCH crates.io. maturin's sdist ships"
    echo "  Cargo.lock but does not vendor sources, so this build needs network"
    echo "  access. Versions are lock-pinned. Set CVC_CARGO_HOME to a pre-seeded"
    echo "  registry plus CVC_CARGO_OFFLINE=1 to build air-gapped."
fi

# ── 4. Build the wheel from the sdist (pip offline, no build isolation) ────
# MATURIN_SETUP_ARGS is deliberately left unset: setup.py then uses its own
# default of `--no-default-features`, which drops upload/rustls/cross-compile/
# scaffolding and leaves the PEP-517 subset we actually need. Setting it would
# REPLACE that default, not extend it.
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
[ -n "${WHEEL}" ] || { echo "maturin-cp312: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "maturin-cp312: built $(basename "${WHEEL}")"

# ── 5. Install into THIS recipe's staging prefix ──────────────────────────
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
    echo "maturin-cp312: no site-packages under ${CVC_INSTALL_DIR} after pip install" >&2
    ls -la "${CVC_INSTALL_DIR}" >&2 || true
    exit 1
}
echo "maturin-cp312: staged into ${SITE_PACKAGES}"

command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── 6. Verification ────────────────────────────────────────────────────────
# Two separate claims to prove: the EXECUTABLE was built and runs, and the SHIM
# is importable by this column's interpreter. A consumer's PEP-517 build needs
# both, and either one can be missing on its own — a wheel that installed the
# Python package but dropped the .data/scripts/ entry looks fine until
# cryptography tries to run `maturin`.
MATURIN_EXE=""
for _cand in "${CVC_INSTALL_DIR}"/bin/maturin "${CVC_INSTALL_DIR}"/Scripts/maturin.exe; do
    if [ -x "${_cand}" ]; then MATURIN_EXE="${_cand}"; break; fi
done
[ -n "${MATURIN_EXE}" ] || {
    echo "maturin-cp312: no maturin executable staged under ${CVC_INSTALL_DIR}" >&2
    ls -la "${CVC_INSTALL_DIR}"/bin "${CVC_INSTALL_DIR}"/Scripts 2>&1 | head -40 >&2 || true
    exit 1
}
echo "maturin-cp312: staged executable ${MATURIN_EXE}"
"${MATURIN_EXE}" --version

export PYTHONPATH="${SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"
PYARGS=()
if cvc_python_is_free_threaded; then
    export PYTHON_GIL=0
    PYARGS+=(-X gil=0)
fi
MATURIN_EXE="${MATURIN_EXE}" "${PY}" ${PYARGS[@]+"${PYARGS[@]}"} - <<'PYCHECK'
import os, subprocess, sys, sysconfig

if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "GIL re-enabled at runtime; no-GIL support unproven"
    print("GIL disabled:", not sys._is_gil_enabled())

# The PEP-517 backend surface pip will actually call on a consumer.
import maturin
for hook in ("build_wheel", "build_sdist", "get_requires_for_build_wheel",
             "prepare_metadata_for_build_wheel"):
    assert hasattr(maturin, hook), f"maturin backend is missing {hook}"
print("maturin shim ->", maturin.__file__)

# The bootstrap hook must be inert here: if it ever returns puccinialin, a
# consumer's --no-build-isolation build fails with a missing build requirement
# instead of using the toolchain we staged.
from maturin import bootstrap
os.environ["MATURIN_NO_INSTALL_RUST"] = "1"
assert bootstrap.get_requires_for_build_wheel() == [], \
    "maturin bootstrap still wants an out-of-graph rust installer"

exe = os.environ["MATURIN_EXE"]
out = subprocess.run([exe, "--version"], capture_output=True, text=True, check=True).stdout
print("maturin exe  ->", exe, "|", out.strip())
assert "maturin" in out, out
print("maturin round-trip OK")
PYCHECK

echo "maturin-cp312: build + verification complete"
