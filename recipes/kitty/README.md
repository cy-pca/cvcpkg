# kitty

kitty 0.49.2, repackaged from upstream's signed release binaries for Linux
(x86_64, arm64) and macOS. kitty has no Windows build. `recipe.yaml` records
what is pinned and why. This file records what was checked while making it,
for libcvc's `arigl --terminal` (`src/cvc/ariadne/term_graphics.cpp`), whose
kitty mode places every frame with Unicode placeholders. WezTerm cannot draw
those (`recipes/wezterm/README.md`).

```sh
cvcpkg install kitty --prefix P
P/bin/kitty --version                  # kitty 0.49.2 created by Kovid Goyal
P/bin/kitty --config NONE <cmd>        # a window running <cmd>, without kitty.conf
```

## Does kitty draw kitty Unicode-placeholder images?

**Yes.** `kitty-placeholder-probe.py` is a copy of the WezTerm recipe's probe
(standard library only). It sends arigl's byte stream: `a=T,U=1,…,q=2,C=1`,
with the cursor moved to the right of a 4×16 grid of U+10EEEE placeholder
cells. Run it inside the terminal under test:

```sh
P/bin/kitty --config NONE P/bin/kitty +launch kitty-placeholder-probe.py --hold 30
```

(`kitty +launch` runs a script on kitty's bundled Python 3.14, so no host
Python is involved.)

| Panel | Sends | kitty 0.49.2 (observed) | WezTerm 20240203 (`recipes/wezterm`) |
|---|---|---|---|
| A | `a=T` without `U=1` (control) | picture | picture |
| B | arigl's `a=T,U=1,…,q=2,C=1`, cursor to the right of the placeholder grid | picture **on** the grid | picture **beside** the grid; grid cells are boxes |
| C | a DEC Sixel picture | nothing (kitty has no Sixel; it logs "Unknown DCS escape code") | picture |

Measured on 2026-10-26 with this package: linux-x86_64 under WSL Ubuntu 22.04,
cvcpkg `xvfb` + `mesa` (llvmpipe, "4.5 (Core Profile) Mesa 25.3.6"), DejaVu
Sans Mono 11 pt, cells about 9 × 18 px:

- **The probe as shipped:** A's teal picture was 135 × 64 px at x=4, and B's
  orange picture 134 × 62 px at x=5, right on its placeholder grid (centre
  +0.1 cells from A's). No grey placeholder cell showed.
- **Negative control:** the same probe with `U=1` deleted from B's transmit,
  i.e. what a terminal that ignores `U=1` does. B landed 21.4 cells to the
  right of A, the grey 4×16 grid stayed empty, and `test.sh` failed with
  "drew the U=1 picture beside its placeholder grid". The check can fail, so
  a pass means something.

A placeholder image is fitted into its grid with the aspect ratio kept, and
centred. A plain `a=T,c=16,r=4` placement (panel A) is stretched over its cells
instead. With cells near 1:2 the two coincide, as above. With other cell sizes,
B is narrower or shorter than A, and grey grid cells show beside it. That is
still correct placeholder rendering, and `probe-check.py` accepts it: it checks
B's centre, not its size. (Seen once while a test bug left kitty on the
proportional DejaVu Sans, with 15-px cells.)

For arigl this means its `a=T,U=1,…,C=1` frames land where it put the
placeholder cells, not at the cursor. kitty does not set `TERM_PROGRAM` (the
probe prints `TERM_PROGRAM=?`).

## How test.sh checks it

"The window opened" proves nothing: the WezTerm work found a build that opened
a window and drew solid blocks instead of glyphs. So on Linux `test.sh`:

1. starts cvcpkg's `Xvfb` (a build dependency) with `-fbdir`, so the screen
   is always available as the file `Xvfb_screen0` (XWD format);
2. runs kitty from a relocated copy of the prefix (no `LD_LIBRARY_PATH`), with
   software OpenGL from cvcpkg's `mesa` (llvmpipe, OpenGL 4.5 core), running
   the probe in the window;
3. runs `probe-check.py` on that screen once a second until both pictures
   appear. It finds the bounding box of panel A's teal pixels and of panel B's
   orange pixels. Neither colour occurs anywhere else on the screen. B must be
   below A, centred within a cell of A's centre, no larger than A, and filled.
   That is where the placeholder grid is, since the grid spans A's 16 columns.
   A terminal that ignores `U=1` puts B's centre about 20 cells further right,
   and the check fails with "beside the grid";
4. prints the measurements, the GL version and font kitty reports, and Mesa's
   loader messages.

The GL stack in the window test is pinned to cvcpkg's Mesa in the relocated
copy (`__GLX_VENDOR_LIBRARY_NAME=mesa`, `LIBGL_DRIVERS_PATH`). Before it runs,
`test.sh` checks that this Mesa loads. If it does not, the test is skipped and
the missing libraries are named (with `CVC_KITTY_GUI_TEST=1` that is a
failure).

That is the case on **linux-arm64** today. The published
`mesa 25.3.6+cvc.1` arm64 bundle (built on a GitHub runner on 2026-10-16) has
a `libgallium-25.3.6.so` that needs `libLLVM.so.18.1`, apparently the runner
image's own LLVM 18. The bundle declares `llvm20`, which ships
`libLLVM.so.20.1`. The x86_64 bundle links `libLLVM.so.20.1` as declared. On
the arm64 runner, `libGLX_mesa` failed to load, glvnd fell back to the system's
GLX, and that older Mesa could not use cvcpkg's driver ("did not find
extension DRI_Mesa version 1"). kitty then failed with `GLXBadFBConfig`. The
loader trace that showed it is what `test.sh` prints when kitty fails to
start. kitty's own arm64 X11 back end loads fine. The window test on arm64
waits for a mesa bundle that is built against llvm20.

