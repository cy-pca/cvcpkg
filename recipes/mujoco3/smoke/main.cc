// recipes/mujoco3/smoke/main.cc — find_package(mujoco) consumer run by test.sh.
//
// Checks, natively and under node for wasm:
//   * the header and library versions agree (mjVERSION_HEADER vs mj_version),
//   * an MJCF model with an OBJ mesh compiles from an in-memory VFS — this
//     needs the OBJ decoder plugin, which registers from a static initialiser
//     and is silently dropped by a static link that is not whole-archive,
//   * a free body actually falls onto the plane and comes to rest on it.
#include <cmath>
#include <cstdio>
#include <cstring>

#include <mujoco/mujoco.h>

static const char kObj[] =
    "v 0 0 0\nv 0.2 0 0\nv 0 0.2 0\nv 0 0 0.2\n"
    "f 1 3 2\nf 1 2 4\nf 1 4 3\nf 2 3 4\n";

static const char kXml[] = R"(
<mujoco model="smoke">
  <asset><mesh name="tet" file="tet.obj"/></asset>
  <worldbody>
    <geom name="floor" type="plane" size="2 2 0.1"/>
    <body name="ball" pos="0 0 1">
      <freejoint/>
      <geom type="sphere" size="0.1" mass="1"/>
    </body>
    <body name="tet" pos="0.5 0 0.5">
      <freejoint/>
      <geom type="mesh" mesh="tet" mass="0.5"/>
    </body>
  </worldbody>
</mujoco>
)";

int main() {
  std::printf("MuJoCo %s (header %d, library %d)\n", mj_versionString(),
              mjVERSION_HEADER, mj_version());
  if (mjVERSION_HEADER != mj_version()) {
    std::printf("FAIL: header/library version mismatch\n");
    return 1;
  }

  mjVFS vfs;
  mj_defaultVFS(&vfs);
  if (mj_addBufferVFS(&vfs, "tet.obj", kObj, (int)std::strlen(kObj)) != 0) {
    std::printf("FAIL: mj_addBufferVFS\n");
    return 1;
  }
  char err[1000] = "";
  mjSpec* spec = mj_parseXMLString(kXml, &vfs, err, sizeof(err));
  if (!spec) {
    std::printf("FAIL: parse: %s\n", err);
    return 1;
  }
  mjModel* m = mj_compile(spec, &vfs);
  if (!m) {
    std::printf("FAIL: compile (is the OBJ decoder linked?): %s\n", mjs_getError(spec));
    return 1;
  }
  mjData* d = mj_makeData(m);
  // 3 s of simulated time at the default 2 ms step.
  for (int i = 0; i < 1500; ++i) mj_step(m, d);
  const double z = d->qpos[2];  // ball's free joint: x y z qw qx qy qz
  std::printf("ball z after %.2f s: %.4f (radius 0.1), mesh vertices: %lld\n",
              d->time, z, (long long)m->nmeshvert);
  const bool ok = std::fabs(z - 0.1) < 0.01 && m->nmeshvert == 4;
  mj_deleteData(d);
  mj_deleteModel(m);
  mj_deleteSpec(spec);
  mj_deleteVFS(&vfs);
  if (!ok) {
    std::printf("FAIL: unexpected simulation result\n");
    return 1;
  }
  std::printf("OK\n");
  return 0;
}
