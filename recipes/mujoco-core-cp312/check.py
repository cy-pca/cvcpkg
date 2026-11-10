# recipes/mujoco-core-cpXYZ/check.py — import-and-simulate check run by
# build.sh / build.ps1 (through cvc_python_check) against the staged package.
import mujoco
import numpy as np

print("mujoco", mujoco.__version__, "C library", mujoco.mj_versionString())
assert mujoco.__version__ == mujoco.mj_versionString(), "bindings/library version mismatch"

XML = """
<mujoco>
  <worldbody>
    <geom type="plane" size="2 2 0.1"/>
    <body pos="0 0 1"><freejoint/><geom type="sphere" size="0.1"/></body>
  </worldbody>
</mujoco>
"""
m = mujoco.MjModel.from_xml_string(XML)
d = mujoco.MjData(m)
mujoco.mj_step(m, d, nstep=1500)
z = float(d.qpos[2])
print(f"ball z after {d.time:.2f} s: {z:.4f}")
assert abs(z - 0.1) < 0.01, "unexpected simulation result"

# mjSpec model editing and the bundled engine plugins (bin/mujoco_plugin in
# the C package, mujoco/plugin in the wheel) load too.
spec = mujoco.MjSpec.from_string(XML)
spec.worldbody.add_body(name="extra", pos=[0, 0, 2]).add_geom(size=[0.05, 0, 0])
m2 = spec.compile()
assert m2.nbody == m.nbody + 1, "mjSpec edit did not compile"
assert np.isfinite(d.qpos).all()

# mujoco.viewer itself imports the `glfw` PyPI package (pyGLFW, upstream's
# optional `rendering` extra), which cvcpkg does not package yet; the
# extension module behind it must still load.
from mujoco import _simulate  # noqa: E402,F401
print("mujoco-core check OK")
