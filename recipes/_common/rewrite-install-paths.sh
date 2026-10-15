#!/usr/bin/env bash
# recipes/_common/rewrite-install-paths.sh — relocatability helper.
#
# Rewrites absolute $CVC_INSTALL_DIR paths inside installed .pc and
# .cmake files so downstream consumers keep working when the package
# is unpacked at a different prefix. .pc files anchor at ${pcfiledir}
# and .cmake files at ${CMAKE_CURRENT_LIST_DIR}; the ../ suffix is
# computed per-file from its depth under $CVC_INSTALL_DIR.
#
# bin/*-config shell scripts (gsl-config, xml2-config, curl-config, ...) are
# rewritten too: they get a line computing the prefix from their own location
# ("$(dirname "$0")/..") and every absolute path becomes ${__cvc_prefix}
# (or '"${__cvc_prefix}"' where the path sits inside single quotes).
#
# Idempotent — running twice is a no-op because the second pass finds
# no absolute install-dir strings to rewrite.

cvc_rewrite_install_paths() {
    local root="${CVC_INSTALL_DIR%/}"
    [ -n "$root" ] || return 0
    [ -d "$root" ] || return 0

    # Also relocate the DEPS prefix. An installed .pc/.cmake can bake an absolute
    # path to a dependency that lived under $CVC_DEPS_PREFIX at build time — e.g. a
    # CMake target export whose INTERFACE_LINK_LIBRARIES carries ${ZLIB_LIBRARIES}
    # (an absolute path emitted by the classic FindZLIB module) instead of the
    # relocatable ZLIB::ZLIB target. That path exists on no consumer machine, so a
    # downstream find_package()+target_link fails everywhere. In cvcpkg's FLAT
    # install every dependency co-locates with the package at the consumer prefix,
    # so a $CVC_DEPS_PREFIX path resolves to the SAME per-file anchor as a
    # $CVC_INSTALL_DIR path. This only fires where such an absolute dep path
    # literally appears (a no-op for recipes whose exports are already clean —
    # CMake normally emits its OWN paths via ${_IMPORT_PREFIX}; the leak is
    # external-dep absolute paths it does not relocate).
    local deps="${CVC_DEPS_PREFIX:-}"
    deps="${deps%/}"

    local root_esc deps_esc
    root_esc=$(printf '%s' "$root" | sed 's/[]\/$*.^[]/\\&/g')
    [ -n "$deps" ] && deps_esc=$(printf '%s' "$deps" | sed 's/[]\/$*.^[]/\\&/g')

    local count=0
    local f dir remainder depth rel i anchor prefix has_root has_deps
    while IFS= read -r -d '' f; do
        has_root=0
        has_deps=0
        grep -q -F -- "$root" "$f" 2>/dev/null && has_root=1
        [ -n "$deps" ] && grep -q -F -- "$deps" "$f" 2>/dev/null && has_deps=1
        [ "$has_root" -eq 1 ] || [ "$has_deps" -eq 1 ] || continue
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
        # -i.bak keeps us portable across GNU and BSD sed. Rewrite the install
        # prefix first (it is the more specific match when the two prefixes
        # overlap), then the deps prefix.
        [ "$has_root" -eq 1 ] && sed -i.bak "s|${root_esc}|${prefix}|g" "$f"
        [ "$has_deps" -eq 1 ] && sed -i.bak "s|${deps_esc}|${prefix}|g" "$f"
        rm -f "${f}.bak"
        count=$((count + 1))
    done < <(find "$root" \( -name '*.pc' -o -name '*.cmake' \) -type f -print0)

    if [ "$count" -gt 0 ]; then
        echo "── cvc_rewrite_install_paths: normalized ${count} file(s) under ${root} ──"
    fi

    _cvc_rewrite_config_scripts "$root" "$deps"
}

