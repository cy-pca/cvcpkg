#!/usr/bin/env bash
# recipes/_common/build-python.sh — shared CPython build logic.
#
# Sourced by recipes/python3{11,12,13}/build.sh after they export:
#   PYTHON_VERSION   e.g. "3.12.10"
#   PYTHON_MINOR     e.g. "3.12"
#
# Required cvcpkg env vars (set by builder):
#   CVC_INSTALL_DIR, CVC_SOURCE_DIR, CVC_BUILD_DIR (unused — CPython uses
#   in-source build), CVC_DEPS_PREFIX, CVC_JOBS, CVC_LINK, CVC_PLATFORM.
set -euo pipefail

: "${CVC_INSTALL_DIR:?}"
: "${CVC_SOURCE_DIR:?}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"
: "${CVC_DEPS_PREFIX:?CVC_DEPS_PREFIX must be set to the cvcpkg deps prefix}"
: "${PYTHON_VERSION:?PYTHON_VERSION must be exported before sourcing this script}"
: "${PYTHON_MINOR:?PYTHON_MINOR must be exported before sourcing this script}"
# PYTHON_LDVERSION — ABI suffix for the binary/lib/include names.
# Defaults to PYTHON_MINOR; free-threaded builds set this to e.g. "3.13t".
: "${PYTHON_LDVERSION:=${PYTHON_MINOR}}"
# PYTHON_DISABLE_GIL — set to "1" to pass --disable-gil (free-threaded build).
: "${PYTHON_DISABLE_GIL:=0}"

cd "${CVC_SOURCE_DIR}"

# Use gmake on BSDs (CPython's Makefile is GNU make).
MAKE=make
if command -v gmake >/dev/null 2>&1; then
    MAKE=gmake
fi

# --- Detect cross-compile targets ---
# wasm / wasi / cosmo: static builds with a cross host triple.
# Native platforms (linux, macos, freebsd, openbsd, netbsd, windows) use
# the native toolchain with shared libraries.
# NOTE: must be declared before the RPATH/flags section below which
# gates LDFLAGS on IS_CROSS — set -u would error if IS_CROSS were unset.
IS_CROSS=false
CROSS_HOST=""
case "${CVC_PLATFORM}" in
    wasm|wasm-mt) IS_CROSS=true; CROSS_HOST="wasm32-emscripten" ;;
    wasi)   IS_CROSS=true; CROSS_HOST="wasm32-wasi" ;;
    cosmo)  IS_CROSS=true; CROSS_HOST="x86_64-cosmo" ;;
esac

# Both emscripten flavors — plain `wasm` (single-threaded) and `wasm-mt`
# (-pthread / SharedArrayBuffer) — share the SAME emscripten toolchain wrappers,
# cross host triple (wasm32-emscripten), native build-python helper, config.site
# and TLS-less module set. Only the -pthread flags differ, and env-wasm.sh
# injects those via CVC_WASM_THREADS. Gate every emscripten-specific branch on
# this rather than on the literal `wasm` platform, so wasm-mt takes the same path.
IS_EMSCRIPTEN=false
case "${CVC_PLATFORM}" in wasm|wasm-mt) IS_EMSCRIPTEN=true ;; esac

# --- RPATH ---
# Embed $ORIGIN/../lib so the installed python3.X binary finds:
#   • libpython3.X.so  (installed alongside it in lib/)
#   • libssl, libz, libffi, libsqlite3, etc. (merged into same prefix)
case "${CVC_PLATFORM}" in
    macos)
        RPATH_SELF="@loader_path/../lib"
        RPATH_DEPS="@loader_path/../lib"
        ;;
    *)
        RPATH_SELF="\$ORIGIN/../lib"
        RPATH_DEPS="\$ORIGIN/../lib"
        ;;
esac

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
if [ "$IS_CROSS" = false ]; then
    export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib -Wl,-rpath,${RPATH_SELF} ${LDFLAGS:-}"
fi

