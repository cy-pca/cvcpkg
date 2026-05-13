/* libiimod imodconfig.h — vendored copy used by libcvc-deps's source build.
 *
 * Upstream IMOD's `setup` script generates this file from machines/*.
 * We don't run that script; instead we ship a fixed copy here that
 * matches the defines libcvc was using when libiimod was vendored
 * in-tree. Endianness is overridden at build time via -D when the
 * target is big-endian (none of our supported runners are).
 *
 * Original source: LabShare-Archive/IMOD machines/ + setup output.
 */
#ifndef IMOD_CONFIG_INCLUDED
#define IMOD_CONFIG_INCLUDED
#define VERSION              406
#define VERSION_NAME "4.0.6"
#define COPYRIGHT_YEARS "1994-2009"
#define LAB_NAME1 "Boulder Laboratory for 3-Dimensional"
#define LAB_NAME2 "Electron Microscopy of Cells"
#define G77__HACK
#define B3D_LITTLE_ENDIAN
#define SWAP_IEEE_FLOATS
#define SENDEVENT_RETRY_HACK 0
#define CTRL_STRING "Ctrl"
typedef char b3dByte;
typedef unsigned char b3dUByte;
typedef short int b3dInt16;
typedef unsigned short int b3dUInt16;
typedef int b3dInt32;
typedef unsigned int b3dUInt32;
typedef float b3dFloat;

#endif
