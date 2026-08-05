"""Disk-aware scheduling — a job whose recipe declares ``build.min_disk_gb``
(e.g. 35 for haiku-image) is dispatched only to a builder advertising at least
that much free on its work volume, and submit-dag refuses to submit it when no
registered builder has the space.

The quantitative sibling of build-time capability routing (see
tests/unit/test_capability_routing.py) and deliberately shaped the same way,
with one property that has no capability analogue: a builder advertising NO
figure means *unknown*, never zero, so a fleet of agents predating this feature
keeps taking the jobs instead of stranding them.

Covers the recipe schema field, the scheduler (`_choose_builder`), the job
store round-trip, the REST surface (registration, heartbeat refresh, anonymous
next-claimable drain), the fleet config, and submit-dag's up-front report.
"""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi", reason="server extras not installed")
aiosqlite = pytest.importorskip("aiosqlite", reason="aiosqlite required for build job tests")

from types import SimpleNamespace

from fastapi.testclient import TestClient

from cvcpkg.server.app import _choose_builder, create_app
from cvcpkg.server.models import TokenRole

# ── Recipe schema: build.min_disk_gb ────────────────────────────


class TestRecipeSchemaMinDiskGb:
    def _build_block(self):
        from cvcpkg.validation import load_schema

        return load_schema("recipe")["$defs"]["build_block"]

    def test_schema_declares_min_disk_gb(self):
        prop = self._build_block()["properties"]["min_disk_gb"]
        assert prop["type"] == "integer"
        # A recipe needing 0 GiB is a recipe with no requirement, which is
        # spelled by omitting the field.
        assert prop["minimum"] == 1
        assert "min_disk_gb" not in self._build_block().get("required", [])

    def _doc(self, min_disk):
        """A schema-complete recipe carrying *min_disk* as build.min_disk_gb."""
        return {
            "schema_version": 1,
            "recipe": {"name": "haiku-image", "upstream_version": "1.0.0", "cvc_revision": 1},
            "source": {"type": "prebuilt", "url": "https://example.invalid/haiku"},
            "build": {
                "min_disk_gb": min_disk,
                "matrix": [{"platform": "linux", "script": "build.sh"}],
            },
            "package": {"files": ["haiku-builder.qcow2"]},
        }

    def test_recipe_with_min_disk_gb_validates(self):
        import jsonschema

        from cvcpkg.validation import load_schema

        jsonschema.validate(self._doc(35), load_schema("recipe"))

    def test_non_integer_min_disk_gb_is_rejected(self):
        import jsonschema

        from cvcpkg.validation import load_schema

        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(self._doc("35G"), load_schema("recipe"))

    def test_zero_min_disk_gb_is_rejected(self):
        # "no requirement" is spelled by omitting the field, not by 0 — which
        # would otherwise read as an assertion that the build needs no disk.
        import jsonschema

        from cvcpkg.validation import load_schema

        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(self._doc(0), load_schema("recipe"))

    def test_haiku_image_recipe_declares_its_35_gib(self):
        """The recipe that motivated the feature actually carries the field.

        haiku-image is the in-repo case: a 26 GiB dev-cluster builder took the
        job and only found out inside build.sh.
        """
        from pathlib import Path

        import yaml

        repo_recipe = (
            Path(__file__).resolve().parents[2] / "recipes" / "haiku-image" / "recipe.yaml"
        )
        if not repo_recipe.is_file():
            pytest.skip("recipes/ not present (installed-package test run)")
        doc = yaml.safe_load(repo_recipe.read_text())
        assert doc["build"]["min_disk_gb"] == 35


# ── _choose_builder ─────────────────────────────────────────────


def _builder(free=None, platform="linux", arch="x86_64", cur=0, mx=2, id=1):
    return SimpleNamespace(
        id=id,
        org_slug="",
        platform=platform,
        arch=arch,
        capabilities={},
        free_disk_gb=free,
        current_jobs=cur,
        max_jobs=mx,
        prefer_affinity=False,
    )


