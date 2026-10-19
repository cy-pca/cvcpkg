#!/usr/bin/env bash
# recipes/texlive/build.sh — stage TeX Live 2025 (pdfTeX, LuaTeX and LaTeX) from
# TeX Live's own frozen repository.
#
# Nothing is compiled. TeX Live 2025's final network repository ("tlnet-final")
# is frozen in TUG's historic archive, so one sha512 for its package database
# (texlive.tlpdb) pins every package: tl-mirror.pl walks the dependency closure
# of the collections below for this platform, downloads each runfile container
# and checks it against the sha512 the tlpdb records. TeX Live's own installer
# (install-tl, pinned the same way) then installs offline from that local
# mirror, so the generated files (fmtutil.cnf, updmap maps, language.dat,
# formats, ls-R) are exactly what a stock TeX Live install produces.
#
# Layout: the TeX Live tree is <prefix>/lib/texlive (TEXDIR, with texmf-var,
# texmf-config and texmf-local inside it). bin/ holds relative symlinks into
# lib/texlive/bin/<platform>; kpathsea resolves the symlink and finds its tree
# from the real binary, so the prefix relocates.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Keep in step with build.ps1 and recipe.yaml's source block.
BASE="https://ftp.math.utah.edu/pub/tex/historic/systems/texlive/2025/tlnet-final"
TLPDB_SHA512="2fb7adc54cff58fa55c5b7c1bd3034e1efd849d0b73535c8dcdf0d8a36f66998b7a578ae036ef24354641be5f3738b0ad1323b7318581d88a0e271d1ac12cb2b"
INSTALLER="install-tl-unx.tar.gz"
INSTALLER_SHA512="a307d7d11bcbd1f054ad0b0d476f7f12bc1a40d07445020edef8713b44453831d18a2f1722c3d2b0ea2e4fe6c06183a79d1c4049495113f412a9f5a570a8614d"

# What ships. latexextra is most of the package count; doc and source
# containers are never fetched.
COLLECTIONS=(
    collection-basic
    collection-latex
    collection-latexrecommended
    collection-latexextra
    collection-pictures
    collection-fontsrecommended
    collection-langenglish
)
# install-tl installs these whatever the selection
# (TeXLive::TLConfig::InstallExtraRequiredPackages); mirror them too.
EXTRA=(texlive-scripts kpathsea hyphen-base)

# Executables linked into bin/ (those this platform's TeX Live has). The perl
# scripts among them (tlmgr, fmtutil, updmap, mktexlsr) need a perl on PATH at
# run time; the TeX engines do not.
TOOLS=(
    tex etex pdftex pdflatex latex
    luatex luahbtex lualatex dvilualatex texlua texluac
    kpsewhich kpseaccess kpsestat kpsereadlink
    bibtex bibtex8 bibtexu makeindex
    dvips dvipdfmx xdvipdfmx extractbb ebb
    mf mktexpk mktextfm mktexlsr texhash mktexfmt
    fmtutil fmtutil-sys fmtutil-user updmap updmap-sys updmap-user tlmgr
)

case "$(uname -s)-$(uname -m)" in
    Linux-x86_64)            TLARCH="x86_64-linux" ;;
    Linux-aarch64)           TLARCH="aarch64-linux" ;;
    Darwin-x86_64|Darwin-arm64) TLARCH="universal-darwin" ;;
    FreeBSD-amd64)           TLARCH="amd64-freebsd" ;;
    *) echo "TeX Live 2025 has no binaries for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

sha512_of() {
    if command -v sha512sum >/dev/null 2>&1; then
        sha512sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 512 "$1" | awk '{print $1}'
    elif command -v sha512 >/dev/null 2>&1; then
        sha512 -q "$1"
    else
        echo "no sha512 tool found (tried sha512sum, shasum, sha512)" >&2
        return 1
    fi
}

fetch_pinned() {  # $1 = path under BASE, $2 = destination, $3 = sha512
    if [ ! -f "$2" ] || [ "$(sha512_of "$2")" != "$3" ]; then
        echo "Downloading ${BASE}/$1 ..."
        curl -fSL --retry 4 --retry-delay 3 -o "$2" "${BASE}/$1"
    fi
    local got
    got="$(sha512_of "$2")"
    if [ "${got}" != "$3" ]; then
        echo "sha512 mismatch for $1: expected $3, got ${got}" >&2
        exit 1
    fi
    echo "sha512 OK: $1"
}

