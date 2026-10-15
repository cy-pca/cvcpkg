"""``cuda`` means a GPU for installs and nvcc for builds.

Installing a CUDA package needs a GPU to run it, not a compiler: a workstation
with an NVIDIA GPU and its driver must be offered torch-cp311-cuda without
setting CVCPKG_CAPABILITIES.  Building a CUDA recipe needs nvcc, so what a
builder advertises to the scheduler stays the compiler probe alone -- a GPU
without a toolkit attracted CUDA jobs it could only fail (see _probe_cuda).
"""

from __future__ import annotations

import subprocess

import pytest
from click.testing import CliRunner

import cvcpkg.platform as plat


def _clear(monkeypatch):
    monkeypatch.setattr(plat, "_probed_capabilities", None)
    monkeypatch.delenv("CVCPKG_CAPABILITIES", raising=False)


# ── the GPU probe ───────────────────────────────────────────────


class TestProbeCudaGpu:
    def _host(self, monkeypatch, *, which=None, sysroot=None, run=None):
        monkeypatch.setattr("shutil.which", lambda n: which if n == "nvidia-smi" else None)
        if sysroot is None:
            monkeypatch.delenv("SystemRoot", raising=False)
        else:
            monkeypatch.setenv("SystemRoot", str(sysroot))
        calls: list[list[str]] = []

        def fake_run(cmd, **k):
            calls.append(cmd)
            if run is None:
                raise AssertionError("nvidia-smi should not have been run")
            return run(cmd)

        monkeypatch.setattr(subprocess, "run", fake_run)
        return calls

    @staticmethod
    def _smi(returncode: int, stdout: str):
        return lambda cmd: subprocess.CompletedProcess(cmd, returncode, stdout, "")

    def test_a_listed_gpu_counts(self, monkeypatch):
        calls = self._host(
            monkeypatch,
            which="/usr/bin/nvidia-smi",
            run=self._smi(0, "GPU 0: NVIDIA GeForce RTX 4070 Laptop GPU (UUID: GPU-1234)\n"),
        )
        assert plat._probe_cuda_gpu() is True
        assert calls == [["/usr/bin/nvidia-smi", "-L"]]

    def test_a_driver_without_a_gpu_does_not(self, monkeypatch):
        self._host(
            monkeypatch,
            which="/usr/bin/nvidia-smi",
            run=self._smi(6, "No devices were found\n"),
        )
        assert plat._probe_cuda_gpu() is False

    def test_no_nvidia_smi_anywhere(self, monkeypatch):
        self._host(monkeypatch)
        assert plat._probe_cuda_gpu() is False

    def test_system32_nvidia_smi_off_path(self, tmp_path, monkeypatch):
        # A Windows service account's PATH may lack System32's nvidia-smi.exe.
        smi = tmp_path / "System32" / "nvidia-smi.exe"
        smi.parent.mkdir()
        smi.write_text("")
        calls = self._host(
            monkeypatch, sysroot=tmp_path, run=self._smi(0, "GPU 0: NVIDIA RTX A4000\n")
        )
        assert plat._probe_cuda_gpu() is True
        assert calls == [[str(smi), "-L"]]

    def test_a_hung_nvidia_smi_is_no_gpu(self, monkeypatch):
        def hang(cmd):
            raise subprocess.TimeoutExpired(cmd, 10)

        self._host(monkeypatch, which="/usr/bin/nvidia-smi", run=hang)
        assert plat._probe_cuda_gpu() is False


# ── install vs build ────────────────────────────────────────────


