#!/usr/bin/env bash
# recipes/datachannel-wasm/test.sh -- smoke test: the library, headers, JS libraries and CMake package.
set -euo pipefail
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "-- datachannel-wasm smoke test (${CVC_PLATFORM:-wasm}) --"

for f in lib/libdatachannel-wasm.a include/rtc/rtc.hpp include/rtc/peerconnection.hpp \
    include/rtc/datachannel.hpp share/datachannel-wasm/webrtc.js \
    share/datachannel-wasm/websocket.js lib/cmake/datachannel-wasm/datachannel-wasmConfig.cmake; do
    test -f "${CVC_INSTALL_DIR}/${f}" || { echo "FAIL: ${f} not found"; exit 1; }
done
grep -q 'RTCPeerConnection' "${CVC_INSTALL_DIR}/share/datachannel-wasm/webrtc.js" \
    || { echo "FAIL: webrtc.js does not use RTCPeerConnection"; exit 1; }

echo "-- datachannel-wasm smoke test passed --"
