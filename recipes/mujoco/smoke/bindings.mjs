// recipes/mujoco/smoke/bindings.mjs — run the installed JS bindings under node.
//
//   node bindings.mjs <prefix>/share/mujoco/wasm/mujoco.js
//
// Loads the Embind module, compiles a one-body model from an XML string
// written into the module's in-memory filesystem, steps it and checks the
// body came to rest on the plane.  Exits non-zero on any failure.
import { pathToFileURL } from 'node:url';

const modPath = process.argv[2];
if (!modPath) {
  console.error('usage: node bindings.mjs <path/to/mujoco.js>');
  process.exit(2);
}

const { default: loadMujoco } = await import(pathToFileURL(modPath).href);
const mujoco = await loadMujoco();

const xml = `<mujoco>
  <worldbody>
    <geom type="plane" size="2 2 0.1"/>
    <body pos="0 0 1"><freejoint/><geom type="sphere" size="0.1"/></body>
  </worldbody>
</mujoco>`;
mujoco.FS.writeFile('/smoke.xml', xml);

const model = mujoco.MjModel.mj_loadXML('/smoke.xml');
const data = new mujoco.MjData(model);
for (let i = 0; i < 1500; i++) mujoco.mj_step(model, data);
const z = data.qpos[2];
console.log(`MuJoCo JS bindings: ball z after ${data.time.toFixed(2)} s = ${z.toFixed(4)}`);
data.delete();
model.delete();
if (Math.abs(z - 0.1) > 0.01) {
  console.error('FAIL: unexpected simulation result');
  process.exit(1);
}
console.log('OK');
