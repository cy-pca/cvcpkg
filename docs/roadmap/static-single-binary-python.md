# Static Single-Binary Python Distributions (Cosmo + WASM)

**Status:** proposed · extends **Phase 19 (Application Packaging — `cvcpkg bake`)** to Python-embedding.
**Motivation:** cvcpkg's end goal is a *single binary* with a fixed entry point and all assets + scratch space embedded. Can we ship a Python interpreter with native C-extension modules (notably `vtk-python`) statically linked into one such binary — an APE (Cosmopolitan) or a `.wasm`?

> **Note on provenance:** the deep-research pass for this doc was interrupted by a rate limit; this is the architect's direct assessment, grounded in the repos. Items flagged **[verify]** should be confirmed against upstream before committing to them.

---

## 0. Verdict

**Yes, it's possible — and it conforms cleanly to the `cvcpkg bake` single-binary goal — but the two cases differ sharply in scope and payoff.**

| Case | Verdict | What works | The wall |
|---|---|---|---|
| **Cosmo (APE) python + vtk-python** | ✅ **headless only** | non-rendering VTK (data model, filters, mesh/volume IO, the `vtkPythonUtil` bridge) + libcvc SDF/mesh/DSL, static-linked into one portable APE, assets in the zipos | **rendering** — Cosmopolitan has no window system / GL. Offscreen software GL (OSMesa) is a **[verify]** stretch, not the target. |
| **WASM VolRover + embedded interpreter (libcvc + vtk-python)** | ✅ **feasible, high effort** | libcvc(wasm) + VTK(wasm, WebGL2) + embedded CPython + vtk-python wrappers + VolRover UI in one `.wasm`, assets via MEMFS | VTK wasm **rendering backend** (today's `vtk` wasm build disables it), threads/`SharedArrayBuffer`, binary size |

**Worth it?** The **cosmo** case is worth it as a **portable headless tool** — a single `cvc.com` APE that runs libcvc's SDF/meshing/quality/DSL scripting anywhere (Linux/macOS/Windows/BSD) with zero install. The **wasm** case is the **high-payoff, ambitious** one — a browser VolRover you can script with an embedded Python, no install, one URL (directly serves the viz-lab/gym goal). Full VTK *rendering* is the deciding factor for both: a wall in cosmo, a real-but-solvable problem in wasm.

---

## 1. How it conforms to the `cvcpkg bake` single-binary goal

Both targets are **already static-only** in cvcpkg and already embed assets — this build case is the Python-embedding layer on top of the existing infra:

- **Cosmo:** `recipes/_common/env-cosmo.sh` points `CC/CXX` at the `cosmocc` frontend ("Cosmopolitan is static-only: APE binaries link everything into one file", `env-cosmo.sh:23`, `SOURCE_DATE_EPOCH=0`). The APE's **zipos** (the zip appended to the executable, addressable as `/zip/…`) is precisely "assets in the binary"; the APE `main()` is the fixed entry point; scratch is memory or the embedded fs. This is the `cvcpkg bake … cosmo APE variant` from **Phase 19**.
- **WASM:** `recipes/_common/env-wasm.sh` + the `emsdk` toolchain. Emscripten's `--embed-file` / `--preload-file` bakes assets into **MEMFS**; the `.wasm` (+ tiny `.js` loader, or a self-contained `.html`) is the single artifact; the module entry point is fixed; scratch is wasm linear memory.

So "static python + native modules in one file" is not a new distribution model — it's `bake` with a CPython + extensions payload.

---

## 2. The general mechanism (static libpython + statically-linked C extensions)

A normal C extension is a `.so` loaded by `dlopen`. To put it in a static single binary:

1. **Static `libpython`** — build CPython `--disable-shared` (→ `libpython3.x.a`). `python-build-standalone` (indygreg) already ships exactly this (static libpython + per-object archives) and is the foundation PyOxidizer builds on — the closest prior art to lean on.
2. **Statically-linked extension modules** — an extension normally exposes `PyInit_<name>`. To make it a *builtin*, register it in the interpreter's **inittab** before `Py_Initialize` (`PyImport_AppendInittab("<name>", PyInit_<name>)`) or via the classic `Modules/Setup` mechanism, and link its `.a` into the binary. No `dlopen`.
3. **Frozen stdlib** — embed the pure-Python stdlib as frozen bytecode (`Py_FrozenModules`) or in the zipos/MEMFS, so there's no filesystem dependency.
4. **Fixed entry point** — a `main()` that sets the inittab, initializes the interpreter, and runs the embedded entry script (from zipos/MEMFS).

**Tools** (mirror or reuse, don't reinvent): **python-build-standalone** (static libpython — the base), **PyOxidizer** (embeds interpreter + resources + in-memory imports into a Rust binary — the reference for "one binary + assets"), **Nuitka** (`--standalone/--onefile`, static libpython + embedded C extensions, compiles Python to C — closest to a fixed-entry-point app). PyInstaller is a self-extracting bundle, **not** a true static binary — reject for this goal.

### VTK's python wrappers, statically
- `vtk_module_wrap_python(…)` takes **`BUILD_STATIC`** and honors `BUILD_SHARED_LIBS=OFF` → it emits **static** per-module wrapper archives (`libvtkCommonCorePython.a`, …) + a static `libvtkWrappingPythonCore.a`, instead of the `.so`/`vtkmodules/*.so`.
- Each wrapped module's `PyInit_…` goes in the inittab; `import vtkmodules.vtkCommonCore` resolves to the builtin.
- **The one caveat:** `vtkPythonUtil` keeps a process-global registry (C++ class → Python wrapper type). It must be **linked exactly once** into the final binary. Static linking a *single* copy of `libvtkWrappingPythonCore.a` satisfies this by construction — the risk is only if two copies got linked. **[verify]** the static wrap path end-to-end (VTK's CI exercises shared wrapping far more than static).

---

## 3. Case A — Cosmo python + vtk-python (headless)

**Pipeline:** cosmocc-built static CPython (in the zipos) + inittab-registered static `vtk-python` wrapper archives for the **non-rendering** module set (CommonCore, CommonDataModel, FiltersCore/General/Sources, IOGeometry/IOXML/IOImage) + libcvc's own static libs + a `cvc` entry script, all `apelink`ed into one `cvc.com`. Assets/scratch in the zipos.

**Realistic scope:**
- **Works:** mesh/volume data structures, filters, file IO, and libcvc's SDF / isosurface / tetrahedralize / quality / the state_exec DSL — driven by an embedded Python. i.e. a portable, install-free scientific-data CLI.
- **The wall — rendering:** VTK rendering needs a GL context + windowing (Qt/X11/EGL); Cosmopolitan provides none. Options, all **[verify]** and all stretch: OSMesa/llvmpipe (pure-software GL — must compile under cosmocc, uncertain) for offscreen `render_png`; otherwise render is simply out of scope for the APE.
- **Blockers:** C++ exceptions/RTTI/`libstdc++` coverage under cosmocc (better than it was, still **[verify]** for a codebase as template-heavy as VTK); any transitive dep that wants `dlopen` or a windowing lib.

**Recommendation:** scope cosmo as **headless data/mesh/IO + DSL**. That alone is a compelling "one portable binary" deliverable. Treat rendering as explicitly out of scope (document it, like Phase 7.5 scoped BSD out).

---

## 4. Case B — WASM VolRover with an embedded interpreter (the payoff)

**Pipeline:** emscripten compiles the VolRover/libcvc C++ to wasm, **links a static wasm `libpython`** (à la Pyodide) + the static `vtk-python` wrappers + libcvc, embeds the entry script + assets via `--embed-file` (MEMFS), and emits one `.wasm` (+ loader). The embedded interpreter runs Python that drives libcvc + `vtk-python`; VTK renders to a WebGL2 canvas.

**The pieces exist:**
- **CPython on wasm** — Pyodide (CPython built with emscripten) is proof; CPython's own `wasm32-emscripten` target exists. Extensions can be **static-linked into the main module** (vs emscripten "side modules" which are dynamic) — static is what we want for one artifact.
- **VTK on wasm** — VTK has a WebAssembly rendering path (WebGL2 via emscripten GL; the newer WebGPU backend). **[verify]** the maturity for our module set.
- **Assets/threads** — MEMFS bakes assets; threads need `SharedArrayBuffer` + COOP/COEP headers (or run single-threaded).

**Where it breaks / the real work:**
1. **The current `vtk` wasm recipe disables exactly what we need** — `recipes/vtk/build-wasm.sh` sets `VTK_WRAP_PYTHON=OFF` and `VTK_MODULE_ENABLE_VTK_RenderingOpenGL2=NO`. A wasm `vtk-python` needs a **new** wasm build with `WRAP_PYTHON=ON` **and** a wasm rendering backend (WebGL2/WebGPU) enabled. This is the crux, not a footnote.
2. **Binary size** — VTK is huge; a naive wasm link is enormous. Requires trimming to a minimal module set + `-Oz` + wasm-opt + lazy assets.
3. **Static-vs-side-module** extension linking under emscripten/Pyodide (**[verify]** which gives a single self-contained artifact).

**Recommendation:** high-value, multi-week. Sequence it *after* the headless static-python foundation (Case A shares the static-wrapper + inittab machinery), and after settling the VTK-wasm-rendering question.

---

## 5. Phased plan + concrete cvcpkg pieces

1. **Static-python foundation** — a `--link static` / static build mode for the `pythonXXX` recipes (static `libpython.a` + object archives, mirroring python-build-standalone), and a **`freeze`/`embed` packaging step** in the builder (register the inittab, freeze the stdlib, embed the entry script + assets). This is the shared substrate for both cases and for `cvcpkg bake`.
2. **`vtk-python-static-cpXXX`** — a static wrapper build (`BUILD_STATIC` + `BUILD_SHARED_LIBS=OFF`), validating the single-`vtkPythonUtil`-registry invariant. Reuses the multi-python cpXXX naming.
3. **Case A — `cvc-cosmo` APE** — a `bake`-style recipe: cosmocc static CPython + static vtk-python(headless) + libcvc + entry script → `cvc.com`, assets in zipos. Depends on the existing cosmo toolchain (done) — no new toolchain.
4. **Case B — `volrover-wasm`** — a **new wasm `vtk-python` build** (WRAP_PYTHON=ON + WebGL2) + wasm libpython + libcvc(wasm) + VolRover → one `.wasm`. Depends on emsdk (exists) + the wasm-VTK-rendering decision.

**Dependency on other roadmap items:** the **pinned native toolchain** ([`hermetic-native-toolchain.md`](hermetic-native-toolchain.md)) makes the *linux* static build reproducible, but the **cosmo and wasm toolchains already exist** (`cosmocc`, `emsdk`) — so this case is **not blocked** on the native-toolchain work; it proceeds in parallel. It *does* build directly on **Phase 19 (`cvcpkg bake`)** as the packaging mechanism.

---

## 6. Risks / open questions

1. **VTK static python wrappers** (`BUILD_STATIC`) end-to-end + the single-`vtkPythonUtil`-registry invariant — **[verify]** (VTK CI favors shared wrapping).
2. **Cosmo C++/`libstdc++`/RTTI coverage** for template-heavy VTK, and whether offscreen software GL (OSMesa) is even buildable under cosmocc — decides if cosmo is headless-only (assume yes).
3. **VTK wasm rendering maturity** (WebGL2 vs WebGPU) for our module set — the crux of Case B; the current `vtk` wasm recipe disables it.
4. **wasm binary size** — needs an aggressive minimal-module VTK + `-Oz`/wasm-opt; may force a curated "viz-lab" module subset.
5. **Static vs side-module** extension linking under Pyodide/emscripten for a single self-contained artifact.
6. **Threads** — `SharedArrayBuffer`/COOP-COEP vs single-threaded wasm.
7. **Which foundation** — build on `python-build-standalone` (static libpython) directly, or lean on Nuitka/PyOxidizer for the freeze/embed step.

**Bottom line:** cosmo headless is a near-term, high-confidence win that directly realizes the "one portable binary" vision; wasm VolRover is the ambitious flagship, gated mainly on VTK's wasm rendering and size. Both are `cvcpkg bake` with a CPython payload — not a new distribution model.
