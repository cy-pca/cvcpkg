#!/usr/bin/env bash
# recipes/cuda-toolkit/build.sh — lay out the NVIDIA CUDA toolkit (nvcc, cudart,
# CCCL) from NVIDIA's own redist archives.
#
# This recipe is `redistributable: false`: it runs on the machine that installs
# it (`cvcpkg install cuda-toolkit` builds it from this recipe), never on the
# builder fleet, and cvcpkg refuses to pack it.  Every archive comes straight
# from developer.download.nvidia.com and is checked against redist.lock before
# it is unpacked.  The NVIDIA files are copied, never edited: the toolkit is
# relocatable as shipped (no RUNPATH anywhere, and nvcc.profile resolves
# everything from nvcc's own directory).
#
# Layout — the standard /usr/local/cuda tree, kept in its own subdirectory so
# ~1700 CUDA headers (thrust, cub, cuda/, nv/) do not land in the shared
# include/, and so nvcc.profile works unmodified:
#
#   cuda/bin/nvcc (+ ptxas, fatbinary, nvlink, cudafe++, bin2c, nvcc.profile)
#   cuda/nvvm/{bin/cicc,lib64/libnvvm.so*,libdevice}
#   cuda/include/                     cudart, CCCL, crt, profiler headers
#   cuda/lib64/                       libcudart*, libcudadevrt.a, stubs/libcuda.so,
#                                     libnvptxcompiler_static.a, cmake/ (CCCL)
#   cuda/version.json, cuda/EULA.txt
#   bin/nvcc                          wrapper, so PATH / CMAKE_PREFIX_PATH find nvcc
#   lib/pkgconfig/cuda{,rt}-12.9.pc   ${pcfiledir}-relative
#
# Consumers: -DCMAKE_PREFIX_PATH=<prefix> is enough for enable_language(CUDA)
# and find_package(CUDAToolkit); CUDAToolkit_ROOT=<prefix>/cuda and
# CMAKE_CUDA_COMPILER=<prefix>/cuda/bin/nvcc also work.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

RECIPE_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK="${RECIPE_DIR}/redist.lock"
BASE="https://developer.download.nvidia.com/compute/cuda/redist"

# NVIDIA's platform keys.  linux-sbsa (aarch64 servers) is published too; it
# needs only a redist.lock entry set (scripts/cuda_redist_lock.py) and a
# matching case here.
case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) KEY="linux-x86_64" ;;
    *)
        echo "cuda-toolkit: no NVIDIA redist entries for $(uname -s)-$(uname -m) in redist.lock" >&2
        exit 1
        ;;
esac

RELEASE="$(awk '$1 == "release" { print $2 }' "${LOCK}")"
[ -n "${RELEASE}" ] || { echo "cuda-toolkit: redist.lock has no release line" >&2; exit 1; }
MAJMIN="${RELEASE%.*}"

# Downloads are cached by sha256, in the same directory and under the same
# switch as cvcpkg's own source cache (CVCPKG_SOURCE_CACHE_DIR; set it empty to
# disable), so a CI job can cache it and a reinstall does not refetch ~55 MB.
if [ -n "${CVCPKG_SOURCE_CACHE_DIR+set}" ]; then
    CACHE="${CVCPKG_SOURCE_CACHE_DIR}"
else
    CACHE="${XDG_CACHE_HOME:-${HOME}/.cache}/cvcpkg/sources"
fi

ROOT="${CVC_INSTALL_DIR}/cuda"
EXTRACT="${CVC_BUILD_DIR}/redist"
rm -rf "${EXTRACT}"
mkdir -p "${EXTRACT}" "${ROOT}/lib64"

# Merge the tree $1 into $2, failing if two archives disagree about a file.
# Archives are disjoint today; a release that moves a header from one
# component to another must not silently pick whichever was unpacked last.
merge_into() {
    local src="$1" dst="$2" rel
    mkdir -p "${dst}"
    while IFS= read -r rel; do
        if [ -e "${dst}/${rel}" ] || [ -L "${dst}/${rel}" ]; then
            if ! cmp -s "${src}/${rel}" "${dst}/${rel}"; then
                echo "cuda-toolkit: ${rel#./} differs between components" >&2
                exit 1
            fi
        fi
    done < <(cd "${src}" && find . \( -type f -o -type l \) -print)
    cp -a "${src}/." "${dst}/"
}

