/* recipes/pillow-cp312/wasm_probe.c: the smallest host that links Pillow's localized wasm
 * extension objects, for build-wasm.sh's gate. It registers them in the inittab (the
 * pil_probe_register registrar that _common/wasm_pyext.py writes from the manifest), boots an
 * isolated interpreter on the deps prefix's stdlib, and runs a script under node (NODERAWFS:
 * the host's files are the module's files).
 *
 *   node wasm_probe.js <python home> <site-packages> <script>
 *
 * The script's status is the exit status: 0 when it ran to the end, 1 on an exception. */
#include <Python.h>

#include <stdio.h>
#include <string.h>

int pil_probe_register(void);

static int append_path(PyConfig *config, const char *path) {
  wchar_t *wide = Py_DecodeLocale(path, NULL);
  if (!wide)
    return -1;
  PyStatus status = PyWideStringList_Append(&config->module_search_paths, wide);
  PyMem_RawFree(wide);
  return PyStatus_Exception(status) ? -1 : 0;
}

int main(int argc, char **argv) {
  if (argc != 4) {
    fprintf(stderr, "usage: wasm_probe <python home> <site-packages> <script>\n");
    return 2;
  }
  if (pil_probe_register() != 0) {
    fprintf(stderr, "wasm_probe: PyImport_AppendInittab failed\n");
    return 1;
  }

  PyConfig config;
  PyConfig_InitIsolatedConfig(&config);
  config.site_import = 0;
  config.write_bytecode = 0;
  PyStatus status = PyConfig_SetBytesString(&config, &config.home, argv[1]);
  if (PyStatus_Exception(status))
    goto fail;
  char stdlib[4096];
  snprintf(stdlib, sizeof stdlib, "%s/lib/python3.12", argv[1]);
  config.module_search_paths_set = 1;
  if (append_path(&config, stdlib) != 0 || append_path(&config, argv[2]) != 0) {
    fprintf(stderr, "wasm_probe: can't set the module search path\n");
    PyConfig_Clear(&config);
    return 1;
  }
  status = Py_InitializeFromConfig(&config);
  if (PyStatus_Exception(status))
    goto fail;
  PyConfig_Clear(&config);

  FILE *script = fopen(argv[3], "rb");
  if (!script) {
    fprintf(stderr, "wasm_probe: can't open %s\n", argv[3]);
    Py_FinalizeEx();
    return 1;
  }
  int rc = PyRun_SimpleFileExFlags(script, argv[3], 1, NULL);
  if (Py_FinalizeEx() < 0)
    rc = -1;
  return rc == 0 ? 0 : 1;

fail:
  PyConfig_Clear(&config);
  Py_ExitStatusException(status);
}
