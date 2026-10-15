#!/usr/bin/env bash
# recipes/texlive/test.sh — smoke test the staged TeX Live.
#
#  1. pdflatex and lualatex compile a document that loads packages from each
#     shipped collection (T1 Latin Modern, babel English, AMS, TikZ, PGFPlots,
#     tcolorbox, enumitem, titlesec, booktabs, hyperref).
#  2. Relocatability: the prefix is copied elsewhere; the copy's kpsewhich
#     must report a tree inside the copy, and pdflatex must still work.
# HOME, TEXMFVAR and TEXMFCACHE are temp dirs, so nothing is written to the
# builder account or into the staged prefix (lualatex's font cache would
# otherwise land in lib/texlive/texmf-var and ship).
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "-- texlive smoke test (${CVC_PLATFORM:-native}) --"

T="$(mktemp -d)"
trap 'chmod -R u+w "${T}" 2>/dev/null || true; rm -rf "${T}"' EXIT
unset TEXMFCNF TEXMFHOME TEXMFCONFIG TEXINPUTS TEXMFLOCAL || true
# luaotfload wants its cache under a TEXMF var tree: point TEXMFCACHE at the
# temp TEXMFVAR (a bare directory is rejected as "no writeable cache path").
export HOME="${T}/home" TEXMFVAR="${T}/texmf-var" TEXMFCACHE="${T}/texmf-var"
mkdir -p "${HOME}" "${TEXMFVAR}"

tool() {  # $1 = prefix, $2 = tool
    if [ -f "$1/bin/$2.cmd" ]; then echo "$1/lib/texlive/bin/windows/$2.exe"; else echo "$1/bin/$2"; fi
}

cat > "${T}/doc.tex" <<'EOF'
\documentclass{article}
\usepackage[T1]{fontenc}
\usepackage[english]{babel}
\usepackage{lmodern,amsmath,booktabs,enumitem,titlesec,hyperref}
\usepackage{tikz,pgfplots}
\usepackage[most]{tcolorbox}
\pgfplotsset{compat=1.17}
\begin{document}
\section{cvcpkg texlive}
\begin{tcolorbox}[title=Check]$\displaystyle\int_0^1 x^2\,\mathrm{d}x=\frac13$\end{tcolorbox}
\begin{itemize}[nosep]\item one\item two\end{itemize}
\begin{tikzpicture}\begin{axis}[width=5cm]\addplot[domain=0:1]{x^2};\end{axis}\end{tikzpicture}
\end{document}
EOF

compile() {  # $1 = engine path, $2 = jobname
    (cd "${T}" && "$1" -interaction=nonstopmode -halt-on-error -jobname="$2" doc.tex >/dev/null) \
        || { tail -30 "${T}/$2.log"; echo "FAIL: $2"; exit 1; }
    [ -s "${T}/$2.pdf" ] || { echo "FAIL: $2 produced no PDF"; exit 1; }
    echo "  OK: $2 built $(wc -c < "${T}/$2.pdf") bytes"
}

PDFLATEX="$(tool "${CVC_INSTALL_DIR}" pdflatex)"
"${PDFLATEX}" --version | head -1 | sed 's/^/  /'
compile "${PDFLATEX}" pdflatex
compile "$(tool "${CVC_INSTALL_DIR}" lualatex)" lualatex
if [ -e "${CVC_INSTALL_DIR}/lib/texlive/texmf-var/luatex-cache" ]; then
    echo "FAIL: lualatex wrote its font cache into the staged prefix"; exit 1
fi

# ── relocate: copy the prefix, test the copy from here on ────────────
MOVED="${T}/relocated/prefix"
mkdir -p "${MOVED}"
cp -al "${CVC_INSTALL_DIR}/." "${MOVED}/" 2>/dev/null || cp -pR "${CVC_INSTALL_DIR}/." "${MOVED}/"
dist="$("$(tool "${MOVED}" kpsewhich)" -var-value=TEXMFDIST)"
want="$(cd "${MOVED}/lib/texlive/texmf-dist" && pwd -P)"
got="$(cd "${dist}" 2>/dev/null && pwd -P || echo "${dist}")"
[ "${got}" = "${want}" ] || { echo "FAIL: relocated TEXMFDIST is ${got}, expected ${want}"; exit 1; }
echo "  OK: relocated kpsewhich reports TEXMFDIST inside the copy"
fmt="$("$(tool "${MOVED}" kpsewhich)" -engine=pdftex pdflatex.fmt)"
case "${fmt}" in "${MOVED}"/*|"$(cd "${MOVED}" && pwd -P)"/*) ;; *) echo "FAIL: pdflatex.fmt resolved to ${fmt}"; exit 1 ;; esac
echo "  OK: relocated pdflatex.fmt found inside the copy"
compile "$(tool "${MOVED}" pdflatex)" relocated

echo "-- texlive smoke test passed --"
