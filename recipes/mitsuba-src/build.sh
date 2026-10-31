#!/usr/bin/env bash
# recipes/mitsuba-src/build.sh -- stage Mitsuba 3.9.1's source tree, with the
# git submodules its scikit-build build reads, at src/mitsuba-src/ (a source
# package: see docs/source-recipes.md).
#
# cvcpkg fetched and verified the superproject's commit archive (recipe.yaml),
# in which each submodule is an empty directory.  Each submodule below is the
# GitHub archive of the commit its parent's gitlink pins (read with
# `git ls-tree <parent commit> <path>`), checked against the sha256 recorded
# here and unpacked into place, parents before children.  Moving any pin is a
# reviewable edit to this table.  ext/drjit is the same commit drjit-src
# stages (v1.5.0); under scikit-build Mitsuba builds against the installed
# drjit package and reads only cmake-defaults and headers from this copy.
set -euo pipefail
# shellcheck disable=SC1091
. "$(dirname "$0")/../_common/stage-source.sh"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

cvc_stage_source
dest="${CVC_INSTALL_DIR}/src/${CVC_COMPONENT}"
manifest="${dest}/cvcpkg-submodules.txt"

_sha256() {
    # Hash from stdin: a FILE operand with a backslash makes coreutils escape
    # its output (a Windows builder's paths).
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum < "$1" | awk '{print $1}'
    else
        shasum -a 256 < "$1" | awk '{print $1}'
    fi
}

# _fetch <owner/repo> <commit> <sha256>: download and verify; echoes the path.
_fetch() {
    local repo="$1" commit="$2" want="$3" archive got
    archive="${CVC_BUILD_DIR}/$(printf '%s' "${repo}" | tr '/' '_')-${commit}.tar.gz"
    curl -fsSL --retry 3 -o "${archive}" "https://github.com/${repo}/archive/${commit}.tar.gz"
    got="$(_sha256 "${archive}")"
    if [ "${got}" != "${want}" ]; then
        echo "mitsuba-src: sha256 mismatch for ${repo}@${commit}: got ${got}, want ${want}" >&2
        exit 1
    fi
    printf '%s\n' "${archive}"
}

# _unpack <archive> <dir>: unpack inside the directory, reading the archive
# from stdin -- on a Windows builder GNU tar takes "C:\..." in -f as host:path
# and unquotes escapes in -C names, so tar never sees a Windows path.
_unpack() {
    rm -rf "${2:?}"
    mkdir -p "$2"
    (cd "$2" && tar -xzf - --strip-components=1) < "$1"
    [ -n "$(ls -A "$2")" ] || { echo "mitsuba-src: $1 unpacked nothing" >&2; exit 1; }
}

# cvc_submodule <path> <owner/repo> <commit> <sha256>
cvc_submodule() {
    local archive
    archive="$(_fetch "$2" "$3" "$4")"
    _unpack "${archive}" "${dest}/$1"
    printf '%s %s %s\n' "$1" "$2" "$3" >> "${manifest}"
    echo "mitsuba-src: $1 <- $2@$3"
}

printf '# path repo commit -- the submodules of mitsuba-renderer/mitsuba3@478e193a183c21723f4a8251afc3ad29a8da4c5e\n' > "${manifest}"

# ── Dr.Jit v1.5.0 and its nesting (as in recipes/drjit-src) ─────────────────
cvc_submodule ext/drjit \
    mitsuba-renderer/drjit 671cbb25244be8a9731ac7b0e40e60ff122b8f8d \
    b2bb35d8102e76d71374a5b8741ada7c5e803c68e0c4beddef02dcba8fb2059c
cvc_submodule ext/drjit/ext/drjit-core \
    mitsuba-renderer/drjit-core 3b96411288b9938889045308654ba37d4ef24b71 \
    095f422a592172535a7fc834e5e778ce219027a507f25d1295f884679c6d5c8e
cvc_submodule ext/drjit/ext/drjit-core/ext/nanothread \
    mitsuba-renderer/nanothread a2097209e10852962c485ee46d84d28e7a63a1a1 \
    ed4abfaec8a12c796f238525b501871ea2c5e250a508fbb572c3fa75644600b2
