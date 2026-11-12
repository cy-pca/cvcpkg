"""recipes/pillow-cp312/wasm_probe.py: what build-wasm.sh runs in wasm_probe.c's host under node.

Every extension must be a builtin (the manifest's inittab, not a file), every pinned feature
present, and each codec must round-trip through BytesIO: PNG and JPEG through _imaging's own
zlib and libjpeg, TIFF through libtiff with each of its codecs (so libtiff's private zlib,
libjpeg, liblzma, libzstd and libwebp copies all run), WebP still and animated (libwebp's
mux and demux), and text through FreeType. The last line is what the script greps for.
"""

import io
import sys

import PIL
from PIL import Image, ImageDraw, ImageFont, features

EXTENSIONS = ("_imaging", "_imagingft", "_imagingmath", "_imagingmorph", "_webp")
for leaf in EXTENSIONS:
    mod = __import__("PIL." + leaf, fromlist=["_"])
    origin = mod.__spec__.origin
    assert origin == "built-in", f"PIL.{leaf} came from {origin}, not the inittab"

WANTED = ("zlib", "jpg", "libtiff", "freetype2", "webp")
missing = [f for f in WANTED if not features.check(f)]
assert not missing, f"features missing from the build: {missing}"


def roundtrip(im, fmt, **params):
    buf = io.BytesIO()
    im.save(buf, fmt, **params)
    out = Image.open(io.BytesIO(buf.getvalue()))
    out.load()
    return out


# A gradient, so a codec that drops or reorders data can't pass by accident.
src = Image.new("RGB", (32, 24))
src.putdata([(x * 8, y * 10, (x + y) * 4) for y in range(24) for x in range(32)])
raw = src.tobytes()

png = roundtrip(src, "PNG", compress_level=9)
assert png.format == "PNG" and png.convert("RGB").tobytes() == raw, "PNG round-trip corrupted"

jpg = roundtrip(src, "JPEG", quality=95)
assert jpg.format == "JPEG" and jpg.size == src.size, "JPEG round-trip lost the image"
err = max(abs(a - b) for a, b in zip(jpg.convert("RGB").getpixel((5, 5)), src.getpixel((5, 5))))
assert err < 24, f"JPEG round-trip is off by {err}"

for compression in ("tiff_adobe_deflate", "tiff_lzw", "lzma", "zstd"):
    tif = roundtrip(src, "TIFF", compression=compression)
    assert tif.format == "TIFF", f"TIFF {compression}: read back as {tif.format}"
    assert tif.convert("RGB").tobytes() == raw, f"TIFF {compression} round-trip corrupted"
for compression in ("jpeg", "webp"):
    tif = roundtrip(src, "TIFF", compression=compression)
    assert tif.size == src.size, f"TIFF {compression} round-trip lost the image"

webp = roundtrip(src, "WEBP", lossless=True)
assert webp.format == "WEBP" and webp.convert("RGB").tobytes() == raw, "WebP lossless corrupted"
assert roundtrip(src, "WEBP", quality=80).size == src.size, "WebP lossy lost the image"
frames = [Image.new("RGB", (16, 16), c) for c in ((255, 0, 0), (0, 0, 255))]
buf = io.BytesIO()
frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], lossless=True, duration=40)
anim = Image.open(io.BytesIO(buf.getvalue()))
assert anim.n_frames == 2, f"animated WebP has {anim.n_frames} frames"
anim.seek(1)
assert anim.convert("RGB").getpixel((8, 8)) == (0, 0, 255), "animated WebP frame 2 is wrong"

font = ImageFont.load_default(size=24)
assert isinstance(font, ImageFont.FreeTypeFont), f"load_default gave {type(font).__name__}"
canvas = Image.new("L", (96, 32), 0)
ImageDraw.Draw(canvas).text((4, 2), "PIL", fill=255, font=font)
assert canvas.getbbox() is not None, "FreeType drew nothing"

versions = ", ".join(f"{f} {features.version(f)}" for f in WANTED)
print(f"pillow(wasm) probe: OK PIL {PIL.__version__} on {sys.platform}; {versions}")
