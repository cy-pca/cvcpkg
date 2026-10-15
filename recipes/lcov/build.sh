#!/usr/bin/env bash
# recipes/lcov/build.sh — lcov 2.0 on cvcpkg's perl, with its CPAN modules.
#
# Layout (see the recipe notes):
#   libexec/lcov/bin/     lcov's scripts, unmodified except the version stamp
#   libexec/lcov/lib/     lcovutil.pm, found by the scripts through FindBin
#   libexec/lcov/perl5/   the CPAN modules from cpan-modules.txt
#   bin/<tool>            /bin/sh launchers that run the scripts under
#                         <prefix>/bin/perl with PERL5LIB at perl5/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

: "${CVC_INSTALL_DIR:?}"
: "${CVC_SOURCE_DIR:?}"
: "${CVC_BUILD_DIR:?}"

LCOV_VERSION=2.0
LCOV_RELEASE=1
TOOLS="lcov geninfo genhtml gendesc genpng"
SUPPORT_SCRIPTS="p4udiff p4annotate getp4version get_signature gitblame gitdiff \
criteria analyzeInfoFiles spreadsheet.py py2lcov"

# The perl that will run lcov: the runtime closure's. XS modules are compiled
# against it, so it must be the same perl, not merely a perl.
PERL="${CVC_DEPS_PREFIX:-}/bin/perl"
if [[ ! -x "$PERL" ]]; then
    PERL="$(command -v perl)"
fi
"$PERL" -e 'exit($] >= 5.040 ? 0 : 1)' || {
    echo "cvcpkg: error: $PERL is $("$PERL" -e 'print $]'), expected cvcpkg's perl 5.40" >&2
    exit 1
}
echo "cvcpkg: using $PERL ($("$PERL" -e 'print $^V'))"

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

dest="${CVC_INSTALL_DIR}/libexec/lcov"
perl5="${dest}/perl5"
mkdir -p "${dest}/bin" "${dest}/lib" "${perl5}"

