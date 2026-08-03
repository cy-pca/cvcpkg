"""submit-dag must express dependency edges that cross the noarch boundary.

``depends_on`` is resolved as indices INTO THE SUBMITTED BODY, so an edge only
exists between jobs in the same submission.  Emitting one DAG per platform plus
a separate noarch DAG dropped every crossing edge and the two raced:

* concrete → noarch: h5py-cp312 (linux) started before cython/pkgconfig/wheel
  (noarch) were published and died in its PEP-517 backend.
* noarch → concrete: jinja2-cp313t (noarch) started before markupsafe-cp313t
  (linux) and died on the missing import.

Both directions occur, so no ordering of two separate DAGs fixes it — the jobs
have to be submitted together.  These tests pin that they are.
"""

from __future__ import annotations

import pytest

from cvcpkg.cli import main

# ── Harness ─────────────────────────────────────────────────────


def _write(tmp_path, name, *, platforms=("linux",), deps=(), version="1.0.0"):
    """Write a recipe; platforms=("any",) makes it noarch."""
    rdir = tmp_path / "recipes" / name
    rdir.mkdir(parents=True)
    deps_yaml = "".join(f"    - name: {d}\n" for d in deps)
    matrix = "".join(f"    - platform: {p}\n      script: build.sh\n" for p in platforms)
    (rdir / "recipe.yaml").write_text(
        f"""schema_version: 1
recipe:
  name: {name}
  upstream_version: "{version}"
  cvc_revision: 1
depends:
  build:
{deps_yaml}build:
  matrix:
{matrix}"""
    )
    return rdir


def _fake_client(posted, builders=None):
    if builders is None:
        builders = [{"platform": "linux", "arch": "x86_64", "capabilities": {}}]

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
            return FakeResp(
                {"dag_id": f"dag-{len(posted)}", "total": len(json["jobs"]), "jobs": []}
            )

    return FakeClient


def _submit(tmp_path, monkeypatch, posted, *names, platform="linux", builders=None, deps=True):
    monkeypatch.setattr("httpx.Client", _fake_client(posted, builders))
    argv = [
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
    ]
    if not deps:
        argv.append("--no-deps")
    return main([*argv, *names])


def _edges(body):
    """{recipe_name: {dep recipe names}} for one submitted DAG body."""
    jobs = body["jobs"]
    out = {}
    for j in jobs:
        out[j["recipe_name"]] = {jobs[i]["recipe_name"] for i in j["depends_on"]}
    return out


# ── Tests ───────────────────────────────────────────────────────