cvc_submodule ext/drjit/ext/drjit-core/ext/nanothread/ext/cmake-defaults \
    mitsuba-renderer/cmake-defaults d65442956cd78772ba26ba25dc552de22566b717 \
    fad0f95617687bfc30a899261e4d00b12f43ae8cd81e775f3f75a2961d4f8ec5
cvc_submodule ext/drjit/ext/drjit-core/ext/robin_map \
    mitsuba-renderer/robin-map f70fd39a960385ef0a963aef00b750e4740228c0 \
    d8575d4d580f5378f01b0b51bc5c2738005b94fff20db732852475972b6645f7
cvc_submodule ext/drjit/ext/nanobind \
    wjakob/nanobind 86e5626728fc282637a6c338597120913dded1bb \
    fee2d84337ad3a14609091b8693399028eb957b8a83493ce2825db2b36a52d8c
cvc_submodule ext/drjit/ext/nanobind/ext/robin_map \
    Tessil/robin-map 4ec1bf19c6a96125ea22062f38c2cf5b958e448e \
    4968947bd2e908854481519ec422823af4161a286ff1e16943293b753e80cb3f

# ── Mitsuba's own forks and libraries (ext/CMakeLists.txt builds them) ──────
cvc_submodule ext/embree \
    mitsuba-renderer/embree 97d0e31dae2c69d9706978f5ebfba82a888fd73c \
    39f3c887ac0f2901b1a7020d6465f058e335e67825fe918fe796487eac9ff68f
cvc_submodule ext/openexr \
    mitsuba-renderer/openexr 2b5ab6935abf7c7c11b618d95940f53e1b4f2de2 \
    09b46bec5c2506866e0cf3494832063949bd0601a574f509017c789f22ce8acd
cvc_submodule ext/libpng \
    mitsuba-renderer/libpng cfcd1dc417f39929c3c540c4f945069cedeee693 \
    f088be954b148ba4a2dadd3d26040ab9e952bf3306341de09528800ec172ed84
cvc_submodule ext/libjpeg-turbo \
    mitsuba-renderer/libjpeg-turbo eeba99c494bf115bdfe671db1ae196803311174c \
    f65ab292b16210e2780257189067498975f8ba0fde44b17756a8b5ca47520436
# Built on Windows only; linux and macOS link the zlib package instead.
cvc_submodule ext/zlib \
    mitsuba-renderer/zlib 080a732c47e86444034c1f99355368d35c1e458a \
    fc4bb0fad343a6d6181e5c2ae9b0e38e5a94911e1ea2e3178b54f54ab0e99e14
cvc_submodule ext/pugixml \
    mitsuba-renderer/pugixml 7e9d5fa64a052d3df4b629d24f1e7fe12f83c3ea \
    8498da649c329b245a3d0f2039bdb3078275fda784b702d10360acf87b59d7ec
cvc_submodule ext/tinyformat \
    mitsuba-renderer/tinyformat 635345c75bd95891ee041ac51ce74ebc891d5bab \
    cedb1778088047dd5c5de1e7094f7ea26aed074c45fdbe47b8831101c6fc413a
cvc_submodule ext/fastfloat \
    fastfloat/fast_float 052975dd5f8166d0f9e4a215fa75a349d5985b91 \
    a8ed1135907c7884ebc6aece9f4e907e12ca9222891a0946ebd44928c2f74c01
cvc_submodule ext/struct-jit \
    mitsuba-renderer/struct-jit ea69db6e0b343e69209cedbd84f500eeafe01847 \
    b7516aae2d89c0016dda9a6d42c5a183cbddc51cc62cea66756ae522c6abf13d
# struct-jit includes its own cmake-defaults and puts its nanobind's robin_map
# on the include path, Python bindings off or not.
cvc_submodule ext/struct-jit/ext/cmake-defaults \
    mitsuba-renderer/cmake-defaults 159d5133a67fd22d733645df57cd55a0f9fd13bf \
    0dbd4d1e81485bf0fc6283fb7ba03261c3201928fe8c8f20e2abc0a8bd359353
