#!/bin/sh
# Install build tools on FreeBSD 14.x
# Run via: incus exec freebsd-build -- sh setup-freebsd-tools.sh
set -e

pkg install -y \
    cmake git ninja pkgconf curl wget gmake \
    autoconf automake libtool \
    python3 py311-pip \
    llvm18 gcc14

echo "FreeBSD build tools installed successfully."
echo "Versions:"
cmake --version | head -1
git --version
ninja --version
gcc14 --version | head -1
clang --version | head -1
python3 --version
