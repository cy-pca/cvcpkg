#!/usr/bin/env bash
# recipes/_common/python-wheel.sh — per-interpreter wheel helper (Phase 7).
#
# cvcpkg ships several CPython interpreters as recipes (python311, python312,
# python313, python313t).  A wheel package is therefore not one recipe but a
# *matrix*: one recipe per interpreter ABI, each depending on its interpreter
# and installing into that interpreter's own site-packages inside the prefix.
#
# The wheel itself is fetched and sha256-verified by cvcpkg core (source.type
# python_wheel) and handed to the build script already on disk, so the install
# below runs fully offline (--no-index) and never re-resolves a pin.
#
# Usage in a wheel recipe's build.sh:
#
#     #!/usr/bin/env bash
#     set -euo pipefail
#     . "$(dirname "$0")/../_common/python-wheel.sh"
#     cvc_pip_install_wheel
#     cvc_python_check "import numpy; numpy.add(1, 2)"
#
# The recipe's `python:` block supplies CVC_PYTHON_ABI / CVC_PYTHON_INTERPRETER
# (exported by the builder from the recipe); they can also be set directly.

set -euo pipefail

# Map a cvcpkg interpreter recipe name to the X.Y[t] version it reports.
#
#   python311  -> 3.11      python313t -> 3.13t
#
# This is authoritative even for an abi3 recipe, whose `abi` tag (abi3) carries
# no version: the interpreter a wheel is *installed under* is named by the
# recipe's `interpreter:` field, not by the ABI tag.
cvc_interp_version() {
  local interp="${1:?usage: cvc_interp_version <pythonNNN[t]>}"
  local digits="${interp#python}"    # python313t -> 313t
  local suffix=""
  case "${digits}" in
    *t) suffix="t"; digits="${digits%t}" ;;
  esac
  echo "${digits:0:1}.${digits:1}${suffix}"   # 313 -> 3.13(t)
}

# Echo the prefix interpreter executable for an X.Y[t] version.
cvc_python_exe_for() {
  local ver="${1:?usage: cvc_python_exe_for <X.Y[t]>}"
  : "${CVC_DEPS_PREFIX:?CVC_DEPS_PREFIX must be set}"
  local exe="${CVC_DEPS_PREFIX}/bin/python${ver}"
  if [ ! -x "${exe}" ]; then
    echo "cvc_python_exe_for: interpreter not found: ${exe}" >&2
    echo "  (does this recipe depend on python${ver//./}?)" >&2
    return 1
  fi
  echo "${exe}"
}

# Echo the target interpreter's executable inside $CVC_DEPS_PREFIX.
#
# The interpreter to install *under* is the recipe's `interpreter:` field
# (CVC_PYTHON_INTERPRETER) — resolved against the *prefix* interpreter, never
# the host's. For an ordinary cpNN recipe this equals the ABI's version; for an
# abi3 recipe (abi tag carries no version) it is the min interpreter the recipe
# builds under, from which cvc_noarch_fanout stages the stable-ABI .so upward.
cvc_python_exe() {
  : "${CVC_PYTHON_INTERPRETER:?CVC_PYTHON_INTERPRETER must be set (recipe python.interpreter)}"
  cvc_python_exe_for "$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
}

# True when the target ABI is the GIL-disabled one.
cvc_python_is_free_threaded() {
  case "${CVC_PYTHON_ABI:-}" in
    *t) return 0 ;;
    *)  return 1 ;;
  esac
}

# Install the verified wheel into the prefix interpreter's site-packages.
#
# --no-deps:  transitive Python deps are themselves cvcpkg recipes, resolved by
#             the depends graph — pip must not go behind cvcpkg's back.
# --no-index: the artifact is already on disk and pinned; forbid any network
#             resolution, which is what makes air-gapped installs work.
cvc_pip_install_wheel() {
  : "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
  : "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

  local py wheel
  py="$(cvc_python_exe)"

  wheel="$(find "${CVC_SOURCE_DIR}" -maxdepth 1 -name '*.whl' -print -quit)"
  if [ -z "${wheel}" ]; then
    echo "cvc_pip_install_wheel: no .whl in ${CVC_SOURCE_DIR}" >&2
    return 1
  fi

  echo "installing $(basename "${wheel}") into ${CVC_INSTALL_DIR} using ${py}"
  "${py}" -m pip install \
    --no-deps \
    --no-index \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${wheel}"

  cvc_noarch_fanout
}