def _job(min_disk=None, platform="linux", arch="x86_64"):
    return SimpleNamespace(
        org_slug="",
        platform=platform,
        arch=arch,
        required_capabilities=[],
        min_disk_gb=min_disk,
    )


class TestChooseBuilderDisk:
    def test_big_job_never_lands_on_a_small_builder(self):
        # The event this feature exists for: 35 GiB onto 26 GiB free.
        small = _builder(free=26)
        assert _choose_builder(_job(min_disk=35), [small]) is None

    def test_big_job_lands_on_a_big_builder(self):
        big = _builder(free=200)
        assert _choose_builder(_job(min_disk=35), [big]) is big

    def test_big_job_picks_the_builder_with_room(self):
        small = _builder(free=26, id=1)
        big = _builder(free=200, id=2)
        assert _choose_builder(_job(min_disk=35), [small, big]) is big

    def test_exactly_enough_counts_as_enough(self):
        exact = _builder(free=35)
        assert _choose_builder(_job(min_disk=35), [exact]) is exact

    def test_small_job_still_lands_on_a_big_builder(self):
        # Free disk only ADDS eligibility; it never reserves the builder for
        # heavy jobs.
        big = _builder(free=200)
        assert _choose_builder(_job(), [big]) is big

    def test_job_without_a_requirement_ignores_a_nearly_full_builder(self):
        nearly_full = _builder(free=1)
        assert _choose_builder(_job(), [nearly_full]) is nearly_full

    def test_disk_respects_capacity(self):
        busy = _builder(free=200, cur=2, mx=2)
        assert _choose_builder(_job(min_disk=35), [busy]) is None


class TestDebitDiskWithinOneTick:
    """One scheduler tick can dispatch several jobs before any heartbeat lands.

    ``_debit_disk`` is what stops a 40 GiB builder taking two 35 GiB jobs in
    the same pass, both of them measured against the same stale figure.
    """

    def test_second_oversized_job_no_longer_fits(self):
        from cvcpkg.server.app import _debit_disk

        b = _builder(free=40, mx=4)
        first = _job(min_disk=35)
        assert _choose_builder(first, [b]) is b
        _debit_disk(b, first)
        assert _choose_builder(_job(min_disk=35), [b]) is None

    def test_debit_never_goes_negative(self):
        from cvcpkg.server.app import _debit_disk

        b = _builder(free=10, mx=4)
        _debit_disk(b, _job(min_disk=35))
        assert b.free_disk_gb == 0

    def test_job_without_a_requirement_debits_nothing(self):
        from cvcpkg.server.app import _debit_disk

        b = _builder(free=40)
        _debit_disk(b, _job())
        assert b.free_disk_gb == 40

    def test_unknown_stays_unknown_rather_than_becoming_zero(self):
        from cvcpkg.server.app import _debit_disk

        b = _builder(free=None)
        _debit_disk(b, _job(min_disk=35))
        assert b.free_disk_gb is None


class TestChooseBuilderDiskUnknown:
    """A missing figure is *unknown*, and unknown fails OPEN.

    Reading "advertises nothing" as "0 GiB free" would make every builder that
    predates this feature ineligible for every disk-bearing job — the recipe
    would go from occasionally mispaired to permanently unschedulable, which is
    strictly worse.  The recipe's own df preflight is what covers the gap.
    """

    def test_builder_advertising_nothing_still_takes_a_big_job(self):
        legacy = _builder(free=None)
        assert _choose_builder(_job(min_disk=35), [legacy]) is legacy

    def test_builder_missing_the_attribute_entirely_still_takes_a_big_job(self):
        # An in-flight object from an older code path has no attribute at all,
        # not merely a None one.
        legacy = SimpleNamespace(
            id=1,
            org_slug="",
            platform="linux",
            arch="x86_64",
            capabilities={},
            current_jobs=0,
            max_jobs=2,
            prefer_affinity=False,
        )
        assert _choose_builder(_job(min_disk=35), [legacy]) is legacy

    def test_job_missing_the_attribute_entirely_is_unconstrained(self):
        job = SimpleNamespace(
            org_slug="", platform="linux", arch="x86_64", required_capabilities=[]
        )
        small = _builder(free=1)
        assert _choose_builder(job, [small]) is small


