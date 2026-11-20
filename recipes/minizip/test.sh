#!/usr/bin/env bash
# recipes/minizip/test.sh -- the headers and CMake package are staged; on unix hosts, a consumer
# that finds unofficial-minizip writes a zip with zip.h and reads it back with unzip.h.
set -euo pipefail
: "${CVC_INSTALL_DIR:?}" "${CVC_DEPS_PREFIX:?}"
for h in ioapi.h unzip.h zip.h mztools.h crypt.h; do
  test -f "${CVC_INSTALL_DIR}/include/minizip/$h" || { echo "missing include/minizip/$h"; exit 1; }
done
test -f "${CVC_INSTALL_DIR}/share/unofficial-minizip/unofficial-minizipConfig.cmake"
[[ "${CVC_PLATFORM:-}" == windows ]] && { echo "minizip: files OK (windows)"; exit 0; }

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
cat > "$T/CMakeLists.txt" <<'CM'
cmake_minimum_required(VERSION 3.21)
project(minizip_smoke C)
find_package(unofficial-minizip REQUIRED)
add_executable(smoke smoke.c)
target_link_libraries(smoke unofficial::minizip::minizip)
CM
cat > "$T/smoke.c" <<'C'
#include <string.h>
#include <minizip/zip.h>
#include <minizip/unzip.h>
int main(int argc, char **argv) {
  const char *msg = "cvcpkg minizip";
  char buf[64] = {0};
  zipFile z = zipOpen(argv[1], APPEND_STATUS_CREATE);
  if (!z || zipOpenNewFileInZip(z, "a.txt", NULL, NULL, 0, NULL, 0, NULL, Z_DEFLATED, 9) != ZIP_OK) return 1;
  if (zipWriteInFileInZip(z, msg, (unsigned)strlen(msg)) != ZIP_OK) return 2;
  zipCloseFileInZip(z); zipClose(z, NULL);
  unzFile u = unzOpen(argv[1]);
  if (!u || unzLocateFile(u, "a.txt", 0) != UNZ_OK || unzOpenCurrentFile(u) != UNZ_OK) return 3;
  if (unzReadCurrentFile(u, buf, sizeof buf - 1) != (int)strlen(msg)) return 4;
  unzCloseCurrentFile(u); unzClose(u);
  return strcmp(buf, msg) == 0 ? 0 : 5;
}
C
cmake -S "$T" -B "$T/b" -G Ninja -DCMAKE_PREFIX_PATH="${CVC_INSTALL_DIR};${CVC_DEPS_PREFIX}" >/dev/null
cmake --build "$T/b" >/dev/null
LD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib:${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" "$T/b/smoke" "$T/t.zip"
echo "minizip smoke test OK"
