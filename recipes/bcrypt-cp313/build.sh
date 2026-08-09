#!/usr/bin/env bash
# recipes/bcrypt-cp313/build.sh — build bcrypt 5.0.0 FROM SOURCE for the cp313
# interpreter column of the per-interpreter wheel matrix.
#
# WHY FROM SOURCE: not to unvendor anything — _bcrypt is a pyo3 cdylib whose KDF
# is pure Rust and which links nothing but libc — but because PyPI publishes no
# FreeBSD or NetBSD wheel, so password hashing was unavailable on those
# platforms. Compiling the sdist is the only route, and it needs cargo on the
# target, which recipes/rust gained in rust+cvc.2.
#
# HOW THIS ONE DIFFERS FROM ITS SIBLINGS: cryptography and pydantic-core go
# through maturin. bcrypt does not — it declares `setuptools.build_meta` and
# describes its extension declaratively in pyproject.toml via
# [[tool.setuptools-rust.ext-modules]] (there is no setup.py in the sdist). So
# the plugin that shells out to cargo is setuptools-rust, and the build closure
# is the shorter one: setuptools + setuptools-rust + wheel + rust.
#
# `py-limited-api = "auto"` in that same block is what makes the ABI choice: the
# stable ABI on a GIL-ful interpreter, and the version-specific ABI on a
# free-threaded one (CPython 3.13t has no stable ABI). That is why the cp313t
# column of this recipe is legitimate while cryptography has none.
#
# MECHANIC: `pip wheel --no-build-isolation --no-deps --no-index <sdist>` with the
# backend already provisioned into the prefix by the depends.build edges, then
# pip-install the resulting wheel into this recipe's staging prefix so the
# dist-info/RECORD/METADATA are real and pip's resolver later sees bcrypt as
# satisfied. This script does NOT re-solve the build closure — it assumes the
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
: "${CVC_PYTHON_ABI:=abi3}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
# cvc_interp_version keeps the free-threaded suffix (python313t -> 3.13t), which
# is also the site-packages directory name — do not re-derive it from the ABI
# tag, which for the GIL-ful columns is the versionless `abi3`.
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "bcrypt-cp313: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# cvc_python_exe runs the interpreter from CVC_DEPS_PREFIX, which imports only its
# OWN site-packages. depends.build python columns land in CVC_BUILD_PREFIX, so
# --no-build-isolation cannot import them without this PYTHONPATH bridge.
# ${BLD}/bin first on PATH is what makes OUR cargo win over anything the builder
# host has installed.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import setuptools, setuptools_rust, wheel; print("bcrypt-cp313: setuptools", setuptools.__version__, "+ setuptools_rust + wheel")'