# CPython bundles its own expat (Modules/expat) and finds every real dependency
# in CVC_DEPS_PREFIX (via CPPFLAGS above / --with-openssl / pkg-config).  On the
# BSDs env-<platform>.sh adds -I/usr/local/include so recipes that link ports
# libraries can find their headers — but that path also carries a SYSTEM expat.h
# (e.g. FreeBSD's expat-2.8.1), and it precedes -I./Modules/expat in CPython's
# compile line.  pyexpat.c then compiles against the system header while linking
# the bundled libexpat.a, and the resulting pyexpat.so cannot be imported:
#   ImportError: ... pyexpat...: Undefined symbol "XML_ParserCreate_MM"
# which cascades into ensurepip and fails the whole build (freebsd-build on the
# dev cluster).  Strip /usr/local/include from CPython's OWN compile flags on the
# native BSDs — nothing CPython builds needs a ports header, its deps are all in
# CVC_DEPS_PREFIX, and dropping it lets the bundled expat win.  Verified on
# freebsd-build: `import pyexpat` then loads the bundled expat_2.7.1.
case "${CVC_PLATFORM}" in
    freebsd|openbsd|netbsd)
        export CFLAGS="${CFLAGS//-I\/usr\/local\/include/}"
        export CXXFLAGS="${CXXFLAGS//-I\/usr\/local\/include/}"
        ;;
esac

# On macOS ensure the deployment target is propagated.
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-13.0}"
fi

# --- Configure flags ---
CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --enable-shared
    # pip is included in the stdlib; ensurepip bootstraps it at build time.
    --with-ensurepip=upgrade
    # Install to versioned paths: lib/python3.X/, bin/python3.X, etc.
    # Multiple Python minor versions coexist in the same prefix this way.
    --enable-ipv6
)

# OpenSSL for ssl/hashlib — the cvcpkg OpenSSL (openssldir=/etc/ssl, so CA
# verification uses the host trust store). SKIPPED on PLAIN wasm only: its OpenSSL
# is built no-threads (no pthreads), so it does not define OPENSSL_THREADS, and
# CPython's _ssl/_hashlib hard-error against a non-thread-safe OpenSSL. Native,
# wasi, cosmo — AND wasm-mt, whose OpenSSL is -pthread thread-safe — get it.
if [ "${CVC_PLATFORM}" != "wasm" ]; then
    CONFIGURE_ARGS+=(--with-openssl="${CVC_DEPS_PREFIX}" --with-ssl-default-suites=openssl)
fi
# wasm-mt: the emscripten config.site disables _ssl/_hashlib by default (written
# for the plain-wasm no-TLS case). Now that a thread-safe wasm-mt OpenSSL exists,
# force the modules back on — a command-line py_cv_module_* assignment overrides
# the config.site's cached value. Gives the threaded browser/node CPython real TLS.
if [ "${CVC_PLATFORM}" = "wasm-mt" ]; then
    CONFIGURE_ARGS+=(py_cv_module__ssl=yes py_cv_module__hashlib=yes)
fi

# Cross-compilation targets: static-only, explicit host, no readline.
if [ "$IS_CROSS" = true ]; then
    # CPython's configure REQUIRES an explicit --build when cross-compiling
    # ("configure: error: Cross compiling required --host=HOST-TUPLE and
    # --build=ARCH"), even though it detects the build type — supply the build
    # triple from the tree's config.guess (native host, unaffected by CC=emcc).
    _build_triple="$(./config.guess)"
    CONFIGURE_ARGS+=(--disable-shared --host="${CROSS_HOST}" --build="${_build_triple}")
    # readline/ncurses not available on wasm/wasi/cosmo. NOTE: --with-readline
    # only accepts editline|readline|no; the old "tkinter" value is invalid and
    # CPython 3.12's configure rejects it ("proper usage is --with(out)-readline
    # [=editline|readline|no]") — only reached once the earlier cross gates pass.
    CONFIGURE_ARGS+=(--without-readline)

    # Emscripten needs more than a host triple. CPython's cross-build wants:
    #   (a) a NATIVE interpreter of the SAME version to run build-time scripts
    #       (--with-build-python), since the cross python can't run on the host;
    #   (b) a config.site of ac_cv_* answers for the feature checks configure
    #       cannot run (a wasm conftest binary won't execute on the build host).
    # CC/CXX/CFLAGS are emcc/emscripten here (env-wasm), so build the native
    # helper in a side dir with the native toolchain and the emscripten flags
    # stripped.
    if [ "$IS_EMSCRIPTEN" = true ]; then
        _NATIVE_PY="${CVC_SOURCE_DIR}/cross-build/build"
        if [ ! -x "${_NATIVE_PY}/python" ]; then
            echo "build-python(wasm): building a full native ${PYTHON_MINOR} build-python for --with-build-python"
            mkdir -p "${_NATIVE_PY}"
            # A FULL native build (not just the `python` target): the cross
            # install runs this interpreter for compileall, so it needs its
            # extension modules (math, etc.) — `make python` alone omits them
            # ("ModuleNotFoundError: No module named 'math'").
            ( cd "${_NATIVE_PY}" && \
              env -u CFLAGS -u CXXFLAGS -u CPPFLAGS -u LDFLAGS -u PKG_CONFIG_PATH \
                  CC=cc CXX=c++ "${CVC_SOURCE_DIR}/configure" && \
              env -u CFLAGS -u CXXFLAGS -u CPPFLAGS -u LDFLAGS \
                  CC=cc CXX=c++ ${MAKE} -j"${CVC_JOBS}" )
        fi
        CONFIGURE_ARGS+=(--with-build-python="${_NATIVE_PY}/python")
        _cfg_site="${CVC_SOURCE_DIR}/Tools/wasm/config.site-wasm32-emscripten"
        [ -f "${_cfg_site}" ] && export CONFIG_SITE="${_cfg_site}"
    fi
