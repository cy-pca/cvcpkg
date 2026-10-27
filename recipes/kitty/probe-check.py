#!/usr/bin/env python3
"""recipes/kitty/probe-check.py -- measure a screenshot of
kitty-placeholder-probe.py: did panel B's picture land ON its placeholder grid?

usage: probe-check.py XWD [--png OUT.png]

XWD is an X window dump of the screen, such as the Xvfb_screen0 file that
`Xvfb -fbdir DIR` keeps current (TrueColor, 32 bits per pixel). Panel A's
picture is a teal-to-blue blend and panel B's an orange-to-magenta one, each
drawn into 16 x 4 cells; the probe's text is grey-white on black, so neither
colour occurs anywhere else on the screen. This finds each picture's bounding
box and exits

  0  B is below A, centred on A's column and no larger than A: it is on its
     placeholder grid, which spans the same 16 columns as A's picture (kitty
     fits a placeholder image into its grid keeping the aspect ratio, so B
     may be narrower than A, with grid cells showing beside it);
  2  B's centre is half a picture-width or more right of A's: drawn at the
     cursor beside the grid, i.e. the terminal ignored U=1;
  3  A or B is not on the screen (not drawn yet, or no kitty graphics);
  1  anything else.

It prints one line of measurements either way, and with --png also writes the
screen as a PNG. Standard library only.
"""

import array
import struct
import sys
import zlib

COLS = 16  # cells per picture, as the probe draws them


def read_xwd(path):
    """(width, height, rows of 32-bit pixels, RGB masks, RGB shifts)."""
    with open(path, "rb") as f:
        d = f.read()
    hdr = struct.unpack(">25I", d[:100])
    header_size, width, height = hdr[0], hdr[4], hdr[5]
    byte_order, bpp, bpl, ncolors = hdr[7], hdr[11], hdr[12], hdr[19]
    masks = hdr[14:17]
    if bpp != 32:
        raise SystemExit(f"{path}: {bpp} bits per pixel, only 32 is handled")
    off = header_size + ncolors * 12
    rows = []
    for y in range(height):
        row = array.array("I")
        row.frombytes(d[off + y * bpl:off + y * bpl + width * 4])
        if (byte_order == 0) != (sys.byteorder == "little"):
            row.byteswap()
        rows.append(row)
    shifts = []
    for m in masks:
        s = 0
        while m and not (m >> s) & 1:
            s += 1
        shifts.append(s)
    return width, height, rows, masks, shifts


class Box:
    def __init__(self):
        self.n, self.x0, self.y0, self.x1, self.y1 = 0, 1 << 30, 1 << 30, -1, -1

    def add(self, x, y):
        self.n += 1
        self.x0, self.y0 = min(self.x0, x), min(self.y0, y)
        self.x1, self.y1 = max(self.x1, x), max(self.y1, y)

    @property
    def w(self):
        return self.x1 - self.x0 + 1

    @property
    def h(self):
        return self.y1 - self.y0 + 1

    def __str__(self):
        if not self.n:
            return "none"
        return f"{self.n} px in {self.w}x{self.h} at ({self.x0},{self.y0})"


def write_png(path, width, height, rgb_rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rgb_rows)

    def chunk(tag, body):
        crc = zlib.crc32(tag + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def main():
    args = sys.argv[1:]
    png = None
    if len(args) == 3 and args[1] == "--png":
        png = args[2]
        args = args[:1]
    if len(args) != 1:
        raise SystemExit(__doc__.split("\n\n")[1])
    width, height, rows, masks, (rs, gs, bs) = read_xwd(args[0])
    rm, gm, bm = masks
    teal, orange = Box(), Box()
    rgb_rows = []
    for y, row in enumerate(rows):
        out = bytearray() if png else None
        for x, v in enumerate(row):
            r, g, b = (v & rm) >> rs, (v & gm) >> gs, (v & bm) >> bs
            if out is not None:
                out += bytes((r, g, b))
            if r == g == b:  # black background, grey-white text
                continue
            # A: (20,160,140) -> (40,90,200). B: (230,120,20) -> (200,30,120).
            if r < 80 and g - r > 40 and b - r > 60:
                teal.add(x, y)
            elif r > 170 and r - g > 60 and r - b > 60:
                orange.add(x, y)
        if out is not None:
            rgb_rows.append(out)
    if png:
        write_png(png, width, height, rgb_rows)

    line = f"A (teal): {teal}; B (orange): {orange}"
    if teal.n < 200 or orange.n < 200:
        print(f"{line} -> a picture is missing")
        return 3
    # A is stretched over its 16 x 4 cells; B is fitted into its grid with
    # the aspect ratio kept and centred, so the two share a centre column
    # but not necessarily a size. (The 4-px frame is not teal: A's box is
    # 15/16 of its cells, near enough.)
    cell = teal.w / COLS
    off = ((orange.x0 + orange.x1) - (teal.x0 + teal.x1)) / 2 / cell
    if off >= COLS / 2:
        print(f"{line} -> B's centre is {off:.1f} cells right of A's:"
              " beside the grid, U=1 ignored")
        return 2
    centred = abs(off) <= 1
    fits = 0.3 * teal.w <= orange.w <= 1.1 * teal.w and orange.h <= 1.1 * teal.h
    filled = orange.n >= 0.6 * orange.w * orange.h
    below = orange.y0 > teal.y1
    if centred and fits and filled and below:
        print(f"{line} -> B is on its placeholder grid (centre {off:+.1f} cells from A's)")
        return 0
    print(f"{line} -> unexpected layout (centre {off:+.1f} cells from A's, fits {fits},"
          f" filled {filled}, below A {below})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
