# cvcpkg-wasm-app.cmake — generic CMake boilerplate for WebAssembly apps built
# against a cvcpkg wasm prefix.
#
# Ship it with your project (or vendor a copy) and include it from your
# CMakeLists.txt:
#
#   include(${CMAKE_SOURCE_DIR}/cmake/cvcpkg-wasm-app.cmake)
#
#   cvcpkg_wasm_deps("/tmp/wasm-app/deps")   # the cvcpkg wasm-mt deps prefix
#   cvcpkg_wasm_threads()                      # threaded (wasm-mt) app only
#
#   find_package(cvcGL REQUIRED)               # resolved via CMAKE_FIND_ROOT_PATH
#
#   add_executable(myapp main.cpp)
#   target_link_libraries(myapp PRIVATE cvcGL::cvcGL)
#   cvcpkg_wasm_app(myapp PTHREADS ON)          # opt into the browser-side fixes
#
# Every function is a NO-OP unless the build is running under Emscripten
# (CMAKE_TOOLCHAIN_FILE set to the Emscripten platform file, i.e. you
# configured with `emcmake cmake`), so the same CMakeLists.txt keeps working
# for native builds with no #if EMSCRIPTEN noise in your code.
#
# The functions
# ------------
#   cvcpkg_wasm_threads()
#       Add -pthread to CMAKE_C_FLAGS / CMAKE_CXX_FLAGS / CMAKE_EXE_LINKER_FLAGS
#       so every object the app compiles AND the final link are threaded. Idempotent
#       (a second call adds nothing) and a no-op outside Emscripten. Call it before
#       any target in the directory is created. This is the CMake-native equivalent
#       of what recipes/_common/env-wasm-mt.sh does with CFLAGS/CXXFLAGS/LDFLAGS —
#       the env-var exports do NOT reach `cmake`, so for a CMake app THIS is what
#       actually gets -pthread onto emcc's command line.
#
#   cvcpkg_wasm_deps([PREFIX ...])
#       Append a cvcpkg wasm deps prefix to CMAKE_FIND_ROOT_PATH so find_package()
#       / find_library() resolve inside it under Emscripten. With no argument it
#       falls back to $ENV{CVCPKG_WASM_DEPS}, then $ENV{CVC_WASM_DEPS}. It does NOT
#       set CMAKE_FIND_ROOT_PATH_MODE_* to ONLY (that would hide the Emscripten
#       sysroot the toolchain file relies on).
#
#   cvcpkg_wasm_app(<target> [ASYNCIFY ON|OFF] [MEMORY_GROWTH ON|OFF]
#                         [MIMALLOC AUTO|ON|OFF] [PTHREADS AUTO|ON|OFF]
#                         [PRE_JS <file> ...])
#       Opt <target> into the browser-side link fixes. No-op outside Emscripten.
#         ASYNCIFY       (default ON)    -sASYNCIFY=1 — needed for apps that yield
#                                       (emscripten_sleep, VTK's in-render yield).
#                                       Pass OFF for a hot loop that never yields.
#         MEMORY_GROWTH  (default ON)    -sALLOW_MEMORY_GROWTH=1.
#         MIMALLOC       (default AUTO)  -sMALLOC=mimalloc. AUTO = ON iff the target
#                                       links -pthread (a threaded app serialises
#                                       every malloc/free on dlmalloc's one global
#                                       lock, which threads contend on; mimalloc is
#                                       per-thread) and NEVER under -fsanitize=
#                                       address (emcc refuses the combination). A
#                                       pre-existing -sMALLOC= of its own is kept.
#         PTHREADS       (default AUTO)  ON iff the target already links -pthread.
#                                       AUTO re-detects from the link line; pass ON
#                                       to force (or rely on cvcpkg_wasm_threads()).
#         PRE_JS <file>  Additional --pre-js files (run before the wasm starts),
#                       each registered as a LINK_DEPENDS so a changed file
#                       re-links the app.
#
# Feature-test a keyword in a newer version:
#   if("PRE_JS" IN_LIST CVCPKG_WASM_APP_FEATURES)
set(CVCPKG_WASM_APP_FEATURES PTHREADS ASYNCIFY MEMORY_GROWTH MIMALLOC PRE_JS)
include_guard(GLOBAL)

