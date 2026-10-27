#!/usr/bin/env python3
"""recipes/ghostty/kitty-screenshot-check.py -- did kitty-placeholder-probe.py's
pictures land where they should?

Reads an X screen dump in XWD format (Xvfb -fbdir DIR keeps a live one at
DIR/Xvfb_screen0), finds the probe's two kitty pictures by colour, and judges
panel B against panel A:

  A  a=T without U=1: a teal -> blue blend, drawn at column 0 under its
     caption. It is the reference for where column 0 is and how big a
     16 x 4-cell picture comes out in this window.
  B  arigl's a=T,U=1 transmission, sent with the cursor to the RIGHT of a
     16 x 4 placeholder grid that starts at column 0: an orange -> magenta
     blend. A terminal that implements Unicode placeholders draws it ON the
     grid (same left edge and size as A); one that ignores U=1 draws it at
     the cursor, about 20 columns to the right.

Each picture's 4-px dark frame is ignored; a pixel counts when it lies close
to the picture's colour ramp. Standard library only.

usage: kitty-screenshot-check.py XWD [--png OUT.png] [--wait-for-a]
  --wait-for-a  exit 3 (not 1) when panel A is not drawn yet, so a caller
                can poll until the terminal has rendered.
Prints one result line; exit 0 = B is on the placeholder grid.
"""

import struct
import sys
import zlib

A_RAMP = ((20, 160, 140), (40, 90, 200))
B_RAMP = ((230, 120, 20), (200, 30, 120))
TOLERANCE = 24  # RGB distance from the ramp; scaling blends neighbours
MIN_A_PIXELS = 1500  # a 16 x 4-cell picture is several thousand pixels


def read_xwd(path):
    with open(path, "rb") as f:
        data = f.read()
    hdr = struct.unpack(">25I", data[:100])
    header_size, version = hdr[0], hdr[1]
    if version != 7:
        raise SystemExit(f"{path}: not an XWD v7 file (version {version})")
    width, height = hdr[4], hdr[5]
    byte_order, bpp, bpl = hdr[7], hdr[11], hdr[12]
    masks = hdr[14], hdr[15], hdr[16]
    ncolors = hdr[19]
    if bpp not in (24, 32):
        raise SystemExit(f"{path}: {bpp} bits per pixel is not supported")
    off = header_size + ncolors * 12
    shifts = [(m & -m).bit_length() - 1 for m in masks]
    order = "little" if byte_order == 0 else "big"
    step = bpp // 8
    rows = []
    for y in range(height):
        line = data[off + y * bpl: off + y * bpl + width * step]
        row = []
        for x in range(width):
            v = int.from_bytes(line[x * step:(x + 1) * step], order)
            row.append(tuple((v & m) >> s for m, s in zip(masks, shifts)))
        rows.append(row)
    return width, height, rows


def ramp_distance(p, ramp):
    (r0, g0, b0), (r1, g1, b1) = ramp
    dr, dg, db = r1 - r0, g1 - g0, b1 - b0
    t = ((p[0] - r0) * dr + (p[1] - g0) * dg + (p[2] - b0) * db) / float(dr * dr + dg * dg + db * db)
    t = min(1.0, max(0.0, t))
    er, eg, eb = p[0] - (r0 + t * dr), p[1] - (g0 + t * dg), p[2] - (b0 + t * db)
    return (er * er + eg * eg + eb * eb) ** 0.5


class Blob:
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
        return f"{self.n} px at x={self.x0}..{self.x1} y={self.y0}..{self.y1} ({self.w}x{self.h})"


def find(rows):
    a, b = Blob(), Blob()
    for y, row in enumerate(rows):
        for x, p in enumerate(row):
            r, g, bl = p
            # Cheap rejects first: most of the screen is background or text.
            if r < 80 and g > 70 and bl > 120 and ramp_distance(p, A_RAMP) <= TOLERANCE:
                a.add(x, y)
            elif r > 170 and bl < 150 and g < 150 and ramp_distance(p, B_RAMP) <= TOLERANCE:
                b.add(x, y)
    return a, b


def write_png(path, width, height, rows):
    raw = b"".join(b"\0" + bytes(c for p in row for c in p) for row in rows)

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(chunk(b"IEND", b""))


def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    xwd, png, wait_for_a = args[0], None, False
    i = 1
    while i < len(args):
        if args[i] == "--png":
            png, i = args[i + 1], i + 2
        elif args[i] == "--wait-for-a":
            wait_for_a, i = True, i + 1
        else:
            raise SystemExit(f"unknown argument {args[i]}")
    width, height, rows = read_xwd(xwd)
    if png:
        write_png(png, width, height, rows)
    a, b = find(rows)
    where = f"A: {a}; B: {b}"
    if a.n < MIN_A_PIXELS:
        print(f"panel A (plain kitty placement) not drawn: {where}")
        sys.exit(3 if wait_for_a else 1)
    if b.n == 0:
        print(f"FAIL: panel B (U=1 placeholders) drew nothing: {where}")
        sys.exit(1)
    slack = 4
    on_grid = (
        abs(b.x0 - a.x0) <= slack
        and abs(b.w - a.w) <= slack
        and abs(b.h - a.h) <= slack
        and b.y0 > a.y1
        and 0.7 * a.n <= b.n <= 1.3 * a.n
    )
    if on_grid:
        print(f"OK: the U=1 picture covers the placeholder grid at column 0: {where}")
        sys.exit(0)
    if b.x0 - a.x0 > a.w // 2:
        print(f"FAIL: the U=1 picture is beside the grid (U=1 ignored): {where}")
    else:
        print(f"FAIL: the U=1 picture is not where the placeholder grid is: {where}")
    sys.exit(1)


if __name__ == "__main__":
    main()
