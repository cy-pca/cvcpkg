/* recipes/wamr/check.c -- the installed WAMR, through its wasm-c-api
 * (wasm_c_api.h), the API libcvc's guests run on (src/ariadne_wasm in libcvc).
 *
 * The module below (its text in the comment) imports env.twice, which this host
 * defines with an env pointer, and exports a memory of 1 to 4 pages and four
 * functions. It is instantiated with InstantiationArgs and no host-managed
 * heap, as libcvc instantiates guests. The checks:
 *   - run(10) calls env.twice(10), and the host's twice calls back into the
 *     guest's own export set(5) before it returns 20: a host import that
 *     re-enters its instance, as libcvc's pauses do (asyncify_start_unwind
 *     from inside an import). run returns twice's 20 plus the 5 set left: 25;
 *   - the host writes a byte into the memory and peek reads it back;
 *   - grow(1) gives 1 and the memory is two pages; grow(8) is past the
 *     module's maximum and gives -1;
 *   - a host trap: twice(-1) traps with a message, and the call reports it.
 *
 * (module
 *   (import "env" "twice" (func $twice (param i32) (result i32)))
 *   (memory (export "memory") 1 4)
 *   (global $g (mut i32) (i32.const 0))
 *   (func (export "set") (param i32) (global.set $g (local.get 0)))
 *   (func (export "run") (param i32) (result i32)
 *     (i32.add (call $twice (local.get 0)) (global.get $g)))
 *   (func (export "grow") (param i32) (result i32) (memory.grow (local.get 0)))
 *   (func (export "peek") (param i32) (result i32) (i32.load8_u (local.get 0))))
 */
#include <stdio.h>
#include <string.h>

#include "wasm_c_api.h"

static const unsigned char kModule[] = {
    0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00, 0x01, 0x0a, 0x02, 0x60, 0x01, 0x7f, 0x01,
    0x7f, 0x60, 0x01, 0x7f, 0x00, 0x02, 0x0d, 0x01, 0x03, 0x65, 0x6e, 0x76, 0x05, 0x74, 0x77,
    0x69, 0x63, 0x65, 0x00, 0x00, 0x03, 0x05, 0x04, 0x01, 0x00, 0x00, 0x00, 0x05, 0x04, 0x01,
    0x01, 0x01, 0x04, 0x06, 0x06, 0x01, 0x7f, 0x01, 0x41, 0x00, 0x0b, 0x07, 0x24, 0x05, 0x06,
    0x6d, 0x65, 0x6d, 0x6f, 0x72, 0x79, 0x02, 0x00, 0x03, 0x73, 0x65, 0x74, 0x00, 0x01, 0x03,
    0x72, 0x75, 0x6e, 0x00, 0x02, 0x04, 0x67, 0x72, 0x6f, 0x77, 0x00, 0x03, 0x04, 0x70, 0x65,
    0x65, 0x6b, 0x00, 0x04, 0x0a, 0x21, 0x04, 0x06, 0x00, 0x20, 0x00, 0x24, 0x00, 0x0b, 0x09,
    0x00, 0x20, 0x00, 0x10, 0x00, 0x23, 0x00, 0x6a, 0x0b, 0x06, 0x00, 0x20, 0x00, 0x40, 0x00,
    0x0b, 0x07, 0x00, 0x20, 0x00, 0x2d, 0x00, 0x00, 0x0b};

/* The instance's exports, in the module's order. */
enum { kMemory, kSet, kRun, kGrow, kPeek, kExports };

struct host {
  wasm_store_t *store;
  const wasm_func_t *set; /* the guest's set, once the instance exists */
  int calls;
};

static wasm_trap_t *twice(void *env, const wasm_val_vec_t *args, wasm_val_vec_t *results) {
  struct host *h = (struct host *)env;
  const int32_t x = args->data[0].of.i32;
  ++h->calls;
  if (x < 0) {
    wasm_message_t msg;
    wasm_name_new_from_string_nt(&msg, "twice: a negative argument");
    wasm_trap_t *trap = wasm_trap_new(h->store, &msg);
    wasm_byte_vec_delete(&msg);
    return trap;
  }
  /* Back into the guest, from inside its import. */
  wasm_val_t five[1] = {WASM_I32_VAL(5)};
  wasm_val_vec_t in = WASM_ARRAY_VEC(five), out = WASM_EMPTY_VEC;
  wasm_trap_t *trap = wasm_func_call(h->set, &in, &out);
  if (trap)
    return trap;
  results->data[0].kind = WASM_I32;
  results->data[0].of.i32 = 2 * x;
  return NULL;
}

