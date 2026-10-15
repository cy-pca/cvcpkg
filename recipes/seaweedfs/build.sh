#!/usr/bin/env bash
# recipes/seaweedfs/build.sh — build bin/weed from source with the cvcpkg Go.
#
# Mirrors upstream's release build (.github/workflows/binaries_release*.yml):
# CGO_ENABLED=0, `go build` in weed/, -s -w and the version.COMMIT stamp.
# Module downloads go through GOPROXY into this job's build dir and are
# checked against go.sum (-mod=readonly). Afterwards every linked module's
# license files are collected into share/doc/seaweedfs/third_party/, and the
# build fails if any of them is a license the project does not accept.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
: "${CVC_JOBS:=4}"

SW_COMMIT="530be3e37337488ecc34d58441e0bc476e121c93"   # tag 4.48

# The go build dependency is staged in the build prefix; prefer it over any
# go on the host PATH.
for d in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
    if [ -n "${d}" ] && [ -x "${d}/bin/go" ]; then
        export PATH="${d}/bin:${PATH}"
        break
    fi
done
command -v go >/dev/null 2>&1 || { echo "go not found (build dependency)" >&2; exit 1; }

unset GOROOT GOBIN GOFLAGS || true
export GOENV=off GOTOOLCHAIN=local CGO_ENABLED=0
export GOPATH="${CVC_BUILD_DIR}/gopath"
export GOMODCACHE="${CVC_BUILD_DIR}/gomodcache"
export GOCACHE="${CVC_BUILD_DIR}/gocache"
export GOTMPDIR="${CVC_BUILD_DIR}/gotmp"
export GOFLAGS="-mod=readonly -modcacherw -trimpath -buildvcs=false"
mkdir -p "${GOTMPDIR}"
go version
go env GOROOT GOPROXY GOSUMDB GOTOOLCHAIN

# No blanket `go mod download`: go.mod's full graph is ~3 GB of modules
# (test-only and optional backends included). `go build` fetches just the
# modules weed's packages import, each checked against go.sum.
cd "${CVC_SOURCE_DIR}/weed"
mkdir -p "${CVC_INSTALL_DIR}/bin"
nice go build -p "${CVC_JOBS}" \
    -ldflags "-s -w -X github.com/seaweedfs/seaweedfs/weed/util/version.COMMIT=${SW_COMMIT}" \
    -o "${CVC_INSTALL_DIR}/bin/weed" .

# ── licenses of everything linked into bin/weed ──────────────────────
DOC="${CVC_INSTALL_DIR}/share/doc/seaweedfs"
TP="${DOC}/third_party"
mkdir -p "${TP}"
cp "${CVC_SOURCE_DIR}/LICENSE" "${DOC}/LICENSE"
# `go version -m` lists exactly the modules the linker pulled in.
go version -m "${CVC_INSTALL_DIR}/bin/weed" > "${DOC}/MODULES.txt"
# Each "dep" line may be followed by a "=>" line naming its replacement; the
# replacement is what was compiled, so it is the one whose license counts.
mods=()
while read -r kind path version _; do
    case "${kind}" in
        dep) mods+=("${path} ${version}") ;;
        "=>") mods[${#mods[@]}-1]="${path} ${version}" ;;
    esac
done < <(sed 's/^[[:space:]]*//' "${DOC}/MODULES.txt")
missing=0
for m in ${mods[@]+"${mods[@]}"}; do
    path="${m% *}"; version="${m#* }"
    case "${path}" in
        ./*|../*|/*) dir="${CVC_SOURCE_DIR}/${path#./}" ;;   # local replace
        *) dir="${GOMODCACHE}/$(printf '%s' "${path}" | sed 's/[A-Z]/!&/g' | tr 'A-Z' 'a-z')@${version}" ;;
    esac
    if [ ! -d "${dir}" ]; then
        echo "module dir missing for ${path}@${version}" >&2; exit 1
    fi
    found=0
    for f in "${dir}"/LICENSE* "${dir}"/LICENCE* "${dir}"/COPYING* "${dir}"/NOTICE* "${dir}"/license* "${dir}"/UNLICENSE*; do
        [ -f "${f}" ] || continue
        mkdir -p "${TP}/${path}"
        cp "${f}" "${TP}/${path}/"
        found=1
    done
    if [ "${found}" = 0 ]; then
        echo "WARN: no license file at the root of ${path}@${version}" >&2
        missing=$((missing + 1))
    fi
done
echo "Collected $(find "${TP}" -type f | wc -l | tr -d ' ') license files for" \
     "${#mods[@]} linked modules (${missing} without a root license file)"

# Owner policy: no AGPL / SSPL / BUSL / RSAL anywhere in the binary.
if bad="$(grep -rlE 'GNU AFFERO GENERAL PUBLIC LICENSE|Server Side Public License|Business Source License|Redis Source Available License' "${TP}")"; then
    echo "Disallowed license text in linked modules:" >&2
    echo "${bad}" >&2
    exit 1
fi
echo "License policy check OK (no AGPL/SSPL/BUSL/RSAL in the linked modules)"

# Drop the module and build caches now (GBs); nothing below needs them.
go clean -modcache
rm -rf "${GOCACHE}" "${GOTMPDIR}"

"${CVC_INSTALL_DIR}/bin/weed" version
