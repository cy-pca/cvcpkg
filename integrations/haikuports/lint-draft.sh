#!/usr/bin/env bash
# integrations/haikuports/lint-draft.sh — run HaikuPorts' OWN lint over a draft.
#
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC
#
# The counterpart of integrations/cpkg/cvcpkg.lua: the piece written in the
# foreign ecosystem's own terms.  cvcpkg's `haiku draft-recipe --lint` runs a
# *re-implementation* of HaikuPorts' rules; this runs the real thing, the same
# way haikuports' .github/workflows/lint.yml does — clone haikuporter, write a
# throwaway haikuports.conf, fetch the licence list, `haikuporter --lint`.
#
# It runs on Linux (upstream's own CI is ubuntu-24.04), so a draft can be made
# CI-green before a human ever looks at it.  It does NOT build anything: a
# lint-clean recipe says nothing about whether the port works, which is exactly
# why the HaikuPorts PR template asks the submitter to attest to a real build on
# a real Haiku machine.  Do not treat a green run here as that attestation.
#
# Usage:
#   integrations/haikuports/lint-draft.sh <haikuports-checkout> <port-name>
#   integrations/haikuports/lint-draft.sh --self-check
#
# Env:
#   HAIKUPORTER_DIR   existing haikuporter checkout (else cloned into a tmpdir)
#   TARGET_ARCH       default x86_64

set -euo pipefail

HAIKUPORTER_REPO="https://github.com/haikuports/haikuporter.git"
LICENSES_REPO="https://github.com/waddlesplash/haiku-licenses.git"
TARGET_ARCH="${TARGET_ARCH:-x86_64}"

die() { echo "lint-draft: $*" >&2; exit 1; }

# --self-check exercises only the parts that need no network, so this script is
# testable in the same offline way the rest of the integration is.
if [ "${1:-}" = "--self-check" ]; then
    command -v git >/dev/null || die "git not found"
    command -v python3 >/dev/null || die "python3 not found"
    echo "lint-draft: prerequisites present (git, python3)"
    exit 0
fi

[ $# -eq 2 ] || die "usage: $0 <haikuports-checkout> <port-name>"
PORTS_TREE="$(cd "$1" && pwd)"
PORT_NAME="$2"

[ -d "$PORTS_TREE" ] || die "not a directory: $PORTS_TREE"
command -v git >/dev/null || die "git not found"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ -n "${HAIKUPORTER_DIR:-}" ]; then
    HP="$HAIKUPORTER_DIR"
else
    HP="$WORK/haikuporter"
    git clone --depth=1 "$HAIKUPORTER_REPO" "$HP"
fi

git clone --depth=1 "$LICENSES_REPO" "$WORK/licenses"

cat > "$WORK/haikuports.conf" <<EOF
PACKAGER="cvcpkg draft lint <nobody@invalid>"
TREE_PATH="$PORTS_TREE"
TARGET_ARCHITECTURE="$TARGET_ARCH"
LICENSES_DIRECTORY="$WORK/licenses"
EOF

# Upstream's lint-new-recipes.sh fails on ANY trailing whitespace before it even
# reaches haikuporter, so check that first and report it the same way.
if git -C "$PORTS_TREE" ls-files -mo --exclude-standard '*.recipe' \
    | while read -r f; do grep -l '[[:blank:]]$' "$PORTS_TREE/$f" || true; done \
    | grep -q .; then
    die "trailing whitespace in a modified recipe (HaikuPorts CI fails on this)"
fi

echo "lint-draft: haikuporter --lint $PORT_NAME"
"$HP/haikuporter" --config="$WORK/haikuports.conf" --no-package-obsoletion \
    --lint "$PORT_NAME"

cat >&2 <<'EOF'

lint-draft: format checks passed.

That is ALL this proves.  It does not prove the port builds, and it is not the
"confirmed to build on your Haiku machine" attestation the HaikuPorts pull
request template asks for.  Build it with `haikuporter -S <port>` on a real
Haiku machine before you submit, and open the pull request yourself.
EOF
