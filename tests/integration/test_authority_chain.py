"""Authority flows down a chain of servers, and a mirror may still dissent.

Three real cvcpkg servers wired as a linked list:

    A (origin)  <--populate--  B (mirror)  <--populate--  C (leaf)

Each hop is a genuine populate: B imports from A over HTTP, C imports from B.
Nothing shares a database, so a fact only reaches C by actually travelling the
chain.

Two properties, and the second is the one with teeth:

1.  A yank at the top reaches the bottom.  It has to traverse B to get to C --
    C never talks to A -- so this catches a propagation that stops one hop in.

2.  A mirror can dissent, and that dissent survives.  If C's operator unyanks a
    bundle A retired, the next sync must not silently revert them; and a client
    resolving against C must still honour A by default, because a bundle is
    usually withdrawn for a reason (it is broken, or it has a CVE) and
    "my mirror still serves it" is not consent to reinstate that everywhere.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="server extras not installed")
pytest.importorskip("aiosqlite", reason="aiosqlite required")
uvicorn = pytest.importorskip("uvicorn", reason="uvicorn required for a real server")
httpx = pytest.importorskip("httpx")


PKG = "chainpkg"
VER = "1.0.0+cvc.1"
VARIANT = dict(platform="linux", arch="x86_64", build_type="release", link="shared")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Server:
    """A cvcpkg server in its own process, with its own DB and archive dir.

    A separate process per hop is not incidental: cvcpkg.server.db holds the
    engine in a module global, so three servers sharing one interpreter would
    silently share one database and the chain would prove nothing.
    """

    def __init__(self, name: str, root: Path, upstream: str = ""):
        self.name = name
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.upstream = upstream
        self.db_url = f"sqlite+aiosqlite:///{self.root / 'srv.db'}"
        self.admin_token = ""
        self._proc: subprocess.Popen | None = None

    def seed_admin(self) -> str:
        """Create the admin token in a child process, so no engine leaks here."""
        code = (
            "import asyncio,sys\n"
            "from pathlib import Path\n"
            "from cvcpkg.server.db import create_tables, dispose_engine, init_db\n"
            "from cvcpkg.server.db_stores import DbTokenStore\n"
            "from cvcpkg.server.models import TokenRole\n"
            "async def m():\n"
            f"    init_db({self.db_url!r})\n"
            "    await create_tables()\n"
            f"    raw = await DbTokenStore(Path({str(self.root)!r})).create({self.name + '-admin'!r}, TokenRole.admin)\n"
            "    await dispose_engine()\n"
            "    print(raw)\n"
            "asyncio.run(m())\n"
        )
        env = {
            **os.environ,
            "CVCPKG_DATABASE_URL": self.db_url,
            "CVCPKG_SERVER_STATE_DIR": str(self.root),
        }
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120
        )
        if out.returncode != 0:
            raise RuntimeError(f"{self.name}: seeding failed: {out.stderr[-800:]}")
        self.admin_token = out.stdout.strip().splitlines()[-1].strip()
        return self.admin_token

    def start(self) -> None:
        env = {
            **os.environ,
            "CVCPKG_DATABASE_URL": self.db_url,
            "CVCPKG_SERVER_STATE_DIR": str(self.root),
            "CVCPKG_POPULATE_UPSTREAM": self.upstream,
            # Poll hard so the test does not wait minutes for a hop.
            "CVCPKG_POPULATE_INTERVAL": "1",
        }
        env.pop("CVCPKG_MIRROR_MODE", None)
        self._proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "--factory",
                "cvcpkg.server.app:create_app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "error",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            if self._proc.poll() is not None:
                err = (self._proc.stderr.read() or b"").decode()[-1200:]
                raise RuntimeError(f"{self.name} exited: {err}")
            try:
                if httpx.get(f"{self.url}/healthz", timeout=2).status_code == 200:
                    return
            except Exception:
                time.sleep(0.2)
        raise RuntimeError(f"{self.name} did not come up on {self.url}")

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=15)
            except Exception:
                self._proc.kill()

    # ── helpers ────────────────────────────────────────────────
    @property
    def auth(self) -> dict:
        return {"Authorization": f"Bearer {self.admin_token}"}

    def bundles(self) -> list[dict]:
        r = httpx.get(
            f"{self.url}/v1/packages/{PKG}?include_yanked=true", headers=self.auth, timeout=20
        )
        if r.status_code >= 400:
            return []
        return r.json().get("packages", [])

    def one(self) -> dict | None:
        rows = self.bundles()
        return rows[0] if rows else None

    def catalog(self) -> dict:
        r = httpx.get(f"{self.url}/v1/catalog", headers=self.auth, timeout=20)
        r.raise_for_status()
        return r.json()

    def catalog_names(self) -> list[str]:
        return [b["name"] for b in self.catalog().get("bundles", [])]

    def yank(self) -> None:
        httpx.post(
            f"{self.url}/v1/packages/{PKG}/{VER}/yank", headers=self.auth, timeout=20
        ).raise_for_status()

    def unyank(self) -> None:
        httpx.post(
            f"{self.url}/v1/packages/{PKG}/{VER}/unyank", headers=self.auth, timeout=20
        ).raise_for_status()

    def await_(self, predicate, what: str, timeout: float = 60.0):
        """Wait for this server's background populate to make *predicate* true."""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.one()
            try:
                if predicate(last):
                    return last
            except Exception:
                pass
            time.sleep(0.5)
        raise AssertionError(f"{self.name}: timed out waiting for {what}; last row={last}")


