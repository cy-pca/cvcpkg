"""``builder._find_bash``: PATH first, then the package systems' locations.

A BSD builder started from cron (the documented @reboot setup) runs with
cron's minimal PATH.  NetBSD keeps bash only in pkgsrc's /usr/pkg/bin, so
every NetBSD build failed with "bash not found on PATH" before env-netbsd.sh
(which adds /usr/pkg/bin) could even be sourced.
"""

import os
import sys

import pytest

from cvcpkg import builder

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX fallback paths")


@posix_only
def test_prefers_bash_on_path(monkeypatch):
    monkeypatch.setattr(builder.shutil, "which", lambda name: "/somewhere/on/path/bash")
    assert builder._find_bash() == "/somewhere/on/path/bash"


@posix_only
def test_falls_back_to_pkgsrc_bash_when_path_is_minimal(monkeypatch):
    """The NetBSD builder: no bash on PATH, pkgsrc's /usr/pkg/bin/bash exists."""
    monkeypatch.setattr(builder.shutil, "which", lambda name: None)
    present = {"/usr/pkg/bin/bash"}
    monkeypatch.setattr(builder.os.path, "isfile", lambda p: p in present)
    monkeypatch.setattr(builder.os, "access", lambda p, mode: p in present)
    assert builder._find_bash() == "/usr/pkg/bin/bash"


@posix_only
def test_fallback_order_prefers_package_bash_over_base(monkeypatch):
    """A FreeBSD/OpenBSD host with both: the packaged bash wins, as on PATH."""
    monkeypatch.setattr(builder.shutil, "which", lambda name: None)
    present = {"/usr/local/bin/bash", "/bin/bash"}
    monkeypatch.setattr(builder.os.path, "isfile", lambda p: p in present)
    monkeypatch.setattr(builder.os, "access", lambda p, mode: p in present)
    assert builder._find_bash() == "/usr/local/bin/bash"


@posix_only
def test_skips_non_executable_candidates(monkeypatch):
    monkeypatch.setattr(builder.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        builder.os.path, "isfile", lambda p: p in {"/usr/pkg/bin/bash", "/bin/bash"}
    )
    monkeypatch.setattr(builder.os, "access", lambda p, mode: p == "/bin/bash" and mode == os.X_OK)
    assert builder._find_bash() == "/bin/bash"


@posix_only
def test_error_names_the_places_it_looked(monkeypatch):
    monkeypatch.setattr(builder.shutil, "which", lambda name: None)
    monkeypatch.setattr(builder.os.path, "isfile", lambda p: False)
    with pytest.raises(builder.BuildError, match=r"/usr/pkg/bin/bash"):
        builder._find_bash()
