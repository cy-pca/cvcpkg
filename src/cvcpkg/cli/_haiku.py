# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""``cvcpkg haiku`` — draft a HaikuPorts recipe from a cvcpkg recipe.

``cvcpkg haiku draft-recipe <name>`` transcribes the fields cvcpkg already
knows into ``<category>/<port>/<port>-<version>.recipe`` and marks everything
else ``# TODO(human):``.  The reader is a Haiku developer who wants to build
the port under their own ``haikuporter``; the recipe goes to stdout by default
so it can be diffed or piped, and ``--output`` writes it into a haikuports
checkout.

Design: this command deliberately stops at a file.  It does not build, does not
push, and has no ``--submit``.  HaikuPorts' pull-request template opens with
"You are not a robot." and requires the submitter to attest that the recipe was
built on their Haiku machine — only the person at the keyboard can say that, so
whether a draft is ever upstreamed is their call alone.  The checklist printed
to stderr says so in as many words.

``--install-tree`` points at the install tree of a **real Haiku build** (the
directory :mod:`cvcpkg.haikuhost` copies back off the Haiku box).  Only with it
can PROVIDES/REQUIRES be derived rather than guessed; without it those blocks
degrade to TODOs, which is the honest outcome.
"""

from __future__ import annotations

from pathlib import Path

import click
import yaml

from cvcpkg import haikuports as _hp
from cvcpkg.cli import cli
from cvcpkg.cli._helpers import _no_default_recipes_opt, _recipes_dir_opt


@cli.group("haiku")
def haiku_group() -> None:
    """Integration with HaikuPorts (https://github.com/haikuports/haikuports)."""


def _resolve_recipe_dir(name: str, recipes_dirs: tuple[str, ...], no_default: bool) -> Path:
    from cvcpkg.cli._build import _resolve_recipe_dir as _resolve

    return _resolve(name, recipes_dirs, no_default=no_default)


@haiku_group.command("draft-recipe")
@click.argument("name")
@_recipes_dir_opt
@_no_default_recipes_opt
@click.option(
    "--install-tree",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help=(
        "Install tree from a REAL Haiku build of this recipe.  Grounds "
        "PROVIDES/REQUIRES in actual binaries instead of leaving them as TODOs."
    ),
)
@click.option(
    "--output",
    type=click.Path(file_okay=False),
    default=None,
    help="haikuports checkout root to write <category>/<port>/<port>-<ver>.recipe into.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="With --output, report the path that would be written but write nothing.",
)
@click.option("--revision", type=int, default=1, help="HaikuPorts REVISION (default 1).")
@click.option(
    "--force", is_flag=True, default=False, help="Overwrite an existing file under --output."
)
@click.option(
    "--lint/--no-lint",
    default=True,
    help="Run cvcpkg's local copy of HaikuPorts' lint rules over the draft.",
)
def draft_recipe(
    name: str,
    recipes_dirs: tuple[str, ...],
    no_default_recipes: bool,
    install_tree: str | None,
    output: str | None,
    dry_run: bool,
    revision: int,
    force: bool,
    lint: bool,
) -> None:
    """Draft a HaikuPorts .recipe for NAME, for your own haikuporter.

    Output is a DRAFT: it needs a human to finish the TODOs and a real Haiku
    machine to build it.  cvcpkg never submits anything anywhere.

    Examples:

      # Print the draft (no build evidence — resolvables become TODOs):
      cvcpkg haiku draft-recipe zlib

      # Ground PROVIDES/REQUIRES in a real Haiku build's install tree:
      cvcpkg haiku draft-recipe zlib --install-tree ./build/zlib/install

      # Write it into your haikuports checkout:
      cvcpkg haiku draft-recipe zlib --output ~/src/haikuports
    """
    recipe_dir = _resolve_recipe_dir(name, recipes_dirs, no_default_recipes)
    try:
        data = yaml.safe_load((recipe_dir / "recipe.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise click.ClickException(f"could not read {recipe_dir / 'recipe.yaml'}: {exc}") from exc

    build_script = None
    script = recipe_dir / "build.sh"
    if script.is_file():
        build_script = script.read_text()

    facts = _hp.scan_install_tree(Path(install_tree)) if install_tree else None

    try:
        draft = _hp.draft_recipe(data, facts=facts, build_script=build_script, revision=revision)
    except _hp.ConversionRefusedError as exc:
        raise click.ClickException(str(exc)) from exc

    # stdout is only ever the recipe; everything advisory goes to stderr.
    target: Path | None = None
    if output:
        target = Path(output) / draft.relpath
        if target.exists() and not force:
            raise click.ClickException(f"{target} already exists (use --force to overwrite)")
        if dry_run:
            click.echo(f"would write {target}", err=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(draft.text)
            click.echo(f"wrote {target}", err=True)
    else:
        click.echo(draft.text, nl=False)

    _report(draft, lint=lint)


def _report(draft: _hp.HaikuDraft, *, lint: bool) -> None:
    """Print the human's remaining work, and the etiquette, to stderr."""

    def say(text: str) -> None:
        click.echo(text, err=True)

    say(f"\n── DRAFT {draft.relpath} ──")
    if not draft.grounded:
        say("  ! generated WITHOUT a Haiku build: PROVIDES/REQUIRES are TODOs, not data.")

    if lint:
        problems = _hp.lint_draft(draft.text, port=draft.port)
        if problems:
            say(f"\n  lint ({len(problems)} problem(s)) — HaikuPorts CI checks these:")
            for problem in problems:
                say(f"    - {problem}")
        else:
            say("\n  lint: clean (format only — this says nothing about whether it builds)")

    if draft.todos:
        say(f"\n  {len(draft.todos)} thing(s) still need a human:")
        for todo in draft.todos:
            say(f"    - {todo.replace(_hp.TODO, '').strip()}")

    say(
        "\n  To make this build under your own haikuporter:\n"
        "    1. Finish every TODO above.\n"
        "    2. `haikuporter -S <port>` on a real Haiku machine; fix the policy checker.\n"
        "    3. `integrations/haikuports/lint-draft.sh` (runs haikuporter --lint).\n"
        "\n  Sending it upstream is your call, not cvcpkg's.  If you do: delete the\n"
        "  DRAFT banner, open ONE pull request yourself through the GitHub web UI (so\n"
        "  the PR template is not bypassed), leave the checklist intact, and disclose\n"
        "  in the body that the metadata was transcribed by `cvcpkg haiku\n"
        "  draft-recipe` and that you built and tested it by hand.\n"
        "  cvcpkg has no code path that can open that pull request."
    )
