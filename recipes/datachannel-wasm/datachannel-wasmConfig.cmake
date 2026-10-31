# datachannel-wasm's CMake package (written by the cvcpkg recipe; upstream installs none).
#
# datachannel-wasm::datachannel-wasm is the static library plus the two JS libraries that
# implement it over the browser's RTCPeerConnection and WebSocket: a consumer that links the
# target gets --js-library for both at its emcc link.
get_filename_component(_dcw_prefix "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

if(NOT TARGET datachannel-wasm::datachannel-wasm)
  add_library(datachannel-wasm::datachannel-wasm STATIC IMPORTED)
  set_target_properties(
    datachannel-wasm::datachannel-wasm
    PROPERTIES IMPORTED_LOCATION "${_dcw_prefix}/lib/libdatachannel-wasm.a"
               INTERFACE_INCLUDE_DIRECTORIES "${_dcw_prefix}/include"
               INTERFACE_COMPILE_FEATURES cxx_std_17
               INTERFACE_LINK_OPTIONS
               "SHELL:--js-library \"${_dcw_prefix}/share/datachannel-wasm/webrtc.js\";SHELL:--js-library \"${_dcw_prefix}/share/datachannel-wasm/websocket.js\"")
endif()

set(datachannel-wasm_VERSION 0.4.0)
unset(_dcw_prefix)
