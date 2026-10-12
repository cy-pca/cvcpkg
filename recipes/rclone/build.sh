#!/usr/bin/env bash
# recipes/rclone/build.sh — build bin/rclone from source with the cvcpkg Go.
#
# Mirrors upstream's release build (bin/cross-compile.go): CGO_ENABLED=0,
# `go build` of the module root, -trimpath, and
# -ldflags "-s -X github.com/rclone/rclone/fs.Version=<tag>". Module
# downloads go through GOPROXY into this job's build dir and are checked
# against go.sum (-mod=readonly). Afterwards every linked module's license
# files are collected into share/doc/rclone/third_party/, and the build fails
# if any of them is a license the project does not accept.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
: "${CVC_JOBS:=4}"

RCLONE_TAG="v1.75.2"

# The go build dependency is staged in the build prefix; prefer it over any
# go on the host PATH.
for d in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
    if [ -n "${d}" ] && [ -x "${d}/bin/go" ]; then
        export PATH="${d}/bin:${PATH}"
        break
    fi
done
command -v go >/dev/null 2>&1 || {
    echo "go not found in ${CVC_BUILD_PREFIX:-<unset>}/bin, ${CVC_DEPS_PREFIX:-<unset>}/bin or PATH (build dependency)" >&2
    exit 1
}

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

# The tarball must be the tag it claims to be: fs/versiontag.go carries it.
grep -q "VersionTag = \"${RCLONE_TAG}\"" "${CVC_SOURCE_DIR}/fs/versiontag.go" || {
    echo "fs/versiontag.go does not say ${RCLONE_TAG}" >&2; exit 1; }

# No blanket `go mod download`: go.mod's full graph includes test-only
# modules. `go build` fetches just the modules rclone's packages import,
# each checked against go.sum.
cd "${CVC_SOURCE_DIR}"
mkdir -p "${CVC_INSTALL_DIR}/bin"
nice go build -p "${CVC_JOBS}" \
    -ldflags "-s -X github.com/rclone/rclone/fs.Version=${RCLONE_TAG}" \
    -o "${CVC_INSTALL_DIR}/bin/rclone" .

# Upstream's generated man page (also what `rclone gendocs`/MANUAL.* hold).
install -d "${CVC_INSTALL_DIR}/share/man/man1"
install -m 0644 "${CVC_SOURCE_DIR}/rclone.1" "${CVC_INSTALL_DIR}/share/man/man1/rclone.1"

# ── licenses of everything linked into bin/rclone ────────────────────
DOC="${CVC_INSTALL_DIR}/share/doc/rclone"
TP="${DOC}/third_party"
mkdir -p "${TP}"
cp "${CVC_SOURCE_DIR}/COPYING" "${DOC}/LICENSE"
# `go version -m` lists exactly the modules the linker pulled in.
go version -m "${CVC_INSTALL_DIR}/bin/rclone" > "${DOC}/MODULES.txt"
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
for m in "${mods[@]}"; do
    path="${m% *}"; version="${m#* }"
    case "${path}" in
        ./*|../*|/*) dir="${CVC_SOURCE_DIR}/${path#./}" ;;   # local replace
        *) dir="${GOMODCACHE}/$(printf '%s' "${path}" | LC_ALL=C sed 's/[[:upper:]]/!&/g' | LC_ALL=C tr '[:upper:]' '[:lower:]')@${version}" ;;
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

# Owner policy: no AGPL / SSPL / BUSL / RSAL (or other source-available
# terms) anywhere in the binary.
if bad="$(grep -rlE 'GNU AFFERO GENERAL PUBLIC LICENSE|Server Side Public License|Business Source License|Redis Source Available License|Commons Clause|Elastic License|PolyForm' "${TP}")"; then
    echo "Disallowed license text in linked modules:" >&2
    echo "${bad}" >&2
    exit 1
fi
echo "License policy check OK (no AGPL/SSPL/BUSL/RSAL/source-available terms in the linked modules)"
# GPL-family copyleft must be a reviewed, listed exception (MPL-2.0 is
# file-level and expected; its text does not match this pattern).
# github.com/cloudsoda/sddl (the SMB backend, via cloudsoda/go-smb2) ships an
# LGPL-3.0 LICENSE file although its README says MIT; see recipe.yaml.
ALLOWED_GPL_FAMILY="github.com/cloudsoda/sddl"
unreviewed=""
while IFS= read -r f; do
    [ -n "${f}" ] || continue
    rel="${f#"${TP}/"}"
    mod="${rel%/*}"
    case " ${ALLOWED_GPL_FAMILY} " in
        *" ${mod} "*) echo "NOTE: ${mod}: GPL-family license file (reviewed exception)" ;;
        *) unreviewed="${unreviewed} ${rel}" ;;
    esac
done <<< "$(grep -rlE 'GNU (LESSER |LIBRARY )?GENERAL PUBLIC LICENSE' "${TP}" || true)"
if [ -n "${unreviewed}" ]; then
    echo "Unreviewed GPL-family license in linked modules:${unreviewed}" >&2
    exit 1
fi

# Drop the module and build caches now (GBs); nothing below needs them.
go clean -modcache
rm -rf "${GOCACHE}" "${GOTMPDIR}" "${GOPATH}"

"${CVC_INSTALL_DIR}/bin/rclone" version