`CVC_KITTY_SCREENSHOT=out.png` keeps the final screen as a PNG.
`CVC_KITTY_GUI_TEST=0` skips the window test, and `=1` makes every skip a
failure. On macOS `test.sh` runs everything except the window.

## Linux notes

- **Layout.** Upstream's bundle (`bin/`, `lib/`, `share/`) is kept whole in
  `lib/kitty/`. `bin/kitty` and `bin/kitten` are launchers that export
  `XKB_CONFIG_ROOT` / `XLOCALEDIR` into the prefix unless set, as the wezterm
  launchers do, then exec `lib/kitty/bin/<name>`. Man pages, the desktop
  entries and the icons are copied to `share/`.
- **What the bundle carries, and what it does not.** It brings Python 3.14,
  OpenSSL, cairo, FreeType, HarfBuzz, GLib, libxkbcommon(-x11) and Wayland,
  among others. It does not bring libX11, libX11-xcb, libXcursor, libxcb,
  libxcb-xkb or fontconfig, so those are runtime dependencies. Nor the X11
  extensions its GLFW dlopens (XRandR, Xinerama, XInput2, XRender, XShape,
  XF86VidMode), which are runtime dependencies too. libdbus-1 and the GL driver
  come from the host. Three of the bundled Python's standard modules
  (`_sqlite3`, `_curses_panel`, `_uuid`) link libraries the bundle does not
  carry. kitty does not use them.
- **DT_RPATH.** `bin/kitty` has a DT_RPATH to the bundle's `lib/`, and the
  bundled libraries have none, so they inherit it and outrank
  `LD_LIBRARY_PATH`. cvcpkg's relocation pass adds `$ORIGIN` to every
  `lib/**/*.so*`. As a DT_RUNPATH that would hand `LD_LIBRARY_PATH` (the
  prefix's `lib/`, under `activate`) precedence over the bundle. `build.sh`
  therefore gives each object its own DT_RPATH, as `recipes/libreoffice` does:
  the bundle's `lib/` first, then the prefix's `lib/`.
- **Why the prefix's `lib/` is written out.** cvcpkg's relocation pass adds
  that entry only for libraries listed in a runtime dependency's installed
  manifest. The linux-arm64 bundles of libx11, libxcb and libxcursor on
  cvcpkg.org (2026-10-26) carry no manifest, and `cvcpkg install` then writes
  one with no file list. On the arm64 CI runner the pass added nothing to
  `kitty.glfw-x11.so`: libX11 came from the runner's system, and
  `libxcb-xkb.so.1` was not found at all. The explicit entry also reaches what
  GLFW dlopens from the prefix (the X11 extensions, and libGL / libEGL when
  the prefix has a GL implementation such as cvcpkg's `mesa`).
- **D-Bus.** kitty asks the desktop portal for settings at start. On WSL's
  session bus, where no portal answers, every such call waits out D-Bus's
  25-second timeout, and the first window took over a minute to appear.
  `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` brings that down to a few
  seconds. `test.sh` does this.
- **Xvfb under WSL.** `Xvfb -displayfd` fails under WSL ("Failed to find a
  socket to listen on"), because `/tmp/.X11-unix` belongs to WSLg and is not
  writable. Given an explicit display number, Xvfb runs on the abstract socket,
  so `test.sh` picks the number itself.

## macOS notes

The `.dmg` holds one universal `kitty.app`, which is staged in `lib/kitty/`.
`bin/kitty` and `bin/kitten` exec `kitty.app/Contents/MacOS/<name>`.

Upstream's `Python.framework` and the Python extension modules in the app (77
objects) carry `LC_RPATH /Users/Shared/kitty-build/sw/sw/lib`, a directory
on kitty's build machine. cvcpkg's pack-time gate refuses that, and a
directory in `/Users/Shared` is writable by any local user. `build.sh` deletes
every absolute `LC_RPATH` and re-signs those objects ad hoc. cvcpkg's
relocation pass rewrites the install names of the dylibs in
`Contents/Frameworks` and re-signs them the same way. kitty runs and loads its
C extension afterwards, but the app as a whole no longer passes
`codesign --verify`. There is no window test on macOS: `test.sh` runs the
versions, the relocated copy and the font listing (Core Text).

## What was run where

Checked on 2026-10-26:

| Platform | Where | build | test.sh | pack (gates) |
|---|---|---|---|---|
| linux-x86_64 | WSL Ubuntu 22.04 on Windows 11: `cvcpkg install --fallback-to-source`, then `cvcpkg pack` | ✓ | ✓ including the window test and its negative control | ✓ glibc ≤ 2.35, relocatable (113 objects), 63 MB |
| linux-arm64 | `linux-arm-recipe-check` (GitHub ubuntu-22.04-arm, catalog deps) | ✓ | ✓; X11 back end resolves; window test **skipped**: cvcpkg's arm64 mesa needs `libLLVM.so.18.1` (above) | ✓ glibc ≤ 2.35, relocatable (113 objects), 62 MB |
| macos-arm64 | `macos-recipe-check` (macos-latest) | ✓ | ✓ (no window test on macOS) | ✓ relocatable (94 objects), 93 MB |
| macos-x86_64 | `macos-recipe-check` (macos-15-intel) | ✓ | ✓ (no window test on macOS) | ✓ relocatable (94 objects), 93 MB |

None of these runs publish anything. Publishing to cvcpkg.org is a separate,
production step.
