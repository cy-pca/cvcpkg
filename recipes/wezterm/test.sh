#!/usr/bin/env bash
# recipes/wezterm/test.sh — smoke test the staged WezTerm.
#
#  1. wezterm and wezterm-mux-server report the pinned version (wezterm-gui:
#     see below).
#  2. Relocatability: the staged tree is copied to a different path and
#     everything below runs from the COPY. On linux the copy also gets the
#     runtime dependencies' libraries and X11 data, and LD_LIBRARY_PATH is
#     dropped, so the RUNPATH and the bin/ launchers are what is tested.
#  3. strip-ansi-escapes filters a coloured string; `wezterm ls-fonts` loads
#     wezterm-gui (on linux: every X11/Wayland/fontconfig library, from the
#     prefix) and resolves the built-in font.
#  4. A real window: `wezterm start --always-new-process -- <cmd>` opens one,
#     runs <cmd> in it -- which gets an answer to a kitty graphics query --
#     and WezTerm's own Lua reports the window's pixel size and its version
#     before quitting. Needs a desktop: on by default on linux when DISPLAY
#     or WAYLAND_DISPLAY is set and on Windows when the process runs on the
#     visible desktop ([Environment]::UserInteractive; false for a service
#     such as an OpenSSH session). CVC_WEZTERM_GUI_TEST=1 forces it, =0
#     skips it. CVC_WEZTERM_FRONT_END picks the renderer (default Software:
#     CPU rasterisation, no GPU needed -- on Windows that is the bundled
#     mesa\opengl32.dll).
#
# Config, cache and runtime state all point into a temp dir, so a user's
# ~/.wezterm.lua or a running WezTerm cannot leak in.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
EXPECT="wezterm 20240203-110809-5046fc22"

echo "-- wezterm smoke test (${CVC_PLATFORM:-native}) --"

T="$(mktemp -d)"
cleanup() { rm -rf "${T}"; }
trap cleanup EXIT

EXE=""
if [ -f "${CVC_INSTALL_DIR}/bin/wezterm.exe" ]; then EXE=".exe"; fi

# ── 1. versions ─────────────────────────────────────────────────────────
# wezterm-gui --version is broken in this release (it prints "wezterm-gui
# someone forgot to call assign_version_info"), so the GUI's version is
# checked from inside it instead: the window test reads wezterm.version.
for b in wezterm wezterm-mux-server; do
    v="$("${CVC_INSTALL_DIR}/bin/${b}${EXE}" --version | tr -d '\r')"
    [ "${v}" = "${b} ${EXPECT#* }" ] || { echo "FAIL: ${b} --version: '${v}', want '${b} ${EXPECT#* }'"; exit 1; }
    echo "  ${v}"
done
"${CVC_INSTALL_DIR}/bin/wezterm-gui${EXE}" --version >/dev/null \
    || { echo "FAIL: wezterm-gui does not start"; exit 1; }

