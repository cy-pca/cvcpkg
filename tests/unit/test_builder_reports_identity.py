"""The builder names itself on complete/fail exactly as it did in its claim.

The server refuses a complete/fail report that names a builder (or claimant)
which no longer holds the job -- a stale report from an attempt that was
paused, resumed and re-dispatched elsewhere must not decide the next
attempt's outcome.  That check only works if the builder sends the identity
it claimed with.

The identity is ``job_identity``, bound once in ``builder_run``'s own scope
right after registration.  It deliberately does not live inside the job
closure that happens to make the claim: when the claim moved into a helper of
its own, a report that spread the claim's *local* body raised NameError at
runtime -- swallowed by the report's ``except`` -- so every job was published
and then left ``running`` until the build timeout reaped it and
cascade-cancelled its dependents.  Hence two layers here:

* structural: ``job_identity`` is bound in builder_run itself, every
  /complete and /fail post anywhere in builder_run spreads it, and the claim
  sends the same identity;
* behavioural: ``builder run`` executes a job against a faked server and the
  JSON it actually posts is checked -- the layer that catches a NameError,
  which the structural one cannot see.
"""

from __future__ import annotations

import ast
import inspect
import io
import json
import signal
import tarfile
import textwrap
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from cvcpkg.cli import _builder
from cvcpkg.cli._builder import builder_run

# ── Structural ───────────────────────────────────────────────────


def _builder_run() -> ast.FunctionDef:
    tree = ast.parse(inspect.getsource(_builder))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "builder_run":
            return node
    raise AssertionError("builder_run() not found in cvcpkg.cli._builder")


def _assigned_value(stmts: list[ast.stmt], name: str) -> ast.expr | None:
    """The value bound to *name* by a statement directly in *stmts*."""
    for stmt in stmts:
        if (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == name
        ):
            return stmt.value
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in stmt.targets
        ):
            return stmt.value
    return None


def _enclosing_functions(root: ast.AST) -> dict[ast.AST, ast.FunctionDef]:
    """Map every node under *root* to the innermost function containing it."""
    owner: dict[ast.AST, ast.FunctionDef] = {}

    def visit(node: ast.AST, fn: ast.FunctionDef | None) -> None:
        for child in ast.iter_child_nodes(node):
            inner = child if isinstance(child, ast.FunctionDef) else fn
            if fn is not None:
                owner[child] = fn
            visit(child, inner)

    visit(root, root if isinstance(root, ast.FunctionDef) else None)
    return owner


def _posts(fn: ast.FunctionDef, verb: str) -> list[ast.Call]:
    """Every client.post(f".../<verb>", ...) call anywhere under *fn*."""
    found = []
    for node in ast.walk(fn):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "post"
            and node.args
            and isinstance(node.args[0], ast.JoinedStr)
        ):
            continue
        literal = [v.value for v in node.args[0].values if isinstance(v, ast.Constant)]
        if "".join(literal).rstrip("/").endswith(f"/{verb}"):
            found.append(node)
    return found


def _json_kw(call: ast.Call) -> ast.expr:
    for kw in call.keywords:
        if kw.arg == "json":
            return kw.value
    raise AssertionError("post() without json=")


def test_job_identity_is_bound_in_builder_run_itself():
    run = _builder_run()
    value = _assigned_value(run.body, "job_identity")
    assert value is not None, (
        "builder_run must bind job_identity in its own scope (not inside a job "
        "closure), so every closure that reports on a job can read it"
    )
    src = ast.unparse(value)
    assert "builder_id" in src and "claimant" in src, src


def test_every_complete_and_fail_report_spreads_job_identity():
    run = _builder_run()
    for verb in ("complete", "fail"):
        posts = _posts(run, verb)
        assert posts, f"no /{verb} post found in builder_run"
        for call in posts:
            body = _json_kw(call)
            assert isinstance(body, ast.Dict), f"{verb} must post a dict literal"
            spread = [v for k, v in zip(body.keys, body.values, strict=True) if k is None]
            assert any(isinstance(v, ast.Name) and v.id == "job_identity" for v in spread), (
                f"the {verb} report does not carry **job_identity: the server cannot "
                "tell a stale report from a previous holder and will let it finish the job"
            )


def test_the_claim_sends_the_same_identity():
    run = _builder_run()
    identity = ast.dump(_assigned_value(run.body, "job_identity"))
    owner = _enclosing_functions(run)
    claims = _posts(run, "claim")
    assert claims, "no /claim post found in builder_run"
    for call in claims:
        sent = _json_kw(call)
        assert isinstance(sent, ast.Name), "the claim must post a named identity dict"
        if sent.id == "job_identity":
            continue
        # A local copy is fine as long as it is built the same way.
        fn = owner[call]
        local = None
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign | ast.AnnAssign):
                local = _assigned_value([node], sent.id) or local
        assert local is not None, f"{sent.id} is not bound in {fn.name}()"
        assert ast.dump(local) == identity, (
            f"the claim's {sent.id} is built differently from job_identity: "
            f"{ast.unparse(local)!r} vs the reports' identity"
        )


