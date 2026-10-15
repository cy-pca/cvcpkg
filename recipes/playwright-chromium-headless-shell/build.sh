#!/usr/bin/env bash
# recipes/playwright-chromium-headless-shell/build.sh — stage the headless
# Chromium build Playwright 1.64.0 runs, where PLAYWRIGHT_BROWSERS_PATH looks.
#
# playwright-core 1.64.0's browsers.json pins chromium-headless-shell at
# revision 1248 (Chromium 156.0.8078.4), downloaded from
# https://cdn.playwright.dev/builds/cft/<version>/<platform>/. We fetch exactly
# that zip, verify its pinned sha256 and unpack it to
# share/ms-playwright/chromium_headless_shell-1248 -- the directory
# `playwright install chromium-headless-shell` would create -- with the
# INSTALLATION_COMPLETE marker Playwright writes after a download.
#
# Deliberately NOT the full "chromium" download (chrome-linux64.zip): that is
# Google Chrome for Testing, Chrome-branded under Google's terms of service,
# and it bundles the proprietary Widevine CDM (WidevineCdm/libwidevinecdm.so),
# whose LICENSE forbids distributing it. See the recipe notes.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

CHROME_VER="156.0.8078.4"
REV="1248"
CDN="https://cdn.playwright.dev/builds/cft/${CHROME_VER}"

case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) PLAT="linux64" ;;
    *) echo "playwright-chromium-headless-shell: only linux-x86_64 is pinned so far" \
            "($(uname -s)-$(uname -m))" >&2
       exit 1 ;;
esac

ZIP="chrome-headless-shell-${PLAT}.zip"
# Computed from the download when pinning: Playwright's CDN publishes no
# checksum file. Keep in step with recipe.yaml's source.artifacts.
ZIP_SHA256="c3e50596aef52c58f8fda3c6b3411c33d34c88b177b3ed1c2243359fd3b8ba20"

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

PY="$(command -v python3 || command -v python || true)"
[ -n "${PY}" ] || { echo "python3 is needed to unpack the zip (no unzip dependency)" >&2; exit 1; }

B="${CVC_INSTALL_DIR}/share/ms-playwright"
DEST="${B}/chromium_headless_shell-${REV}"
DOC="${CVC_INSTALL_DIR}/share/doc/playwright-chromium-headless-shell"
mkdir -p "${B}" "${DOC}"

url="${CDN}/${PLAT}/${ZIP}"
out="${CVC_BUILD_DIR}/${ZIP}"
echo "Downloading ${url}..."
curl -fSL --retry 3 -o "${out}" "${url}"
got="$(sha256_of "${out}")"
if [ "${got}" != "${ZIP_SHA256}" ]; then
    echo "sha256 mismatch for ${ZIP}: expected ${ZIP_SHA256}, got ${got}" >&2
    exit 1
fi
echo "sha256 OK (${got})"

rm -rf "${DEST}"
mkdir -p "${DEST}"
# zipfile restores neither modes nor symlinks on its own; do both, and refuse
# members that would land outside the destination.
"${PY}" -I - "${out}" "${DEST}" <<'PY'
import os, stat, sys, zipfile
src, dst = sys.argv[1], sys.argv[2]
root = os.path.realpath(dst)
with zipfile.ZipFile(src) as z:
    for info in z.infolist():
        target = os.path.join(dst, info.filename)
        if not os.path.realpath(target).startswith(root + os.sep):
            sys.exit("unsafe member: " + info.filename)
        mode = info.external_attr >> 16
        if info.is_dir():
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if stat.S_ISLNK(mode):
            os.symlink(z.read(info).decode(), target)
            continue
        with z.open(info) as f, open(target, "wb") as w:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                w.write(chunk)
        os.chmod(target, (stat.S_IMODE(mode) or 0o644) | stat.S_IRUSR | stat.S_IWUSR)
PY
rm -f "${out}"
: > "${DEST}/INSTALLATION_COMPLETE"

SHELL_BIN="${DEST}/chrome-headless-shell-${PLAT}/chrome-headless-shell"
[ -x "${SHELL_BIN}" ] || { echo "missing ${SHELL_BIN}" >&2; exit 1; }

# The zip carries the full open-source license compilation for this binary
# (Chromium's BSD-3-Clause plus every third-party component); keep a copy
# where cvcpkg packages put their docs.
cp -p "${DEST}/chrome-headless-shell-${PLAT}/LICENSE.headless_shell" "${DOC}/LICENSE"
cp -p "${DEST}/chrome-headless-shell-${PLAT}/ABOUT" "${DOC}/ABOUT"

echo "chromium-headless-shell ${CHROME_VER} (Playwright revision ${REV}) staged under ${B}"