def _make_archive(tmp: Path) -> Path:
    src = tmp / "payload"
    src.mkdir(parents=True, exist_ok=True)
    (src / "hello.txt").write_text("chain\n", encoding="utf-8")
    arc = tmp / f"{PKG}-{VER}-linux-x86_64-release-shared.tar.gz"
    with tarfile.open(arc, "w:gz") as tf:
        tf.add(src / "hello.txt", arcname="hello.txt")
    return arc


def _publish(server: Server, archive: Path) -> None:
    params = {"name": PKG, "version": VER, **VARIANT}
    with open(archive, "rb") as fh:
        r = httpx.post(
            f"{server.url}/v1/publish",
            params=params,
            files={"file": (archive.name, fh, "application/gzip")},
            headers=server.auth,
            timeout=60,
        )
    r.raise_for_status()


@pytest.fixture()
def chain(tmp_path, monkeypatch):
    """A -> B -> C, each a real server, each populating from the one above."""
    saved = {
        k: os.environ.get(k)
        for k in (
            "CVCPKG_DATABASE_URL",
            "CVCPKG_POPULATE_UPSTREAM",
            "CVCPKG_POPULATE_SYNC_INTERVAL",
            "CVCPKG_MIRROR_MODE",
        )
    }
    os.environ.pop("CVCPKG_MIRROR_MODE", None)

    a = Server("A", tmp_path / "a")
    a.seed_admin()
    a.start()
    b = Server("B", tmp_path / "b", upstream=a.url)
    b.seed_admin()
    b.start()
    c = Server("C", tmp_path / "c", upstream=b.url)
    c.seed_admin()
    c.start()
    try:
        yield a, b, c
    finally:
        for s in (c, b, a):
            with contextlib.suppress(Exception):
                s.stop()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _yanked(row) -> bool:
    return bool(row and row.get("yanked"))


def _present(row) -> bool:
    return row is not None


