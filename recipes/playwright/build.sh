#!/usr/bin/env bash
# recipes/playwright/build.sh — stage @playwright/test + playwright +
# playwright-core 1.64.0 as a global node_modules tree, plus launchers.
#
# Noarch (platform: any): the npm tarballs are pure JavaScript. Each is
# fetched from the npm registry, checked against its pinned sha256 and
# unpacked (the tarballs' package/ prefix stripped) -- no npm, no network
# besides the three downloads, no install scripts run. Like
# recipes/ca-bundle/build.sh this does not source _common/env-*.sh.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

PW_VER="1.64.0"
REG="https://registry.npmjs.org"

sha256_of() {  # reads stdin so a Windows path with backslashes is never an operand
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum < "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 < "$1" | awk '{print $1}'
    elif command -v sha256 >/dev/null 2>&1; then
        sha256 -q < "$1"
    else
        echo "no sha256 tool found (tried sha256sum, shasum, sha256)" >&2
        return 1
    fi
}

NM="${CVC_INSTALL_DIR}/lib/node_modules"
mkdir -p "${NM}" "${CVC_INSTALL_DIR}/bin"

# name | tarball URL | sha256 (also listed in recipe.yaml with the registry's
# sha512 integrity)
while IFS='|' read -r name url sha; do
    [ -n "${name}" ] || continue
    tgz="${CVC_BUILD_DIR}/$(basename "${url}")"
    echo "Downloading ${url}..."
    curl -fSL --retry 3 -o "${tgz}" "${url}"
    got="$(sha256_of "${tgz}")"
    if [ "${got}" != "${sha}" ]; then
        echo "sha256 mismatch for ${url}: expected ${sha}, got ${got}" >&2
        exit 1
    fi
    echo "sha256 OK (${got})"
    rm -rf "${NM:?}/${name}"
    mkdir -p "${NM}/${name}"
    tar xzf "${tgz}" -C "${NM}/${name}" --strip-components=1
    ver="$(sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*/\1/p' "${NM}/${name}/package.json" | head -n 1)"
    [ "${ver}" = "${PW_VER}" ] || { echo "${name}: package.json says ${ver}" >&2; exit 1; }
done <<EOF
@playwright/test|${REG}/@playwright/test/-/test-${PW_VER}.tgz|5b97c581e4d66baf4110d16562d3f11d573035af03d5354ece3d9d4bfbd0a3e5
playwright|${REG}/playwright/-/playwright-${PW_VER}.tgz|55cf2db31a1d9037b050ac272bec7a824c3c9ed3ef75a6e26d6e9e89d104f155
playwright-core|${REG}/playwright-core/-/playwright-core-${PW_VER}.tgz|90c8b6b19da0a1b1e9a004cd6ff3e49e82668a0856e01efcc0e2d9c2b4466bb6
EOF

# ── bin/playwright: @playwright/test's CLI on the prefix's node ──────────
cat > "${CVC_INSTALL_DIR}/bin/playwright" <<'SH'
#!/bin/sh
# cvcpkg playwright launcher: @playwright/test's CLI on the node in this
# prefix, with this prefix's packages and browsers.
self=$0
while [ -h "$self" ]; do
    link=$(readlink "$self")
    case $link in
        /*) self=$link ;;
        *) self=$(dirname -- "$self")/$link ;;
    esac
done
prefix=$(CDPATH= cd -- "$(dirname -- "$self")/.." && pwd)
node=$prefix/bin/node
[ -x "$node" ] || node=$prefix/bin/node.exe
if [ ! -x "$node" ]; then
    echo "playwright: no node in $prefix/bin; install nodejs22 into this prefix" >&2
    exit 127
fi
# Specs that require('@playwright/test') resolve it from this prefix.
NODE_PATH=$prefix/lib/node_modules${NODE_PATH:+:$NODE_PATH}
export NODE_PATH
# Browsers from the cvcpkg browser packages, unless the caller chose a path.
if [ -z "${PLAYWRIGHT_BROWSERS_PATH:-}" ] && [ -d "$prefix/share/ms-playwright" ]; then
    PLAYWRIGHT_BROWSERS_PATH=$prefix/share/ms-playwright
    export PLAYWRIGHT_BROWSERS_PATH
fi
exec "$node" "$prefix/lib/node_modules/@playwright/test/cli.js" "$@"
SH
chmod 0755 "${CVC_INSTALL_DIR}/bin/playwright"

sed 's/$/\r/' > "${CVC_INSTALL_DIR}/bin/playwright.cmd" <<'CMD'
@echo off
rem cvcpkg playwright launcher: @playwright/test's CLI on the node in this prefix.
setlocal
set "PW_PREFIX=%~dp0.."
if defined NODE_PATH (set "NODE_PATH=%PW_PREFIX%\lib\node_modules;%NODE_PATH%") else (set "NODE_PATH=%PW_PREFIX%\lib\node_modules")
if not defined PLAYWRIGHT_BROWSERS_PATH if exist "%PW_PREFIX%\share\ms-playwright" set "PLAYWRIGHT_BROWSERS_PATH=%PW_PREFIX%\share\ms-playwright"
"%PW_PREFIX%\bin\node.exe" "%PW_PREFIX%\lib\node_modules\@playwright\test\cli.js" %*
exit /b %ERRORLEVEL%
CMD

sh -n "${CVC_INSTALL_DIR}/bin/playwright"
echo "playwright ${PW_VER} staged to ${CVC_INSTALL_DIR}"
