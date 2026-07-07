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
# Restore them recursively on all files in bin/ and libexec/.
find bin libexec -type f -exec chmod +x {} +

# Python's zipfile.extractall() also does not preserve symlinks — they
# are extracted as small regular files whose content is the link target.
# Scan directories for such files and recreate them as proper symlinks
# so that multicall binaries (cosmocross, cosmocc) are invoked with
# the correct argv[0].
_fix_broken_symlinks() {
    local dir="$1"
    for f in "$dir"/*; do
        [[ -f "$f" ]] || continue
        local sz
        sz=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null)
        # Real binaries/scripts are >100 bytes; broken "symlinks" are tiny
        if (( sz < 100 )); then
            local target
            target=$(cat "$f")
            # Sanity: target must be a single token (no whitespace/slashes)
            if [[ "$target" =~ ^[a-zA-Z0-9._+-]+$ ]] && [[ -e "$dir/$target" ]]; then
                rm "$f"
                ln -s "$target" "$f"
            fi
        fi
    done
}
_fix_broken_symlinks bin
# libexec has deep subdirectories (gcc/x86_64-linux-cosmo/14.1.0/ etc.)
while IFS= read -r subdir; do
    _fix_broken_symlinks "$subdir"
done < <(find libexec -type d)

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
