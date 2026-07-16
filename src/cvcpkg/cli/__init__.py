"""cvcpkg command-line interface (click-based).

This package defines the entire CLI surface for cvcpkg, the component
package manager for libcvc-deps.  Commands are organized into submodules
for maintainability:

- ``_helpers``   — shared option decorators and utility functions
- ``_install``   — install, list, info, validate, verify, lock, sync
- ``_catalog``   — catalog, gc, clean, download
- ``_publish``   — publish, add, remove
- ``_build``     — world, build, pack, build-all, pack-all, recipes, rev-bump
- ``_signing``   — key management, sign, verify-sig
- ``_cache``     — cache subcommand group
- ``_doctor``    — environment diagnostics (``cvcpkg doctor``)
- ``_init``      — recipe scaffolding (``cvcpkg init``)
- ``_server``    — token, user, registration, server, org management
- ``_builder``   — builder agent commands
- ``_builds``    — build job commands and wait helpers
- ``_recipe``    — recipe distribution commands (push/pull/publish)
- ``_webhook``   — webhook commands
"""

from __future__ import annotations

import click

from cvcpkg import __version__
from cvcpkg.errors import CvcpkgError


def _restore_default_sigpipe() -> None:
    """Terminate quietly when our stdout pipe is closed early.

    Python installs SIG_IGN for SIGPIPE, which turns a closed downstream pipe
    (``cvcpkg recipes | head``, ``… | grep -q``) into a BrokenPipeError and a
    traceback on stdout flush.  Restore the default so cvcpkg behaves like a
    normal Unix filter.  POSIX + main thread only; a no-op on Windows.
    """
    import signal

    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError, OSError):  # no SIGPIPE / not main thread
        pass


# ── Root group ──────────────────────────────────────────────────


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="cvcpkg")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Component package manager for libcvc-deps prebuilt dependency bundles.

    cvcpkg resolves, downloads, and installs prebuilt component bundles
    from the libcvc-deps catalog, or builds them from source recipes.

    \b
    Quick start (downstream consumer):
      cvcpkg install --from cvc-requirements.yaml
      cmake -B build -DCMAKE_PREFIX_PATH=./deps

    \b
    Quick start (libcvc-deps maintainer):
      cvcpkg build-all --prefix ./prefix --recipes-dir recipes
      cvcpkg validate
    """
    _restore_default_sigpipe()
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ── Import submodules to register commands on the cli group ─────
# Order does not matter — each submodule decorates functions with
# @cli.command() or creates subgroups.

from cvcpkg.cli import (  # noqa: E402, F401
    _build,
    _builder,
    _builds,
    _cache,
    _catalog,
    _cpkg,
    _doctor,
    _init,
    _install,
    _publish,
    _recipe,
    _search,
    _server,
    _signing,
    _telemetry,
    _webhook,
)
from cvcpkg.cli._build import (  # noqa: E402, F401
    _auto_platform,
    _resolve_recipe_dir,
    _try_pull_server_recipes,
)
from cvcpkg.cli._builds import (  # noqa: E402, F401
    _wait_for_dags,
    _wait_for_jobs,
)
from cvcpkg.cli._catalog import (  # noqa: E402, F401
    _fetch_mirror_urls,
)

# ── Backward-compatible re-exports ──────────────────────────────
# Tests and external code may import helpers directly from cvcpkg.cli.
from cvcpkg.cli._helpers import (  # noqa: E402, F401
    _resolve_recipes_dirs,
    _validate_org_slug,
)
from cvcpkg.cli._publish import (  # noqa: E402, F401
    _extract_manifest,
    _publish_chunked,
    _publish_simple,
    _publish_to_backend,
    _publish_to_server,
    _resolve_all_archives,
    _resolve_publish_archives,
    _variant_exists,
)

# ── main() wrapper for backward compat with tests ──────────────


def main(argv: list[str] | None = None) -> int:
    """Entry point for programmatic invocation and tests.

    Wraps the Click CLI group so callers get a clean integer return
    code instead of SystemExit.  All CvcpkgError and ClickException
    errors are caught, printed, and mapped to exit code 1.

    Args:
        argv: Command-line arguments, e.g. ``["install", "--from", "req.yaml"]``.
              Defaults to ``sys.argv[1:]`` when ``None``.

    Returns:
        0 on success, non-zero on error.
    """
    try:
        cli(args=argv, standalone_mode=False)
        return 0
    except click.exceptions.Exit as e:
        return e.code
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    except click.ClickException as e:
        e.show()
        return 1
    except CvcpkgError as e:
        click.echo(f"cvcpkg: ERROR: {e}", err=True)
        return 1


if __name__ == "__main__":
    cli()
