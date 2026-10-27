#!/usr/bin/env bash
# recipes/zig/test.sh — smoke test the staged Zig toolchain.
#
#  1. zig reports the pinned version and found its standard library in the
#     prefix's lib/zig.
#  2. Relocatability: the staged tree is copied elsewhere; the copy's zig must
#     report the copy's lib/zig, and everything below runs from the COPY.
#  3. It compiles: a Zig program (build-exe, then run), its unit test (zig
#     test), a C program (zig cc, libc built from lib/zig/libc), a C++ program
#     (zig c++, libc++ built from lib/zig/libcxx), and a build.zig project
#     (zig build test + zig build run), all offline.
# Every cache zig writes goes to a temp dir, never ~/.cache/zig.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
EXPECT="0.15.2"

echo "-- zig smoke test (${CVC_PLATFORM:-native}) --"

T="$(mktemp -d)"
trap 'chmod -R u+w "${T}" 2>/dev/null || true; rm -rf "${T}"' EXIT

EXE=""
if [ -f "${CVC_INSTALL_DIR}/bin/zig.exe" ]; then EXE=".exe"; fi
# Paths handed to zig (a native program on Windows).
native() { if [ -n "${EXE}" ]; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

unset ZIG_LIB_DIR ZIG_LIBC ZIG_BUILD_RUNNER CC CXX || true
export ZIG_GLOBAL_CACHE_DIR="$(native "${T}/zig-global-cache")"
export ZIG_LOCAL_CACHE_DIR="$(native "${T}/zig-local-cache")"

# The lib dir `zig env` reports, as an absolute physical path. zig 0.15
# prints ZON (.lib_dir = "..."), with backslashes doubled on Windows, and the
# path may be relative to the current directory.
zig_lib_dir() {  # $1 = zig executable
    local raw
    raw="$("$1" env | tr -d '\r' | sed -n 's/^ *\.lib_dir = "\(.*\)",$/\1/p' | sed 's/\\\\/\//g')"
    [ -n "${raw}" ] || { echo "FAIL: no .lib_dir in '$1 env'" >&2; return 1; }
    if [ -n "${EXE}" ]; then raw="$(cygpath -u "${raw}")"; fi
    (cd "${raw}" && pwd -P)
}

ZIG="${CVC_INSTALL_DIR}/bin/zig${EXE}"
ver="$("${ZIG}" version | tr -d '\r')"
[ "${ver}" = "${EXPECT}" ] || { echo "FAIL: zig version '${ver}', want '${EXPECT}'"; exit 1; }
echo "  zig ${ver}"
want="$(cd "${CVC_INSTALL_DIR}/lib/zig" && pwd -P)"
got="$(zig_lib_dir "${ZIG}")"
[ "${got}" = "${want}" ] || { echo "FAIL: zig uses lib dir ${got}, expected ${want}"; exit 1; }
echo "  OK: standard library from <prefix>/lib/zig"

# ── relocate: copy the staged tree, test the copy from here on ───────
MOVED="${T}/relocated/prefix"
mkdir -p "${MOVED}"
# Hard links where the platform has them: same bytes, new paths, no 300 MB copy.
cp -al "${CVC_INSTALL_DIR}/." "${MOVED}/" 2>/dev/null || cp -pR "${CVC_INSTALL_DIR}/." "${MOVED}/"
ZIG="${MOVED}/bin/zig${EXE}"
want="$(cd "${MOVED}/lib/zig" && pwd -P)"
got="$(zig_lib_dir "${ZIG}")"
[ "${got}" = "${want}" ] || { echo "FAIL: relocated zig uses lib dir ${got}, expected ${want}"; exit 1; }
echo "  OK: relocated zig uses the copy's lib/zig"

W="${T}/work"
mkdir -p "${W}/proj/src"
cd "${W}"

# ── Zig: build-exe + run, zig test ──────────────────────────────────────
cat > "${W}/proj/src/main.zig" <<'EOF'
const std = @import("std");
const builtin = @import("builtin");

pub fn greet(buf: []u8, name: []const u8) ![]u8 {
    return std.fmt.bufPrint(buf, "hello, {s}", .{name});
}

pub fn main() !void {
    var buf: [64]u8 = undefined;
    var line: [128]u8 = undefined;
    const g = try greet(&buf, "cvcpkg");
    try std.fs.File.stdout().writeAll(try std.fmt.bufPrint(&line, "{s} zig {s}\n", .{ g, builtin.zig_version_string }));
}

test "greet" {
    var buf: [64]u8 = undefined;
    try std.testing.expectEqualStrings("hello, cvcpkg", try greet(&buf, "cvcpkg"));
}
EOF
# macOS on Apple silicon with an Xcode 26.4+ SDK: zig 0.15.2's Mach-O linker
# cannot match aarch64-macos against the SDK libSystem.tbd's arm64e-macos
# entries, so a NATIVE link finds no libSystem symbols at all ("undefined
# symbol: _abort", ziglang/zig#31665, fixed for 0.16.0). An explicit -target
# makes zig link against its own lib/zig/libc/darwin stubs instead. Recognise
# exactly that failure, say so, and test the rest that way; anything else
# fails.
TARGET=()
FORCED_TARGET=""
if ! "${ZIG}" build-exe "$(native "${W}/proj/src/main.zig")" -femit-bin="$(native "${W}/hello${EXE}")" \
        > "${W}/native-link.log" 2>&1; then
    if [ "$(uname -s)-$(uname -m)" = "Darwin-arm64" ] \
            && grep -q 'undefined symbol: _abort' "${W}/native-link.log"; then
        sdk="$(xcrun --sdk macosx --show-sdk-version 2>/dev/null || echo '?')"
        echo "  KNOWN: native link fails against the macOS ${sdk} SDK (ziglang/zig#31665," \
             "fixed in zig 0.16.0); testing with -target aarch64-macos"
        FORCED_TARGET="aarch64-macos"
        TARGET=(-target "${FORCED_TARGET}")
        "${ZIG}" build-exe ${TARGET[@]+"${TARGET[@]}"} "$(native "${W}/proj/src/main.zig")" \
            -femit-bin="$(native "${W}/hello${EXE}")"
    else
        cat "${W}/native-link.log"
        echo "FAIL: zig build-exe"; exit 1
    fi
fi
out="$("${W}/hello${EXE}" | tr -d '\r')"
[ "${out}" = "hello, cvcpkg zig ${EXPECT}" ] || { echo "FAIL: zig program printed '${out}'"; exit 1; }
echo "  OK: build-exe + run (${out})"
"${ZIG}" test ${TARGET[@]+"${TARGET[@]}"} "$(native "${W}/proj/src/main.zig")" 2>&1 | tr -d '\r' | sed 's/^/  /'
echo "  OK: zig test"

# ── C and C++ through zig cc / zig c++ ──────────────────────────────────
cat > "${W}/t.c" <<'EOF'
#include <math.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    char b[32];
    snprintf(b, sizeof b, "%.4f", sqrt(2.0));
    printf("c %s\n", b);
    return strcmp(b, "1.4142") != 0;
}
EOF
"${ZIG}" cc ${TARGET[@]+"${TARGET[@]}"} -O2 -o "$(native "${W}/c-bin${EXE}")" "$(native "${W}/t.c")" -lm
out="$("${W}/c-bin${EXE}" | tr -d '\r')"
[ "${out}" = "c 1.4142" ] || { echo "FAIL: zig cc program printed '${out}'"; exit 1; }
echo "  OK: zig cc (${out})"

