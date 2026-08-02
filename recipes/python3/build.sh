#!/usr/bin/env bash
# recipes/python3/build.sh — create the generic python3/pip3 aliases.
# The unversioned `python`/`pip` names are owned by the `python` meta on top.
#
# This meta-recipe ships no upstream source. It creates the generic
# interpreter and pip aliases as RELATIVE symlinks pointing at the default
# CPython (python313), which is pulled in as a runtime dependency and staged
# into the same shared prefix. Because the links are relative, they resolve
# once both this package and python313 land in the consumer's prefix.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

mkdir -p "${CVC_INSTALL_DIR}/bin"
cd "${CVC_INSTALL_DIR}/bin"

ln -sf python3.13        python3
ln -sf python3.13-config python3-config
ln -sf pip3.13           pip3
