#!/usr/bin/env bash
# recipes/playwright/test.sh — run the Playwright Test runner on the cvcpkg node.
#
# Builds a scratch prefix shaped like a real install (this package's files +
# the nodejs22 runtime dependency's node), then runs a spec that needs no
# browser (the browser test lives in recipes/playwright-chromium-headless-shell/test.sh) and
# checks the runner resolves @playwright/test from the prefix via NODE_PATH.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
EXPECT="1.64.0"

echo "-- playwright smoke test (${CVC_PLATFORM:-native}) --"

NODE=""
for p in "${CVC_INSTALL_DIR}" "${CVC_DEPS_PREFIX:-}"; do
    [ -n "${p}" ] || continue
    for n in "${p}/bin/node" "${p}/bin/node.exe"; do
        if [ -x "${n}" ]; then NODE="${n}"; break 2; fi
    done
done
[ -n "${NODE}" ] || { echo "FAIL: nodejs22 (bin/node) not found in the install or deps prefix"; exit 1; }

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
P="${T}/prefix"
mkdir -p "${P}/bin" "${P}/lib"
cp -p "${CVC_INSTALL_DIR}/bin/playwright" "${P}/bin/"
cp -pR "${CVC_INSTALL_DIR}/lib/node_modules" "${P}/lib/"
ln -s "${NODE}" "${P}/bin/$(basename "${NODE}")"

# No HOME / XDG cache of the builder account gets involved.
export HOME="${T}/home" XDG_CACHE_HOME="${T}/cache" PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
unset PLAYWRIGHT_BROWSERS_PATH NODE_PATH || true
mkdir -p "${HOME}"

ver="$("${P}/bin/playwright" --version)"
echo "  ${ver} (node $("${NODE}" --version))"
[ "${ver}" = "Version ${EXPECT}" ] || { echo "FAIL: expected Version ${EXPECT}"; exit 1; }

mkdir -p "${T}/proj" && cd "${T}/proj"
cat > runner.spec.js <<'JS'
// No browser fixture is used, so this exercises only the runner.
const { test, expect } = require('@playwright/test');
test('runner works on the cvcpkg node', async () => {
  expect(process.version.startsWith('v22.')).toBe(true);
  expect(require.resolve('@playwright/test')).toContain('lib/node_modules');
  expect([1, 2, 3].map((x) => x * 2)).toEqual([2, 4, 6]);
});
JS
out="$("${P}/bin/playwright" test --reporter=line 2>&1)" || { echo "${out}"; echo "FAIL: playwright test"; exit 1; }
echo "${out}" | sed 's/^/    /' | tail -5
case "${out}" in *"1 passed"*) echo "  OK: spec ran and passed" ;; *) echo "FAIL: expected 1 passed"; exit 1 ;; esac

# The registry this version expects: where `playwright install --only-shell`
# would put the headless shell, which playwright-chromium-headless-shell matches.
"${P}/bin/playwright" install --dry-run --only-shell chromium 2>&1 | sed 's/^/    /'

echo "-- playwright smoke test passed --"
