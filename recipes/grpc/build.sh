#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Pre-download third_party archives that grpc's CMakeLists.txt would otherwise
# fetch via cmake file(DOWNLOAD) which has no retries and fails frequently in CI.
# By populating the directories beforehand, the "NOT EXISTS" checks in
# CMakeLists.txt pass and cmake skips its own flaky downloads.
_grpc_fetch_archive() {
    local dest="$1" url="$2" sha256="$3" strip_dir="$4"
    if [[ -e "$dest" ]]; then
        echo "cvcpkg: $dest already exists, skipping download"
        return 0
    fi
    local tmp
    tmp="$(mktemp -d)"
    echo "cvcpkg: downloading $dest from $url ..."
    curl -fsSL --retry 5 --retry-delay 3 -o "$tmp/archive" "$url"
    local actual
    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$tmp/archive" | awk '{print $1}')"
    elif command -v sha256 >/dev/null 2>&1; then
        actual="$(sha256 -q "$tmp/archive")"
    else
        actual="$(openssl dgst -sha256 "$tmp/archive" | awk '{print $NF}')"
    fi
    if [[ "$actual" != "$sha256" ]]; then
        echo "cvcpkg: sha256 mismatch for $url (expected $sha256, got $actual)" >&2
        rm -rf "$tmp"
        return 1
    fi
    mkdir -p "$tmp/extract"
    if [[ "$url" == *.zip ]]; then
        unzip -q "$tmp/archive" -d "$tmp/extract"
    else
        tar xzf "$tmp/archive" -C "$tmp/extract"
    fi
    mkdir -p "$(dirname "$dest")"
    mv "$tmp/extract/$strip_dir" "$dest"
    rm -rf "$tmp"
    echo "cvcpkg: installed $dest ($(ls "$dest" | wc -l) entries)"
}

_src="${CVC_SOURCE_DIR}"
_grpc_fetch_archive \
    "$_src/third_party/envoy-api" \
    "https://github.com/envoyproxy/data-plane-api/archive/f8b75d1efa92bbf534596a013d9ca5873f79dd30.tar.gz" \
    "e525a6fb6e6ed3eef1eec6bef3da9b5708e471f0f9335a7604df14a4b386231e" \
    "data-plane-api-f8b75d1efa92bbf534596a013d9ca5873f79dd30"

_grpc_fetch_archive \
    "$_src/third_party/googleapis" \
    "https://github.com/googleapis/googleapis/archive/fe8ba054ad4f7eca946c2d14a63c3f07c0b586a0.tar.gz" \
    "0513f0f40af63bd05dc789cacc334ab6cec27cc89db596557cb2dfe8919463e4" \
    "googleapis-fe8ba054ad4f7eca946c2d14a63c3f07c0b586a0"

_grpc_fetch_archive \
    "$_src/third_party/opencensus-proto/src" \
    "https://github.com/census-instrumentation/opencensus-proto/archive/v0.3.0.tar.gz" \
    "b7e13f0b4259e80c3070b583c2f39e53153085a6918718b1c710caf7037572b0" \
    "opencensus-proto-0.3.0/src"

_grpc_fetch_archive \
    "$_src/third_party/protoc-gen-validate" \
    "https://github.com/bufbuild/protoc-gen-validate/archive/refs/tags/v1.0.4.zip" \
    "9372f9ecde8fbadf83c8c7de3dbb49b11067aa26fb608c501106d0b4bf06c28f" \
    "protoc-gen-validate-1.0.4"

_grpc_fetch_archive \
    "$_src/third_party/xds" \
    "https://github.com/cncf/xds/archive/3a472e524827f72d1ad621c4983dd5af54c46776.tar.gz" \
    "dc305e20c9fa80822322271b50aa2ffa917bf4fd3973bcec52bfc28dc32c5927" \
    "xds-3a472e524827f72d1ad621c4983dd5af54c46776"

# Temporarily remove protobuf-installed upb headers from the prefix.
# grpc builds its own upb from third_party/upb, and the protobuf-installed
# upb headers at prefix/include/upb/ are a different version, causing
# compile-time symbol mismatches.  We keep the libupb* libraries in
# place so protobuf's cmake config validates successfully.
_upb_backup=""
if [[ -d "${CVC_DEPS_PREFIX}/include/upb" ]]; then
    _upb_backup="$(mktemp -d)"
    mv "${CVC_DEPS_PREFIX}/include/upb" "${_upb_backup}/upb"
    if [[ -d "${CVC_DEPS_PREFIX}/include/upb_generator" ]]; then
        mv "${CVC_DEPS_PREFIX}/include/upb_generator" "${_upb_backup}/upb_generator"
    fi
    echo "cvcpkg: moved prefix/include/upb{,_generator} aside to avoid header collision"
fi

# On macOS shared builds, grpc's internal upb dylibs reference protobuf
# descriptor mini-table symbols (_google__protobuf__*_msg_init) that live
# in protobuf's libupb.  macOS requires all symbols resolved at link time,
# so we allow deferred lookup — the symbols resolve at load time when both
# grpc and protobuf dylibs are present.
_extra_cmake_flags=()
if [[ "${CVC_PLATFORM}" == "macos" && "${CVC_LINK}" == "shared" ]]; then
    _extra_cmake_flags+=("-DCMAKE_SHARED_LINKER_FLAGS=-Wl,-undefined,dynamic_lookup")
fi

cvc_cmake_build \
    ${_extra_cmake_flags[@]+"${_extra_cmake_flags[@]}"} \
    -DgRPC_BUILD_TESTS=OFF \
    -DgRPC_BUILD_CSHARP_EXT=OFF \
    -DgRPC_BUILD_GRPC_CPP_PLUGIN=ON \
    -DgRPC_BUILD_GRPC_CSHARP_PLUGIN=OFF \
    -DgRPC_BUILD_GRPC_NODE_PLUGIN=OFF \
    -DgRPC_BUILD_GRPC_OBJECTIVE_C_PLUGIN=OFF \
    -DgRPC_BUILD_GRPC_PHP_PLUGIN=OFF \
    -DgRPC_BUILD_GRPC_PYTHON_PLUGIN=ON \
    -DgRPC_BUILD_GRPC_RUBY_PLUGIN=OFF \
    -DgRPC_ABSL_PROVIDER=package \
    -DgRPC_CARES_PROVIDER=package \
    -DgRPC_PROTOBUF_PROVIDER=package \
    -DgRPC_RE2_PROVIDER=package \
    -DgRPC_SSL_PROVIDER=package \
    -DgRPC_ZLIB_PROVIDER=package \
    -DCMAKE_CXX_STANDARD=17

# Restore the upb headers so downstream consumers can use them.
if [[ -n "${_upb_backup}" ]]; then
    if [[ -d "${_upb_backup}/upb" ]]; then
        mv "${_upb_backup}/upb" "${CVC_DEPS_PREFIX}/include/upb"
    fi
    if [[ -d "${_upb_backup}/upb_generator" ]]; then
        mv "${_upb_backup}/upb_generator" "${CVC_DEPS_PREFIX}/include/upb_generator"
    fi
    rm -rf "${_upb_backup}"
    echo "cvcpkg: restored prefix/include/upb headers"
fi
