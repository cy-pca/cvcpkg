#!/usr/bin/env bash
# recipes/crate-git-xcb-imdkit--215ce4b0/build.sh -- stage xcb-imdkit 0.3.0 from
# wez/xcb-imdkit-rs@215ce4b08ac9, with its fcitx/xcb-imdkit submodule, as an entry
# of the cargo directory source share/cargo/crates/ (docs/rust-crates.md).
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

# deps/xcb-imdkit is a submodule, so the commit's archive leaves it empty.
# The gitlink at 215ce4b08ac9 pins fcitx/xcb-imdkit at this commit; sha256 taken
# after download.
SUB_COMMIT="30e2f16f9a8b0e338e25ce5e3643809a07ad41f0"
SUB_SHA256="96225fd06deb23321d161ed3eb4cdec09b4f183811b77b5b018af5caabced960"
sub="${CVC_BUILD_DIR}/xcb-imdkit-${SUB_COMMIT}.tar.gz"
curl -fSL --retry 3 -o "${sub}" "https://github.com/fcitx/xcb-imdkit/archive/${SUB_COMMIT}.tar.gz"
# Hash from stdin: a FILE operand with a backslash makes coreutils escape its output.
if command -v sha256sum >/dev/null 2>&1; then
    got="$(sha256sum < "${sub}" | awk '{print $1}')"
else
    got="$(shasum -a 256 < "${sub}" | awk '{print $1}')"
fi
[ "${got}" = "${SUB_SHA256}" ] \
    || { echo "sha256 mismatch for fcitx/xcb-imdkit@${SUB_COMMIT}: ${got}" >&2; exit 1; }

dest="${CVC_INSTALL_DIR}/share/cargo/crates/xcb-imdkit-0.3.0-git-215ce4b0"
mkdir -p "${dest}"
cp -R "${CVC_SOURCE_DIR}/." "${dest}/"
rm -rf "${dest}/deps/xcb-imdkit"
mkdir -p "${dest}/deps/xcb-imdkit"
# GNU tar reads "C:\..." (a Windows builder's paths) as host:path without this.
local_flag=""
tar --help 2>/dev/null | grep -q -- '--force-local' && local_flag="--force-local"
tar ${local_flag} -xzf "${sub}" -C "${dest}/deps/xcb-imdkit" --strip-components=1
[ -f "${dest}/deps/xcb-imdkit/CMakeLists.txt" ] \
    || { echo "the fcitx/xcb-imdkit archive did not unpack into deps/xcb-imdkit" >&2; exit 1; }
# A git source has no Cargo.lock checksum: "package" is null, as cargo vendor
# writes it for git dependencies.
printf '{"files":{},"package":null}\n' > "${dest}/.cargo-checksum.json"
