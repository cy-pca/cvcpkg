"""The CLI org-slug validation must not require the server extras.

``cvcpkg publish --org`` crashed on base installs with
``ModuleNotFoundError: pydantic``: the click callback lazily imported
``cvcpkg.server.models`` (pydantic at module level, a server-extra dep)
just to reach the pure-regex ``validate_org_slug``. The function now lives
in the dependency-free ``cvcpkg.orgs``; these tests pin that property.
"""

from __future__ import annotations

import subprocess
import sys

from cvcpkg.orgs import validate_org_slug


def test_validate_org_slug_semantics():
    assert validate_org_slug("cvc") is None
    assert validate_org_slug("my-team-42") is None
    assert validate_org_slug("") is not None
    assert validate_org_slug("-leading") is not None
    assert validate_org_slug("trailing-") is not None
    assert validate_org_slug("double--hyphen") is not None
    assert validate_org_slug("x" * 40) is not None
    assert validate_org_slug("UpperCase") is not None


def test_org_validation_importable_with_pydantic_blocked():
    """Simulate a base install: block pydantic, exercise the CLI callback path."""
    code = (
        "import sys\n"
        "sys.modules['pydantic'] = None  # imports of pydantic now fail\n"
        "from cvcpkg.orgs import validate_org_slug\n"
        "assert validate_org_slug('cvc') is None\n"
        "assert validate_org_slug('bad--slug') is not None\n"
        "import cvcpkg.cli._helpers as h\n"
        "import click\n"
        "ctx = click.Context(click.Command('publish'))\n"
        "param = click.Option(['--org'])\n"
        "assert h._validate_org_slug(ctx, param, 'cvc') == 'cvc'\n"
        "try:\n"
        "    h._validate_org_slug(ctx, param, 'bad--slug')\n"
        "except click.BadParameter:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('bad slug not rejected')\n"
        "print('OK')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    assert "OK" in out.stdout


def test_server_models_reexport_backcompat():
    """Server-side callers keep importing from cvcpkg.server.models."""
    import pytest

    pytest.importorskip("pydantic")
    from cvcpkg.server.models import validate_org_slug as reexported

    assert reexported is validate_org_slug
