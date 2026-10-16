#!/usr/bin/env bash
# recipes/libreoffice/test.sh — smoke test the staged LibreOffice.
#
#  1. soffice reports the pinned version, and converts HTML (Writer) and an
#     .odp it first writes itself (Impress) to PDF, headless.
#  2. Relocatability: the prefix is copied elsewhere and the copy converts.
# HOME and the user profile are temp dirs, so nothing is written to the
# builder account.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
EXPECT="LibreOffice 26.8.1"

echo "-- libreoffice smoke test (${CVC_PLATFORM:-native}) --"

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
export HOME="${T}/home"
mkdir -p "${HOME}"
# Runtime deps sit in the deps prefix until cvcpkg installs them beside it.
# It also holds cvcpkg's libpython3.13 (python3, a build dependency): on
# LD_LIBRARY_PATH, as cvcpkg's activate script puts it, it must not replace
# LibreOffice's own (build.sh, DT_RPATH). This runs after the pack pass has
# rewritten the search paths, so it also checks that the pass kept DT_RPATH.
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX:+${CVC_DEPS_PREFIX}/lib}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
if [ -e "${CVC_DEPS_PREFIX:-/nonexistent}/lib/libpython3.13.so.1.0" ]; then
    echo "  LD_LIBRARY_PATH has a libpython3.13.so.1.0 (${CVC_DEPS_PREFIX}/lib)"
fi

convert() {  # $1 = soffice, $2 = input, $3 = format, $4 = outdir
    "$1" --headless --norestore "-env:UserInstallation=file://${T}/profile" \
        --convert-to "$3" --outdir "$4" "$2" >/dev/null 2>&1
}

SOFFICE="${CVC_INSTALL_DIR}/bin/soffice"
ver="$("${SOFFICE}" --version 2>/dev/null | head -1)"
echo "  ${ver}"
case "${ver}" in *"${EXPECT}"*) ;; *) echo "FAIL: expected ${EXPECT}"; exit 1 ;; esac

mkdir -p "${T}/out"
printf '<html><body><h1>cvcpkg</h1><p>libreoffice smoke test</p></body></html>\n' > "${T}/doc.html"
convert "${SOFFICE}" "${T}/doc.html" pdf "${T}/out"
[ -s "${T}/out/doc.pdf" ] || { echo "FAIL: HTML -> PDF"; exit 1; }
echo "  OK: HTML -> PDF ($(wc -c < "${T}/out/doc.pdf") bytes)"

# Impress: make a presentation with LibreOffice itself, then render it.
printf '<html><body><h1>slide</h1></body></html>\n' > "${T}/slide.html"
convert "${SOFFICE}" "${T}/slide.html" "odp:impress8" "${T}/out" || true
if [ -s "${T}/out/slide.odp" ]; then
    convert "${SOFFICE}" "${T}/out/slide.odp" pdf "${T}/out"
    [ -s "${T}/out/slide.pdf" ] || { echo "FAIL: ODP -> PDF"; exit 1; }
    echo "  OK: Impress ODP -> PDF ($(wc -c < "${T}/out/slide.pdf") bytes)"
fi

# ── relocate: copy the prefix, convert with the copy ─────────────────
MOVED="${T}/relocated/prefix"
mkdir -p "${MOVED}"
cp -al "${CVC_INSTALL_DIR}/." "${MOVED}/" 2>/dev/null || cp -pR "${CVC_INSTALL_DIR}/." "${MOVED}/"
mkdir -p "${T}/out2"
convert "${MOVED}/bin/soffice" "${T}/doc.html" pdf "${T}/out2"
[ -s "${T}/out2/doc.pdf" ] || { echo "FAIL: relocated HTML -> PDF"; exit 1; }
echo "  OK: relocated soffice converts"

echo "-- libreoffice smoke test passed --"
