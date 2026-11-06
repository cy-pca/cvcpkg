#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# ImageMagick uses autotools. Build with Q16-HDRI (16-bit + high
# dynamic range) to match the existing bundle configuration.
cd "${CVC_SOURCE_DIR}"

# On BSDs, "make" is BSD make which can't parse ImageMagick's GNU
# Makefiles. Use gmake and tell configure/sub-makes about it.
if command -v gmake >/dev/null 2>&1; then
    MAKE=gmake
else
    MAKE=make
fi
export MAKE

# ── Force configure onto the cvcpkg libxml2, not the build host's system one ──
#
# ImageMagick REQUIRES libxml2 (it parses delegates.xml/type.xml at runtime), and
# its autotools build otherwise auto-detects the host's system libxml2 (which
# still ships the FTP client) — so MagickCore bakes in xmlNanoFTP* symbols that
# the cvcpkg libxml2 (2.12.9, FTP removed) does not export, and every consumer's
# link fails (libcvc: undefined reference to xmlNanoFTPClose@LIBXML2_2.4.30).
#
# _common/env only exports CMAKE_PREFIX_PATH, which autotools ignores, so expose
# the deps prefix to configure explicitly. The cvcpkg libxml2 bundle ships
# lib/pkgconfig/libxml-2.0.pc but NOT xml2-config, and ImageMagick's configure
# prefers xml2-config — so also drop in a shim that answers from that .pc via
# pkg-config. Together this pins libxml2 for BOTH detection paths (pkg-config and
# xml2-config).
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib/pkgconfig}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"

# Explicitly link the codecs that are only *private* pkg-config deps of the
# cvcpkg libwebp/libtiff (libwebp.pc: `Requires.private: libsharpyuv`;
# libtiff-4.pc: `Requires.private: ... Lerc libzstd`). MagickCore references
# SharpYuvInit / ZSTD_decompress / lerc_* DIRECTLY, but a dynamic
# `pkg-config --libs` (no --static) drops Requires.private, so configure only
# adds -lwebp / -ltiff and the final link fails on a HERMETIC builder with
# `undefined reference to SharpYuvInit / ZSTD_decompress` (this is what broke the
# cvc.9 debug fleet build). A dev box links only because its system libwebp still
# bundles sharpyuv. autotools appends $LIBS at the END of every link line, which
# is where these must go to satisfy the refs pulled in by -lMagickCore/-lwebp/
# -ltiff. (--copy-dt-needed-entries does NOT fix it — the symbols are referenced
# by MagickCore itself, not resolvable through a command-line lib's DT_NEEDED.)
export LIBS="-lsharpyuv -lzstd -lLerc ${LIBS:-}"

# xml2-config shim (only when pkg-config can resolve the cvcpkg libxml2). The
# critical platform is Linux — where the system libxml2 has the FTP client and
# pkg-config is always present — so this reliably pins it there; on a host with
# no pkg-config we skip the shim and let configure auto-detect (no regression).
if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists libxml-2.0; then
    echo "── pinning libxml2 to cvcpkg: $(pkg-config --modversion libxml-2.0) ($(pkg-config --variable=prefix libxml-2.0))"
    _xml2_shim_dir="${CVC_BUILD_DIR}/_xml2-config-shim"
    mkdir -p "${_xml2_shim_dir}"
    cat > "${_xml2_shim_dir}/xml2-config" <<'XML2CFG'
#!/usr/bin/env bash
# Shim: answer like libxml2's xml2-config, but from the cvcpkg libxml-2.0.pc, so
# ImageMagick builds against the cvcpkg (FTP-less) libxml2, not the host's.
set -euo pipefail
case "${1:-}" in
    --version) exec pkg-config --modversion libxml-2.0 ;;
    --cflags)  exec pkg-config --cflags     libxml-2.0 ;;
    --libs|--dynamic|--static) exec pkg-config --libs libxml-2.0 ;;
    --prefix)  exec pkg-config --variable=prefix libxml-2.0 ;;
    --exec-prefix) exec pkg-config --variable=exec_prefix libxml-2.0 ;;
    *) exec pkg-config --libs libxml-2.0 ;;
esac
XML2CFG
    chmod +x "${_xml2_shim_dir}/xml2-config"
    export PATH="${_xml2_shim_dir}:${PATH}"
    export XML2_CONFIG="${_xml2_shim_dir}/xml2-config"
else
    echo "── warning: pkg-config/libxml-2.0 not found in the deps prefix; letting" \
         "configure auto-detect libxml2 (may pick the host's FTP-enabled one)"
fi

# Every optional delegate configure enables by default and this recipe does not
# declare is switched off.  configure links whatever it can find, and a builder's
# shared populate prefix holds unrelated cvcpkg packages (cvc.24 picked up
# freetype and fontconfig that way, undeclared), so leaving these to
# auto-detection makes the bundle depend on what else happened to be built.
_im_undeclared_delegates=(
    --without-djvu --without-dmr --without-gvc --without-heic --without-jxl
    --without-lcms --without-lqr --without-openexr --without-pango
    --without-raqm --without-zip
)
# OpenMP: linux links GCC's libgomp, declared through gcc-runtime.  macOS stays
# without it, as it always built: Apple clang has no OpenMP, and any other clang
# would take cvcpkg's libomp (the openmp package) out of a shared populate
# prefix, undeclared.
_im_openmp=()
[ "${CVC_PLATFORM}" != "macos" ] || _im_openmp=(--disable-openmp)

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --enable-shared \
    --enable-static \
    --with-quantum-depth=16 \
    --enable-hdri \
    --with-magick-plus-plus \
    --with-xml=yes \
    --with-jpeg=yes \
    --with-png=yes \
    --with-webp=yes \
    --with-tiff=yes \
    --with-zstd=yes \
    --with-lzma=yes \
    --with-bzlib=yes \
    --with-freetype=yes \
    --with-fontconfig=yes \
    --without-perl \
    --without-x \
    --without-jbig \
    --without-raw \
    --without-openjp2 \
    "${_im_undeclared_delegates[@]}" \
    ${_im_openmp[@]+"${_im_openmp[@]}"} \
    --disable-docs \
    CFLAGS="${CFLAGS:-"-O2 -fPIC"}" \
    CXXFLAGS="${CXXFLAGS:-"-O2 -fPIC -std=c++17"}"

# freetype and fontconfig are declared (recipe.yaml), so their delegates must be
# on: --with-freetype=yes does not make configure fail when the library is not
# found, it just builds without text rendering.
for _delegate in FREETYPE FONTCONFIG; do
    if ! grep -Eq "^[[:space:]]*#[[:space:]]*define[[:space:]]+MAGICKCORE_${_delegate}_DELEGATE([[:space:]]|$)" \
            MagickCore/magick-baseconfig.h; then
        echo "imagemagick build.sh: configure did not enable the declared ${_delegate} delegate" >&2
        exit 1
    fi
done

$MAKE -j "${CVC_JOBS}"
$MAKE install

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
