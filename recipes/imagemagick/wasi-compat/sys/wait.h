/* recipes/imagemagick/wasi-compat/sys/wait.h -- for the wasi build only.
 *
 * wasip1 has no processes, so wasi-libc ships no <sys/wait.h>.  MagickCore's
 * studio.h includes it on every POSIX target, and delegate.c reads the status
 * macros.  No child can exist, so waitpid() fails with ECHILD.  This directory
 * is on the include path of the ImageMagick build alone; it is not installed.
 */
#ifndef CVC_WASI_COMPAT_SYS_WAIT_H
#define CVC_WASI_COMPAT_SYS_WAIT_H

#include <errno.h>
#include <sys/types.h>

#define WNOHANG 1
#define WUNTRACED 2

#define WEXITSTATUS(s) (((s) & 0xff00) >> 8)
#define WTERMSIG(s) ((s) & 0x7f)
#define WIFEXITED(s) (WTERMSIG(s) == 0)
#define WIFSIGNALED(s) (((s) & 0x7f) != 0 && ((s) & 0x7f) != 0x7f)

static inline pid_t waitpid(pid_t pid, int *status, int options)
{
  (void) pid;
  (void) status;
  (void) options;
  errno = ECHILD;
  return (pid_t) -1;
}

#endif
