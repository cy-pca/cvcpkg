"""Recipe source downloads retry transport failures, DNS included.

Every source URL used to get one attempt.  On the 2026-10-14 populates a
macOS runner's resolver failed ("nodename nor servname provided, or not
known") for about 40 downloads in a row, and linux/arm64 lost a batch to
connection resets, each failure taking its dependents with it.
"""

from __future__ import annotations

import hashlib
import socket
import ssl
import urllib.error
from pathlib import Path

import pytest

from cvcpkg import builder
from cvcpkg.builder import RecipeError
from cvcpkg.retry import is_transient, is_transient_fetch


def _dns_failure() -> urllib.error.URLError:
    return urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided, or not known"))


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x/y", code, "err", {}, None)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)


# ── what a source fetch retries ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "exc",
    [
        _dns_failure(),
        socket.gaierror(-3, "Temporary failure in name resolution"),
        urllib.error.URLError(ConnectionResetError(104, "Connection reset by peer")),
        urllib.error.URLError(TimeoutError(60, "Operation timed out")),
        _http_error(503),
    ],
)
def test_fetch_retries_transport_failures(exc):
    assert is_transient_fetch(exc)


@pytest.mark.parametrize(
    "exc",
    [
        _http_error(404),
        _http_error(403),
        _http_error(500),
        urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed")),
        ValueError("not a transport error"),
    ],
)
def test_fetch_does_not_retry_real_answers(exc):
    assert not is_transient_fetch(exc)


def test_install_path_still_does_not_retry_dns():
    # The install path keeps its stricter policy: an unknown host there is as
    # likely a typo as an outage.
    assert not is_transient(_dns_failure())


# ── the download helper ─────────────────────────────────────────────────────


def _fake_urlretrieve(outcomes, payload=b"data"):
    calls = []

    def fake(url, path):
        calls.append(url)
        out = outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        Path(path).write_bytes(payload)
        return str(path), None

    return fake, calls


def test_helper_survives_a_dns_outage(monkeypatch, tmp_path):
    fake, calls = _fake_urlretrieve([_dns_failure(), _dns_failure(), None])
    monkeypatch.setattr("urllib.request.urlretrieve", fake)
    builder._urlretrieve_retrying("https://example.org/x.tar.gz", tmp_path / "x")
    assert len(calls) == 3
    assert (tmp_path / "x").read_bytes() == b"data"


def test_helper_gives_up_after_the_attempt_limit(monkeypatch, tmp_path):
    fake, calls = _fake_urlretrieve([_dns_failure()] * 10)
    monkeypatch.setattr("urllib.request.urlretrieve", fake)
    with pytest.raises(urllib.error.URLError):
        builder._urlretrieve_retrying("https://example.org/x.tar.gz", tmp_path / "x")
    assert len(calls) == builder._SOURCE_FETCH_ATTEMPTS


def test_helper_does_not_retry_a_404(monkeypatch, tmp_path):
    fake, calls = _fake_urlretrieve([_http_error(404), None])
    monkeypatch.setattr("urllib.request.urlretrieve", fake)
    with pytest.raises(urllib.error.HTTPError):
        builder._urlretrieve_retrying("https://example.org/x.tar.gz", tmp_path / "x")
    assert len(calls) == 1


def test_pinned_wheel_download_retries(monkeypatch, tmp_path):
    payload = b"wheel bytes"
    sha = hashlib.sha256(payload).hexdigest()
    fake, calls = _fake_urlretrieve([_dns_failure(), None], payload=payload)
    monkeypatch.setattr("urllib.request.urlretrieve", fake)
    monkeypatch.setattr(builder, "_source_cache_dir", lambda: None)
    out = builder._download_pinned_wheel(
        "https://files.example.org/w-1.0-py3-none-any.whl", sha, "w-1.0-py3-none-any.whl", tmp_path
    )
    assert out.read_bytes() == payload
    assert len(calls) == 2


def test_pinned_wheel_download_reports_a_permanent_failure(monkeypatch, tmp_path):
    fake, _calls = _fake_urlretrieve([_http_error(404)])
    monkeypatch.setattr("urllib.request.urlretrieve", fake)
    monkeypatch.setattr(builder, "_source_cache_dir", lambda: None)
    with pytest.raises(RecipeError, match="failed to download wheel"):
        builder._download_pinned_wheel(
            "https://files.example.org/w.whl", "0" * 64, "w-1.0-py3-none-any.whl", tmp_path
        )
