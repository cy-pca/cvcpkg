# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Branch coverage for the server-side admin CLI (``cvcpkg.server.cli``).

The ``cvcpkg-server`` command group manages tokens, the audit chain, DB
migrations and archive-storage backends.  These tests drive every subcommand
through ``CliRunner`` with the boundaries mocked:

  * token/bootstrap: the real file-backed ``TokenStore`` on a ``tmp_path`` state
    dir for the YAML path; the async DB path is exercised with ``init_db`` /
    ``create_tables`` / ``DbTokenStore`` patched (no PostgreSQL).
  * audit: the real ``AuditLog`` (append-only YAML) for both the intact chain
    and a hand-corrupted one.
  * migrate: ``_require_alembic`` / ``_alembic_config`` patched so no real
    database is touched, plus the missing-alembic and missing-URL guards.
  * storage: ``run_migration`` / ``diagnose`` / ``heal`` patched to return real
    result dataclasses; ``storage show`` reads the real archive-store default.

``uvicorn`` is an optional server extra (absent on the CI runner), so the
``run`` command's happy path injects a fake ``uvicorn`` module and the
ImportError branch is exercised directly.
"""

from __future__ import annotations

import os
import sys
import types
from unittest import mock

import pytest

# server.cli pulls in the pydantic models + sqlalchemy stores transitively.
pytest.importorskip("pydantic", reason="server extras not installed")
pytest.importorskip("sqlalchemy", reason="server extras not installed")

from click.testing import CliRunner

from cvcpkg.server.audit import AuditLog
from cvcpkg.server.auth import TokenStore
from cvcpkg.server.cli import server_cli
from cvcpkg.server.models import AuditAction, TokenRole


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Keep CVCPKG_* env out of the way and undo direct os.environ writes.

    ``run`` mutates ``os.environ`` in place (not via monkeypatch), so snapshot
    and restore around every test.
    """
    for key in list(os.environ):
        if key.startswith("CVCPKG_"):
            monkeypatch.delenv(key, raising=False)
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


def _run(args):
    return CliRunner().invoke(server_cli, args)


def _sd(tmp_path):
    return ["--state-dir", str(tmp_path)]


# ── DB-branch helpers ───────────────────────────────────────────


def _install_fake_db(monkeypatch, store):
    """Route the CLI's async DB path at an in-memory fake store."""
    monkeypatch.setattr("cvcpkg.server.db.init_db", mock.MagicMock())
    monkeypatch.setattr("cvcpkg.server.db.create_tables", mock.AsyncMock())
    monkeypatch.setattr("cvcpkg.server.db.dispose_engine", mock.AsyncMock())
    monkeypatch.setattr("cvcpkg.server.db_stores.DbTokenStore", lambda *a, **k: store)


def _db_env(monkeypatch):
    monkeypatch.setenv("CVCPKG_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")


# ══ run ══════════════════════════════════════════════════════════


