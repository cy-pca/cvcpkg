#!/usr/bin/env bash
# recipes/sionna-rt-src/build.sh -- stage Sionna RT 2.2.0's packaging files and
# src/ at src/sionna-rt-src/ (a source package: see docs/source-recipes.md).
# The archive's tutorials/ (24 MB of notebooks), doc/ and test/ play no part
# in the build, so they are left out.
set -euo pipefail
# shellcheck disable=SC1091
. "$(dirname "$0")/../_common/stage-source.sh"
cvc_stage_source pyproject.toml README.md LICENSE src
