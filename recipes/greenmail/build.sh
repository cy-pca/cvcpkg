#!/usr/bin/env bash
# recipes/greenmail/build.sh — stage the GreenMail standalone jar + launchers.
#
# Noarch (platform: any): one jar from Maven Central, its license text, and
# two launcher scripts (sh and cmd), byte-identical everywhere. Like
# recipes/ca-bundle/build.sh this does NOT source _common/env-*.sh -- nothing
# is compiled, and an `any` job can land on any builder.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

GM_VER="2.1.14"
JAR_URL="https://repo1.maven.org/maven2/com/icegreen/greenmail-standalone/${GM_VER}/greenmail-standalone-${GM_VER}.jar"
JAR_SHA256="0381392f3a44e4d8ae78051778440f58820d01acaf5757c3f43649deda8c1d23"
# license.txt at tag release-2.1.14 (commit cdde8bb9...); the jar has none.
LIC_URL="https://raw.githubusercontent.com/greenmail-mail-test/greenmail/cdde8bb9488f11763df0737d69a892cf131b9445/license.txt"
LIC_SHA256="03cbe1314042730ac6eb76c992f7a32a1e126ba525fcf91cfe3047f255b8ada1"

sha256_of() {  # reads stdin so a Windows path with backslashes is never an operand
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum < "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 < "$1" | awk '{print $1}'
    elif command -v sha256 >/dev/null 2>&1; then
        sha256 -q < "$1"
    else
        echo "no sha256 tool found (tried sha256sum, shasum, sha256)" >&2
        return 1
    fi
}

fetch() {  # $1 = url, $2 = dest, $3 = expected sha256
    echo "Downloading $1..."
    curl -fSL --retry 3 -o "$2" "$1"
    local got
    got="$(sha256_of "$2")"
    if [ "${got}" != "$3" ]; then
        echo "sha256 mismatch for $1: expected $3, got ${got}" >&2
        exit 1
    fi
    echo "sha256 OK (${got})"
}

SHARE="${CVC_INSTALL_DIR}/share/greenmail"
DOC="${CVC_INSTALL_DIR}/share/doc/greenmail"
mkdir -p "${SHARE}" "${DOC}" "${CVC_INSTALL_DIR}/bin"

fetch "${JAR_URL}" "${SHARE}/greenmail-standalone.jar" "${JAR_SHA256}"
fetch "${LIC_URL}" "${DOC}/LICENSE" "${LIC_SHA256}"

# The bundled components' notices live in the jar's META-INF; copy them next
# to the license so they are visible without unpacking the jar.
PY="$(command -v python3 || command -v python || true)"
if [ -n "${PY}" ]; then
    "${PY}" -I - "${SHARE}/greenmail-standalone.jar" "${DOC}" <<'PY'
import os, sys, zipfile
jar, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(jar) as z:
    for n in z.namelist():
        base = os.path.basename(n)
        if n.startswith("META-INF/") and base and ("NOTICE" in base or "LICENSE" in base):
            with open(os.path.join(out, base), "wb") as f:
                f.write(z.read(n))
PY
elif command -v unzip >/dev/null 2>&1; then
    unzip -o -j -q "${SHARE}/greenmail-standalone.jar" 'META-INF/*NOTICE*' 'META-INF/*LICENSE*' -d "${DOC}"
else
    echo "need python3 or unzip to extract the jar's NOTICE files" >&2
    exit 1
fi

