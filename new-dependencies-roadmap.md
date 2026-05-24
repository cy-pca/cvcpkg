# New Dependencies Roadmap

Planned new recipes for libcvc-deps. Each entry describes why the
dependency is needed, which libcvc/volrover3 subsystem would consume
it, and any known build-system notes.

---

## Database client libraries

### MySQL / MariaDB client (`libmysqlclient` / Connector/C)

- **Why**: Enable libcvc's data-management layer to read/write
  experimental datasets stored in MySQL/MariaDB databases. Qt6's
  `QMYSQL` SQL driver plugin also requires the client library at
  runtime; making it available as a recipe would let us re-enable
  the plugin instead of disabling it with `-DFEATURE_sql_mysql=OFF`.
- **Upstream**: https://dev.mysql.com/downloads/c-api/ or
  https://mariadb.com/downloads/connectors/connectors-data-access/
- **Build system**: CMake (MariaDB Connector/C is straightforward).
  MariaDB Connector/C is Apache-2.0 licensed and wire-compatible
  with MySQL; prefer it over Oracle's GPL-licensed client.
- **Platforms**: Linux, macOS, Windows.
- **Recipe notes**:
  - Source: MariaDB Connector/C tarball from GitHub releases.
  - Linux/macOS: `cvc_cmake_build` with TLS via system OpenSSL.
  - Windows: `Invoke-CvcCMakeBuild` linking against bundled OpenSSL.
  - Once available, Qt6 recipe can drop `-DFEATURE_sql_mysql=OFF`
    and VTK/other consumers that use Qt6 SQL will get MySQL support.

### PostgreSQL client (`libpq`)

- **Why**: Enable libcvc to interact with PostgreSQL databases for
  structured scientific data. Qt6's `QPSQL` driver plugin needs
  `libpq` at runtime. Ubuntu runners already have it, but bundling
  our own ensures version consistency and reproducibility.
- **Upstream**: https://www.postgresql.org/ftp/source/ or the
  `libpq` subset extracted from the PostgreSQL source tree.
- **Build system**: Meson (since PG 16) or autotools/CMake wrappers.
  The libpq client library can be built standalone with Meson
  (`-Dlibpq=true`).
- **Platforms**: Linux, macOS, Windows.
- **Recipe notes**:
  - Source: PostgreSQL source tarball.
  - Build only the client library (`libpq`), not the server.
  - Once available, Qt6 recipe can drop `-DFEATURE_sql_psql=OFF`.

---

## WebAssembly runtimes

Adding a Wasm runtime lets libcvc and volrover3 load and execute
sandboxed Wasm modules — enabling plugin architectures, user-supplied
compute kernels, and portable extension code without native shared
libraries.

### Wasmtime (primary candidate)

- **Why**: Mature, well-documented, production-quality Wasm runtime
  from the Bytecode Alliance. Provides a C API (`wasmtime.h`) that
  can be consumed from C++ via the included header-only C++ bindings.
  Supports WASI (WebAssembly System Interface) for file I/O, env
  vars, and clocks inside Wasm modules.
- **Upstream**: https://github.com/bytecodealliance/wasmtime
- **Build system**: Rust/Cargo for the runtime itself; the C API is
  released as pre-built static/shared libraries + headers on GitHub
  Releases. Recipe can download the pre-built C API release artifact
  rather than building from source.
- **Platforms**: Linux (x86_64, aarch64), macOS (x86_64, arm64),
  Windows (x86_64).
- **Recipe notes**:
  - Source type: pre-built tarball from GitHub Releases (e.g.
    `wasmtime-v*-x86_64-linux-c-api.tar.xz`). SHA256 pinned.
  - No build step — just stage headers, `.a`/`.lib`/`.so`/`.dll`
    into the prefix.
  - Export a `wasmtime-config.cmake` with a `wasmtime::wasmtime`
    imported target.

### Wasmer (alternative)

- **Why**: Another major Wasm runtime with a C API. Supports multiple
  compilation backends (Cranelift, LLVM, Singlepass). Useful if
  consumers need a runtime with different performance characteristics
  or LLVM-backed AOT compilation.
- **Upstream**: https://github.com/wasmerio/wasmer
- **Build system**: Rust/Cargo; pre-built C API release artifacts
  available on GitHub Releases.
- **Platforms**: Linux, macOS, Windows.
- **Recipe notes**: Similar pattern to Wasmtime — download pre-built
  C API artifact, stage headers + libraries.

### WasmEdge (alternative)

- **Why**: High-performance Wasm runtime with WASI-NN support for
  ML inference inside Wasm modules. Could be interesting for scientific
  computing pipelines that combine Wasm plugins with tensor operations.
- **Upstream**: https://github.com/WasmEdge/WasmEdge
- **Build system**: CMake (C++ project). Can be built from source.
- **Platforms**: Linux, macOS, Windows.
- **Recipe notes**: CMake-based, so `cvc_cmake_build` /
  `Invoke-CvcCMakeBuild` should work.

### WAMR — WebAssembly Micro Runtime (lightweight alternative)

- **Why**: Extremely lightweight and embeddable Wasm runtime from
  the Bytecode Alliance. Targets resource-constrained environments.
  Good option if minimal footprint is more important than JIT
  performance.
- **Upstream**: https://github.com/bytecodealliance/wasm-micro-runtime
- **Build system**: CMake.
- **Platforms**: Linux, macOS, Windows (and many embedded targets).

### Recommendation

Start with **Wasmtime** as the primary Wasm runtime recipe:
- Most mature C API and ecosystem
- Well-defined WASI support
- Pre-built release artifacts simplify the recipe (no Rust toolchain
  needed)
- Backed by the Bytecode Alliance (same org as Firefox, Fastly)

Add Wasmer or WasmEdge later if consumers need alternative backends
or ML-specific features.

---

## Priority and sequencing

| Priority | Recipe | Blocked by | Notes |
|----------|--------|------------|-------|
| High | MariaDB Connector/C | — | Unblocks Qt6 MySQL plugin |
| High | Wasmtime C API | — | Enables Wasm plugin arch |
| Medium | libpq | — | Unblocks Qt6 PSQL plugin |
| Low | Wasmer | — | Alternative Wasm runtime |
| Low | WasmEdge | — | ML-focused Wasm runtime |
| Low | WAMR | — | Lightweight Wasm runtime |