# ── free_disk_gb() host probe ───────────────────────────────────


class TestFreeDiskProbe:
    def test_measures_an_existing_dir(self, tmp_path):
        from cvcpkg.platform import free_disk_gb

        got = free_disk_gb(tmp_path)
        assert got is None or (isinstance(got, int) and got >= 0)

    def test_missing_path_falls_back_to_its_nearest_existing_ancestor(self, tmp_path):
        from cvcpkg.platform import free_disk_gb

        # The builder measures before its first job has created the work dir.
        missing = tmp_path / "not" / "created" / "yet"
        assert free_disk_gb(missing) == free_disk_gb(tmp_path)

    def test_none_measures_the_system_temp_dir(self):
        import tempfile

        from cvcpkg.platform import free_disk_gb

        # --work-dir unset means jobs mkdtemp into the system temp dir.
        assert free_disk_gb(None) == free_disk_gb(tempfile.gettempdir())

    def test_truncates_rather_than_rounds(self, tmp_path, monkeypatch):
        import shutil

        from cvcpkg.platform import free_disk_gb

        # 34.9 GiB free must report 34, never 35: rounding up would satisfy a
        # 35 GiB requirement it cannot actually meet.
        monkeypatch.setattr(
            shutil,
            "disk_usage",
            lambda _p: SimpleNamespace(total=0, used=0, free=int(34.9 * 1024**3)),
        )
        assert free_disk_gb(tmp_path) == 34

    def test_probe_failure_is_unknown_not_zero(self, tmp_path, monkeypatch):
        import shutil

        from cvcpkg.platform import free_disk_gb

        def _boom(_p):
            raise OSError("unreadable mount")

        monkeypatch.setattr(shutil, "disk_usage", _boom)
        assert free_disk_gb(tmp_path) is None


# ── Job store round-trip ────────────────────────────────────────


