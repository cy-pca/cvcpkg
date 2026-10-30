"""drjit-cp313 post-build check, run by test.sh against the staged package.

argv[1]: the libLLVM path drjit should pick up from the prefix layout, or ""
where no libLLVM is in the closure (the check then skips the LLVM backend).
"""

import os
import sys

expected_llvm = sys.argv[1] if len(sys.argv) > 1 else ""
# Only the patched lookup may choose libLLVM.
os.environ.pop("DRJIT_LIBLLVM_PATH", None)

import drjit as dr  # noqa: E402

print("drjit", dr.__version__, "from", dr.__file__)
assert dr.__version__ == "1.5.0", dr.__version__

# Scalar backend (no JIT).
from drjit.scalar import Array3f  # noqa: E402

v = Array3f(1, 2, 3)
assert dr.dot(v, v) == 14.0, dr.dot(v, v)
assert dr.allclose(dr.normalize(Array3f(0, 3, 4)), Array3f(0, 0.6, 0.8))

if expected_llvm:
    got = os.environ.get("DRJIT_LIBLLVM_PATH", "")
    assert got, "drjit/__init__.py did not set DRJIT_LIBLLVM_PATH"
    assert os.path.realpath(got) == os.path.realpath(expected_llvm), (got, expected_llvm)
    assert dr.has_backend(dr.JitBackend.LLVM), "LLVM backend inactive: libLLVM did not load from " + got

    from drjit.llvm import Float  # noqa: E402

    x = dr.arange(Float, 16)
    s = dr.sum(x * 2 + 1)  # 2 * (0 + ... + 15) + 16
    assert abs(s[0] - 256.0) < 1e-4, s

    # Reverse-mode autodiff through the JIT: d/dy (y^2 + 2y) at 3 is 8.
    from drjit.llvm.ad import Float as FloatAD  # noqa: E402

    y = FloatAD(3.0)
    dr.enable_grad(y)
    z = y * y + 2 * y
    dr.backward(z)
    assert abs(dr.grad(y)[0] - 8.0) < 1e-5, dr.grad(y)
    print("drjit LLVM backend OK, libLLVM from", got)
else:
    print("drjit LLVM backend not checked on", sys.platform,
          "(no libLLVM in the closure); active:", dr.has_backend(dr.JitBackend.LLVM))

print("drjit-cp313 check OK")
