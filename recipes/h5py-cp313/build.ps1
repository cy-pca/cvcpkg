# recipes/h5py-cp313/build.ps1 — Windows from-source build (STUB).
#
# DELTA vs build.sh: the Unix path links cvcpkg's libhdf5.so.310 via HDF5_DIR and
# stamps an $ORIGIN rpath into the extension .so. Windows has no rpath — the
# h5py *.pyd must instead find hdf5.dll on the DLL search path (the merged
# prefix's bin/), and the HDF5 layout comes from the vcpkg-built hdf5 package,
# not the CMake tarball. That port is not done yet.
#
# Until it lands, Windows consumers fall back to the prebuilt h5py wheel column.
# Fail loudly rather than produce a broken bundle.
$ErrorActionPreference = "Stop"
Write-Error "h5py-cp313: Windows from-source build not yet implemented (MSVC + vcpkg HDF5_DIR port pending). Use the prebuilt wheel column on Windows."
exit 1
