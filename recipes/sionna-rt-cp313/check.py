"""sionna-rt-cp313 post-build check, run by test.sh against the staged package.

CPU only: the Mitsuba variant is fixed to llvm_ad_mono_polarized before
sionna.rt is imported, so a GPU on the builder cannot change what is tested.
"""

import platform
import sys

import numpy as np
import drjit as dr
import mitsuba as mi


def cpu_has_fma():
    """False only on an x86_64 Linux CPU without FMA, where Dr.Jit's LLVM
    backend shuts itself down (see drjit-cp313's check.py)."""
    if platform.machine().lower() not in ("x86_64", "amd64"):
        return True
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("flags"):
                    return "fma" in line.split()
    except OSError:
        pass
    return True


if not dr.has_backend(dr.JitBackend.LLVM) and not cpu_has_fma():
    # Sionna RT's CPU path is that backend, so nothing here can run on this
    # host: check what was installed without importing sionna.rt.
    import importlib.metadata
    import importlib.util

    assert importlib.metadata.version("sionna-rt") == "2.2.0", importlib.metadata.version("sionna-rt")
    assert importlib.util.find_spec("sionna.rt") is not None, "sionna.rt is not installed"
    print("sionna-rt 2.2.0 installed; solvers not run: this x86_64 CPU has no FMA,",
          "which Dr.Jit's LLVM backend (Sionna RT's CPU path) needs")
    sys.exit(0)

mi.set_variant("llvm_ad_mono_polarized")
assert dr.has_backend(dr.JitBackend.LLVM), "LLVM backend inactive (libLLVM not found)"

import sionna.rt  # noqa: E402
from sionna.rt import (  # noqa: E402
    PathSolver,
    PlanarArray,
    RadioMapSolver,
    Receiver,
    Transmitter,
    load_scene,
)

print("sionna-rt", sionna.rt.__version__, "from", sionna.rt.__file__)
print("mitsuba", mi.__version__, "variant", mi.variant(), "| drjit", dr.__version__)
assert sionna.rt.__version__ == "2.2.0", sionna.rt.__version__
assert mi.variant() == "llvm_ad_mono_polarized", mi.variant()

scene = load_scene(sionna.rt.scene.simple_street_canyon)
scene.tx_array = PlanarArray(num_rows=1, num_cols=1, pattern="iso", polarization="V")
scene.rx_array = PlanarArray(num_rows=1, num_cols=1, pattern="iso", polarization="V")
# Both well above the street canyon's roofs: the line-of-sight path exists
# whatever the geometry, and the ground adds a reflection.
scene.add(Transmitter("tx", position=[-20.0, 0.0, 60.0]))
scene.add(Receiver("rx", position=[20.0, 10.0, 50.0]))

paths = PathSolver()(
    scene,
    max_depth=2,
    max_num_paths_per_src=10**4,
    samples_per_src=10**4,
    los=True,
    specular_reflection=True,
    diffuse_reflection=False,
    refraction=False,
)
a, tau = paths.cir(out_type="numpy")
if isinstance(a, tuple):  # (real, imag)
    a = a[0] + 1j * a[1]
a = np.asarray(a)
tau = np.asarray(tau)
print("paths: a", a.shape, "tau", tau.shape)
assert a.size > 0 and np.all(np.isfinite(a)), "non-finite path coefficients"
gain = float(np.sum(np.abs(a) ** 2))
assert gain > 0.0, "no path reached the receiver"
print("path gain %.3e" % gain)

rm = RadioMapSolver()(
    scene,
    cell_size=mi.Point2f(10.0, 10.0),
    samples_per_tx=10**4,
    max_depth=2,
    los=True,
    specular_reflection=True,
    diffuse_reflection=False,
    refraction=False,
)
pg = np.asarray(rm.path_gain.numpy())
print("radio map path_gain", pg.shape)
assert pg.size > 0 and np.all(np.isfinite(pg)), "non-finite radio map"
assert float(pg.max()) > 0.0, "empty radio map"

print("sionna-rt-cp313 check OK")
