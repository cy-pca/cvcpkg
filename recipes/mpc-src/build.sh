#!/usr/bin/env bash
# recipes/mpc-src/build.sh -- stage GNU MPC's upstream source tree at src/mpc-src/
# (a source package: see docs/source-recipes.md).
set -euo pipefail
# shellcheck disable=SC1091
. "$(dirname "$0")/../_common/stage-source.sh"
cvc_stage_source