# ── Behavioural ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_signal_handlers(monkeypatch):
    """builder_run installs process-wide SIGINT/SIGTERM handlers."""
    monkeypatch.setattr(signal, "signal", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _fast_drain_settle(monkeypatch):
    monkeypatch.setenv("CVCPKG_DRAIN_SETTLE_SECS", "0")


def _recipe_bundle(name: str) -> bytes:
    recipe_yaml = textwrap.dedent(
        f"""\
        recipe:
          name: {name}
          upstream_version: "1.0.0"
          cvc_revision: 1
        source:
          type: none
        """
    ).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="recipe.yaml")
        info.size = len(recipe_yaml)
        tar.addfile(info, io.BytesIO(recipe_yaml))
    return buf.getvalue()


class _Resp:
    def __init__(self, status: int = 200, data=None, content: bytes = b""):
        self.status_code = status
        self._data = {} if data is None else data
        self.content = content
        self.text = json.dumps(self._data)

    def json(self):
        return self._data


def _fake_server(job: dict, posted: dict[str, list[dict]]):
    """An httpx.Client stand-in: hands out *job* once, records posted JSON."""
    handed_out = {"done": False}
    bundle = _recipe_bundle(job["recipe_name"])

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass

        def post(self, url, json=None, **k):
            verb = url.rstrip("/").rsplit("/", 1)[-1]
            posted.setdefault(verb, []).append(json)
            if verb == "register":
                return _Resp(200, {"id": 7})
            if verb == "claim":
                return _Resp(200, {"id": job["id"], "status": "running"})
            return _Resp(200, {})

        def patch(self, url, **k):
            return _Resp(200, {})

        def get(self, url, **k):
            if url.endswith(f"/v1/recipes/{job['recipe_name']}"):
                return _Resp(200, content=bundle)
            if url.endswith(("/next-job", "/next-claimable")):
                if not handed_out["done"]:
                    handed_out["done"] = True
                    return _Resp(200, job)
                return _Resp(204)
            return _Resp(204)

        def delete(self, url, **k):
            return _Resp(200, {})

    return _Client


def _run(monkeypatch, tmp_path, *, build_ok: bool, register: bool):
    job = {"id": 42, "recipe_name": "zlib", "platform": "linux", "arch": "x86_64"}
    posted: dict[str, list[dict]] = {}
    monkeypatch.setattr(httpx, "Client", _fake_server(job, posted))

    def fake_pack(recipe_dir, *, output_dir, **k):
        if not build_ok:
            raise RuntimeError("compiler exploded")
        out = Path(output_dir) / "zlib-1.0.0-linux-x86_64.tar.gz"
        out.write_bytes(b"archive")
        return out, "0" * 64, out.stat().st_size

    monkeypatch.setattr("cvcpkg.builder.pack_recipe", fake_pack)
    monkeypatch.setattr(_builder, "_publish_to_server", lambda **k: None)

    args = [
        "--server",
        "http://test",
        "--token",
        "t",
        "--name",
        "w-identity",
        "--platform",
        "linux",
        "--arch",
        "x86_64",
        "--no-websocket",
        "--no-auto-capabilities",
        "--no-free-disk",
        # A free slot keeps the poll loop from sleeping out its 5 s capacity
        # back-off while the job runs; the fake answers 204 instantly.
        "--max-jobs",
        "2",
        "--exit-when-empty",
        "--max-runtime",
        "60",
        "--work-dir",
        str(tmp_path / "wd"),
        "--recipe-cache-dir",
        str(tmp_path / "rc"),
        "--pidfile",
        str(tmp_path / "b.pid"),
    ]
    if not register:
        args.append("--no-register")
    result = CliRunner().invoke(builder_run, args)
    assert result.exit_code == 0, result.output
    return posted, result.output


@pytest.mark.parametrize(
    ("register", "identity"),
    [(True, {"builder_id": 7}), (False, {"claimant": "w-identity"})],
    ids=["registered", "unregistered"],
)
def test_a_completed_job_reports_the_identity_it_claimed_with(
    monkeypatch, tmp_path, register, identity
):
    posted, output = _run(monkeypatch, tmp_path, build_ok=True, register=register)
    assert posted.get("claim") == [identity], output
    assert posted.get("complete") == [
        {"result_archive_url": "http://test/v1/packages/zlib", **identity}
    ], output
    assert "fail" not in posted, output
    assert "Completed: zlib" in output


@pytest.mark.parametrize(
    ("register", "identity"),
    [(True, {"builder_id": 7}), (False, {"claimant": "w-identity"})],
    ids=["registered", "unregistered"],
)
def test_a_failed_job_reports_the_identity_it_claimed_with(
    monkeypatch, tmp_path, register, identity
):
    posted, output = _run(monkeypatch, tmp_path, build_ok=False, register=register)
    assert posted.get("claim") == [identity], output
    assert "complete" not in posted, output
    [fail] = posted.get("fail") or [None]
    assert fail is not None, output
    assert {k: fail[k] for k in identity} == identity
    assert "compiler exploded" in fail["error_message"]
