# shewchuk-predicates

Jonathan Richard Shewchuk's adaptive exact geometric predicates (`orient2d`, `orient3d`,
`incircle`, `insphere`) as a reentrant C++ header, for the `shewchuk-predicates` recipe.

- `predicates.c`: upstream, unmodified. "Routines for Arbitrary Precision Floating-point
  Arithmetic and Fast Robust Geometric Predicates", May 18, 1996, placed in the public domain;
  https://www.cs.cmu.edu/afs/cs/project/quake/public/code/predicates.c, sha256
  `f8662c3f407d1c1c5dcd4dd49ea8b8ddd801a71827d65734206ddc3741792029`. It lives here because
  upstream ships one bare `.c` file, no tarball.
- `gen_predicates.py`: turns it into `include/shewchuk/predicates.hpp` (Python 3, standard
  library only). The header's prologue lists every change. In short:
  - `exactinit()`'s globals become `constexpr` constants for IEEE-754 binary64, so there is no
    initialisation call and no mutable state;
  - only the four adaptive predicates and the routines they call are kept, their bodies
    verbatim;
  - floating-point contraction is switched off around them, because an FMA breaks
    `Two_Product`;
  - `insphereexact`'s ~200 KB of arrays live in one heap block. A wasm thread's stack is 64 KB
    by default, and an overflow there is silent.
- `include/shewchuk/predicates.hpp`: the generated header, committed so the recipe needs no
  Python. `build.sh` regenerates it and fails on any difference when `python3` is on PATH.

To regenerate after changing the script:

```bash
python3 -I gen_predicates.py predicates.c include/shewchuk/predicates.hpp
```

Usage: `find_package(ShewchukPredicates CONFIG)`, link `Shewchuk::predicates`, and
`#include <shewchuk/predicates.hpp>`. The functions are `shewchuk::orient2d`, `orient3d`,
`incircle` and `insphere`, on `const double *` or `std::array` points, plus
`shewchuk::sign`. `orient3d`'s sign is the opposite of CGAL's `orientation` (the header
documents each convention).
