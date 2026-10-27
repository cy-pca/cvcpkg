#!/usr/bin/env bash
# recipes/ghostty/test.sh — smoke test the staged Ghostty.
#
#  1. `ghostty --version` reports the pinned version; on linux also the GTK
#     front end and the GTK / libadwaita it runs against.
#  2. Relocatability: the staged tree is copied to a different path and
#     everything below runs from the COPY. On linux the copy also gets the
#     runtime dependencies' libraries and LD_LIBRARY_PATH is dropped for the
#     CLI checks, so the $ORIGIN RUNPATH is what is tested; `+list-themes`
#     proves Ghostty found its share/ghostty from its own location.
#  3. linux: real graphics, headless. Xvfb (cvcpkg xvfb) with software
#     OpenGL (cvcpkg mesa, llvmpipe) hosts a Ghostty window running
#     kitty-placeholder-probe.py; the screen dump must show the probe's U=1
#     picture ON its placeholder grid (kitty-screenshot-check.py). A window
#     that opens but draws nothing, or draws the picture beside the grid,
#     fails. CVC_GHOSTTY_GUI_TEST=0 skips it, =1 fails when Xvfb is missing
#     (default: run when Xvfb is on PATH). CVC_GHOSTTY_SCREENSHOT=<file.png>
#     keeps the screenshot.
#  macOS: the app's signature is re-verified in place; no window is opened.
#
# Config, cache and runtime state all point into a temp dir, so a user's
# ~/.config/ghostty or a running Ghostty cannot leak in.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
EXPECT="1.3.1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "-- ghostty smoke test (${CVC_PLATFORM:-native}) --"

T="$(mktemp -d)"
PIDS=""
cleanup() {
    for p in ${PIDS}; do kill "${p}" 2>/dev/null || true; done
    for p in ${PIDS}; do wait "${p}" 2>/dev/null || true; done
    rm -rf "${T}"
}
trap cleanup EXIT

export HOME="${T}/home"
export XDG_CONFIG_HOME="${T}/config" XDG_CACHE_HOME="${T}/cache" XDG_DATA_HOME="${T}/data"
export XDG_STATE_HOME="${T}/state" XDG_RUNTIME_DIR="${T}/run"
mkdir -p "${HOME}" "${XDG_CONFIG_HOME}/ghostty" "${XDG_CACHE_HOME}" "${XDG_DATA_HOME}" \
         "${XDG_STATE_HOME}" "${XDG_RUNTIME_DIR}"
chmod 700 "${XDG_RUNTIME_DIR}"
unset GHOSTTY_RESOURCES_DIR GHOSTTY_BIN_DIR TERMINFO || true

# ── 1. version ──────────────────────────────────────────────────────────
ver="$("${CVC_INSTALL_DIR}/bin/ghostty" --version)"
echo "${ver}" | sed 's/^/  | /'
[ "$(echo "${ver}" | sed -n 1p)" = "Ghostty ${EXPECT}" ] \
    || { echo "FAIL: first line of --version is not 'Ghostty ${EXPECT}'"; exit 1; }
echo "${ver}" | grep -q 'channel: stable' || { echo "FAIL: not a stable-channel build"; exit 1; }

if [ "$(uname -s)" = "Darwin" ]; then
    APP="${CVC_INSTALL_DIR}/lib/ghostty/Ghostty.app"
    codesign --verify --deep --strict "${APP}"
    echo "  OK: Ghostty.app signature still valid"
    MOVED="${T}/relocated/prefix"
    mkdir -p "${MOVED}"
    ditto "${CVC_INSTALL_DIR}" "${MOVED}"
    v="$("${MOVED}/bin/ghostty" --version | sed -n 1p)"
    [ "${v}" = "Ghostty ${EXPECT}" ] || { echo "FAIL: relocated ghostty --version: '${v}'"; exit 1; }
    echo "  OK: ${v} (relocated copy, through a symlink-free launcher)"
    echo "  SKIP: window test (linux only)"
    echo "-- ghostty smoke test passed --"
    exit 0
fi

