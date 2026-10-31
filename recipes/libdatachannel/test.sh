#!/usr/bin/env bash
# recipes/libdatachannel/test.sh -- smoke test: headers, the library and its CMake package.
set -euo pipefail
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

echo "-- libdatachannel smoke test (${CVC_PLATFORM:-native}) --"

for h in rtc/rtc.hpp rtc/peerconnection.hpp rtc/datachannel.hpp rtc/version.h; do
    test -f "${CVC_INSTALL_DIR}/include/${h}" || { echo "FAIL: include/${h} not found"; exit 1; }
done
test -f "${CVC_INSTALL_DIR}/lib/cmake/LibDataChannel/LibDataChannelConfig.cmake" \
    || { echo "FAIL: the LibDataChannel CMake package not found"; exit 1; }
grep -q 'RTC_VERSION "0.24.6"' "${CVC_INSTALL_DIR}/include/rtc/version.h" \
    || { echo "FAIL: include/rtc/version.h is not 0.24.6"; exit 1; }

case "${CVC_PLATFORM:-}" in
windows)
    ls "${CVC_INSTALL_DIR}"/lib/datachannel*.lib >/dev/null 2>&1 \
        || { echo "FAIL: lib/datachannel*.lib not found"; exit 1; }
    ;;
*)
    ls "${CVC_INSTALL_DIR}"/lib/libdatachannel.* >/dev/null 2>&1 \
        || { echo "FAIL: lib/libdatachannel.* not found"; exit 1; }
    ;;
esac

echo "-- libdatachannel smoke test passed --"
