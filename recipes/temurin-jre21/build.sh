#!/usr/bin/env bash
# recipes/temurin-jre21/build.sh — download and stage the Eclipse Temurin 21 JRE.
#
# Adoptium publishes prebuilt runtime images per OS/arch. We download the one
# matching the build host, verify its pinned sha256, stage the image under
# lib/jvm/temurin-jre21 (its JAVA_HOME) and link bin/java + bin/keytool into
# it. Nothing is compiled.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

JRE_TAG="jdk-21.0.12.1+1"          # upstream release tag
FILE_VER="21.0.12.1_1"             # the same, as spelled in the file names
BASE="https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1"
JAVA_HOME_REL="lib/jvm/temurin-jre21"

case "$(uname -m)" in
    x86_64|amd64)  ARCH="x64" ;;
    aarch64|arm64) ARCH="aarch64" ;;
    *) echo "Unsupported host arch: $(uname -m)" >&2; exit 1 ;;
esac
case "$(uname -s)" in
    Linux)  OS="linux" ;;
    Darwin) OS="mac" ;;
    *) echo "Unsupported host OS: $(uname -s)" >&2; exit 1 ;;
esac

# Adoptium's published <file>.sha256.txt values. Keep in step with
# recipe.yaml's source.artifacts and build.ps1's $expected.
sha_for() {
    case "$1" in
        x64-linux)     echo "2413149700df0f7d440500a84a8f764c535f21e5a5e87d38328b64eec2c5b500" ;;
        aarch64-linux) echo "14be1f35ebdbd1f6e8d57eb911a3ffb74d6d9aa255abc5daf2b1302002cf2cf2" ;;
        x64-mac)       echo "6717ec641fd9ce0bb209ca083ee23b42202ac68cb6fcc5753496e0e4a0f41989" ;;
        aarch64-mac)   echo "dec50fc6f9fcd4fe3ae8cabf5a5fa68f6afc48841f7698e468e9aa5d54beed84" ;;
        *) echo "" ;;
    esac
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        echo "no sha256 tool found (tried sha256sum, shasum)" >&2
        return 1
    fi
}

TARBALL="OpenJDK21U-jre_${ARCH}_${OS}_hotspot_${FILE_VER}.tar.gz"
ARCHIVE="${CVC_BUILD_DIR}/${TARBALL}"
EXPECTED="$(sha_for "${ARCH}-${OS}")"
[ -n "${EXPECTED}" ] || { echo "No pinned sha256 for ${ARCH}-${OS}" >&2; exit 1; }

echo "Downloading ${BASE}/${TARBALL}..."
curl -fSL --retry 3 -o "${ARCHIVE}" "${BASE}/${TARBALL}"

ACTUAL="$(sha256_of "${ARCHIVE}")"
if [ "${ACTUAL}" != "${EXPECTED}" ]; then
    echo "sha256 mismatch for ${TARBALL}: expected ${EXPECTED}, got ${ACTUAL}" >&2
    exit 1
fi
echo "sha256 OK (${ACTUAL})"

X="${CVC_BUILD_DIR}/extract"
rm -rf "${X}"; mkdir -p "${X}"
tar xzf "${ARCHIVE}" -C "${X}"
SRC="${X}/${JRE_TAG}-jre"
# macOS images are a .jre bundle; the runtime proper is Contents/Home. Stage
# that without the bundle wrapper so JAVA_HOME has one shape everywhere.
[ "${OS}" = "mac" ] && SRC="${SRC}/Contents/Home"
[ -x "${SRC}/bin/java" ] || { echo "no bin/java in ${SRC}" >&2; exit 1; }

JH="${CVC_INSTALL_DIR}/${JAVA_HOME_REL}"
rm -rf "${JH}"; mkdir -p "${JH}" "${CVC_INSTALL_DIR}/bin"
cp -pR "${SRC}/." "${JH}/"
# Upstream ships legal/ and a few other files read-only (0444); a later
# install, upgrade or rebuild into the same prefix would then fail to
# overwrite them. Nothing depends on those modes.
chmod -R u+w "${JH}"

# The image's lib/*.so NEED libjvm.so, which lives in lib/server/, while their
# RUNPATH is only $ORIGIN (=lib/). At run time that is fine -- the launcher has
# already loaded server/libjvm.so (SONAME libjvm.so) before any of them -- but
# cvcpkg's pack gate rightly sees a shipped library their RUNPATH cannot
# reach, and would otherwise need patchelf to add $ORIGIN/server to 24
# vendor binaries. A relative symlink lib/libjvm.so -> server/libjvm.so makes
# the shipped copy reachable without modifying any Adoptium binary.
if [ "${OS}" = "linux" ] && [ -f "${JH}/lib/server/libjvm.so" ] && [ ! -e "${JH}/lib/libjvm.so" ]; then
    ln -s server/libjvm.so "${JH}/lib/libjvm.so"
fi

# Relative symlinks: the java launcher resolves its own path to find
# java.home, so a link (unlike a copy) keeps working, and keeps the
# `readlink -f $(command -v java)` JAVA_HOME idiom honest.
for tool in java keytool; do
    ln -sfn "../${JAVA_HOME_REL}/bin/${tool}" "${CVC_INSTALL_DIR}/bin/${tool}"
done

# Prove the staged runtime starts on this host.
"${CVC_INSTALL_DIR}/bin/java" -version