class TestCrossNoarchEdges:
    def test_concrete_job_depends_on_noarch_build_tool(self, tmp_path, monkeypatch):
        # h5py (linux) needs cython (noarch) — the edge that used to vanish.
        _write(tmp_path, "cython-cp312", platforms=("any",))
        _write(tmp_path, "h5py-cp312", platforms=("linux",), deps=("cython-cp312",))
        posted: list = []
        assert _submit(tmp_path, monkeypatch, posted, "h5py-cp312", "cython-cp312") == 0

        assert len(posted) == 1, "concrete + noarch must share ONE submission"
        assert _edges(posted[0])["h5py-cp312"] == {"cython-cp312"}

    def test_noarch_job_depends_on_concrete_at_the_build_target(self, tmp_path, monkeypatch):
        # jinja2 (noarch) needs markupsafe (linux).  A noarch job is BUILT on
        # the reference target, so the edge must point at that platform's job.
        _write(tmp_path, "markupsafe-cp313t", platforms=("linux",))
        _write(tmp_path, "jinja2-cp313t", platforms=("any",), deps=("markupsafe-cp313t",))
        posted: list = []
        assert _submit(tmp_path, monkeypatch, posted, "jinja2-cp313t", "markupsafe-cp313t") == 0

        assert len(posted) == 1
        body = posted[0]
        assert _edges(body)["jinja2-cp313t"] == {"markupsafe-cp313t"}
        dep_job = next(
            body["jobs"][i]
            for j in body["jobs"]
            if j["recipe_name"] == "jinja2-cp313t"
            for i in j["depends_on"]
        )
        assert (dep_job["platform"], dep_job["arch"]) == ("linux", "x86_64")

    def test_one_dag_per_config_link_across_platforms(self, tmp_path, monkeypatch):
        _write(tmp_path, "tool", platforms=("any",))
        _write(tmp_path, "lib", platforms=("linux", "windows"), deps=("tool",))
        posted: list = []
        builders = [
            {"platform": "linux", "arch": "x86_64", "capabilities": {}},
            {"platform": "windows", "arch": "x86_64", "capabilities": {}},
        ]
        assert (
            _submit(
                tmp_path,
                monkeypatch,
                posted,
                "lib",
                "tool",
                platform="linux,windows",
                builders=builders,
            )
            == 0
        )
        # One body holding both platforms AND the noarch job.
        assert len(posted) == 1
        jobs = posted[0]["jobs"]
        targets = {(j["recipe_name"], j["platform"]) for j in jobs}
        assert ("lib", "linux") in targets
        assert ("lib", "windows") in targets
        assert ("tool", "any") in targets
        # Each platform's lib depends on the single shared noarch tool.
        for j in jobs:
            if j["recipe_name"] == "lib":
                assert [jobs[i]["recipe_name"] for i in j["depends_on"]] == ["tool"]

    def test_same_platform_edge_still_wins_over_noarch(self, tmp_path, monkeypatch):
        # A concrete dep must resolve to this platform's job, not a noarch one
        # of the same name (the concrete build is what this job links against).
        _write(tmp_path, "zlib", platforms=("linux",))
        _write(tmp_path, "app", platforms=("linux",), deps=("zlib",))
        posted: list = []
        assert _submit(tmp_path, monkeypatch, posted, "app", "zlib") == 0
        body = posted[0]
        dep_job = next(
            body["jobs"][i]
            for j in body["jobs"]
            if j["recipe_name"] == "app"
            for i in j["depends_on"]
        )
        assert dep_job["recipe_name"] == "zlib"
        assert dep_job["platform"] == "linux"

    def test_no_self_edge(self, tmp_path, monkeypatch):
        # A recipe that lists itself (or is reached twice) must not depend on
        # its own index — the server would deadlock the job forever.
        _write(tmp_path, "solo", platforms=("linux",), deps=("solo",))
        posted: list = []
        assert _submit(tmp_path, monkeypatch, posted, "solo") == 0
        for j in posted[0]["jobs"]:
            assert j["depends_on"] == []


class TestAutoDepsAcrossTheBoundary:
    def test_unpublished_noarch_dep_is_pulled_in_and_ordered(self, tmp_path, monkeypatch):
        # Only the concrete recipe is named; its unpublished noarch build tool
        # used to be flagged "cannot be scheduled" and left out entirely.
        _write(tmp_path, "wheel-cp312", platforms=("any",))
        _write(tmp_path, "h5py-cp312", platforms=("linux",), deps=("wheel-cp312",))
        posted: list = []
        assert _submit(tmp_path, monkeypatch, posted, "h5py-cp312") == 0

        assert len(posted) == 1
        names = {j["recipe_name"] for j in posted[0]["jobs"]}
        assert names == {"h5py-cp312", "wheel-cp312"}
        assert _edges(posted[0])["h5py-cp312"] == {"wheel-cp312"}


@pytest.mark.parametrize("flag", [True, False])
def test_submission_is_wellformed_regardless_of_deps_flag(tmp_path, monkeypatch, flag):
    _write(tmp_path, "tool", platforms=("any",))
    _write(tmp_path, "lib", platforms=("linux",), deps=("tool",))
    posted: list = []
    assert _submit(tmp_path, monkeypatch, posted, "lib", "tool", deps=flag) == 0
    for body in posted:
        n = len(body["jobs"])
        for j in body["jobs"]:
            for i in j["depends_on"]:
                assert 0 <= i < n, "depends_on index must be in range"
