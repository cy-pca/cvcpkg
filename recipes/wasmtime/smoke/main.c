/* recipes/wasmtime/smoke: compile a WAT module with wasmtime's C API and call its export. */
#include <stdio.h>
#include <string.h>
#include <wasmtime.h>

static int failed(const char *what, wasmtime_error_t *error, wasm_trap_t *trap) {
  wasm_byte_vec_t message;
  message.size = 0;
  if (error != NULL) {
    wasmtime_error_message(error, &message);
    wasmtime_error_delete(error);
  } else if (trap != NULL) {
    wasm_trap_message(trap, &message);
    wasm_trap_delete(trap);
  }
  fprintf(stderr, "wasmtime smoke: %s failed: %.*s\n", what, (int)message.size,
          message.size ? message.data : "");
  if (message.size)
    wasm_byte_vec_delete(&message);
  return 1;
}

int main(void) {
  static const char wat[] = "(module (func (export \"answer\") (result i32) i32.const 42))";
  wasm_engine_t *engine = wasm_engine_new();
  if (engine == NULL) {
    fprintf(stderr, "wasmtime smoke: wasm_engine_new failed\n");
    return 1;
  }
  wasmtime_store_t *store = wasmtime_store_new(engine, NULL, NULL);
  wasmtime_context_t *context = wasmtime_store_context(store);

  wasm_byte_vec_t wasm;
  wasmtime_error_t *error = wasmtime_wat2wasm(wat, strlen(wat), &wasm);
  if (error != NULL)
    return failed("wat2wasm", error, NULL);
  wasmtime_module_t *module = NULL;
  error = wasmtime_module_new(engine, (const uint8_t *)wasm.data, wasm.size, &module);
  wasm_byte_vec_delete(&wasm);
  if (error != NULL)
    return failed("compiling the module", error, NULL);

  wasm_trap_t *trap = NULL;
  wasmtime_instance_t instance;
  error = wasmtime_instance_new(context, module, NULL, 0, &instance, &trap);
  if (error != NULL || trap != NULL)
    return failed("instantiating the module", error, trap);

  wasmtime_extern_t answer;
  if (!wasmtime_instance_export_get(context, &instance, "answer", strlen("answer"), &answer) ||
      answer.kind != WASMTIME_EXTERN_FUNC) {
    fprintf(stderr, "wasmtime smoke: no exported function 'answer'\n");
    return 1;
  }
  wasmtime_val_t result;
  error = wasmtime_func_call(context, &answer.of.func, NULL, 0, &result, 1, &trap);
  if (error != NULL || trap != NULL)
    return failed("calling answer", error, trap);

  printf("wasmtime answer %d\n", (int)result.of.i32);
  wasmtime_module_delete(module);
  wasmtime_store_delete(store);
  wasm_engine_delete(engine);
  return result.of.i32 == 42 ? 0 : 1;
}
