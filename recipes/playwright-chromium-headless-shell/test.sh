#!/usr/bin/env bash
# recipes/playwright-chromium-headless-shell/test.sh — open a data: URL in
# headless Chromium through Playwright Test, using only cvcpkg's node,
# playwright and browser.
#
#  1. The staged directory is exactly where Playwright 1.64.0 looks
#     (`playwright install --dry-run --only-shell chromium` reports it).
#  2. Host libraries: Chromium needs the system's NSS, ATK, GBM, X11 client
#     libraries etc. (not packaged by cvcpkg). They are checked with ldd; when
#     some are missing the browser launch is SKIPPED with the list printed,
#     unless CVC_PLAYWRIGHT_REQUIRE_BROWSER=1 makes that a failure.
#  3. A one-file spec loads a data: URL in the default headless browser and
#     checks the DOM (title, script-modified text) and the browser version.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
REV="1248"
CHROME_VER="156.0.8078.4"
B="${CVC_INSTALL_DIR}/share/ms-playwright"

echo "-- playwright-chromium-headless-shell smoke test (${CVC_PLATFORM:-native}) --"

# node + playwright are build dependencies of this recipe.
find_in_prefixes() {  # $1 = relative path
    local p
    for p in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}" "${CVC_INSTALL_DIR}"; do
        if [ -n "${p}" ] && [ -e "${p}/$1" ]; then echo "${p}"; return 0; fi
    done
    return 1
}
NP="$(find_in_prefixes bin/node)" || { echo "FAIL: nodejs22 not found (build dependency)"; exit 1; }
PP="$(find_in_prefixes lib/node_modules/@playwright/test/cli.js)" \
    || { echo "FAIL: playwright not found (build dependency)"; exit 1; }

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
# A prefix shaped like `cvcpkg install playwright playwright-chromium-headless-shell`.
P="${T}/prefix"
mkdir -p "${P}/bin" "${P}/lib" "${P}/share"
ln -s "${NP}/bin/node" "${P}/bin/node"
cp -p "${PP}/bin/playwright" "${P}/bin/playwright"
ln -s "${PP}/lib/node_modules" "${P}/lib/node_modules"
ln -s "${B}" "${P}/share/ms-playwright"
export HOME="${T}/home" XDG_CACHE_HOME="${T}/cache" PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
unset PLAYWRIGHT_BROWSERS_PATH NODE_PATH || true
mkdir -p "${HOME}"

# ── 1. layout matches what this Playwright version expects ───────────
dry="$("${P}/bin/playwright" install --dry-run --only-shell chromium 2>&1)"
echo "${dry}" | sed 's/^/    /'
d="chromium_headless_shell-${REV}"
echo "${dry}" | grep -q "Install location: *${P}/share/ms-playwright/${d}\$" \
    || { echo "FAIL: playwright does not expect ${d} under share/ms-playwright"; exit 1; }
[ -f "${B}/${d}/INSTALLATION_COMPLETE" ] || { echo "FAIL: ${d} not staged"; exit 1; }
echo "  OK: ${d} is where Playwright looks"

# ── 2. host libraries ────────────────────────────────────────────────
SHELL_BIN="${B}/${d}/chrome-headless-shell-linux64/chrome-headless-shell"
missing="$(ldd "${SHELL_BIN}" 2>/dev/null | awk '/not found/ {print $1}' | sort -u | tr '\n' ' ')"
if [ -n "${missing}" ]; then
    echo "  Host is missing shared libraries Chromium needs: ${missing}"
    echo "  (Debian/Ubuntu: \`playwright install-deps chromium\`, or the package"
    echo "   list in this recipe's notes.)"
    if [ "${CVC_PLAYWRIGHT_REQUIRE_BROWSER:-0}" = "1" ]; then
        echo "FAIL: CVC_PLAYWRIGHT_REQUIRE_BROWSER=1 and host libraries are missing"; exit 1
    fi
    echo "  SKIP: browser launch (set CVC_PLAYWRIGHT_REQUIRE_BROWSER=1 to make this fatal)"
    echo "-- playwright-chromium-headless-shell smoke test passed (launch skipped) --"
    exit 0
fi
echo "  OK: every shared library the headless shell needs resolves on this host"

# ── 3. launch: data: URL in headless Chromium ────────────────────────
mkdir -p "${T}/proj" && cd "${T}/proj"
cat > playwright.config.js <<'JS'
module.exports = { reporter: 'line', workers: 1, use: { headless: true } };
JS
cat > chromium.spec.js <<JS
const { test, expect } = require('@playwright/test');
const url = 'data:text/html,' + encodeURIComponent(
  '<title>cvcpkg</title><h1 id="h">hello</h1>' +
  '<script>document.getElementById("h").textContent = "cvcpkg " + (6 * 7);</script>');

test('headless chromium renders a data: URL', async ({ page, browser }) => {
  await page.goto(url);
  await expect(page).toHaveTitle('cvcpkg');
  await expect(page.locator('#h')).toHaveText('cvcpkg 42');
  expect(browser.version()).toBe('${CHROME_VER}');
  console.log('browser ' + browser.version());
});
JS
if ! out="$("${P}/bin/playwright" test 2>&1)"; then
    echo "${out}"; echo "FAIL: playwright test"; exit 1
fi
echo "${out}" | sed 's/^/    /' | tail -6
case "${out}" in *"1 passed"*) ;; *) echo "FAIL: expected 1 passed"; exit 1 ;; esac
echo "  OK: headless Chromium ${CHROME_VER} rendered the page"
echo "-- playwright-chromium-headless-shell smoke test passed --"
