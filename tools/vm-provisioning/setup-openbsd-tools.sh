#!/bin/sh
# Install build tools on OpenBSD 7.x
# Run via SSH: ssh root@<IP> < setup-openbsd-tools.sh
set -e

pkg_add -I \
    cmake git ninja pkgconf curl wget gmake \
    autoconf-2.72p0 automake-1.17 libtool \
    python-3.12.11 \
    gcc-11.2.0p15 llvm

echo "OpenBSD build tools installed successfully."
echo "Versions:"
cmake --version | head -1
git --version
ninja --version
egcc --version | head -1
clang --version | head -1
python3 --version