# ── cvcpkg_wasm_threads() ─────────────────────────────────────────────
# Macro (not function) so it writes the caller's directory-scoped
# CMAKE_C_FLAGS / CMAKE_CXX_FLAGS / CMAKE_EXE_LINKER_FLAGS.
macro(cvcpkg_wasm_threads)
  if(EMSCRIPTEN)
    foreach(_cvw_var CMAKE_C_FLAGS CMAKE_CXX_FLAGS CMAKE_EXE_LINKER_FLAGS)
      # Skip the ones that already carry -pthread (idempotent).
      if(NOT "${${_cvw_var}}" MATCHES "(^|[ ;])-pthread( |$)")
        if(${_cvw_var})
          set(${_cvw_var} "${${_cvw_var}} -pthread")
        else()
          set(${_cvw_var} "-pthread")
        endif()
      endif()
    endforeach()
  endif()
endmacro()

# ── cvcpkg_wasm_deps() ────────────────────────────────────────────────
# Macro so it can append to the caller's CMAKE_FIND_ROOT_PATH.
macro(cvcpkg_wasm_deps)
  if(EMSCRIPTEN)
    set(_cvw_prefixes ${ARGV})
    if(NOT _cvw_prefixes)
      if(DEFINED ENV{CVCPKG_WASM_DEPS} AND EXISTS "$ENV{CVCPKG_WASM_DEPS}")
        set(_cvw_prefixes "$ENV{CVCPKG_WASM_DEPS}")
      elseif(DEFINED ENV{CVC_WASM_DEPS} AND EXISTS "$ENV{CVC_WASM_DEPS}")
        set(_cvw_prefixes "$ENV{CVC_WASM_DEPS}")
      endif()
    endif()
    if(NOT _cvw_prefixes)
      message(WARNING "cvcpkg_wasm_deps(): no prefix given and neither "
                      "CVCPKG_WASM_DEPS nor CVC_WASM_DEPS is set — "
                      "find_package() will fall back to the host system")
    else()
      foreach(_cvw_p IN LISTS _cvw_prefixes)
        if(NOT EXISTS "${_cvw_p}")
          message(WARNING "cvcpkg_wasm_deps(): prefix '${_cvw_p}' does not exist")
        else()
          # Append only. Do NOT set CMAKE_FIND_ROOT_PATH_MODE_* to ONLY: that
          # would hide the Emscripten sysroot the toolchain file needs.
          list(APPEND CMAKE_FIND_ROOT_PATH "${_cvw_p}")
          message(STATUS "cvcpkg_wasm_deps(): CMAKE_FIND_ROOT_PATH += ${_cvw_p}")
        endif()
      endforeach()
    endif()
  endif()
endmacro()

