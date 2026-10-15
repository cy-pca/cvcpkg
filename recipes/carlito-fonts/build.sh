#!/usr/bin/env bash
# recipes/carlito-fonts/build.sh — stage the Carlito TrueType fonts.
#
# Nothing to compile: the repository carries the built .ttf files under
# fonts/ttf. Install them where fontconfig looks (share/fonts/truetype/carlito)
# and the OFL licence beside them, which the licence requires.
set -euo pipefail

: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

FONTDIR="${CVC_INSTALL_DIR}/share/fonts/truetype/carlito"
DOCDIR="${CVC_INSTALL_DIR}/share/doc/carlito-fonts"
mkdir -p "${FONTDIR}" "${DOCDIR}"

n=0
for f in "${CVC_SOURCE_DIR}"/fonts/ttf/Carlito-*.ttf; do
    [ -f "${f}" ] || continue
    cp "${f}" "${FONTDIR}/"
    chmod 0644 "${FONTDIR}/$(basename "${f}")"
    n=$((n + 1))
done
# Regular, Bold, Italic, BoldItalic.
[ "${n}" -eq 4 ] || { echo "expected 4 Carlito .ttf files, found ${n}" >&2; exit 1; }

for f in OFL.txt AUTHORS.txt README.md; do
    if [ -f "${CVC_SOURCE_DIR}/${f}" ]; then
        cp "${CVC_SOURCE_DIR}/${f}" "${DOCDIR}/" && chmod 0644 "${DOCDIR}/${f}"
    fi
done
[ -f "${DOCDIR}/OFL.txt" ] || { echo "no OFL.txt in the source" >&2; exit 1; }

echo "cvcpkg: installed ${n} Carlito fonts -> ${FONTDIR}"