class TestMinDiskGbStore:
    @pytest.fixture(autouse=True)
    def _setup_db(self, tmp_path, monkeypatch):
        db_path = tmp_path / "disk_routing.db"
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

    def test_create_round_trips_min_disk_gb(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            info = await store.create(
                recipe_name="haiku-image",
                platform="linux",
                arch="x86_64",
                submitted_by="test-admin",
                min_disk_gb=35,
            )
            assert info.min_disk_gb == 35
            fetched = await store.get(info.id)
            assert fetched.min_disk_gb == 35

            plain = await store.create(
                recipe_name="zlib",
                platform="linux",
                arch="x86_64",
                submitted_by="test-admin",
            )
            assert plain.min_disk_gb is None

        self._run(_test())

    def test_create_dag_round_trips_min_disk_gb(self):
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            jobs = await store.create_dag(
                jobs=[
                    {"recipe_name": "zlib", "platform": "linux", "arch": "x86_64"},
                    {
                        "recipe_name": "haiku-image",
                        "platform": "linux",
                        "arch": "x86_64",
                        "min_disk_gb": 35,
                        "depends_on": [0],
                    },
                ],
                dag_id="dag-disk",
                submitted_by="test-admin",
            )
            assert jobs[0].min_disk_gb is None
            assert jobs[1].min_disk_gb == 35
            fetched = await store.get(jobs[1].id)
            assert fetched.min_disk_gb == 35

        self._run(_test())

    def test_reaper_never_marks_a_job_unschedulable_for_disk(self):
        """Free disk is transient, so it must not cancel a DAG.

        A capability is a property of the host that will not appear on its own;
        free space comes back the moment the builder's GC sweep runs or a
        neighbouring job finishes.  Reaping on it would cancel every downstream
        dependent because the fleet was momentarily full.
        """
        from cvcpkg.server.db_stores import DbBuildJobStore

        async def _test():
            store = DbBuildJobStore()
            big = await store.create(
                recipe_name="haiku-image",
                platform="linux",
                arch="x86_64",
                submitted_by="test-admin",
                min_disk_gb=35,
            )
            # One linux builder covering the target; the reaper knows nothing
            # about disk and must leave the job pending.
            reaped = await store.reap_unschedulable(
                {("linux", "x86_64")},
                set(),
                min_age_seconds=0,
                builder_offers=[({("linux", "x86_64")}, set(), set())],
            )
            assert big.id not in {j.id for j in reaped}

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


class TestDiskAPI:
    def test_builder_registration_advertises_free_disk(self, db_server_env):
        client, _admin, pub, _tmp = db_server_env
        resp = client.post(
            "/v1/builders/register",
            headers=_auth(pub),
            json={
                "name": "star-00",
                "platform": "linux",
                "arch": "x86_64",
                "free_disk_gb": 200,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["free_disk_gb"] == 200

        listed = client.get("/v1/builders", headers=_auth(pub)).json()["builders"]
        me = next(b for b in listed if b["name"] == "star-00")
        assert me["free_disk_gb"] == 200

    def test_builder_registering_without_free_disk_is_unknown_not_zero(self, db_server_env):
        client, _admin, pub, _tmp = db_server_env
        resp = client.post(
            "/v1/builders/register",
            headers=_auth(pub),
            json={"name": "legacy-agent", "platform": "linux", "arch": "x86_64"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["free_disk_gb"] is None

    def test_heartbeat_refreshes_free_disk(self, db_server_env):
        client, _admin, pub, _tmp = db_server_env
        bid = client.post(
            "/v1/builders/register",
            headers=_auth(pub),
            json={
                "name": "star-00",
                "platform": "linux",
                "arch": "x86_64",
                "free_disk_gb": 200,
            },
        ).json()["id"]
        # A build ate the volume between beats; the scheduler must match
        # against the new number, not the registration-time one.
        resp = client.post(
            f"/v1/builders/{bid}/heartbeat",
            headers=_auth(pub),
            json={"status": "online", "current_jobs": 1, "free_disk_gb": 12},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["free_disk_gb"] == 12

    def test_heartbeat_without_the_field_leaves_the_stored_value_alone(self, db_server_env):
        client, _admin, pub, _tmp = db_server_env
        bid = client.post(
            "/v1/builders/register",
            headers=_auth(pub),
            json={
                "name": "star-00",
                "platform": "linux",
                "arch": "x86_64",
                "free_disk_gb": 200,
            },
        ).json()["id"]
        # An older agent's beat must not zero (or clear) a good figure.
        resp = client.post(
            f"/v1/builders/{bid}/heartbeat",
            headers=_auth(pub),
            json={"status": "online", "current_jobs": 0},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["free_disk_gb"] == 200

    def test_dag_submit_round_trips_min_disk_gb(self, db_server_env):
        client, _admin, pub, _tmp = db_server_env
        resp = client.post(
            "/v1/builds/dag",
            headers=_auth(pub),
            json={
                "jobs": [
                    {"recipe_name": "zlib", "platform": "linux", "arch": "x86_64"},
                    {
                        "recipe_name": "haiku-image",
                        "platform": "linux",
                        "arch": "x86_64",
                        "min_disk_gb": 35,
                        "depends_on": [0],
                    },
                ]
            },
        )
        assert resp.status_code == 200, resp.text
        jobs = resp.json()["jobs"]
        assert jobs[0]["min_disk_gb"] is None
        assert jobs[1]["min_disk_gb"] == 35

        job = client.get(f"/v1/builds/{jobs[1]['id']}", headers=_auth(pub)).json()
        assert job["min_disk_gb"] == 35

    def test_single_submit_round_trips_min_disk_gb(self, db_server_env):
        client, _admin, pub, _tmp = db_server_env
        resp = client.post(
            "/v1/builds",
            headers=_auth(pub),
            json={
                "recipe_name": "haiku-image",
                "platform": "linux",
                "arch": "x86_64",
                "min_disk_gb": 35,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["min_disk_gb"] == 35

    def test_next_claimable_gates_on_free_disk(self, db_server_env):
        client, _admin, pub, _tmp = db_server_env
        big = client.post(
            "/v1/builds",
            headers=_auth(pub),
            json={
                "recipe_name": "haiku-image",
                "platform": "linux",
                "arch": "x86_64",
                "min_disk_gb": 35,
                "priority": 10,
            },
        ).json()
        plain = client.post(
            "/v1/builds",
            headers=_auth(pub),
            json={"recipe_name": "zlib", "platform": "linux", "arch": "x86_64"},
        ).json()

        # A cramped drainer skips the big job even at higher priority.
        got = client.get(
            "/v1/builds/next-claimable",
            headers=_auth(pub),
            params={"platform": "linux", "free_disk_gb": 26},
        )
        assert got.status_code == 200
        assert got.json()["id"] == plain["id"]

        # A roomy one gets it (higher priority).
        got = client.get(
            "/v1/builds/next-claimable",
            headers=_auth(pub),
            params={"platform": "linux", "free_disk_gb": 200},
        )
        assert got.status_code == 200
        assert got.json()["id"] == big["id"]

    def test_next_claimable_without_free_disk_is_ungated(self, db_server_env):
        client, _admin, pub, _tmp = db_server_env
        big = client.post(
            "/v1/builds",
            headers=_auth(pub),
            json={
                "recipe_name": "haiku-image",
                "platform": "linux",
                "arch": "x86_64",
                "min_disk_gb": 35,
            },
        ).json()
        # An older drainer states nothing: unknown fails open, same as a
        # registered builder that advertises nothing.
        got = client.get(
            "/v1/builds/next-claimable",
            headers=_auth(pub),
            params={"platform": "linux"},
        )
        assert got.status_code == 200
        assert got.json()["id"] == big["id"]

    def test_next_claimable_204_when_only_oversized_jobs_pend(self, db_server_env):
        client, _admin, pub, _tmp = db_server_env
        client.post(
            "/v1/builds",
            headers=_auth(pub),
            json={
                "recipe_name": "haiku-image",
                "platform": "linux",
                "arch": "x86_64",
                "min_disk_gb": 35,
            },
        )
        got = client.get(
            "/v1/builds/next-claimable",
            headers=_auth(pub),
            params={"platform": "linux", "free_disk_gb": 26},
        )
        assert got.status_code == 204


# ── Fleet config → worker argv ──────────────────────────────────


class TestFleetFreeDisk:
    def test_advertise_free_disk_default_and_override(self):
        from cvcpkg.builder_fleet import parse_fleet_config

        cfg = parse_fleet_config(
            {
                "name": "star-00",
                "servers": [
                    {"server": "https://cvcpkg.org", "token": "cvctok_x"},
                    {
                        "server": "https://pkg.tx.wtf",
                        "token": "cvctok_y",
                        "advertise_free_disk": False,
                    },
                ],
            }
        )
        assert cfg.servers[0].advertise_free_disk is True
        assert cfg.servers[1].advertise_free_disk is False

    def test_worker_argv_carries_no_free_disk(self):
        from cvcpkg.builder_fleet import parse_fleet_config, worker_argv

        cfg = parse_fleet_config(
            {
                "name": "star-00",
                "advertise_free_disk": False,
                "servers": [{"server": "https://cvcpkg.org", "token": "cvctok_x"}],
            }
        )
        assert "--no-free-disk" in worker_argv(cfg.servers[0])

    def test_worker_argv_default_has_no_free_disk_flag(self):
        from cvcpkg.builder_fleet import parse_fleet_config, worker_argv

        cfg = parse_fleet_config(
            {
                "name": "star-00",
                "servers": [{"server": "https://cvcpkg.org", "token": "cvctok_x"}],
            }
        )
        assert "--no-free-disk" not in worker_argv(cfg.servers[0])


# ── submit-dag: recipe build.min_disk_gb → job min_disk_gb ───────
#
# The CLI half: reading the recipe's build.min_disk_gb, stamping it onto every
# job it submits, and reporting up front — before the operator waits on a
# build — that no registered builder has the space.  Mocked-httpx style,
# matching test_capability_routing.py.


def _write_disk_recipe(tmp_path, name, min_disk=None, platforms=("linux",), caps=()):
    """A minimal recipe, optionally declaring build.min_disk_gb."""
    rdir = tmp_path / "recipes" / name
    rdir.mkdir(parents=True)
    disk_line = f"  min_disk_gb: {min_disk}\n" if min_disk else ""
    caps_block = "requires_capabilities:\n" + "".join(f"  - {c}\n" for c in caps) if caps else ""
    matrix = "".join(f"    - platform: {p}\n      script: build.sh\n" for p in platforms)
    (rdir / "recipe.yaml").write_text(
        f"""schema_version: 1
recipe:
  name: {name}
  upstream_version: "1.0.0"
  cvc_revision: 1
{caps_block}build:
{disk_line}  matrix:
{matrix}"""
    )
    return rdir


def _disk_fake_client(posted_bodies, builders):
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


_SMALL_BUILDER = {
    "platform": "linux",
    "arch": "x86_64",
    "capabilities": {},
    "free_disk_gb": 26,  # the dev-cluster builder that failed haiku-image
}
_BIG_BUILDER = {
    "platform": "linux",
    "arch": "x86_64",
    "capabilities": {},
    "free_disk_gb": 200,
}
_LEGACY_BUILDER = {"platform": "linux", "arch": "x86_64", "capabilities": {}}


def _submit_dag(tmp_path, monkeypatch, posted, builders, *recipes, platform="linux"):
    from cvcpkg.cli import main

    monkeypatch.setattr("httpx.Client", _disk_fake_client(posted, builders))
    return main(
        [
            "builds",
            "submit-dag",
            "--server",
            "http://s.example",
            "--token",
            "tok",
            "--platform",
            platform,
            "--arch",
            "x86_64",
            "--recipes-dir",
            str(tmp_path / "recipes"),
            "--no-default-recipes",
            "--no-deps",
            *recipes,
        ]
    )


class TestSubmitDagDiskPropagation:
    def test_recipe_requirement_lands_on_the_job(self, tmp_path, monkeypatch):
        _write_disk_recipe(tmp_path, "haiku-image", min_disk=35)
        posted: list = []
        assert _submit_dag(tmp_path, monkeypatch, posted, [_BIG_BUILDER], "haiku-image") == 0
        jobs = [j for b in posted for j in b["jobs"]]
        assert len(jobs) == 1
        assert jobs[0]["min_disk_gb"] == 35

    def test_job_without_a_requirement_omits_the_field(self, tmp_path, monkeypatch):
        # Absent (not null) so an OLD server ignoring the field behaves
        # exactly as it did before this feature.
        _write_disk_recipe(tmp_path, "zlib")
        posted: list = []
        assert _submit_dag(tmp_path, monkeypatch, posted, [_BIG_BUILDER], "zlib") == 0
        jobs = [j for b in posted for j in b["jobs"]]
        assert len(jobs) == 1
        assert "min_disk_gb" not in jobs[0]

    def test_skipped_when_no_builder_has_the_space(self, tmp_path, monkeypatch):
        _write_disk_recipe(tmp_path, "haiku-image", min_disk=35)
        posted: list = []
        assert _submit_dag(tmp_path, monkeypatch, posted, [_SMALL_BUILDER], "haiku-image") == 0
        assert [j for b in posted for j in b["jobs"]] == []

    def test_skip_message_names_the_recipe_and_its_requirement(self, tmp_path, monkeypatch, capsys):
        """The operator learns this BEFORE waiting on a build."""
        _write_disk_recipe(tmp_path, "haiku-image", min_disk=35)
        posted: list = []
        assert _submit_dag(tmp_path, monkeypatch, posted, [_SMALL_BUILDER], "haiku-image") == 0
        out = capsys.readouterr().out
        assert "Skipping 1 recipe(s)" in out
        assert "no registered builder has enough free disk" in out
        assert "haiku-image (35 GiB)" in out

    def test_roomy_builder_must_also_cover_the_target(self, tmp_path, monkeypatch):
        # A roomy builder on a DIFFERENT platform must not make a linux recipe
        # look schedulable — target and space must come from ONE builder.
        _write_disk_recipe(tmp_path, "haiku-image", min_disk=35)
        win_big = {
            "platform": "windows",
            "arch": "x86_64",
            "capabilities": {},
            "free_disk_gb": 500,
        }
        posted: list = []
        assert (
            _submit_dag(tmp_path, monkeypatch, posted, [_SMALL_BUILDER, win_big], "haiku-image")
            == 0
        )
        assert [j for b in posted for j in b["jobs"]] == []

    def test_builder_advertising_nothing_does_not_block_submission(self, tmp_path, monkeypatch):
        # Whole fleet predates the feature: submit anyway.  The alternative —
        # refusing every min_disk_gb recipe until every agent is upgraded —
        # would make the feature a regression on rollout.
        _write_disk_recipe(tmp_path, "haiku-image", min_disk=35)
        posted: list = []
        assert _submit_dag(tmp_path, monkeypatch, posted, [_LEGACY_BUILDER], "haiku-image") == 0
        jobs = [j for b in posted for j in b["jobs"]]
        assert len(jobs) == 1
        assert jobs[0]["min_disk_gb"] == 35

    def test_plain_recipes_still_submit_alongside_a_skipped_big_one(self, tmp_path, monkeypatch):
        _write_disk_recipe(tmp_path, "haiku-image", min_disk=35)
        _write_disk_recipe(tmp_path, "zlib")
        posted: list = []
        assert (
            _submit_dag(tmp_path, monkeypatch, posted, [_SMALL_BUILDER], "haiku-image", "zlib") == 0
        )
        names = {j["recipe_name"] for b in posted for j in b["jobs"]}
        assert names == {"zlib"}

    def test_space_and_capability_must_come_from_the_same_builder(self, tmp_path, monkeypatch):
        """A recipe needing BOTH cuda and 35 GiB is not placeable by two hosts.

        The cuda box is too small and the roomy box has no cuda, so the
        server's ``_choose_builder`` would never pair this job with either.
        Checking the two requirements independently would let submit-dag post a
        job that then sits pending forever — the disk filter is deliberately
        excluded from the unschedulable reaper, so nothing ever fails it.
        """
        _write_disk_recipe(tmp_path, "cuda-monster", min_disk=35, caps=("cuda",))
        cuda_small = {
            "platform": "linux",
            "arch": "x86_64",
            "capabilities": {"cuda": True},
            "free_disk_gb": 26,
        }
        roomy_no_cuda = {
            "platform": "linux",
            "arch": "x86_64",
            "capabilities": {},
            "free_disk_gb": 500,
        }
        posted: list = []
        assert (
            _submit_dag(tmp_path, monkeypatch, posted, [cuda_small, roomy_no_cuda], "cuda-monster")
            == 0
        )
        assert [j for b in posted for j in b["jobs"]] == []

    def test_one_builder_with_both_still_submits(self, tmp_path, monkeypatch):
        """Control for the above: the combined check must not over-skip."""
        _write_disk_recipe(tmp_path, "cuda-monster", min_disk=35, caps=("cuda",))
        cuda_big = {
            "platform": "linux",
            "arch": "x86_64",
            "capabilities": {"cuda": True},
            "free_disk_gb": 500,
        }
        posted: list = []
        assert _submit_dag(tmp_path, monkeypatch, posted, [cuda_big], "cuda-monster") == 0
        jobs = [j for b in posted for j in b["jobs"]]
        assert len(jobs) == 1
        assert jobs[0]["min_disk_gb"] == 35
        assert "cuda" in jobs[0]["required_capabilities"]
