# recipes/pystring/CMakeLists.txt — replaces pystring 1.1.4's own CMakeLists.txt.
#
# Upstream's forces BUILD_SHARED_LIBS, always builds the test program and
# installs only the library: no header, and no RUNTIME destination, so a
# Windows DLL build fails at install. This one builds a static, PIC library
# and installs it with pystring.h under include/pystring/, the layout
# OpenColorIO's Findpystring.cmake searches.
cmake_minimum_required(VERSION 3.12)
project(pystring VERSION 1.1.4 LANGUAGES CXX)

include(GNUInstallDirs)

add_library(pystring STATIC pystring.cpp)
set_target_properties(pystring PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(pystring PUBLIC
    "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>"
    "$<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}/pystring>")

install(TARGETS pystring EXPORT pystringTargets
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(FILES pystring.h DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/pystring)
install(EXPORT pystringTargets NAMESPACE pystring::
    FILE pystringConfig.cmake
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/pystring)
