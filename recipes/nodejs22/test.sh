#!/usr/bin/env bash
# recipes/nodejs22/test.sh — smoke test the staged Node.js 22 toolchain.
#
#  1. node / npm / npx run and report the pinned versions; core modules,
#     OpenSSL and full ICU work.
#  2. Relocatability: the staged tree is copied to a different path and
#     everything below runs from the COPY.
#  3. Offline `npm ci`: a throwaway local registry (a node http server on
#     127.0.0.1) serves one fixture package; `npm install` populates an
#     isolated cache and writes the lockfile; the registry is then shut down
#     and `npm ci --offline` must reproduce node_modules from the cache alone.
#
# npm is fully isolated from the builder's account: cache, user/global
# config and HOME all point into a temp dir.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
EXPECT_VER="v22.23.3"

echo "-- nodejs22 smoke test (${CVC_PLATFORM:-native}) --"

T="$(mktemp -d)"
REG_PID=""
cleanup() {
    if [ -n "${REG_PID}" ]; then kill "${REG_PID}" 2>/dev/null || true; fi
    rm -rf "${T}"
}
trap cleanup EXIT

# Windows keeps upstream's flat layout in bin\ (node.exe + node_modules\npm).
layout() {  # $1 = prefix; sets NODE and NPM_CLI / NPX_CLI
    if [ -f "$1/bin/node.exe" ]; then
        NODE="$1/bin/node.exe"
        NPM_CLI="$1/bin/node_modules/npm/bin/npm-cli.js"
        NPX_CLI="$1/bin/node_modules/npm/bin/npx-cli.js"
    else
        NODE="$1/bin/node"
        NPM_CLI="$1/lib/node_modules/npm/bin/npm-cli.js"
        NPX_CLI="$1/lib/node_modules/npm/bin/npx-cli.js"
    fi
}

# ── 1. versions + core functionality ────────────────────────────────────
layout "${CVC_INSTALL_DIR}"
ver="$("${NODE}" --version)"
echo "  node ${ver}"
[ "${ver}" = "${EXPECT_VER}" ] || { echo "FAIL: expected node ${EXPECT_VER}, got ${ver}"; exit 1; }
[ ! -e "${CVC_INSTALL_DIR}/bin/corepack" ] || { echo "FAIL: corepack should not ship"; exit 1; }

"${NODE}" -e '
const assert = require("assert");
const crypto = require("crypto"), zlib = require("zlib");
assert.strictEqual(crypto.createHash("sha256").update("cvcpkg").digest("hex").length, 64);
assert.strictEqual(zlib.gunzipSync(zlib.gzipSync("cvcpkg")).toString(), "cvcpkg");
require("https"); require("worker_threads");
// Official builds carry full ICU: a non-English locale must format.
assert.strictEqual(new Intl.DateTimeFormat("es", {month: "long", timeZone: "UTC"})
    .format(new Date(Date.UTC(2020, 0, 15))), "enero");
console.log("  OK: crypto (OpenSSL " + process.versions.openssl + "), zlib, https, " +
            "worker_threads, full ICU " + process.versions.icu);
'

# ── 2. relocate: copy the staged tree, test the copy from here on ───────
MOVED="${T}/relocated/prefix"
mkdir -p "${MOVED}"
cp -pR "${CVC_INSTALL_DIR}/." "${MOVED}/"
layout "${MOVED}"
export PATH="$(dirname "${NODE}"):${PATH}"

export HOME="${T}/home"
export npm_config_cache="${T}/npm-cache"
export npm_config_userconfig="${T}/npmrc"
export npm_config_globalconfig="${T}/global-npmrc"
export npm_config_update_notifier=false npm_config_fund=false npm_config_audit=false
mkdir -p "${HOME}"
: > "${npm_config_userconfig}"
: > "${npm_config_globalconfig}"

npm_() { "${NODE}" "${NPM_CLI}" "$@"; }

nver="$(npm_ --version)"; echo "  npm ${nver} (relocated copy)"
xver="$("${NODE}" "${NPX_CLI}" --version)"; echo "  npx ${xver} (relocated copy)"
[ "${nver}" = "${xver}" ] || { echo "FAIL: npm/npx version mismatch"; exit 1; }
# On POSIX the bin/npm launcher itself must work from the moved prefix.
if [ -f "${MOVED}/bin/npm" ] && [ ! -f "${MOVED}/bin/node.exe" ]; then
    [ "$("${MOVED}/bin/npm" --version)" = "${nver}" ] \
        || { echo "FAIL: relocated bin/npm launcher"; exit 1; }
    [ "$("${MOVED}/bin/npx" --version)" = "${nver}" ] \
        || { echo "FAIL: relocated bin/npx launcher"; exit 1; }
    # ...and through a symlink to it, the way a user might expose it.
    ln -s "${MOVED}/bin/npm" "${T}/npm-link"
    [ "$("${T}/npm-link" --version)" = "${nver}" ] \
        || { echo "FAIL: bin/npm launcher via symlink"; exit 1; }
    echo "  OK: bin/npm + bin/npx launchers run from the relocated prefix"