# ── bin/greenmail: the image's run_greenmail.sh as a relocatable launcher ──
cat > "${CVC_INSTALL_DIR}/bin/greenmail" <<'SH'
#!/bin/sh
# cvcpkg greenmail launcher: the greenmail/standalone Docker entrypoint
# (run_greenmail.sh) as a plain process. Same variables, same defaults.
self=$0
while [ -h "$self" ]; do
    link=$(readlink "$self")
    case $link in
        /*) self=$link ;;
        *) self=$(dirname -- "$self")/$link ;;
    esac
done
prefix=$(CDPATH= cd -- "$(dirname -- "$self")/.." && pwd)
jar=$prefix/share/greenmail/greenmail-standalone.jar

# Java: explicit override, then the cvcpkg JRE in this prefix, then JAVA_HOME,
# then PATH.
if [ -n "${GREENMAIL_JAVA:-}" ]; then
    java=$GREENMAIL_JAVA
elif [ -x "$prefix/lib/jvm/temurin-jre21/bin/java" ]; then
    java=$prefix/lib/jvm/temurin-jre21/bin/java
elif [ -x "$prefix/lib/jvm/temurin-jre21/bin/java.exe" ]; then
    java=$prefix/lib/jvm/temurin-jre21/bin/java.exe
elif [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/java" ]; then
    java=$JAVA_HOME/bin/java
elif command -v java >/dev/null 2>&1; then
    java=java
else
    echo "greenmail: no Java runtime found; install temurin-jre21 into this" \
         "prefix or set GREENMAIL_JAVA / JAVA_HOME" >&2
    exit 127
fi

# Defaults from greenmail-docker/standalone/Dockerfile, applied only when the
# variable is unset (an explicitly empty value stays empty, as with docker -e).
: "${JAVA_OPTS=-Djava.net.preferIPv4Stack=true}"
: "${GREENMAIL_OPTS=-Dgreenmail.setup.test.all -Dgreenmail.hostname=0.0.0.0 -Dgreenmail.auth.disabled}"

echo "Executing '$java $JAVA_OPTS $GREENMAIL_OPTS ${GREENMAIL_ADDITIONAL_OPTS:-} $* -jar $jar' ..." >&2
# Word splitting of the *_OPTS variables is intentional (run_greenmail.sh does
# the same); extra arguments are JVM options, so they go before -jar.
# shellcheck disable=SC2086
exec "$java" $JAVA_OPTS $GREENMAIL_OPTS ${GREENMAIL_ADDITIONAL_OPTS:-} "$@" -jar "$jar"
SH
chmod 0755 "${CVC_INSTALL_DIR}/bin/greenmail"

# ── bin/greenmail.cmd: the same for cmd.exe (CRLF line endings) ──
sed 's/$/\r/' > "${CVC_INSTALL_DIR}/bin/greenmail.cmd" <<'CMD'
@echo off
rem cvcpkg greenmail launcher: the greenmail/standalone Docker entrypoint as a process.
setlocal
set "GM_PREFIX=%~dp0.."
set "GM_JAR=%GM_PREFIX%\share\greenmail\greenmail-standalone.jar"
set "GM_JAVA=java"
if defined JAVA_HOME if exist "%JAVA_HOME%\bin\java.exe" set "GM_JAVA=%JAVA_HOME%\bin\java.exe"
if exist "%GM_PREFIX%\lib\jvm\temurin-jre21\bin\java.exe" set "GM_JAVA=%GM_PREFIX%\lib\jvm\temurin-jre21\bin\java.exe"
if defined GREENMAIL_JAVA set "GM_JAVA=%GREENMAIL_JAVA%"
if not defined JAVA_OPTS set "JAVA_OPTS=-Djava.net.preferIPv4Stack=true"
if not defined GREENMAIL_OPTS set "GREENMAIL_OPTS=-Dgreenmail.setup.test.all -Dgreenmail.hostname=0.0.0.0 -Dgreenmail.auth.disabled"
"%GM_JAVA%" %JAVA_OPTS% %GREENMAIL_OPTS% %GREENMAIL_ADDITIONAL_OPTS% %* -jar "%GM_JAR%"
exit /b %ERRORLEVEL%
CMD

sh -n "${CVC_INSTALL_DIR}/bin/greenmail"
echo "greenmail ${GM_VER} staged to ${CVC_INSTALL_DIR}"
