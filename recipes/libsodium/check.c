/* recipes/libsodium/check.c -- a consumer of the installed libsodium, built and
   run by test.sh (linux, macos, wasm, wasm-mt) and build.ps1 (windows, with the
   MSVC environment the build has).  Known answers for every primitive TX.WTF's
   end-to-end encryption uses; see test.sh's header for the list.
   CVC_THREADS=N (wasm-mt): the checks again on N pthreads, with randomness from
   getentropy() (see build-wasm.sh). */
#include <sodium.h>
#include <stdio.h>
#include <string.h>
#ifdef CVC_THREADS
#include <pthread.h>
#include <sys/random.h>
#include <unistd.h>
#endif

static int hex_eq(const unsigned char *b, size_t n, const char *hex) {
  char buf[257];
  if (n * 2 + 1 > sizeof buf)
    return 0;
  sodium_bin2hex(buf, sizeof buf, b, n);
  return strcmp(buf, hex) == 0;
}

static void from_hex(unsigned char *b, size_t n, const char *hex) {
  size_t got = 0;
  sodium_hex2bin(b, n, hex, strlen(hex), NULL, &got, NULL);
}

#define CHECK(name, cond)                                                                          \
  do {                                                                                             \
    if (!(cond)) {                                                                                 \
      printf("FAIL: %s\n", name);                                                                  \
      return 0;                                                                                    \
    }                                                                                              \
  } while (0)

static int check(void) {
  /* X25519, RFC 7748 section 6.1. */
  unsigned char a[32], b[32], pa[32], pb[32], k1[32], k2[32];
  from_hex(a, 32, "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a");
  from_hex(b, 32, "5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb");
  CHECK("x25519 base", crypto_scalarmult_base(pa, a) == 0 && crypto_scalarmult_base(pb, b) == 0);
  CHECK("x25519 alice pub", hex_eq(pa, 32, "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"));
  CHECK("x25519 bob pub", hex_eq(pb, 32, "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"));
  CHECK("x25519 dh", crypto_scalarmult(k1, a, pb) == 0 && crypto_scalarmult(k2, b, pa) == 0);
  CHECK("x25519 shared", hex_eq(k1, 32, "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742") &&
                             memcmp(k1, k2, 32) == 0);
  unsigned char zero[32] = {0};
  CHECK("x25519 low order refused", crypto_scalarmult(k1, a, zero) != 0);

  /* Ed25519, RFC 8032 section 7.1, test 1 (empty message). */
  unsigned char seed[32], pk[32], sk[64], sig[64];
  from_hex(seed, 32, "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60");
  CHECK("ed25519 keypair", crypto_sign_seed_keypair(pk, sk, seed) == 0);
  CHECK("ed25519 pub", hex_eq(pk, 32, "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"));
  CHECK("ed25519 sign", crypto_sign_detached(sig, NULL, (const unsigned char *)"", 0, sk) == 0);
  CHECK("ed25519 sig", hex_eq(sig, 64,
                              "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
                              "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"));
  CHECK("ed25519 verify", crypto_sign_verify_detached(sig, (const unsigned char *)"", 0, pk) == 0);
  sig[0] ^= 1;
  CHECK("ed25519 bad sig refused", crypto_sign_verify_detached(sig, (const unsigned char *)"", 0, pk) != 0);

  /* BLAKE2b-512, RFC 7693 appendix A; a keyed hash is a function of its key. */
  unsigned char h[64], h2[32], h3[32];
  crypto_generichash(h, 64, (const unsigned char *)"abc", 3, NULL, 0);
  CHECK("blake2b abc", hex_eq(h, 64,
                              "ba80a53f981c4d0d6a2797b69f12f6e94c212f14685ac4b74b12bb6fdbffa2d1"
                              "7d87c5392aab792dc252d5de4533cc9518d38aa8dbf1925ab92386edd4009923"));
  crypto_generichash(h2, 32, (const unsigned char *)"\x01", 1, k2, 32);
  crypto_generichash(h3, 32, (const unsigned char *)"\x01", 1, k2, 32);
  CHECK("blake2b keyed", memcmp(h2, h3, 32) == 0);
  crypto_generichash(h3, 32, (const unsigned char *)"\x02", 1, k2, 32);
  CHECK("blake2b keyed differs", memcmp(h2, h3, 32) != 0);

  /* XChaCha20-Poly1305 IETF: round trip; tamper and wrong AD refused. */
  const unsigned char msg[] = "the quick brown fox";
  const size_t mlen = sizeof msg - 1;
  unsigned char key[32], nonce[24], ct[sizeof msg + 16], pt[sizeof msg];
  unsigned long long clen = 0, plen = 0;
  randombytes_buf(key, sizeof key);
  randombytes_buf(nonce, sizeof nonce);
  crypto_aead_xchacha20poly1305_ietf_encrypt(ct, &clen, msg, mlen, (const unsigned char *)"ad", 2,
                                             NULL, nonce, key);
  CHECK("xchacha len", clen == mlen + 16);
  CHECK("xchacha open", crypto_aead_xchacha20poly1305_ietf_decrypt(pt, &plen, NULL, ct, clen,
                                                                   (const unsigned char *)"ad", 2,
                                                                   nonce, key) == 0 &&
                            plen == mlen && memcmp(pt, msg, mlen) == 0);
  CHECK("xchacha wrong ad", crypto_aead_xchacha20poly1305_ietf_decrypt(pt, &plen, NULL, ct, clen,
                                                                       (const unsigned char *)"aD", 2,
                                                                       nonce, key) != 0);
  ct[3] ^= 0x40;
  CHECK("xchacha tamper", crypto_aead_xchacha20poly1305_ietf_decrypt(pt, &plen, NULL, ct, clen,
                                                                     (const unsigned char *)"ad", 2,
                                                                     nonce, key) != 0);

  /* secretbox (XSalsa20-Poly1305) and a sealed box. */
  unsigned char sb[sizeof msg + crypto_secretbox_MACBYTES], sbo[sizeof msg];
  crypto_secretbox_easy(sb, msg, mlen, nonce, key);
  CHECK("secretbox open", crypto_secretbox_open_easy(sbo, sb, mlen + crypto_secretbox_MACBYTES,
                                                     nonce, key) == 0 &&
                              memcmp(sbo, msg, mlen) == 0);
  unsigned char bpk[32], bsk[32], sealed[sizeof msg + crypto_box_SEALBYTES], so[sizeof msg];
  crypto_box_keypair(bpk, bsk);
  crypto_box_seal(sealed, msg, mlen, bpk);
  CHECK("box seal open", crypto_box_seal_open(so, sealed, sizeof sealed - 1, bpk, bsk) == 0 &&
                             memcmp(so, msg, mlen) == 0);

  /* Argon2id at the minimum limits: deterministic per salt, salts differ. */
  unsigned char salt[crypto_pwhash_SALTBYTES] = {1, 2, 3}, d1[32], d2[32];
  const unsigned long long ops = crypto_pwhash_OPSLIMIT_MIN;
  const size_t mem = crypto_pwhash_MEMLIMIT_MIN;
  CHECK("argon2id", crypto_pwhash(d1, 32, "correct horse", 13, salt, ops, mem,
                                  crypto_pwhash_ALG_ARGON2ID13) == 0 &&
                        crypto_pwhash(d2, 32, "correct horse", 13, salt, ops, mem,
                                      crypto_pwhash_ALG_ARGON2ID13) == 0 &&
                        memcmp(d1, d2, 32) == 0);
  salt[0] = 9;
  CHECK("argon2id salt", crypto_pwhash(d2, 32, "correct horse", 13, salt, ops, mem,
                                       crypto_pwhash_ALG_ARGON2ID13) == 0 &&
                             memcmp(d1, d2, 32) != 0);
  char str[crypto_pwhash_STRBYTES];
  CHECK("argon2id str", crypto_pwhash_str_alg(str, "pw", 2, ops, mem, crypto_pwhash_ALG_ARGON2ID13) == 0 &&
                            crypto_pwhash_str_verify(str, "pw", 2) == 0 &&
                            crypto_pwhash_str_verify(str, "pW", 2) != 0);
  return 1;
}

