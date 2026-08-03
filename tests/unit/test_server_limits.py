"""The upload cap is a first-class setting: size grammar, default, wiring.

The cap used to be a bare ``int(os.environ[...])`` read with a 1 GiB default
(documented as 512 MiB — the docs had drifted), which rejected any human size
and was too small for the bundles we actually publish (nvidia-cudnn unpacks to
~2 GiB).  It is now ``cvcpkg server run --max-upload-bytes`` /
``CVCPKG_MAX_UPLOAD_BYTES``, default 4 GiB, parsed by server.limits.
"""

from __future__ import annotations

import pytest

from cvcpkg.server.limits import (
    DEFAULT_MAX_UPLOAD_BYTES,
    format_size,
    parse_size,
)

GIB = 1024**3


class TestDefault:
    def test_default_is_four_gib(self):
        assert DEFAULT_MAX_UPLOAD_BYTES == 4 * GIB

    def test_app_uses_the_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("CVCPKG_MAX_UPLOAD_BYTES", raising=False)
        import importlib

        import cvcpkg.server.app as app_mod

        importlib.reload(app_mod)
        assert app_mod.MAX_UPLOAD_BYTES == 4 * GIB

    def test_app_honours_the_env_override(self, monkeypatch):
        monkeypatch.setenv("CVCPKG_MAX_UPLOAD_BYTES", "8GB")
        import importlib

        import cvcpkg.server.app as app_mod

        importlib.reload(app_mod)
        assert app_mod.MAX_UPLOAD_BYTES == 8 * GIB


class TestParseSize:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("4294967296", 4 * GIB),
            ("4GB", 4 * GIB),
            ("4G", 4 * GIB),
            ("4GiB", 4 * GIB),
            ("4 gb", 4 * GIB),
            ("512MB", 512 * 1024 * 1024),
            ("1.5GB", int(1.5 * GIB)),
            ("2TB", 2 * 1024**4),
            ("100", 100),
        ],
    )
    def test_grammar(self, text, expected):
        assert parse_size(text) == expected

    def test_units_are_binary(self):
        # "GB" means 1024**3 here, not 10**9: the cap is compared against real
        # on-disk sizes, so a 4GB limit that meant 3.7 GiB would be a bug.
        assert parse_size("1GB") == 1024**3

    @pytest.mark.parametrize("bad", ["", "bogus", "4 gigabytes", "-1", "GB"])
    def test_bad_values_fall_back_to_default(self, bad):
        # A typo in a deployment env var must not stop the server booting.
        assert parse_size(bad, default=7) == 7

    @pytest.mark.parametrize("bad", ["", "bogus", "GB"])
    def test_bad_values_raise_without_a_default(self, bad):
        # The CLI passes default=None so an operator sees the typo at startup.
        with pytest.raises(ValueError):
            parse_size(bad)


class TestFormatSize:
    @pytest.mark.parametrize(
        "n,expected",
        [(4 * GIB, "4 GiB"), (512 * 1024 * 1024, "512 MiB"), (900, "900 B")],
    )
    def test_render(self, n, expected):
        assert format_size(n) == expected


class TestCliWiring:
    def test_run_validates_and_exports_the_setting(self, monkeypatch, tmp_path):
        """--max-upload-bytes is parsed, exported, and never imports the app.

        Importing cvcpkg.server.app to parse the value would freeze its
        module-level MAX_UPLOAD_BYTES before the env var is set — uvicorn is
        handed 'cvcpkg.server.app:create_app' as a string and reuses whatever
        is already in sys.modules.
        """
        import click
        from click.testing import CliRunner

        from cvcpkg.server.cli import server_cli

        monkeypatch.delenv("CVCPKG_MAX_UPLOAD_BYTES", raising=False)
        captured: dict[str, str] = {}

        def _fake_run(*a, **kw):
            captured.update(os_environ_snapshot())
            raise click.exceptions.Exit(0)

        def os_environ_snapshot():
            import os

            return dict(os.environ)

        import uvicorn

        monkeypatch.setattr(uvicorn, "run", _fake_run)
        CliRunner().invoke(
            server_cli,
            ["run", "--state-dir", str(tmp_path), "--max-upload-bytes", "8GB"],
        )
        assert captured.get("CVCPKG_MAX_UPLOAD_BYTES") == str(8 * GIB)

    def test_run_rejects_a_bad_size(self, monkeypatch, tmp_path):
        from click.testing import CliRunner

        from cvcpkg.server.cli import server_cli

        monkeypatch.delenv("CVCPKG_MAX_UPLOAD_BYTES", raising=False)
        result = CliRunner().invoke(
            server_cli,
            ["run", "--state-dir", str(tmp_path), "--max-upload-bytes", "banana"],
        )
        assert result.exit_code != 0
        assert "max-upload-bytes" in result.output
