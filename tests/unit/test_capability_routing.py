"""Build-time capability routing — a job whose recipe declares
``requires_capabilities`` (e.g. ``[cuda]``) is dispatched only to a builder
advertising every one of them, and is marked unschedulable when no registered
builder covering its target advertises them.

Covers the scheduler (`_choose_builder`), the job store round-trip and the
unschedulable reaper's joint target+capability check, and the REST surface
(builder registration, DAG/job submission, anonymous next-claimable drain).
"""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for build job tests")

from types import SimpleNamespace

from fastapi.testclient import TestClient

from cvcpkg.server.app import _choose_builder, create_app
from cvcpkg.server.models import BuildJobStatus, TokenRole

# ── _choose_builder ─────────────────────────────────────────────


def _builder(caps=None, platform="linux", arch="x86_64", cur=0, mx=2, id=1):
    return SimpleNamespace(
        id=id,
        org_slug="",
        platform=platform,
        arch=arch,
        capabilities=caps if caps is not None else {},
        current_jobs=cur,
        max_jobs=mx,
        prefer_affinity=False,
    )


def _job(required=None, platform="linux", arch="x86_64"):
    return SimpleNamespace(
        org_slug="",
        platform=platform,
        arch=arch,
        required_capabilities=required or [],
    )


class TestChooseBuilderCapabilities:
    def test_cuda_job_never_lands_on_plain_builder(self):
        plain = _builder()
        assert _choose_builder(_job(required=["cuda"]), [plain]) is None

    def test_cuda_job_lands_on_cuda_builder(self):
        cuda = _builder(caps={"cuda": True})
        assert _choose_builder(_job(required=["cuda"]), [cuda]) is cuda

    def test_cuda_job_picks_cuda_builder_among_plain(self):
        plain = _builder(id=1)
        cuda = _builder(caps={"cuda": True}, id=2)
        assert _choose_builder(_job(required=["cuda"]), [plain, cuda]) is cuda

    def test_plain_job_still_lands_on_cuda_builder(self):
        # A capability only ADDS eligibility; it never reserves the builder.
        cuda = _builder(caps={"cuda": True})
        assert _choose_builder(_job(), [cuda]) is cuda

    def test_all_required_capabilities_must_be_advertised(self):
        cuda_only = _builder(caps={"cuda": True})
        assert _choose_builder(_job(required=["cuda", "avx512"]), [cuda_only]) is None
        both = _builder(caps={"cuda": True, "avx512": True}, id=2)
        assert _choose_builder(_job(required=["cuda", "avx512"]), [cuda_only, both]) is both

    def test_falsy_capability_flag_does_not_count(self):
        disabled = _builder(caps={"cuda": False})
        assert _choose_builder(_job(required=["cuda"]), [disabled]) is None

    def test_capability_combines_with_cross_platform_match(self):
        # A WSL builder cross-building windows that also advertises cuda.
        b = _builder(
            caps={
                "cross_platforms": [{"platform": "windows", "arch": "x86_64"}],
                "cuda": True,
            }
        )
        job = _job(required=["cuda"], platform="windows", arch="x86_64")
        assert _choose_builder(job, [b]) is b

    def test_capability_respects_capacity(self):
        busy_cuda = _builder(caps={"cuda": True}, cur=2, mx=2)
        assert _choose_builder(_job(required=["cuda"]), [busy_cuda]) is None


# ── Job store: round-trip + unschedulable reaper ────────────────