static int call1(const wasm_func_t *f, int32_t arg, int32_t *result, char *why, size_t cap) {
  wasm_val_t a[1] = {WASM_I32_VAL(arg)};
  wasm_val_t r[1] = {WASM_INIT_VAL};
  wasm_val_vec_t in = WASM_ARRAY_VEC(a), out = WASM_ARRAY_VEC(r);
  wasm_trap_t *trap = wasm_func_call(f, &in, &out);
  if (trap) {
    wasm_message_t msg;
    wasm_trap_message(trap, &msg);
    snprintf(why, cap, "%.*s", (int)msg.size, msg.data);
    wasm_byte_vec_delete(&msg);
    wasm_trap_delete(trap);
    return 0;
  }
  *result = r[0].of.i32;
  return 1;
}

#define FAIL(...)                                                                                  \
  do {                                                                                             \
    printf("FAIL: " __VA_ARGS__);                                                                  \
    printf("\n");                                                                                  \
    return 1;                                                                                      \
  } while (0)

int main(void) {
  wasm_engine_t *engine = wasm_engine_new();
  if (!engine)
    FAIL("wasm_engine_new");
  wasm_store_t *store = wasm_store_new(engine);
  if (!store)
    FAIL("wasm_store_new");

  wasm_byte_vec_t binary;
  wasm_byte_vec_new(&binary, sizeof kModule, (const wasm_byte_t *)kModule);
  wasm_module_t *module = wasm_module_new(store, &binary);
  wasm_byte_vec_delete(&binary);
  if (!module)
    FAIL("wasm_module_new refused the module");

  struct host h = {store, NULL, 0};
  wasm_functype_t *ty = wasm_functype_new_1_1(wasm_valtype_new_i32(), wasm_valtype_new_i32());
  wasm_func_t *host_twice = wasm_func_new_with_env(store, ty, twice, &h, NULL);
  wasm_functype_delete(ty);
  wasm_extern_t *imports[1] = {wasm_func_as_extern(host_twice)};
  wasm_extern_vec_t import_vec = WASM_ARRAY_VEC(imports);
  wasm_trap_t *trap = NULL;
  /* No host-managed heap: wasm_instance_new asks for 32 KiB of one, which WAMR adds to the guest's
     memory as a page of its own. */
  InstantiationArgs args = {0};
  wasm_instance_t *inst = wasm_instance_new_with_args_ex(store, module, &import_vec, &trap, &args);
  if (!inst)
    FAIL("wasm_instance_new_with_args_ex");

  wasm_extern_vec_t exports;
  wasm_instance_exports(inst, &exports);
  if (exports.size != kExports)
    FAIL("%zu exports, not %d", exports.size, (int)kExports);
  wasm_memory_t *mem = wasm_extern_as_memory(exports.data[kMemory]);
  h.set = wasm_extern_as_func(exports.data[kSet]);
  const wasm_func_t *run = wasm_extern_as_func(exports.data[kRun]);
  const wasm_func_t *grow = wasm_extern_as_func(exports.data[kGrow]);
  const wasm_func_t *peek = wasm_extern_as_func(exports.data[kPeek]);
  if (!mem || !h.set || !run || !grow || !peek)
    FAIL("an export of the wrong kind");

  char why[256] = "";
  int32_t r = 0;
  if (!call1(run, 10, &r, why, sizeof why))
    FAIL("run(10) trapped: %s", why);
  if (r != 25 || h.calls != 1)
    FAIL("run(10) gave %d after %d host calls, not 25 after 1", (int)r, h.calls);
  printf("  OK: a host import re-entered its instance (run(10) = 25)\n");

  if (wasm_memory_data_size(mem) != 65536)
    FAIL("the memory is %zu bytes, not 65536", wasm_memory_data_size(mem));
  wasm_memory_data(mem)[1234] = 0xab;
  if (!call1(peek, 1234, &r, why, sizeof why) || r != 0xab)
    FAIL("peek(1234) gave %d (%s), not 171", (int)r, why);
  if (!call1(grow, 1, &r, why, sizeof why) || r != 1)
    FAIL("grow(1) gave %d (%s), not 1", (int)r, why);
  if (wasm_memory_data_size(mem) != 2 * 65536)
    FAIL("after grow(1) the memory is %zu bytes", wasm_memory_data_size(mem));
  if (!call1(grow, 8, &r, why, sizeof why) || r != -1)
    FAIL("grow(8), past the maximum, gave %d (%s), not -1", (int)r, why);
  printf("  OK: the host reads and writes the memory, and it grows to its maximum only\n");

  if (call1(run, -1, &r, why, sizeof why))
    FAIL("run(-1) returned %d, and twice should have trapped", (int)r);
  if (!strstr(why, "a negative argument"))
    FAIL("run(-1)'s trap says \"%s\"", why);
  printf("  OK: a host trap reaches the caller (%s)\n", why);

  wasm_extern_vec_delete(&exports);
  wasm_instance_delete(inst);
  wasm_func_delete(host_twice);
  wasm_module_delete(module);
  wasm_store_delete(store);
  wasm_engine_delete(engine);
  printf("wamr check: OK\n");
  return 0;
}
