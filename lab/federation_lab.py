#!/usr/bin/env python3
"""Federation laboratory: prove cross-server dependency resolution end-to-end
against REAL cvcpkg-server processes.

Topology (three independent servers, each with its own org namespace):

    edge-a  publishes  app            -> dep cvc://edge-b/shell/iqi-core   (private)
    edge-b  publishes  shell/iqi-core -> dep cvc://edge-c/pub/base         (public org)
    edge-c  publishes  pub/base       (leaf)

The resolver runs against edge-a with a registries config mapping the logical
hosts edge-b/edge-c to the real URLs + tokens.  We assert:
  * the closure resolves across all three servers, deepest-first;
  * each node is fetched from the right registry with that registry's token;
  * the PRIVATE edge-b package is invisible without edge-b's token;
  * an un-allowlisted host is refused (FederationError).

Run:  PYTHONPATH=src python3 lab/federation_lab.py
Exit 0 = all assertions passed.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO_SRC = str(Path(__file__).resolve().parents[1] / "src")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Server:
    def __init__(self, name: str, tmp: Path):
        self.name = name
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.state_dir = tmp / name
        self.state_dir.mkdir(parents=True)
        self.db = f"sqlite+aiosqlite:///{self.state_dir / 'srv.db'}"
        self.proc: subprocess.Popen | None = None
        self.admin = ""

    def _env(self) -> dict:
        env = dict(os.environ)
        env["PYTHONPATH"] = REPO_SRC + os.pathsep + env.get("PYTHONPATH", "")
        env["CVCPKG_DATABASE_URL"] = self.db
        env["CVCPKG_SERVER_STATE_DIR"] = str(self.state_dir)
        env.pop("CVCPKG_POPULATE_UPSTREAM", None)  # plain servers for this lab
        return env

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", "from cvcpkg.server.cli import server_cli; server_cli()", *args],
            env=self._env(), capture_output=True, text=True,
        )

    def bootstrap(self) -> None:
        r = self._cli("bootstrap", "--state-dir", str(self.state_dir))
        m = re.search(r"Token:\s*(\S+)", r.stdout) or re.search(r"(cvcp[_a-zA-Z0-9]{16,})", r.stdout)
        if not m:
            raise RuntimeError(f"[{self.name}] bootstrap gave no token:\n{r.stdout}\n{r.stderr}")
        self.admin = m.group(1)

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-c", "from cvcpkg.server.cli import server_cli; server_cli()",
             "run", "--host", "127.0.0.1", "--port", str(self.port)],
            env=self._env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            with contextlib.suppress(Exception):
                if httpx.get(f"{self.url}/healthz", timeout=1).status_code == 200:
                    return
            time.sleep(0.2)
        raise RuntimeError(f"[{self.name}] did not become healthy on {self.url}")

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            with contextlib.suppress(Exception):
                self.proc.wait(timeout=10)

    def h(self) -> dict:
        return {"Authorization": f"Bearer {self.admin}"}

    def create_org(self, slug: str, private: bool) -> None:
        r = httpx.post(f"{self.url}/v1/orgs", headers=self.h(),
                       json={"slug": slug, "display_name": slug, "is_private": private}, timeout=10)
        r.raise_for_status()

    def publish(self, name: str, org: str, deps: list[dict]) -> None:
        r = httpx.post(
            f"{self.url}/v1/publish", headers=self.h(),
            params={"name": name, "version": "1.0.0", "platform": "linux",
                    "arch": "x86_64", "org": org, "required_deps": json.dumps(deps)},
            files={"file": (f"{name}.tar.zst", b"lab-artifact", "application/octet-stream")},
            timeout=15,
        )
        r.raise_for_status()


def _fail(msg: str):
    print(f"  \033[31mFAIL\033[0m {msg}")
    raise SystemExit(1)


def _ok(msg: str):
    print(f"  \033[32mok\033[0m   {msg}")


def main() -> int:
    sys.path.insert(0, REPO_SRC)
    from cvcpkg.federation import FederationError, resolve_federated

    tmp = Path(tempfile.mkdtemp(prefix="cvc-fed-lab-"))
    servers: list[Server] = []
    try:
        a, b, c = (Server(n, tmp) for n in ("edge-a", "edge-b", "edge-c"))
        servers = [a, b, c]
        for s in servers:
            s.bootstrap()
            s.start()
        print(f"servers up: edge-a={a.url} edge-b={b.url} edge-c={c.url}")

        # Seed leaf -> middle (private) -> root.
        c.create_org("pub", private=False)
        c.publish("base", org="pub", deps=[])
        b.create_org("shell", private=True)
        b.publish("iqi-core", org="shell",
                  deps=[{"name": "base", "org": "pub", "server": "edge-c"}])
        a.publish("app", org="", deps=[{"name": "iqi-core", "org": "shell", "server": "edge-b"}])
        _ok("seeded edge-a/app -> edge-b/shell/iqi-core -> edge-c/pub/base")

        # registries.yaml maps logical hosts -> real url + that server's token.
        cfg = tmp / "cfg"
        cfg.mkdir()
        (cfg / "registries.yaml").write_text(
            "registries:\n"
            f"  edge-b:\n    url: {b.url}\n    token: {b.admin}\n"
            f"  edge-c:\n    url: {c.url}\n    token: {c.admin}\n"
        )
        os.environ.pop("CVCPKG_REGISTRIES", None)
        os.environ.pop("CVCPKG_REGISTRIES_FILE", None)

        # 1) Full cross-domain resolution.
        order = resolve_federated("app", local_url=a.url, local_token=a.admin, config_dir=cfg)
        names = [n.ref.name for n in order]
        if names != ["base", "iqi-core", "app"]:
            _fail(f"closure order {names} != ['base','iqi-core','app']")
        _ok(f"closure resolved deepest-first across 3 servers: {names}")
        by = {n.ref.name: n.base_url for n in order}
        if not (by["base"] == c.url and by["iqi-core"] == b.url and by["app"] == a.url):
            _fail(f"nodes resolved on wrong registries: {by}")
        _ok("each node fetched from the correct registry")
        if not all(n.bundles for n in order):
            _fail("a node resolved with no bundles (private access failed?)")
        _ok("edge-b PRIVATE package resolved WITH edge-b's token")

        # 2) Private package invisible without the right token.
        (cfg / "registries.yaml").write_text(
            "registries:\n"
            f"  edge-b:\n    url: {b.url}\n    token: BOGUS\n"
            f"  edge-c:\n    url: {c.url}\n    token: {c.admin}\n"
        )
        order2 = resolve_federated("app", local_url=a.url, local_token=a.admin, config_dir=cfg)
        iqi = next(n for n in order2 if n.ref.name == "iqi-core")
        if iqi.bundles:
            _fail("PRIVATE edge-b package was visible with a bogus token!")
        _ok("edge-b PRIVATE package INVISIBLE without a valid edge-b token")

        # 3) Un-allowlisted host is refused.
        (cfg / "registries.yaml").write_text(
            f"registries:\n  edge-c:\n    url: {c.url}\n    token: {c.admin}\n"
        )
        try:
            resolve_federated("app", local_url=a.url, local_token=a.admin, config_dir=cfg)
            _fail("un-allowlisted edge-b host was NOT refused")
        except FederationError:
            _ok("un-allowlisted host refused (FederationError)")

        print("\n\033[32mFEDERATION LAB: ALL ASSERTIONS PASSED\033[0m")
        return 0
    finally:
        for s in servers:
            s.stop()
        with contextlib.suppress(Exception):
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
