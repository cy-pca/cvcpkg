"""The builder's bearer token must never be rebound by anything else.

``builder_run`` takes the bearer ``token`` as a parameter, and the nested
``_execute_job`` closes over it to authenticate publishes.  A slot-reservation
id was once assigned to that same name in ``builder_run``'s own scope, which
silently replaced the credential with an int: every publish after the first
dispatched job sent ``Authorization: Bearer 1`` and the server answered
401 "invalid or expired token".

Nothing else failed, because claim / log-stream / complete use a ``headers``
dict built once at startup -- only the publish call reads ``token`` directly.
So a builder looked healthy while being unable to publish anything at all.
"""

from __future__ import annotations

import ast
import inspect

from cvcpkg.cli import _builder


def _func(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found in cvcpkg.cli._builder")


def _innermost_scope(tree: ast.AST, lineno: int) -> ast.FunctionDef | None:
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                if best is None or node.lineno > best.lineno:
                    best = node
    return best


def _tree() -> ast.AST:
    return ast.parse(inspect.getsource(_builder))


def test_builder_run_takes_token_and_never_rebinds_it():
    """The credential must survive the whole run loop."""
    tree = _tree()
    run = _func(tree, "builder_run")

    params = [a.arg for a in run.args.args] + [a.arg for a in run.args.kwonlyargs]
    assert "token" in params, "builder_run no longer takes a bearer token parameter"

    offenders = []
    for node in ast.walk(run):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.For, ast.AsyncFor)):
            targets = [node.target]
        for t in targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name) and n.id == "token":
                    # Only an assignment landing in builder_run's OWN scope can
                    # clobber the credential; one inside a nested def makes a
                    # local there and is harmless.
                    if _innermost_scope(tree, n.lineno) is run:
                        offenders.append(n.lineno)

    assert not offenders, (
        "builder_run rebinds its bearer `token` at line(s) "
        f"{offenders}. Every nested closure -- notably _execute_job's publish "
        "-- reads that name, so the credential becomes whatever was assigned "
        "(a slot id sent as 'Bearer 1'). Use a distinct name such as slot_id."
    )


def test_execute_job_reads_the_credential_it_does_not_own():
    """_execute_job must close over the bearer token, never assign it.

    If it ever assigns `token` locally the closure breaks differently, so pin
    the shape that makes the invariant above meaningful.
    """
    tree = _tree()
    ex = _func(tree, "_execute_job")

    stores = [
        n.lineno
        for n in ast.walk(ex)
        if isinstance(n, ast.Name) and n.id == "token" and isinstance(n.ctx, ast.Store)
    ]
    assert not stores, f"_execute_job assigns `token` at {stores}; it must close over it"

    loads = [
        n
        for n in ast.walk(ex)
        if isinstance(n, ast.Name) and n.id == "token" and isinstance(n.ctx, ast.Load)
    ]
    assert loads, "_execute_job no longer reads `token` — did the publish call stop authenticating?"


def test_slot_helpers_do_not_use_the_name_token():
    """Keep the slot vocabulary distinct from the credential vocabulary."""
    tree = _tree()
    for name in ("_claim_slot", "_release_slot", "_run_job_guarded"):
        fn = _func(tree, name)
        params = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
        assert "token" not in params, (
            f"{name}() names a slot id `token`; that vocabulary collision is what "
            "led to the bearer credential being overwritten"
        )
