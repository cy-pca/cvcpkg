"""The builder names itself on complete/fail exactly as it did in its claim.

The server refuses a complete/fail report that names a builder (or claimant)
which no longer holds the job -- a stale report from an attempt that was
paused, resumed and re-dispatched elsewhere must not decide the next
attempt's outcome.  That check only works if the builder sends the identity
it claimed with, so pin it: the claim body and the identity spread into both
reports are the same object.
"""

from __future__ import annotations

import ast
import inspect

from cvcpkg.cli import _builder


def _execute_job() -> ast.FunctionDef:
    tree = ast.parse(inspect.getsource(_builder))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_execute_job":
            return node
    raise AssertionError("_execute_job() not found in cvcpkg.cli._builder")


def _posts(fn: ast.FunctionDef) -> dict[str, ast.Call]:
    """Map 'claim'/'complete'/'fail' to the client.post(...) call for it."""
    found: dict[str, ast.Call] = {}
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
        tail = "".join(literal).rstrip("/")
        for verb in ("claim", "complete", "fail"):
            if tail.endswith(f"/{verb}"):
                found[verb] = node
    return found


def _json_kw(call: ast.Call) -> ast.expr:
    for kw in call.keywords:
        if kw.arg == "json":
            return kw.value
    raise AssertionError("post() without json=")


def test_reports_carry_the_claim_identity():
    posts = _posts(_execute_job())
    assert set(posts) == {"claim", "complete", "fail"}, posts.keys()

    claim_json = _json_kw(posts["claim"])
    assert isinstance(claim_json, ast.Name), "the claim must post a named identity dict"
    identity = claim_json.id

    for verb in ("complete", "fail"):
        body = _json_kw(posts[verb])
        assert isinstance(body, ast.Dict), f"{verb} must post a dict literal"
        spread = [v for k, v in zip(body.keys, body.values) if k is None]
        assert any(isinstance(v, ast.Name) and v.id == identity for v in spread), (
            f"the {verb} report does not carry **{identity}: the server cannot tell a "
            "stale report from a previous holder and will let it finish the job"
        )
