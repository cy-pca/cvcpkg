#!/usr/bin/env python3
"""recipes/wezterm/kitty-placeholder-probe.py -- does this terminal draw kitty
Unicode-placeholder images?

libcvc's `arigl --terminal` (src/cvc/ariadne/term_graphics.cpp) shows kitty
images through Unicode placeholders: it transmits each picture with
`a=T,U=1,...,q=2,C=1` (a virtual placement) and prints U+10EEEE cells carrying
row/column diacritics, foreground colour = image id. This prints three panels
in the terminal it runs in, standard library only:

  A  a plain kitty placement (a=T, no U=1) -- does kitty graphics work at all?
  B  arigl's byte stream: a placeholder grid on the left, then, with the
     cursor moved to the RIGHT of the grid, the a=T,U=1 transmit. A terminal
     that supports placeholders draws the picture ON the grid; one that
     ignores U=1 draws it at the cursor, beside the grid, and the grid shows
     as text.
  C  a DEC Sixel picture -- the other path arigl has.

Look at the window (or screenshot it); nothing is read back.

usage: kitty-placeholder-probe.py [--hold SECONDS]   (default: wait for Enter)
"""

import base64
import os
import sys
import time

ESC = "\x1b"
COLS, ROWS = 16, 4  # cells per picture
W, H = 128, 64  # pixels per picture

# kitty's rowcolumn-diacritics.txt, first entries (row/column 0, 1, 2, ...).
DIACRITICS = [0x0305, 0x030D, 0x030E, 0x0310, 0x0312, 0x033D, 0x033E, 0x033F,
              0x0346, 0x034A, 0x034B, 0x034C, 0x0350, 0x0351, 0x0352, 0x0357]


def picture(c0, c1):
    """W x H RGB: a horizontal blend c0 -> c1 with a dark 4-px frame."""
    out = bytearray()
    for y in range(H):
        for x in range(W):
            if x < 4 or y < 4 or x >= W - 4 or y >= H - 4:
                out += b"\x10\x10\x10"
                continue
            t = x / (W - 1)
            out += bytes(round(a + (b - a) * t) for a, b in zip(c0, c1))
    return bytes(out)


def kitty(keys, rgb, chunk=4096):
    """One kitty transmission, chunked the way arigl chunks it (m=1 ... m=0)."""
    payload = base64.b64encode(rgb).decode("ascii")
    out, pos = [], 0
    while True:
        part = payload[pos:pos + chunk]
        pos += len(part)
        more = pos < len(payload)
        head = keys + "," if not out else "q=2,"
        out.append(f"{ESC}_G{head}m={1 if more else 0};{part}{ESC}\\")
        if not more:
            return "".join(out)


def placeholder(row, col):
    return chr(0x10EEEE) + chr(DIACRITICS[row]) + chr(DIACRITICS[col])


def sixel(bands):
    """A 96 x 36 px sixel: six 6-px stripes cycling through *bands* (RGB %)."""
    regs = "".join(f"#{i};2;{r};{g};{b}" for i, (r, g, b) in enumerate(bands))
    rows = [f"#{i % len(bands)}!96~" for i in range(6)]
    return f'{ESC}Pq"1;1;96;36{regs}' + "-".join(rows) + f"{ESC}\\"


def main():
    hold = None
    if len(sys.argv) == 3 and sys.argv[1] == "--hold":
        hold = float(sys.argv[2])
    out = sys.stdout
    if hasattr(out, "reconfigure"):
        out.reconfigure(encoding="utf-8", newline="\n")
    w = out.write

    w(f"{ESC}]2;kitty-placeholder-probe\a{ESC}[2J{ESC}[H")
    w("kitty Unicode-placeholder probe (cvcpkg recipes/wezterm)\n\n")

    w("A  kitty, plain placement (a=T, no U=1): a teal picture below\n")
    w(kitty(f"a=T,f=24,s={W},v={H},c={COLS},r={ROWS},i=4001,q=2,C=1",
            picture((20, 160, 140), (40, 90, 200))))
    w("\n" * (ROWS + 1))

    w("B  arigl's placeholders (a=T,U=1,...,C=1): an orange picture should\n")
    w("   cover the grid on the LEFT; beside it on the right = U=1 ignored\n")
    img = 4002
    fg = f"{ESC}[38;2;{(img >> 16) & 255};{(img >> 8) & 255};{img & 255}m"
    w(f"{ESC}7")  # remember where the grid starts
    for r in range(ROWS):
        w(fg + f"{ESC}[48;2;60;60;60m")
        w("".join(placeholder(r, c) for c in range(COLS)))
        w(f"{ESC}[0m\n")
    w(f"{ESC}8{ESC}[{COLS + 4}C")  # back to the grid's first row, right of it
    w(kitty(f"a=T,U=1,f=24,s={W},v={H},c={COLS},r={ROWS},i={img},q=2,C=1",
            picture((230, 120, 20), (200, 30, 120))))
    w(f"{ESC}8{ESC}[{ROWS + 1}B\r")

    w("C  DEC Sixel: red / green / blue stripes below\n")
    w(sixel([(100, 0, 0), (0, 100, 0), (0, 0, 100)]))
    w("\n\n")
    w(f"TERM_PROGRAM={os.environ.get('TERM_PROGRAM', '?')} "
      f"{os.environ.get('TERM_PROGRAM_VERSION', '')}\n")
    out.flush()

    if hold is None:
        try:
            input("press Enter to quit ")
        except EOFError:
            pass
    else:
        time.sleep(hold)
    # Free the pictures (a=d, d=I: by id, with their placements).
    w(f"{ESC}_Ga=d,d=I,i=4001,q=2{ESC}\\{ESC}_Ga=d,d=I,i={img},q=2{ESC}\\")
    out.flush()


if __name__ == "__main__":
    main()