# ── 2. relocate: copy the staged tree, test the copy from here on ───────
MOVED="${T}/relocated/prefix"
mkdir -p "${MOVED}"
cp -pR "${CVC_INSTALL_DIR}/." "${MOVED}/"
W="${MOVED}/bin/wezterm${EXE}"
if [ "$(uname -s)" = "Linux" ] && [ -n "${CVC_DEPS_PREFIX:-}" ]; then
    # Make the copy a whole prefix -- the runtime dependencies' libraries and
    # X11 data beside it -- and drop what the test harness adds, so that what
    # runs below is the $ORIGIN RUNPATH and the bin/ launchers' defaults, not
    # LD_LIBRARY_PATH.
    # (-L: xkeyboard-config's share/X11/xkb links to ../xkeyboard-config-2.)
    mkdir -p "${MOVED}/lib" "${MOVED}/share"
    cp -pP "${CVC_DEPS_PREFIX}"/lib/*.so* "${MOVED}/lib/"
    cp -pRL "${CVC_DEPS_PREFIX}/share/X11" "${MOVED}/share/"
    unset LD_LIBRARY_PATH XKB_CONFIG_ROOT XLOCALEDIR
fi

export HOME="${T}/home"
export XDG_CONFIG_HOME="${T}/config" XDG_CACHE_HOME="${T}/cache" XDG_DATA_HOME="${T}/data"
# A relative WAYLAND_DISPLAY names a socket in XDG_RUNTIME_DIR; keep reaching
# the compositor once the runtime dir moves.
case "${WAYLAND_DISPLAY:-}" in
    ""|/*) ;;
    *) [ -n "${XDG_RUNTIME_DIR:-}" ] && export WAYLAND_DISPLAY="${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}" ;;
esac
export XDG_RUNTIME_DIR="${T}/run"
mkdir -p "${HOME}" "${XDG_CONFIG_HOME}" "${XDG_CACHE_HOME}" "${XDG_DATA_HOME}" "${XDG_RUNTIME_DIR}"
chmod 700 "${XDG_RUNTIME_DIR}"
# Windows: wezterm keys its config and runtime dirs off USERPROFILE.
if [ -n "${EXE}" ]; then export USERPROFILE="$(cygpath -w "${HOME}" 2>/dev/null || echo "${HOME}")"; fi

v="$("${W}" --version | tr -d '\r')"
[ "${v}" = "${EXPECT}" ] || { echo "FAIL: relocated wezterm --version: '${v}'"; exit 1; }
echo "  OK: ${v} (relocated copy)"

# ── 3. strip-ansi-escapes, ls-fonts ─────────────────────────────────────
out="$(printf '\033[1;31mcvc\033[0mpkg\n' | "${MOVED}/bin/strip-ansi-escapes${EXE}" | tr -d '\r')"
[ "${out}" = "cvcpkg" ] || { echo "FAIL: strip-ansi-escapes gave '${out}'"; exit 1; }
echo "  OK: strip-ansi-escapes"

# (tr also drops the NULs wezterm-gui.exe writes to a pipe on Windows.)
fonts="$("${W}" -n ls-fonts --text 'Ab' 2>&1 | tr -d '\r\000')" \
    || { echo "FAIL: wezterm ls-fonts"; echo "${fonts}"; exit 1; }
echo "${fonts}" | grep -q 'JetBrains Mono' \
    || { echo "FAIL: ls-fonts did not resolve the built-in JetBrains Mono:"; echo "${fonts}"; exit 1; }
echo "  OK: ls-fonts resolves the built-in JetBrains Mono"

# ── 4. a window ─────────────────────────────────────────────────────────
gui="${CVC_WEZTERM_GUI_TEST:-auto}"
if [ "${gui}" = "auto" ]; then
    gui=0
    case "$(uname -s)" in
        Linux) [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && gui=1 ;;
        MINGW*|MSYS*|CYGWIN*)
            ui="$(powershell.exe -NoProfile -NonInteractive -Command '[Environment]::UserInteractive' 2>/dev/null | tr -d '\r' || true)"
            [ "${ui}" = "True" ] && gui=1 ;;
    esac
fi
if [ "${gui}" != "1" ]; then
    echo "  SKIP: window test (no desktop session; CVC_WEZTERM_GUI_TEST=1 forces it)"
    echo "-- wezterm smoke test passed --"
    exit 0
fi

# Paths handed to WezTerm (a native program on Windows) and read back from Lua.
native() { if [ -n "${EXE}" ]; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
CHILD_MARK="${T}/child.txt"
WIN_MARK="${T}/window.txt"
CFG="${T}/wezterm-test.lua"
cat > "${CFG}" <<EOF
local wezterm = require 'wezterm'
-- update-status fires about once a second for every GUI window. Once the
-- program in the pane has written its marker, record what the window
-- reports and quit.
wezterm.on('update-status', function(window, pane)
  local child = io.open('$(native "${CHILD_MARK}")', 'r')
  if not child then return end
  child:close()
  local d = window:get_dimensions()
  local f = io.open('$(native "${WIN_MARK}")', 'w')
  f:write(string.format('window %d %dx%d px, %s dpi, wezterm %s\n',
    window:window_id(), d.pixel_width, d.pixel_height, tostring(d.dpi), wezterm.version))
  f:close()
  window:perform_action(wezterm.action.QuitApplication, pane)
end)
return {
  front_end = '${CVC_WEZTERM_FRONT_END:-Software}',
  initial_cols = 80,
  initial_rows = 24,
  status_update_interval = 250,
  window_close_confirmation = 'NeverPrompt',
  skip_close_confirmation_for_processes_named = { 'bash', 'bash.exe', 'sh', 'sleep', 'sleep.exe' },
}
EOF

# The program in the pane proves it runs inside this WezTerm, then asks it a
# kitty graphics query (a=q: "would you load this 1x1 image?") and records
# the reply. On Windows that round trip crosses ConPTY both ways -- the
# bundled ConPTY used to drop the query, so kitty images never arrived. The
# marker is renamed into place so the Lua side never reads half of it. On
# Windows the program is Git Bash's bash.exe, named by its native path.
SH="${BASH}"
if [ -n "${EXE}" ]; then SH="$(native "${BASH}")"; [ -f "${SH}.exe" ] && SH="${SH}.exe"; fi
CHILD_SH="${T}/child.sh"
cat > "${CHILD_SH}" <<'EOF'
printf '%s %s pane=%s\n' "${TERM_PROGRAM:-}" "${TERM_PROGRAM_VERSION:-}" "${WEZTERM_PANE:-}" > "$1.tmp"
printf '\033_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\033\\'
reply=""
IFS= read -rs -t 10 -d '\' reply || true
printf 'kitty-reply=%s\n' "$(printf '%s' "${reply}" | tr -d '\033')" >> "$1.tmp"
mv "$1.tmp" "$1"
sleep 120
EOF

"${W}" --config-file "$(native "${CFG}")" start --always-new-process \
    --class org.cvcpkg.wezterm-test -- "${SH}" "$(native "${CHILD_SH}")" "$(native "${CHILD_MARK}")" \
    > "${T}/gui.log" 2>&1 &
GUI_PID=$!
for _ in $(seq 1 120); do
    [ -s "${WIN_MARK}" ] && break
    kill -0 "${GUI_PID}" 2>/dev/null || break
    sleep 0.5
done
if [ ! -s "${WIN_MARK}" ]; then
    echo "FAIL: no window reported within 60 s"
    [ -s "${CHILD_MARK}" ] && echo "  (the program in the pane did run: $(cat "${CHILD_MARK}"))"
    sed 's/^/  gui: /' "${T}/gui.log"
    kill "${GUI_PID}" 2>/dev/null || true
    exit 1
fi
for _ in $(seq 1 40); do kill -0 "${GUI_PID}" 2>/dev/null || break; sleep 0.5; done
if kill -0 "${GUI_PID}" 2>/dev/null; then
    echo "FAIL: wezterm did not exit after QuitApplication"; kill "${GUI_PID}" 2>/dev/null || true; exit 1
fi

child="$(head -n 1 "${CHILD_MARK}" | tr -d '\r')"
case "${child}" in
    "WezTerm ${EXPECT#* } pane="[0-9]*) ;;
    *) echo "FAIL: the pane's program saw '${child}'"; exit 1 ;;
esac
echo "  OK: pane program ran inside WezTerm (${child})"
kitty="$(sed -n 's/^kitty-reply=//p' "${CHILD_MARK}" | tr -d '\r')"
[ "${kitty}" = "_Gi=31;OK" ] \
    || { echo "FAIL: kitty graphics query from the pane got '${kitty}', want '_Gi=31;OK'"; exit 1; }
echo "  OK: kitty graphics query answered (${kitty}) -- escapes reach WezTerm from the pane"
win="$(tr -d '\r' < "${WIN_MARK}")"
case "${win}" in
    "window "*" "[1-9]*x[1-9]*" px, "*" dpi, wezterm ${EXPECT#* }") ;;
    *) echo "FAIL: the window reported '${win}'"; exit 1 ;;
esac
echo "  OK: ${win} [front_end ${CVC_WEZTERM_FRONT_END:-Software}]"

echo "-- wezterm smoke test passed --"