class TestRun:
    def test_mirror_mode_requires_upstream(self, tmp_path):
        res = _run(["run", *_sd(tmp_path), "--mirror-mode"])
        assert res.exit_code != 0
        assert "--mirror-upstream is required" in res.output

    def test_bad_max_upload_bytes_is_a_startup_error(self, tmp_path):
        res = _run(["run", *_sd(tmp_path), "--max-upload-bytes", "not-a-size"])
        assert res.exit_code != 0
        assert "--max-upload-bytes" in res.output

    def test_uvicorn_missing_raises_click_exception(self, tmp_path, monkeypatch):
        # `import uvicorn` fails when the module is mapped to None.
        monkeypatch.setitem(sys.modules, "uvicorn", None)
        res = _run(["run", *_sd(tmp_path)])
        assert res.exit_code != 0
        assert "uvicorn is required" in res.output

    def test_happy_path_launches_uvicorn(self, tmp_path, monkeypatch):
        fake_uvicorn = types.ModuleType("uvicorn")
        fake_uvicorn.run = mock.MagicMock()
        monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
        # Don't install a real SIGTERM handler during the test.
        monkeypatch.setattr("signal.signal", lambda *a, **k: None)

        res = _run(
            [
                "run",
                *_sd(tmp_path),
                "--host",
                "127.0.0.1",
                "--port",
                "9999",
                "--database-url",
                "postgresql+asyncpg://bob:secret@db/cvc",
                "--mirror-mode",
                "--mirror-upstream",
                "https://up.example",
                "--mirror-token",
                "up-token",
                "--mirror-sync-interval",
                "60",
                "--registration-mode",
                "admin-gated",
                "--max-upload-bytes",
                "8GB",
                "--log-json",
            ]
        )
        assert res.exit_code == 0, res.output
        assert fake_uvicorn.run.called
        assert os.environ["CVCPKG_MIRROR_TOKEN"] == "up-token"
        assert os.environ["CVCPKG_MIRROR_SYNC_INTERVAL"] == "60"
        # The DB password is masked in the banner.
        assert "bob:***@db" in res.output
        assert "secret" not in res.output
        assert "MIRROR MODE" in res.output
        assert "registration mode: admin-gated" in res.output
        assert "max upload size: 8 GiB" in res.output
        assert "docs at http://127.0.0.1:9999/docs" in res.output
        # Env is populated for the app factory that uvicorn will import.
        assert os.environ["CVCPKG_MIRROR_UPSTREAM"] == "https://up.example"
        assert os.environ["CVCPKG_REGISTRATION_MODE"] == "admin-gated"
        # log_json => a structured log_config dict is handed to uvicorn.
        assert fake_uvicorn.run.call_args.kwargs["log_config"] is not None

    def test_happy_path_yaml_backend_no_logjson(self, tmp_path, monkeypatch):
        fake_uvicorn = types.ModuleType("uvicorn")
        fake_uvicorn.run = mock.MagicMock()
        monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
        monkeypatch.setattr("signal.signal", lambda *a, **k: None)

        res = _run(["run", *_sd(tmp_path), "--require-auth-reads", "--storage", "file:///srv/x"])
        assert res.exit_code == 0, res.output
        assert "backend: YAML files" in res.output
        assert os.environ["CVCPKG_SERVER_REQUIRE_AUTH_READS"] == "1"
        assert os.environ["CVCPKG_SERVER_STORAGE_URI"] == "file:///srv/x"
        # No --log-json => uvicorn gets log_config=None.
        assert fake_uvicorn.run.call_args.kwargs["log_config"] is None


# ══ bootstrap ════════════════════════════════════════════════════


class TestBootstrap:
    def test_creates_first_admin_token(self, tmp_path):
        res = _run(["bootstrap", *_sd(tmp_path), "--name", "root", "--email", "a@b.c"])
        assert res.exit_code == 0, res.output
        assert "ADMIN TOKEN CREATED" in res.output
        assert "cvctok_" in res.output
        # The token really landed in the store as an admin.
        toks = TokenStore(tmp_path).list_tokens()
        assert [t.name for t in toks] == ["root"]
        assert toks[0].role == TokenRole.admin

    def test_refuses_when_admin_exists(self, tmp_path):
        TokenStore(tmp_path).create("existing-admin", TokenRole.admin)
        res = _run(["bootstrap", *_sd(tmp_path)])
        assert res.exit_code != 0
        assert "admin token already exists" in res.output

    def test_db_backend_creates_admin(self, tmp_path, monkeypatch):
        _db_env(monkeypatch)
        store = mock.MagicMock()
        store.list_tokens = mock.AsyncMock(return_value=[])
        store.create = mock.AsyncMock(return_value="cvctok_DBADMIN")
        _install_fake_db(monkeypatch, store)

        res = _run(["bootstrap", *_sd(tmp_path), "--name", "root"])
        assert res.exit_code == 0, res.output
        assert "cvctok_DBADMIN" in res.output
        store.create.assert_awaited_once()

    def test_db_backend_refuses_when_admin_exists(self, tmp_path, monkeypatch):
        _db_env(monkeypatch)
        existing = TokenStore(tmp_path)
        existing.create("db-admin", TokenRole.admin)
        (admin_rec,) = existing.list_tokens()
        store = mock.MagicMock()
        store.list_tokens = mock.AsyncMock(return_value=[admin_rec])
        store.create = mock.AsyncMock()
        _install_fake_db(monkeypatch, store)

        res = _run(["bootstrap", *_sd(tmp_path)])
        assert res.exit_code != 0
        assert "admin token already exists" in res.output
        store.create.assert_not_awaited()


