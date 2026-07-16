"""Build-job -> builder matching (_choose_builder), incl. org isolation.

A build job is dispatched only to a builder in the SAME org namespace: public
jobs run on public builders, and an organization's (private) jobs run only on
that org's builders. This keeps a private build off public builders and stops a
private builder from running someone else's work.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")

from cvcpkg.server.app import _choose_builder


def _builder(org="", platform="linux", arch="x86_64", cross=None, cur=0, mx=2, aff=False, id=1):
    return NS(
        id=id,
        org_slug=org,
        platform=platform,
        arch=arch,
        capabilities={"cross_platforms": cross or []},
        current_jobs=cur,
        max_jobs=mx,
        prefer_affinity=aff,
    )


def _job(org="", platform="linux", arch="x86_64"):
    return NS(org_slug=org, platform=platform, arch=arch)


class TestOrgIsolation:
    def test_public_job_runs_on_public_builder(self):
        b = _builder(org="")
        assert _choose_builder(_job(org=""), [b]) is b

    def test_public_job_not_dispatched_to_org_builder(self):
        assert _choose_builder(_job(org=""), [_builder(org="shell")]) is None

    def test_org_job_not_dispatched_to_public_builder(self):
        # The core security property: a private build never lands on a public builder.
        assert _choose_builder(_job(org="shell"), [_builder(org="")]) is None

    def test_org_job_runs_on_its_org_builder(self):
        b = _builder(org="shell")
        assert _choose_builder(_job(org="shell"), [b]) is b

    def test_org_job_not_dispatched_to_other_org_builder(self):
        assert _choose_builder(_job(org="shell"), [_builder(org="acme")]) is None

    def test_isolation_picks_the_right_builder_from_a_mixed_fleet(self):
        pub = _builder(org="", id=1)
        shell = _builder(org="shell", id=2)
        assert _choose_builder(_job(org="shell"), [pub, shell]) is shell
        assert _choose_builder(_job(org=""), [pub, shell]) is pub

    def test_cross_capable_public_builder_still_refuses_org_job(self):
        b = _builder(org="", platform="linux", cross=[{"platform": "windows", "arch": "x86_64"}])
        assert _choose_builder(_job(org="shell", platform="windows", arch="x86_64"), [b]) is None


class TestMatchingUnchanged:
    def test_platform_arch_mismatch_no_match(self):
        assert _choose_builder(_job(platform="windows"), [_builder(platform="linux")]) is None

    def test_cross_platform_match(self):
        b = _builder(platform="linux", cross=[{"platform": "windows", "arch": "x86_64"}])
        assert _choose_builder(_job(platform="windows", arch="x86_64"), [b]) is b

    def test_legacy_platform_only_cross_target(self):
        b = _builder(platform="linux", cross=["wasm"])
        assert _choose_builder(_job(platform="wasm", arch="wasm32"), [b]) is b

    def test_full_builder_excluded(self):
        assert _choose_builder(_job(), [_builder(cur=2, mx=2)]) is None

    def test_affinity_preferred(self):
        plain = _builder(id=1, aff=False)
        aff = _builder(id=2, aff=True)
        assert _choose_builder(_job(), [plain, aff]) is aff

    def test_no_builders_returns_none(self):
        assert _choose_builder(_job(), []) is None