# _cvc_rewrite_config_scripts <install-root> [deps-prefix]
#
# autoconf-style bin/*-config scripts (gsl-config, krb5-config, xml2-config,
# libpng16-config, curl-config, pcre2-config, geos-config, MagickWand-config,
# pspell-config, ...) echo compile/link flags built from absolute paths baked in
# at configure time -- the temporary install dir, and sometimes the deps prefix.
# Unpacked anywhere else they hand out dead -I/-L paths.  Each such script gets
#   __cvc_prefix="$(cd "$(dirname "$0")/.." && pwd -P)"
# after its shebang, and every occurrence of either prefix becomes
# ${__cvc_prefix}; inside single quotes (prefix='...', echo '--prefix=...')
# it becomes '"${__cvc_prefix}"' so the shell still expands it.  Only shell
# scripts are touched (a #!.../sh or bash shebang); binaries are left alone.
_cvc_rewrite_config_scripts() {
    local root="$1" deps="${2:-}" f first n=0 tmp
    [ -d "${root}/bin" ] || return 0
    for f in "${root}"/bin/*-config; do
        [ -f "$f" ] || continue
        [ -L "$f" ] && continue
        IFS= read -r first < "$f" || continue
        case "$first" in
            '#!'*sh*) ;;
            *) continue ;;
        esac
        grep -q -F -- "$root" "$f" 2>/dev/null \
            || { [ -n "$deps" ] && grep -q -F -- "$deps" "$f" 2>/dev/null; } \
            || continue
        tmp="${f}.cvc-tmp"
        awk -v root="$root" -v deps="$deps" '
            # Shell quote state at the end of s: 0 unquoted, 1 inside single
            # quotes, 2 inside double quotes (single quotes are literal there).
            function qstate(s,    st, k, c) {
                st = 0
                for (k = 1; k <= length(s); k++) {
                    c = substr(s, k, 1)
                    if (st == 0) {
                        if (c == "\\") k++
                        else if (c == "\047") st = 1
                        else if (c == "\"") st = 2
                    } else if (st == 1) {
                        if (c == "\047") st = 0
                    } else {
                        if (c == "\\") k++
                        else if (c == "\"") st = 0
                    }
                }
                return st
            }
            function relocate(line, p,    out, i, before) {
                out = ""
                while ((i = index(line, p)) > 0) {
                    before = out substr(line, 1, i - 1)
                    out = before (qstate(before) == 1 ? "\047\"${__cvc_prefix}\"\047" : "${__cvc_prefix}")
                    line = substr(line, i + length(p))
                }
                return out line
            }
            NR == 1 {
                print
                print "__cvc_prefix=\"$(cd \"$(dirname \"$0\")/..\" && pwd -P)\"  # cvc_rewrite_install_paths"
                next
            }
            {
                $0 = relocate($0, root)
                if (deps != "") $0 = relocate($0, deps)
                print
            }
        ' "$f" > "$tmp" && chmod u+w "$f" 2>/dev/null; cat "$tmp" > "$f"
        rm -f "$tmp"
        n=$((n + 1))
    done
    if [ "$n" -gt 0 ]; then
        echo "── cvc_rewrite_install_paths: relocated ${n} bin/*-config script(s) under ${root} ──"
    fi
}

# cvc_relocate_macos_dylibs — rewrite autotools/libtool dylib install names to
# @rpath (macOS only; no-op elsewhere).
#
# cvc_cmake_build gives CMake-built libraries @loader_path rpaths, but GNU
# autotools bakes the absolute --prefix (a per-build temp $CVC_INSTALL_DIR) into
# every produced dylib: both its own id (LC_ID_DYLIB) and its load commands for
# sibling libraries. Once the bundle is unpacked at a different prefix those
# temp paths no longer exist, so dyld aborts at load time
# ("Library not loaded: <build-temp>/lib/libFoo.dylib"). Rewrite the id and any
# dependency that points back into our install prefix to @rpath/<name>;
# consumers already add the real libdir to their rpath (-Wl,-rpath,.../lib).
#
# Operates on versioned real files (e.g. libgmpxx.4.dylib); the unversioned
# symlinks (libgmpxx.dylib) resolve to them, so consumers that link the symlink
# record the fixed @rpath id. Idempotent: entries already @rpath don't match the
# temp prefix. No-op off macOS or when install_name_tool/otool are unavailable.
cvc_relocate_macos_dylibs() {
    [ "${CVC_PLATFORM:-}" = "macos" ] || return 0
    command -v install_name_tool >/dev/null 2>&1 || return 0
    command -v otool >/dev/null 2>&1 || return 0

    local root="${CVC_INSTALL_DIR%/}"
    local libdir="${root}/lib"
    [ -d "$libdir" ] || return 0

    local count=0 dylib base dep
    while IFS= read -r -d '' dylib; do
        base="$(basename "$dylib")"
        chmod u+w "$dylib" 2>/dev/null || true
        # Set the library's own id to @rpath first, so the otool pass below sees
        # (and skips) the already-relocated id.
        install_name_tool -id "@rpath/${base}" "$dylib" 2>/dev/null || true
        while IFS= read -r dep; do
            case "$dep" in
                "${root}"/*)
                    install_name_tool -change "$dep" "@rpath/$(basename "$dep")" "$dylib" 2>/dev/null || true
                    ;;
            esac
        done < <(otool -L "$dylib" | awk 'NR>1 {print $1}')
        count=$((count + 1))
    done < <(find "$libdir" -type f -name '*.dylib' -print0)

    if [ "$count" -gt 0 ]; then
        echo "── cvc_relocate_macos_dylibs: relocated ${count} dylib(s) to @rpath under ${libdir} ──"
    fi
}
