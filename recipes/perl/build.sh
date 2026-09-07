#!/usr/bin/env bash
# recipes/perl/build.sh — build Perl 5 from source via its own Configure.
#
# Perl uses a bespoke Configure script (not autotools). `-des` accepts all
# defaults non-interactively.
#
# -Duserelocatableinc is LOAD-BEARING and was missing. `-Dprefix` alone bakes
# ABSOLUTE @INC paths at configure time, and cvcpkg builds in a throwaway
# scratch dir, so the published bundle pointed its own core modules at a
# directory that ceased to exist the moment the build finished:
#
#     Can't locate strict.pm in @INC (@INC entries checked:
#       /tmp/cvcpkg-builder/cvcpkg-job-perl-vk_k0664/cvcpkg-perl-mf3qnc5w/install/lib/...)
#     BEGIN failed--compilation aborted at ./Configure line 13.
#
# i.e. the bundle could not run `perl -e 'use strict'` on any machine. That is
# why openssl (whose Configure IS a perl script) failed to build fleet-wide,
# which cascade-cancelled curl and log4cplus and left several variants
# unbuildable. The old comment claimed this install was relocatable; it was not.
#
# With -Duserelocatableinc, @INC entries are stored as ".../"-prefixed paths
# resolved relative to the perl binary at runtime, so the bundle works wherever
# cvcpkg unpacks it.
#
# BUT relocatableinc needs perl to know its OWN executable's path. On Linux/
# FreeBSD it reads that from /proc/self/exe or KERN_PROC_PATHNAME. OpenBSD, by
# design, exposes NO way for a process to find its own executable, so perl falls
# back to argv[0] — and cvcpkg rewrites script shebangs to `#!/usr/bin/env perl`,
# under which argv[0] is the bare string "perl" (no directory). The ".../" base
# then resolves to the CURRENT DIRECTORY, so @INC becomes ../lib/... and every
# perl script run from elsewhere dies with "Can't locate strict.pm in @INC"
# (breaking autoconf/autom4te → automake, openssl's Configure, etc. on OpenBSD).
# Fix below: wrap perl in a /bin/sh shim that re-execs the real interpreter by
# its ABSOLUTE path — the kernel hands a shebang script its real file path as $0
# even when env passed argv[0]="perl", so relocatableinc gets a real base again.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

sh ./Configure -des \
    -Dprefix="${CVC_INSTALL_DIR}" \
    -Duserelocatableinc \
    -Dusethreads \
    -Uversiononly \
    -Dman1dir=none \
    -Dman3dir=none

make -j "${CVC_JOBS}"
make install

# OpenBSD relocation shim (see the header). perl and perlX.Y.Z are the same real
# interpreter; keep ONE as perl.bin and replace the entry points with a /bin/sh
# shim that re-execs it by absolute path so -Duserelocatableinc has a real base.
if [ "$(uname -s)" = "OpenBSD" ]; then
    _bin="${CVC_INSTALL_DIR}/bin"
    _ver="$("${_bin}/perl" -e 'print $^V' 2>/dev/null | sed 's/^v//')"
    mv -f "${_bin}/perl" "${_bin}/perl.bin"
    for _w in perl "perl${_ver}"; do
        [ -n "${_w}" ] || continue
        rm -f "${_bin}/${_w}"
        cat > "${_bin}/${_w}" <<'SH'
#!/bin/sh
# cvcpkg OpenBSD relocation shim: OpenBSD cannot let a process discover its own
# executable, so `env perl` (argv[0]="perl") defeats -Duserelocatableinc and @INC
# resolves to the CWD. Re-exec the real perl by its absolute path — the kernel
# gives this shebang script its real file path as "$0" regardless of argv[0].
d=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$d/perl.bin" "$@"
SH
        chmod 0755 "${_bin}/${_w}"
    done
    # Sanity: the shimmed perl must load a core module from OUTSIDE its own dir.
    ( cd / && "${_bin}/perl" -e 'use strict; use warnings; print "reloc ok\n"' ) \
        || { echo "perl OpenBSD reloc shim FAILED self-test" >&2; exit 1; }
fi