# ── CPAN modules, in manifest order ──────────────────────────────────────────
# No CPAN client and no network lookup: each pinned tarball is fetched, checked
# against its sha256 and installed with plain MakeMaker. Tests are not run;
# the smoke test at the end exercises what lcov uses.
work="${CVC_BUILD_DIR}/cpan"
mkdir -p "$work"
export PERL5LIB="${perl5}/lib/perl5"
export PERL_MM_USE_DEFAULT=1
unset PERL_MM_OPT PERL_MB_OPT PERL_LOCAL_LIB_ROOT PERL5OPT
while read -r dist version url want; do
    [[ -z "$dist" || "$dist" == \#* ]] && continue
    file="${work}/${url##*/}"
    curl -fsSL --retry 3 -o "$file" "$url"
    got=$(sha256 "$file")
    if [[ "$got" != "$want" ]]; then
        echo "cvcpkg: error: ${url##*/}: sha256 $got, expected $want" >&2
        exit 1
    fi
    srcdir="${work}/${dist}-${version}"
    rm -rf "$srcdir"
    tar -xzf "$file" -C "$work"
    [[ -d "$srcdir" ]] || { echo "cvcpkg: error: ${file##*/} did not unpack to ${srcdir}" >&2; exit 1; }
    echo "cvcpkg: installing CPAN ${dist} ${version}"
    (
        cd "$srcdir"
        "$PERL" Makefile.PL INSTALL_BASE="$perl5" INSTALLMAN1DIR=none INSTALLMAN3DIR=none \
            INSTALLSITEMAN1DIR=none INSTALLSITEMAN3DIR=none
        make
        make pure_install
    )
done < "${SCRIPT_DIR}/cpan-modules.txt"
# Install bookkeeping and any scripts the distributions carry: lcov uses neither.
rm -rf "${perl5}/bin" "${perl5}/man"
find "$perl5" \( -name .packlist -o -name perllocal.pod \) -delete
# MakeMaker installs modules read-only (0444). Make them owner-writable, or
# installing a newer bundle over this one fails with "Permission denied".
chmod -R u+w "$perl5"

# ── lcov ─────────────────────────────────────────────────────────────────────
# Upstream's source layout, which finds lib/ relative to bin/ through FindBin.
# `make install` is not used: it bakes absolute lib/bin paths and
# /usr/bin/perl into the scripts. Only the version is stamped, as it does.
stamp() {
    "$PERL" "${CVC_SOURCE_DIR}/bin/fix.pl" --version "$LCOV_VERSION" \
        --release "$LCOV_RELEASE" --fixver --exec "$1"
}
for t in $TOOLS; do
    install -m 0755 "${CVC_SOURCE_DIR}/bin/${t}" "${dest}/bin/${t}"
    stamp "${dest}/bin/${t}"
done
install -m 0644 "${CVC_SOURCE_DIR}/lib/lcovutil.pm" "${dest}/lib/lcovutil.pm"
stamp "${dest}/lib/lcovutil.pm"

mkdir -p "${CVC_INSTALL_DIR}/share/lcov/support-scripts"
for s in $SUPPORT_SCRIPTS; do
    install -m 0755 "${CVC_SOURCE_DIR}/bin/${s}" "${CVC_INSTALL_DIR}/share/lcov/support-scripts/${s}"
done
install -m 0644 "${CVC_SOURCE_DIR}/lcovrc" "${CVC_INSTALL_DIR}/share/lcov/lcovrc"
mkdir -p "${CVC_INSTALL_DIR}/share/man/man1" "${CVC_INSTALL_DIR}/share/man/man5"
for m in lcov geninfo genhtml gendesc genpng; do
    install -m 0644 "${CVC_SOURCE_DIR}/man/${m}.1" "${CVC_INSTALL_DIR}/share/man/man1/${m}.1"
done
install -m 0644 "${CVC_SOURCE_DIR}/man/lcovrc.5" "${CVC_INSTALL_DIR}/share/man/man5/lcovrc.5"

# ── launchers ────────────────────────────────────────────────────────────────
mkdir -p "${CVC_INSTALL_DIR}/bin"
for t in $TOOLS; do
    cat > "${CVC_INSTALL_DIR}/bin/${t}" <<EOF
#!/bin/sh
# cvcpkg launcher for lcov's ${t}: run it under this prefix's perl with the
# CPAN modules bundled in libexec/lcov/perl5. <prefix>/bin goes first on PATH
# because lcov starts geninfo through its #!/usr/bin/env perl line.
self=\$0
while [ -L "\$self" ]; do
    link=\$(readlink "\$self")
    case \$link in
        /*) self=\$link ;;
        *) self=\$(dirname "\$self")/\$link ;;
    esac
done
prefix=\$(cd "\$(dirname "\$self")/.." && pwd -P) || exit 1
if [ ! -x "\$prefix/bin/perl" ]; then
    echo "${t}: \$prefix/bin/perl is missing; reinstall cvcpkg's perl into this prefix" >&2
    exit 127
fi
PATH="\$prefix/bin:\$PATH"
PERL5LIB="\$prefix/libexec/lcov/perl5/lib/perl5\${PERL5LIB:+:\$PERL5LIB}"
export PATH PERL5LIB
exec "\$prefix/bin/perl" "\$prefix/libexec/lcov/bin/${t}" "\$@"
EOF
    chmod 0755 "${CVC_INSTALL_DIR}/bin/${t}"
    sh -n "${CVC_INSTALL_DIR}/bin/${t}"
done

# ── smoke test ───────────────────────────────────────────────────────────────
# During the build perl lives in the deps prefix, not next to the launchers,
# so run the scripts the way the launchers do, with that perl.
run() {
    local t=$1
    shift
    PATH="$(dirname "$PERL"):$PATH" PERL5LIB="${perl5}/lib/perl5" \
        "$PERL" "${dest}/bin/${t}" "$@"
}
run lcov --version | tee /dev/stderr | grep -q "LCOV version ${LCOV_VERSION}-${LCOV_RELEASE}"
run genhtml --version | grep -q "LCOV version ${LCOV_VERSION}"
PERL5LIB="${perl5}/lib/perl5" "$PERL" -e \
    'use Capture::Tiny; use DateTime; use Date::Parse; print "DateTime ", DateTime->VERSION, " ", DateTime->now->ymd, "\n"'

# A real capture and report where a gcc with gcov is at hand (the Linux
# builders): exercises geninfo (Capture::Tiny) and genhtml (DateTime).
if [[ "${CVC_PLATFORM}" == "linux" ]] && command -v gcc >/dev/null 2>&1 \
        && command -v gcov >/dev/null 2>&1; then
    t="${CVC_BUILD_DIR}/smoke"
    rm -rf "$t"
    mkdir -p "$t"
    printf 'int f(int x) { return x > 0 ? x : -x; }\nint main(void) { return f(1) - 1; }\n' > "$t/s.c"
    (cd "$t" && gcc --coverage -O0 -o s s.c && ./s)
    run lcov --capture --directory "$t" --output-file "$t/s.info" 2>&1 | tee "$t/capture.log"
    if grep -q 'JSON::PP' "$t/capture.log"; then
        echo "cvcpkg: error: geninfo fell back to JSON::PP; Cpanel::JSON::XS did not load" >&2
        exit 1
    fi
    grep -q '^SF:.*s\.c$' "$t/s.info"
    run genhtml "$t/s.info" --output-directory "$t/html"
    [[ -f "$t/html/index.html" ]]
    echo "cvcpkg: lcov capture + genhtml smoke test passed"
fi
