"""ABI-tag compatibility checks (§3.2.1 of the roadmap)."""

from __future__ import annotations

from cvcpkg.errors import AbiError
from cvcpkg.manifest import AbiTag


def check_abi_compat(a: AbiTag, b: AbiTag, *, strict: bool = True) -> list[str]:
    """Return a list of ABI-mismatch diagnostics between two tags.

    If *strict* is True (the default) and there are mismatches, raise
    ``AbiError``.  Otherwise return warnings only.
    """
    issues: list[str] = []

    if a.cxx_std and b.cxx_std and a.cxx_std != b.cxx_std:
        lo, hi = sorted([a.cxx_std, b.cxx_std])
        issues.append(f"C++ standard mismatch: {lo} vs {hi}")

    if a.cxx_runtime and b.cxx_runtime:
        # Same family + major required.
        af = a.cxx_runtime.rsplit("-", 1)[0]
        bf = b.cxx_runtime.rsplit("-", 1)[0]
        if af != bf:
            issues.append(f"C++ runtime family mismatch: {a.cxx_runtime} vs {b.cxx_runtime}")

    if a.libc and b.libc:
        if a.libc != b.libc:
            issues.append(f"libc mismatch: {a.libc} vs {b.libc}")

    if a.crt_link and b.crt_link and a.crt_link != b.crt_link:
        issues.append(f"CRT link mismatch: {a.crt_link} vs {b.crt_link}")

    if strict and issues:
        raise AbiError("ABI incompatibility detected:\n  " + "\n  ".join(issues))
    return issues
