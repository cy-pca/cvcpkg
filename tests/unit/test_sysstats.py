"""cvcpkg.sysstats: the host figures a builder sends with each heartbeat."""

from __future__ import annotations

import pytest

from cvcpkg import sysstats


def test_collect_reports_what_it_can_and_omits_unknowns(tmp_path):
    cpu = sysstats.CpuSampler()
    stats = sysstats.collect(tmp_path, cpu)
    assert all(v is not None for v in stats.values())
    assert stats["cpu_count"] >= 1
    assert stats["disk_total_bytes"] >= stats["disk_free_bytes"] > 0
    assert stats["python"]
    if "cpu_percent" in stats:
        assert 0 <= stats["cpu_percent"] <= 100


def test_collect_matches_the_server_model(tmp_path):
    models = pytest.importorskip("cvcpkg.server.models")
    stats = sysstats.collect(tmp_path, sysstats.CpuSampler())
    parsed = models.BuilderSystemStats.model_validate(stats)
    assert parsed.model_dump(exclude_none=True).keys() == stats.keys()


def test_cpu_percent_is_the_busy_share_between_samples(monkeypatch):
    samples = iter([(100, 1000), (150, 1100), (150, 1100)])
    monkeypatch.setattr(sysstats, "_cpu_times", lambda: next(samples))
    cpu = sysstats.CpuSampler()
    assert cpu.percent() == 50.0
    # No time passed: no figure rather than a division by zero.
    assert cpu.percent() is None


def test_bsd_cp_time_reads_idle_as_the_last_counter(monkeypatch):
    monkeypatch.setattr(sysstats.sys, "platform", "netbsd10")
    for raw, busy in (
        ("10 0 20 5 65", 35),  # FreeBSD/NetBSD: user nice sys intr idle
        ("10,0,20,1,5,64", 36),  # OpenBSD: user nice sys spin intr idle
        ("user = 10 nice = 0 sys = 20 intr = 5 idle = 65", 35),
    ):
        monkeypatch.setattr(sysstats, "_sysctl", lambda name, raw=raw: raw)
        assert sysstats._cpu_times() == (busy, 100)


def test_a_failing_probe_is_left_out(monkeypatch, tmp_path):
    monkeypatch.setattr(sysstats, "_memory", lambda: (None, None))
    monkeypatch.setattr(sysstats, "_host_uptime", lambda: None)
    stats = sysstats.collect(tmp_path)
    assert "mem_total_bytes" not in stats
    assert "uptime_seconds" not in stats
    assert "cpu_percent" not in stats  # no sampler given
