"""x86-64 levels as builder capabilities (cvcpkg/cpu.py, build.min_cpu)."""

from __future__ import annotations

import pytest

from cvcpkg import cpu

# Feature flags as /proc/cpuinfo prints them.
_V2 = "cx16 lahf_lm popcnt sse4_1 sse4_2 ssse3"
# Sandy Bridge (catx-03's Xeon E5-2640): v2 plus AVX, but no AVX2/FMA/F16C.
_SANDY_BRIDGE = _V2 + " avx xsave aes pclmulqdq"
_HASWELL = _V2 + " avx avx2 bmi1 bmi2 f16c fma abm movbe xsave"
_SKYLAKE_X = _HASWELL + " avx512f avx512bw avx512cd avx512dq avx512vl"


class TestLevels:
    @pytest.mark.parametrize(
        ("flags", "levels"),
        [
            ("", set()),
            ("sse4_2 popcnt", set()),
            (_V2, {"x86-64-v2"}),
            (_SANDY_BRIDGE, {"x86-64-v2"}),
            (_HASWELL, {"x86-64-v2", "x86-64-v3"}),
            (_SKYLAKE_X, {"x86-64-v2", "x86-64-v3", "x86-64-v4"}),
        ],
    )
    def test_levels_for_flags(self, flags, levels):
        assert cpu.levels_for_flags(set(flags.split())) == levels

    def test_a_level_needs_every_level_below_it(self):
        # AVX-512 without the v3 set is not v4.
        flags = set((_V2 + " avx512f avx512bw avx512cd avx512dq avx512vl").split())
        assert cpu.levels_for_flags(flags) == {"x86-64-v2"}

    def test_parse_cpuinfo_reads_the_first_flags_line(self):
        text = (
            "processor\t: 0\nmodel name\t: Intel(R) Xeon(R) CPU E5-2640 0 @ 2.50GHz\n"
            f"flags\t\t: fpu vme {_SANDY_BRIDGE}\n\nprocessor\t: 1\nflags\t\t: fpu\n"
        )
        flags = cpu.parse_cpuinfo(text)
        assert flags is not None
        assert {"avx", "sse4_2"} <= flags
        assert "avx2" not in flags

    def test_parse_cpuinfo_without_flags_is_unknown(self):
        # arm64 /proc/cpuinfo has "Features", not "flags".
        assert cpu.parse_cpuinfo("processor\t: 0\nFeatures\t: fp asimd\n") is None

    def test_parse_darwin_sysctl(self):
        out = (
            "FPU SSE3 SSSE3 FMA CX16 SSE4.1 SSE4.2 MOVBE POPCNT XSAVE AVX1.0 F16C\n"
            "BMI1 AVX2 BMI2\n"
            "SYSCALL XD LAHF LZCNT\n"
        )
        assert cpu.levels_for_flags(cpu.parse_darwin_sysctl(out)) == {"x86-64-v2", "x86-64-v3"}


class TestBuilderCapabilities:
    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_HOST_CPU_FLAGS", _HASWELL.replace(" ", ","))
        assert cpu.builder_capabilities(machine="x86_64") == {"x86-64-v2", "x86-64-v3"}

    def test_non_x86_host_advertises_nothing(self):
        assert cpu.builder_capabilities(set(_SKYLAKE_X.split()), machine="aarch64") == set()

    def test_unknown_flags_advertise_nothing(self, monkeypatch):
        monkeypatch.setattr(cpu, "host_cpu_flags", lambda: None)
        assert cpu.builder_capabilities(machine="AMD64") == set()


class TestRequiredLevel:
    def test_x86_64_level(self):
        assert cpu.required_level({"x86_64": "x86-64-v3"}, "x86_64") == "x86-64-v3"

    @pytest.mark.parametrize("arch", ["arm64", "aarch64", "noarch", "wasm32"])
    def test_other_arches_need_nothing(self, arch):
        assert cpu.required_level({"x86_64": "x86-64-v3"}, arch) is None

    @pytest.mark.parametrize("min_cpu", [None, {}, {"x86_64": "haswell"}, "x86-64-v3"])
    def test_absent_or_malformed_needs_nothing(self, min_cpu):
        assert cpu.required_level(min_cpu, "x86_64") is None


# ── submit-dag: build.min_cpu lands on the jobs ─────────────────


def _write_recipe(tmp_path, name, *, min_cpu=None, platforms=("linux",)):
    rdir = tmp_path / "recipes" / name
    rdir.mkdir(parents=True)
    matrix = "".join(f"    - platform: {p}\n      script: build.sh\n" for p in platforms)
    cpu_block = f"  min_cpu:\n    x86_64: {min_cpu}\n" if min_cpu else ""
    (rdir / "recipe.yaml").write_text(
        f"""schema_version: 1
recipe:
  name: {name}
  upstream_version: "1.0.0"
  cvc_revision: 1
build:
{cpu_block}  matrix:
{matrix}"""
    )


