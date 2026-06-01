#!/usr/bin/env bash
set -euo pipefail

# Source files are bundled alongside this script in the recipe directory.
cp "$CVC_RECIPE_DIR/hello.c" "$CVC_SOURCE_DIR/"
cp "$CVC_RECIPE_DIR/hello.h" "$CVC_SOURCE_DIR/"

cd "$CVC_SOURCE_DIR"

# Build shared library
gcc -shared -fPIC -O2 -o libhello.so hello.c

# Install
mkdir -p "$CVC_INSTALL_DIR/lib" "$CVC_INSTALL_DIR/include"
cp libhello.so "$CVC_INSTALL_DIR/lib/"
cp hello.h "$CVC_INSTALL_DIR/include/"

echo "hello: build complete"
