#!/usr/bin/env bash
set -euo pipefail

# Source files are bundled alongside this script in the recipe directory.
cp "$CVC_RECIPE_DIR/greet.c" "$CVC_SOURCE_DIR/"
cp "$CVC_RECIPE_DIR/greet.h" "$CVC_SOURCE_DIR/"

cd "$CVC_SOURCE_DIR"

# Build shared library (self-contained — no link-time dep on hello)
gcc -shared -fPIC -O2 -o libgreet.so greet.c

# Install
mkdir -p "$CVC_INSTALL_DIR/lib" "$CVC_INSTALL_DIR/include"
cp libgreet.so "$CVC_INSTALL_DIR/lib/"
cp greet.h "$CVC_INSTALL_DIR/include/"

echo "greet: build complete"