class TestAuthorityChain:
    def test_a_yank_at_the_top_reaches_the_bottom(self, chain, tmp_path):
        a, b, c = chain
        _publish(a, _make_archive(tmp_path / "arc"))

        # The bundle has to walk the whole list; C never talks to A.
        b.await_(_present, "B to import from A")
        c.await_(_present, "C to import from B")
        assert c.one()["yanked"] is False

        # Retire it at the origin only.
        a.yank()
        assert a.one()["yanked"] is True

        b.await_(_yanked, "the yank to reach the first mirror")
        c.await_(_yanked, "the yank to traverse the whole chain")

        row = c.one()
        assert row["yanked"] is True
        assert row["upstream_yanked"] is True
        # ...and it is gone from what C actually serves.
        assert PKG not in c.catalog_names()

    def test_a_mirror_may_dissent_and_the_dissent_survives_sync(self, chain, tmp_path):
        a, b, c = chain
        _publish(a, _make_archive(tmp_path / "arc"))
        b.await_(_present, "B to import")
        c.await_(_present, "C to import")

        a.yank()
        b.await_(_yanked, "B to follow the yank")
        c.await_(_yanked, "C to follow the yank")

        # C's operator needs it back -- they know something A does not.
        c.unyank()
        row = c.one()
        assert row["yanked"] is False
        assert row["upstream_yanked"] is True, "the disagreement stays on record"

        # Several sync cycles must pass without reverting them.  This is the
        # regression that matters: with a single yanked flag there is nowhere to
        # record "we chose otherwise", so every cycle silently re-yanked and the
        # bundle flip-flopped forever.
        time.sleep(6)
        row = c.one()
        assert row["yanked"] is False, (
            "reconciliation reverted a deliberate local unyank -- the operator "
            "would be overridden on every sync"
        )
        assert row["upstream_yanked"] is True, "upstream's verdict is still recorded"

    def test_a_middle_hop_cannot_launder_the_origins_yank(self, chain, tmp_path):
        """B dissenting must not clear A's verdict for C.

        Every other dissent test unyanks at C, the leaf -- which has nothing
        downstream, so it cannot demonstrate laundering.  Dissenting in the
        *middle* is the case that matters: B serves the bundle again
        (``yanked`` false) while still disclosing ``upstream_yanked``.  A
        downstream that classifies on ``yanked`` alone reads that as an
        ordinary live bundle, un-yanks, and clears its own record of A's
        ruling -- so a bundle A withdrew for a CVE is served by C with no
        disclosure and nothing for --trust-mirror to opt into.
        """
        a, b, c = chain
        _publish(a, _make_archive(tmp_path / "arc"))
        b.await_(_present, "B to import")
        c.await_(_present, "C to import")

        a.yank()
        b.await_(_yanked, "B to follow the yank")
        c.await_(_yanked, "C to follow the yank")

        # B's operator overrides -- legitimately, and it is recorded on B.
        b.unyank()
        assert b.one()["yanked"] is False
        assert b.one()["upstream_yanked"] is True

        # Let several sync cycles run: C now sees B advertising the bundle as
        # not-yanked, which is exactly the laundering opportunity.
        time.sleep(8)

        row = c.one()
        assert row["upstream_yanked"] is True, (
            "a middle mirror's dissent erased the origin's verdict downstream -- "
            "C has no record that A ever retired this bundle, so --trust-mirror "
            "cannot opt into or out of anything"
        )
        assert row["yanked"] is True, (
            "C un-yanked a bundle the origin withdrew, because one mirror "
            "in between chose to keep serving it"
        )
        assert PKG not in c.catalog_names()

    def test_upstream_wins_by_default_and_trust_mirror_opts_out(self, chain, tmp_path):
        """The client-side half of the same disagreement."""
        from cvcpkg.catalog import catalog_entries

        a, b, c = chain
        _publish(a, _make_archive(tmp_path / "arc"))
        b.await_(_present, "B to import")
        c.await_(_present, "C to import")
        a.yank()
        b.await_(_yanked, "B to follow")
        c.await_(_yanked, "C to follow")
        c.unyank()  # C serves it again, against A's ruling

        cat = c.catalog()
        assert PKG in [x["name"] for x in cat["bundles"]], "C really is serving it"
        assert any(x.get("upstream_yanked") for x in cat["bundles"]), (
            "C must disclose that its upstream retired this bundle, or a client "
            "cannot honour upstream even if it wants to"
        )

        assert [e.name for e in catalog_entries(cat)] == [], (
            "a bundle the origin retired must not be selected just because a "
            "mirror still serves it"
        )
        assert [e.name for e in catalog_entries(cat, trust_mirror=True)] == [PKG]

    def test_an_upstream_unyank_also_flows_down(self, chain, tmp_path):
        """Authority is not a one-way ratchet."""
        a, b, c = chain
        _publish(a, _make_archive(tmp_path / "arc"))
        b.await_(_present, "B to import")
        c.await_(_present, "C to import")

        a.yank()
        b.await_(_yanked, "B to follow the yank")
        c.await_(_yanked, "C to follow the yank")

        a.unyank()
        b.await_(lambda r: not _yanked(r), "B to follow the unyank")
        c.await_(lambda r: not _yanked(r), "C to follow the unyank")

        row = c.one()
        assert row["yanked"] is False
        assert row["upstream_yanked"] is False, (
            "clearing the recorded verdict matters: otherwise a later re-yank "
            "looks like an operator override and is never enforced"
        )
