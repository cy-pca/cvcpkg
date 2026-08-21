# Roadmap: new dependencies

Status: living document. Last reviewed 2026-08-21.

This file used to hold two proposal-stage shopping lists: the
emsdk / SWIG / Qt-on-wasm / zstd batch drafted here, and the
database-client / wasm-runtime batch from the old root
`new-dependencies-roadmap.md`. Everything proposed in both has since
shipped as a recipe under [`../../recipes/`](../../recipes/) — see the
ledger below. The per-component recipe model from the
[split-distribution roadmap](split-distribution.md) also made the old
"land the split distribution first" sequencing constraint moot.

What remains here is the follow-up work those recipes unblocked.

## Open items

### 1. Enable the Qt 6 QMYSQL / QPSQL SQL driver plugins

The Qt 6 recipe still disables both SQL driver plugins, a leftover
from when the client libraries had no recipes. All four build scripts
pass the flags today:

- `recipes/qt6/build.sh` and `build.ps1` (native):
  `-DFEATURE_sql_mysql=OFF -DFEATURE_sql_psql=OFF`
- `recipes/qt6/build-wasm.sh` and `build-wasm.ps1`: same flags

Now that `recipes/mariadb-connector-c/` and `recipes/libpq/` exist,
the native builds can enable the plugins:

1. Drop the two `OFF` flags from `build.sh` and `build.ps1` — or flip
   them to `=ON` so configure fails loudly if the client libraries are
   not found. Keep them `OFF` in the two wasm scripts; there are no
   database client libraries on wasm.
2. Add `depends.runtime` edges on `mariadb-connector-c` and `libpq` to
   `recipes/qt6/recipe.yaml`, platform-scoped to
   `[linux, macos, windows]` (the same `platforms:` scoping the recipe
   already uses for its `python3` build dep).
3. Bump `cvc_revision` (currently 7). A script change without a bump
   silently no-ops against the published variants — see
   [revision-bump-cascade.md](revision-bump-cascade.md).

### 2. `hdf5-zstd-filter` plugin recipe

Carried from the zstd proposal's open questions: HDF5 supports zstd
compression through a third-party filter plugin, and the decision was
to ship that plugin as its own recipe rather than folding it into
`hdf5` or `zstd`. Both prerequisites (`recipes/hdf5/`,
`recipes/zstd/`) have shipped; the filter recipe still does not exist.
It is a small CMake build with `hdf5` and `zstd` as build deps, a
runtime edge on `zstd`, and the plugin shared library installed in the
prefix where consumers can point `HDF5_PLUGIN_PATH`.

## Shipped ledger

All ten proposals from the two original documents, for provenance.
Versions are each recipe's `upstream_version` as of 2026-08. Older
roadmap docs that cite "`new-dependencies.md` §1" mean the Emscripten
SDK row.

| Proposal | Recipe | Note |
|---|---|---|
| Emscripten SDK | `recipes/emsdk/` (5.0.7) | Snapshot of an activated emsdk tree; ports cache pre-populated via `embuilder`, so consumer-side builds are offline |
| SWIG | `recipes/swig/` (4.4.1) | Build-time host tool for Python / C# wrapper generation |
| Qt 6 `wasm_singlethread` | `recipes/qt6/` (6.8.2) | Not a separate recipe — `platform: wasm` matrix entries (`build-wasm.sh` / `.ps1`) configure with `-DFEATURE_thread=OFF` |
| zstd | `recipes/zstd/` (1.5.7) | Also built for wasm, wasi, and cosmo |
| MariaDB Connector/C | `recipes/mariadb-connector-c/` (3.4.8) | CMake; TLS via the `openssl` recipe. Unblocks open item 1 |
| libpq | `recipes/libpq/` (18.4) | Meson; builds the client library only, not the server. Unblocks open item 1 |
| Wasmtime | `recipes/wasmtime/` (45.0.0) | Stages prebuilt C API release artifacts — no Rust toolchain needed |
| Wasmer | `recipes/wasmer/` (7.1.0) | Stages prebuilt release artifacts |
| WasmEdge | `recipes/wasmedge/` (0.17.0) | Built from source with CMake |
| WAMR | `recipes/wamr/` (2.4.4) | Built from source with CMake |
