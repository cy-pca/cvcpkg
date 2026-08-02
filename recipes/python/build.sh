#!/usr/bin/env bash
# recipes/python/build.sh — create the generic `python` / `pip` aliases.
#
# This meta-recipe ships no upstream source. It creates the unversioned
# aliases as RELATIVE symlinks chaining through the `python3` meta's
# aliases (python3 -> python3.13, pip3 -> pip3.13), which is pulled in as a
# runtime dependency and staged into the same shared prefix. Because the
# links are relative, they resolve once python3 (and its python313) land in
# the consumer's prefix — and repointing the default in the python3 meta
# repoints `python` for free.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

mkdir -p "${CVC_INSTALL_DIR}/bin"
cd "${CVC_INSTALL_DIR}/bin"

ln -sf python3 python
ln -sf pip3    pip