# ══ token create ═════════════════════════════════════════════════


class TestTokenCreate:
    def test_file_backend(self, tmp_path):
        res = _run(["token", "create", "--name", "ci-bot", "--role", "publisher", *_sd(tmp_path)])
        assert res.exit_code == 0, res.output
        assert "Token created for 'ci-bot'" in res.output
        assert "cvctok_" in res.output
        assert TokenStore(tmp_path).list_tokens()[0].name == "ci-bot"

    def test_file_backend_with_expiry_and_email(self, tmp_path):
        res = _run(
            [
                "token",
                "create",
                "--name",
                "temp",
                "--role",
                "reader",
                "--expires-in-days",
                "7",
                "--email",
                "x@y.z",
                *_sd(tmp_path),
            ]
        )
        assert res.exit_code == 0, res.output
        rec = TokenStore(tmp_path).list_tokens()[0]
        assert rec.role == TokenRole.reader
        assert rec.email == "x@y.z"
        assert rec.expires_at is not None

    def test_db_backend(self, tmp_path, monkeypatch):
        _db_env(monkeypatch)
        store = mock.MagicMock()
        store.create = mock.AsyncMock(return_value="cvctok_DBTOK")
        _install_fake_db(monkeypatch, store)

        res = _run(["token", "create", "--name", "dbbot", "--role", "admin", *_sd(tmp_path)])
        assert res.exit_code == 0, res.output
        assert "cvctok_DBTOK" in res.output
        # role forwarded as a TokenRole to the DB store.
        assert store.create.await_args.kwargs["role"] == TokenRole.admin


# ══ token list ═══════════════════════════════════════════════════


