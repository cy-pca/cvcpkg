/* recipes/mujoco/smoke/c/main.c: C-only find_package(mujoco) consumer run by
 * test.sh.  Compiles a model from an XML string and steps it, from C. */
#include <stdio.h>

#include <mujoco/mujoco.h>

static const char kXml[] =
    "<mujoco><worldbody><geom type='plane' size='1 1 0.1'/>"
    "<body pos='0 0 1'><freejoint/><geom size='0.1'/></body>"
    "</worldbody></mujoco>";

int main(void) {
  char err[1000] = "";
  mjSpec* spec;
  mjModel* m;
  mjData* d;
  int i;

  printf("MuJoCo %s from C (header %d, library %d)\n", mj_versionString(),
         mjVERSION_HEADER, mj_version());
  if (mjVERSION_HEADER != mj_version()) {
    printf("FAIL: header/library version mismatch\n");
    return 1;
  }
  spec = mj_parseXMLString(kXml, NULL, err, sizeof(err));
  if (!spec) {
    printf("FAIL: parse: %s\n", err);
    return 1;
  }
  m = mj_compile(spec, NULL);
  if (!m) {
    printf("FAIL: compile: %s\n", mjs_getError(spec));
    mj_deleteSpec(spec);
    return 1;
  }
  d = mj_makeData(m);
  for (i = 0; i < 1000; i++) {
    mj_step(m, d);
  }
  printf("body z after %d steps: %.3f\n", i, d->qpos[2]);
  if (d->qpos[2] > 0.2 || d->qpos[2] < 0.0) {
    printf("FAIL: the body did not come to rest on the plane\n");
    return 1;
  }
  mj_deleteData(d);
  mj_deleteModel(m);
  mj_deleteSpec(spec);
  return 0;
}
