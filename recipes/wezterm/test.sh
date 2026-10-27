#!/usr/bin/env bash
# recipes/wezterm/test.sh — smoke test the staged WezTerm.
#
#  1. wezterm and wezterm-mux-server report the pinned version (wezterm-gui:
#     see below).
#  2. Relocatability: the staged tree is copied to a different path and
#     everything below runs from the COPY. On linux the copy also gets the
#     runtime dependencies' libraries and X11 data, and LD_LIBRARY_PATH is
#     dropped, so the RUNPATH and the bin/ launchers are what is tested; on
#     Windows it gets OpenSSL's and ConPTY's DLLs beside the executables.
#  3. strip-ansi-escapes filters a coloured string; `wezterm ls-fonts` loads
#     wezterm-gui (on linux: every X11/Wayland/fontconfig library, from the
#     prefix) and resolves the built-in font.
#  4. A real window: `wezterm start --always-new-process -- <cmd>` opens one,
#     runs <cmd> in it -- which gets an answer to a kitty graphics query --
#     and WezTerm's own Lua reports the window's pixel size and its version
#     before quitting. On Windows the window is also captured and its pixels
#     checked: glyphs must draw as glyphs, not filled cells, and a kitty image
#     must appear. Needs a desktop: on by default on linux when DISPLAY
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
elif [ -n "${EXE}" ] && [ -n "${CVC_DEPS_PREFIX:-}" ]; then
    # Windows: OpenSSL's DLLs and ConPTY (the openssl and conpty packages),
    # which an installed prefix has in the same bin\ -- wezterm-gui loads
    # conpty.dll from its own directory, and the kitty check below fails with
    # any other ConPTY.
    cp -p "${CVC_DEPS_PREFIX}"/bin/libssl-3*.dll "${CVC_DEPS_PREFIX}"/bin/libcrypto-3*.dll \
          "${CVC_DEPS_PREFIX}/bin/conpty.dll" "${CVC_DEPS_PREFIX}/bin/OpenConsole.exe" "${MOVED}/bin/"
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
QUIT_MARK="${T}/quit.txt"
CFG="${T}/wezterm-test.lua"
cat > "${CFG}" <<EOF
local wezterm = require 'wezterm'
-- update-status fires several times a second for every GUI window. Once the
-- program in the pane has written its marker, record what the window
-- reports; once test.sh is done looking at the window, quit.
local reported = false
wezterm.on('update-status', function(window, pane)
  local child = io.open('$(native "${CHILD_MARK}")', 'r')
  if not child then return end
  child:close()
  if not reported then
    local d = window:get_dimensions()
    local f = io.open('$(native "${WIN_MARK}")', 'w')
    f:write(string.format('window %d %dx%d px, %s dpi, wezterm %s\n',
      window:window_id(), d.pixel_width, d.pixel_height, tostring(d.dpi), wezterm.version))
    f:close()
    reported = true
  end
  local quit = io.open('$(native "${QUIT_MARK}")', 'r')
  if quit then
    quit:close()
    window:perform_action(wezterm.action.QuitApplication, pane)
  end
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
#
# Before the marker it draws what the Windows pixel check below looks for: a
# line of 40 red dots, a line of 40 green full blocks and a 32x32 blue kitty
# image.
SH="${BASH}"
if [ -n "${EXE}" ]; then SH="$(native "${BASH}")"; [ -f "${SH}.exe" ] && SH="${SH}.exe"; fi
CHILD_SH="${T}/child.sh"
cat > "${CHILD_SH}" <<'EOF'
printf '%s %s pane=%s\n' "${TERM_PROGRAM:-}" "${TERM_PROGRAM_VERSION:-}" "${WEZTERM_PANE:-}" > "$1.tmp"
printf '\033_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\033\\'
reply=""
IFS= read -rs -t 10 -d '\' reply || true
printf 'kitty-reply=%s\n' "$(printf '%s' "${reply}" | tr -d '\033')" >> "$1.tmp"
dots="$(printf '.%.0s' $(seq 1 40))"
blocks="$(printf '\342\226\210%.0s' $(seq 1 40))"
printf '\033[2J\033[H\033[31m%s\033[0m\r\n\033[32m%s\033[0m\r\n' "${dots}" "${blocks}"
blue="$(printf '\000\000\377%.0s' $(seq 1 1024) | base64 | tr -d '\n')"
printf '\033_Ga=T,f=24,s=32,v=32,i=32,q=2;%s\033\\' "${blue}"
mv "$1.tmp" "$1"
sleep 120
EOF

# Windows: what the window shows. PrintWindow (PW_RENDERFULLCONTENT) copies it
# even when covered; then count the red, green and blue pixels. Rendering that
# works draws dots that are a small fraction of the blocks and a blue square.
# A renderer that fills glyph cells instead of drawing glyphs -- what glium
# 0.31 did under Rust 1.90 (recipe.yaml, patches/0002) -- gives as much red as
# green and no blue, and so does a window that never got the image.
pixel_check() {
    cat > "${T}/shot.ps1" <<'EOF'
param([string]$Dir, [string]$Out)
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Drawing @"
using System; using System.Drawing; using System.Drawing.Imaging;
using System.Runtime.InteropServices;
public static class CvcShot {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  public static string Count(IntPtr h, string png) {
    RECT r; GetWindowRect(h, out r);
    using (var bmp = new Bitmap(r.R - r.L, r.B - r.T, PixelFormat.Format32bppArgb)) {
      using (var g = Graphics.FromImage(bmp)) {
        IntPtr dc = g.GetHdc(); PrintWindow(h, dc, 2); g.ReleaseHdc(dc);
      }
      bmp.Save(png);
      var d = bmp.LockBits(new Rectangle(0, 0, bmp.Width, bmp.Height),
                           ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
      var px = new byte[d.Stride * d.Height];
      Marshal.Copy(d.Scan0, px, 0, px.Length);
      bmp.UnlockBits(d);
      long red = 0, green = 0, blue = 0, white = 0;
      for (int y = 0; y < bmp.Height; y++) for (int x = 0; x < bmp.Width; x++) {
        int i = y * d.Stride + x * 4, b = px[i], gr = px[i + 1], rd = px[i + 2];
        if (rd > 150 && gr < 100 && b < 100) red++;
        else if (gr > 150 && rd < 100 && b < 100) green++;
        else if (b > 200 && rd < 60 && gr < 60) blue++;
        else if (rd > 250 && gr > 250 && b > 250) white++;
      }
      if (white * 10 >= (long)bmp.Width * bmp.Height * 9) return "blank";
      return red + " " + green + " " + blue;
    }
  }
}
"@
[CvcShot]::SetProcessDPIAware() | Out-Null
$h = [IntPtr]::Zero
for ($i = 0; $i -lt 40 -and $h -eq [IntPtr]::Zero; $i++) {
  foreach ($p in Get-Process wezterm-gui -ErrorAction SilentlyContinue) {
    if ($p.Path -and $p.Path.StartsWith($Dir, 'OrdinalIgnoreCase') -and $p.MainWindowHandle -ne [IntPtr]::Zero) {
      $h = $p.MainWindowHandle; break
    }
  }
  if ($h -eq [IntPtr]::Zero) { Start-Sleep -Milliseconds 250 }
}
if ($h -eq [IntPtr]::Zero) { 'no-window'; exit 0 }
[CvcShot]::Count($h, $Out)
EOF
    sleep 1    # the frame with the pattern on it is presented
    local counts red green blue
    counts="$(powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass \
        -File "$(cygpath -w "${T}/shot.ps1")" -Dir "$(cygpath -w "${MOVED}/bin")" \
        -Out "$(cygpath -w "${T}/window.png")" 2>&1 | tr -d '\r')"
    # While the session is locked or its display is off, nothing is composited:
    # PrintWindow returns the frame around a blank white client area, which
    # WezTerm (dark by default) never draws. Nothing to judge the rendering by.
    case "${counts}" in
        blank) echo "  SKIP: pixel check (the capture is blank: locked session or display off)"; return 0 ;;
    esac
    read -r red green blue <<<"${counts}"
    case "${red}:${green}:${blue}" in
        *[!0-9:]*|*::*|:*|*:) echo "FAIL: could not capture the window: ${counts}"; return 1 ;;
    esac
    echo "  pixels: red ${red}, green ${green}, blue ${blue}"
    [ "${green}" -ge 1500 ] || { echo "FAIL: the green blocks did not draw"; return 1; }
    [ $((red * 4)) -le "${green}" ] \
        || { echo "FAIL: the red dots fill their cells -- glyphs draw as solid blocks"; return 1; }
    [ "${blue}" -ge 400 ] || { echo "FAIL: the blue kitty image did not draw"; return 1; }
    echo "  OK: glyphs and a kitty image render (pixel check)"
}

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
# Pixels only on Windows: a linux builder's display (Xvfb, WSLg, a desktop)
# offers no capture this package can rely on.
pixels_ok=1
if [ -n "${EXE}" ]; then pixel_check || pixels_ok=0; fi
: > "${QUIT_MARK}"
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
[ "${pixels_ok}" = 1 ] || exit 1

echo "-- wezterm smoke test passed --"