echo "${ver}" | grep -qE 'app runtime *: *apprt.Runtime.gtk' || { echo "FAIL: not the GTK front end"; exit 1; }
echo "${ver}" | grep -qE 'renderer *: *renderer.OpenGL' || { echo "FAIL: not the OpenGL renderer"; exit 1; }
# GTK and libadwaita: what the binary was built against and what it loaded.
gtk_rt="$(echo "${ver}" | sed -n '/GTK version/,/libadwaita/p' | sed -n 's/^ *runtime *: *//p' | head -n 1)"
adw_rt="$(echo "${ver}" | sed -n '/libadwaita/,$p' | sed -n 's/^ *runtime *: *//p' | head -n 1)"
case "${gtk_rt}" in 4.*) ;; *) echo "FAIL: GTK runtime version '${gtk_rt}'"; exit 1 ;; esac
case "${adw_rt}" in 1.*) ;; *) echo "FAIL: libadwaita runtime version '${adw_rt}'"; exit 1 ;; esac
echo "  OK: Ghostty ${EXPECT}, GTK ${gtk_rt}, libadwaita ${adw_rt}"

# ── 2. relocate ─────────────────────────────────────────────────────────
MOVED="${T}/relocated/prefix"
mkdir -p "${MOVED}/lib"
cp -pR "${CVC_INSTALL_DIR}/." "${MOVED}/"
if [ -n "${CVC_DEPS_PREFIX:-}" ]; then
    cp -pP "${CVC_DEPS_PREFIX}"/lib/*.so* "${MOVED}/lib/"
    # GTK and fontconfig data: settings schemas, gdk-pixbuf loaders, fonts.conf.
    for d in share/glib-2.0 share/fontconfig etc/fonts lib/gdk-pixbuf-2.0; do
        if [ -d "${CVC_DEPS_PREFIX}/${d}" ]; then
            mkdir -p "$(dirname "${MOVED}/${d}")"
            cp -pR "${CVC_DEPS_PREFIX}/${d}" "${MOVED}/${d}"
        fi
    done
fi
G="${MOVED}/bin/ghostty"
missing="$(env -u LD_LIBRARY_PATH ldd "${G}" | grep 'not found' || true)"
[ -z "${missing}" ] || { echo "FAIL: relocated ghostty cannot resolve:"; echo "${missing}"; exit 1; }
v="$(env -u LD_LIBRARY_PATH "${G}" --version | sed -n 1p)"
[ "${v}" = "Ghostty ${EXPECT}" ] || { echo "FAIL: relocated ghostty --version: '${v}'"; exit 1; }
echo "  OK: ${v} (relocated copy, RUNPATH only)"
themes="$(env -u LD_LIBRARY_PATH "${G}" +list-themes --plain | wc -l)"
[ "${themes}" -gt 100 ] || { echo "FAIL: +list-themes found ${themes} themes (share/ghostty not found?)"; exit 1; }
echo "  OK: ${themes} themes from the copy's share/ghostty"
cat > "${XDG_CONFIG_HOME}/ghostty/config" <<'EOF'
font-size = 12
window-width = 100
window-height = 30
window-decoration = none
background = 000000
foreground = c0c0c0
cursor-style-blink = false
resize-overlay = never
confirm-close-surface = false
quit-after-last-window-closed = true
gtk-single-instance = false
linux-cgroup = never
shell-integration = none
EOF
env -u LD_LIBRARY_PATH "${G}" +validate-config \
    || { echo "FAIL: the test config does not validate"; exit 1; }
echo "  OK: +validate-config"

# ── 3. a window, headless ───────────────────────────────────────────────
gui="${CVC_GHOSTTY_GUI_TEST:-auto}"
XVFB="$(command -v Xvfb || true)"
if [ "${gui}" = "0" ] || { [ "${gui}" = "auto" ] && [ -z "${XVFB}" ]; }; then
    echo "  SKIP: window test (${XVFB:+CVC_GHOSTTY_GUI_TEST=0}${XVFB:-no Xvfb on PATH})"
    echo "-- ghostty smoke test passed --"
    exit 0
fi
[ -n "${XVFB}" ] || { echo "FAIL: CVC_GHOSTTY_GUI_TEST=1 but no Xvfb on PATH"; exit 1; }
PY="$(command -v python3)" || { echo "FAIL: no python3 for the probe"; exit 1; }

dpy=""
for n in $(seq 70 99); do
    if [ ! -e "/tmp/.X${n}-lock" ] && [ ! -e "/tmp/.X11-unix/X${n}" ]; then dpy="${n}"; break; fi
done
[ -n "${dpy}" ] || { echo "FAIL: no free X display number in :70-:99"; exit 1; }
FB="${T}/fb"
mkdir -p "${FB}"
"${XVFB}" ":${dpy}" -screen 0 1280x800x24 -fbdir "${FB}" -nolisten tcp -noreset \
    > "${T}/xvfb.log" 2>&1 &
PIDS="${PIDS} $!"
for _ in $(seq 1 50); do [ -e "/tmp/.X11-unix/X${dpy}" ] && break; sleep 0.2; done
[ -e "/tmp/.X11-unix/X${dpy}" ] || { echo "FAIL: Xvfb did not start"; sed 's/^/  xvfb: /' "${T}/xvfb.log"; exit 1; }

# Ghostty under X11 on Xvfb with Mesa's llvmpipe: the copy's libraries first,
# then the build prefix's (mesa, libglvnd). No session bus: GTK's
# accessibility bridge is switched off rather than left to time out.
GL_LIBS="${CVC_BUILD_PREFIX:+${CVC_BUILD_PREFIX}/lib:}${CVC_DEPS_PREFIX:+${CVC_DEPS_PREFIX}/lib}"
env DISPLAY=":${dpy}" GDK_BACKEND=x11 GTK_A11Y=none NO_AT_BRIDGE=1 \
    LD_LIBRARY_PATH="${MOVED}/lib${GL_LIBS:+:${GL_LIBS}}" \
    "${G}" -e "${PY}" "${SCRIPT_DIR}/kitty-placeholder-probe.py" --hold 60 \
    > "${T}/ghostty.log" 2>&1 &
GPID=$!
PIDS="${GPID} ${PIDS}"

CHECK="${SCRIPT_DIR}/kitty-screenshot-check.py"
result=""
rc=3
for _ in $(seq 1 90); do
    sleep 1
    kill -0 "${GPID}" 2>/dev/null || break
    cp "${FB}/Xvfb_screen0" "${T}/shot.xwd"
    rc=0; result="$("${PY}" "${CHECK}" "${T}/shot.xwd" --wait-for-a)" || rc=$?
    [ "${rc}" = 3 ] || break
done
if [ "${rc}" = 0 ]; then
    # Panel A is there and B with it; give B a moment in case it was mid-draw.
    sleep 2
    cp "${FB}/Xvfb_screen0" "${T}/shot.xwd"
    rc=0; result="$("${PY}" "${CHECK}" "${T}/shot.xwd" ${CVC_GHOSTTY_SCREENSHOT:+--png "${CVC_GHOSTTY_SCREENSHOT}"})" || rc=$?
elif [ -n "${CVC_GHOSTTY_SCREENSHOT:-}" ] && [ -f "${T}/shot.xwd" ]; then
    "${PY}" "${CHECK}" "${T}/shot.xwd" --png "${CVC_GHOSTTY_SCREENSHOT}" >/dev/null || true
fi
if [ "${rc}" != 0 ]; then
    if kill -0 "${GPID}" 2>/dev/null; then
        echo "FAIL: ${result:-no screenshot}"
    else
        echo "FAIL: ghostty exited before drawing the probe (${result:-no screenshot})"
    fi
    sed 's/^/  ghostty: /' "${T}/ghostty.log" | tail -n 40
    exit 1
fi
echo "  ${result}"
grep -iE 'error|critical' "${T}/ghostty.log" | sed 's/^/  ghostty log: /' | head -n 10 || true

echo "-- ghostty smoke test passed --"
