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
P/bin/kitty --config NONE -- <cmd>     # a window running <cmd>, without kitty.conf
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

RESULTS

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
   orange pixels. Neither colour occurs anywhere else on the screen. B must
   start in A's column, below A, at A's size, and be filled. That is where the
   placeholder grid is. A terminal that ignores `U=1` puts B at least a
   picture-width further right, and the check fails with "beside the grid";
4. prints the measurements and the GL version kitty reports.

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
  therefore gives each object its own DT_RPATH, as `recipes/libreoffice` does.
- **D-Bus.** kitty asks the desktop portal for settings at start. On WSL's
  session bus, where no portal answers, every such call waits out D-Bus's
  25-second timeout, and the first window took over a minute to appear.
  `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` brings that down to a few
  seconds. `test.sh` does this.