else
    # Use the wide-char ncurses (libncursesw) for curses + readline.
    CONFIGURE_ARGS+=(--with-readline=readline)
fi

# Enable PGO on platforms where it's supported and reliable.
# Disabled on BSD and cross-compile variants.
case "${CVC_PLATFORM}" in
    linux)
        CONFIGURE_ARGS+=(--enable-optimizations)
        ;;
    macos)
        CONFIGURE_ARGS+=(--enable-optimizations)
        ;;
esac

# Free-threaded (no-GIL) build.
if [ "${PYTHON_DISABLE_GIL}" = "1" ]; then
    CONFIGURE_ARGS+=(--disable-gil)
fi

# [wasm] Disable _decimal. CPython's emscripten build archives _decimal.o into
# the static libpython3.X.a but does NOT archive its bundled libmpdec objects
# (Modules/_decimal/libmpdec/*.o), so libpython carries undefined mpd_* symbols
# (mpd_isspecial, mpd_version, ...) that break ANY final wasm app embedding it
# (e.g. VolRover / the vtk-python-cp312 import harness — wasm-ld: undefined
# symbol: mpd_isspecial). `decimal` is unused for the graphics/scene use case, so
# disable it via CPython's own module-state knob (the same mechanism its wasm
# config.site uses for unsupported modules) to keep libpython self-contained.
# Keeping decimal would instead require archiving the bundled libmpdec — a
# follow-up if the module is ever needed in the browser.
case "${CVC_PLATFORM}" in
    wasm|wasm-mt)
        CONFIGURE_ARGS+=(py_cv_module__decimal=n/a)
        ;;
esac

# wasm builds must go through the emscripten compiler wrappers
# (emconfigure/emmake), which put emcc/em++ on CC/CXX. env-wasm.sh only prepares
# the emsdk PATH (so cmake toolchain files resolve) — it does NOT set CC=emcc, so
# a bare ./configure builds CPython with native cc and dies compiling
# Python/emscripten_signal.c (emscripten.h not found). wasi/cosmo set CC via their
# own env, so they configure/make bare.
_EMWRAP=""
[ "$IS_EMSCRIPTEN" = true ] && _EMWRAP="emmake"
if [ "$IS_EMSCRIPTEN" = true ]; then
    emconfigure ./configure "${CONFIGURE_ARGS[@]}"
else
    ./configure "${CONFIGURE_ARGS[@]}"
fi

${_EMWRAP} $MAKE -j "${CVC_JOBS}"
${_EMWRAP} $MAKE install

# [wasm] Fold the bundled HACL crypto objects into the static libpython. Same class
# of gap as _decimal/libmpdec: CPython's emscripten build archives the hash MODULE
# objects (sha2module.o, sha3module.o, ...) into libpython3.X.a but NOT the HACL
# primitives they call (Modules/_hacl/*.o), so libpython carries undefined
# python_hashlib_Hacl_* symbols that break ANY static app embedding it (e.g. the
# pycvc_gl wasm host). Archive them so the built-in hash modules are self-contained
# (CPython builds in-source, so the objects sit under CVC_SOURCE_DIR/Modules/_hacl).
if [ "$IS_EMSCRIPTEN" = true ]; then
    _libpy="$(find "${CVC_INSTALL_DIR}" -name "libpython${PYTHON_MINOR}*.a" 2>/dev/null | head -1)"
    _hacl_objs="$(find "${CVC_SOURCE_DIR}/Modules/_hacl" -name '*.o' 2>/dev/null || true)"
    if [ -n "${_libpy}" ] && [ -n "${_hacl_objs}" ]; then
        # shellcheck disable=SC2086
        emar rs "${_libpy}" ${_hacl_objs}
        echo "build-python(wasm): archived $(printf '%s\n' ${_hacl_objs} | wc -l) HACL object(s) into $(basename "${_libpy}")"
    else
        echo "build-python(wasm): WARN — HACL objects or libpython not found; _sha2/_sha3 may leave undefined symbols" >&2
    fi