# ── 3. Pin the Rust toolchain to the one cvcpkg built ──────────────────────
# The failure this guards against is silent and expensive: a builder with a
# personal rustup gets picked up, the artifact is compiled by an untracked
# toolchain, and nothing in the manifest says so. Resolve cargo/rustc and REFUSE
# anything that did not come out of the cvcpkg prefixes.
_require_prefix_tool() {
    local tool="$1" resolved
    resolved="$(command -v "${tool}" 2>/dev/null || true)"
    [ -n "${resolved}" ] || {
        echo "bcrypt-cp313: ${tool} not on PATH — is the 'rust' build dep in the closure?" >&2
        exit 1
    }
    case "${resolved}" in
        "${BLD}"/*|"${DEPS}"/*) echo "${resolved}" ;;
        *)
            echo "bcrypt-cp313: refusing ${tool} at ${resolved} — it is outside the cvcpkg" >&2
            echo "  prefixes (${BLD}, ${DEPS}). A system rustup must not build cvcpkg artifacts." >&2
            exit 1
            ;;
    esac
}
# The helper's `exit 1` only leaves its command substitution, so `set -e` is what
# actually aborts the build; assert non-empty too rather than relying on that.
CARGO_BIN="$(_require_prefix_tool cargo)"
RUSTC_BIN="$(_require_prefix_tool rustc)"
[ -n "${CARGO_BIN}" ] && [ -n "${RUSTC_BIN}" ] || exit 1
# setuptools_rust reads $CARGO (build.py: os.getenv("CARGO", "cargo")) and probes
# rustc's version through it, so setting these is what makes the pin stick rather
# than merely being decorative.
export CARGO="${CARGO_BIN}"
export RUSTC="${RUSTC_BIN}"
echo "bcrypt-cp313: cargo $("${CARGO_BIN}" --version) [${CARGO_BIN}]"
echo "bcrypt-cp313: rustc $("${RUSTC_BIN}" --version) [${RUSTC_BIN}]"

# Keep cargo's state inside the build tree: a $HOME/.cargo on the builder would
# otherwise be both an input (a stale or poisoned registry) and an output. The
# RUSTUP_* pins and the RUSTUP_TOOLCHAIN unset close the same door for a shim.
export CARGO_HOME="${CVC_CARGO_HOME:-${CVC_BUILD_DIR}/cargo-home}"
export RUSTUP_HOME="${CVC_BUILD_DIR}/rustup-home"
unset RUSTUP_TOOLCHAIN
mkdir -p "${CARGO_HOME}" "${RUSTUP_HOME}"
# Build artifacts out of CVC_SOURCE_DIR so the extracted sdist stays pristine.
export CARGO_TARGET_DIR="${CVC_BUILD_DIR}/cargo-target"

# The one non-hermetic step in this recipe, made explicit rather than implicit.
if [ -n "${CVC_CARGO_OFFLINE:-}" ]; then
    export CARGO_NET_OFFLINE=true
    echo "bcrypt-cp313: CARGO_NET_OFFLINE=true — ${CARGO_HOME} must already contain"
    echo "  every crate in Cargo.lock, or cargo will fail rather than fetch."
else
    export CARGO_NET_OFFLINE=false
    echo "bcrypt-cp313: NOTE — cargo will FETCH crates.io. This sdist ships"
    echo "  Cargo.lock but does not vendor crate sources, so the build needs network"
    echo "  access. Versions are lock-pinned. Set CVC_CARGO_HOME to a pre-seeded"
    echo "  registry plus CVC_CARGO_OFFLINE=1 to build air-gapped."
fi

# ── 4. Build the wheel from the sdist (pip offline, no build isolation) ────
# --no-deps so pip never reaches past cvcpkg's graph; --no-index so pip cannot
# resolve anything over the network; --no-build-isolation so the backend uses the
# prefix's setuptools/setuptools-rust rather than downloading its own.
#
# setuptools_rust sets PYO3_PYTHON to sys.executable for the cargo invocation, so
# pyo3 configures itself against THIS column's interpreter without further help —
# including detecting Py_GIL_DISABLED and dropping abi3 on the cp313t column.
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
[ -n "${WHEEL}" ] || { echo "bcrypt-cp313: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "bcrypt-cp313: built $(basename "${WHEEL}")"

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
    echo "bcrypt-cp313: no site-packages under ${CVC_INSTALL_DIR} after pip install" >&2
    ls -la "${CVC_INSTALL_DIR}" >&2 || true
    exit 1
}
echo "bcrypt-cp313: staged into ${SITE_PACKAGES}"

# No rpath pass here, deliberately: _bcrypt has no external NEEDED library to
# find (cryptography and cffi do, which is why they patchelf and this does not).
command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── 6. Verification ────────────────────────────────────────────────────────
# Prove the extension loads AND hashes — an import alone would not distinguish a
# working KDF from a broken one, and bcrypt is precisely the package where a
# quietly wrong answer is worst.
export PYTHONPATH="${SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"
# A free-threaded column must be proven WITHOUT the GIL: an extension that only
# imports under a re-enabled GIL has not been shown to work without one, and
# cvcpkg would be publishing an unproven guarantee (see _common/python-wheel.sh).
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

import bcrypt
from bcrypt import _bcrypt

assert _bcrypt.__file__.endswith((".so", ".pyd", ".dylib")), _bcrypt.__file__
print("bcrypt", bcrypt.__version__, "->", _bcrypt.__file__)

# Round-trip against a freshly generated salt: proves the Rust KDF runs.
# rounds=4 is the minimum bcrypt accepts and keeps the check cheap on a slow
# BSD builder; the algorithm exercised is the same at any cost factor.
pw = b"cvcpkg-correct-horse"
hashed = bcrypt.hashpw(pw, bcrypt.gensalt(rounds=4))
assert bcrypt.checkpw(pw, hashed), hashed
assert not bcrypt.checkpw(b"wrong", hashed)

# Shape assertions on the crypt string, which a round-trip alone would not
# catch: a bcrypt hash is "$2b$<cost>$" + 22 salt chars + 31 digest chars.
# NOTE this is deliberately NOT a cross-implementation known-answer vector — a
# hardcoded digest that nobody re-derived would be a guess, and a wrong guess
# would fail every build. Cross-checking against another bcrypt belongs in the
# recipe's test step, against a second implementation actually present there.
assert hashed.startswith(b"$2b$04$"), hashed
assert len(hashed) == 60, (len(hashed), hashed)
# Re-hashing with the SAME salt must reproduce the same digest — the property a
# broken or half-linked KDF fails.
assert bcrypt.hashpw(pw, hashed) == hashed

# kdf() is the bcrypt-pbkdf half of the crate (used for OpenSSH keys); exercise
# it too so a partially-linked extension does not pass. It must be deterministic
# and length-correct.
out = bcrypt.kdf(password=pw, salt=b"cvcpkg-salt", desired_key_bytes=32, rounds=4)
assert isinstance(out, bytes) and len(out) == 32, out
assert bcrypt.kdf(password=pw, salt=b"cvcpkg-salt", desired_key_bytes=32, rounds=4) == out
print("bcrypt round-trip OK")
PYCHECK

echo "bcrypt-cp313: build + verification complete"
