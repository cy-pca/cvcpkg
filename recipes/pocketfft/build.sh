#!/usr/bin/env bash
# pocketfft is header-only: there is nothing to compile, so this installs the
# single header and the licence rather than running a build system.
set -euo pipefail

: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

install -d "$CVC_INSTALL_DIR/include"
install -m 0644 "$CVC_SOURCE_DIR/pocketfft_hdronly.h" "$CVC_INSTALL_DIR/include/"

# Ship the licence beside the header: BSD-3 requires the copyright notice to
# travel with redistributions, and the notice lives inside the header itself
# plus LICENSE.md upstream.
install -d "$CVC_INSTALL_DIR/share/pocketfft"
for f in LICENSE.md LICENSE COPYING; do
  if [ -f "$CVC_SOURCE_DIR/$f" ]; then
    install -m 0644 "$CVC_SOURCE_DIR/$f" "$CVC_INSTALL_DIR/share/pocketfft/"
  fi
done

echo "cvcpkg: installed pocketfft_hdronly.h -> $CVC_INSTALL_DIR/include"