PERL="$(command -v perl || true)"
[ -n "${PERL}" ] || { echo "perl is required (install-tl and tl-mirror.pl are perl)" >&2; exit 1; }

W="${CVC_BUILD_DIR}/texlive-work"
mkdir -p "${W}"

# ── 1. pinned package database and installer ──────────────────────────
fetch_pinned tlpkg/texlive.tlpdb "${W}/texlive.tlpdb" "${TLPDB_SHA512}"
fetch_pinned "${INSTALLER}" "${W}/${INSTALLER}" "${INSTALLER_SHA512}"
rm -rf "${W}/installer" && mkdir -p "${W}/installer"
tar xzf "${W}/${INSTALLER}" -C "${W}/installer"
ITL="$(ls -d "${W}"/installer/install-tl-*)"
[ -f "${ITL}/install-tl" ] || { echo "no install-tl in ${INSTALLER}" >&2; exit 1; }

# ── 2. local mirror, every container checked against the tlpdb ────────
"${PERL}" "${SCRIPT_DIR}/tl-mirror.pl" --tlpdb "${W}/texlive.tlpdb" --base "${BASE}" \
    --dest "${W}/mirror" --arch "${TLARCH}" "${COLLECTIONS[@]}" "${EXTRA[@]}"

# ── 3. install-tl, offline, from the mirror ───────────────────────────
TEXDIR="${CVC_INSTALL_DIR}/lib/texlive"
rm -rf "${TEXDIR}"
{
    echo "selected_scheme scheme-custom"
    echo "TEXDIR ${TEXDIR}"
    echo "TEXMFLOCAL ${TEXDIR}/texmf-local"
    echo "TEXMFSYSVAR ${TEXDIR}/texmf-var"
    echo "TEXMFSYSCONFIG ${TEXDIR}/texmf-config"
    echo "TEXMFHOME ~/texmf"
    echo "TEXMFVAR ~/.texlive2025/texmf-var"
    echo "TEXMFCONFIG ~/.texlive2025/texmf-config"
    echo "binary_${TLARCH} 1"
    for c in "${COLLECTIONS[@]}"; do echo "${c} 1"; done
    cat <<'EOF'
instopt_adjustpath 0
instopt_adjustrepo 0
instopt_letter 0
instopt_portable 0
instopt_write18_restricted 1
tlpdbopt_autobackup 0
tlpdbopt_create_formats 1
tlpdbopt_desktop_integration 0
tlpdbopt_file_assocs 0
tlpdbopt_generate_updmap 0
tlpdbopt_install_docfiles 0
tlpdbopt_install_srcfiles 0
tlpdbopt_post_code 1
tlpdbopt_w32_multi_user 0
EOF
} > "${W}/texlive.profile"

# A scratch HOME keeps ~/.texlive2025 and friends out of the builder account,
# and no inherited TeX variable may steer the install.
mkdir -p "${W}/home"
env -u TEXMFCNF -u TEXMFHOME -u TEXMFVAR -u TEXMFCONFIG -u TEXINPUTS -u TEXMFLOCAL \
    HOME="${W}/home" \
    "${PERL}" "${ITL}/install-tl" -profile "${W}/texlive.profile" \
        -repository "${W}/mirror" -no-verify-downloads -no-interaction

BIN="${TEXDIR}/bin/${TLARCH}"
[ -x "${BIN}/pdflatex" ] || { echo "install-tl did not produce bin/${TLARCH}/pdflatex" >&2; exit 1; }

# ── 4. make the tree relocatable and point it at the pinned repository ─
# install-tl writes the TEXMF* locations it was given into TEXDIR/texmf.cnf as
# absolute paths; TEXDIR is kpathsea's $SELFAUTOPARENT, so say that instead.
sed -i.bak "s|${TEXDIR}|\$SELFAUTOPARENT|g" "${TEXDIR}/texmf.cnf" && rm -f "${TEXDIR}/texmf.cnf.bak"
if grep -n "${CVC_INSTALL_DIR}" "${TEXDIR}/texmf.cnf"; then
    echo "texmf.cnf still names the staging prefix" >&2; exit 1
