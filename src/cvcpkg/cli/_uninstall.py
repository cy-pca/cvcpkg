# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""The ``cvcpkg uninstall`` command."""

from __future__ import annotations

from pathlib import Path

import click

from cvcpkg.cli import cli
from cvcpkg.cli._helpers import _prefix_opt, _resolve_recipes_dirs

# ── uninstall ───────────────────────────────────────────────────


@cli.command()
@click.argument("components", nargs=-1, required=True)
@_prefix_opt
@click.option(
    "--cascade",
    is_flag=True,
    help=(
        "Also uninstall every installed package that (transitively) depends "
        "on a target.  Without this flag, uninstall refuses when dependents "
        "exist rather than silently breaking them."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be removed without touching the prefix.",
)
def uninstall(components: tuple[str, ...], prefix: str, cascade: bool, dry_run: bool) -> None:
    """Uninstall component bundles from a prefix.

    Removes the files each named component's bundle extracted into the
    prefix, prunes directories the removal emptied, and rewrites the
    lockfile.  When other installed packages depend on a target, the
    command refuses to proceed; pass --cascade to remove the dependent
    closure along with it.

    File lists are read from the bundle archives recorded in the lockfile
    (served from the local download cache, re-downloaded if evicted).  A
    component that was built from source has no archive, so it cannot be
    uninstalled this way — recreate the prefix without it instead.

    \b
    Examples:
      cvcpkg uninstall python313 --prefix ./deps
      cvcpkg uninstall boost --cascade --prefix ./deps
      cvcpkg uninstall zlib --dry-run --prefix ./deps
    """
    from cvcpkg.cache import default_cache_dir
    from cvcpkg.lockfile import Lockfile
    from cvcpkg.uninstaller import (
        dependent_closure,
        execute_removal,
        fetch_removal_archive,
        load_installed,
        plan_removal,
    )

    prefix_path = Path(prefix).resolve()
    lock_path = prefix_path / "share" / "libcvc-deps" / "lockfile.yaml"
    if not lock_path.is_file():
        raise click.ClickException(f"no lockfile at {lock_path} -- nothing to uninstall.")
    lock = Lockfile.read(lock_path)

    targets = list(dict.fromkeys(components))  # dedupe, keep order
    installed_names = {b.name for b in lock.bundles}
    missing = [t for t in targets if t not in installed_names]
    if missing:
        listing = ", ".join(sorted(installed_names)) or "(none)"
        raise click.ClickException(
            f"not installed in {prefix_path}: {', '.join(missing)}.\n"
            f"The lockfile lists: {listing}."
        )

    # Local recipes are only a fallback dep source for source-built entries;
    # like install's conflict check, their absence must not fail the command.
    try:
        rdirs: list[Path] | None = _resolve_recipes_dirs((), no_default=False)
    except click.ClickException:
        rdirs = None

    cache_dir = default_cache_dir()
    packages = load_installed(lock, cache_dir, rdirs)

    # A bystander with unknown deps could invisibly depend on a target; warn
    # rather than refuse -- the archive may simply have been evicted, and
    # refusing would make such prefixes permanently un-uninstallable.  One
    # summary line, not one per package: a prefix that has been around a while
    # can easily have a dozen bundles aged out of the cache.
    unknown = [n for n, p in sorted(packages.items()) if not p.deps_known and n not in targets]
    if unknown:
        click.echo(
            f"cvcpkg: warning -- cannot determine the dependencies of "
            f"{len(unknown)} installed package(s), so they are treated as "
            f"depending on nothing (no cached archive and no local recipe): "
            f"{', '.join(unknown)}",
            err=True,
        )

    closure = dependent_closure(set(targets), packages)
    dependents = sorted(closure - set(targets))
    if dependents and not cascade:
        raise click.ClickException(
            f"cannot uninstall {', '.join(targets)}: "
            f"other installed package(s) depend on the target(s): {', '.join(dependents)}.\n"
            f"Uninstalling would break them.  Either uninstall the dependents first, or\n"
            f"re-run with --cascade to uninstall the whole dependent closure:\n"
            f"  cvcpkg uninstall {' '.join(targets)} --cascade --prefix {prefix_path}"
        )

    removal_names = sorted(closure) if cascade else list(targets)
    removal = {n: packages[n] for n in removal_names}
    kept = {n: p for n, p in packages.items() if n not in removal}

    # Removal targets hard-require their archive: it is the file list.
    for name, pkg in removal.items():
        if pkg.source_built:
            raise click.ClickException(
                f"{name!r} was built from source; no archive records its files, so "
                f"cvcpkg cannot remove it file-by-file.\n"
                f"Recreate the prefix without it instead, e.g.:\n"
                f"  cvcpkg install <remaining components> --prefix <new-dir>"
            )
        if pkg.archive is None:
            click.echo(f"cvcpkg: fetching archive for {name} {pkg.entry.version} ...")
            fetch_removal_archive(pkg, lock, cache_dir)

    plan = plan_removal(removal, kept, lock.platform)

    verb = "would uninstall" if dry_run else "uninstalling"
    click.echo(f"cvcpkg: {verb} {len(removal)} package(s) from {prefix_path}:")
    for name in removal_names:
        tag = "  [dependent]" if name not in targets else ""
        click.echo(
            f"  {name} == {removal[name].entry.version} ({len(plan.remove[name])} file(s)){tag}"
        )
    if plan.shared_kept:
        click.echo(
            f"cvcpkg: keeping {len(plan.shared_kept)} path(s) also owned by remaining packages."
        )
    if dry_run:
        click.echo("cvcpkg: dry run -- nothing removed.")
        return

    all_paths = [p for name in removal_names for p in plan.remove[name]]
    result = execute_removal(prefix_path, all_paths)

    # Rewrite the lockfile even when some paths could not be removed: the
    # packages ARE gone as far as the prefix is concerned, and leaving them
    # listed would make a re-run try to remove them all over again.
    lock.bundles = [b for b in lock.bundles if b.name not in removal]
    lock.write(lock_path)

    summary = f"cvcpkg: done -- removed {result.removed} file(s)"
    if result.absent:
        summary += f" ({result.absent} already absent)"
    if result.dirs_pruned:
        summary += f", pruned {result.dirs_pruned} empty dir(s)"
    click.echo(summary)
    click.echo(f"cvcpkg: lockfile updated ({len(lock.bundles)} bundle(s) remain).")

    if result.failed:
        click.echo(
            f"cvcpkg: warning -- {len(result.failed)} path(s) could not be removed "
            f"and are still in the prefix:",
            err=True,
        )
        for path, reason in result.failed:
            click.echo(f"  {path}: {reason}", err=True)
