#!/usr/bin/env python3
"""Turn a Cargo.lock into cvcpkg crate packages and a crate set for its consumer.

usage: tools/make_crate_packages.py --lockfile Cargo.lock --set NAME --set-version VERSION
                                    [--recipes-dir recipes] [--crate-sources DIR]
                                    [--consumer TEXT]

Every crate a Cargo.lock pins from crates.io becomes a recipe of its own,
``crate-<name>--<version>`` (docs/rust-crates.md): ``platform: any``, the
``.crate`` file from static.crates.io as its tarball, and the checksum Cargo.lock
records as its sha256 -- so the crate is downloaded once, by whichever builder
publishes the noarch package, and checked against the same hash cargo checks.
It stages the crate as one entry of a cargo directory source,
``share/cargo/crates/<name>-<version>/``, with the ``.cargo-checksum.json``
cargo needs there.  A recipe that already exists for a crate is reused: one
package per exact crate version, shared by every Rust recipe that pins it.

The set recipe ``NAME`` (normally ``<consumer>-crates``) lists every crate of
the lockfile in ``depends.runtime`` and ships ``share/cargo/sets/NAME/``: the
lockfile itself and ``cargo-config.toml.in``, the source replacement that
points cargo at the crates.  A consumer declares the set in ``depends.build``,
so the crates land in its build prefix, and writes the config with
``@CRATES_DIR@`` replaced by ``<build prefix>/share/cargo/crates`` before
``cargo build --offline --locked``.

Git dependencies are not generated: each needs a hand-written recipe named
``crate-git-<name>--<first 8 hex of the commit>`` that stages
``share/cargo/crates/<name>-<version>-git-<8 hex>/`` (see
recipes/crate-git-xcb-imdkit--215ce4b0).  The tool stops and names any that
are missing.  Path dependencies (the workspace's own members) are skipped.

License and description come from each crate's Cargo.toml, read from
``--crate-sources`` (a directory of extracted crates, ``<name>-<version>/``, such
as a ``cargo vendor --versioned-dirs`` output) when it has the crate, else from
the crates.io API (one request a second, as crates.io asks).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

try:  # pragma: no cover - version shim
    import tomllib
except ModuleNotFoundError:  # Python 3.10: the backport (in the dev environment)
    import tomli as tomllib

CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"
USER_AGENT = "cvcpkg tools/make_crate_packages.py (https://github.com/cy-pca/cvcpkg)"

MAINTAINER = (
    '  maintainer: "cvcpkg group"\n'
    '  maintainer_email: "info@cvcpkg.org"\n'
    '  maintainer_url: "https://cvcpkg.org"\n'
)


@dataclass(frozen=True)
class Crate:
    name: str
    version: str
    checksum: str

    @property
    def recipe(self) -> str:
        return crate_recipe_name(self.name, self.version)

    @property
    def dirname(self) -> str:
        return f"{self.name}-{self.version}"

    @property
    def url(self) -> str:
        return f"https://static.crates.io/crates/{self.name}/{self.name}-{self.version}.crate"


@dataclass(frozen=True)
class GitDep:
    name: str
    version: str
    source_key: str  # the lockfile source without its #commit fragment
    url: str
    query: dict[str, str]  # rev / branch / tag, as written in the dependency
    commit: str

    @property
    def recipe(self) -> str:
        return f"crate-git-{_slug(self.name)}--{self.commit[:8]}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def crate_recipe_name(name: str, version: str) -> str:
    """``crate-<name>--<version>``: lowercase, ``_`` and ``.`` as ``-``.

    crates.io treats ``-`` and ``_`` in names as the same crate, so folding them
    cannot merge two crates.  ``+build`` metadata is dropped: cargo and crates.io
    ignore it, so two versions never differ only there.  The ``--`` keeps the
    name and the version apart (a crate name may end in digits)."""
    return f"crate-{_slug(name)}--{_slug(version.split('+', 1)[0])}"


def upstream_version(version: str) -> str:
    """The crate version without ``+build`` metadata (cvcpkg reserves ``+cvc.N``)."""
    return version.split("+", 1)[0]


def read_lockfile(path: Path) -> tuple[list[Crate], list[GitDep]]:
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
    crates: list[Crate] = []
    gits: list[GitDep] = []
    for pkg in doc.get("package", []):
        source = pkg.get("source")
        if source is None:
            continue  # a workspace member
        if source == CRATES_IO:
            if "checksum" not in pkg:
                raise SystemExit(f"{path}: {pkg['name']} {pkg['version']} has no checksum")
            crates.append(Crate(pkg["name"], pkg["version"], pkg["checksum"]))
        elif source.startswith("git+"):
            key, _, commit = source.partition("#")
            parts = urllib.parse.urlsplit(key[len("git+") :])
            url = urllib.parse.urlunsplit(parts._replace(query=""))
            query = dict(urllib.parse.parse_qsl(parts.query))
            gits.append(GitDep(pkg["name"], pkg["version"], key, url, query, commit))
        else:
            raise SystemExit(f"{path}: {pkg['name']}: unsupported source {source!r}")
    return crates, gits


def crate_metadata(crate: Crate, sources: Path | None) -> tuple[str, str]:
    """(license, description) from the crate's Cargo.toml, or from crates.io."""
    if sources is not None:
        toml = sources / crate.dirname / "Cargo.toml"
        if toml.is_file():
            pkg = tomllib.loads(toml.read_text(encoding="utf-8")).get("package", {})
            return _license_text(pkg.get("license"), pkg.get("license-file")), _one_line(
                pkg.get("description", "")
            )
    req = urllib.request.Request(
        f"https://crates.io/api/v1/crates/{crate.name}/{crate.version}",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    time.sleep(1.0)
    version = data.get("version", {})
    return _license_text(version.get("license"), None), _one_line(version.get("description") or "")


def _license_text(license: str | None, license_file: str | None) -> str:
    if license:
        # Older crates write "MIT/Apache-2.0"; SPDX says "OR".
        return " OR ".join(part.strip() for part in license.split("/"))
    if license_file:
        return f"LicenseRef-crate-file-{_slug(license_file)}"
    return "LicenseRef-unspecified"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _q(text: str) -> str:
    """A YAML double-quoted scalar (JSON's escaping is valid YAML), ASCII only:
    cvcpkg on a Windows builder has read recipe.yaml in the locale's code page,
    where a crate description's curly quote fails to decode."""
    return json.dumps(text, ensure_ascii=True)


def crate_recipe_yaml(crate: Crate, license: str, description: str, consumer: str) -> str:
    about = f"The {crate.name} {crate.version} crate from crates.io"
    if description:
        about += f" ({description.rstrip('.')})"
    about += (
        ", as one entry of a cargo directory source at share/cargo/crates/: Rust recipes "
        "build from it offline instead of downloading it. The .crate file is checked "
        "against the sha256 the Cargo.lock records."
    )
    if consumer:
        about += f" Pinned first by {consumer}."
    about += " Generated by tools/make_crate_packages.py."
    return (
        "schema_version: 1\n"
        "recipe:\n"
        f"  name: {crate.recipe}\n"
        f"  upstream_version: {_q(upstream_version(crate.version))}\n"
        "  cvc_revision: 1\n"
        f"{MAINTAINER}"
        f"  homepage: https://crates.io/crates/{crate.name}/{crate.version}\n"
        f"  license: {_q(license)}\n"
        "  tags: [source, rust-crate]\n"
        f"  description: {_q(about)}\n"
        "\n"
        "source:\n"
        "  type: tarball\n"
        f"  url: {crate.url}\n"
        f'  sha256: "{crate.checksum}"\n'
        "  strip_components: 1\n"
        "\n"
        "patches: []\n"
        "\n"
        "depends:\n"
        "  build: []\n"
        "  runtime: []\n"
        "  host_tools: []\n"
        "\n"
        "build:\n"
        "  build_type_independent: true\n"
        "  matrix:\n"
        "    - platform: any\n"
        "      script: build.sh\n"
        "\n"
        "package:\n"
        "  files:\n"
        f"    - share/cargo/crates/{crate.dirname}/\n"
    )


def crate_build_sh(crate: Crate) -> str:
    return (
        "#!/usr/bin/env bash\n"
        f"# recipes/{crate.recipe}/build.sh -- stage the {crate.name} {crate.version} crate as\n"
        "# an entry of the cargo directory source share/cargo/crates/ (docs/rust-crates.md).\n"
        "# Generated by tools/make_crate_packages.py.\n"
        "set -euo pipefail\n"
        f'dest="${{CVC_INSTALL_DIR}}/share/cargo/crates/{crate.dirname}"\n'
        'mkdir -p "${dest}"\n'
        'cp -R "${CVC_SOURCE_DIR}/." "${dest}/"\n'
        '# cargo reads a directory-source entry\'s checksums from here. "package" is the\n'
        "# Cargo.lock checksum, which cargo compares; no per-file hashes (the bundle's\n"
        "# own sha256 covers the files).\n"
        f'printf \'{{"files":{{}},"package":"{crate.checksum}"}}\\n\''
        ' > "${dest}/.cargo-checksum.json"\n'
    )


def set_recipe_yaml(
    name: str, version: str, consumer: str, members: list[str], lockfile_name: str
) -> str:
    deps = "".join(f"    - name: {m}\n" for m in members)
    about = (
        f"The crates {consumer or name} builds from: every crate its Cargo.lock pins, "
        "each its own cvcpkg package (crate-<name>--<version>), as runtime dependencies. "
        "A recipe that build-depends on this set gets them all in its build prefix's "
        f"share/cargo/crates/, and share/cargo/sets/{name}/cargo-config.toml.in points "
        "cargo there. Generated by tools/make_crate_packages.py."
    )
    return (
        "schema_version: 1\n"
        "recipe:\n"
        f"  name: {name}\n"
        f"  upstream_version: {_q(version)}\n"
        "  cvc_revision: 1\n"
        f"{MAINTAINER}"
        "  homepage: https://cvcpkg.org\n"
        '  license: "MIT"\n'
        "  tags: [rust-crate-set]\n"
        f"  description: {_q(about)}\n"
        "\n"
        "source:\n"
        "  type: none\n"
        "\n"
        "patches: []\n"
        "\n"
        f"# {len(members)} crates, from {lockfile_name} (Cargo.lock beside this file).\n"
        "depends:\n"
        "  build: []\n"
        "  runtime:\n"
        f"{deps}"
        "  host_tools: []\n"
        "\n"
        "build:\n"
        "  build_type_independent: true\n"
        "  matrix:\n"
        "    - platform: any\n"
        "      script: build.sh\n"
        "\n"
        "package:\n"
        "  files:\n"
        f"    - share/cargo/sets/{name}/\n"
    )


def set_build_sh(name: str) -> str:
    return (
        "#!/usr/bin/env bash\n"
        f"# recipes/{name}/build.sh -- ship the lockfile and the cargo source replacement\n"
        "# of this crate set (docs/rust-crates.md). Generated by tools/make_crate_packages.py.\n"
        "set -euo pipefail\n"
        f'dest="${{CVC_INSTALL_DIR}}/share/cargo/sets/{name}"\n'
        'mkdir -p "${dest}"\n'
        'cp "$(dirname "$0")/Cargo.lock" "$(dirname "$0")/cargo-config.toml.in" "${dest}/"\n'
    )


def cargo_config(name: str, gits: list[GitDep]) -> str:
    out = [
        f"# cargo source replacement for the {name} crate set (docs/rust-crates.md).",
        "# Generated by tools/make_crate_packages.py. A consumer replaces @CRATES_DIR@",
        "# with <build prefix>/share/cargo/crates and builds with --offline --locked.",
        "",
        "[source.crates-io]",
        'replace-with = "cvcpkg-crates"',
        "",
    ]
    for g in sorted({g.source_key: g for g in gits}.values(), key=lambda g: g.source_key):
        out.append(f"[source.{_q(g.source_key)}]")
        out.append(f"git = {_q(g.url)}")
        for k in ("rev", "branch", "tag"):
            if k in g.query:
                out.append(f"{k} = {_q(g.query[k])}")
        out.append('replace-with = "cvcpkg-crates"')
        out.append("")
    out += ["[source.cvcpkg-crates]", 'directory = "@CRATES_DIR@"', ""]
    return "\n".join(out)


def existing_checksum(recipe_dir: Path) -> str | None:
    text = (recipe_dir / "recipe.yaml").read_text(encoding="utf-8")
    m = re.search(r'^\s*sha256:\s*"?([0-9a-f]{64})"?', text, re.M)
    return m.group(1) if m else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lockfile", required=True, type=Path)
    ap.add_argument("--set", dest="set_name", required=True)
    ap.add_argument("--set-version", required=True)
    ap.add_argument("--recipes-dir", default="recipes", type=Path)
    ap.add_argument("--crate-sources", type=Path)
    ap.add_argument("--consumer", default="", help="named in each new crate's description")
    args = ap.parse_args(argv)

    crates, gits = read_lockfile(args.lockfile)
    rd: Path = args.recipes_dir
    members: list[str] = []
    written = reused = 0

    missing_git = [g for g in gits if not (rd / g.recipe / "recipe.yaml").is_file()]
    if missing_git:
        for g in missing_git:
            print(
                f"missing: recipes/{g.recipe} for git dependency {g.name} {g.version} "
                f"({g.source_key}#{g.commit})",
                file=sys.stderr,
            )
        return 1
    members += [g.recipe for g in gits]

    for crate in sorted(crates, key=lambda c: (c.name, c.version)):
        cdir = rd / crate.recipe
        members.append(crate.recipe)
        if (cdir / "recipe.yaml").is_file():
            have = existing_checksum(cdir)
            if have != crate.checksum:
                raise SystemExit(
                    f"recipes/{crate.recipe}: sha256 {have}"
                    f" but {args.lockfile} says {crate.checksum}"
                )
            reused += 1
            continue
        license, description = crate_metadata(crate, args.crate_sources)
        cdir.mkdir(parents=True)
        (cdir / "recipe.yaml").write_text(
            crate_recipe_yaml(crate, license, description, args.consumer),
            encoding="utf-8",
            newline="\n",
        )
        (cdir / "build.sh").write_text(crate_build_sh(crate), encoding="utf-8", newline="\n")
        written += 1

    sdir = rd / args.set_name
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "recipe.yaml").write_text(
        set_recipe_yaml(
            args.set_name, args.set_version, args.consumer, sorted(members), args.lockfile.name
        ),
        encoding="utf-8",
        newline="\n",
    )
    (sdir / "build.sh").write_text(set_build_sh(args.set_name), encoding="utf-8", newline="\n")
    (sdir / "Cargo.lock").write_bytes(args.lockfile.read_bytes())
    (sdir / "cargo-config.toml.in").write_text(
        cargo_config(args.set_name, gits), encoding="utf-8", newline="\n"
    )
    print(
        f"{args.set_name}: {len(members)} crates ({written} new recipes, {reused} reused, "
        f"{len(gits)} git)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