# Cross-interpreter fan-out for noarch (py3-none-any) wheels.
#
# pip installs a wheel only under its own interpreter's version dir
# (lib/pythonX.Y/site-packages). A py3-none-any wheel is valid on EVERY
# interpreter, but a package placed only in lib/python3.12 is invisible to
# python3.11/3.13 — so a noarch dependency can't be imported by a cp311/cp313
# build, and at runtime the package only works from python3.12.
#
# For a noarch recipe (builder sets CVC_PYTHON_NOARCH_FANOUT to the space-
# separated interpreter versions cvcpkg ships, e.g. "3.11 3.12 3.13 3.13t"),
# copy the just-installed package into every one of those interpreters'
# site-packages so it imports from all of them. A no-op for concrete
# (C-extension) recipes, where CVC_PYTHON_NOARCH_FANOUT is unset.
cvc_noarch_fanout() {
  [ -n "${CVC_PYTHON_NOARCH_FANOUT:-}" ] || return 0
  : "${CVC_INSTALL_DIR:?}"

  local src_sp v dst_sp
  src_sp="$(find "${CVC_INSTALL_DIR}" -maxdepth 3 -type d -name site-packages -print -quit)"
  if [ -z "${src_sp}" ]; then
    echo "cvc_noarch_fanout: no site-packages under ${CVC_INSTALL_DIR}" >&2
    return 1
  fi
  for v in ${CVC_PYTHON_NOARCH_FANOUT}; do
    dst_sp="${CVC_INSTALL_DIR}/lib/python${v}/site-packages"
    [ "${dst_sp}" = "${src_sp}" ] && continue
    mkdir -p "${dst_sp}"
    cp -a "${src_sp}/." "${dst_sp}/"
  done
  echo "noarch fan-out: staged into python ${CVC_PYTHON_NOARCH_FANOUT}"
}

# Echo the X.Y[t] version a wheel's ABI tag targets, from its filename.
#
#   foo-1.0-cp311-cp311-manylinux…whl  -> 3.11
#   foo-1.0-cp313-cp313t-manylinux…whl -> 3.13t   (free-threaded ABI)
#
# The ABI tag (the field after the Python tag) is authoritative: it, not the
# Python tag, distinguishes the free-threaded build.
cvc_wheel_abi_version() {
  local fn abi digits suffix
  fn="$(basename "${1:?usage: cvc_wheel_abi_version <wheel>}")"
  # Every cpNN-cpNN pair in the name is identical except free-threaded
  # (cp313-cp313t); take the last match so the trailing `t` wins.
  abi="$(printf '%s\n' "${fn}" | grep -oE 'cp3[0-9]{2}t?' | tail -1)"
  [ -n "${abi}" ] || { echo "cvc_wheel_abi_version: no cpNN tag in ${fn}" >&2; return 1; }
  digits="${abi#cp}"
  suffix=""
  case "${digits}" in
    *t) suffix="t"; digits="${digits%t}" ;;
  esac
  echo "${digits:0:1}.${digits:1}${suffix}"
}

