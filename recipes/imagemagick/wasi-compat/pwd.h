/* recipes/imagemagick/wasi-compat/pwd.h -- for the wasi build only.
 *
 * wasip1 has no users, so wasi-libc ships no <pwd.h>.  MagickCore includes it
 * on every POSIX target and calls getpwnam() to expand "~user/" in a file
 * name; with no user database that lookup finds nobody, and the name is left
 * as it is.  Not installed.
 */
#ifndef CVC_WASI_COMPAT_PWD_H
#define CVC_WASI_COMPAT_PWD_H

#include <errno.h>
#include <stddef.h>
#include <sys/types.h>

struct passwd
{
  char *pw_name;
  char *pw_passwd;
  uid_t pw_uid;
  gid_t pw_gid;
  char *pw_gecos;
  char *pw_dir;
  char *pw_shell;
};

static inline struct passwd *getpwnam(const char *name)
{
  (void) name;
  errno = ENOENT;
  return (struct passwd *) NULL;
}

static inline struct passwd *getpwuid(uid_t uid)
{
  (void) uid;
  errno = ENOENT;
  return (struct passwd *) NULL;
}

#endif
