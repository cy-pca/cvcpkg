#!/usr/bin/env bash
# recipes/blender-src/build.sh -- stage Blender's upstream source tree at src/blender-src/
# (a source package: see docs/source-recipes.md).
set -euo pipefail
# shellcheck disable=SC1091
. "$(dirname "$0")/../_common/stage-source.sh"
cvc_stage_source
