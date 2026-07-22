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

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --enable-shared \
    --enable-static \
    --with-quantum-depth=16 \
    --enable-hdri \
    --with-magick-plus-plus \
    --with-xml=yes \
    --without-perl \
    --without-x \
    --without-jpeg \
    --without-png \
    --without-webp \
    --without-jbig \
    --without-raw \
    --without-openjp2 \
    --disable-docs \
    CFLAGS="${CFLAGS:-"-O2 -fPIC"}" \
    CXXFLAGS="${CXXFLAGS:-"-O2 -fPIC -std=c++17"}"
$MAKE -j "${CVC_JOBS}"
$MAKE install

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