#ifdef CVC_THREADS
/* Randomness that works on every thread: emscripten implements getentropy()
   on whichever thread calls it, where libsodium's own JavaScript source is set
   up on the thread that ran randombytes_stir(). */
static const char *ge_name(void) { return "getentropy"; }
static void ge_buf(void *const buf, const size_t size) {
  unsigned char *p = (unsigned char *)buf;
  size_t left = size;
  while (left > 0) {
    const size_t n = left > 256 ? 256 : left;
    if (getentropy(p, n) != 0)
      sodium_misuse();
    p += n;
    left -= n;
  }
}
static uint32_t ge_random(void) {
  uint32_t v;
  ge_buf(&v, sizeof v);
  return v;
}
static randombytes_implementation ge_impl = {ge_name, ge_random, NULL, NULL, ge_buf, NULL};

static void *run(void *res) {
  *(int *)res = check();
  return NULL;
}
#endif

int main(void) {
#ifdef CVC_THREADS
  randombytes_set_implementation(&ge_impl);
#endif
  if (sodium_init() < 0) {
    printf("FAIL: sodium_init\n");
    return 1;
  }
  int ok = strcmp(sodium_version_string(), "1.0.22") == 0;
  if (!ok)
    printf("FAIL: version %s\n", sodium_version_string());
  ok &= check();
  int threads = 0;
#ifdef CVC_THREADS
  pthread_t th[CVC_THREADS];
  int res[CVC_THREADS], started[CVC_THREADS];
  for (int i = 0; i < CVC_THREADS; ++i) {
    res[i] = 0;
    started[i] = pthread_create(&th[i], NULL, run, &res[i]) == 0;
  }
  for (int i = 0; i < CVC_THREADS; ++i) {
    if (started[i])
      pthread_join(th[i], NULL);
    ok &= started[i] && res[i];
    threads += started[i];
  }
#endif
  printf("libsodium %s (%s): checks %s (threads: %d)\n", sodium_version_string(),
         randombytes_implementation_name(), ok ? "ok" : "FAILED", threads);
  return ok ? 0 : 1;
}