fi

# --- Relocatable RPATH post-fixup (native only) ---
# CPython's Makefile bakes the absolute build-time LDFLAGS rpath; patch
# the installed binary so it uses $ORIGIN-relative paths instead.
if [ "$IS_CROSS" = false ]; then
    PY_BIN="${CVC_INSTALL_DIR}/bin/python${PYTHON_LDVERSION}"
    if [[ "${CVC_PLATFORM}" != "macos" ]]; then
        # ELF (Linux/BSD): overwrite the rpath with an $ORIGIN-relative path so
        # the install is relocatable. CPython's Makefile otherwise bakes a
        # make-MANGLED rpath — the $O in $ORIGIN is a make variable, so it
        # expands to a broken "RIGIN/../lib" (RUNPATH). patchelf writes literal
        # bytes and sidesteps make/shell $ORIGIN escaping entirely, so it is a
        # REQUIRED build dependency here (declared per-recipe for linux/*bsd).
        if ! command -v patchelf >/dev/null 2>&1; then
            echo "build-python.sh: patchelf required on ${CVC_PLATFORM} but not found on PATH" >&2
            exit 1
        fi
        patchelf --set-rpath '$ORIGIN/../lib' "${PY_BIN}"
        # Extension modules (lib/pythonX.Y/**): point each back at the prefix
        # lib/ (where libssl/libcrypto/libffi/... live). The depth varies —
        # stdlib C extensions sit at lib/pythonX.Y/lib-dynload/ (needs
        # $ORIGIN/../..) while site-packages/<pkg>/**.so are deeper — so a single
        # fixed relative path is WRONG for lib-dynload (a uniform
        # $ORIGIN/../../.. lands on the prefix ROOT, not lib/, so _ssl.so then
        # loads the system libcrypto -> "OPENSSL_x.y.z not found"). Compute the
        # correct $ORIGIN-relative path to lib/ per file. (cvcpkg's own relocation
        # pass later PREPENDS $ORIGIN and preserves these $ORIGIN-relative entries.)
        # The FREE-THREADED build installs its stdlib under the LDVERSION name
        # (lib/python3.13t/), not lib/python3.13/ — so keying this off
        # PYTHON_MINOR pointed `find` at a directory that does not exist. Under
        # `set -o pipefail` that non-zero find failed the whole build, which is
        # why python313t could not be rebuilt (and cascade-cancelled every
        # -cp313t consumer); when it did not fail, the extension rpaths were
        # simply never patched. Resolve the real directory, and guard with -d so
        # a layout we do not expect degrades to "nothing to patch" instead of
        # killing the build.
        _STDLIB_DIR="${CVC_INSTALL_DIR}/lib/python${PYTHON_LDVERSION}"
        [ -d "${_STDLIB_DIR}" ] || _STDLIB_DIR="${CVC_INSTALL_DIR}/lib/python${PYTHON_MINOR}"
        # `realpath --relative-to` is GNU coreutils only; the BSD realpath in
        # every *BSD base rejects it ("realpath: unknown option -- -") and, under
        # set -e, fails the whole build after ensurepip has already run — the real
        # openbsd python blocker behind libffi. Probe once and fall back to a
        # lexical computation: every extension .so lives under CVC_INSTALL_DIR/lib,
        # so the path back to lib/ is one ".." per path segment below it.
        _HAVE_RELTO=0
        realpath --relative-to=/ / >/dev/null 2>&1 && _HAVE_RELTO=1
        if [ -d "${_STDLIB_DIR}" ]; then
            find "${_STDLIB_DIR}" -name '*.so' -print0 \
                | while IFS= read -r -d '' _so; do
                    if [ "${_HAVE_RELTO}" = 1 ]; then
                        _rel="$(realpath --relative-to="$(dirname "${_so}")" "${CVC_INSTALL_DIR}/lib")"
                    else
                        _sub="$(dirname "${_so}")"; _sub="${_sub#"${CVC_INSTALL_DIR}/lib/"}"
                        _rel=""; _oldifs="$IFS"; IFS='/'
                        for _seg in ${_sub}; do _rel="../${_rel}"; done
                        IFS="${_oldifs}"; _rel="${_rel%/}"; [ -n "${_rel}" ] || _rel="."
                    fi
                    patchelf --set-rpath "\$ORIGIN/${_rel}" "${_so}" 2>/dev/null || true
                  done
        else
            echo "build-python.sh: no stdlib dir under ${CVC_INSTALL_DIR}/lib — skipping rpath pass" >&2
        fi
    fi

    if [[ "${CVC_PLATFORM}" == "macos" ]]; then
        # Fix install_name on the framework-less shared build.
        DYLIB="${CVC_INSTALL_DIR}/lib/libpython${PYTHON_LDVERSION}.dylib"
        if [[ -f "$DYLIB" ]]; then
            install_name_tool -id "@rpath/libpython${PYTHON_LDVERSION}.dylib" "$DYLIB"
            install_name_tool -change \
                "${CVC_INSTALL_DIR}/lib/libpython${PYTHON_LDVERSION}.dylib" \
                "@rpath/libpython${PYTHON_LDVERSION}.dylib" \
                "${PY_BIN}" 2>/dev/null || true
        fi
    fi

    # Guarantee pip lands in the PACKAGE. --with-ensurepip=upgrade runs ensurepip during
    # `make install`, but it invokes `./python -E -m ensurepip`: -E ignores PYTHON* env vars but NOT
    # the user site (~/.local). On a builder whose account has a user-site pip, ensurepip resolves
    # THAT ("Requirement already satisfied: pip in /home/.../.local/...") and installs NOTHING into the
    # package — so python312 +cvc.11/+cvc.12 linux shipped with no site-packages/pip and
    # `python -m pip` -> "No module named pip". Re-run ensurepip ISOLATED from the user site (-s +
    # PYTHONNOUSERSITE) so it targets the package's own site-packages, and probe the same way (a bare
    # `import pip` would also leak the builder's ~/.local pip and hide the gap). Bundled wheel, no
    # network. Native only — this whole block is IS_CROSS=false (a cross PY_BIN can't run here).
    if ! PYTHONNOUSERSITE=1 "${PY_BIN}" -s -c 'import pip' >/dev/null 2>&1; then
        echo "build-python.sh: pip missing from the package — bootstrapping via ensurepip (isolated)"
        PYTHONNOUSERSITE=1 "${PY_BIN}" -s -m ensurepip --upgrade >/dev/null 2>&1 ||
            echo "build-python.sh: WARNING: ensurepip bootstrap failed" >&2
    fi