fi

# ── 3. npm ci --offline from a cache populated through a local registry ─
FIX="${T}/fixture"; PROJ="${T}/project"
mkdir -p "${FIX}" "${PROJ}"
cat > "${FIX}/package.json" <<'JSON'
{ "name": "cvc-fixture", "version": "1.0.0", "main": "index.js", "license": "MIT" }
JSON
echo 'module.exports = "cvc-fixture-ok";' > "${FIX}/index.js"
# Pack with a separate cache: `npm pack` stores the tarball in its cache by
# integrity, and the install below must fetch it from the registry instead.
(cd "${FIX}" && npm_config_cache="${T}/pack-cache" npm_ pack --silent --pack-destination "${T}" >/dev/null)
TGZ="${T}/cvc-fixture-1.0.0.tgz"
[ -f "${TGZ}" ] || { echo "FAIL: npm pack produced no tarball"; exit 1; }

cat > "${T}/registry.js" <<'JS'
// Minimal npm registry: one packument + one tarball, request log on stdout.
const http = require("http"), fs = require("fs"), crypto = require("crypto");
const [tgz, portFile] = process.argv.slice(2);
const data = fs.readFileSync(tgz);
const integrity = "sha512-" + crypto.createHash("sha512").update(data).digest("base64");
const shasum = crypto.createHash("sha1").update(data).digest("hex");
const server = http.createServer((req, res) => {
  const base = "http://127.0.0.1:" + server.address().port;
  const url = decodeURIComponent(req.url.split("?")[0]);
  console.log(req.method + " " + url);
  if (url === "/cvc-fixture") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ name: "cvc-fixture", "dist-tags": { latest: "1.0.0" },
      versions: { "1.0.0": { name: "cvc-fixture", version: "1.0.0", main: "index.js",
        dist: { tarball: base + "/cvc-fixture/-/cvc-fixture-1.0.0.tgz", integrity, shasum } } } }));
  } else if (url === "/cvc-fixture/-/cvc-fixture-1.0.0.tgz") {
    res.writeHead(200, { "content-type": "application/octet-stream" });
    res.end(data);
  } else {
    res.writeHead(404, { "content-type": "application/json" });
    res.end("{}");
  }
});
server.listen(0, "127.0.0.1", () => fs.writeFileSync(portFile, String(server.address().port)));
JS
"${NODE}" "${T}/registry.js" "${TGZ}" "${T}/port" > "${T}/registry.log" 2>&1 &
REG_PID=$!
for _ in $(seq 1 100); do [ -s "${T}/port" ] && break; sleep 0.1; done
[ -s "${T}/port" ] || { echo "FAIL: local registry did not start"; cat "${T}/registry.log"; exit 1; }
REG="http://127.0.0.1:$(cat "${T}/port")/"

cat > "${PROJ}/package.json" <<'JSON'
{ "name": "cvc-npm-ci-probe", "version": "1.0.0", "private": true,
  "dependencies": { "cvc-fixture": "1.0.0" } }
JSON
(cd "${PROJ}" && npm_ install --registry="${REG}" --no-progress >/dev/null)
[ -f "${PROJ}/package-lock.json" ] || { echo "FAIL: npm install wrote no lockfile"; exit 1; }
grep -q '"integrity": "sha512-' "${PROJ}/package-lock.json" \
    || { echo "FAIL: lockfile has no integrity"; exit 1; }
echo "  OK: npm install via the local registry wrote a lockfile; registry saw:"
sed 's/^/      /' "${T}/registry.log"
grep -q '^GET /cvc-fixture/-/cvc-fixture-1.0.0.tgz' "${T}/registry.log" \
    || { echo "FAIL: the tarball never came from the registry"; exit 1; }

# Registry gone: from here on the cache is the only source.
kill "${REG_PID}"; wait "${REG_PID}" 2>/dev/null || true; REG_PID=""
rm -rf "${PROJ}/node_modules"
(cd "${PROJ}" && npm_ ci --offline --registry=http://127.0.0.1:9/ --no-progress >/dev/null)
out="$(cd "${PROJ}" && "${NODE}" -e 'process.stdout.write(require("cvc-fixture"))')"
[ "${out}" = "cvc-fixture-ok" ] || { echo "FAIL: offline npm ci did not restore cvc-fixture"; exit 1; }
echo "  OK: npm ci --offline restored node_modules from the cache (registry stopped)"

echo "-- nodejs22 smoke test passed --"
