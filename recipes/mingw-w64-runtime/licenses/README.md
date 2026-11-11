# Licences of the runtime DLLs in mingw-w64-runtime

The WinLibs archive this recipe stages from carries no licence texts for the
runtime libraries (its only licence file is Python's), so they are kept here
and installed into `share/licenses/mingw-w64-runtime/` by `build.ps1`.

| DLL | Licence | Text |
|---|---|---|
| libgcc_s_seh-1, libstdc++-6, libgomp-1, libatomic-1, libssp-0 | GPL-3.0-or-later WITH GCC-exception-3.1 | `COPYING3`, `COPYING.RUNTIME` |
| libquadmath-0 | LGPL-2.1-or-later | `COPYING.LIB` |
| libwinpthread-1 | MIT, and BSD-3-Clause for parts | `COPYING.winpthreads` |
| libmcfgthread-2 | LGPL-3.0-or-later WITH GCC-exception-3.1 | `LICENSE.mcfgthread.md`, `COPYING.LESSERv3`, `COPYING3`, `COPYING.RUNTIME` |

Sources, verbatim:

- `COPYING3`, `COPYING.RUNTIME`: gcc-mirror/gcc, tag `releases/gcc-16.1.0` (fe7c47a8e79e165fc686592d895402744e6b54c1), top level.
- `COPYING.LIB`: the same commit, `libquadmath/COPYING.LIB`.
- `COPYING.winpthreads`: mingw-w64/mingw-w64, tag `v14.0.0`, `mingw-w64-libraries/winpthreads/COPYING`.
- `LICENSE.mcfgthread.md`, `COPYING.LESSERv3`: lhmouse/mcfgthread 0b46054a7d9cc8756ad17317e91915c13a3b6502, `LICENSE.md` and `licenses/lgpl-3.0.txt`.
