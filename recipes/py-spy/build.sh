#!/usr/bin/env bash
# recipes/py-spy/build.sh — stage py-spy's prebuilt Rust binary.
#
# cvcpkg core has already downloaded and sha256-verified this platform's
# wheel into $CVC_SOURCE_DIR (source.type python_wheel). The wheel holds no
# Python module, only the binary and its dist-info, so it is unpacked with the
# build interpreter's zipfile module rather than pip-installed: the bundle
# gets bin/py-spy and the license texts, nothing under lib/python3.11.
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"

: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
VER="0.4.2"

wheel=""
for wheel in "${CVC_SOURCE_DIR}"/py_spy-"${VER}"-*.whl; do [ -e "${wheel}" ] && break; wheel=""; done
[ -n "${wheel}" ] || { echo "no py_spy-${VER} wheel in ${CVC_SOURCE_DIR}" >&2; exit 1; }

unpacked="${CVC_BUILD_DIR}/wheel"
rm -rf "${unpacked}"
"$(cvc_python_exe)" -I -m zipfile -e "${wheel}" "${unpacked}"

# zipfile drops the mode bits, so install sets them.
install -d "${CVC_INSTALL_DIR}/bin" "${CVC_INSTALL_DIR}/share/licenses/py-spy"
install -m 0755 "${unpacked}/py_spy-${VER}.data/scripts/py-spy" "${CVC_INSTALL_DIR}/bin/py-spy"
# The binary statically links its Rust crates; upstream's SBOM lists each one
# with its license, so it travels with the binary beside py-spy's own LICENSE.
install -m 0644 "${unpacked}/py_spy-${VER}.dist-info/licenses/LICENSE" \
    "${unpacked}/py_spy-${VER}.dist-info/sboms/py-spy.cyclonedx.json" \
    "${CVC_INSTALL_DIR}/share/licenses/py-spy/"

"${CVC_INSTALL_DIR}/bin/py-spy" --version
