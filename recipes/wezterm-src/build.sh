#!/usr/bin/env bash
# recipes/wezterm-src/build.sh -- stage WezTerm's upstream source tree at src/wezterm-src/
# (a source package: see docs/source-recipes.md).
set -euo pipefail
# shellcheck disable=SC1091
. "$(dirname "$0")/../_common/stage-source.sh"
cvc_stage_source
# The tree's one symlink, harfbuzz's README -> README.md, is documentation, and a
# symlink in a bundle is the one thing a Windows consumer may fail to extract.
rm -f "${CVC_INSTALL_DIR}/src/wezterm-src/deps/harfbuzz/harfbuzz/README"
