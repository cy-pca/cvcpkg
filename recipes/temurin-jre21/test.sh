#!/usr/bin/env bash
# recipes/temurin-jre21/test.sh — smoke test the staged Temurin 21 JRE.
#
#  1. bin/java reports the pinned Temurin build; the module image, the
#     security providers and the bundled cacerts work (keytool).
#  2. Relocatability: the staged tree is copied to another path, and the
#     copy's bin/java must run and report java.home inside the copy.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
EXPECT_VER="21.0.12.1+1"
JH_REL="lib/jvm/temurin-jre21"

echo "-- temurin-jre21 smoke test (${CVC_PLATFORM:-native}) --"

T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT

exe() {  # $1 = prefix, $2 = tool; Windows runs the image's .exe directly
    if [ -f "$1/${JH_REL}/bin/$2.exe" ]; then echo "$1/${JH_REL}/bin/$2.exe"; else echo "$1/bin/$2"; fi
}

JAVA="$(exe "${CVC_INSTALL_DIR}" java)"
out="$("${JAVA}" -version 2>&1)"
echo "${out}" | sed 's/^/  /'
case "${out}" in
    *Temurin*"${EXPECT_VER}"*) echo "  OK: Temurin ${EXPECT_VER}" ;;
    *) echo "FAIL: expected a Temurin ${EXPECT_VER} runtime"; exit 1 ;;
esac

mods="$("${JAVA}" --list-modules)"
for m in java.base java.net.http java.sql jdk.crypto.ec jdk.httpserver; do
    echo "${mods}" | grep -q "^${m}@" || { echo "FAIL: module ${m} missing"; exit 1; }
done
echo "  OK: $(echo "${mods}" | wc -l | tr -d ' ') modules in the runtime image"

# keytool exercises the JCA providers and the bundled CA store.
n="$("$(exe "${CVC_INSTALL_DIR}" keytool)" -list -cacerts -storepass changeit 2>/dev/null \
      | grep -c 'trustedCertEntry' || true)"
[ "${n}" -gt 50 ] || { echo "FAIL: cacerts has ${n} trusted certificates"; exit 1; }
echo "  OK: keytool reads cacerts (${n} trusted certificates)"

# ── relocation ─────────────────────────────────────────────────────────
MOVED="${T}/somewhere/else"
mkdir -p "${MOVED}"
cp -pR "${CVC_INSTALL_DIR}/." "${MOVED}/"
MJAVA="$(exe "${MOVED}" java)"
home="$("${MJAVA}" -XshowSettings:properties -version 2>&1 | sed -n 's/^ *java\.home = //p')"
want="$(cd "${MOVED}/${JH_REL}" && pwd -P)"
got="$(cd "${home}" 2>/dev/null && pwd -P || echo "${home}")"
echo "  relocated java.home = ${home}"
[ "${got}" = "${want}" ] || { echo "FAIL: relocated java.home is ${home}, expected ${want}"; exit 1; }
echo "  OK: relocated bin/java runs and resolves java.home inside the copy"

echo "-- temurin-jre21 smoke test passed --"
