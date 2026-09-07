#!/usr/bin/env bash
# recipes/automake/build.sh — build GNU Automake from source.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

# Prefer the autoconf/autom4te cvcpkg installed into this build's closure over
# anything on the host. That bundle is now relocatable (see recipes/autoconf),
# and using it avoids the OpenBSD/NetBSD trap where the ports `autoconf` is a
# version-dispatch wrapper that aborts unless AUTOCONF_VERSION is exported —
# which made automake's configure report "Autoconf 2.65 or better is required".
for _root in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
    [[ -n "${_root}" ]] || continue
    [[ -z "${AUTOCONF:-}" && -x "${_root}/bin/autoconf" ]] && export AUTOCONF="${_root}/bin/autoconf"
    [[ -z "${AUTOM4TE:-}" && -x "${_root}/bin/autom4te" ]] && export AUTOM4TE="${_root}/bin/autom4te"
done

# GNU make on BSD; and, as a last resort where the closure lacks autoconf, fall
# back to the ports-versioned autotools names.
MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        if command -v gmake >/dev/null 2>&1; then
            MAKE=gmake
        fi
        if [[ -z "${AUTOCONF:-}" ]]; then
            for candidate in autoconf-2.72 autoconf-2.71 autoconf-2.69 autoconf; do
                if command -v "${candidate}" >/dev/null 2>&1; then
                    export AUTOCONF="${candidate}"
                    break
                fi
            done
        fi
        if [[ -z "${AUTOM4TE:-}" ]]; then
            for candidate in autom4te-2.72 autom4te-2.71 autom4te-2.69 autom4te; do
                if command -v "${candidate}" >/dev/null 2>&1; then
                    export AUTOM4TE="${candidate}"
                    break
                fi
            done
        fi
        ;;
esac

cd "${CVC_SOURCE_DIR}"

if ! ./configure --prefix="${CVC_INSTALL_DIR}"; then
    # The build-log tail never carries config.log; surface the toolchain-check
    # region so a failure is diagnosable instead of a bare "autoconf does not
    # work" / "Autoconf 2.65 or better is required".
    echo "===== configure FAILED (automake) — config.log excerpt ====="
    grep -nE "autoconf is installed|autoconf works|Autoconf 2.65|autom4te|Can't locate|need GNU m4" \
        config.log 2>/dev/null | head -20 || true
    echo "===== end config.log excerpt ====="
    exit 1
fi

# Stub help2man. The man pages are not in this recipe's package.files, so
# regenerating the versioned .1 pages is pure wasted work — and it runs the
# freshly-built `bin/automake --help`, which fails the whole build on OpenBSD.
# A no-op that just creates the --output target keeps `make`/`make install`
# happy without shipping (or depending on) man pages.
_h2m="${CVC_BUILD_DIR:-${CVC_SOURCE_DIR}}/.cvcpkg-help2man"
cat > "${_h2m}" <<'STUB'
#!/bin/sh
out=
while [ $# -gt 0 ]; do
    case "$1" in
        --output=*) out=${1#--output=} ;;
        -o) shift; out=$1 ;;
    esac
    shift
done
[ -n "${out}" ] && : > "${out}"
exit 0
STUB
chmod +x "${_h2m}"

"${MAKE}" -j "${CVC_JOBS}" HELP2MAN="${_h2m}"
"${MAKE}" install HELP2MAN="${_h2m}"

# Relocate the installed automake. Like autoconf, `bin/automake` and `bin/aclocal`
# bake ${CVC_INSTALL_DIR}/share/automake-<ver> as the @INC dir for their Perl
# modules (Automake::*), so the INSTALLED tool cannot find its own modules once
# cvcpkg reaps the ephemeral build prefix — it would die "Can't locate
# Automake/Config.pm in @INC" on every platform. Inject a BEGIN that prepends the
# real, self-relative module dir (<prefix>/share/automake-<ver>) derived from the
# tool's own path. Idempotent (guarded by a marker).
_reloc='BEGIN {
  # cvcpkg relocation: find our Automake/* modules relative to this script.
  my $s = $0; $s = "./$s" if $s !~ m{/};
  (my $p = $s) =~ s{/[^/]+/[^/]+$}{};
  for my $d (glob("$p/share/automake-*"), glob("$p/share/aclocal-*")) {
    unshift @INC, $d if -d "$d/Automake" || -d $d;
  }
}'
_relf="$(mktemp "${TMPDIR:-/tmp}/cvcpkg-automake-reloc.XXXXXX")"
printf '%s\n' "${_reloc}" > "${_relf}"
for _t in "${CVC_INSTALL_DIR}"/bin/automake* "${CVC_INSTALL_DIR}"/bin/aclocal*; do
    [ -f "${_t}" ] || continue
    head -1 "${_t}" | grep -q perl || continue
    _RELF="${_relf}" perl -0777 -i -pe '
        BEGIN { local $/; open my $fh, "<", $ENV{"_RELF"} or die $!; our $B = <$fh>; close $fh; }
        s/(\n)(use warnings[^\n]*;\n)/$1$2\n$B\n/ unless /cvcpkg relocation/;
    ' "${_t}"
done
rm -f "${_relf}"
grep -q "cvcpkg relocation" "${CVC_INSTALL_DIR}/bin/automake" \
    || { echo "automake reloc: @INC injection missing from bin/automake" >&2; exit 1; }
