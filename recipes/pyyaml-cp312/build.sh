#!/usr/bin/env bash
# recipes/pyyaml-cp312/build.sh — build PyYAML 6.0.3 FROM SOURCE for the cp312
# interpreter column of the per-interpreter wheel matrix.
#
# WHY FROM SOURCE (not the PyPI wheel): the published PyYAML wheel bundles its own
# statically linked libyaml, and PyPI has no FreeBSD/OpenBSD/NetBSD wheel at all —
# that absence is the only reason pyyaml was missing on the BSDs. Building here
# links cvcpkg's OWN libyaml (recipes/yaml) out of the merged prefix, and makes
# the C loader mandatory instead of a silent best-effort (see step 3).
#
# MECHANIC: `pip wheel --no-build-isolation --no-deps --no-index <sdist>` with the
# PEP-517 backend already provisioned into the prefix by the depends.build edges,
# then pip-install the resulting wheel into this recipe's staging prefix so the
# dist-info/RECORD/METADATA are real and pip's resolver later sees PyYAML as
# satisfied. This script does NOT re-solve the build closure — it assumes the
# declared deps are present and fails loudly when an import or a probe is missing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # toolchain, CVC_JOBS
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
echo "pyyaml-cp312: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# cvc_python_exe runs the interpreter from CVC_DEPS_PREFIX, which imports only its
# OWN site-packages. depends.build python columns land in CVC_BUILD_PREFIX, so
# --no-build-isolation cannot import them without this PYTHONPATH bridge — and the
# bridge is also what lets setuptools 80.x win over the 65.5 the base
# interpreter bundles, because PYTHONPATH precedes site-packages in sys.path.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import setuptools; print("pyyaml-cp312: setuptools", setuptools.__version__, setuptools.__file__)'

# ── 3. Require libyaml, and tell the build where ours is ─────────────────
# Two separate upstream defaults have to be overridden here.
#
# (a) DISCOVERY. setup.py declares the extension as `libraries=['yaml']` with no
#     include_dirs at all, i.e. it expects yaml.h on the default system search
#     path. Ours is in the cvcpkg prefix, so hand the paths in through PyYAML's
#     own PEP-517 config-setting (`pyyaml_build_config`, read by its build_ext in
#     finalize_options). That is used in preference to CFLAGS/LDFLAGS because it
#     is compiler-agnostic — the same key works for cl.exe in build.ps1 — and
#     because it cannot be clobbered by a matrix `env:` entry.
#
# (b) OPTIONALITY. Without PYYAML_FORCE_LIBYAML, build_ext.build_extensions()
#     swallows CompileError/LinkError and logs "falling back to pure Python". The
#     wheel then installs perfectly and has no yaml.CLoader — a regression that is
#     invisible until some consumer's `from yaml import CSafeLoader` explodes at
#     runtime. Setting it to 1 flips ext_status() to a hard requirement, so a
#     missing or unlinkable libyaml fails the build instead of the consumer.
#     We require it on every platform: "sometimes fast, depending on what the
#     builder happened to have installed" is the non-determinism we are removing.
export PYYAML_FORCE_LIBYAML=1

if [ ! -f "${DEPS}/include/yaml.h" ] && [ ! -f "${BLD}/include/yaml.h" ]; then
    echo "pyyaml-cp312: yaml.h not found in ${DEPS}/include or ${BLD}/include" >&2
    echo "  — is the 'yaml' (libyaml) dep in the closure?" >&2
    exit 1
fi

# Cython is not optional either: the sdist ships yaml/_yaml.pyx and NO generated
# _yaml.c, so without it setup.py rewrites the source list to a file that does not
# exist. Assert it up front so the failure names the missing dep.
"${PY}" -c 'import Cython; print("pyyaml-cp312: Cython", Cython.__version__)'

PYYAML_CFG="$(printf '{"include_dirs": ["%s", "%s"], "library_dirs": ["%s", "%s"]}' \
    "${DEPS}/include" "${BLD}/include" "${DEPS}/lib" "${BLD}/lib")"
echo "pyyaml-cp312: pyyaml_build_config=${PYYAML_CFG}"

# ── 4. Build the wheel from the sdist (offline, no build isolation) ────────
# --no-deps so pip never reaches past cvcpkg's graph; --no-index so it cannot
# resolve anything over the network (this is what makes air-gapped builds work);
# --no-build-isolation so the backend uses the prefix's setuptools rather than
# downloading its own.
WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY}" -m pip wheel \
    --no-deps \
    --no-build-isolation \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    --config-settings "pyyaml_build_config=${PYYAML_CFG}" \
    "${CVC_SOURCE_DIR}"

