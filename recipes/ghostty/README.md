# ghostty

Ghostty 1.3.1, for testing programs that draw images in the terminal with the
kitty graphics protocol's Unicode placeholders, which is how libcvc's
`arigl --terminal` (`src/cvc/ariadne/term_graphics.cpp`) draws its 3D scene.
WezTerm cannot show those (`recipes/wezterm/README.md`). Ghostty and kitty
can. `recipe.yaml` records what is pinned and why. This file covers how the
Linux build works and what the test proves.

```sh
cvcpkg install ghostty --prefix P
P/bin/ghostty --version                  # Ghostty 1.3.1
P/bin/ghostty -e <cmd>                   # new window running <cmd>
```

| Platform | Payload |
|---|---|
| macOS (x86_64, arm64) | the official `Ghostty.dmg`: one signed, notarised universal app, staged whole in `lib/ghostty/Ghostty.app` |
| linux-x86_64 | built here from `ghostty-1.3.1.tar.gz` with cvcpkg `zig` 0.15.2, GTK 4.16 and `libadwaita` 1.6 |
| Windows | none: Ghostty has no Windows port |

## Linux: a source build that never touches the network

Ghostty ships no Linux binaries, so `build.sh` builds the release tarball
the way `PACKAGING.md` asks distributions to:

1. **Fetch.** `build.zig.zon.json` in the tarball lists all 36 Zig packages
   the build can use: Zig libraries, the C/C++ libraries Ghostty compiles
   in, fonts and themes. Each entry carries its Zig content hash. `build.sh`
   runs `zig fetch <url>` for each one into a cache in the build dir, and
   fails unless zig reports exactly the pinned hash. A changed archive behind
   the same URL hashes differently.
2. **Build.** `zig build --system <cache>/p -Doptimize=ReleaseFast
   -Dcpu=baseline` then runs with no network access. GTK 4, libadwaita,
   freetype and fontconfig come from the cvcpkg prefix (`-fsys=`). cvcpkg does not package oniguruma, glslang, SPIRV-Cross,
   simdutf, highway or gtk4-layer-shell, so those are compiled from their
   pinned packages (`-fno-sys=`). gtk4-layer-shell has to be a shared
   library and is installed in `lib/`. HarfBuzz is compiled in as well,
   because Ghostty 1.3 needs HarfBuzz 11 and cvcpkg's `harfbuzz` is 10.2
   (pango keeps using the shared 10.2).
3. **Fix up.** `bin/ghostty` gets an `$ORIGIN/../lib` RUNPATH. The desktop
   file and the AppStream metainfo call `ghostty` from PATH instead of the
   builder's install path. The D-Bus activation file and the systemd user
   unit, which need an absolute path, are left out. Licences go to
   `share/doc/ghostty/`, including every fetched package's under
   `third-party/`.

Ghostty locates `share/ghostty` (themes, shell integration) and its
`xterm-ghostty` terminfo relative to its own executable, so the prefix can
be moved.

At run time, Ghostty needs OpenGL 4.3 through GTK, from the host's driver.
On a machine without a GPU driver, cvcpkg `mesa` (llvmpipe) is enough; the
test runs on it.

## Does it draw kitty Unicode-placeholder images?

**Yes, and `test.sh` checks the pixels on every linux build.** It starts
cvcpkg's `Xvfb` with `-fbdir`, which keeps a live XWD dump of the screen.
It opens a Ghostty window running `kitty-placeholder-probe.py` (the same
probe as `recipes/wezterm/`, copied here so the recipe is self-contained)
and reads the dump with `kitty-screenshot-check.py`.

| Panel | Sends | Pass condition |
|---|---|---|
| A | `a=T` without `U=1` | a teal picture at column 0; the reference for where column 0 is and how big 16×4 cells come out |
| B | arigl's `a=T,U=1,…,q=2,C=1`, sent with the cursor to the right of a 16×4 placeholder grid that starts at column 0 | an orange picture with A's left edge and A's size, below A: **on** the grid |
| C | DEC Sixel | not checked: Ghostty has no Sixel |

If B is missing, or is about 20 columns to the right (where the cursor was,
which is what a terminal that ignores `U=1` does), the test fails. So does a
window that opens but never draws panel A. That matters: a WezTerm build that
opened fine but drew solid blocks is what showed "it started" proves
nothing. To look at the result yourself, pass
`CVC_GHOSTTY_SCREENSHOT=/path/shot.png` to `test.sh`.

For arigl this means Ghostty's kitty mode shows each frame on the cells arigl
reserved for it.

## macOS

`build-macos.sh` mounts the pinned `Ghostty.dmg` (sha256 checked) and copies
`Ghostty.app` with `ditto`. It refuses the app unless `codesign --verify
--deep --strict` passes and the signing team is `24VZTF6M5V` (Developer ID
Application: Mitchell Hashimoto). `bin/ghostty` is a launcher into the app.
Man pages, shell completions and the terminfo are copied out beside it.
macOS can only be built on the cvcpkg CI macOS runners, and `test.sh` opens
no window there.