components_json=""
while read -r plat comp ver sha path name; do
    [ "${plat}" = "${KEY}" ] || continue
    file="${path##*/}"
    archive="${CACHE}/${sha}.tar.xz"
    if [ -n "${CACHE}" ] && [ -f "${archive}" ] \
        && [ "$(sha256sum "${archive}" | awk '{print $1}')" = "${sha}" ]; then
        echo "cuda-toolkit: ${comp} ${ver} (cached)"
    else
        archive="${CVC_BUILD_DIR}/${file}"
        echo "cuda-toolkit: downloading ${BASE}/${path}"
        curl -fsSL --retry 3 -o "${archive}" "${BASE}/${path}"
        actual="$(sha256sum "${archive}" | awk '{print $1}')"
        if [ "${actual}" != "${sha}" ]; then
            echo "cuda-toolkit: sha256 mismatch for ${file}: expected ${sha}, got ${actual}" >&2
            exit 1
        fi
        if [ -n "${CACHE}" ]; then
            mkdir -p "${CACHE}"
            cp "${archive}" "${CACHE}/${sha}.tar.xz"
        fi
    fi

    tar -xJf "${archive}" -C "${EXTRACT}"
    top="${EXTRACT}/${file%.tar.xz}"
    [ -d "${top}" ] || { echo "cuda-toolkit: ${file} did not unpack to ${top##*/}/" >&2; exit 1; }
    for entry in "${top}"/*; do
        case "${entry##*/}" in
            bin | include | nvvm) merge_into "${entry}" "${ROOT}/${entry##*/}" ;;
            lib) merge_into "${entry}" "${ROOT}/lib64" ;;
            # The same EULA ships in every archive.
            LICENSE) cp "${entry}" "${ROOT}/EULA.txt" ;;
            # cudart's .pc files hardcode /usr/local/cuda-X.Y; replaced below.
            pkg-config) ;;
            *)
                echo "cuda-toolkit: unexpected ${entry##*/} in ${file}; teach build.sh where it goes" >&2
                exit 1
                ;;
        esac
    done
    components_json="${components_json},
   \"${comp}\" : {
      \"name\" : \"${name}\",
      \"version\" : \"${ver}\"
   }"
done < <(grep -v '^#' "${LOCK}")

# version.json in NVIDIA's installer format: FindCUDAToolkit reads it (it is
# also its sentinel when nvcc is not executed), and it records exactly which
# component builds this tree holds.
cat > "${ROOT}/version.json" <<EOF
{
   "cuda" : {
      "name" : "CUDA SDK",
      "version" : "${RELEASE}"
   }${components_json}
}
EOF

# nvcc finds its headers, libraries and cicc relative to its own directory
# (nvcc.profile's _HERE_), which a symlink would point at <prefix>/bin.  Exec
# the real binary instead; CMake parses `nvcc -v` and lands on cuda/.
mkdir -p "${CVC_INSTALL_DIR}/bin"
cat > "${CVC_INSTALL_DIR}/bin/nvcc" <<'EOF'
#!/bin/sh
# cvcpkg cuda-toolkit: run the real nvcc, which lives in ../cuda/bin.
exec "$(dirname "$(readlink -f "$0")")/../cuda/bin/nvcc" "$@"
EOF
chmod 755 "${CVC_INSTALL_DIR}/bin/nvcc"

# cuda-X.Y.pc links the driver stub: the real libcuda.so.1 comes with the
# driver at run time, and a build host need not have one.
mkdir -p "${CVC_INSTALL_DIR}/lib/pkgconfig"
write_pc() {
    cat > "${CVC_INSTALL_DIR}/lib/pkgconfig/$1-${MAJMIN}.pc" <<EOF
cudaroot=\${pcfiledir}/../../cuda
libdir=\${cudaroot}/lib64
includedir=\${cudaroot}/include

Name: $1
Description: $2
Version: ${MAJMIN}
Libs: -L\${libdir}$3 -l$1
Cflags: -I\${includedir}
EOF
}
write_pc cuda "CUDA Driver Library" /stubs
write_pc cudart "CUDA Runtime Library" ""

for f in bin/nvcc bin/nvcc.profile bin/ptxas bin/fatbinary bin/nvlink nvvm/bin/cicc \
    nvvm/libdevice/libdevice.10.bc include/cuda_runtime.h include/cuda.h \
    include/crt/host_config.h include/thrust/version.h include/nv/target \
    lib64/libcudart_static.a lib64/libcudadevrt.a lib64/stubs/libcuda.so; do
    [ -e "${ROOT}/${f}" ] || { echo "cuda-toolkit: ${f} missing from the staged toolkit" >&2; exit 1; }
done
"${ROOT}/bin/nvcc" --version
"${CVC_INSTALL_DIR}/bin/nvcc" --version | tail -n 1
echo "cuda-toolkit: CUDA ${RELEASE} staged under ${ROOT}"
