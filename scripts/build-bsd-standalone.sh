#!/usr/bin/env bash
#
# Build the FreeBSD / OpenBSD / NetBSD standalone cvcpkg binaries for a release
# and (optionally) attach them to the matching GitHub Release.
#
# WHY THIS EXISTS
#   The standalone release pipeline (.github/workflows/cvcpkg-standalone.yml)
#   builds the BSD columns in its `build-bsd` job, which runs on a self-hosted
#   runner labelled `cvcpkg-builder` and SSHes to the BSD builder VMs. cy-pca
#   has no such runner yet, so `build-bsd` is gated off (vars.ENABLE_BSD_BUILDS)
#   and the BSDs are cut with this script from any host that can reach the VMs
#   over SSH (e.g. prettyhatemachine on the 10.66.77.x LAN). See
#   docs/bsd-standalone-builds.md. Once a cvcpkg-builder runner exists, set
#   ENABLE_BSD_BUILDS=true and the workflow does this automatically instead.
#
# USAGE
#   git checkout cvcpkg-vX.Y.Z         # build the exact released source
#   scripts/build-bsd-standalone.sh cvcpkg-vX.Y.Z            # build + upload
#   scripts/build-bsd-standalone.sh cvcpkg-vX.Y.Z --no-upload   # build only
#
# It builds from the CURRENT working tree (checkout the tag first). Binaries and
# checksums land in ./dist-bsd/. Requires: ssh/scp reachability to the VMs, and
# `gh` authed against cy-pca/cvcpkg for the upload.
#
# The per-VM steps mirror cvcpkg-standalone.yml build-bsd exactly (same
# PyInstaller version, the NetBSD bootloader patch, the TMPDIR quirks, and the
# `--version` + `validate` smoke test), so this stays a faithful stand-in.
set -euo pipefail

REPO="${CVCPKG_REPO:-cy-pca/cvcpkg}"
PYINSTALLER_VERSION="${PYINSTALLER_VERSION:-6.21.0}"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 -o BatchMode=yes"
OUT="$(pwd)/dist-bsd"

TAG="${1:-}"
UPLOAD=1
[ "${2:-}" = "--no-upload" ] && UPLOAD=0
[ "${1:-}" = "--no-upload" ] && { UPLOAD=0; TAG=""; }
if [ "$UPLOAD" = 1 ] && [ -z "$TAG" ]; then
  echo "usage: $0 <cvcpkg-vX.Y.Z> [--no-upload]" >&2; exit 2
fi

# platform | primary IP | fallback IP | python | 1=netbsd(patched pyinstaller)
# IPs mirror the FREEBSD/OPENBSD/NETBSD_BUILDER_IPS repo variables (primary
# first). Override the whole line via env if the VMs move.
TARGETS=(
  "cvcpkg-freebsd-x86_64|${FREEBSD_IPS:-10.66.77.174 10.66.77.154}|python3.11|/tmp|0"
  "cvcpkg-openbsd-x86_64|${OPENBSD_IPS:-10.66.77.197 10.66.77.235}|python3.12|/usr/local/tmp|0"
  "cvcpkg-netbsd-x86_64|${NETBSD_IPS:-10.66.77.214 10.66.77.167}|python3.13|/tmp|1"
)

reachable() { ssh $SSH_OPTS "root@$1" "command -v $2 >/dev/null && $2 -c 'import sys'" >/dev/null 2>&1; }

build_one() {
  local artifact="$1" ips="$2" python="$3" base_dir="$4" is_netbsd="$5"
  local host="" ip
  for ip in $ips; do reachable "$ip" "$python" && { host="$ip"; break; }; done
  [ -n "$host" ] || { echo "::error:: no usable $artifact VM in: $ips"; return 1; }
  local vm="root@${host}" rd="${base_dir}/cvcpkg-standalone-$(date +%s)-${artifact}"
  echo "### [$artifact] host=$host remote=$rd python=$python"

  ssh $SSH_OPTS "$vm" "mkdir -p ${rd}"
  tar cf - --exclude='.git' --exclude='build' --exclude='prefix' --exclude='dist' \
    --exclude='dist-bsd' --exclude='__pycache__' --exclude='*.egg-info' . \
    | ssh $SSH_OPTS "$vm" "tar xf - -C ${rd}"

  if [ "$is_netbsd" = "1" ]; then
    ssh $SSH_OPTS "$vm" "set -e; rm -rf /tmp/pyi-src; \
      git clone --quiet --branch v${PYINSTALLER_VERSION} --depth 1 \
        https://github.com/pyinstaller/pyinstaller /tmp/pyi-src; \
      cd /tmp/pyi-src && patch -p1 < ${rd}/recipes/pyinstaller-cp313/netbsd-platform-tables.patch; \
      TMPDIR=/usr/local/tmp ${python} -m pip install --break-system-packages . 2>/dev/null || ${python} -m pip install ."
  else
    ssh $SSH_OPTS "$vm" "set -e; \
      TMPDIR=/usr/local/tmp ${python} -m pip install --break-system-packages 'pyinstaller==${PYINSTALLER_VERSION}' 2>/dev/null \
        || ${python} -m pip install 'pyinstaller==${PYINSTALLER_VERSION}'"
  fi

  ssh $SSH_OPTS "$vm" "set -e; cd ${rd} && \
    TMPDIR=/usr/local/tmp ${python} -m pip install --break-system-packages . 2>/dev/null || ${python} -m pip install .; \
    ${python} -m PyInstaller --clean --noconfirm packaging/cvcpkg.spec"

  # Smoke test from a dir with no ./recipes so recipes+schemas resolve from _MEIPASS.
  ssh $SSH_OPTS "$vm" "set -e; cd ${rd} && chmod +x dist/cvcpkg && ./dist/cvcpkg --version && \
    BIN=\$(pwd)/dist/cvcpkg && cd /tmp && \$BIN validate recipes/zlib"

  mkdir -p "$OUT"
  scp $SSH_OPTS "${vm}:${rd}/dist/cvcpkg" "$OUT/$artifact"
  ( cd "$OUT" && sha256sum "$artifact" > "$artifact.sha256" )
  ssh $SSH_OPTS "$vm" "rm -rf ${rd}" || true
  echo "### [$artifact] built $(wc -c < "$OUT/$artifact") bytes"
}

for t in "${TARGETS[@]}"; do
  IFS='|' read -r artifact ips python base is_netbsd <<<"$t"
  build_one "$artifact" "$ips" "$python" "$base" "$is_netbsd"
done

if [ "$UPLOAD" = 1 ]; then
  echo "### uploading BSD binaries + checksums to $TAG"
  cd "$OUT"
  for a in cvcpkg-freebsd-x86_64 cvcpkg-openbsd-x86_64 cvcpkg-netbsd-x86_64; do
    # GitHub's asset upload 500s intermittently on large files; retry.
    for attempt in 1 2 3 4; do
      if gh release upload "$TAG" --repo "$REPO" --clobber "$a" "$a.sha256"; then break; fi
      echo "upload $a attempt $attempt failed; retrying"; sleep 5
    done
  done
  echo "### done"
fi
