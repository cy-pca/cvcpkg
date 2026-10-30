"""mitsuba-cp313 post-build check, run by test.sh against the staged package.

argv[1]: comma-separated variants the build must have compiled
argv[2]: path of test-scene.xml
argv[3]: "1" to also render with llvm_ad_mono_polarized (libLLVM in the closure)
"""

import math
import os
import sys
import tempfile

want_variants = [v for v in sys.argv[1].split(",") if v]
scene_xml = sys.argv[2]
check_llvm = sys.argv[3] == "1"

import drjit as dr  # noqa: E402
import mitsuba as mi  # noqa: E402

print("mitsuba", mi.__version__, "from", mi.__file__)
print("drjit", dr.__version__, "from", dr.__file__)
assert mi.__version__ == "3.9.1", mi.__version__
assert dr.__version__ == "1.5.0", dr.__version__

have = list(mi.variants())
print("variants:", have)
missing = [v for v in want_variants if v not in have]
assert not missing, "variants missing: %s (have %s)" % (missing, have)

if sys.platform.startswith("linux"):
    # Mitsuba's libraries must bind the drjit Python imported, not a copy.
    with open("/proc/self/maps") as fh:
        cores = {line.split()[-1] for line in fh if "libdrjit-core" in line}
    assert len(cores) == 1, cores
    print("libdrjit-core:", cores.pop())


def scene_dict():
    # Built per variant: the transform type belongs to the active variant.
    return {
        "type": "scene",
        "integrator": {"type": "path", "max_depth": 4},
        "sensor": {
            "type": "perspective",
            "fov": 45,
            "to_world": mi.ScalarTransform4f().look_at(
                origin=[0, 0, 4], target=[0, 0, 0], up=[0, 1, 0]
            ),
            "sampler": {"type": "independent", "sample_count": 4},
            "film": {"type": "hdrfilm", "width": 16, "height": 16},
        },
        "emitter": {"type": "constant"},
        "sphere": {"type": "sphere", "bsdf": {"type": "diffuse"}},
    }


def render_checked(scene, label):
    img = mi.render(scene, spp=4)
    shape = tuple(img.shape)
    assert shape[:2] == (16, 16), (label, shape)
    vals = [float(x) for x in img.array]
    assert vals and all(math.isfinite(x) for x in vals), (label, "non-finite pixels")
    mean = sum(vals) / len(vals)
    assert mean > 0.0, (label, "black image")
    print("%s: %s mean %.4f" % (label, shape, mean))
    return img


# ── scalar_rgb: what the mitsuba-blender add-on uses ────────────────────────
mi.set_variant("scalar_rgb")
img = render_checked(mi.load_dict(scene_dict()), "scalar_rgb load_dict")
render_checked(mi.load_file(scene_xml), "scalar_rgb load_file")

# Image I/O through the bundled OpenEXR, libpng and libjpeg-turbo (and zlib).
with tempfile.TemporaryDirectory() as tmp:
    for ext in ("exr", "png", "jpg"):
        path = os.path.join(tmp, "out." + ext)
        mi.util.write_bitmap(path, img)
        bmp = mi.Bitmap(path)
        assert (bmp.width(), bmp.height()) == (16, 16), (ext, bmp)
        print("bitmap round trip:", ext, bmp.pixel_format(), bmp.component_format())

# ── llvm_ad_mono_polarized: Sionna RT's CPU variant ─────────────────────────
if check_llvm:
    assert dr.has_backend(dr.JitBackend.LLVM), (
        "LLVM backend inactive; DRJIT_LIBLLVM_PATH=%r" % os.environ.get("DRJIT_LIBLLVM_PATH")
    )
    print("libLLVM:", os.environ.get("DRJIT_LIBLLVM_PATH"))
    for variant in ("llvm_ad_mono_polarized", "llvm_ad_rgb"):
        mi.set_variant(variant)
        render_checked(mi.load_dict(scene_dict()), variant + " load_dict")
        render_checked(mi.load_file(scene_xml), variant + " load_file")
else:
    print("llvm variants not rendered on", sys.platform, "(no libLLVM in the closure)")

print("mitsuba-cp313 check OK")