# Per-version binary fan-out for a true per-interpreter C-extension package.
#
# Unlike a noarch (or stable-ABI) wheel — one payload copied into every
# interpreter's site-packages by cvc_noarch_fanout — a per-version extension
# ships a *distinct* binary per ABI (cp311 ≠ cp312 ≠ cp313), so a cp312 .so
# cannot load under 3.11. This installs EACH pinned wheel present in
# CVC_SOURCE_DIR into the site-packages of the interpreter matching its own ABI
# tag, so one package works from every interpreter cvcpkg ships.
#
# The recipe must depend (build) on each interpreter it carries a wheel for.
cvc_pip_install_wheels_fanout() {
  : "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
  : "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

  local wheel ver py n=0
  shopt -s nullglob
  for wheel in "${CVC_SOURCE_DIR}"/*.whl; do
    ver="$(cvc_wheel_abi_version "${wheel}")" || return 1
    py="$(cvc_python_exe_for "${ver}")" || return 1
    echo "installing $(basename "${wheel}") into ${CVC_INSTALL_DIR} using ${py}"
    "${py}" -m pip install \
      --no-deps \
      --no-index \
      --no-compile \
      --ignore-installed \
      --prefix "${CVC_INSTALL_DIR}" \
      "${wheel}"
    n=$((n + 1))
  done
  if [ "${n}" -eq 0 ]; then
    echo "cvc_pip_install_wheels_fanout: no .whl in ${CVC_SOURCE_DIR}" >&2
    return 1
  fi
  echo "per-version fan-out: installed ${n} wheel(s)"
}

# Run a snippet under EVERY interpreter a per-version fan-out installed into,
# each with only its own staged site-packages importable. Proves the right ABI
# landed in the right interpreter's tree.
cvc_python_check_each() {
  local snippet="${1:?usage: cvc_python_check_each <python-snippet>}"
  : "${CVC_SOURCE_DIR:?}"; : "${CVC_INSTALL_DIR:?}"

  local wheel ver py sp n=0
  shopt -s nullglob
  for wheel in "${CVC_SOURCE_DIR}"/*.whl; do
    ver="$(cvc_wheel_abi_version "${wheel}")" || return 1
    py="$(cvc_python_exe_for "${ver}")" || return 1
    sp="${CVC_INSTALL_DIR}/lib/python${ver}/site-packages"
    if [ ! -d "${sp}" ]; then
      echo "cvc_python_check_each: expected staged site-packages ${sp}" >&2
      return 1
    fi
    echo "verifying cp${ver//./} under ${py}"
    PYTHONPATH="${sp}" "${py}" -c "
${snippet}
print('cp${ver//./} check OK')
"
    n=$((n + 1))
  done
  [ "${n}" -gt 0 ] || { echo "cvc_python_check_each: no wheels to check" >&2; return 1; }
}
# --ignore-installed: stage the wheel into CVC_INSTALL_DIR unconditionally. Without
# it, pip skips a package that is already present on the interpreter's path at the
# same version ("already installed with the same version ... Use --force-reinstall"),
# leaving CVC_INSTALL_DIR/site-packages empty so cvc_python_check then fails with
# "no site-packages found". We always want the pinned wheel in the staged prefix.

# Run a Python snippet under the target interpreter, with the wheel we just
# installed importable.
#
# For a free-threaded ABI this asserts the GIL is actually *disabled* at
# runtime (-X gil=0 / PYTHON_GIL=0) before running the snippet.  That check is
# the substance of the no-GIL claim: a cp313t wheel that only imports under a
# re-enabled GIL has not been shown to work without one, and cvcpkg would be
# publishing an unproven guarantee.  If CPython refuses to disable the GIL
# (e.g. an extension on the prefix forced it back on), the build fails here.
cvc_python_check() {
  local snippet="${1:?usage: cvc_python_check <python-snippet>}"
  local py libdir
  py="$(cvc_python_exe)"

  # sysconfig would report the *interpreter's* own prefix, not the staging dir
  # we just installed into, so locate the staged site-packages directly.  The
  # find covers both layouts: lib/pythonX.Yt/site-packages and Lib/site-packages.
  libdir="$(find "${CVC_INSTALL_DIR}" -maxdepth 3 -type d -name 'site-packages' -print -quit)"
  if [ -z "${libdir}" ]; then
    echo "cvc_python_check: no site-packages found under ${CVC_INSTALL_DIR}" >&2
    return 1
  fi
  export PYTHONPATH="${libdir}${PYTHONPATH:+:${PYTHONPATH}}"

  if cvc_python_is_free_threaded; then
    echo "verifying ${CVC_PYTHON_ABI} with the GIL disabled"
    PYTHON_GIL=0 "${py}" -X gil=0 -c "
import sys, sysconfig
if not sysconfig.get_config_var('Py_GIL_DISABLED'):
    sys.exit('${CVC_PYTHON_ABI}: interpreter is not a free-threaded build')
if sys._is_gil_enabled():
    sys.exit('${CVC_PYTHON_ABI}: GIL was re-enabled at runtime; no-GIL support unproven')
print('GIL disabled:', not sys._is_gil_enabled())
${snippet}
print('${CVC_PYTHON_ABI} check OK (GIL disabled)')
"
  else
    "${py}" -c "
${snippet}
print('${CVC_PYTHON_ABI} check OK')
"
  fi
}
