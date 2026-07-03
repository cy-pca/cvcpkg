#!/usr/bin/env bash
# recipes/_common/rewrite-install-paths.sh — relocatability helper.
#
# Rewrites absolute $CVC_INSTALL_DIR paths inside installed .pc and
# .cmake files so downstream consumers keep working when the package
# is unpacked at a different prefix. .pc files anchor at ${pcfiledir}
# and .cmake files at ${CMAKE_CURRENT_LIST_DIR}; the ../ suffix is
# computed per-file from its depth under $CVC_INSTALL_DIR.
#
# Idempotent — running twice is a no-op because the second pass finds
# no absolute install-dir strings to rewrite.

cvc_rewrite_install_paths() {
    local root="${CVC_INSTALL_DIR%/}"
    [ -n "$root" ] || return 0
    [ -d "$root" ] || return 0

    local root_esc
    root_esc=$(printf '%s' "$root" | sed 's/[]\/$*.^[]/\\&/g')

    local count=0
    local f dir remainder depth rel i anchor prefix
    while IFS= read -r -d '' f; do
        grep -q -F -- "$root" "$f" 2>/dev/null || continue
        dir="$(dirname "$f")"
        remainder="${dir#$root}"
        remainder="${remainder#/}"
        depth=0
        if [ -n "$remainder" ]; then
            local _IFS_saved="$IFS"
            IFS=/
            for _ in $remainder; do depth=$((depth + 1)); done
            IFS="$_IFS_saved"
        fi
        rel=""
        for ((i = 0; i < depth; i++)); do rel+="../"; done
        rel="${rel%/}"
        [ -z "$rel" ] && rel="."
        case "$f" in
            *.pc)    anchor='${pcfiledir}' ;;
            *.cmake) anchor='${CMAKE_CURRENT_LIST_DIR}' ;;
            *)       continue ;;
        esac
        prefix="${anchor}/${rel}"
        # -i.bak keeps us portable across GNU and BSD sed.
        sed -i.bak "s|${root_esc}|${prefix}|g" "$f"
        rm -f "${f}.bak"
        count=$((count + 1))
    done < <(find "$root" \( -name '*.pc' -o -name '*.cmake' \) -type f -print0)

    if [ "$count" -gt 0 ]; then
        echo "── cvc_rewrite_install_paths: normalized ${count} file(s) under ${root} ──"
    fi
}
