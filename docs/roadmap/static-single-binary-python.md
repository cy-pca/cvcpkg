# Static Single-Binary Python Distributions (Cosmo + WASM)

**Status:** proposed · extends **Phase 19 (Application Packaging — `cvcpkg bake`)** to Python-embedding.
**Motivation:** cvcpkg's end goal is a *single binary* with a fixed entry point and all assets + scratch space embedded. Can we ship a Python interpreter with native C-extension modules (notably `vtk-python`) statically linked into one such binary — an APE (Cosmopolitan) or a `.wasm`?

> **Provenance (2026-07):** this revision replaces the earlier rate-limited draft. Three grounded investigations — one on the general static-embed mechanism, one on the Cosmopolitan case, one on the WASM case — now back every claim against the repos and upstream. Where the earlier draft said **[verify]**, this doc records the confirmed answer and flags only the genuinely open items. The three reports are cited inline as **[R1]** (general mechanism), **[R2]** (cosmo), **[R3]** (wasm).

---

## 0. Verdict

**Possible: yes, on both targets. The CPython machinery is standard and solid; the risk lives entirely in VTK and in rendering.** The two cases diverge sharply on scope and payoff.

| Case | Possible? | Worth it? | What works | The wall |
|---|---|---|---|---|
| **Cosmo (APE) python + vtk-python** | ✅ **headless only** | ✅ yes — as a portable install-free scientific CLI | non-rendering VTK (CommonCore, CommonDataModel, CommonExecutionModel, Filters{Core,General,Sources,Geometry}, IO{Geometry,XML,Image,Legacy}, the `vtkPythonUtil` bridge) + libcvc SDF/mesh/quality/`state_exec` DSL, static-linked into one fat (x86_64+aarch64) APE, assets in the zipos | **rendering** — Cosmopolitan ships *no* window system, GL driver, EGL, or portable `dlopen`. There is literally nothing for `vtkRenderingOpenGL2` to bind to. OSMesa/llvmpipe software GL is unproven under cosmocc — a research spike, not the deliverable **[R2]** |
| **WASM VolRover + embedded interpreter (libcvc + vtk-python)** | ✅ **feasible with effort** | ⚠️ high-payoff flagship, multi-month, front-loaded risk | libcvc(wasm) + VTK(wasm, WebGL2 *or* WebGPU) + embedded CPython + static vtk-python wrappers + a browser UI in one `.wasm`, assets via MEMFS | the VTK **wasm rendering backend** (today's recipe disables it), **`VTK_WRAP_PYTHON` on wasm is undocumented upstream and VTK is absent from Pyodide**, GPU volume rendering correctness, threads/`SharedArrayBuffer`, binary size **[R3]** |

**Load-bearing reasons.**
- Static linking C extensions into one `libpython`-embedding binary is a **fully supported, standard CPython capability** — it is how CPython builds its own stdlib extensions in every `--disable-shared`/cross configuration. The hard part is never "can Python do it"; it is (a) getting each third-party build system to emit a **static archive with `PyInit_*` still exported** instead of a `.so`, (b) **registering** each module in the inittab, and (c) the one-copy-only invariant for C++ global registries like VTK's `vtkPythonUtil` **[R1]**.
- **Cosmo is worth it** because a single `cvc.com` that runs libcvc's SDF/meshing/quality/DSL + headless VTK data/IO on Linux/macOS/Windows/BSD with zero install is a clean, near-term realization of the single-binary goal — and it reuses the static-wrapper + inittab + zipos machinery `cvcpkg bake` needs anyway. Rendering is the wrong ask for cosmo; that is the wasm case's job **[R2]**.
- **WASM is worth pursuing but honestly gated:** every individual piece exists (emsdk infra in-repo, several C deps already build to wasm, CPython-on-wasm proven by Pyodide, VTK renders on wasm), but *the specific combination VolRover needs* — VTK Python wrappers compiled to wasm **with volume rendering**, statically embedded in a CPython interpreter inside a C++ app — is unproven at every seam, and the current recipes point the opposite way (wasm VTK is headless + un-wrapped; vtk-python is linux/windows-only shared libs) **[R3]**.

Full VTK **rendering** is the deciding factor for both: a hard wall in cosmo, a real-but-solvable-and-fragile problem in wasm.

---

## 1. How it conforms to the `cvcpkg bake` single-binary goal

Both targets are **already static-only** in cvcpkg and already embed assets. This build case is the Python-embedding layer on top of existing infra — not a new distribution model.

- **Cosmo — the APE zipos.** `recipes/_common/env-cosmo.sh` points `CC/CXX` at the `cosmocc` frontend and forces `BUILD_SHARED_LIBS=OFF` / `CVC_LINK=static` — the header comment states plainly *"Cosmopolitan is static-only: APE binaries link everything into one file"* (`env-cosmo.sh:23`). It also sets `SOURCE_DATE_EPOCH=0` and `LC_ALL=C` (the only in-repo `SOURCE_DATE_EPOCH` precedent). The APE's **zipos** — a zip appended to the executable, addressable at runtime as `/zip/…` — *is* "assets in the binary"; the APE `main()` is the fixed entry point; scratch is memory or the embedded fs. This is the `cvcpkg bake … cosmo APE variant` under **Phase 19** (`CVCPKG-ROADMAP.md:255`); the `cvpkg` APE bootstrap (`CVCPKG-ROADMAP.md:1212–1245`) is the same path for the tool itself **[R2]**.
- **WASM — MEMFS.** `recipes/_common/env-wasm.sh` sources the host env, activates emscripten, then forces every wasm build static (`BUILD_SHARED_LIBS=OFF`, `CVC_LINK=static`) and injects `-DCMAKE_TOOLCHAIN_FILE=…/Emscripten.cmake`. Emscripten's `--embed-file` / `--preload-file` bakes assets into **MEMFS**; the `.wasm` (+ a small `.js` loader, optionally one self-contained `.html`) is the single artifact; `main()` is the fixed entry point; scratch is wasm linear memory. `recipes/_common/cvc_wasm_run.sh` already runs emscripten output under `node` as a smoke test **[R3]**.

So "static python + native modules in one file" is `bake` with a CPython + extensions payload — the embedding model the goal wants is already the documented target for both platforms; it just has not been exercised with a CPython+VTK payload.

---

## 2. The general mechanism (static libpython + statically-linked C extensions)

Keep two orthogonal axes separate **[R1]**:

- **Axis A — `libpython` itself: shared vs static.** `--enable-shared` → `.so`/`.dylib`; `--disable-shared` → `libpython3.x.a`.
- **Axis B — each *extension module*: builtin (statically linked, in the inittab) vs shared (`.so` via `dlopen`).**

A single static binary needs static on **both** axes. `recipes/_common/build-python.sh` **already flips Axis A per platform**: native builds pass `--enable-shared`, but the cross targets (`wasm`/`wasi`/`cosmo`) pass `--disable-shared --host=…`. So the static-`libpython` foundation already exists in-repo for exactly the two targets in question. The missing piece is the **Axis-B machinery for third-party extensions**.

### 2.1 Turning a `.so` extension into a builtin

An ordinary extension `.so` and a builtin are the *same C code*; the difference is packaging + registration **[R1]**:

1. **Build a static archive, not a shared object**, keeping `PyMODINIT_FUNC PyInit_<name>` externally visible (SWIG marks it `extern "C" PyMODINIT_FUNC`, so it survives — just don't strip/localize it).
2. **Register it** before `Py_Initialize`, either build-time or embed-time:
   - *Build-time* — `Modules/Setup[.local]`: CPython's `makesetup` writes the module into `Modules/config.c`'s `_PyImport_Inittab[]`. This is how the whole stdlib becomes builtins in a `--disable-shared` build. It couples the extension build into the CPython build — awkward for a package manager building CPython and VTK as separate recipes.
   - *Embed-time* — **`PyImport_AppendInittab("<name>", PyInit_<name>)`** (or `PyImport_ExtendInittab` for a whole array), called from the host `main()` **before** `Py_Initialize()`. This keeps the CPython recipe and the extension recipes **decoupled** — the launcher `main()` is the single stitch point. **This is the recommended mechanism for the cvcpkg `bake` launcher.** It is what PyOxidizer/Nuitka-style embedders and CPython's own `Programs/_testembed.c` use.
3. **Force-load the archive at final link** (`-Wl,--whole-archive` / `-force_load` / `/WHOLEARCHIVE`) so objects whose only reference is the runtime inittab function pointer are not dropped. (Referencing each `PyInit_*` from the launcher — which putting them in `PyImport_AppendInittab` does — itself pulls the object.)
4. **One copy only** of any shared C++ global registry (see VTK below).

### 2.2 Frozen / embedded pure-Python

The inittab handles *C* extensions; the pure-Python stdlib + entry script still need a source with no filesystem **[R1]**:

- **Zip/embedded-FS import** — stdlib in the APE **zipos** (`/zip/…`) or emscripten **MEMFS**, `sys.path` pointed at it. Lowest effort; already fits cvcpkg's asset-in-binary model.
- **Frozen bytecode** — `PyImport_FrozenModules` / `Tools/freeze` / (3.11+) deepfreeze compiles `.py` into a C array linked in. Fully filesystem-free; heavier to wire.
- **Pragmatic combination for cvcpkg:** zipos/MEMFS stdlib + a frozen (or zipos) **entry script** — the frozen `__main__` cleanly gives the "fixed entry point," with full `Py_FrozenModules` available if zero embedded-FS dependency is ever required.

### 2.3 VTK's python wrappers, statically

**VTK does support static python wrapping** — confirmed against the upstream `vtk_module_wrap_python` CMake API **[R1]**:

- The function takes **`BUILD_STATIC`**, which **"Defaults to `${BUILD_SHARED_LIBS}`."** Building VTK with `-DBUILD_SHARED_LIBS=OFF` flips the wrappers to static automatically.
- In static mode VTK emits **static archives** plus a generated header `<TARGET>.h` exposing **`void <TARGET>_load()`** which *"will add all Python modules created by this call to the imported module table"* — i.e. VTK's own bulk `PyImport_AppendInittab`. In shared builds the same `_load()` exists as a **no-op**, so launcher code can call it unconditionally. The `DEPENDS` argument threads `_load()` registration through the module dependency graph, so one top-level `_load()` cascades.

The static VTK-Python launcher shape: link the static wrapper archives + `libvtkWrappingPythonCore.a` + the static C++ `libvtk*.a`, call the generated `_load()` before `Py_Initialize`, and `import vtkmodules.vtkCommonCore` resolves to the builtin — the exact static analogue of the current shared `vtk-python-cp313` recipe.

**The caveats (the honest part) [R1]:**
1. **Upstream's own hedge:** the same doc states *"shared modules with a static build is not completely supported."* VTK CI and nearly all users exercise the **shared** wrap path; the static path is real but comparatively under-tested. Expect to be an early adopter and to debug the wrap generator's static output. This is the single biggest "feasible vs. stretch" flag for VTK.
2. **`vtkPythonUtil` is a process-global singleton** — a registry mapping C++ class identity → the live Python wrapper (so a `vtkActor*` returned from C++ finds its one canonical Python wrapper), living in `libvtkWrappingPythonCore`. Static linking must guarantee **exactly one copy** of that archive's objects in the binary. Two copies ⇒ two registries ⇒ objects wrapped through one path invisible to the other ⇒ silent identity/RTTI breakage. **Mitigation is structural:** link `libvtkWrappingPythonCore.a` **once**, at the final link, visible to all wrapper archives — never bundle it into an intermediate archive. A single final link with uniform flags (`-frtti`, consistent `-fvisibility`) also keeps `dynamic_cast`/`typeid` coherent across the boundary. The recipe's existing insistence that the wrapper build track the C++ build "byte-for-byte" on flags is exactly the discipline that keeps the registry coherent.
3. **`__file__` / package layout** — builtins have no on-disk file; keep the pure-Python `vtkmodules/*.py` in the zipos/MEMFS while the `PyInit_*` come from the inittab.
4. **The whole C++ VTK must also be static and cosmo/wasm-buildable** — the neighboring gate (template-heavy C++/RTTI under cosmocc; a rendering backend under emscripten).

### 2.4 Prior art — mirror the pattern, don't adopt the tool

| Tool | True static single binary? | C-extension handling | Fit |
|---|---|---|---|
| **python-build-standalone** (astral-sh, ex-indygreg) | Ships the *ingredients* — static `libpython.a` + per-object archives + statically-linked stdlib-extension deps. Fully-static variants exist but docs warn they are **"extremely brittle"** and cannot `dlopen`. | Stdlib extensions statically linked against their deps. | **Best foundation to lean on / mirror.** cvcpkg's own `--disable-shared` cross build already reproduces its static recipe in-house. |
| **PyOxidizer** | Yes — interpreter + resources + in-memory imports in a Rust binary; built *on* python-build-standalone. | Built-in (static into libpython) vs standalone (`dlopen`ed); in-memory `.so` load **rejected off-Windows**. | **The reference architecture** for "one binary + embedded resources + fixed entry point." Borrow the model (inittab + embedded resources) without adopting Rust. Now in upstream maintenance mode. |
| **Nuitka** `--standalone/--onefile` | `--onefile` is a self-extracting bundle (unpacks to temp dir + `dlopen`), **not** a static binary. | C extensions included as `.so`/`.pyd`. Static-libpython support limited (Anaconda/MSYS2); PBS static lib "currently unusable." | **Weak fit** — fails the dlopen-free constraint. Value is conceptual (compile entry Python → C). |
| **PyInstaller / py2exe / cx_Freeze** | **No** — self-extracting temp dir + `dlopen`. | Bundles + extracts `.so`. | **Reject** for this goal. |

**Closest to cvcpkg's target:** python-build-standalone (static ingredients) + PyOxidizer's model (inittab-registered builtins + embedded resources + fixed `main`). cvcpkg already has its own from-source `--disable-shared` CPython recipe, its own asset embedding (zipos/MEMFS), and its own `bake` step — so the only missing surface is **(a)** emitting third-party extensions as static archives and **(b)** a launcher that inittab-registers them. Strictly smaller than adopting PyOxidizer/Nuitka wholesale, and it avoids their platform assumptions (neither targets Cosmopolitan or a wasm main-module embed) **[R1]**.

---

## 3. Case A — Cosmo python + vtk-python (headless)

**Pipeline:** cosmocc-built static CPython 3.13 (in the zipos) + inittab-registered static `vtk-python` wrapper archives for the **non-rendering** module set + libcvc's static libs + a `cvc` entry script, all `apelink`ed into one fat (x86_64+aarch64) `cvc.com`. Assets/scratch in the zipos. Depends on **no new toolchain** — `cosmocc` is already a recipe.

**What cvcpkg already has [R2]:**
- **Toolchain is real and current** — `recipes/cosmocc/recipe.yaml` pins **cosmocc 4.0.2** (GCC 14 + Clang 19 + Cosmopolitan Libc + LLVM libcxx + compiler-rt + OpenMP, x86_64 & aarch64), registered as the `cross_toolchain` provider for `[cosmo]`.
- **A non-trivial cosmo stack already builds** — `build-cosmo.sh` scripts exist for ~30 recipes (`zlib, xz, gmp, mpfr, gsl, clapack, levmar, fftw3, nfft3, hdf5, tiff, libjpeg-turbo, libwebp, libgeos, ffmpeg, …`, plus `python311/312/313/313t`). Conspicuously **absent: any VTK, Qt, GL, or vtk-python cosmo build.**

**Realistic scope — the subset that works [R2]:**
- **Yes:** `CommonCore`, `CommonDataModel`, `CommonExecutionModel`, `Filters{Core,General,Sources,Geometry}`, `IO{Geometry,XML,Image,Legacy}`, and the `vtkPythonUtil`/`vtkWrappingPythonCore` bridge — mesh/volume data structures, filters, and file IO, scriptable from the embedded interpreter, plus libcvc's SDF / isosurface / tetrahedralize / quality / `state_exec` DSL. A genuinely useful, install-free, cross-OS scientific-data CLI.
- **No:** `RenderingOpenGL2`, `RenderingUI`, `Interaction{Style,Widgets}`, `GUISupportQt`, `RenderingQt` — anything needing a GL context or window.

**The wall — rendering [R2]:** Cosmopolitan is a portable **libc**; it has no windowing, X11, Wayland, EGL, GPU driver, or portable `dlopen` to reach a system GL at runtime. There is nothing for `vtkRenderingOpenGL2` to bind to. (Confirmed by omission: even the *wasm* recipe — which at least has WebGL2 — disables `RenderingOpenGL2`; cosmo has strictly less.) Offscreen software GL (OSMesa/llvmpipe) would need all of Mesa + LLVM's software rasterizer — a large, `dlopen`-happy, driver-loading codebase — to statically link under cosmocc; there is no OSMesa/EGL/llvmpipe recipe anywhere in cvcpkg. **Possible-on-paper, unproven, high-effort — a research spike, not a plan item.**

**Steps:**
1. New static **`vtk-python` cosmo build** — `build-cosmo.sh` with `-DBUILD_SHARED_LIBS=OFF` (→ `BUILD_STATIC` wrappers + `_load()` header), module-pruned to the headless set. Net-new: today's `vtk-python-cp313` matrix is **linux + windows only**, and the base `vtk` matrix has **no cosmo entry**.
2. **pycvc / libcvc** as static archives keeping `PyInit_*` exported, linked against static `libcvc.a`.
3. A **launcher `main()`** calling `vtk…Python_load()` + `PyImport_AppendInittab("_pycvc", …)` before `Py_Initialize`, then running the zipos entry script.
4. Force-load the wrapper + `_pycvc` archives; link `libvtkWrappingPythonCore.a` exactly once.
5. `apelink` into one fat `cvc.com`; stdlib + `vtkmodules/*.py` + entry script in the zipos.

**Blockers, in priority order [R2]:**
1. **VTK's template-heavy C++ building under cosmocc at all** — the largest unknown; nothing near VTK's C++ weight has been done on cosmo. cosmocc *does* bundle LLVM libcxx + compiler-rt so exceptions/RTTI are supported, but the faked `-DCMAKE_SYSTEM_NAME=Linux` in `env-cosmo.sh` (cosmo ships no CMake toolchain file yet) means VTK's platform detection will believe it is on Linux and may mis-select GL/threading backends.
2. **Stock CPython 3.13.3 linking under cosmocc** — the cvcpkg recipe asserts a plain `./configure --host=x86_64-cosmo --disable-shared` works, but upstream's *proven* cosmo Python is a **patched 3.11.4** (ahgamut/superconfigure; C extensions via `Modules/Setup`, no `dlopen`). No built cosmo-python artifact or CI evidence exists in-repo. vtk-python must be ABI-matched to whatever cosmo CPython actually links (cp313-locked).
3. **Static VTK wrappers + the single-`vtkPythonUtil`-registry invariant** — under-exercised upstream (§2.3).
4. **No cosmo matrix entry** for `vtk`/`vtk-python` — net-new recipe work.
5. **Rendering out of scope by construction** — document it explicitly (treat like the scoped-out BSD in Phase 7.5).

**Recommendation:** scope cosmo as **headless data/mesh/IO + DSL**. That alone is a compelling one-portable-binary deliverable and a high-confidence near-term win.

---

## 4. Case B — WASM VolRover with an embedded interpreter (the flagship)

**Pipeline:** emscripten compiles the VolRover/libcvc C++ to wasm, statically links a wasm `libpython` (Pyodide-style) + static `vtk-python` wrappers + libcvc, bakes the entry script + assets via `--embed-file`/MEMFS, and emits one `.wasm` (+ small `.js`, optionally one `.html`). The embedded interpreter runs Python driving libcvc + `vtk-python`; VTK renders to a canvas. The single-artifact/asset mechanics are the *easy* part — that is just `bake` with a CPython payload. Everything below is where it breaks **[R3]**.

**What cvcpkg already has [R3]:**
- **Toolchain** — `recipes/emsdk/recipe.yaml` pins **emsdk 5.0.7**, pre-populates the ports cache for offline builds, registered as `cross_toolchain` for `[wasm]`.
- **Libraries already ported** — `build-wasm.sh` exists for `zstd, ffmpeg, hdf5, openssl, libjpeg-turbo, clapack, levmar, fftw3, qtmultimedia, skia, python312, vtk` — a good chunk of libcvc's C closure.
- **CPython on wasm** — `recipes/python312/build-wasm.sh` cross-compiles **3.12.10** `--host=wasm32-emscripten --disable-shared`. The recipe's own note — *"Cross-compilation targets build libpython as a static archive since extension modules (.so) are uncommon"* — **is the entire design tension**: to embed vtk-python, extensions are the whole point and must be static builtins.
- **VTK on wasm — exists but headless and un-wrapped.** `recipes/vtk/build-wasm.sh` builds VTK 9.5 to wasm but turns off exactly what this project needs: `VTK_WRAP_PYTHON=OFF`, `VTK_ENABLE_WRAPPING=OFF`, `RenderingOpenGL2=NO`, `RenderingUI=NO`. So today's wasm VTK is a data-model/filters/IO build with **no renderer and no Python** — useful for headless number-crunching, useless for VolRover. (The only wasm patch present is a one-line `<cstdlib>` include fix.)
- **vtk-python does NOT target wasm** — `vtk-python-cp312` matrix is **linux + windows only**, and it uses the shared-library model (`libvtkWrappingPythonCore…so`, `vtkmodules/*.so`) — the opposite of wasm static linking.
- **libcvc has no wasm build** — no wasm/emscripten preset in `CMakePresets.json`; C++20 + CUDA-default (CUDA must go off); uses `std::thread`/`std::filesystem` in ~127 spots plus an `xmlrpc` networking module (`XmlRpcSocket.cpp`).
- **VolRover itself is a separate repo** (extracted `volrover3` viz-lab/gym), historically a **Qt desktop app**. The Qt-free VTK scene graph is `src/cvcGL` (→ `pycvc-gl`), whose `CMakeLists.txt` needs `RenderingCore RenderingVolume RenderingVolumeOpenGL2 RenderingOpenGL2 RenderingAnnotation RenderingFreeType InteractionStyle` — including **`RenderingVolumeOpenGL2`** (GPU volume ray-casting), the hardest thing to get right on wasm.

**Where it breaks, ranked [R3]:**
1. **VTK-wasm-Python-with-rendering does not exist yet.** You must create a *new* wasm VTK build with `VTK_WRAP_PYTHON=ON` + `BUILD_STATIC` + a render backend **on** — undoing every disable in `build-wasm.sh` and adding static Python wrapping that upstream does not test on wasm. VTK's current build guide documents rendering via **WebGPU** (`-DVTK_ENABLE_WEBGPU=ON`, tested with Emscripten 4.0.20) and no longer emphasizes the older **WebGL2/`RenderingOpenGL2`** path that cvcGL is written against. And **`VTK_WRAP_PYTHON` on wasm is undocumented; VTK is absent from Pyodide's package list.** This is the make-or-break go/no-go spike.
2. **GPU volume rendering correctness.** `RenderingVolumeOpenGL2` → WebGL2 3D-textures or a WebGPU compute path; the ray-cast mapper is heavy and least-tested on wasm — most likely to render wrong or slow, and it is central to a *volume* viewer.
3. **Binary size.** VTK+CPython+libcvc naively links to tens of MB+. Needs a curated minimal module subset, `-Oz`, `wasm-opt`, dead-code stripping, lazy/streamed assets. Gates shippability.
4. **libcvc portability.** CUDA off; `xmlrpc` raw sockets unavailable under emscripten (websocket shim or removal); `std::thread` forces the threaded build (`VTK_WEBASSEMBLY_THREADS=ON` + WebWorkers → **SharedArrayBuffer** + **COOP/COEP** headers, ruling out some CDNs/`file://`) or a single-threaded refactor; HDF5 IO through MEMFS only.
5. **The VolRover UI.** The Qt desktop app is a separate, un-ported repo. Qt-for-WebAssembly exists (helpers already vendored) but porting a real Qt app is its own multi-week effort — **better to rebuild the frontend as HTML/JS driving the canvas + calling into the embedded interpreter**, which is the more sensible viz-lab/gym design anyway.

**Version caveat cvcpkg must resolve [R3]:** CPython's Emscripten support was **dropped in 3.13** and **restored as PEP 11 Tier-3 only in 3.14** (PEP 776/783). cvcpkg pins **3.12** — plausible (that era worked) but *not* the blessed, actively-tested target. Building wasm vtk-python wrappers against 3.12 risks fighting bugs upstream already fixed. **Move to CPython 3.14+ (tier-3, 25+ emscripten libc fixes) or lean on Pyodide's patched CPython** (the most battle-tested wasm interpreter, current line ~0.29 tracking 3.13/3.14). Note this diverges from the cosmo case, which is cp313-locked — the two targets may not share a CPython version.

**Static vs side modules [R3]:** static builtins (inittab, linked into the main module) are the right and necessary model for one artifact. Emscripten side modules (`-sSIDE_MODULE`) are how Pyodide normally ships packages, but they defeat the single-`.wasm` goal, add cross-module JS call overhead, and have known limits (SDL/GL window creation fails from a side module, emscripten #24106).

**Rough effort:** on the order of **several months of focused work**, front-loaded on the VTK-wasm-Python-rendering spike (step 1), the go/no-go gate. **Fallback if the spike fails or volume rendering proves unusable:** the Kitware/trame model — VTK.wasm doing *rendering* in the browser while heavy compute stays server-side — which sidesteps the embedded-interpreter ambition entirely.

---

## 5. General recipe — the load-bearing steps (shared substrate)

The static-embed happy path, independent of platform **[R1]**:

1. **Static `libpython.a`** — CPython `--disable-shared` (repo already does this for cross targets). All stdlib C extensions become builtins automatically.
2. **Static third-party extension archives** — VTK rebuilt `-DBUILD_SHARED_LIBS=OFF` (→ per-module `.a` + `_load()` header + static `libvtkWrappingPythonCore.a`); pycvc's SWIG `_wrap.cxx` compiled to an archive keeping `PyInit__pycvc` exported, against static `libcvc.a`.
3. **A launcher `main()`** (the fixed entry point) — before `Py_Initialize`: cascading VTK `_load()` + `PyImport_AppendInittab` for each SWIG module; then `Py_Initialize()`; then run the embedded entry script (optionally a frozen `__main__`).
4. **Force-load the archives** at final link; link `libvtkWrappingPythonCore.a` exactly once.
5. **Embed stdlib + assets** in zipos (cosmo) / MEMFS (wasm), or frozen; point `sys.path` at the embedded FS.
6. **Link with the platform frontend** — cosmocc → `.com` APE; emcc → `.wasm`.

**The hard parts (where it actually breaks) [R1]:**
- **Init-registration coverage & naming** — every wrapped module needs `PyInit_*` in the inittab under the *exact* import name (`vtkmodules.vtkCommonCore`, not `vtkCommonCorePython`). Miss one → `ModuleNotFoundError` at import, not link time.
- **dlopen-free-ness of the whole transitive closure** — any dep that internally `dlopen`s a plugin (image codecs, HDF5 plugins, Qt platform plugins, GL driver loading) breaks; must be compiled in statically or disabled. This is *why* cosmo is scoped headless — rendering pulls in exactly the `dlopen`/windowing machinery a static APE cannot provide.
- **The single-`vtkPythonUtil`/RTTI-registry invariant** across statically-linked TUs (§2.3) — one link unit, uniform flags.
- **`--whole-archive` bloat** — force-loading whole VTK wrapper archives drags in far more than needed; curate a minimal module set.
- **Static `libpython` brittleness** — python-build-standalone's own warning applies to cvcpkg's static CPython too; budget for a real embedded-import test matrix.

---

## 6. Phased plan + concrete cvcpkg pieces

1. **Static-python foundation (shared substrate).** A `--link static` / static build mode for the `pythonXXX` recipes (static `libpython.a` + object archives, mirroring python-build-standalone), plus a **`freeze`/`embed` packaging step** in the builder (register the inittab, freeze the stdlib / embed the entry script + assets). Substrate for both cases and for `cvcpkg bake`. **Decide the CPython version split here:** cp313 for cosmo (matching the recipe), CPython **3.14+ or Pyodide's CPython** for wasm.
2. **`vtk-python-static-cpXXX`.** A static wrapper build (`BUILD_STATIC` + `BUILD_SHARED_LIBS=OFF`), packaging the `.a` + `_load()` header instead of `.so`/`vtkmodules/*.so`, validating the single-`vtkPythonUtil`-registry invariant end-to-end early. Today's `vtk-python-cp313/build.sh` is hard-coded to `BUILD_SHARED_LIBS=ON` — this is a static sibling. **De-risk on linux static first** — the cheapest place to prove `BUILD_STATIC` + `_load()` before cosmo/wasm.
3. **Case A — `cvc-cosmo` APE.** A `bake`-style recipe: cosmocc static CPython + static vtk-python (headless subset) + libcvc + entry script → fat `cvc.com`, assets in zipos. Depends on the existing cosmo toolchain (done) — **no new toolchain**. First prove the two cosmo blockers: (a) VTK's C++ compiles under cosmocc, (b) stock 3.13.3 links under cosmocc.
4. **Case B — `volrover-wasm`.** A **new wasm `vtk-python` build** (`WRAP_PYTHON=ON` + `BUILD_STATIC` + a render backend) + wasm libpython + libcvc(wasm) + a UI → one `.wasm`. **Decide WebGL2 vs WebGPU first** (WebGL2 = matches cvcGL today but the deprecating path; WebGPU = strategic but requires adapting cvcGL's module set + `vtk_module_autoinit` object-factory registration and a modern-browser floor). Depends on emsdk (exists) + the rendering decision. Sequence *after* steps 1–2; run the VTK-wasm-Python-rendering spike as the go/no-go gate before committing.

**Dependency on other roadmap items.** The **pinned native toolchain** ([`hermetic-native-toolchain.md`](hermetic-native-toolchain.md) / [`native-toolchain-spec.md`](native-toolchain-spec.md)) makes the *linux* static build reproducible, but the **cosmo and wasm toolchains already exist** (`cosmocc` 4.0.2, `emsdk` 5.0.7) — so this case is **not blocked** on the native-toolchain work and proceeds in parallel. It builds directly on **Phase 19 (`cvcpkg bake`)** as the packaging mechanism.

---

## 7. Risks / open questions for the user

1. **VTK static python wrappers end-to-end + the single-`vtkPythonUtil`-registry invariant** — upstream states *"shared modules with a static build is not completely supported"* and CI favors shared wrapping. Prove `BUILD_STATIC` + `_load()` + a single registry early on *some* platform (linux static first is the cheapest place to de-risk before cosmo/wasm).
2. **Does VTK's template-heavy C++ compile under cosmocc at all?** The single biggest cosmo unknown — nothing near VTK's C++ weight has been built on cosmo, and the faked `CMAKE_SYSTEM_NAME=Linux` may mis-select backends. **Decision:** are we willing to fund that spike, or scope cosmo to libcvc-only (no VTK) as a fallback?
3. **Does stock CPython 3.13.3 link under cosmocc**, or must we adopt ahgamut's patched-CPython lineage (proven at 3.11.4)? No in-repo artifact/CI proves the recipe's claim today.
4. **Which CPython for wasm** — the pinned 3.12, upstream **3.14+** (tier-3), or **Pyodide's CPython**? This decides how much emscripten-libc breakage we fight and whether cosmo and wasm share a version.
5. **WebGL2 vs WebGPU** for VTK wasm rendering — cvcGL is written against WebGL2 (`RenderingOpenGL2`/`RenderingVolumeOpenGL2`), but Kitware is steering wasm toward WebGPU. Committing to WebGL2 rides a deprecating path; WebGPU means adapting cvcGL and requiring a modern-browser floor.
6. **GPU volume rendering on wasm** — the fragile, central feature for VolRover; if it proves unusable, do we accept the trame server-compute/client-render fallback instead of a self-contained `.wasm`?
7. **wasm binary size / threads** — an aggressive minimal-module VTK + `-Oz`/`wasm-opt` may force a curated "viz-lab" module subset; threads impose SharedArrayBuffer + COOP/COEP deployment constraints (or a single-threaded refactor of libcvc's ~127 `std::thread` sites).
8. **The VolRover UI** — port the Qt desktop app to Qt-for-WebAssembly, or rebuild the frontend as HTML/JS + embedded-Python scripting over the canvas (recommended)?

**Bottom line.** Cosmo headless is a near-term, high-confidence win that directly realizes the "one portable binary" vision and reuses the static-wrapper + inittab + zipos machinery `bake` needs anyway. WASM VolRover is the ambitious flagship, gated on VTK's wasm rendering + Python wrapping (undocumented upstream, disabled in-recipe) and on volume-rendering correctness and size. Both are `cvcpkg bake` with a CPython payload — not a new distribution model. The CPython half of the mechanism is standard and solid; the risk is entirely VTK and rendering.

---

### Key files cited

**cvcpkg (`/home/joe/src/cvc/libcvc-deps/`)**
- `recipes/_common/build-python.sh` — flips `--enable-shared` (native) vs `--disable-shared --host=…` (wasm/wasi/cosmo)
- `recipes/_common/env-cosmo.sh` — cosmo static-only env (`BUILD_SHARED_LIBS=OFF`, `CVC_LINK=static`, `SOURCE_DATE_EPOCH=0`, faked `CMAKE_SYSTEM_NAME=Linux`)
- `recipes/_common/env-wasm.sh`, `recipes/_common/cvc_wasm_run.sh` — wasm cross-env (forces static) + node smoke harness
- `recipes/cosmocc/recipe.yaml` — cosmocc 4.0.2 (`cross_toolchain` for `[cosmo]`)
- `recipes/emsdk/recipe.yaml`, `recipes/emsdk/build.sh` — emsdk 5.0.7 (`cross_toolchain` for `[wasm]`)
- `recipes/python313/build-cosmo.sh`, `recipes/python312/build-wasm.sh` — static cosmo/wasm CPython
- `recipes/vtk/build-wasm.sh` — **headless, `WRAP_PYTHON=OFF`, `RenderingOpenGL2=NO`** (the wall)
- `recipes/vtk-python-cp313/{build.sh,recipe.yaml}` (+ cp311/cp312) — shared-lib wrappers, **linux+windows only**, `vtkPythonUtil` bridge
- `CVCPKG-ROADMAP.md:255–257` (Phase 19 `cvcpkg bake` + cosmo APE), `:1212–1245` (`cvpkg` APE bootstrap)

**libcvc (`/home/joe/src/cvc/libcvc/`)**
- `src/cvcGL/CMakeLists.txt` — VTK render modules cvcGL needs (incl. `RenderingVolumeOpenGL2`)
- `CMakeLists.txt` — C++20 + CUDA-default; no wasm preset

### Upstream sources
- [VTK `vtkModuleWrapPython` CMake API](https://docs.vtk.org/en/latest/api/cmake/vtkModuleWrapPython.html) — `BUILD_STATIC` defaults to `${BUILD_SHARED_LIBS}`; generated `<TARGET>_load()`; "shared modules with a static build is not completely supported"
- [VTK: Building using emscripten for WebAssembly](https://docs.vtk.org/en/latest/advanced/build_wasm_emscripten.html) — WebGPU flag, threads, wasm64 (tested Emscripten 4.0.20)
- [Kitware: Introducing WebAssembly support in VTK](https://www.kitware.com/introducing-webassembly-support-in-vtk/) · [VTK.wasm + trame](https://www.kitware.com/vtk-wasm-and-its-trame-integration/) (server-compute/client-render fallback)
- [python-build-standalone (astral-sh)](https://github.com/astral-sh/python-build-standalone) — static `libpython` + object archives; static-binary variants brittle, cannot `dlopen`
- [PyOxidizer — extension modules](https://gregoryszorc.com/docs/pyoxidizer/main/pyoxidizer_packaging_extension_modules.html) — built-in vs standalone; in-memory `.so` load rejected off-Windows
- [Nuitka user docs](https://nuitka.net/user-documentation/use-cases.html) — `--onefile` is self-extracting; static-libpython support limited
- [Actually Portable Python — ahgamut](https://ahgamut.github.io/2021/07/13/ape-python/) · [superconfigure](https://github.com/ahgamut/superconfigure) — static C extensions via `Modules/Setup`, prebuilt cosmo Python **3.11.4**
- [Cosmopolitan `ape/specification.md`](https://github.com/jart/cosmopolitan/blob/master/ape/specification.md) — no portable dynamic-module ABI · [cosmocc README](https://github.com/jart/cosmopolitan/blob/master/tool/cosmocc/README.md) — GCC 14 / Clang 19 / libcxx (exceptions+RTTI)
- [Pyodide ABI](https://pyodide.org/en/0.29.0/development/abi.html) · [package compatibility](https://pyodide.org/en/stable/usage/wasm-constraints.html) (VTK absent)
- [PEP 776 — Emscripten Support (3.14 tier-3)](https://peps.python.org/pep-0776/) · [PEP 783 — Emscripten Packaging](https://peps.python.org/pep-0783/) · [CPython #95085](https://github.com/python/cpython/issues/95085)
- [Emscripten Dynamic Linking](https://emscripten.org/docs/compiling/Dynamic-Linking.html) · CPython C-API: `PyImport_AppendInittab`/`PyImport_ExtendInittab`, `Modules/Setup`/`makesetup`/`config.c`, `PyImport_FrozenModules`/`Tools/freeze`