# Plain glob rather than `find -print -quit`: OpenBSD's find has no -quit, and a
# `find | head` pipeline trips `set -o pipefail` when head closes the pipe early.
shopt -s nullglob
_wheels=( "${WHEELHOUSE}"/*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "pyyaml-cp312: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "pyyaml-cp312: built $(basename "${WHEEL}")"

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

# Locate the staged site-packages by globbing the known install schemes (same
# reason as above: no `find -quit`). An unmatched glob stays literal, so the
# `-d` test simply fails for it.
SITE_PACKAGES=""
for _cand in "${CVC_INSTALL_DIR}"/lib/python*/site-packages \
             "${CVC_INSTALL_DIR}"/lib64/python*/site-packages \
             "${CVC_INSTALL_DIR}"/Lib/site-packages; do
    if [ -d "${_cand}" ]; then SITE_PACKAGES="${_cand}"; break; fi
done
[ -n "${SITE_PACKAGES}" ] || {
    echo "pyyaml-cp312: no site-packages under ${CVC_INSTALL_DIR} after pip install" >&2
    ls -la "${CVC_INSTALL_DIR}" >&2 || true
    exit 1
}
echo "pyyaml-cp312: staged into ${SITE_PACKAGES}"

# ── 6. Make the extension relocatable ──────────────────────────────────────
# The wheel's extension links libyaml out of the build-time ${DEPS}/lib. At the
# consumer, pyyaml and libyaml land in ONE merged prefix, so the correct search path
# is $ORIGIN-relative to <prefix>/lib. Compute that relative path PER FILE rather
# than hard-coding a depth, and keep a bare $ORIGIN so sibling extensions resolve.
#
# The relative path is computed with the interpreter, not `realpath
# --relative-to`: that flag is GNU coreutils-only and the BSD realpath(1) this
# script also runs under does not have it.
_relpath_to_lib() {
    "${PY}" - "$1" "${CVC_INSTALL_DIR}/lib" <<'PYREL'
import os, sys
print(os.path.relpath(sys.argv[2], os.path.dirname(sys.argv[1])))
PYREL
}

if [ "${CVC_PLATFORM}" = "macos" ]; then
    # Mach-O: repoint any load command still pointing into our build prefixes at
    # @rpath/<name>, then add a @loader_path-relative rpath to reach lib/.
    command -v install_name_tool >/dev/null 2>&1 || {
        echo "pyyaml-cp312: install_name_tool required on macOS but not found" >&2; exit 1; }
    while IFS= read -r -d '' _so; do
        _rel="$(_relpath_to_lib "${_so}")"
        chmod u+w "${_so}" 2>/dev/null || true
        while IFS= read -r _dep; do
            case "${_dep}" in
                "${DEPS}"/*|"${BLD}"/*)
                    install_name_tool -change "${_dep}" \
                        "@rpath/$(basename "${_dep}")" "${_so}" 2>/dev/null || true
                    ;;
            esac
        done < <(otool -L "${_so}" | awk 'NR>1 {print $1}')
        install_name_tool -add_rpath "@loader_path/${_rel}" "${_so}" 2>/dev/null || true
        install_name_tool -add_rpath "@loader_path"          "${_so}" 2>/dev/null || true
    done < <(find "${SITE_PACKAGES}/yaml" -name '*.so' -print0)
else
    # ELF (Linux/BSD): overwrite RUNPATH with a purely $ORIGIN-relative one so the
    # bundle relocates. patchelf writes literal bytes (no shell $ORIGIN escaping),
    # and is a REQUIRED host tool here — declared in recipe.yaml host_tools.
    #
    # Caveat, deliberately not worked around: OpenBSD's ld.so does not implement
    # $ORIGIN (cvcpkg's own _ELF_RPATH_PLATFORMS excludes openbsd for exactly this
    # reason), so on that platform the entry is inert and libyaml is found the way
    # every other OpenBSD bundle finds its siblings — via the activated prefix's
    # library path. Stamping it anyway costs nothing and is right everywhere else.
    command -v patchelf >/dev/null 2>&1 || {
        echo "pyyaml-cp312: patchelf required on ${CVC_PLATFORM} but not on PATH" >&2; exit 1; }
    while IFS= read -r -d '' _so; do
        _rel="$(_relpath_to_lib "${_so}")"
        patchelf --set-rpath "\$ORIGIN:\$ORIGIN/${_rel}" "${_so}"
    done < <(find "${SITE_PACKAGES}/yaml" -name '*.so' -print0)
fi

command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── 7. Verification ────────────────────────────────────────────────────────
# Prove the compiled extension loads AND drives libyaml end to end — an
# import alone would not distinguish a working link from a lucky one.
export PYTHONPATH="${SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"
# The staged extension carries a prefix-relative RUNPATH that does NOT resolve at
# this build-time layout (libyaml is in the deps prefix, not our staging lib/), so
# widen the loader path just for the check.
_LOADPATH="${DEPS}/lib${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib}"
if [ "${CVC_PLATFORM}" = "macos" ]; then
    export DYLD_LIBRARY_PATH="${_LOADPATH}${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
else
    export LD_LIBRARY_PATH="${_LOADPATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi
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

import yaml

# The whole reason this column links libyaml. If setup.py had fallen back to the
# pure-Python parser, __with_libyaml__ would be False and CSafeLoader absent —
# an invisible regression for every consumer that asks for the fast loader.
assert yaml.__with_libyaml__ is True, "PyYAML was built WITHOUT libyaml"
from yaml import _yaml
assert _yaml.__file__.endswith((".so", ".pyd", ".dylib")), _yaml.__file__
print("PyYAML", yaml.__version__, "->", _yaml.__file__)

doc = "a: 1\nb: [x, y]\nc: {d: true}\n"
loaded = yaml.load(doc, Loader=yaml.CSafeLoader)
assert loaded == {"a": 1, "b": ["x", "y"], "c": {"d": True}}, loaded

# Round-trip through the C emitter as well as the C parser.
dumped = yaml.dump(loaded, Dumper=yaml.CSafeDumper, default_flow_style=False)
assert yaml.load(dumped, Loader=yaml.CSafeLoader) == loaded

# The unsafe/full loaders must still be the C ones.
assert yaml.CLoader is not None and yaml.CDumper is not None
print("pyyaml round-trip OK (libyaml", ".".join(str(v) for v in _yaml.get_version()) + ")")
PYCHECK

echo "pyyaml-cp312: build + verification complete"