def _fake_client(posted, builders):
    class FakeResp:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, url, **kw):
            if "/v1/builders" in url:
                return FakeResp({"builders": builders})
            if "/v1/packages" in url:
                return FakeResp({"total": 0, "packages": []})
            raise AssertionError(f"unexpected GET {url}")

        def post(self, url, json=None, **kw):
            posted.append(json)
            return FakeResp({"dag_id": "dag-t", "total": len(json["jobs"]), "jobs": []})

    return FakeClient


def _submit(tmp_path, monkeypatch, builders, *recipes, arch="x86_64"):
    from cvcpkg.cli import main

    posted: list = []
    monkeypatch.setattr("httpx.Client", _fake_client(posted, builders))
    rc = main(
        [
            "builds",
            "submit-dag",
            "--server",
            "http://s.example",
            "--token",
            "tok",
            "--platform",
            "linux",
            "--arch",
            arch,
            "--recipes-dir",
            str(tmp_path / "recipes"),
            "--no-default-recipes",
            "--no-deps",
            *recipes,
        ]
    )
    assert rc == 0
    return [j for body in posted for j in body["jobs"]]


def _builder(arch="x86_64", *levels):
    caps = {"glibc2.35": True, "glibc2.39": True, "glibc2.41": True}
    caps.update({lvl: True for lvl in levels})
    return {"platform": "linux", "arch": arch, "capabilities": caps}


_V3_BUILDER = _builder("x86_64", "x86-64-v2", "x86-64-v3")
_V2_BUILDER = _builder("x86_64", "x86-64-v2")


class TestSubmitDagMinCpu:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_GLIBC_FLOOR", "2.35")
        monkeypatch.delenv("CVCPKG_NOARCH_BUILD_PLATFORM", raising=False)
        monkeypatch.delenv("CVCPKG_NOARCH_BUILD_ARCH", raising=False)

    def test_x86_64_job_carries_the_level(self, tmp_path, monkeypatch):
        _write_recipe(tmp_path, "drjit", min_cpu="x86-64-v3")
        jobs = _submit(tmp_path, monkeypatch, [_V3_BUILDER], "drjit")
        assert len(jobs) == 1
        assert jobs[0]["required_capabilities"] == ["glibc2.35", "x86-64-v3"]

    def test_recipe_without_min_cpu_is_unchanged(self, tmp_path, monkeypatch):
        _write_recipe(tmp_path, "zlib")
        jobs = _submit(tmp_path, monkeypatch, [_V2_BUILDER], "zlib")
        assert jobs[0]["required_capabilities"] == ["glibc2.35"]

    def test_skipped_when_only_older_cpus_are_registered(self, tmp_path, monkeypatch, capsys):
        _write_recipe(tmp_path, "drjit", min_cpu="x86-64-v3")
        assert _submit(tmp_path, monkeypatch, [_V2_BUILDER], "drjit") == []
        assert "drjit (glibc2.35, x86-64-v3)" in capsys.readouterr().out

    def test_unknown_cpu_is_not_excused(self, tmp_path, monkeypatch):
        # A builder that advertises no level (older agent, unreadable CPU).
        _write_recipe(tmp_path, "drjit", min_cpu="x86-64-v3")
        assert _submit(tmp_path, monkeypatch, [_builder("x86_64")], "drjit") == []

    def test_arm64_job_needs_no_level(self, tmp_path, monkeypatch):
        _write_recipe(tmp_path, "drjit", min_cpu="x86-64-v3")
        jobs = _submit(tmp_path, monkeypatch, [_builder("arm64")], "drjit", arch="arm64")
        assert len(jobs) == 1
        assert jobs[0]["required_capabilities"] == ["glibc2.35"]

    def test_noarch_job_built_on_x86_64_carries_the_level(self, tmp_path, monkeypatch):
        _write_recipe(tmp_path, "sionna", min_cpu="x86-64-v3", platforms=("any",))
        jobs = _submit(tmp_path, monkeypatch, [_V3_BUILDER], "sionna")
        assert len(jobs) == 1
        assert jobs[0]["platform"] == "any"
        assert jobs[0]["required_capabilities"] == ["glibc2.35", "x86-64-v3"]

    def test_noarch_job_skipped_without_a_capable_reference_builder(
        self, tmp_path, monkeypatch, capsys
    ):
        _write_recipe(tmp_path, "sionna", min_cpu="x86-64-v3", platforms=("any",))
        assert _submit(tmp_path, monkeypatch, [_V2_BUILDER], "sionna") == []
        assert "sionna (glibc2.35, x86-64-v3)" in capsys.readouterr().out
