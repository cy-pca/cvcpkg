#!/usr/bin/env bash
# recipes/kitty/test.sh — smoke test the staged kitty.
#
#  1. kitty and kitten report the pinned version.
#  2. Relocatability: the staged tree is copied to a different path and
#     everything below runs from the COPY. On linux the copy also gets the
#     dependencies' libraries, fontconfig's config, the fonts and the X11
#     data, and LD_LIBRARY_PATH is dropped, so the RPATHs and the bin/
#     launchers are what is tested.
#  3. kitty's own Python loads its C extension (fast_data_types: cairo,
#     FreeType, HarfBuzz and, on linux, fontconfig from the prefix) and lists
#     at least one monospace font family.
#  4. linux: a real window. Xvfb (a build dependency) runs with its screen in
#     a file, kitty runs kitty-placeholder-probe.py in a window, and
#     probe-check.py measures that screen until panel B's picture -- sent as
#     arigl sends its frames, a=T,U=1 -- covers its placeholder grid. Pixels,
#     not "the window opened". Software OpenGL from the build dependency
#     `mesa`; no GPU and no desktop needed. CVC_KITTY_GUI_TEST=0 skips it,
#     =1 turns every reason to skip it into a failure;
#     CVC_KITTY_SCREENSHOT=<file.png> keeps the final screen.
#
# Config, cache and runtime state all point into a temp dir, and the D-Bus
# session bus is pointed at nothing, so a user's kitty.conf, a desktop session
# or a running kitty cannot leak in.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VER="0.49.2"

echo "-- kitty smoke test (${CVC_PLATFORM:-native}) --"

T="$(mktemp -d)"
PIDS=""
cleanup() {
    for p in ${PIDS}; do kill "${p}" 2>/dev/null || true; done
    rm -rf "${T}"
}
trap cleanup EXIT

# ── 1. versions ─────────────────────────────────────────────────────────
for b in kitty kitten; do
    v="$("${CVC_INSTALL_DIR}/bin/${b}" --version)"
    [ "${v}" = "${b} ${VER} created by Kovid Goyal" ] \
        || { echo "FAIL: ${b} --version: '${v}'"; exit 1; }
    echo "  ${v}"
done

