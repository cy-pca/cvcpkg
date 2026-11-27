/* recipes/imagemagick/wasi-compat/cvc-wasi-compat.h -- for the wasi build only.
 *
 * Force-included (-include) into every ImageMagick translation unit.  wasi-libc
 * declares system() in <stdlib.h> but does not define it, since wasip1 cannot
 * start a process; MagickCore calls it to run an external delegate
 * (ghostscript, ffmpeg, ...), which on wasi fails the way a missing shell
 * does.  <stdlib.h> comes first so the macro never meets its declaration.
 * Not installed.
 */
#ifndef CVC_WASI_COMPAT_H
#define CVC_WASI_COMPAT_H

#include <errno.h>
#include <stdlib.h>

static inline int cvc_wasi_no_system(const char *command)
{
  (void) command;
  errno = ENOSYS;
  return -1;
}
#define system(command) cvc_wasi_no_system(command)

/* Nor does it have popen()/pclose(), not even declared: utility-private.h's
 * popen_utf8() calls popen() on every non-Windows target.  No pipe to a
 * command can open, so callers take their "command failed" path. */
#include <stdio.h>

static inline FILE *popen(const char *command, const char *mode)
{
  (void) command;
  (void) mode;
  errno = ENOSYS;
  return (FILE *) NULL;
}

static inline int pclose(FILE *file)
{
  (void) file;
  errno = ECHILD;
  return -1;
}

#endif
