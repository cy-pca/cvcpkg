// recipes/binaryen/check.cpp -- the installed libbinaryen, through the C++ API
// libcvc uses (src/ariadne_wasm in libcvc): a WAT module parsed, run through
// Asyncify around its `ari` imports, written as a binary and read back.
//
// The checks:
//   - the module below parses, validates and writes;
//   - after Asyncify (asyncify-imports@ari.*, at -O1, as wasm-opt
//     --asyncify --pass-arg=asyncify-imports@ari.* -O1 runs it), the module
//     exports asyncify_start_unwind and the other four, still imports ari.call,
//     and reads back from its binary;
//   - WAT that doesn't parse is an error with a message, not an exit (the C
//     API's BinaryenModuleParse calls Fatal(), which exits the process).
#include <cstdio>
#include <string>
#include <vector>

#include "parser/wat-parser.h"
#include "pass.h"
#include "support/result.h"
#include "wasm-binary.h"
#include "wasm-validator.h"
#include "wasm.h"

namespace {

const char *const kGuest = R"((module
  (import "ari" "call" (func $call (param i32) (result i64)))
  (memory (export "memory") 1)
  (func $add (export "add") (param i32 i32) (result i32)
    (drop (call $call (local.get 0)))
    (i32.add (local.get 0) (local.get 1))))
)";

int fail(const std::string &why) {
  std::printf("FAIL: %s\n", why.c_str());
  return 1;
}

} // namespace

int main() {
  wasm::Module m;
  m.features = wasm::FeatureSet::Default | wasm::FeatureSet::BulkMemory |
               wasm::FeatureSet::BulkMemoryOpt | wasm::FeatureSet::Multivalue |
               wasm::FeatureSet::TruncSat | wasm::FeatureSet::ReferenceTypes;
  if (auto *err = wasm::WATParser::parseModule(m, kGuest).getErr())
    return fail("the guest didn't parse: " + err->msg);
  if (!wasm::WasmValidator().validate(m, wasm::WasmValidator::Globally | wasm::WasmValidator::Quiet))
    return fail("the parsed guest isn't valid");
  std::printf("  OK: a WAT module parsed and validated\n");

  wasm::PassOptions options;
  options.optimizeLevel = 1;
  options.shrinkLevel = 0;
  options.arguments["asyncify-imports"] = "ari.*";
  {
    wasm::PassRunner runner(&m, options);
    runner.add("asyncify");
    runner.run();
  }
  {
    wasm::PassRunner runner(&m, options);
    runner.addDefaultOptimizationPasses();
    runner.run();
  }
  if (!wasm::WasmValidator().validate(m, wasm::WasmValidator::Globally | wasm::WasmValidator::Quiet))
    return fail("the module isn't valid after Asyncify");

  wasm::BufferWithRandomAccess out;
  wasm::WasmBinaryWriter writer(&m, out, options);
  writer.write();
  std::vector<char> bytes(out.begin(), out.end());

  wasm::Module back;
  back.features = m.features;
  try {
    wasm::WasmBinaryReader reader(back, back.features, bytes);
    reader.read();
  } catch (const wasm::ParseException &e) {
    return fail("the transformed binary didn't read back: " + e.text);
  }
  for (const char *name : {"asyncify_start_unwind", "asyncify_stop_unwind", "asyncify_start_rewind",
                           "asyncify_stop_rewind", "asyncify_get_state", "add"})
    if (!back.getExportOrNull(name))
      return fail(std::string("the transformed module exports no ") + name);
  bool imports_call = false;
  for (const auto &f : back.functions)
    if (f->imported() && f->module == "ari" && f->base == "call")
      imports_call = true;
  if (!imports_call)
    return fail("the transformed module no longer imports ari.call");
  std::printf("  OK: Asyncify around ari.* (%zu bytes), read back with its five exports\n",
              bytes.size());

  wasm::Module bad;
  if (!wasm::WATParser::parseModule(bad, "(module (func (i32.const)))").getErr())
    return fail("WAT that can't parse parsed");
  std::printf("  OK: WAT that doesn't parse is an error\n");
  std::printf("binaryen check: OK\n");
  return 0;
}
