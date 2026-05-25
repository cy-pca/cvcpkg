#!/bin/sh
# Install build tools on HaikuOS R1/beta5
#
# Run via SSH:  ssh user@<IP> < setup-haiku-tools.sh
# Or from Terminal inside Haiku.
#
# HaikuOS uses pkgman (HaikuPorts package manager).
# The default user is "user" (no password on fresh installs).

set -e

echo "=== Installing HaikuOS build tools ==="

# Update package repos
pkgman refresh

# Install development tools
# Note: gcc is part of haiku_devel, cmake/git/etc are separate packages
pkgman install -y \
    cmake \
    git \
    ninja \
    pkgconf \
    curl \
    wget \
    make \
    autoconf \
    automake \
    libtool \
    python3.11 \
    gcc \
    llvm18_clang

echo ""
echo "=== HaikuOS build tools installed ==="
echo "Versions:"
uname -a
cmake --version | head -1
git --version
ninja --version
gcc --version | head -1
python3 --version 2>&1 || python3.11 --version 2>&1
df -h /boot
