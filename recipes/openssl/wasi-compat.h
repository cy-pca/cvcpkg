/* recipes/openssl/wasi-compat.h -- force-included into the wasi build only.
 *
 * wasip1 has no processes.  wasi-libc declares getpid() (deprecated) but
 * defines it only in libwasi-emulated-getpid.a, so libcrypto's two callers --
 * fork detection in crypto/threads_none.c and the pid mixed into the seed's
 * additional data in rand_unix.c -- would leave every consumer of libcrypto.a
 * an undefined getpid to resolve.  A wasi program is one process that never
 * forks, so a constant pid is the truth for both.  Not installed.
 */
#ifndef CVC_OPENSSL_WASI_COMPAT_H
#define CVC_OPENSSL_WASI_COMPAT_H

#include <sys/types.h>
#include <unistd.h>

static inline pid_t cvc_wasi_getpid(void)
{
  return (pid_t) 1;
}
#define getpid() cvc_wasi_getpid()

#endif
