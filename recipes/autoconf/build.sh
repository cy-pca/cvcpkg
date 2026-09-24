#!/usr/bin/env bash
# recipes/autoconf/build.sh — build GNU Autoconf from source.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

./configure \
    --prefix="${CVC_INSTALL_DIR}"

make -j "${CVC_JOBS}"
make install

# ---------------------------------------------------------------------------
# Make the installed autoconf RELOCATABLE.
#
# autoconf's generated Perl tools (autoconf, autom4te, autoheader, ...) and
# share/autoconf/autom4te.cfg bake ${CVC_INSTALL_DIR} — the *ephemeral* cvcpkg
# build prefix — as the default location of their Perl modules, m4 macro tree,
# helper tools, trailer.m4 and m4 binary. cvcpkg unpacks the bundle into an
# arbitrary consumer prefix and does not rewrite these text files, and the build
# prefix is reaped the moment autoconf finishes building. So every consumer that
# later runs autoconf/autom4te on the fleet (e.g. automake's configure proving
# "whether autoconf works") dies with:
#     autom4te: error: m4sugar/m4sugar.m4: no such file or directory
#     autom4te: error: need GNU m4 1.4 or later: <gone>/bin/m4
# and configure reports "the installed version of autoconf does not work". A dev
# host only passes because its throwaway autoconf build dir happens to survive.
#
# Every baked default is honored-if-set through an env var EXCEPT autom4te.cfg's
# `--prepend-include`. So: (1) inject a BEGIN block into each tool that derives
# the real prefix from the tool's own path ($0) and fills in the tool env vars
# when unset; (2) teach autom4te to also search that relocated macro dir, so a
# stale absolute --prepend-include left in autom4te.cfg cannot defeat it. Both
# edits are idempotent (guarded by a "cvcpkg relocation" marker).
_acbin="${CVC_INSTALL_DIR}/bin"

# Perl uses $0 to find the install prefix (<prefix>/bin/<tool>); fill the env the
# autoconf tools already consult, without clobbering anything the caller set.
_reloc_begin='BEGIN {
  # cvcpkg relocation: autoconf was configured with an ephemeral build prefix
  # that no longer exists; derive the real prefix from this tool'"'"'s own path.
  my $self = $0; $self = "./$self" if $self !~ m{/};
  (my $prefix = $self) =~ s{/[^/]+/[^/]+$}{};
  my $acdir = "$prefix/share/autoconf";
  if (-d $acdir) {
    $ENV{"autom4te_perllibdir"} = $acdir                       unless exists $ENV{"autom4te_perllibdir"};
    $ENV{"AC_MACRODIR"}         = $acdir                       unless exists $ENV{"AC_MACRODIR"};
    $ENV{"AUTOM4TE_CFG"}        = "$acdir/autom4te.cfg"        unless exists $ENV{"AUTOM4TE_CFG"};
    $ENV{"trailer_m4"}          = "$acdir/autoconf/trailer.m4" unless exists $ENV{"trailer_m4"};
    $ENV{"AUTOM4TE"}   = "$prefix/bin/autom4te"   unless exists $ENV{"AUTOM4TE"};
    $ENV{"AUTOCONF"}   = "$prefix/bin/autoconf"   unless exists $ENV{"AUTOCONF"};
    $ENV{"AUTOHEADER"} = "$prefix/bin/autoheader" unless exists $ENV{"AUTOHEADER"};
    $ENV{"M4"} = "$prefix/bin/m4" if !exists $ENV{"M4"} && -x "$prefix/bin/m4";
  }
}'

_relfile="$(mktemp "${TMPDIR:-/tmp}/cvcpkg-autoconf-reloc.XXXXXX")"
printf '%s\n' "${_reloc_begin}" > "${_relfile}"

for _t in autoconf autoheader autom4te autoreconf autoscan autoupdate ifnames; do
    _f="${_acbin}/${_t}"
    [ -f "${_f}" ] || continue
    _RELFILE="${_relfile}" perl -0777 -i -pe '
        BEGIN { local $/; open my $fh, "<", $ENV{"_RELFILE"} or die $!; our $B = <$fh>; close $fh; }
        s/(\n    if 0;\n)/$1\n$B\n/ unless /cvcpkg relocation/;
    ' "${_f}"
done
rm -f "${_relfile}"

# autom4te: prepend the (now self-computed) macro dir to the include search path,
# so m4sugar/autoconf macros resolve even though autom4te.cfg still carries an
# ephemeral --prepend-include from build time.
perl -0777 -i -pe '
    my $anchor = q{  @include = grep { !/^\.$/ } uniq (reverse(@prepend_include), @include);};
    my $ins = "  # cvcpkg relocation: also search the relocated macro dir, since\n"
            . "  # autom4te.cfg may still name an ephemeral --prepend-include.\n"
            . "  unshift \@prepend_include, \$pkgdatadir\n"
            . "    if defined \$pkgdatadir && -d \$pkgdatadir\n"
            . "       && !grep { \$_ eq \$pkgdatadir } \@prepend_include;\n";
    s/\Q$anchor\E/$ins$anchor/ unless /cvcpkg relocation: also search/;
' "${_acbin}/autom4te"

# Fail loudly if the anchors ever move (a future autoconf bump) instead of
# silently shipping a broken, non-relocatable bundle again.
grep -q "cvcpkg relocation" "${_acbin}/autom4te" \
    || { echo "autoconf reloc: BEGIN injection missing from autom4te" >&2; exit 1; }
grep -q "cvcpkg relocation: also search" "${_acbin}/autom4te" \
    || { echo "autoconf reloc: include-path patch did not apply to autom4te" >&2; exit 1; }
