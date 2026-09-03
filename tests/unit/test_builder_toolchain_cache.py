"""Cross-toolchain cache readiness — the shared-cache race between jobs.

Builders run several cross-compilation jobs concurrently against ONE toolchain
cache.  The cache used to be considered usable when its directory merely
existed and was non-empty, which is true the instant the first job writes its
download into that directory.  A second job then symlinked a half-unpacked
toolchain into its build prefix; for emsdk that surfaces as

    Error: unable to determine 'emsdk' directory. Perhaps you are using a shell…

because emsdk_env.sh checks for emsdk.py, which had not been extracted yet.
Every wasm job on the builder failed that way.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from cvcpkg.cli._builder import (
    _TC_CACHE_MARKER,
    _publish_toolchain_cache,
    _symlink_merge_into,
    _toolchain_cache_ready,
)


def test_missing_cache_is_not_ready(tmp_path: Path) -> None:
    assert not _toolchain_cache_ready(tmp_path / "emsdk-5.0.7+cvc.1")


def test_partially_extracted_cache_is_not_ready(tmp_path: Path) -> None:
    """The race state: non-empty, but mid-extraction and missing emsdk.py."""
    cache = tmp_path / "emsdk-5.0.7+cvc.1"
    cache.mkdir()
    # What a concurrent job has produced so far: its download, plus whatever
    # the archive has unpacked at this instant.
    (cache / "_toolchain_emsdk.tar.gz").write_bytes(b"\x1f\x8b partial")
    (cache / "emsdk_env.sh").write_text("#!/bin/sh\n")
    assert any(cache.iterdir())  # the old readiness test would have said yes
    assert not _toolchain_cache_ready(cache)


def test_completed_cache_is_ready(tmp_path: Path) -> None:
    cache = tmp_path / "emsdk-5.0.7+cvc.1"
    cache.mkdir()
    (cache / "emsdk.py").write_text("# emsdk\n")
    (cache / _TC_CACHE_MARKER).write_text("emsdk 5.0.7+cvc.1\n")
    assert _toolchain_cache_ready(cache)


def test_symlink_merge_publishes_top_level_files(tmp_path: Path) -> None:
    """emsdk_env.sh resolves its directory and requires emsdk.py beside it.

    Top-level FILES must reach the build prefix, not just directories --
    a prefix with emsdk_env.sh but no emsdk.py is exactly the broken state.
    """
    cache = tmp_path / "cache"
    (cache / "upstream" / "emscripten").mkdir(parents=True)
    (cache / "emsdk.py").write_text("# emsdk\n")
    (cache / "emsdk_env.sh").write_text("#!/bin/sh\n")
    (cache / "upstream" / "emscripten" / "emcc").write_text("#!/bin/sh\n")

    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)  # a dependency already installed here
    (prefix / "bin" / "cmake").write_text("#!/bin/sh\n")

    _symlink_merge_into(cache, prefix)

    assert (prefix / "emsdk.py").is_file()
    assert (prefix / "emsdk_env.sh").is_file()
    assert (prefix / "upstream" / "emscripten" / "emcc").is_file()
    # The dependency's own tree survives the merge.
    assert (prefix / "bin" / "cmake").is_file()


def _stage_fake_emsdk(staging: Path, slow: bool = False) -> None:
    """Unpack a fake toolchain, emsdk_env.sh first, emsdk.py last.

    That order matters: emsdk_env.sh resolves its own directory and then
    requires emsdk.py beside it, so the window between the two files is
    exactly the window in which a concurrent job used to pick the cache up.
    """
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "emsdk_env.sh").write_text("#!/bin/sh\n")
    if slow:
        time.sleep(0.01)
    (staging / "upstream").mkdir(exist_ok=True)
    (staging / "upstream" / "emcc").write_text("#!/bin/sh\n")
    (staging / "emsdk.py").write_text("# emsdk\n")


def test_concurrent_publishers_never_expose_a_partial_cache(tmp_path: Path) -> None:
    """Race real publishers against a reader using the shipped helpers.

    Reproduces the shape of the builder failure: several cross-compilation
    jobs populate one cold cache at once while others consume it.  A consumer
    must never see a cache that reports ready but lacks emsdk.py.
    """
    cache_root = tmp_path / "toolchains"
    cache_root.mkdir()
    cache_path = cache_root / "emsdk-5.0.7+cvc.1"

    torn: list[str] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            if _toolchain_cache_ready(cache_path):
                # Ready implies fully extracted -- this is the invariant the
                # old "directory is non-empty" test violated.
                if not (cache_path / "emsdk.py").is_file():
                    torn.append("ready cache missing emsdk.py")
                if not (cache_path / "upstream" / "emcc").is_file():
                    torn.append("ready cache missing upstream/emcc")

    def publisher(idx: int) -> None:
        staging = cache_root / f".emsdk-5.0.7+cvc.1.{idx}.tmp"
        _stage_fake_emsdk(staging, slow=True)
        _publish_toolchain_cache(staging, cache_path, "emsdk 5.0.7+cvc.1")

    readers = [threading.Thread(target=reader) for _ in range(2)]
    for t in readers:
        t.start()
    writers = [threading.Thread(target=publisher, args=(i,)) for i in range(8)]
    for t in writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    for t in readers:
        t.join()

    assert torn == []
    assert _toolchain_cache_ready(cache_path)
    assert (cache_path / "emsdk.py").is_file()
    # Losers cleaned up after themselves: no staging debris is left behind.
    assert [p.name for p in cache_root.iterdir()] == [cache_path.name]


def test_marker_less_debris_is_replaced_not_trusted(tmp_path: Path) -> None:
    """A crashed older run leaves a partial dir; publishing must replace it."""
    cache_root = tmp_path / "toolchains"
    debris = cache_root / "emsdk-5.0.7+cvc.1"
    debris.mkdir(parents=True)
    (debris / "emsdk_env.sh").write_text("#!/bin/sh\n")  # no emsdk.py, no marker

    staging = cache_root / ".emsdk-5.0.7+cvc.1.new.tmp"
    _stage_fake_emsdk(staging)
    _publish_toolchain_cache(staging, debris, "emsdk 5.0.7+cvc.1")

    assert _toolchain_cache_ready(debris)
    assert (debris / "emsdk.py").is_file()
