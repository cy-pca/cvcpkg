#!/bin/sh
# Install build tools on NetBSD 10.x
# Run via SSH: ssh root@<IP> < setup-netbsd-tools.sh
# Or via expect with the serial console.
set -e

# Ensure sbin paths are in PATH (SSH login may not include them)
export PATH=/usr/sbin:/sbin:/usr/pkg/sbin:$PATH

# Set package path for pkg_add (used to bootstrap pkgin)
export PKG_PATH="https://cdn.NetBSD.org/pub/pkgsrc/packages/NetBSD/amd64/10.1/All"

# Install pkgin (binary package manager)
pkg_add pkgin || true

# Update package database
pkgin -y update

# Install build tools
pkgin -y install \
    cmake git ninja-build pkgconf curl wget gmake \
    autoconf automake libtool \
    python313 \
    gcc14 llvm clang

echo "NetBSD build tools installed successfully."
echo "Versions:"
cmake --version | head -1
git --version
ninja --version
gcc --version | head -1
clang --version | head -1
python3.13 --version