# ── 2. relocate: copy the staged tree, test the copy from here on ───────
MOVED="${T}/relocated/prefix"
mkdir -p "${MOVED}"
cp -pR "${CVC_INSTALL_DIR}/." "${MOVED}/"
if [ "$(uname -s)" = "Linux" ] && [ -n "${CVC_DEPS_PREFIX:-}" ]; then
    # Make the copy a whole prefix -- the dependencies' libraries, the
    # fontconfig config and fonts and the X11 data beside it -- and drop
    # what the test harness adds, so that what runs below is the RPATHs and
    # the bin/ launchers' defaults, not LD_LIBRARY_PATH.
    # (-L: xkeyboard-config's share/X11/xkb links to ../xkeyboard-config-2,
    # and fontconfig's etc/fonts/conf.d/*.conf to share/fontconfig/conf.avail.
    # Without those rules "monospace" means nothing and kitty gets DejaVu Sans.)
    mkdir -p "${MOVED}/lib" "${MOVED}/share" "${MOVED}/etc"
    cp -pP "${CVC_DEPS_PREFIX}"/lib/*.so* "${MOVED}/lib/"
    if [ -d "${CVC_DEPS_PREFIX}/lib/dri" ]; then cp -pR "${CVC_DEPS_PREFIX}/lib/dri" "${MOVED}/lib/"; fi
    cp -pRL "${CVC_DEPS_PREFIX}/share/X11" "${MOVED}/share/"
    if [ -d "${CVC_DEPS_PREFIX}/etc/fonts" ]; then cp -pRL "${CVC_DEPS_PREFIX}/etc/fonts" "${MOVED}/etc/"; fi
    if [ -d "${CVC_DEPS_PREFIX}/share/fonts" ]; then cp -pR "${CVC_DEPS_PREFIX}/share/fonts" "${MOVED}/share/"; fi
    unset LD_LIBRARY_PATH XKB_CONFIG_ROOT XLOCALEDIR
fi
K="${MOVED}/bin/kitty"

export HOME="${T}/home"
export XDG_CONFIG_HOME="${T}/config" XDG_CACHE_HOME="${T}/cache" XDG_DATA_HOME="${T}/data"
export KITTY_CONFIG_DIRECTORY="${T}/config/kitty" KITTY_CACHE_DIRECTORY="${T}/cache/kitty"
export XDG_RUNTIME_DIR="${T}/run"
mkdir -p "${HOME}" "${XDG_CONFIG_HOME}" "${XDG_CACHE_HOME}" "${XDG_DATA_HOME}" "${XDG_RUNTIME_DIR}"
chmod 700 "${XDG_RUNTIME_DIR}"
# kitty asks the desktop portal on the session bus for settings at start; on
# a bus where nothing answers, each call waits out a 25 s D-Bus timeout. A
# bus address where nothing listens fails at once (see the recipe notes).
export DBUS_SESSION_BUS_ADDRESS="unix:path=${T}/no-bus"

v="$("${K}" --version)"
[ "${v}" = "kitty ${VER} created by Kovid Goyal" ] \
    || { echo "FAIL: relocated kitty --version: '${v}'"; exit 1; }
echo "  OK: ${v} (relocated copy)"

# ── 3. kitty's Python, its C extension, fonts ───────────────────────────
fonts="$("${K}" +runpy 'import sys
fonts = __import__("kitty.fonts." + ("core_text" if sys.platform == "darwin" else "fontconfig"), fromlist=["all_fonts_map"])
print("\n".join(sorted(fonts.all_fonts_map(True)["family_map"])))' 2>&1)" \
    || { echo "FAIL: kitty +runpy (bundled Python / fast_data_types)"; echo "${fonts}"; exit 1; }
[ -n "${fonts}" ] || { echo "FAIL: kitty found no monospace font"; exit 1; }
echo "  OK: fast_data_types loads; monospace families: $(echo "${fonts}" | head -n 5 | tr '\n' ',' | sed 's/,$//')"

# ── 4. a window, and the kitty graphics placeholders in it ──────────────
gui="${CVC_KITTY_GUI_TEST:-auto}"
skip() {
    if [ "${gui}" = "1" ]; then echo "FAIL: window test: $1"; exit 1; fi
    echo "  SKIP: window test: $1"
    echo "-- kitty smoke test passed --"
    exit 0
}
[ "${gui}" != "0" ] || skip "CVC_KITTY_GUI_TEST=0"
[ "$(uname -s)" = "Linux" ] || skip "linux only (it runs kitty under Xvfb)"

XVFB=""
XPREFIX=""
for p in "${CVC_DEPS_PREFIX:-}" "${CVC_BUILD_PREFIX:-}"; do
    if [ -n "${p}" ] && [ -x "${p}/bin/Xvfb" ]; then XVFB="${p}/bin/Xvfb"; XPREFIX="${p}"; break; fi
done
[ -n "${XVFB}" ] || XVFB="$(command -v Xvfb || true)"
[ -n "${XVFB}" ] || skip "no Xvfb (cvcpkg xvfb, a linux build dependency)"

# What kitty's X11 back end links must resolve from the copy alone. The one
# library cvcpkg does not package is libdbus-1 (see the recipe notes).
GLFW_X11="${MOVED}/lib/kitty/lib/kitty-extensions/kitty.glfw-x11.so"
missing="$(ldd "${GLFW_X11}" | awk '/not found/ {print $1}' | sort -u | tr '\n' ' ')"
case "${missing}" in
    "") ;;
    "libdbus-1.so.3 ") skip "the host has no libdbus-1.so.3 (Debian/Ubuntu: libdbus-1-3)" ;;
    *) echo "FAIL: kitty's X11 back end cannot load: ${missing}"; ldd "${GLFW_X11}"; exit 1 ;;
esac

# -fbdir: the screen is the file FB/Xvfb_screen0 (XWD), always current; it
# appears once the server has set its screen up. The display number is picked
# here, not with -displayfd: that also insists on a socket in /tmp/.X11-unix,
# which under WSL is WSLg's and not writable, so every number fails. Given a
# number, Xvfb falls back to the abstract socket there and runs.
FB="${T}/fb"
mkdir -p "${FB}"
# Xvfb's own GLX loads Mesa's swrast_dri.so from the prefix it came from.
xenv=()
if [ -n "${XPREFIX}" ]; then xenv=(LIBGL_DRIVERS_PATH="${XPREFIX}/lib/dri"); fi
disp=""
for n in $(seq 90 130); do
    [ -e "/tmp/.X${n}-lock" ] && continue
    rm -f "${FB}/Xvfb_screen0"
    env ${xenv[@]+"${xenv[@]}"} "${XVFB}" ":${n}" -screen 0 1100x700x24 -fbdir "${FB}" -nolisten tcp \
        >"${T}/xvfb.log" 2>&1 &
    xpid=$!
    for _ in $(seq 1 100); do
        if [ -s "${FB}/Xvfb_screen0" ]; then disp="${n}"; break; fi
        kill -0 "${xpid}" 2>/dev/null || break
        sleep 0.1
    done
    if [ -n "${disp}" ]; then PIDS="${xpid}"; break; fi
    kill "${xpid}" 2>/dev/null || true
done
[ -n "${disp}" ] || { echo "FAIL: Xvfb did not start"; cat "${T}/xvfb.log"; exit 1; }
export DISPLAY=":${disp}"
unset WAYLAND_DISPLAY

# kitty's OpenGL: cvcpkg's Mesa (llvmpipe) in the copy, which its GLFW
# dlopens through the prefix RPATH. glvnd is told the vendor and Mesa where
# its drivers are, so that a GLX vendor the host's glvnd might fall back to
# (a distro's own, older Mesa) cannot take over. LIBGL_DEBUG puts Mesa's
# loader messages in the log.
glenv=(LIBGL_DEBUG=verbose)
if [ -d "${MOVED}/lib/dri" ]; then
    glenv+=(LIBGL_DRIVERS_PATH="${MOVED}/lib/dri" __GLX_VENDOR_LIBRARY_NAME=mesa)
fi
# The program in the window runs on kitty's own Python (+launch), so the test
# needs no Python from the host. --debug-rendering logs the GL version,
# --debug-font-fallback the fonts.
env "${glenv[@]}" "${K}" --config NONE --class org.cvcpkg.kitty-test --debug-rendering --debug-font-fallback \
    -o linux_display_server=x11 -o initial_window_width=1000 -o initial_window_height=600 \
    -o remember_window_size=no -o update_check_interval=0 -o allow_remote_control=no \
    -o confirm_os_window_close=0 -o font_size=11 \
    "${K}" +launch "${SCRIPT_DIR}/kitty-placeholder-probe.py" --hold 300 \
    > "${T}/kitty.log" 2>&1 &
KPID=$!
PIDS="${KPID} ${PIDS}"

diag() {
    sed 's/^/  kitty: /' "${T}/kitty.log" | tail -n 25
    grep -i -E 'glx|dri|swrast|mesa|\(EE\)' "${T}/xvfb.log" | sed 's/^/  xvfb: /' | head -n 15 || true
}

SHOT="${FB}/Xvfb_screen0"
rc=3
verdict=""
for _ in $(seq 1 120); do
    sleep 1
    if ! kill -0 "${KPID}" 2>/dev/null; then
        echo "FAIL: kitty exited"; diag; exit 1
    fi
    rc=0
    verdict="$("${K}" +launch "${SCRIPT_DIR}/probe-check.py" "${SHOT}")" || rc=$?
    [ "${rc}" = 3 ] || break
done
"${K}" +launch "${SCRIPT_DIR}/probe-check.py" "${SHOT}" --png "${T}/screen.png" >/dev/null || true
if [ -n "${CVC_KITTY_SCREENSHOT:-}" ]; then cp "${T}/screen.png" "${CVC_KITTY_SCREENSHOT}"; fi
# kitty writes the GL version to a buffered stdout; let it exit and flush.
kill "${KPID}" 2>/dev/null || true
for _ in $(seq 1 50); do kill -0 "${KPID}" 2>/dev/null || break; sleep 0.2; done
gl="$(sed -n "s/.*GL version string: '\([^']*\)'.*/\1/p" "${T}/kitty.log" | head -n 1)"
font="$(sed -n 's/.* Normal: \([^:]*\):.*/\1/p' "${T}/kitty.log" | head -n 1)"

echo "  screen: ${verdict}"
case "${rc}" in
    0) echo "  OK: kitty draws the a=T,U=1 picture on its placeholder cells [GL: ${gl:-?}; font: ${font:-?}]" ;;
    2) echo "FAIL: kitty drew the U=1 picture beside its placeholder grid"; exit 1 ;;
    3) echo "FAIL: the probe's pictures did not appear within 120 s"; diag; exit 1 ;;
    *) echo "FAIL: unexpected screen"; diag; exit 1 ;;
esac
grep -E '^libGL: ' "${T}/kitty.log" | sort -u | sed 's/^/  /' | head -n 5 || true

echo "-- kitty smoke test passed --"
