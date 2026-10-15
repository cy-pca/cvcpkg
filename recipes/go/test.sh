#!/usr/bin/env bash
# recipes/go/test.sh — smoke test the staged Go toolchain.
#
#  1. go reports the pinned version, GOROOT is lib/go, GOTOOLCHAIN is local
#     and a go.mod asking for a newer Go fails instead of downloading one.
#  2. Relocatability: the staged tree is copied elsewhere; the copy's go must
#     report GOROOT inside the copy and build + test + run a small module.
# Everything Go writes (build cache, GOPATH, module cache) goes to a temp dir;
# GOENV=off keeps the builder account's `go env -w` file out of it.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
EXPECT="go1.27.2"

echo "-- go smoke test (${CVC_PLATFORM:-native}) --"

T="$(mktemp -d)"
trap 'chmod -R u+w "${T}" 2>/dev/null || true; rm -rf "${T}"' EXIT

gobin() {  # $1 = prefix
    if [ -f "$1/lib/go/bin/go.exe" ]; then echo "$1/lib/go/bin/go.exe"; else echo "$1/bin/go"; fi
}
unset GOROOT GOPATH GOFLAGS GOTOOLCHAIN GOBIN || true
export GOENV=off GOCACHE="${T}/gocache" GOPATH="${T}/gopath" GOMODCACHE="${T}/gomodcache"
export CGO_ENABLED=0 GOFLAGS=-modcacherw GOPROXY=off

GO="$(gobin "${CVC_INSTALL_DIR}")"
ver="$("${GO}" version)"
echo "  ${ver}"
case "${ver}" in *"${EXPECT} "*) ;; *) echo "FAIL: expected ${EXPECT}"; exit 1 ;; esac
[ "$("${GO}" env GOTOOLCHAIN)" = "local" ] || { echo "FAIL: GOTOOLCHAIN is not local"; exit 1; }
echo "  OK: GOTOOLCHAIN=local"

# A go.mod that wants a newer Go must fail loudly, not fetch go1.99.
mkdir -p "${T}/newer" && cd "${T}/newer"
printf 'module example.com/newer\n\ngo 1.99\n' > go.mod
printf 'package main\n\nfunc main() {}\n' > main.go
if out="$("${GO}" build ./... 2>&1)"; then
    echo "FAIL: building a go 1.99 module succeeded"; exit 1
fi
case "${out}" in
    *"requires go >= 1.99"*) echo "  OK: go 1.99 module refused (${out%%$'\n'*})" ;;
    *) echo "FAIL: unexpected error for a go 1.99 module: ${out}"; exit 1 ;;
esac

# ── relocate: copy the staged tree, test the copy from here on ───────
MOVED="${T}/relocated/prefix"
mkdir -p "${MOVED}"
# Hard links where the platform has them: same bytes, new paths, no 250 MB copy.
cp -al "${CVC_INSTALL_DIR}/." "${MOVED}/" 2>/dev/null || cp -pR "${CVC_INSTALL_DIR}/." "${MOVED}/"
GO="$(gobin "${MOVED}")"
want="$(cd "${MOVED}/lib/go" && pwd -P)"
got="$("${GO}" env GOROOT)"
got="$(cd "${got}" 2>/dev/null && pwd -P || echo "${got}")"
[ "${got}" = "${want}" ] || { echo "FAIL: relocated GOROOT is ${got}, expected ${want}"; exit 1; }
echo "  OK: relocated go reports GOROOT inside the copy"

mkdir -p "${T}/hello/greet" && cd "${T}/hello"
printf 'module example.com/hello\n\ngo 1.26\n' > go.mod
cat > greet/greet.go <<'EOF'
package greet

import "fmt"

func Hello(name string) string { return fmt.Sprintf("hello, %s", name) }
EOF
cat > greet/greet_test.go <<'EOF'
package greet

import "testing"

func TestHello(t *testing.T) {
	if got := Hello("cvcpkg"); got != "hello, cvcpkg" {
		t.Fatalf("Hello = %q", got)
	}
}
EOF
cat > main.go <<'EOF'
package main

import (
	"crypto/sha256"
	"fmt"
	"runtime"

	"example.com/hello/greet"
)

func main() {
	sum := sha256.Sum256([]byte("cvcpkg"))
	fmt.Printf("%s %x %s\n", greet.Hello("cvcpkg"), sum[:4], runtime.Version())
}
EOF
"${GO}" vet ./...
"${GO}" test ./... | sed 's/^/  /'
"${GO}" build -trimpath -o "${T}/hello-bin" .
out="$("${T}/hello-bin")"
echo "  ${out}"
case "${out}" in "hello, cvcpkg "*" ${EXPECT}") ;; *) echo "FAIL: unexpected program output"; exit 1 ;; esac
echo "  OK: vet + test + build + run with the relocated toolchain"

echo "-- go smoke test passed --"