cvc_submodule ext/struct-jit/ext/nanobind \
    wjakob/nanobind 2deac96697d1b304f3c973cef7de5f94cbad5a57 \
    862ca243f8089675a58dfbb3c5cc796ec640b5139d5f1af6ce7da30eb93c4897
cvc_submodule ext/struct-jit/ext/nanobind/ext/robin_map \
    Tessil/robin-map 4ec1bf19c6a96125ea22062f38c2cf5b958e448e \
    4968947bd2e908854481519ec422823af4161a286ff1e16943293b753e80cb3f
cvc_submodule ext/rgb2spec \
    mitsuba-renderer/rgb2spec f13d570de53544e5008bc8659651503eb05c850e \
    66fa58df87b00e1e4faa5c273960bee003b42ff1943abce448fba488fbde0267
# nanobind v2.14.0, as in drjit; the build compiles against nanobind-cp313.
cvc_submodule ext/nanobind \
    wjakob/nanobind 86e5626728fc282637a6c338597120913dded1bb \
    fee2d84337ad3a14609091b8693399028eb957b8a83493ce2825db2b36a52d8c
cvc_submodule ext/nanobind/ext/robin_map \
    Tessil/robin-map 4ec1bf19c6a96125ea22062f38c2cf5b958e448e \
    4968947bd2e908854481519ec422823af4161a286ff1e16943293b753e80cb3f
# Top level only: Mitsuba reads include/ and resources/bin2c.cmake; nanogui's
# own submodules (glfw, nanovg, nanovg_metal, pybind11) serve the GUI, which
# is not built.
cvc_submodule ext/nanogui \
    mitsuba-renderer/nanogui a6afde86f48bf2e3a00689c7145746c89fa474a3 \
    3e4edcf7c4fa8d6d625f1dabce5273510de8ba8f3bb110ed8bc02a4c1fe44671

# ── resources/data: only what the build installs ───────────────────────────
# The mitsuba-data submodule is a 250 MB tree of test scenes and images; the
# build copies just ior/ (refractive-index spectra) and sunsky/output/ (the
# sun/sky model tables) into mitsuba/data.  Unpack it aside and keep those.
data_commit=01100fa9b0b154492061a92404f26dcd7b784dbb
data_archive="$(_fetch mitsuba-renderer/mitsuba-data "${data_commit}" \
    bc8d97e321b51ce5a07107243371febfe53dedefff3654e2c0bfe1391c78c7e0)"
_unpack "${data_archive}" "${CVC_BUILD_DIR}/mitsuba-data"
rm -rf "${dest:?}/resources/data"
mkdir -p "${dest}/resources/data/sunsky"
cp -pR "${CVC_BUILD_DIR}/mitsuba-data/ior" "${dest}/resources/data/ior"
cp -pR "${CVC_BUILD_DIR}/mitsuba-data/sunsky/output" "${dest}/resources/data/sunsky/output"
cp -p "${CVC_BUILD_DIR}/mitsuba-data/README.md" "${dest}/resources/data/README.md"
rm -rf "${CVC_BUILD_DIR}/mitsuba-data"
printf '%s %s %s (ior/ and sunsky/output/ only)\n' resources/data mitsuba-renderer/mitsuba-data "${data_commit}" >> "${manifest}"
echo "mitsuba-src: resources/data <- mitsuba-renderer/mitsuba-data@${data_commit} (ior/, sunsky/output/)"

for f in ext/drjit/ext/drjit-core/ext/nanothread/ext/cmake-defaults/CMakeLists.txt \
         ext/struct-jit/ext/cmake-defaults/CMakeLists.txt \
         resources/data/sunsky/output/sunsky_datasets.bin; do
    [ -f "${dest}/${f}" ] || { echo "mitsuba-src: ${f} missing after unpacking" >&2; exit 1; }
done
echo "mitsuba-src: staged $(grep -vc '^#' "${manifest}") submodules"
