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


def _mh_builder(served, org=None, platform="linux", arch="x86_64", cur=0, mx=2, id=1):
    """A multi-homed builder that advertises an explicit served-namespace set.

    ``org`` (home / identity) defaults to the first served namespace.
    """
    served = list(served)
    return NS(
        id=id,
        org_slug=org if org is not None else (served[0] if served else ""),
        served_namespaces=served,
        platform=platform,
        arch=arch,
        capabilities={"cross_platforms": []},
        current_jobs=cur,
        max_jobs=mx,
        prefer_affinity=False,
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


class TestNoarchDispatch:
    """A platform-independent (any/noarch) job runs on any builder in namespace."""

    def test_noarch_job_runs_on_any_platform_builder(self):
        # A noarch job has no host of its own; a linux builder can build it.
        b = _builder(platform="linux", arch="x86_64")
        assert _choose_builder(_job(platform="any", arch="noarch"), [b]) is b

    def test_noarch_job_runs_on_a_differently_named_host(self):
        # Even a macOS/arm64 builder can produce the single noarch bundle.
        b = _builder(platform="macos", arch="arm64")
        assert _choose_builder(_job(platform="any", arch="noarch"), [b]) is b

    def test_noarch_job_still_respects_namespace_isolation(self):
        # Being noarch does not let it cross a namespace boundary.
        assert (
            _choose_builder(_job(org="shell", platform="any", arch="noarch"), [_builder(org="")])
            is None
        )

    def test_noarch_job_respects_capacity(self):
        assert _choose_builder(_job(platform="any", arch="noarch"), [_builder(cur=2, mx=2)]) is None

    def test_noarch_job_with_no_builders_returns_none(self):
        assert _choose_builder(_job(platform="any", arch="noarch"), []) is None


class TestServedNamespaces:
    """Multi-tenant shared fleet: a builder serving a SET of namespaces."""

    def test_builder_serving_public_and_org_takes_both(self):
        b = _mh_builder(["", "cvc"])
        assert _choose_builder(_job(org=""), [b]) is b
        assert _choose_builder(_job(org="cvc"), [b]) is b

    def test_served_set_still_refuses_unlisted_namespace(self):
        b = _mh_builder(["", "cvc"])
        assert _choose_builder(_job(org="acme"), [b]) is None

    def test_org_only_builder_refuses_public(self):
        # served == ["cvc"] (home cvc, does not opt into public)
        b = _mh_builder(["cvc"])
        assert _choose_builder(_job(org=""), [b]) is None
        assert _choose_builder(_job(org="cvc"), [b]) is b

    def test_multi_org_builder(self):
        b = _mh_builder(["cvc", "cypca"], org="cvc")
        assert _choose_builder(_job(org="cvc"), [b]) is b
        assert _choose_builder(_job(org="cypca"), [b]) is b
        assert _choose_builder(_job(org=""), [b]) is None

    def test_mixed_fleet_routes_by_served_set(self):
        pub_only = _mh_builder([""], id=1)
        shared = _mh_builder(["", "cvc"], org="cvc", id=2)
        # A cvc job can only go to the builder that serves cvc.
        assert _choose_builder(_job(org="cvc"), [pub_only, shared]) is shared
        # A public job could run on either; the first candidate wins.
        assert _choose_builder(_job(org=""), [pub_only, shared]) is pub_only

    def test_capacity_still_enforced_on_shared_builder(self):
        b = _mh_builder(["", "cvc"], cur=2, mx=2)
        assert _choose_builder(_job(org="cvc"), [b]) is None