fi
# tlmgr updates must come from the same frozen repository, never live CTAN.
HOME="${W}/home" "${BIN}/tlmgr" option repository "${BASE}"
# The install log and profile record the build machine's paths; the format
# build logs too. None is read at run time.
rm -f "${TEXDIR}/install-tl.log" "${TEXDIR}/tlpkg/texlive.profile"
find "${TEXDIR}/texmf-var/web2c" -name '*.log' -delete
rm -rf "${TEXDIR}/tlpkg/backups"
# Generated files carry provenance comments naming the build job's paths
# ("Generated by <job>/install-tl-*/install-tl", "% <job>/texmf-var/...map:").
# Rewrite them so the bundle names no build machine, then insist that no
# staging path is left anywhere outside a comment.
grep -rIl -e "${W}" -e "${TEXDIR}" "${TEXDIR}" 2>/dev/null | while IFS= read -r f; do
    sed -i.bak -e "s|${W}/installer/install-tl-[0-9]*/install-tl|install-tl|g" \
               -e "s|${TEXDIR}|<texlive>|g" "${f}" && rm -f "${f}.bak"
done
if grep -rIn -e "${CVC_BUILD_DIR}" -e "${CVC_INSTALL_DIR}" "${TEXDIR}" | grep -v -E ':[0-9]+:[[:space:]]*(%|#|--)'; then
    echo "staging paths left in the TeX Live tree (above)" >&2; exit 1
fi

# ── 4b. macOS: nothing from XQuartz ───────────────────────────────────
# universal-darwin's mf (METAFONT with an X11 window) and xdvi-xaw link
# XQuartz's libraries in /opt/X11 (libX11, libXt, libXaw, ...), which a clean
# Mac does not have, so the bundle failed cvcpkg's loader-path gate (the
# 2026-10-18 macOS arm64 populate).  mf-nowin is the same METAFONT without the
# window, and it is what fmtutil builds mf.base with, so mf becomes a link to
# it.  xdvi is an X11 previewer with no X-less build: it goes, with its
# wrapper script and any link left dangling.
if [ "$(uname -s)" = Darwin ]; then
    if [ -x "${BIN}/mf-nowin" ]; then
        rm -f "${BIN}/mf" && ln -s mf-nowin "${BIN}/mf"
    fi
    for f in "${BIN}"/*; do
        if [ -f "${f}" ] && [ ! -L "${f}" ] && otool -L "${f}" 2>/dev/null | grep -q '/opt/X11/'; then
            echo "texlive: dropping $(basename "${f}"), which links XQuartz (/opt/X11)"
            rm -f "${f}"
        fi
    done
    rm -f "${BIN}/xdvi"
    for f in "${BIN}"/*; do
        if [ -L "${f}" ] && [ ! -e "${f}" ]; then rm -f "${f}"; fi
    done
fi

# ── 5. bin/ links ─────────────────────────────────────────────────────
mkdir -p "${CVC_INSTALL_DIR}/bin"
for t in "${TOOLS[@]}"; do
    if [ -e "${BIN}/${t}" ]; then
        ln -sfn "../lib/texlive/bin/${TLARCH}/${t}" "${CVC_INSTALL_DIR}/bin/${t}"
    fi
done

# ── 6. prove it: the staged pdflatex builds a document ────────────────
T="${W}/smoke"
rm -rf "${T}" && mkdir -p "${T}"
cat > "${T}/smoke.tex" <<'EOF'
\documentclass{article}
\usepackage[T1]{fontenc}
\usepackage{lmodern,amsmath,tikz,pgfplots,tcolorbox,booktabs,hyperref}
\pgfplotsset{compat=1.17}
\begin{document}
\begin{tcolorbox}cvcpkg texlive smoke test: $\int_0^1 x\,\mathrm{d}x=\tfrac12$\end{tcolorbox}
\begin{tikzpicture}\begin{axis}[width=5cm]\addplot{x^2};\end{axis}\end{tikzpicture}
\end{document}
EOF
( cd "${T}" && env -u TEXMFCNF HOME="${W}/home" TEXMFVAR="${T}/var" TEXMFCACHE="${T}/var" \
      "${CVC_INSTALL_DIR}/bin/pdflatex" \
      -interaction=nonstopmode -halt-on-error smoke.tex >/dev/null ) \
    || { tail -40 "${T}/smoke.log" >&2; exit 1; }
[ -s "${T}/smoke.pdf" ] || { echo "pdflatex produced no PDF" >&2; exit 1; }
"${CVC_INSTALL_DIR}/bin/pdflatex" --version | head -1
du -sh "${TEXDIR}"
rm -rf "${W}/mirror" "${W}/installer" "${T}"
