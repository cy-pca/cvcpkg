#!/usr/bin/env bash
# recipes/snappy/test.sh -- a CMake consumer of Snappy::snappy round-trips data through the
# raw and the Source/Sink APIs, subclassing snappy::Sink with RTTI on (which links only
# because rtti-no-werror.patch keeps RTTI in the library), and through the C API.
# Elsewhere than linux and macos only the headers and the CMake package are checked.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

P="${CVC_INSTALL_DIR%/}"
PLAT="${CVC_PLATFORM:-linux}"
echo "-- snappy test (${PLAT}) --"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  OK: $*"; }

for h in snappy.h snappy-c.h snappy-sinksource.h snappy-stubs-public.h; do
    test -f "${P}/include/${h}" || fail "include/${h} missing"
done
test -f "${P}/lib/cmake/Snappy/SnappyConfig.cmake" || fail "lib/cmake/Snappy/SnappyConfig.cmake missing"
ok "headers and CMake package present"

case "${PLAT}" in
    linux|macos) ;;
    *) ok "no consumer built on ${PLAT}"; exit 0 ;;
esac

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
cat > "${T}/CMakeLists.txt" <<'CMAKE'
cmake_minimum_required(VERSION 3.16)
project(snappy_consumer CXX C)
set(CMAKE_CXX_STANDARD 17)
find_package(Snappy 1.3 CONFIG REQUIRED)
add_executable(consumer consumer.cc)
target_link_libraries(consumer PRIVATE Snappy::snappy)
CMAKE
cat > "${T}/consumer.cc" <<'CXX'
#include <snappy.h>
#include <snappy-c.h>
#include <snappy-sinksource.h>
#include <cstdio>
#include <string>
#include <typeinfo>

// A Sink subclass in an RTTI translation unit needs snappy::Sink's typeinfo from the library.
class StringSink : public snappy::Sink {
 public:
  void Append(const char* bytes, size_t n) override { out.append(bytes, n); }
  std::string out;
};

int main() {
  std::string input;
  for (int i = 0; i < 4096; ++i) input += "snappy round trip ";

  std::string compressed;
  snappy::Compress(input.data(), input.size(), &compressed);
  if (compressed.size() * 4 > input.size()) {
    std::printf("compressed %zu -> %zu, not a real compression\n", input.size(), compressed.size());
    return 1;
  }
  std::string restored;
  if (!snappy::Uncompress(compressed.data(), compressed.size(), &restored) || restored != input) {
    std::printf("raw round trip failed\n");
    return 1;
  }

  snappy::ByteArraySource source(input.data(), input.size());
  StringSink sink;
  snappy::Compress(&source, &sink);
  snappy::Sink* base = &sink;
  if (dynamic_cast<StringSink*>(base) == nullptr || sink.out != compressed) {
    std::printf("Source/Sink compression differs from the raw API\n");
    return 1;
  }

  size_t len = 0;
  if (snappy_uncompressed_length(compressed.data(), compressed.size(), &len) != SNAPPY_OK ||
      len != input.size()) {
    std::printf("C API length failed\n");
    return 1;
  }
  std::printf("snappy %zu -> %zu bytes, %s\n", input.size(), compressed.size(),
              typeid(snappy::Sink).name());
  return 0;
}
CXX

cmake -S "${T}" -B "${T}/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="${P}" >"${T}/configure.log" 2>&1 \
    || { cat "${T}/configure.log"; fail "find_package(Snappy) did not configure"; }
cmake --build "${T}/build" >"${T}/build.log" 2>&1 \
    || { cat "${T}/build.log"; fail "a consumer of Snappy::snappy did not build (RTTI typeinfo?)"; }
ok "a consumer of Snappy::snappy builds with RTTI"
out="$( (unset LD_LIBRARY_PATH DYLD_LIBRARY_PATH DYLD_FALLBACK_LIBRARY_PATH; exec "${T}/build/consumer") )" \
    || fail "the consumer did not run: ${out:-}"
ok "the consumer runs (${out})"
