# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""``cvcpkg image`` — locate and check installed VM/disk images.

An image package installs a bootable guest disk into
``<prefix>/share/<package-name>/`` (see :mod:`cvcpkg.images`).  A provisioning
script needs to answer four questions about it — where is the disk, where is
the importer metadata, are the bytes still good, and what does the hypervisor
need to be told — without hardcoding a layout that will change.  This group is
that contract.

It is deliberately in CORE, not an extra: the consumer is a ``/bin/sh`` script
on a bare cluster node that just ran ``cvcpkg install <image>``.  Nothing here
contacts a server or reads an index; discovery is a glob over
``<prefix>/share/*/image.yaml``, which is a filesystem property of the layout.

Exit codes (``image path`` in particular is meant for ``$(...)`` capture):

* ``0`` — success, and for ``path``/``dir``/``env`` stdout holds ONLY the answer
* ``1`` — usage or I/O error
* ``3`` — no such image installed in this prefix
* ``4`` — the image has no artifact for the requested role
* ``5`` — ``verify`` found a checksum mismatch
* ``6`` — ``test`` booted the image and the guest failed (a SKIP is still 0)
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from cvcpkg import images as _images
from cvcpkg import vmtest as _vmtest
from cvcpkg.cli import cli
from cvcpkg.cli._helpers import _human_size, _prefix_opt

# Exit codes a shell consumer branches on.
EXIT_NOT_INSTALLED = 3
EXIT_NO_SUCH_ROLE = 4
EXIT_VERIFY_FAILED = 5
EXIT_VM_TEST_FAILED = 6


def _die(message: str, code: int) -> None:
    """Report on stderr and exit with *code*, leaving stdout clean."""
    click.echo(f"cvcpkg: {message}", err=True)
    raise SystemExit(code)


def _load(prefix: str, name: str) -> _images.InstalledImage:
    """Look up *name* under *prefix* or exit 3 with an actionable message."""
    prefix_path = Path(prefix).resolve()
    try:
        image = _images.find_image(prefix_path, name)
    except _images.ImageError as exc:
        _die(str(exc), 1)
    if image is None:
        installed = [i.name for i in _images.discover_images(prefix_path)]
        hint = f" installed images: {', '.join(installed)}" if installed else " none installed"
        _die(f"image '{name}' is not installed in {prefix_path} —{hint}", EXIT_NOT_INSTALLED)
    return image  # type: ignore[return-value]


@cli.group("image")
def image_group() -> None:
    """Locate and check VM/disk images installed into a prefix.

    Images live at <prefix>/share/<package-name>/ with role-based
    filenames, so every path below is derivable from the package name alone.

    \b
    Typical provisioning use:
      export CVCPKG_PREFIX=/srv/cvcpkg/images
      cvcpkg install haiku-image
      cvcpkg image verify haiku-image
      DISK=$(cvcpkg image path haiku-image)
      META=$(cvcpkg image path haiku-image --role incus-metadata)
      eval "$(cvcpkg image env haiku-image)"
    """


# ── ls ──────────────────────────────────────────────────────────


@image_group.command("ls")
@_prefix_opt
@click.option(
    "--json", "as_json", is_flag=True, default=False, help="Emit JSON instead of a table."
)
@click.option("--guest-os", default=None, help="Only list images whose guest OS is this.")
def image_ls(prefix: str, as_json: bool, guest_os: str | None) -> None:
    """List the images installed in a prefix.

    \b
    Example:
      cvcpkg image ls --prefix /srv/cvcpkg/images
    """
    prefix_path = Path(prefix).resolve()
    found = _images.discover_images(prefix_path)
    if guest_os:
        found = [i for i in found if i.guest_os == guest_os]

    if as_json:
        click.echo(json.dumps([i.to_dict() for i in found], indent=2))
        return

    if not found:
        click.echo(f"cvcpkg: no images installed in {prefix_path}", err=True)
        return

    header = ("NAME", "GUEST", "ARCH", "RELEASE", "VARIANT", "FIRMWARE", "BUS", "SIZE", "VERSION")
    rows = [
        (
            i.name,
            i.guest_os or "-",
            i.guest_arch or "-",
            i.guest_release or "-",
            i.variant or "-",
            i.firmware or "-",
            i.disk_bus or "-",
            _human_size(i.virtual_size_bytes) if i.virtual_size_bytes else "-",
            i.version or "-",
        )
        for i in found
    ]
    widths = [max(len(header[c]), *(len(r[c]) for r in rows)) for c in range(len(header))]
    click.echo("  ".join(h.ljust(w) for h, w in zip(header, widths, strict=False)).rstrip())
    for row in rows:
        click.echo("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=False)).rstrip())


# ── path / dir ──────────────────────────────────────────────────


@image_group.command("path")
@click.argument("name")
@_prefix_opt
@click.option(
    "--role",
    type=click.Choice(list(_images.ROLES)),
    default="disk",
    help="Which artifact to locate (default: the bootable disk).",
)
def image_path(name: str, prefix: str, role: str) -> None:
    """Print ONE absolute path for NAME's ROLE artifact and nothing else.

    This is the entire contract for a shell consumer: capture stdout, branch on
    the exit code (3 = not installed, 4 = no such role).

    \b
    Example:
      DISK=$(cvcpkg image path haiku-image) || exit 1
    """
    image = _load(prefix, name)
    target = image.role_path(role)
    if target is None:
        _die(
            f"image '{name}' has no '{role}' artifact "
            f"(available: {', '.join(r for r in _images.ROLES if image.role_path(r))})",
            EXIT_NO_SUCH_ROLE,
        )
        return
    click.echo(str(target))


@image_group.command("dir")
@click.argument("name")
@_prefix_opt
def image_dir(name: str, prefix: str) -> None:
    """Print the directory NAME's files live in, and nothing else."""
    click.echo(str(_load(prefix, name).directory))


# ── info ────────────────────────────────────────────────────────


@image_group.command("info")
@click.argument("name")
@_prefix_opt
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit the raw descriptor.")
def image_info(name: str, prefix: str, as_json: bool) -> None:
    """Show NAME's descriptor (image.yaml).

    \b
    Example:
      cvcpkg image info haiku-image --json | jq .boot.disk_bus
    """
    image = _load(prefix, name)
    if as_json:
        click.echo(json.dumps(image.data, indent=2, sort_keys=False, default=str))
        return

    click.echo(f"Name:         {image.name}")
    click.echo(f"Version:      {image.version or '-'}")
    click.echo(f"Directory:    {image.directory}")
    click.echo(
        "Guest:        "
        f"{image.guest_os or '-'} {image.guest_release or ''} "
        f"({image.guest_arch or '-'})".rstrip()
    )
    if image.variant:
        click.echo(f"Variant:      {image.variant}")
    for disk in image.disks:
        size = disk.get("virtual_size_bytes") or 0
        click.echo(
            f"Disk:         {disk.get('file', '?')} "
            f"[{disk.get('format', '?')}, {disk.get('role', '?')}"
            + (f", {_human_size(int(size))}" if size else "")
            + "]"
        )
    for key in ("firmware", "disk_bus", "net_model", "console", "secureboot"):
        value = image.boot.get(key)
        if value not in (None, ""):
            click.echo(f"{(key + ':').ljust(13)} {value}")
    for key, label in (
        ("cpu_min", "Min vCPUs:   "),
        ("memory_min_mib", "Min memory:  "),
        ("disk_min_gib", "Min disk:    "),
    ):
        value = image.boot.get(key)
        if value not in (None, ""):
            unit = {"memory_min_mib": " MiB", "disk_min_gib": " GiB"}.get(key, "")
            click.echo(f"{label} {value}{unit}")
    if image.access.get("ssh_user"):
        click.echo(f"SSH user:     {image.access['ssh_user']}")
    for importer, rel in sorted(image.importers.items()):
        click.echo(f"Importer:     {importer} -> {rel}")
    if not image.writable:
        click.echo(
            "Writable:     no — boot a qcow2 overlay with this disk as the "
            "backing file, never the master in place"
        )


# ── env ─────────────────────────────────────────────────────────


@image_group.command("env")
@click.argument("name")
@_prefix_opt
@click.option(
    "--relative",
    is_flag=True,
    default=False,
    help="Emit paths relative to the image directory (as the shipped image.env does).",
)
def image_env(name: str, prefix: str, relative: bool) -> None:
    """Emit NAME's facts as CVCPKG_IMAGE_* shell assignments.

    Regenerated from image.yaml — the same facts the shipped image.env
    carries, but with absolute paths, since this output is meant for eval
    from an arbitrary working directory.

    \b
    Example:
      eval "$(cvcpkg image env haiku-image)"
      incus init img vm --vm -c limits.cpu="$CVCPKG_IMAGE_CPU_MIN"
    """
    click.echo(_images.env_script(_load(prefix, name), absolute=not relative), nl=False)


# ── verify ──────────────────────────────────────────────────────


@image_group.command("verify")
@click.argument("name")
@_prefix_opt
@click.option("--quiet", is_flag=True, default=False, help="Only report failures.")
def image_verify(name: str, prefix: str, quiet: bool) -> None:
    """Re-hash NAME's files on disk against its SHA256SUMS.

    Not redundant with the installer's download-time check: that covers the
    archive, once, when it was fetched.  This covers a multi-gigabyte payload
    months later, where it actually sits.

    \b
    Example:
      cvcpkg image verify haiku-image || exit 1
    """
    image = _load(prefix, name)
    try:
        rows = _images.verify_image(image)
    except _images.ImageError as exc:
        _die(str(exc), 1)
        return

    bad = [r for r in rows if r[1] != "OK"]
    for relpath, status, detail in rows:
        if quiet and status == "OK":
            continue
        line = f"  {status:<8} {relpath}"
        click.echo(line if status == "OK" else f"{line}  ({detail})")
    if bad:
        _die(
            f"image '{name}': {len(bad)} of {len(rows)} file(s) FAILED verification",
            EXIT_VERIFY_FAILED,
        )
    if not quiet:
        click.echo(f"cvcpkg: {name} verified ({len(rows)} file(s)).")


# ── export ──────────────────────────────────────────────────────


@image_group.command("export")
@click.argument("name")
@_prefix_opt
@click.option(
    "--to",
    "dest",
    required=True,
    type=click.Path(file_okay=False),
    help="Directory to copy the artifact into.",
)
@click.option(
    "--role",
    type=click.Choice(list(_images.ROLES)),
    default="disk",
    help="Which artifact to export (default: the bootable disk).",
)
def image_export(name: str, prefix: str, dest: str, role: str) -> None:
    """Copy NAME's ROLE artifact out of the prefix, under a meaningful name.

    Never hand a hypervisor a path inside a cvcpkg prefix — a later
    cvcpkg install can replace those bytes underneath a running VM.  The
    copy is reflinked where the filesystem supports it.

    \b
    Example:
      cvcpkg image export haiku-image --to /var/tmp
      # -> /var/tmp/haiku-image-1.0.0-beta.5+cvc.1.qcow2
    """
    image = _load(prefix, name)
    if image.role_path(role) is None:
        _die(f"image '{name}' has no '{role}' artifact", EXIT_NO_SUCH_ROLE)
    try:
        out = _images.export_image(image, dest, role=role)
    except (OSError, _images.ImageError) as exc:
        _die(str(exc), 1)
        return
    click.echo(str(out))


# ── test ────────────────────────────────────────────────────────


@image_group.command("test")
@click.argument("name")
@_prefix_opt
@click.option(
    "--script",
    "script",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Script to run INSIDE the guest (default: assert only that it boots).",
)
@click.option(
    "--connect",
    type=click.Choice(["ssh", "agent"]),
    default="ssh",
    help="How to reach the guest (default: ssh; agent needs the incus agent).",
)
@click.option("--ssh-user", default=None, help="Override image.yaml's access.ssh_user.")
@click.option("--ssh-key", "ssh_key", default=None, help="Private key file for --connect ssh.")
@click.option(
    "--hypervisor",
    "hypervisors",
    multiple=True,
    type=click.Choice(list(_vmtest.VM_CAPABLE)),
    help="Restrict which hypervisor is driven (repeatable, in preference order).",
)
@click.option(
    "--timeout",
    default=_vmtest.DEFAULT_TIMEOUT_SECONDS,
    show_default=True,
    help="Hard wall-clock ceiling for the whole VM phase, in seconds.",
)
@click.option(
    "--boot-timeout",
    default=_vmtest.DEFAULT_BOOT_TIMEOUT_SECONDS,
    show_default=True,
    help="Ceiling for 'the guest became reachable', in seconds.",
)
def image_test(
    name: str,
    prefix: str,
    script: str | None,
    connect: str,
    ssh_user: str | None,
    ssh_key: str | None,
    hypervisors: tuple[str, ...],
    timeout: int,
    boot_timeout: int,
) -> None:
    """Boot NAME in a throwaway VM, assert, and destroy it.

    The manual half of a recipe's `test.vm` block: same engine, same
    destroy-always lifecycle, driven against an image that is already
    installed rather than one being built.  Use it to reproduce a builder's
    VM-test failure, or to sanity-check an image after copying it to a node.

    Exits 0 on pass OR skip (no hypervisor is not a failure), 6 on a real
    failure.  A skip always says which gate stopped it.

    \b
    Example:
      cvcpkg image test haiku-image --ssh-key ~/.ssh/haiku_builder
    """
    image = _load(prefix, name)
    spec = _vmtest.VmTestSpec(
        hypervisors=tuple(hypervisors) or _vmtest.VM_CAPABLE,
        script=Path(script).name if script else None,
        connect=connect,
        ssh_user=ssh_user,
        ssh_key_file=ssh_key,
        timeout_seconds=timeout,
        boot_timeout_seconds=boot_timeout,
    )
    try:
        result = _vmtest.run_vm_test(
            spec=spec,
            image=image,
            script_path=Path(script) if script else None,
            log=lambda m: click.echo(m, err=True),
        )
    except _vmtest.VmTestError as exc:
        _die(str(exc), 1)
        return

    if result.output:
        click.echo(result.output)
    click.echo(_vmtest.format_result(name, result), err=True)
    if result.leaked:
        click.echo(
            f"cvcpkg: WARNING: the test VM or its imported image may still exist — "
            f"look for {_vmtest.INSTANCE_PREFIX}* in BOTH `image list` and `list`",
            err=True,
        )
    if result.status == _vmtest.FAILED:
        raise SystemExit(EXIT_VM_TEST_FAILED)
