# wezterm

WezTerm 20240203-110809-5046fc22 (the last stable release), built from source
with the cvcpkg Rust toolchain. Every input is a cvcpkg package: the source
tree and all of its crates from `wezterm-src`, the libraries from the runtime
dependencies, and on Windows Microsoft's ConPTY from `conpty`; the build itself
downloads nothing. `recipe.yaml` records what is pinned and why; this file
records what was found about images in the terminal while making it, for
libcvc's `arigl --terminal` (`src/cvc/ariadne/term_graphics.cpp`).

```sh
cvcpkg install wezterm --prefix P
P/bin/wezterm --version                 # wezterm 20240203-110809-5046fc22
P/bin/wezterm start --always-new-process -- <cmd>
```

## Does WezTerm draw kitty Unicode-placeholder images?

**No.** WezTerm draws kitty images placed directly (`a=T`, `a=p`) and DEC
Sixel, but it does not implement Unicode placeholders. Its kitty parser
(`termwiz/src/escape/apc.rs` at the stable tag,
`wezterm-escape-parser/src/apc.rs` on `main` as of 2026-10-25) reads no `U`
key in either.
An `a=T,U=1` transmission is therefore handled as a plain `a=T`: the picture
is drawn at the cursor as an ordinary placement, and the U+10EEEE cells with
their row/column diacritics print as text (missing-glyph boxes).

`kitty-placeholder-probe.py` (standard library only) reproduces arigl's byte
stream and shows the difference in one screen. Run it inside the terminal under
test:

```sh
python3 kitty-placeholder-probe.py            # waits for Enter
python3 kitty-placeholder-probe.py --hold 30  # or holds for 30 s
```

| Panel | Sends | kitty / Ghostty (per the kitty spec; not run here) | WezTerm 20240203 (observed) |
|---|---|---|---|
| A | `a=T` without `U=1` (control) | picture | picture |
| B | arigl's `a=T,U=1,…,q=2,C=1`, sent with the cursor to the right of a 4×16 placeholder grid | picture **on** the grid | picture **beside** the grid; grid cells are boxes |
| C | a DEC Sixel picture | — (kitty has no Sixel) | picture |

Checked on 2026-10-25, with upstream's release binaries (cvc.1) and with this
recipe's from-source builds (cvc.2), the same way each time:

- linux-x86_64, the Linux binaries under cvcpkg's `xvfb` (WezTerm's own pty, so
  no ConPTY in the way): A and C draw, B lands beside the grid.
- windows-x86_64, native Windows program (`python.exe`) in the pane: same
  result, **once the ConPTY is replaced** (below).

The from-source builds first showed no images at all, and glyphs as solid
blocks, with the OpenGL and Software front ends: glium 0.31 under Rust 1.90.
`wezterm-src`'s `patches/0002` (upstream's glium 0.34 commit) fixes that, and `test.sh` now
checks the pixels of its window on Windows so it cannot come back unnoticed.

So on WezTerm, arigl's kitty mode would show each frame at wherever the
cursor happens to be when the transmission is written, and leave placeholder
boxes where the picture should be. WezTerm answers the kitty query (`a=q`)
with `OK`, so a probe that only asks "is kitty graphics supported?" chooses
kitty. To get images in WezTerm, arigl should use Sixel there, or kitty with
direct placement (`a=T`/`a=p` with the cursor at the image's cell, no `U=1`).
WezTerm identifies itself with `TERM_PROGRAM=WezTerm` and in its XTVERSION
reply (`CSI > q` → `DCS >|WezTerm 20240203-110809-5046fc22 ST`, checked through
the replacement ConPTY below).

## Windows: the bundled ConPTY ate the escapes

WezTerm hosts Windows programs through ConPTY (`conpty.dll` + `OpenConsole.exe`
beside `wezterm-gui.exe`), which parses the program's output and re-emits it.
The ConPTY WezTerm 20240203 bundles (`assets/windows/conhost` in its source,
and in its release zip; from February 2024) drops APC and DCS sequences. With
it **no** image reached WezTerm from a native Windows program: probe panels A,
B and C were all empty, and a window title set with OSC 2 was lost as well. A
kitty query from the pane got no reply.

This package therefore does not ship that ConPTY: its `conpty` dependency
installs Microsoft's ConPTY 1.25.260930003 (`Microsoft.Windows.Console.ConPTY`,
from the Windows Terminal 1.25.2733.0 release) into the same `bin\`. With it
the same probe
shows A and C drawn and B beside the grid, exactly as on Linux. `test.sh`
checks it on every Windows build that has a desktop: the program in the pane
sends a kitty query and must get `ESC _Gi=31;OK ESC \` back through ConPTY.
WezTerm's own nightlies fixed this the same way. They bundle ConPTY
1.22.250204002, which behaved identically here: dropping that `conpty.dll` +
`OpenConsole.exe` pair into the stable tree also made A and C draw.

Programs on the far side of `wezterm ssh` or a WezTerm mux connection never go
through ConPTY.

## Linux notes

`bin/wezterm*` are launchers. They export `XKB_CONFIG_ROOT` and `XLOCALEDIR`
into the prefix (from `xkeyboard-config` and `libx11`), then exec
`lib/wezterm/<name>`. cvcpkg's `libxkbcommon` has its builder's paths compiled
in as defaults, and without these two variables `wezterm-gui` cannot start. On
a machine without a GPU, use `--config 'front_end="Software"'`.