fi

# --- Alias hygiene: keep version-specific builds side-by-side safe ---
# Version-specific recipes must ship ONLY versioned binaries (python3.X,
# pip3.X, ...) so that several minor versions can be installed into the same
# prefix without fighting over the generic names. The generic
# python3/python/pip3/pip aliases are owned by the `python3` meta-recipe
# instead, which points them at a single default interpreter.
#
# CPython's `make install` and ensurepip create some of these generic
# aliases; strip them here so they never end up in the staged tree.
# (stage_bundle copies the whole install dir — package.files does not filter
# it — so removing the files here is what actually prevents the collision.)
cd "${CVC_INSTALL_DIR}/bin"
rm -f python3 python python3-config pip3 pip 2>/dev/null || true
if [ "${PYTHON_LDVERSION}" != "${PYTHON_MINOR}" ]; then
    # Free-threaded (t) build: keep a short pythonXt alias (e.g. python3t).
    MAJOR="${PYTHON_MINOR%%.*}"
    ln -sf "python${PYTHON_LDVERSION}" "python${MAJOR}t" 2>/dev/null || true
    # ensurepip names its console script pip3.X even in the free-threaded
    # build; give it the t-suffixed name (pip3.13t) so it can never collide
    # with the non-t interpreter's bin/pip3.X in a shared prefix. (Its
    # shebang already runs the t interpreter — ensurepip ran under it.)
    if [ -f "pip${PYTHON_MINOR}" ]; then
        mv -f "pip${PYTHON_MINOR}" "pip${PYTHON_LDVERSION}"
    fi
fi