cat > "${W}/t.cpp" <<'EOF'
#include <iostream>
#include <numeric>
#include <string>
#include <vector>

int main() {
    std::vector<int> v{1, 2, 3, 4};
    std::string s = "c++ " + std::to_string(std::accumulate(v.begin(), v.end(), 0));
    std::cout << s << std::endl;
    return s != "c++ 10";
}
EOF
"${ZIG}" c++ ${TARGET[@]+"${TARGET[@]}"} -O2 -std=c++17 -o "$(native "${W}/cxx-bin${EXE}")" "$(native "${W}/t.cpp")"
out="$("${W}/cxx-bin${EXE}" | tr -d '\r')"
[ "${out}" = "c++ 10" ] || { echo "FAIL: zig c++ program printed '${out}'"; exit 1; }
echo "  OK: zig c++ with libc++ (${out})"

# ── zig build ───────────────────────────────────────────────────────────
cat > "${W}/proj/build.zig" <<'EOF'
const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});
    const mod = b.createModule(.{
        .root_source_file = b.path("src/main.zig"),
        .target = target,
        .optimize = optimize,
    });
    const exe = b.addExecutable(.{ .name = "hello", .root_module = mod });
    b.installArtifact(exe);
    b.step("run", "Run hello").dependOn(&b.addRunArtifact(exe).step);
    const tests = b.addTest(.{ .root_module = mod });
    b.step("test", "Run the unit tests").dependOn(&b.addRunArtifact(tests).step);
}
EOF
cd "${W}/proj"
ZB=()
[ -z "${FORCED_TARGET}" ] || ZB=("-Dtarget=${FORCED_TARGET}")
"${ZIG}" build ${ZB[@]+"${ZB[@]}"}
[ -f "${W}/proj/zig-out/bin/hello${EXE}" ] || { echo "FAIL: zig build installed no zig-out/bin/hello${EXE}"; exit 1; }
"${ZIG}" build test ${ZB[@]+"${ZB[@]}"}
out="$("${ZIG}" build run ${ZB[@]+"${ZB[@]}"} 2>&1 | tr -d '\r')"
[ "${out}" = "hello, cvcpkg zig ${EXPECT}" ] || { echo "FAIL: zig build run printed '${out}'"; exit 1; }
echo "  OK: zig build (install) + test + run"

echo "-- zig smoke test passed --"
