#!/usr/bin/env bash
# recipes/powershell/build.sh — download + stage PowerShell 7 (pwsh).
#
# pwsh ships as a self-contained .NET bundle (a directory of the pwsh binary
# plus its runtime assemblies). We download the host-matching archive, verify
# its sha256, extract it under lib/powershell/, and expose bin/pwsh.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

PWSH_VER="7.4.6"
BASE="https://github.com/PowerShell/PowerShell/releases/download/v${PWSH_VER}"

case "$(uname -m)" in
    x86_64|amd64)  ARCH="x64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *) echo "Unsupported host arch: $(uname -m)" >&2; exit 1 ;;
esac
case "$(uname -s)" in
    Linux)  OS="linux" ;;
    Darwin) OS="osx" ;;
    *) echo "Unsupported host OS: $(uname -s)" >&2; exit 1 ;;
esac

sha_for() {
    case "$1" in
        linux-x64)  echo "6f6015203c47806c5cc444c19d8ed019695e610fbd948154264bf9ca8e157561" ;;
        linux-arm64) echo "c0159b03e85f44ae1e7697818a011558da6c813d0aae848bf5ac13bf435d8624" ;;
        osx-x64)    echo "7a18daed105b7cfc80bf8cc00762fe7990105dd23f951cc32ceb744651650e3d" ;;
        osx-arm64)  echo "a482d668787ef98c37f0a5a7696107dffdb3dc340c5be3d1c153ec9d239072a8" ;;
        *) echo "" ;;
    esac
}

TARBALL="powershell-${PWSH_VER}-${OS}-${ARCH}.tar.gz"
ARCHIVE="${CVC_BUILD_DIR}/${TARBALL}"
EXPECTED="$(sha_for "${OS}-${ARCH}")"
[ -n "${EXPECTED}" ] || { echo "No pinned sha256 for ${OS}-${ARCH}" >&2; exit 1; }

echo "Downloading ${BASE}/${TARBALL}..."
curl -fSL -o "${ARCHIVE}" "${BASE}/${TARBALL}"

echo "Verifying sha256..."
if command -v sha256sum >/dev/null 2>&1; then
    echo "${EXPECTED}  ${ARCHIVE}" | sha256sum -c -
else
    echo "${EXPECTED}  ${ARCHIVE}" | shasum -a 256 -c -
fi

PWSH_DIR="${CVC_INSTALL_DIR}/lib/powershell"
mkdir -p "${PWSH_DIR}" "${CVC_INSTALL_DIR}/bin"
tar xf "${ARCHIVE}" -C "${PWSH_DIR}"
chmod +x "${PWSH_DIR}/pwsh"

# bin/pwsh is a launcher, NOT a symlink: .NET's apphost resolves pwsh.dll
# relative to the executable's directory, and cvcpkg stages install trees by
# copying (a symlink's target would be copied into bin/ without its runtime
# assemblies). exec the real pwsh so its bundled assemblies resolve.
cat > "${CVC_INSTALL_DIR}/bin/pwsh" <<'WRAP'
#!/bin/sh
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${here}/../lib/powershell/pwsh" "$@"
WRAP
chmod +x "${CVC_INSTALL_DIR}/bin/pwsh"
