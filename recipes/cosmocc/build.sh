#!/usr/bin/env bash
# recipes/cosmocc/build.sh — install the cosmocc toolchain bundle.
#
# cosmocc.zip is a self-contained tree with bin/, include/, lib/,
# libexec/, x86_64-linux-cosmo/, aarch64-linux-cosmo/.  All binaries
# use relative paths so the tree is fully relocatable — we just copy
# it under $CVC_INSTALL_DIR/cosmocc/.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"

dest="${CVC_INSTALL_DIR}/cosmocc"
mkdir -p "${dest}"

# strip_components:0 means the source dir has cosmocc's top-level
# entries directly (bin/, include/, lib/, ...).  Copy them.
cd "${CVC_SOURCE_DIR}"

# Python's zipfile.extractall() does not preserve Unix executable bits.
# Restore them on all ELF/APE binaries in bin/ and libexec/ before copying.
chmod +x bin/* libexec/*/* 2>/dev/null || true

cp -a bin include lib libexec x86_64-linux-cosmo aarch64-linux-cosmo "${dest}/"
# Preserve top-level metadata files if present.
for f in LICENSE.gpl2 LICENSE.gpl3 LICENSE.lgpl2 LICENSE.lgpl3 Name README.md; do
    if [[ -f "$f" ]]; then
        cp -a "$f" "${dest}/"
    fi
done

# Sanity check.
if [[ ! -x "${dest}/bin/cosmocc" ]]; then
    echo "error: cosmocc binary not found at ${dest}/bin/cosmocc after install" >&2
    exit 1
fi

echo "cvcpkg: cosmocc toolchain installed at ${dest}"
"${dest}/bin/cosmocc" --version | head -1 || true