# ── cvcpkg_wasm_app() ─────────────────────────────────────────────────
function(cvcpkg_wasm_app target)
  if(NOT EMSCRIPTEN)
    return()
  endif()
  if(NOT TARGET ${target})
    message(FATAL_ERROR "cvcpkg_wasm_app: '${target}' is not a target")
  endif()

  cmake_parse_arguments(_cwa ""
                        "ASYNCIFY;MEMORY_GROWTH;MIMALLOC;PTHREADS"
                        "PRE_JS"
                        ${ARGN})
  if(_cwa_UNPARSED_ARGUMENTS)
    message(FATAL_ERROR "cvcpkg_wasm_app(${target}): unknown arguments: ${_cwa_UNPARSED_ARGUMENTS}")
  endif()
  foreach(_cvw_k IN LISTS _cwa_KEYWORDS_MISSING_VALUES)
    message(FATAL_ERROR "cvcpkg_wasm_app(${target}): ${_cvw_k} needs a value")
  endforeach()

  # The flags this target is compiled and linked with that this call can see:
  # the target's own LINK_OPTIONS / LINK_FLAGS[_<CFG>], the -s... items it passes
  # to target_link_libraries (the common emscripten idiom), and the global
  # C/C++ + linker flags for the build type. Not the directory's: a target's
  # LINK_OPTIONS / COMPILE_OPTIONS start as its directory's when it is created,
  # and a directory option added later is not on its link line.
  string(TOUPPER "${CMAKE_BUILD_TYPE}" _cfg)
  _cvw_app_target_flags(${target} _flags)
  get_target_property(_cvv ${target} COMPILE_OPTIONS)
  if(_cvv)
    string(APPEND _flags ";${_cvv}")
  endif()
  foreach(_cvv CMAKE_C_FLAGS CMAKE_CXX_FLAGS CMAKE_EXE_LINKER_FLAGS)
    string(APPEND _flags " ${${_cvv}} ${${_cvv}_${_cfg}}")
  endforeach()
  set(_asan OFF)
  if(_flags MATCHES "(^|[; :])-fsanitize=[^ ;]*address")
    set(_asan ON)
  endif()

  # PTHREADS: any CMake boolean, or AUTO = re-detect from the link line (a
  # -pthread added by cvcpkg_wasm_threads() or passed on the command line).
  # ON puts -pthread on this target's compile AND link line: Emscripten must
  # compile the app's objects threaded, and the final link pulls every static
  # dependency in (built -pthread too; mixing is "--shared-memory is
  # disallowed").
  set(_pthread AUTO)
  if(DEFINED _cwa_PTHREADS)
    if(_cwa_PTHREADS STREQUAL "AUTO")
      set(_pthread AUTO)
    elseif(_cwa_PTHREADS)
      set(_pthread ON)
    else()
      set(_pthread OFF)
    endif()
  endif()
  if(_pthread STREQUAL "AUTO")
    if(_flags MATCHES "(^|[; :])-pthread($|[; ])")
      set(_pthread ON)
    else()
      set(_pthread OFF)
    endif()
  endif()
  if(_pthread)
    target_compile_options(${target} PRIVATE -pthread)
    target_link_options(${target} PRIVATE -pthread)
  endif()

  # ASYNCIFY: default ON — an app that yields (emscripten_sleep, VTK's
  # in-render yield, a blocking fetch) needs it; pass ASYNCIFY OFF for a hot
  # loop that never yields. Not added if the link line already sets -sASYNCIFY.
  set(_asyncify ON)
  if(DEFINED _cwa_ASYNCIFY)
    if(_cwa_ASYNCIFY)
      set(_asyncify ON)
    else()
      set(_asyncify OFF)
    endif()
  endif()
  if(_asyncify AND NOT _flags MATCHES "(^|[; :])-s[ ]*ASYNCIFY")
    target_link_options(${target} PRIVATE "-sASYNCIFY=1")
  endif()

  # MEMORY_GROWTH: default ON — a browser page starts with a small heap and
  # grows it on demand; a fixed heap is a common cause of OOM crashes in the
  # browser that do not happen natively.
  set(_mgrowth ON)
  if(DEFINED _cwa_MEMORY_GROWTH)
    if(_cwa_MEMORY_GROWTH)
      set(_mgrowth ON)
    else()
      set(_mgrowth OFF)
    endif()
  endif()
  if(_mgrowth AND NOT _flags MATCHES "(^|[; :])-s[ ]*ALLOW_MEMORY_GROWTH")
    target_link_options(${target} PRIVATE "-sALLOW_MEMORY_GROWTH=1")
  endif()

  # MIMALLOC: AUTO = ON iff this target links -pthread (a threaded app
  # serialises every malloc/free on dlmalloc's one global lock, which threads
  # contend on; mimalloc is per-thread) and NEVER under -fsanitize=address
  # (emcc refuses the combination). A pre-existing -sMALLOC= is kept (below).
  set(_malloc AUTO)
  if(DEFINED _cwa_MIMALLOC)
    string(TOUPPER "${_cwa_MIMALLOC}" _malloc)
    if(NOT _malloc STREQUAL "AUTO")
      if(_cwa_MIMALLOC)
        set(_malloc ON)
      else()
        set(_malloc OFF)
      endif()
    endif()
  endif()
  if(_malloc STREQUAL "AUTO")
    if(_pthread AND _asan)
      message(STATUS "cvcpkg_wasm_app(${target}): MIMALLOC AUTO -> OFF: built with "
                     "-fsanitize=address, which emcc refuses to combine with mimalloc")
      set(_malloc OFF)
    elseif(_pthread)
      set(_malloc ON)
    else()
      set(_malloc OFF)
    endif()
  elseif(_malloc AND _asan)
    message(WARNING "cvcpkg_wasm_app(${target}): MIMALLOC ON with -fsanitize=address -- "
                    "emcc refuses mimalloc under ASan and will stop at the link. Pass "
                    "MIMALLOC AUTO (OFF under ASan) or OFF.")
  endif()

  # An allocator the app already chose (its own -sMALLOC= or a dependency's)
  # wins: mimalloc already there is simply ON (nothing to add); any other
  # -sMALLOC= of its own is kept, with a note if we would have added mimalloc.
  if(_flags MATCHES "(^|[; :])-s[ ]*MALLOC=([A-Za-z0-9_-]*)")
    if(NOT "${CMAKE_MATCH_1}" STREQUAL "mimalloc" AND _malloc)
      message(WARNING "cvcpkg_wasm_app(${target}): the target already links "
                      "-sMALLOC=${CMAKE_MATCH_1}; keeping it instead of mimalloc "
                      "(pass MIMALLOC OFF to silence this)")
    endif()
    set(_malloc OFF)
  elseif(_malloc)
    target_link_options(${target} PRIVATE "-sMALLOC=mimalloc")
  endif()

  # PRE_JS: client-side JS that runs before the wasm module starts, one
  # --pre-js per file; each is registered as a LINK_DEPENDS so an edited file
  # re-links the app.
  foreach(_cvw_js IN LISTS _cwa_PRE_JS)
    target_link_options(${target} PRIVATE "SHELL:--pre-js \"${_cvw_js}\"")
    set_property(TARGET ${target} APPEND PROPERTY LINK_DEPENDS "${_cvw_js}")
  endforeach()

  set_target_properties(${target} PROPERTIES
    CVCPKG_WASM_APP ON
    CVCPKG_WASM_APP_PTHREADS ${_pthread}
    CVCPKG_WASM_APP_ASYNCIFY ${_asyncify}
    CVCPKG_WASM_APP_MEMORY_GROWTH ${_mgrowth}
    CVCPKG_WASM_APP_MIMALLOC ${_malloc})
endfunction()

# The link flags on ${target}'s own link line: LINK_OPTIONS, LINK_FLAGS[_<CFG>],
# and the -s... items given to target_link_libraries (a common emscripten idiom).
function(_cvw_app_target_flags target out)
  string(TOUPPER "${CMAKE_BUILD_TYPE}" _cfg)
  set(_r "")
  foreach(_p LINK_OPTIONS LINK_FLAGS LINK_FLAGS_${_cfg})
    get_target_property(_v ${target} ${_p})
    if(_v)
      string(APPEND _r ";${_v}")
    endif()
  endforeach()
  get_target_property(_v ${target} LINK_LIBRARIES)
  if(_v)
    foreach(_i IN LISTS _v)
      if(_i MATCHES "^-s")
        string(APPEND _r ";${_i}")
      endif()
    endforeach()
  endif()
  set(${out} "${_r}" PARENT_SCOPE)
endfunction()