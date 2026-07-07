"""CLI commands — auto-extracted from cli.py."""

from __future__ import annotations

from pathlib import Path

import click

from cvcpkg.cli import cli

# ── key ─────────────────────────────────────────────────────────


@cli.group()
def key() -> None:
    """Manage Ed25519 signing keys.

    Generate keypairs, import public keys from trusted publishers,
    and list the local keyring.

    \b
    Examples:
      cvcpkg key generate --label release
      cvcpkg key list
      cvcpkg key import publisher.pub --label upstream
      cvcpkg key export --label release
    """


@key.command("generate")
@click.option("--label", required=True, help="Human-readable label for this key.")
@click.option("--password", default=None, help="Password-protect the private key.")
@click.option(
    "--keys-dir",
    type=click.Path(),
    default=None,
    help="Override key storage directory.",
)
def key_generate(label: str, password: str | None, keys_dir: str | None) -> None:
    """Generate a new Ed25519 signing keypair."""
    from cvcpkg.signing import generate_keypair

    kd = Path(keys_dir) if keys_dir else None
    info = generate_keypair(label, keys_dir=kd, password=password)
    click.echo(f"Generated key '{info.label}'")
    click.echo(f"  Fingerprint: {info.fingerprint}")
    click.echo(f"  Private key: {info.path}")
    click.echo(f"  Public key:  {info.path.with_suffix('.pub') if info.path else 'N/A'}")


@key.command("list")
@click.option(
    "--keys-dir",
    type=click.Path(),
    default=None,
    help="Override key storage directory.",
)
def key_list(keys_dir: str | None) -> None:
    """List all keys in the keyring."""
    from cvcpkg.signing import list_keys

    kd = Path(keys_dir) if keys_dir else None
    keys = list_keys(kd)
    if not keys:
        click.echo("No keys found.")
        return
    for ki in keys:
        kind = "private+public" if ki.has_private else "public only"
        click.echo(f"  {ki.label:<20} {ki.fingerprint[:16]}...  ({kind})")


@key.command("import")
@click.argument("pub_file", type=click.Path(exists=True))
@click.option("--label", required=True, help="Label for the imported key.")
@click.option(
    "--keys-dir",
    type=click.Path(),
    default=None,
    help="Override key storage directory.",
)
def key_import(pub_file: str, label: str, keys_dir: str | None) -> None:
    """Import a public key into the trusted keyring."""
    from cvcpkg.signing import import_public_key

    kd = Path(keys_dir) if keys_dir else None
    pub_pem = Path(pub_file).read_text()
    info = import_public_key(pub_pem, label, keys_dir=kd)
    click.echo(f"Imported '{info.label}' ({info.fingerprint[:16]}...)")


@key.command("export")
@click.option("--label", required=True, help="Label of the key to export.")
@click.option(
    "--keys-dir",
    type=click.Path(),
    default=None,
    help="Override key storage directory.",
)
def key_export(label: str, keys_dir: str | None) -> None:
    """Export a public key (PEM) to stdout."""
    from cvcpkg.signing import list_keys

    kd = Path(keys_dir) if keys_dir else None
    keys = list_keys(kd)
    for ki in keys:
        if ki.label == label:
            click.echo(ki.public_pem, nl=False)
            return
    raise click.ClickException(f"Key '{label}' not found")


# ── sign ────────────────────────────────────────────────────────


@cli.command()
@click.argument("archive", type=click.Path(exists=True))
@click.option(
    "--signing-key",
    required=True,
    type=click.Path(exists=True),
    help="Path to Ed25519 private key (.key).",
)
@click.option("--password", default=None, help="Password for the signing key.")
def sign(archive: str, signing_key: str, password: str | None) -> None:
    """Sign an archive file.

    Creates a detached ``<archive>.sig`` signature file alongside
    the archive.

    \b
    Example:
      cvcpkg sign dist/zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.gz \\
          --signing-key ~/.config/cvcpkg/keys/release.key
    """
    from cvcpkg.signing import sign_file, write_signature

    archive_path = Path(archive)
    sig = sign_file(archive_path, Path(signing_key), password)
    sig_path = archive_path.with_suffix(archive_path.suffix + ".sig")
    write_signature(sig, sig_path)
    click.echo(f"Signed: {sig_path.name} (key: {sig.key_fingerprint[:16]}...)")


# ── verify-sig ──────────────────────────────────────────────────


@cli.command("verify-sig")
@click.argument("archive", type=click.Path(exists=True))
@click.option(
    "--sig-file",
    type=click.Path(exists=True),
    default=None,
    help="Signature file (defaults to <archive>.sig).",
)
@click.option(
    "--keys-dir",
    type=click.Path(),
    default=None,
    help="Override key storage directory.",
)
def verify_sig(archive: str, sig_file: str | None, keys_dir: str | None) -> None:
    """Verify the signature of an archive against trusted keys.

    \b
    Example:
      cvcpkg verify-sig dist/zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.gz
    """
    from cvcpkg.signing import read_signature, verify_file

    archive_path = Path(archive)
    if sig_file:
        sp = Path(sig_file)
    else:
        sp = archive_path.with_suffix(archive_path.suffix + ".sig")
    if not sp.is_file():
        raise click.ClickException(f"Signature file not found: {sp}")

    kd = Path(keys_dir) if keys_dir else None
    sig = read_signature(sp)
    ki = verify_file(archive_path, sig, kd)
    click.echo(f"Verified: signed by '{ki.label}' ({ki.fingerprint[:16]}...)")
