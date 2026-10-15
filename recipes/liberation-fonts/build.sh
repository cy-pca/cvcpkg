#!/usr/bin/env bash
# recipes/liberation-fonts/build.sh — stage the Liberation TrueType fonts.
#
# Nothing to compile: the release tarball holds the .ttf files. Install them
# where fontconfig looks (share/fonts/truetype/liberation) and the OFL licence
# beside them, which the licence requires to travel with the fonts.
set -euo pipefail

: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

FONTDIR="${CVC_INSTALL_DIR}/share/fonts/truetype/liberation"
DOCDIR="${CVC_INSTALL_DIR}/share/doc/liberation-fonts"
mkdir -p "${FONTDIR}" "${DOCDIR}"

n=0
for f in "${CVC_SOURCE_DIR}"/Liberation*.ttf; do
    [ -f "${f}" ] || continue
    cp "${f}" "${FONTDIR}/"
    chmod 0644 "${FONTDIR}/$(basename "${f}")"
    n=$((n + 1))
done
# Sans, Serif and Mono, each Regular/Bold/Italic/BoldItalic.
[ "${n}" -eq 12 ] || { echo "expected 12 Liberation .ttf files, found ${n}" >&2; exit 1; }

for f in LICENSE AUTHORS README.md ChangeLog; do
    if [ -f "${CVC_SOURCE_DIR}/${f}" ]; then
        cp "${CVC_SOURCE_DIR}/${f}" "${DOCDIR}/" && chmod 0644 "${DOCDIR}/${f}"
    fi
done
[ -f "${DOCDIR}/LICENSE" ] || { echo "no LICENSE in the release tarball" >&2; exit 1; }

echo "cvcpkg: installed ${n} Liberation fonts -> ${FONTDIR}"
