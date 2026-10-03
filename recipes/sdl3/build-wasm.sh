#!/usr/bin/env bash
# recipes/sdl3/build-wasm.sh — cross-compile SDL3 to wasm via Emscripten.
#
# SDL3 has first-class Emscripten support: its CMake auto-detects EMSCRIPTEN and
# selects the browser-backed drivers (audio = Web Audio, camera = getUserMedia,
# joystick = Gamepad API, video = emscripten) while disabling the desktop
# X11 / Wayland / ALSA / PulseAudio backends. So NONE of the native recipe's
# X11/Wayland/audio dependencies apply here — they are all platforms:-scoped to
# linux/BSD — and SDL3 needs no wasm deps of its own.
#
# Built STATIC (there is no dlopen under emscripten) for the browser VolRover /
# pycvc_gl stack, where SDL is used PERIPHERALS-ONLY (SDL_INIT_AUDIO |
# SDL_INIT_GAMEPAD for audio / microphone / gamepad). VTK owns the WebGL canvas via
# vtkWebAssemblyRenderWindow, so SDL_INIT_VIDEO is unused and there is no GL/canvas
# contention. The whole static wasm closure must be a single flavor: env-wasm.sh
# prepends -pthread on the CVC_WASM_THREADS hook (the wasm-mt matrix entry), which
# SDL's CMake detects to enable SDL_PTHREADS for the threaded variant.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -DSDL_STATIC=ON \
    -DSDL_SHARED=OFF \
    -DSDL_TEST_LIBRARY=OFF \
    -DSDL_TESTS=OFF \
    -DSDL_EXAMPLES=OFF \
    -DSDL_INSTALL_TESTS=OFF
