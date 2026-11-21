#!/usr/bin/env bash
# recipes/leveldb/test.sh -- LevelDB is built with Snappy.
#
# A CMake consumer of leveldb::leveldb (which must find Snappy through leveldbConfig.cmake)
# writes the same compressible records into a Snappy-compressed and an uncompressed database,
# compacts both to table files, and requires the Snappy one to be several times smaller: a
# LevelDB without Snappy silently stores kSnappyCompression blocks uncompressed, so the two
# would match. It then reopens the Snappy database with checksums verified and reads every
# record back, which is what reading an official AirDC++ install's HashData needs.
# On linux the shared library must also record libsnappy as NEEDED. Elsewhere than linux and
# macos only the headers and the CMake package are checked.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

P="${CVC_INSTALL_DIR%/}"
D="${CVC_DEPS_PREFIX:-${P}}"
D="${D%/}"
PLAT="${CVC_PLATFORM:-linux}"
echo "-- leveldb test (${PLAT}) --"

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  OK: $*"; }

for h in db.h c.h options.h write_batch.h; do
    test -f "${P}/include/leveldb/${h}" || fail "include/leveldb/${h} missing"
done
test -f "${P}/lib/cmake/leveldb/leveldbConfig.cmake" || fail "lib/cmake/leveldb/leveldbConfig.cmake missing"
grep -q 'find_dependency(Snappy' "${P}/lib/cmake/leveldb/leveldbConfig.cmake" \
    || fail "leveldbConfig.cmake does not find Snappy for its consumers"
ok "headers and CMake package present; the package finds Snappy"

case "${PLAT}" in
    linux|macos) ;;
    *) ok "no consumer built on ${PLAT}"; exit 0 ;;
esac

if [ "${PLAT}" = linux ] && command -v readelf >/dev/null 2>&1; then
    lib=""
    for f in "${P}"/lib/libleveldb.so.*; do
        [ -f "${f}" ] && [ ! -L "${f}" ] && lib="${f}"
    done
    if [ -n "${lib}" ]; then
        readelf -d "${lib}" | grep -q 'NEEDED.*libsnappy' \
            || fail "$(basename "${lib}") does not link libsnappy"
        ok "$(basename "${lib}") links libsnappy"
    else
        ok "static bundle: no NEEDED entry to check"
    fi
fi

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
cat > "${T}/CMakeLists.txt" <<'CMAKE'
cmake_minimum_required(VERSION 3.16)
project(leveldb_consumer CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(leveldb CONFIG REQUIRED)
add_executable(consumer consumer.cc)
target_link_libraries(consumer PRIVATE leveldb::leveldb)
CMAKE
cat > "${T}/consumer.cc" <<'CXX'
#include <leveldb/db.h>
#include <leveldb/options.h>
#include <leveldb/write_batch.h>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <memory>
#include <string>

namespace fs = std::filesystem;
static const int kRecords = 2000;

static std::string Key(int i) { char b[32]; std::snprintf(b, sizeof b, "tth-%06d", i); return b; }
static std::string Value(int i) {
  std::string v;
  for (int r = 0; r < 64; ++r) v += "hash-tree-leaf-" + std::to_string(i % 7) + ";";
  return v;
}

static bool Write(const std::string& path, leveldb::CompressionType type) {
  leveldb::Options o;
  o.create_if_missing = true;
  o.error_if_exists = true;
  o.compression = type;
  leveldb::DB* raw = nullptr;
  leveldb::Status s = leveldb::DB::Open(o, path, &raw);
  if (!s.ok()) { std::printf("open %s: %s\n", path.c_str(), s.ToString().c_str()); return false; }
  std::unique_ptr<leveldb::DB> db(raw);
  leveldb::WriteBatch batch;
  for (int i = 0; i < kRecords; ++i) batch.Put(Key(i), Value(i));
  s = db->Write(leveldb::WriteOptions(), &batch);
  if (!s.ok()) { std::printf("write: %s\n", s.ToString().c_str()); return false; }
  db->CompactRange(nullptr, nullptr);
  return true;
}

static std::uintmax_t TableBytes(const std::string& path) {
  std::uintmax_t n = 0;
  for (const auto& e : fs::directory_iterator(path))
    if (e.path().extension() == ".ldb" || e.path().extension() == ".sst") n += e.file_size();
  return n;
}

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  const std::string snappy = std::string(argv[1]) + "/snappy";
  const std::string plain = std::string(argv[1]) + "/plain";
  if (!Write(snappy, leveldb::kSnappyCompression) || !Write(plain, leveldb::kNoCompression)) return 1;

  const std::uintmax_t zs = TableBytes(snappy), zp = TableBytes(plain);
  if (zs == 0 || zp == 0 || zs * 4 > zp) {
    std::printf("tables: snappy %ju bytes, uncompressed %ju bytes: blocks are not Snappy-compressed\n", zs, zp);
    return 1;
  }

  leveldb::Options o;
  o.paranoid_checks = true;
  leveldb::DB* raw = nullptr;
  leveldb::Status s = leveldb::DB::Open(o, snappy, &raw);
  if (!s.ok()) { std::printf("reopen: %s\n", s.ToString().c_str()); return 1; }
  std::unique_ptr<leveldb::DB> db(raw);
  leveldb::ReadOptions r;
  r.verify_checksums = true;
  r.fill_cache = false;
  int seen = 0;
  std::unique_ptr<leveldb::Iterator> it(db->NewIterator(r));
  for (it->SeekToFirst(); it->Valid(); it->Next(), ++seen) {
    if (it->key().ToString() != Key(seen) || it->value().ToString() != Value(seen)) {
      std::printf("record %d differs\n", seen);
      return 1;
    }
  }
  if (!it->status().ok() || seen != kRecords) {
    std::printf("read %d of %d records: %s\n", seen, kRecords, it->status().ToString().c_str());
    return 1;
  }
  std::printf("%d records; tables %ju bytes with Snappy, %ju without\n", seen, zs, zp);
  return 0;
}
CXX

cmake -S "${T}" -B "${T}/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="${P};${D}" >"${T}/configure.log" 2>&1 \
    || { cat "${T}/configure.log"; fail "find_package(leveldb) did not configure"; }
cmake --build "${T}/build" >"${T}/build.log" 2>&1 \
    || { cat "${T}/build.log"; fail "a consumer of leveldb::leveldb did not build"; }
ok "a consumer of leveldb::leveldb builds"
mkdir -p "${T}/db"
out="$( (unset LD_LIBRARY_PATH DYLD_LIBRARY_PATH DYLD_FALLBACK_LIBRARY_PATH; exec "${T}/build/consumer" "${T}/db") )" \
    || fail "the consumer failed: ${out:-}"
ok "Snappy-compressed database written and read back (${out})"