class TestCapabilityPurposes:
    def _probes(self, monkeypatch, *, compiler: bool, gpu: bool):
        _clear(monkeypatch)
        monkeypatch.setattr(plat, "_CAPABILITY_PROBES", {"cuda": lambda: compiler})
        monkeypatch.setattr(plat, "_INSTALL_PROBES", {"cuda": lambda: gpu})

    def test_gpu_without_toolkit_installs_but_does_not_build(self, monkeypatch):
        self._probes(monkeypatch, compiler=False, gpu=True)
        assert plat.host_capabilities() == {"cuda"}
        assert plat.host_capabilities("install") == {"cuda"}
        assert plat.host_capabilities("build") == set()

    def test_toolkit_without_gpu_does_both(self, monkeypatch):
        # Installing a CUDA package to build against needs no GPU.
        self._probes(monkeypatch, compiler=True, gpu=False)
        assert plat.host_capabilities("install") == {"cuda"}
        assert plat.host_capabilities("build") == {"cuda"}

    def test_neither(self, monkeypatch):
        self._probes(monkeypatch, compiler=False, gpu=False)
        assert plat.host_capabilities("install") == set()
        assert plat.host_capabilities("build") == set()

    def test_env_override_is_authoritative_for_both(self, monkeypatch):
        self._probes(monkeypatch, compiler=True, gpu=True)
        monkeypatch.setenv("CVCPKG_CAPABILITIES", "")
        assert plat.host_capabilities("install") == set()
        assert plat.host_capabilities("build") == set()

    def test_unknown_purpose_is_an_error(self, monkeypatch):
        _clear(monkeypatch)
        with pytest.raises(ValueError, match="purpose"):
            plat.host_capabilities("run")

    def test_the_resolver_offers_the_cuda_build_to_a_gpu_host(self, monkeypatch):
        from cvcpkg.manifest import CatalogEntry, ComponentReq
        from cvcpkg.resolver import resolve

        def entry(name, *, provides=(), caps=()):
            return CatalogEntry(
                name=name,
                version="2.8.0+cvc.1",
                upstream_version="2.8.0",
                cvc_revision=1,
                platform="windows",
                arch="x86_64",
                build_type="release",
                link="shared",
                sha256="0" * 64,
                size_bytes=1,
                archive_url=f"https://example.test/{name}.zip",
                source_release="",
                provides=list(provides),
                requires_capabilities=list(caps),
            )

        candidates = {
            "torch-cp311": [entry("torch-cp311")],
            "torch-cp311-cuda": [
                entry("torch-cp311-cuda", provides=["torch-cp311"], caps=["cuda"])
            ],
        }
        self._probes(monkeypatch, compiler=False, gpu=True)
        picked = resolve([ComponentReq(name="torch-cp311")], candidates).picked
        assert picked["torch-cp311"].name == "torch-cp311-cuda"

        self._probes(monkeypatch, compiler=False, gpu=False)
        picked = resolve([ComponentReq(name="torch-cp311")], candidates).picked
        assert picked["torch-cp311"].name == "torch-cp311"


# ── what a builder advertises ───────────────────────────────────


class _Resp:
    def __init__(self, status: int = 200, data=None):
        self.status_code = status
        self._data = {} if data is None else data
        self.content = b""
        self.text = ""

    def json(self):
        return self._data


def _registered_capabilities(monkeypatch, tmp_path) -> dict:
    """Run ``builder run`` against a fake server with no jobs; return the
    capabilities it registered with."""
    import httpx

    from cvcpkg.cli._builder import builder_run

    registered: list[dict] = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass

        def post(self, url, json=None, **k):
            if url.rstrip("/").endswith("/register"):
                registered.append(json)
                return _Resp(200, {"id": 7})
            return _Resp(200, {})

        def patch(self, url, **k):
            return _Resp(200, {})

        def get(self, url, **k):
            return _Resp(204)

        def delete(self, url, **k):
            return _Resp(200, {})

    monkeypatch.setattr(httpx, "Client", _Client)
    result = CliRunner().invoke(
        builder_run,
        [
            "--server",
            "http://test",
            "--token",
            "t",
            "--name",
            "w-caps",
            "--platform",
            "windows",
            "--arch",
            "x86_64",
            "--no-websocket",
            "--no-free-disk",
            "--exit-when-empty",
            "--max-runtime",
            "30",
            "--work-dir",
            str(tmp_path / "wd"),
            "--recipe-cache-dir",
            str(tmp_path / "rc"),
            "--pidfile",
            str(tmp_path / "b.pid"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert registered, result.output
    return registered[0]["capabilities"]


class TestBuilderAdvertisesTheBuildSet:
    def test_a_gpu_without_a_toolkit_is_not_advertised(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        monkeypatch.setattr(plat, "_CAPABILITY_PROBES", {"cuda": lambda: False})
        monkeypatch.setattr(plat, "_INSTALL_PROBES", {"cuda": lambda: True})
        assert "cuda" not in _registered_capabilities(monkeypatch, tmp_path)

    def test_a_toolkit_is_advertised(self, monkeypatch, tmp_path):
        _clear(monkeypatch)
        monkeypatch.setattr(plat, "_CAPABILITY_PROBES", {"cuda": lambda: True})
        monkeypatch.setattr(plat, "_INSTALL_PROBES", {})
        assert _registered_capabilities(monkeypatch, tmp_path).get("cuda") is True
