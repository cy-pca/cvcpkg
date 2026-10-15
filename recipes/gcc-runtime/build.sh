#!/usr/bin/env bash
# recipes/gcc-runtime/build.sh — GCC's shared runtime libraries, copied out of
# the gfortran package (a build dependency) rather than rebuilt.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"

# The gfortran on PATH must be cvcpkg's (its build prefix's bin/ comes first),
# not one the builder happens to have: the libraries have to match the GCC the
# fleet compiles with, and come from a build cvcpkg made.
_gfc=$(command -v gfortran || true)
if [[ -z "${_gfc}" ]] || ! "${_gfc}" --version | grep -q "cvcpkg gfortran"; then
    echo "gcc-runtime build.sh: cvcpkg's gfortran is not on PATH (found '${_gfc:-nothing}')" >&2
    exit 1
fi
"${_gfc}" --version | head -n 1

mkdir -p "${CVC_INSTALL_DIR}/lib"
# libquadmath exists only where GCC builds it, on x86 targets.  aarch64 has no
# __float128 runtime, so gfortran there has no libquadmath.so.0 (and
# libgfortran does not need one); linux/arm64 failed with "gfortran has no
# libquadmath.so.0" until it became optional.
_copied=()
for _soname in libgfortran.so.5 libquadmath.so.0 libgomp.so.1; do
    _found=$("${_gfc}" -print-file-name="${_soname}")
    if [[ "${_found}" != /* || ! -e "${_found}" ]]; then
        if [[ "${_soname}" == libquadmath.so.0 ]]; then
            echo "gcc-runtime build.sh: no libquadmath on $(uname -m); skipping it"
            continue
        fi
        echo "gcc-runtime build.sh: gfortran has no ${_soname} (got '${_found}')" >&2
        exit 1
    fi
    _real=$(readlink -f "${_found}")
    _file=$(basename "${_real}")                 # libgfortran.so.5.0.0
    cp "${_real}" "${CVC_INSTALL_DIR}/lib/${_file}"
    chmod 0755 "${CVC_INSTALL_DIR}/lib/${_file}"
    ln -sf "${_file}" "${CVC_INSTALL_DIR}/lib/${_soname}"
    # The unversioned link-time name, so `-lgfortran` / `-lquadmath` resolve
    # against the prefix (Boost::charconv exports `quadmath`).
    ln -sf "${_soname}" "${CVC_INSTALL_DIR}/lib/${_soname%%.so.*}.so"
    # libgfortran NEEDs libquadmath: siblings, found through $ORIGIN.
    patchelf --set-rpath '$ORIGIN' "${CVC_INSTALL_DIR}/lib/${_file}"
    _copied+=("${CVC_INSTALL_DIR}/lib/${_file}")
done

mkdir -p "${CVC_INSTALL_DIR}/share/licenses/gcc-runtime"
cp "${CVC_SOURCE_DIR}"/COPYING3 "${CVC_SOURCE_DIR}"/COPYING.RUNTIME \
    "${CVC_SOURCE_DIR}"/COPYING.LIB "${CVC_INSTALL_DIR}/share/licenses/gcc-runtime/"

# Each library must load on its own, from here, with nothing else on the path.
for _so in "${_copied[@]}"; do
    _ldd=$(env -u LD_LIBRARY_PATH ldd "${_so}")
    echo "${_ldd}"
    if grep -q "not found" <<<"${_ldd}"; then
        echo "gcc-runtime build.sh: $(basename "${_so}") has unresolved dependencies" >&2
        exit 1
    fi
done
