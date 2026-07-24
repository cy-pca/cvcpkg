# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""CyberPC Angel, LLC brand splash for the cvcpkg CLI.

Renders an ASCII-art rendition of the CyberPC Angel gears logo, optionally
colorized with the same palette the web front end uses (Bulma-dark: link
blue ``#3273dc``, grey ``#b5b5b5``), so the CLI and the SPA read as one
product.

The art is plain 7-bit ASCII so it renders in any terminal, on any
platform, and in a log file.  Color is strictly opt-out-safe:

* ``NO_COLOR`` (any value) disables color — https://no-color.org
* ``FORCE_COLOR`` enables it even when stdout is not a TTY
* a non-TTY stream (pipe, file, CI capture) gets no color and no splash
* ``TERM=dumb`` gets no color

Depth is detected from the environment: 24-bit truecolor when
``COLORTERM`` says so, else 256-color when ``TERM`` mentions it, else the
basic 16 ANSI colors.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import TextIO

# ── ASCII gears + wordmark ──────────────────────────────────────
# Laid out in three column bands so coloring needs no parallel mask:
#   cols [0, 23)  -> the big blue gear
#   cols [23, 34) -> the small grey gear
#   cols [34, ..) -> the "CVCPKG" wordmark
_ART = (
    "                         ##  ##",
    "         ###            #oooooo#",
    "   ###  #=====#  ###   #ooo  ooo#",
    "   #===ooooooooo===#   #ooo  ooo#   ____ __     __ ____  ____   _  __  ____",
    "    #ooo       ooo#     #oooooo#   / ___|\\ \\   / // ___||  _ \\ | |/ / / ___|",
    " ##=oo           oo=##   ##  ##   | |     \\ \\ / /| |    | |_) || ' / | |  _",
    " ##=oo           oo=##            | |___   \\ V / | |___ |  __/ | . \\ | |_| |",
    "    #ooo       ooo#                \\____|   \\_/   \\____||_|    |_|\\_\\ \\____|",
    "   #===ooooooooo===#",
    "   ###  #=====#  ###",
    "         ###",
)

_BLUE_BAND_END = 23
_GRAY_BAND_END = 34

#: Minimum terminal width for the full splash; narrower falls back to compact.
_MIN_WIDTH = 78

_TEXT_COL = 34

_TAGLINE = "cross-platform binary package archive"
_CREDIT = "A CyberPC Angel, LLC project"

# ── Palette (matches the web front end) ─────────────────────────

_RGB = {
    "blue": (0x32, 0x73, 0xDC),
    "gray": (0xB5, 0xB5, 0xB5),
    "white": (0xF5, 0xF5, 0xF5),
    "dim": (0x80, 0x80, 0x80),
}
_XTERM256 = {"blue": 33, "gray": 249, "white": 231, "dim": 244}
_ANSI16 = {"blue": "94", "gray": "37", "white": "97", "dim": "90"}

_RESET = "\033[0m"


def color_mode(stream: TextIO | None = None) -> str:
    """Return ``"truecolor"``, ``"256"``, ``"16"``, or ``"none"``."""
    stream = stream if stream is not None else sys.stdout

    if os.environ.get("NO_COLOR") is not None:
        return "none"

    forced = os.environ.get("FORCE_COLOR")
    if not forced:
        try:
            if not stream.isatty():
                return "none"
        except (AttributeError, ValueError):  # detached / closed stream
            return "none"

    term = os.environ.get("TERM", "")
    if term == "dumb":
        return "none"

    colorterm = os.environ.get("COLORTERM", "").lower()
    if colorterm in ("truecolor", "24bit"):
        return "truecolor"
    if "256" in term or "256" in colorterm:
        return "256"
    return "16"


def _paint(text: str, name: str, mode: str) -> str:
    """Wrap *text* in the ANSI escape for palette entry *name*."""
    if mode == "none" or not text.strip():
        return text
    if mode == "truecolor":
        r, g, b = _RGB[name]
        return f"\033[38;2;{r};{g};{b}m{text}{_RESET}"
    if mode == "256":
        return f"\033[38;5;{_XTERM256[name]}m{text}{_RESET}"
    return f"\033[{_ANSI16[name]}m{text}{_RESET}"


def _color_art_line(line: str, mode: str) -> str:
    """Colorize one art line by its column bands."""
    if mode == "none":
        return line
    blue = line[:_BLUE_BAND_END]
    gray = line[_BLUE_BAND_END:_GRAY_BAND_END]
    word = line[_GRAY_BAND_END:]
    return _paint(blue, "blue", mode) + _paint(gray, "gray", mode) + _paint(word, "white", mode)


def render_splash(
    version: str | None = None,
    *,
    mode: str = "none",
    width: int | None = None,
) -> str:
    """Render the splash.  Pure — takes the color *mode* and *width*.

    Falls back to a compact one-gear form when the terminal is narrower
    than the full art.
    """
    if version is None:
        from cvcpkg import __version__ as version

    if width is not None and width < _MIN_WIDTH:
        gear = _paint("(*)", "blue", mode)
        name = _paint(f"cvcpkg {version}", "white", mode)
        return "\n".join(
            [
                f"  {gear} {name}",
                f"      {_paint(_TAGLINE, 'gray', mode)}",
                f"      {_paint(_CREDIT, 'dim', mode)}",
            ]
        )

    lines = [_color_art_line(line, mode) for line in _ART]
    pad = " " * _TEXT_COL
    lines.append("")
    lines.append(pad + _paint(f"v{version}  -  {_TAGLINE}", "gray", mode))
    lines.append(pad + _paint(_CREDIT, "dim", mode))
    return "\n".join(lines)


def splash(stream: TextIO | None = None) -> str:
    """Return the splash for *stream*, or ``""`` when it should be hidden.

    Hidden for non-TTY output so piped/redirected output and CI logs stay
    clean; ``FORCE_COLOR`` overrides that.
    """
    stream = stream if stream is not None else sys.stdout
    mode = color_mode(stream)

    if mode == "none" and not os.environ.get("FORCE_COLOR"):
        try:
            if not stream.isatty():
                return ""
        except (AttributeError, ValueError):
            return ""

    try:
        width = shutil.get_terminal_size().columns
    except OSError:
        width = 80
    return render_splash(mode=mode, width=width)