class TestRequiredCapabilitiesStore:
    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        db_path = tmp_path / "cap_routing.db"
        db_url = f"sqlite+aiosqlite:///{db_path}"
        monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)

        from cvcpkg.server.db import create_tables, dispose_engine, init_db

        async def _init():
            init_db(db_url)
            await create_tables()

        asyncio.run(_init())
        yield

        async def _cleanup():
            await dispose_engine()

        asyncio.run(_cleanup())

    def _run(self, coro):
        return asyncio.run(coro)

    def test_create_round_trips_required_capabilities(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            info = await store.create(
                recipe_name="libcvc-cuda",
                platform="linux",
                arch="x86_64",
                submitted_by="test-admin",
                required_capabilities=["cuda"],
            )
            assert info.required_capabilities == ["cuda"]
            fetched = await store.get(info.id)
            assert fetched.required_capabilities == ["cuda"]

            plain = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="test-admin",
            )
            assert plain.required_capabilities == []

        self._run(_test())

    def test_create_dag_round_trips_required_capabilities(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            jobs = await store.create_dag(
                jobs=[
                    {"recipe_name": "cufft", "platform": "linux", "arch": "x86_64"},
                    {
                        "recipe_name": "libcvc-cuda",
                        "platform": "linux",
                        "arch": "x86_64",
                        "required_capabilities": ["cuda"],
                        "depends_on": [0],
                    },
                ],
                dag_id="dag-cuda",
                submitted_by="test-admin",
            )
            assert jobs[0].required_capabilities == []
            assert jobs[1].required_capabilities == ["cuda"]
            fetched = await store.get(jobs[1].id)
            assert fetched.required_capabilities == ["cuda"]

        self._run(_test())

    def test_reaper_marks_cuda_job_with_no_cuda_builder(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            cuda_job = await store.create(
                recipe_name="libcvc-cuda",
                platform="linux",
                arch="x86_64",
                submitted_by="test-admin",
                required_capabilities=["cuda"],
            )
            plain_job = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="test-admin",
            )
            # One linux builder, no cuda capability.
            offers = [({("linux", "x86_64")}, set(), set())]
            reaped = await store.reap_unschedulable(
                {("linux", "x86_64")},
                set(),
                min_age_seconds=0,
                builder_offers=offers,
            )
            reaped_ids = {j.id for j in reaped}
            assert cuda_job.id in reaped_ids
            assert plain_job.id not in reaped_ids

            c = await store.get(cuda_job.id)
            assert c.status == BuildJobStatus.unschedulable
            assert "cuda" in (c.error_message or "")

        self._run(_test())

    def test_reaper_keeps_cuda_job_when_cuda_builder_registered(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            cuda_job = await store.create(
                recipe_name="libcvc-cuda",
                platform="linux",
                arch="x86_64",
                submitted_by="test-admin",
                required_capabilities=["cuda"],
            )
            offers = [
                ({("linux", "x86_64")}, set(), set()),  # star-00
                ({("linux", "x86_64")}, set(), {"cuda"}),  # prettyhatemachine
            ]
            reaped = await store.reap_unschedulable(
                {("linux", "x86_64")},
                set(),
                min_age_seconds=0,
                builder_offers=offers,
            )
            assert cuda_job.id not in {j.id for j in reaped}

        self._run(_test())

    def test_reaper_needs_target_and_capability_on_same_builder(self):
        # A windows/cuda builder plus a plain linux builder must NOT keep a
        # linux cuda job alive: no single builder covers target AND capability.
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            cuda_job = await store.create(
                recipe_name="libcvc-cuda",
                platform="linux",
                arch="x86_64",
                submitted_by="test-admin",
                required_capabilities=["cuda"],
            )
            offers = [
                ({("linux", "x86_64")}, set(), set()),
                ({("windows", "x86_64")}, set(), {"cuda"}),
            ]
            reaped = await store.reap_unschedulable(
                {("linux", "x86_64"), ("windows", "x86_64")},
                set(),
                min_age_seconds=0,
                builder_offers=offers,
            )
            assert cuda_job.id in {j.id for j in reaped}

        self._run(_test())

    def test_legacy_call_without_offers_is_capability_blind(self):
        # Old callers that pass no builder_offers keep the old semantics: a
        # target-covered job is never reaped for its capabilities.
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            cuda_job = await store.create(
                recipe_name="libcvc-cuda",
                platform="linux",
                arch="x86_64",
                submitted_by="test-admin",
                required_capabilities=["cuda"],
            )
            reaped = await store.reap_unschedulable({("linux", "x86_64")}, set(), min_age_seconds=0)
            assert cuda_job.id not in {j.id for j in reaped}

        self._run(_test())


# ── REST surface ────────────────────────────────────────────────


@pytest.fixture()
def db_server_env(tmp_path, monkeypatch):
    """DB-backed test server with admin and publisher tokens."""
    db_path = tmp_path / "test.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("CVCPKG_DATABASE_URL", db_url)
    monkeypatch.delenv("CVCPKG_MIRROR_MODE", raising=False)

    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbTokenStore

    async def _seed():
        init_db(db_url)
        await create_tables()
        store = DbTokenStore(tmp_path)
        admin_raw = await store.create("test-admin", TokenRole.admin)
        pub_raw = await store.create("test-publisher", TokenRole.publisher)
        await dispose_engine()
        return admin_raw, pub_raw

    admin_token, pub_token = asyncio.run(_seed())

    app = create_app(state_dir=tmp_path)
    with TestClient(app) as client:
        yield client, admin_token, pub_token, tmp_path


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


class TestCapabilityAPI:
    def test_builder_registration_advertises_capabilities(self, db_server_env):
        client, _admin, pub, _tmp = db_server_env
        resp = client.post(
            "/v1/builders/register",
            headers=_auth(pub),
            json={
                "name": "prettyhatemachine",
                "platform": "linux",
                "arch": "x86_64",
                "max_jobs": 2,
                "capabilities": {"cuda": True},
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["capabilities"] == {"cuda": True}

        listed = client.get("/v1/builders", headers=_auth(pub)).json()["builders"]
        me = next(b for b in listed if b["name"] == "prettyhatemachine")
        assert me["capabilities"] == {"cuda": True}

    def test_dag_submit_round_trips_required_capabilities(self, db_server_env):
        client, _admin, pub, _tmp = db_server_env
        resp = client.post(
            "/v1/builds/dag",
            headers=_auth(pub),
            json={
                "jobs": [
                    {"recipe_name": "cufft", "platform": "linux", "arch": "x86_64"},
                    {
                        "recipe_name": "libcvc-cuda",
                        "platform": "linux",
                        "arch": "x86_64",
                        "required_capabilities": ["cuda"],
                        "depends_on": [0],
                    },
                ]
            },
        )
        assert resp.status_code == 200, resp.text
        jobs = resp.json()["jobs"]
        assert jobs[0]["required_capabilities"] == []
        assert jobs[1]["required_capabilities"] == ["cuda"]

        job = client.get(f"/v1/builds/{jobs[1]['id']}", headers=_auth(pub)).json()
        assert job["required_capabilities"] == ["cuda"]

    def test_single_submit_round_trips_required_capabilities(self, db_server_env):
        client, _admin, pub, _tmp = db_server_env
        resp = client.post(
            "/v1/builds",
            headers=_auth(pub),
            json={
                "recipe_name": "libcvc-cuda",
                "platform": "linux",
                "arch": "x86_64",
                "required_capabilities": ["cuda"],
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["required_capabilities"] == ["cuda"]

    def test_next_claimable_gates_on_capabilities(self, db_server_env):
        client, _admin, pub, _tmp = db_server_env
        cuda = client.post(
            "/v1/builds",
            headers=_auth(pub),
            json={
                "recipe_name": "libcvc-cuda",
                "platform": "linux",
                "arch": "x86_64",
                "required_capabilities": ["cuda"],
                "priority": 10,
            },
        ).json()
        plain = client.post(
            "/v1/builds",
            headers=_auth(pub),
            json={"recipe_name": "zlib", "platform": "linux", "arch": "x86_64"},
        ).json()

        # A capability-less drainer skips the cuda job even at higher
        # priority and gets the plain one.
        got = client.get(
            "/v1/builds/next-claimable",
            headers=_auth(pub),
            params={"platform": "linux"},
        )
        assert got.status_code == 200
        assert got.json()["id"] == plain["id"]

        # A cuda-capable drainer gets the cuda job (higher priority).
        got = client.get(
            "/v1/builds/next-claimable",
            headers=_auth(pub),
            params={"platform": "linux", "capabilities": "cuda"},
        )
        assert got.status_code == 200
        assert got.json()["id"] == cuda["id"]

    def test_next_claimable_204_when_only_capability_jobs_pend(self, db_server_env):
        client, _admin, pub, _tmp = db_server_env
        client.post(
            "/v1/builds",
            headers=_auth(pub),
            json={
                "recipe_name": "libcvc-cuda",
                "platform": "linux",
                "arch": "x86_64",
                "required_capabilities": ["cuda"],
            },
        )
        got = client.get(
            "/v1/builds/next-claimable",
            headers=_auth(pub),
            params={"platform": "linux"},
        )
        assert got.status_code == 204


# ── Fleet config → worker argv ──────────────────────────────────


class TestFleetCapabilities:
    def test_fleet_capabilities_default_and_override(self):
        from cvcpkg.builder_fleet import parse_fleet_config

        cfg = parse_fleet_config(
            {
                "name": "prettyhatemachine",
                "capabilities": ["cuda"],
                "servers": [
                    {"server": "https://cvcpkg.org", "token": "cvctok_x"},
                    {
                        "server": "https://pkg.tx.wtf",
                        "token": "cvctok_y",
                        "capabilities": [],
                        "auto_capabilities": False,
                    },
                ],
            }
        )
        assert cfg.servers[0].capabilities == ("cuda",)
        assert cfg.servers[0].auto_capabilities is True
        assert cfg.servers[1].auto_capabilities is False

    def test_worker_argv_carries_capability_flags(self):
        from cvcpkg.builder_fleet import parse_fleet_config, worker_argv

        cfg = parse_fleet_config(
            {
                "name": "phm",
                "servers": [
                    {
                        "server": "https://cvcpkg.org",
                        "token": "cvctok_x",
                        "capabilities": ["cuda"],
                        "auto_capabilities": False,
                    }
                ],
            }
        )
        argv = worker_argv(cfg.servers[0])
        assert argv[argv.index("--capability") + 1] == "cuda"
        assert "--no-auto-capabilities" in argv

    def test_worker_argv_default_has_no_capability_flags(self):
        from cvcpkg.builder_fleet import parse_fleet_config, worker_argv

        cfg = parse_fleet_config(
            {
                "name": "star-00",
                "servers": [{"server": "https://cvcpkg.org", "token": "cvctok_x"}],
            }
        )
        argv = worker_argv(cfg.servers[0])
        assert "--capability" not in argv
        assert "--no-auto-capabilities" not in argv


# ── submit-dag: recipe requires_capabilities → job required_capabilities ──
#
# The CLI half of the feature: reading the recipe's top-level
# requires_capabilities, stamping it onto every job it submits, and
# pre-skipping recipes no registered builder can serve.  Mocked-httpx
# style, matching the other submit-dag tests (tests/unit/test_populate.py).


def _write_cap_recipe(tmp_path, name, caps=(), platforms=("linux",)):
    """A minimal recipe, optionally declaring requires_capabilities."""
    rdir = tmp_path / "recipes" / name
    rdir.mkdir(parents=True)
    caps_line = (
        "requires_capabilities: [" + ", ".join(f'"{c}"' for c in caps) + "]\n" if caps else ""
    )
    matrix = "".join(f"    - platform: {p}\n      script: build.sh\n" for p in platforms)
    (rdir / "recipe.yaml").write_text(
        f"""schema_version: 1
recipe:
  name: {name}
  upstream_version: "1.0.0"
  cvc_revision: 1
{caps_line}build:
  matrix:
{matrix}"""
    )
    return rdir


def _cap_fake_client(posted_bodies, builders):
    """httpx.Client stub serving *builders* from /v1/builders."""

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
            posted_bodies.append(json)
            return FakeResp({"dag_id": "dag-t", "total": len(json["jobs"]), "jobs": []})

    return FakeClient


_PLAIN_BUILDER = {"platform": "linux", "arch": "x86_64", "capabilities": {}}
_CUDA_BUILDER = {"platform": "linux", "arch": "x86_64", "capabilities": {"cuda": True}}


def _submit_dag(tmp_path, monkeypatch, posted, builders, *recipes):
    from cvcpkg.cli import main

    monkeypatch.setattr("httpx.Client", _cap_fake_client(posted, builders))
    return main(
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
            "x86_64",
            "--recipes-dir",
            str(tmp_path / "recipes"),
            "--no-default-recipes",
            "--no-deps",
            *recipes,
        ]
    )


class TestSubmitDagCapabilityPropagation:
    def test_recipe_requirement_lands_on_the_job(self, tmp_path, monkeypatch):
        _write_cap_recipe(tmp_path, "libcvc-cuda", caps=("cuda",))
        posted: list = []
        assert _submit_dag(tmp_path, monkeypatch, posted, [_CUDA_BUILDER], "libcvc-cuda") == 0
        jobs = [j for b in posted for j in b["jobs"]]
        assert len(jobs) == 1
        assert jobs[0]["required_capabilities"] == ["cuda"]

    def test_plain_recipe_carries_no_requirement(self, tmp_path, monkeypatch):
        # Absent (not empty-list) so an OLD server ignoring the field behaves
        # exactly as before this feature.
        _write_cap_recipe(tmp_path, "zlib")
        posted: list = []
        assert _submit_dag(tmp_path, monkeypatch, posted, [_PLAIN_BUILDER], "zlib") == 0
        jobs = [j for b in posted for j in b["jobs"]]
        assert len(jobs) == 1
        assert "required_capabilities" not in jobs[0]

    def test_skipped_when_no_builder_advertises_the_capability(self, tmp_path, monkeypatch):
        # Only a CPU builder is registered: the cuda recipe must not be
        # submitted at all (it would sit pending until the reaper failed it).
        _write_cap_recipe(tmp_path, "libcvc-cuda", caps=("cuda",))
        posted: list = []
        assert _submit_dag(tmp_path, monkeypatch, posted, [_PLAIN_BUILDER], "libcvc-cuda") == 0
        assert [j for b in posted for j in b["jobs"]] == []

    def test_capable_builder_must_also_cover_the_target(self, tmp_path, monkeypatch):
        # A cuda builder on a DIFFERENT platform must not make a linux cuda
        # recipe look schedulable (target and capability from one builder).
        _write_cap_recipe(tmp_path, "libcvc-cuda", caps=("cuda",))
        win_cuda = {"platform": "windows", "arch": "x86_64", "capabilities": {"cuda": True}}
        posted: list = []
        assert (
            _submit_dag(tmp_path, monkeypatch, posted, [_PLAIN_BUILDER, win_cuda], "libcvc-cuda")
            == 0
        )
        assert [j for b in posted for j in b["jobs"]] == []

    def test_plain_recipes_still_submit_alongside_a_skipped_cuda_one(self, tmp_path, monkeypatch):
        _write_cap_recipe(tmp_path, "libcvc-cuda", caps=("cuda",))
        _write_cap_recipe(tmp_path, "zlib")
        posted: list = []
        assert (
            _submit_dag(tmp_path, monkeypatch, posted, [_PLAIN_BUILDER], "libcvc-cuda", "zlib") == 0
        )
        names = {j["recipe_name"] for b in posted for j in b["jobs"]}
        assert names == {"zlib"}


# ── CUDA host probe ─────────────────────────────────────────────


class TestProbeCuda:
    """The probe backing --capability auto-detect (and resolver gating)."""

    def _clear(self, monkeypatch):
        import cvcpkg.platform as plat_mod

        monkeypatch.setattr(plat_mod, "_probed_capabilities", None)
        monkeypatch.delenv("CVCPKG_CAPABILITIES", raising=False)
        monkeypatch.delenv("CUDA_PATH", raising=False)
        monkeypatch.delenv("CUDA_HOME", raising=False)
        monkeypatch.setattr("ctypes.util.find_library", lambda _n: None)
        monkeypatch.setattr("shutil.which", lambda _n: None)
        return plat_mod

    def test_no_cuda_anywhere(self, monkeypatch):
        plat_mod = self._clear(monkeypatch)
        assert plat_mod._probe_cuda() is False

    def test_nvcc_on_path(self, monkeypatch):
        plat_mod = self._clear(monkeypatch)
        monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/nvcc" if n == "nvcc" else None)
        assert plat_mod._probe_cuda() is True

    def test_cuda_path_env_without_nvcc_on_path(self, tmp_path, monkeypatch):
        # The Windows service-account case: the toolkit sets CUDA_PATH
        # system-wide but a schtasks /RU SYSTEM builder's PATH lacks bin/.
        plat_mod = self._clear(monkeypatch)
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "nvcc.exe").write_text("")
        monkeypatch.setenv("CUDA_PATH", str(tmp_path))
        assert plat_mod._probe_cuda() is True

    def test_cuda_path_env_pointing_nowhere(self, tmp_path, monkeypatch):
        plat_mod = self._clear(monkeypatch)
        monkeypatch.setenv("CUDA_PATH", str(tmp_path / "does-not-exist"))
        assert plat_mod._probe_cuda() is False

    def test_env_override_wins_over_probe(self, monkeypatch):
        plat_mod = self._clear(monkeypatch)
        monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/nvcc" if n == "nvcc" else None)
        monkeypatch.setenv("CVCPKG_CAPABILITIES", "")
        assert plat_mod.host_capabilities() == set()
        monkeypatch.setenv("CVCPKG_CAPABILITIES", "cuda,avx512")
        assert plat_mod.host_capabilities() == {"cuda", "avx512"}
