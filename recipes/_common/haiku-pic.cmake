# recipes/_common/haiku-pic.cmake — CMAKE_PROJECT_INCLUDE for Haiku builds of
# projects that turn on POSITION_INDEPENDENT_CODE themselves.
#
# Haiku links executables as shared objects, so everything must be compiled
# -fPIC (see env-haiku.sh).  A project that sets CMAKE_POSITION_INDEPENDENT_CODE
# gets CMake's PIE flag (-fPIE) appended to every executable's objects, after
# env-haiku.sh's -fPIC, and the link then fails with "relocation R_X86_64_PC32
# against symbol ... can not be used when making a shared object".  Included at
# the end of each project(), after the compiler modules set the PIE flags, this
# makes that flag -fPIC as well.  Opt in from build.sh with
# -DCMAKE_PROJECT_INCLUDE="${SCRIPT_DIR}/../_common/haiku-pic.cmake".
set(CMAKE_C_COMPILE_OPTIONS_PIE "-fPIC")
set(CMAKE_CXX_COMPILE_OPTIONS_PIE "-fPIC")
