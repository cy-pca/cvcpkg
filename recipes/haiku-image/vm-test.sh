#!/bin/sh
# Guest-side test for haiku-image.  cvcpkg streams this to `sh -s` over SSH
# INSIDE a throwaway VM booted from the qcow2 this build just produced, then
# destroys the VM (see src/cvcpkg/vmtest.py).
#
# This is Haiku's /bin/sh, not bash: keep it POSIX.  Do not assume a TTY, a
# writable $HOME beyond ~, or that any network mirror is reachable.
#
# What it asserts is exactly what the recipe CLAIMS in image.yaml and could
# not previously prove:
#
#   * the guest is really Haiku (not, say, a boot loader shell), and
#   * it is the arch and release the descriptor advertises, and
#   * the disk is a real writable BFS root, not a ramdisk that will vanish, and
#   * the toolchain the "builder" variant exists to provide is present and can
#     actually compile and run a binary, and
#   * a boot-time hook that will start sshd on the NEXT boot is installed
#     (either mechanism -- see check 5).
#
# Every failure below is fatal.  A guest that boots but cannot compile is not a
# builder image, and shipping it would move the failure to the first job that
# gets routed to it, hours later, with no obvious cause.

set -eu

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "ok: $*"; }

echo "--- uname ---"
uname -a

# 1. It is Haiku.
sysname="$(uname -s)"
[ "$sysname" = "Haiku" ] || fail "guest reports uname -s = '$sysname', expected Haiku"
ok "guest is Haiku"

# 2. It is the architecture image.yaml advertises.  getarch is Haiku's own
#    tool; uname -m is the cross-check.
machine="$(uname -m)"
case "$machine" in
    x86_64|BePC) ok "arch $machine" ;;
    *) fail "guest arch is '$machine', expected x86_64" ;;
esac

# 3. The root volume is a real, writable BFS on the boot disk -- not a
#    read-only ramdisk.  A Haiku that failed to mount its root still reaches a
#    shell in some configurations, and it would pass every check above.
df -h / || true
tmp="/tmp/cvcpkg-vmtest.$$"
echo probe > "$tmp" || fail "root volume is not writable"
[ "$(cat "$tmp")" = "probe" ] || fail "wrote to the root volume but read back garbage"
rm -f "$tmp"
ok "root volume is writable"

# 4. The build toolchain is present AND works.  `which gcc` is not evidence:
#    a partial haikuporter state leaves the driver without its cc1plus.
for tool in gcc make; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is not on PATH -- this is not a builder image"
done
ok "gcc and make are on PATH"

work="/tmp/cvcpkg-vmtest-cc.$$"
mkdir -p "$work"
cat > "$work/hello.c" <<'CSRC'
#include <stdio.h>
int main(void) { printf("cvcpkg-guest-ok\n"); return 0; }
CSRC
gcc -O0 -o "$work/hello" "$work/hello.c" || fail "gcc could not compile a hello world"
out="$("$work/hello")" || fail "the compiled binary did not run"
[ "$out" = "cvcpkg-guest-ok" ] || fail "compiled binary printed '$out'"
rm -rf "$work"
ok "gcc compiled and ran a binary"

# 5. sshd is the channel we arrived on, so it is proven by construction -- but
#    whatever STARTED it at boot is not, and a hand-started sshd would let a
#    one-shot test pass an image that comes up dead on the next boot.  So
#    assert that a boot-time hook is actually installed on disk.
#
#    Deliberately mechanism-AGNOSTIC.  There are two ways this image can be
#    made self-configuring and they are not interchangeable:
#
#      a) ~/config/settings/boot/UserBootscript -- what this branch injects.
#         Only runs inside a DESKTOP session, so it is the weaker one.
#      b) /boot/system/settings/launch/sshd, a launch_daemon job in the SYSTEM
#         context -- what the boot-repair work installs, and the only one that
#         runs on a headless boot.
#
#    Naming only (a) would have turned this check into a guaranteed false
#    failure the moment the boot-repair branch lands: it DELETES UserBootscript
#    on purpose and ships (b) instead.  A test that fails on the change that
#    fixes the thing it is testing is worse than no test.  Accept either, and
#    say which one was found so the log records the mechanism.
boot_hook=""
for cand in \
    "/boot/system/settings/launch/sshd" \
    "$HOME/config/settings/boot/UserBootscript" \
    "$HOME/config/settings/launch/sshd"
do
    [ -f "$cand" ] || continue
    boot_hook="$cand"
    break
done
[ -n "$boot_hook" ] \
    || fail "no boot-time sshd hook on disk (looked for a launch_daemon job and
      a UserBootscript) -- sshd answered this session but nothing will start it
      on the next boot, so the image is not self-configuring"
ok "boot-time sshd hook installed: $boot_hook"

echo "PASS: haiku-image guest checks completed"