class TestTokenList:
    def test_empty(self, tmp_path):
        res = _run(["token", "list", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "No tokens found." in res.output

    def test_lists_tokens(self, tmp_path):
        store = TokenStore(tmp_path)
        store.create("alpha", TokenRole.admin)
        store.create("beta", TokenRole.reader, expires_in_days=30)
        res = _run(["token", "list", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "alpha" in res.output and "admin" in res.output
        assert "beta" in res.output
        # An unexpiring token prints "never".
        assert "never" in res.output

    def test_db_backend(self, tmp_path, monkeypatch):
        _db_env(monkeypatch)
        src = TokenStore(tmp_path)
        src.create("dbtok", TokenRole.publisher)
        recs = src.list_tokens()
        store = mock.MagicMock()
        store.list_tokens = mock.AsyncMock(return_value=recs)
        _install_fake_db(monkeypatch, store)
        # Use a different state dir so we know the output came from the fake.
        res = _run(["token", "list", "--state-dir", str(tmp_path / "other")])
        assert res.exit_code == 0
        assert "dbtok" in res.output and "publisher" in res.output


# ══ token revoke / set-email / set-description / set-metadata ════


class TestTokenMutations:
    def test_revoke_found(self, tmp_path):
        TokenStore(tmp_path).create("bot", TokenRole.publisher)
        res = _run(["token", "revoke", "--name", "bot", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "revoked." in res.output
        assert TokenStore(tmp_path).list_tokens()[0].revoked is True

    def test_revoke_missing(self, tmp_path):
        res = _run(["token", "revoke", "--name", "ghost", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "not found or already revoked" in res.output

    def test_revoke_db(self, tmp_path, monkeypatch):
        _db_env(monkeypatch)
        store = mock.MagicMock()
        store.revoke = mock.AsyncMock(return_value=True)
        _install_fake_db(monkeypatch, store)
        res = _run(["token", "revoke", "--name", "bot", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "revoked." in res.output

    def test_revoke_db_missing(self, tmp_path, monkeypatch):
        _db_env(monkeypatch)
        store = mock.MagicMock()
        store.revoke = mock.AsyncMock(return_value=False)
        _install_fake_db(monkeypatch, store)
        res = _run(["token", "revoke", "--name", "ghost", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "not found or already revoked" in res.output

    def test_set_email_found(self, tmp_path):
        TokenStore(tmp_path).create("bot", TokenRole.publisher)
        res = _run(["token", "set-email", "--name", "bot", "--email", "n@e.w", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "Email for 'bot' set to 'n@e.w'." in res.output
        assert TokenStore(tmp_path).list_tokens()[0].email == "n@e.w"

    def test_set_email_missing(self, tmp_path):
        res = _run(["token", "set-email", "--name", "ghost", "--email", "x@y.z", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "not found or already revoked" in res.output

    def test_set_email_db(self, tmp_path, monkeypatch):
        _db_env(monkeypatch)
        store = mock.MagicMock()
        store.update_email = mock.AsyncMock(return_value=True)
        _install_fake_db(monkeypatch, store)
        res = _run(["token", "set-email", "--name", "bot", "--email", "a@b.c", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "Email for 'bot' set to 'a@b.c'." in res.output

    def test_set_email_db_missing(self, tmp_path, monkeypatch):
        _db_env(monkeypatch)
        store = mock.MagicMock()
        store.update_email = mock.AsyncMock(return_value=False)
        _install_fake_db(monkeypatch, store)
        res = _run(["token", "set-email", "--name", "ghost", "--email", "a@b.c", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "not found or already revoked" in res.output

    def test_set_description_found(self, tmp_path):
        TokenStore(tmp_path).create("bot", TokenRole.publisher)
        res = _run(
            ["token", "set-description", "--name", "bot", "--description", "ci", *_sd(tmp_path)]
        )
        assert res.exit_code == 0
        assert "Description for 'bot' updated." in res.output

    def test_set_description_missing(self, tmp_path):
        res = _run(
            ["token", "set-description", "--name", "ghost", "--description", "x", *_sd(tmp_path)]
        )
        assert res.exit_code == 0
        assert "not found or already revoked" in res.output

    def test_set_description_db(self, tmp_path, monkeypatch):
        _db_env(monkeypatch)
        store = mock.MagicMock()
        store.update_profile = mock.AsyncMock(return_value=True)
        _install_fake_db(monkeypatch, store)
        res = _run(
            ["token", "set-description", "--name", "bot", "--description", "d", *_sd(tmp_path)]
        )
        assert res.exit_code == 0
        assert "Description for 'bot' updated." in res.output
        assert store.update_profile.await_args.kwargs == {"description": "d"}

    def test_set_description_db_missing(self, tmp_path, monkeypatch):
        _db_env(monkeypatch)
        store = mock.MagicMock()
        store.update_profile = mock.AsyncMock(return_value=False)
        _install_fake_db(monkeypatch, store)
        res = _run(
            ["token", "set-description", "--name", "ghost", "--description", "d", *_sd(tmp_path)]
        )
        assert res.exit_code == 0
        assert "not found or already revoked" in res.output

    def test_set_metadata_found(self, tmp_path):
        TokenStore(tmp_path).create("bot", TokenRole.publisher)
        res = _run(
            ["token", "set-metadata", "--name", "bot", "--metadata", '{"k":1}', *_sd(tmp_path)]
        )
        assert res.exit_code == 0
        assert "Metadata for 'bot' updated." in res.output

    def test_set_metadata_missing(self, tmp_path):
        res = _run(["token", "set-metadata", "--name", "ghost", "--metadata", "x", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "not found or already revoked" in res.output

    def test_set_metadata_db_found(self, tmp_path, monkeypatch):
        _db_env(monkeypatch)
        store = mock.MagicMock()
        store.update_profile = mock.AsyncMock(return_value=True)
        _install_fake_db(monkeypatch, store)
        res = _run(["token", "set-metadata", "--name", "bot", "--metadata", "m", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "Metadata for 'bot' updated." in res.output
        assert store.update_profile.await_args.kwargs == {"metadata": "m"}

    def test_set_metadata_db_missing(self, tmp_path, monkeypatch):
        _db_env(monkeypatch)
        store = mock.MagicMock()
        store.update_profile = mock.AsyncMock(return_value=False)
        _install_fake_db(monkeypatch, store)
        res = _run(["token", "set-metadata", "--name", "ghost", "--metadata", "m", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "not found or already revoked" in res.output


# ══ audit ════════════════════════════════════════════════════════


class TestAudit:
    def test_log_empty(self, tmp_path):
        res = _run(["audit", "log", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "Audit log is empty." in res.output

    def test_log_shows_entries_with_detail(self, tmp_path):
        log = AuditLog(tmp_path)
        log.record(AuditAction.publish, actor="ci", target="zlib", detail="v1.2.3")
        log.record(AuditAction.token_create, actor="admin", target="bot")
        res = _run(["audit", "log", "--limit", "10", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "Showing 2 of 2 entries" in res.output
        assert "publish" in res.output and "actor=ci" in res.output
        assert "v1.2.3" in res.output  # detail line

    def test_verify_ok(self, tmp_path):
        log = AuditLog(tmp_path)
        log.record(AuditAction.publish, actor="ci", target="zlib")
        res = _run(["audit", "verify", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert res.output.startswith("OK:")

    def test_verify_detects_tampering(self, tmp_path):
        import yaml

        log = AuditLog(tmp_path)
        log.record(AuditAction.publish, actor="ci", target="a")
        log.record(AuditAction.publish, actor="ci", target="b")
        # Corrupt the chain: rewrite the second entry's prev hash on disk.
        path = tmp_path / "audit.yaml"
        data = yaml.safe_load(path.read_text())
        data[1]["prev_sha256"] = "0" * 64
        path.write_text(yaml.safe_dump(data))

        res = _run(["audit", "verify", *_sd(tmp_path)])
        assert res.exit_code == 1
        assert "FAILED:" in res.output


# ══ migrate ══════════════════════════════════════════════════════


class TestMigrate:
    def _patch_alembic(self, monkeypatch):
        command = mock.MagicMock()
        monkeypatch.setattr("cvcpkg.server.cli._require_alembic", lambda: command)
        monkeypatch.setattr("cvcpkg.server.cli._alembic_config", lambda: "CFG")
        return command

    def test_upgrade(self, tmp_path, monkeypatch):
        command = self._patch_alembic(monkeypatch)
        monkeypatch.setenv("CVCPKG_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        res = _run(["migrate", "upgrade"])
        assert res.exit_code == 0, res.output
        assert "Upgraded to head." in res.output
        command.upgrade.assert_called_once_with("CFG", "head")

    def test_upgrade_named_revision(self, tmp_path, monkeypatch):
        command = self._patch_alembic(monkeypatch)
        monkeypatch.setenv("CVCPKG_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        res = _run(["migrate", "upgrade", "abc123"])
        assert res.exit_code == 0
        command.upgrade.assert_called_once_with("CFG", "abc123")

    def test_downgrade(self, monkeypatch):
        command = self._patch_alembic(monkeypatch)
        monkeypatch.setenv("CVCPKG_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        res = _run(["migrate", "downgrade", "base"])
        assert res.exit_code == 0
        assert "Downgraded to base." in res.output
        command.downgrade.assert_called_once_with("CFG", "base")

    def test_stamp(self, monkeypatch):
        command = self._patch_alembic(monkeypatch)
        monkeypatch.setenv("CVCPKG_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        res = _run(["migrate", "stamp", "head"])
        assert res.exit_code == 0
        assert "Stamped at head." in res.output
        command.stamp.assert_called_once_with("CFG", "head")

    def test_current(self, monkeypatch):
        command = self._patch_alembic(monkeypatch)
        monkeypatch.setenv("CVCPKG_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        res = _run(["migrate", "current"])
        assert res.exit_code == 0
        command.current.assert_called_once()

    def test_history(self, monkeypatch):
        command = self._patch_alembic(monkeypatch)
        monkeypatch.setenv("CVCPKG_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        res = _run(["migrate", "history"])
        assert res.exit_code == 0
        command.history.assert_called_once()

    def test_missing_alembic(self, monkeypatch):
        # `from alembic import command` fails when alembic maps to None.
        monkeypatch.setitem(sys.modules, "alembic", None)
        monkeypatch.setenv("CVCPKG_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        res = _run(["migrate", "upgrade"])
        assert res.exit_code != 0
        assert "alembic is required" in res.output

    def test_missing_database_url(self, monkeypatch):
        # alembic present (patched), but no URL configured.
        monkeypatch.setattr("cvcpkg.server.cli._require_alembic", lambda: mock.MagicMock())
        monkeypatch.delenv("CVCPKG_DATABASE_URL", raising=False)
        res = _run(["migrate", "upgrade"])
        assert res.exit_code != 0
        assert "CVCPKG_DATABASE_URL must be set" in res.output

    def test_require_alembic_returns_command_module(self):
        pytest.importorskip("alembic")
        from cvcpkg.server.cli import _require_alembic

        command = _require_alembic()
        # The real alembic.command module exposes the migration verbs.
        assert hasattr(command, "upgrade")
        assert hasattr(command, "downgrade")


class TestAlembicConfig:
    def test_normal_config_from_repo_ini(self):
        pytest.importorskip("alembic")
        from cvcpkg.server.cli import _alembic_config

        cfg = _alembic_config()
        # A Config object with a script_location resolved somewhere.
        assert cfg is not None

    def test_frozen_meipass_branch(self, tmp_path, monkeypatch):
        pytest.importorskip("alembic")
        from cvcpkg.server.cli import _alembic_config

        (tmp_path / "alembic.ini").write_text("[alembic]\nscript_location = ignored\n")
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        cfg = _alembic_config()
        assert cfg.get_main_option("script_location").endswith("migrations")


# ══ storage ══════════════════════════════════════════════════════


class TestStorageShow:
    def test_show_default_uri(self, tmp_path):
        res = _run(["storage", "show", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "file://" in res.output


class TestStorageMigrate:
    def test_success(self, tmp_path, monkeypatch):
        from cvcpkg.server.storage_migration import MigrationResult

        result = MigrationResult(
            total=3, migrated=2, skipped=1, failures=[], dest_uri="s3://bucket/prefix"
        )
        monkeypatch.setattr("cvcpkg.server.storage_migration.run_migration", lambda *a, **k: result)
        res = _run(["storage", "migrate", "--to", "s3://bucket/prefix", *_sd(tmp_path)])
        assert res.exit_code == 0, res.output
        assert "total=3 migrated=2 skipped=1 failed=0" in res.output
        assert "active storage backend is now: s3://bucket/prefix" in res.output

    def test_partial_failure_exits_nonzero(self, tmp_path, monkeypatch):
        from cvcpkg.server.storage_migration import MigrationResult

        result = MigrationResult(
            total=2, migrated=1, skipped=0, failures=[("bad.tgz", "checksum")], dest_uri="s3://b"
        )
        monkeypatch.setattr("cvcpkg.server.storage_migration.run_migration", lambda *a, **k: result)
        res = _run(["storage", "migrate", "--to", "s3://b", *_sd(tmp_path)])
        assert res.exit_code == 1
        assert "FAIL bad.tgz: checksum" in res.output
        assert "migration incomplete" in res.output

    def test_migration_error_becomes_click_exception(self, tmp_path, monkeypatch):
        from cvcpkg.server.storage_migration import MigrationError

        def _boom(*a, **k):
            raise MigrationError("same backend")

        monkeypatch.setattr("cvcpkg.server.storage_migration.run_migration", _boom)
        res = _run(["storage", "migrate", "--to", "file:///x", *_sd(tmp_path)])
        assert res.exit_code != 0
        assert "same backend" in res.output


class TestStorageDoctor:
    def _report(self, **kw):
        from cvcpkg.server.storage_doctor import DoctorReport

        base = {"active_uri": "file:///srv", "total": 2}
        base.update(kw)
        return DoctorReport(**base)

    def test_healthy(self, tmp_path, monkeypatch):
        monkeypatch.setattr("cvcpkg.server.storage_doctor.diagnose", lambda *a, **k: self._report())
        res = _run(["storage", "doctor", *_sd(tmp_path)])
        assert res.exit_code == 0
        assert "OK — every catalog archive is present and intact." in res.output

    def test_unhealthy_without_heal_exits_nonzero(self, tmp_path, monkeypatch):
        from cvcpkg.server.storage_doctor import MISSING, Finding

        report = self._report(findings=[Finding("a.tgz", MISSING, "absent")])
        monkeypatch.setattr("cvcpkg.server.storage_doctor.diagnose", lambda *a, **k: report)
        res = _run(["storage", "doctor", *_sd(tmp_path)])
        assert res.exit_code == 1
        assert "MISSING" in res.output
        assert "a.tgz" in res.output

    def test_orphans_and_incomplete_migration_are_reported(self, tmp_path, monkeypatch):
        report = self._report(
            orphans=["stray.tgz"],
            journal_incomplete=True,
            journal_dest="s3://dest",
            journal_pending=["p1.tgz", "p2.tgz"],
            deep=True,
        )
        monkeypatch.setattr("cvcpkg.server.storage_doctor.diagnose", lambda *a, **k: report)
        res = _run(["storage", "doctor", "--deep", "--orphans", *_sd(tmp_path)])
        # journal_incomplete makes the report unhealthy -> non-zero without --heal.
        assert res.exit_code == 1
        assert "deep (sha256)" in res.output
        assert "ORPHAN        stray.tgz" in res.output
        assert "INCOMPLETE-MIGRATION -> s3://dest" in res.output
        assert "2 archive(s) not verified" in res.output

    def test_heal_success(self, tmp_path, monkeypatch):
        from cvcpkg.server.storage_doctor import MISSING, Finding, HealResult

        report = self._report(findings=[Finding("a.tgz", MISSING, "absent")])
        heal = HealResult(healed=["a.tgz"], unhealable=[], resumed_migration=None)
        monkeypatch.setattr("cvcpkg.server.storage_doctor.diagnose", lambda *a, **k: report)
        monkeypatch.setattr("cvcpkg.server.storage_doctor.heal", lambda *a, **k: heal)
        res = _run(["storage", "doctor", "--heal", "--source", "file:///backup", *_sd(tmp_path)])
        assert res.exit_code == 0, res.output
        assert "healed=1 unhealable=0" in res.output
        assert "heal complete" in res.output

    def test_heal_with_unhealable_exits_nonzero(self, tmp_path, monkeypatch):
        from cvcpkg.server.storage_doctor import MISSING, Finding, HealResult

        report = self._report(findings=[Finding("a.tgz", MISSING, "absent")])
        heal = HealResult(healed=[], unhealable=[("a.tgz", "no source")], resumed_migration=None)
        monkeypatch.setattr("cvcpkg.server.storage_doctor.diagnose", lambda *a, **k: report)
        monkeypatch.setattr("cvcpkg.server.storage_doctor.heal", lambda *a, **k: heal)
        res = _run(["storage", "doctor", "--heal", *_sd(tmp_path)])
        assert res.exit_code == 1
        assert "UNHEALABLE a.tgz: no source" in res.output

    def test_heal_resumes_migration(self, tmp_path, monkeypatch):
        from cvcpkg.server.storage_doctor import MISSING, Finding, HealResult
        from cvcpkg.server.storage_migration import MigrationResult

        report = self._report(findings=[Finding("a.tgz", MISSING, "absent")])
        resumed = MigrationResult(total=5, migrated=4, skipped=1, failures=[])
        heal = HealResult(healed=["a.tgz"], unhealable=[], resumed_migration=resumed)
        monkeypatch.setattr("cvcpkg.server.storage_doctor.diagnose", lambda *a, **k: report)
        monkeypatch.setattr("cvcpkg.server.storage_doctor.heal", lambda *a, **k: heal)
        res = _run(["storage", "doctor", "--heal", *_sd(tmp_path)])
        assert res.exit_code == 0, res.output
        assert "resumed migration: migrated=4 skipped=1 failed=0" in res.output
