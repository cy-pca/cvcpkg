"""Tests for the installed-image layout: discovery, roles, verify, export, CLI.

Regression suite for the prefix-root collision: ``haiku-image`` used to stage
``metadata.yaml`` and ``README-import.md`` at the ROOT of the install prefix,
so a second image package would have clobbered both.  The layout under test
here confines every image to ``share/<package-name>/`` with role-based
filenames, which is what makes N images co-installable AND makes the payload
path derivable from the package name alone.
"""

from __future__ import annotations

import hashlib
import json
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from cvcpkg import images

# ── Fixtures ────────────────────────────────────────────────────


def _descriptor(package: str, *, guest_os: str, version: str, disk_sha: str) -> dict:
    return {
        "schema_version": 1,
        "image": {
            "package": package,
            "version": version,
            "guest_os": guest_os,
            "guest_arch": "x86_64",
            "guest_release": "r1beta5",
            "variant": "builder",
        },
        "disks": [
            {
                "file": "disk.qcow2",
                "format": "qcow2",
                "role": "root",
                "virtual_size_bytes": 53687091200,
                "sha256": disk_sha,
            }
        ],
        "boot": {
            "firmware": "uefi",
            "disk_bus": "virtio-blk",
            "net_model": "virtio-net",
            "console": "none",
            "secureboot": False,
            "cpu_min": 4,
            "memory_min_mib": 4096,
            "disk_min_gib": 50,
        },
        "access": {"ssh_user": "user", "ssh_pubkey_baked": True},
        "importers": {"incus": "incus/metadata.tar.xz", "lxd": "incus/metadata.tar.xz"},
        "writable": False,
        "docs": "README.md",
    }


def make_image(
    prefix: Path,
    package: str = "haiku-image",
    *,
    guest_os: str = "haiku",
    version: str = "1.0.0-beta.5+cvc.1",
    payload: bytes = b"qcow2-payload",
) -> Path:
    """Stage a complete image package under ``<prefix>/share/<package>/``."""
    root = prefix / "share" / package
    (root / "incus").mkdir(parents=True)

    (root / "disk.qcow2").write_bytes(payload)
    (root / "README.md").write_text("import guide\n", encoding="utf-8")
    (root / "incus" / "metadata.yaml").write_text("architecture: x86_64\n", encoding="utf-8")
    (root / "incus" / "metadata.tar.xz").write_bytes(b"tarball")

    disk_sha = hashlib.sha256(payload).hexdigest()
    doc = _descriptor(package, guest_os=guest_os, version=version, disk_sha=disk_sha)
    (root / "image.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    (root / "image.env").write_text(
        textwrap.dedent(
            f"""\
            CVCPKG_IMAGE_NAME={package}
            CVCPKG_IMAGE_DISK=disk.qcow2
            CVCPKG_IMAGE_DISK_BUS=virtio-blk
            """
        ),
        encoding="utf-8",
    )

    lines = [f"{disk_sha}  disk.qcow2"]
    for rel in ("README.md", "image.yaml", "image.env", "incus/metadata.yaml"):
        digest = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        lines.append(f"{digest}  {rel}")
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


@pytest.fixture
def prefix(tmp_path):
    p = tmp_path / "prefix"
    p.mkdir()
    return p


# ── Layout invariants ───────────────────────────────────────────


class TestLayout:
    def test_nothing_lands_at_the_prefix_root(self, prefix):
        """The whole point: an image owns one directory, named after itself."""
        make_image(prefix, "haiku-image")
        assert sorted(p.name for p in prefix.iterdir()) == ["share"]
        assert not (prefix / "metadata.yaml").exists()
        assert not (prefix / "README-import.md").exists()

    def test_two_images_coexist_without_collision(self, prefix):
        """Generic names (README.md, metadata.yaml) used to collide at the root."""
        make_image(prefix, "haiku-image", guest_os="haiku")
        make_image(prefix, "freebsd-image", guest_os="freebsd")
        found = images.discover_images(prefix)
        assert [i.name for i in found] == ["freebsd-image", "haiku-image"]
        # Same role-based filenames, different directories -> no clobber.
        for img in found:
            assert img.role_path("docs").name == "README.md"
            assert img.role_path("docs").parent.name == img.name

    def test_disk_path_is_derivable_from_the_package_name_alone(self, prefix):
        """No version, no guest arch, no upstream naming knowledge in the path."""
        make_image(prefix, "haiku-image")
        img = images.find_image(prefix, "haiku-image")
        assert img.role_path("disk") == prefix / "share" / "haiku-image" / "disk.qcow2"


# ── Discovery ───────────────────────────────────────────────────


class TestDiscovery:
    def test_no_images_installed(self, prefix):
        assert images.discover_images(prefix) == []

    def test_missing_prefix_is_not_an_error(self, tmp_path):
        assert images.discover_images(tmp_path / "nope") == []

    def test_find_image_returns_none_when_not_installed(self, prefix):
        make_image(prefix, "haiku-image")
        assert images.find_image(prefix, "freebsd-image") is None

    def test_unrelated_image_yaml_is_skipped_not_fatal(self, prefix):
        """A package may ship a file called image.yaml that isn't a descriptor."""
        make_image(prefix, "haiku-image")
        other = prefix / "share" / "some-tool"
        other.mkdir(parents=True)
        (other / "image.yaml").write_text("layers:\n  - foo\n", encoding="utf-8")
        assert [i.name for i in images.discover_images(prefix)] == ["haiku-image"]

    def test_unparseable_descriptor_is_skipped_by_ls_but_raises_by_name(self, prefix):
        make_image(prefix, "haiku-image")
        bad = prefix / "share" / "broken-image"
        bad.mkdir(parents=True)
        (bad / "image.yaml").write_text("image: {package: broken-image}\n", encoding="utf-8")
        assert [i.name for i in images.discover_images(prefix)] == ["haiku-image"]
        with pytest.raises(images.ImageError, match="schema_version"):
            images.find_image(prefix, "broken-image")

    def test_package_name_must_match_directory(self, prefix):
        root = make_image(prefix, "haiku-image")
        doc = yaml.safe_load((root / "image.yaml").read_text())
        doc["image"]["package"] = "something-else"
        (root / "image.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
        with pytest.raises(images.ImageError, match="addressing key"):
            images.find_image(prefix, "haiku-image")

    def test_future_schema_version_is_rejected_with_a_useful_message(self, prefix):
        root = make_image(prefix, "haiku-image")
        doc = yaml.safe_load((root / "image.yaml").read_text())
        doc["schema_version"] = 99
        (root / "image.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
        with pytest.raises(images.ImageError, match="upgrade cvcpkg"):
            images.find_image(prefix, "haiku-image")


# ── Roles ───────────────────────────────────────────────────────


class TestRoles:
    def test_every_declared_role_resolves(self, prefix):
        make_image(prefix, "haiku-image")
        img = images.find_image(prefix, "haiku-image")
        assert img.role_path("disk").name == "disk.qcow2"
        assert img.role_path("descriptor").name == "image.yaml"
        assert img.role_path("env").name == "image.env"
        assert img.role_path("checksums").name == "SHA256SUMS"
        assert img.role_path("docs").name == "README.md"
        assert img.role_path("incus-metadata").as_posix().endswith("incus/metadata.tar.xz")
        assert img.role_path("lxd-metadata") == img.role_path("incus-metadata")

    def test_unknown_role_is_none(self, prefix):
        make_image(prefix, "haiku-image")
        assert images.find_image(prefix, "haiku-image").role_path("cdrom") is None

    def test_declared_but_absent_file_is_none(self, prefix):
        root = make_image(prefix, "haiku-image")
        (root / "incus" / "metadata.tar.xz").unlink()
        assert images.find_image(prefix, "haiku-image").role_path("incus-metadata") is None

    def test_root_disk_wins_over_ordering(self, prefix):
        root = make_image(prefix, "haiku-image")
        doc = yaml.safe_load((root / "image.yaml").read_text())
        doc["disks"].insert(0, {"file": "scratch.qcow2", "format": "qcow2", "role": "data"})
        (root / "image.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
        img = images.find_image(prefix, "haiku-image")
        assert img.primary_disk["file"] == "disk.qcow2"

    @pytest.mark.parametrize("evil", ["/etc/passwd", "../../etc/passwd", "a/../../b"])
    def test_path_escape_is_refused(self, prefix, evil):
        assert images.is_safe_relpath(evil) is False
        root = make_image(prefix, "haiku-image")
        doc = yaml.safe_load((root / "image.yaml").read_text())
        doc["disks"][0]["file"] = evil
        (root / "image.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
        assert images.find_image(prefix, "haiku-image").role_path("disk") is None


# ── image.env ───────────────────────────────────────────────────


class TestEnv:
    #: The keys a provisioning script on a node with no jq/yq depends on.
    REQUIRED = {
        "CVCPKG_IMAGE_DISK",
        "CVCPKG_IMAGE_DISK_BUS",
        "CVCPKG_IMAGE_FIRMWARE",
        "CVCPKG_IMAGE_SECUREBOOT",
        "CVCPKG_IMAGE_DISK_MIN_GIB",
        "CVCPKG_IMAGE_CPU_MIN",
        "CVCPKG_IMAGE_MEMORY_MIN_MIB",
        "CVCPKG_IMAGE_SSH_USER",
        "CVCPKG_IMAGE_INCUS_METADATA",
    }

    def test_contract_keys_are_present(self, prefix):
        make_image(prefix, "haiku-image")
        env = images.env_map(images.find_image(prefix, "haiku-image"))
        assert set(env) >= self.REQUIRED
        assert env["CVCPKG_IMAGE_DISK_BUS"] == "virtio-blk"
        assert env["CVCPKG_IMAGE_SECUREBOOT"] == "false"
        assert env["CVCPKG_IMAGE_CPU_MIN"] == "4"

    def test_absolute_vs_relative_paths(self, prefix):
        root = make_image(prefix, "haiku-image")
        img = images.find_image(prefix, "haiku-image")
        assert images.env_map(img)["CVCPKG_IMAGE_DISK"] == str(root / "disk.qcow2")
        assert images.env_map(img, absolute=False)["CVCPKG_IMAGE_DISK"] == "disk.qcow2"

    def test_script_is_evalable_and_quoted(self, prefix):
        make_image(prefix, "haiku-image")
        script = images.env_script(images.find_image(prefix, "haiku-image"))
        for line in script.splitlines():
            key, _, value = line.partition("=")
            assert key.startswith("CVCPKG_IMAGE_")
            assert value  # never assigns an empty string
        assert "\n\n" not in script

    def test_work_dir_is_exported_when_declared(self, prefix):
        """access.work_dir reaches a shell consumer, and is absent otherwise.

        A provisioning script that has to name a build directory inside the
        guest either reads it here or hardcodes one guest's home layout.
        """
        root = make_image(prefix, "haiku-image")
        env = images.env_map(images.find_image(prefix, "haiku-image"))
        assert "CVCPKG_IMAGE_WORK_DIR" not in env

        doc = yaml.safe_load((root / "image.yaml").read_text())
        doc["access"]["work_dir"] = "/boot/home/cvcpkg-build"
        (root / "image.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
        env = images.env_map(images.find_image(prefix, "haiku-image"))
        assert env["CVCPKG_IMAGE_WORK_DIR"] == "/boot/home/cvcpkg-build"

    def test_empty_values_are_omitted(self, prefix):
        root = make_image(prefix, "haiku-image")
        doc = yaml.safe_load((root / "image.yaml").read_text())
        doc.pop("access")
        (root / "image.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
        env = images.env_map(images.find_image(prefix, "haiku-image"))
        assert "CVCPKG_IMAGE_SSH_USER" not in env


# ── Verification ────────────────────────────────────────────────


class TestVerify:
    def test_intact_image_verifies(self, prefix):
        make_image(prefix, "haiku-image")
        rows = images.verify_image(images.find_image(prefix, "haiku-image"))
        assert rows and all(status == "OK" for _, status, _ in rows)

    def test_corrupted_payload_is_caught(self, prefix):
        root = make_image(prefix, "haiku-image")
        (root / "disk.qcow2").write_bytes(b"bit-rot")
        rows = images.verify_image(images.find_image(prefix, "haiku-image"))
        assert ("disk.qcow2", "FAILED") in [(r[0], r[1]) for r in rows]

    def test_deleted_file_is_caught(self, prefix):
        root = make_image(prefix, "haiku-image")
        (root / "README.md").unlink()
        rows = images.verify_image(images.find_image(prefix, "haiku-image"))
        assert ("README.md", "MISSING") in [(r[0], r[1]) for r in rows]

    def test_falls_back_to_descriptor_digests_without_sha256sums(self, prefix):
        root = make_image(prefix, "haiku-image")
        (root / "SHA256SUMS").unlink()
        rows = images.verify_image(images.find_image(prefix, "haiku-image"))
        assert [(r[0], r[1]) for r in rows] == [("disk.qcow2", "OK")]

    def test_nothing_to_verify_raises(self, prefix):
        root = make_image(prefix, "haiku-image")
        (root / "SHA256SUMS").unlink()
        doc = yaml.safe_load((root / "image.yaml").read_text())
        doc["disks"][0].pop("sha256")
        (root / "image.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
        with pytest.raises(images.ImageError, match="nothing to verify"):
            images.verify_image(images.find_image(prefix, "haiku-image"))

    def test_parse_sha256sums_accepts_binary_mode_and_comments(self):
        parsed = images.parse_sha256sums(
            "# a comment\n" + ("a" * 64) + " *disk.qcow2\n\n" + ("b" * 64) + "  README.md\n"
        )
        assert parsed == [("a" * 64, "disk.qcow2"), ("b" * 64, "README.md")]


# ── Export ──────────────────────────────────────────────────────


class TestExport:
    def test_export_restores_a_meaningful_name(self, prefix, tmp_path):
        make_image(prefix, "haiku-image")
        out = images.export_image(images.find_image(prefix, "haiku-image"), tmp_path / "out")
        assert out.name == "haiku-image-1.0.0-beta.5+cvc.1.qcow2"
        assert out.read_bytes() == b"qcow2-payload"

    def test_export_multi_suffix_role(self, prefix, tmp_path):
        make_image(prefix, "haiku-image")
        out = images.export_image(
            images.find_image(prefix, "haiku-image"), tmp_path / "out", role="incus-metadata"
        )
        assert out.name == "haiku-image-1.0.0-beta.5+cvc.1.tar.xz"

    def test_export_missing_role_raises(self, prefix, tmp_path):
        make_image(prefix, "haiku-image")
        with pytest.raises(images.ImageError, match="no 'cdrom' artifact"):
            images.export_image(images.find_image(prefix, "haiku-image"), tmp_path, role="cdrom")


# ── Staged-tree enforcement (pack preflight) ────────────────────


class TestStagedTreeGate:
    def test_good_tree_passes(self, tmp_path):
        install = tmp_path / "install"
        install.mkdir()
        make_image(install, "haiku-image")
        assert images.check_staged_image_tree(install, "haiku-image") == []

    def test_prefix_root_files_are_rejected(self, tmp_path):
        """The exact defect: metadata.yaml/README-import.md at the prefix root."""
        install = tmp_path / "install"
        install.mkdir()
        make_image(install, "haiku-image")
        (install / "metadata.yaml").write_text("architecture: x86_64\n", encoding="utf-8")
        (install / "README-import.md").write_text("docs\n", encoding="utf-8")
        errors = images.check_staged_image_tree(install, "haiku-image")
        assert errors and "outside share/haiku-image/" in errors[0]
        assert "metadata.yaml" in errors[0] and "README-import.md" in errors[0]

    def test_wrong_subdirectory_is_rejected(self, tmp_path):
        install = tmp_path / "install"
        install.mkdir()
        make_image(install, "haiku-image")
        (install / "share" / "libcvc-deps").mkdir(parents=True)
        (install / "share" / "libcvc-deps" / "image.qcow2").write_bytes(b"x")
        errors = images.check_staged_image_tree(install, "haiku-image")
        assert errors and "share/libcvc-deps/image.qcow2" in errors[0]

    def test_missing_descriptor_is_rejected(self, tmp_path):
        install = tmp_path / "install"
        install.mkdir()
        root = make_image(install, "haiku-image")
        (root / "image.yaml").unlink()
        errors = images.check_staged_image_tree(install, "haiku-image")
        assert errors and "no share/haiku-image/image.yaml" in errors[0]

    def test_declared_disk_must_exist(self, tmp_path):
        install = tmp_path / "install"
        install.mkdir()
        root = make_image(install, "haiku-image")
        (root / "disk.qcow2").unlink()
        (root / "SHA256SUMS").unlink()  # keep the failure attributable to the disk
        errors = images.check_staged_image_tree(install, "haiku-image")
        assert any("does not exist" in e for e in errors)

    def test_descriptor_is_schema_validated(self, tmp_path):
        install = tmp_path / "install"
        install.mkdir()
        root = make_image(install, "haiku-image")
        doc = yaml.safe_load((root / "image.yaml").read_text())
        doc["boot"]["disk_bus"] = "floppy"
        (root / "image.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
        errors = images.check_staged_image_tree(install, "haiku-image")
        assert any("disk_bus" in e for e in errors)


# ── CLI ─────────────────────────────────────────────────────────

click_testing = pytest.importorskip("click.testing")
from click.testing import CliRunner  # noqa: E402

from cvcpkg.cli import cli  # noqa: E402


def run(*args):
    return CliRunner().invoke(cli, ["image", *args])


class TestImageCLI:
    def test_ls_table(self, prefix):
        make_image(prefix, "haiku-image")
        make_image(prefix, "freebsd-image", guest_os="freebsd")
        res = run("ls", "--prefix", str(prefix))
        assert res.exit_code == 0, res.output
        assert "NAME" in res.output and "BUS" in res.output
        assert "haiku-image" in res.output and "freebsd-image" in res.output
        assert "virtio-blk" in res.output

    def test_ls_json_and_guest_os_filter(self, prefix):
        make_image(prefix, "haiku-image")
        make_image(prefix, "freebsd-image", guest_os="freebsd")
        res = run("ls", "--prefix", str(prefix), "--json", "--guest-os", "haiku")
        assert res.exit_code == 0, res.output
        data = json.loads(res.output)
        assert [d["name"] for d in data] == ["haiku-image"]
        assert data[0]["disk_bus"] == "virtio-blk"

    def test_ls_empty_prefix_is_not_an_error(self, prefix):
        res = run("ls", "--prefix", str(prefix))
        assert res.exit_code == 0
        assert "no images installed" in res.output

    def test_ls_empty_prefix_json_is_an_empty_array(self, prefix):
        res = run("ls", "--prefix", str(prefix), "--json")
        assert res.exit_code == 0
        assert json.loads(res.output) == []

    def test_path_prints_exactly_one_absolute_path(self, prefix):
        root = make_image(prefix, "haiku-image")
        res = run("path", "haiku-image", "--prefix", str(prefix))
        assert res.exit_code == 0, res.output
        assert res.output.strip() == str(root / "disk.qcow2")
        assert len(res.output.strip().splitlines()) == 1

    def test_path_role_incus_metadata(self, prefix):
        root = make_image(prefix, "haiku-image")
        res = run("path", "haiku-image", "--prefix", str(prefix), "--role", "incus-metadata")
        assert res.exit_code == 0, res.output
        assert res.output.strip() == str(root / "incus" / "metadata.tar.xz")

    def test_path_not_installed_exits_3(self, prefix):
        make_image(prefix, "haiku-image")
        res = run("path", "netbsd-image", "--prefix", str(prefix))
        assert res.exit_code == 3
        assert "not installed" in res.output
        # The message names what IS installed, so the operator can self-correct.
        assert "haiku-image" in res.output

    def test_path_not_installed_in_empty_prefix_exits_3(self, prefix):
        res = run("path", "haiku-image", "--prefix", str(prefix))
        assert res.exit_code == 3
        assert "none installed" in res.output

    def test_path_unknown_role_is_rejected_by_click(self, prefix):
        make_image(prefix, "haiku-image")
        res = run("path", "haiku-image", "--prefix", str(prefix), "--role", "cdrom")
        assert res.exit_code != 0

    def test_path_undeclared_role_exits_4(self, prefix):
        root = make_image(prefix, "haiku-image")
        (root / "incus" / "metadata.tar.xz").unlink()
        res = run("path", "haiku-image", "--prefix", str(prefix), "--role", "incus-metadata")
        assert res.exit_code == 4
        assert "no 'incus-metadata' artifact" in res.output

    def test_dir(self, prefix):
        root = make_image(prefix, "haiku-image")
        res = run("dir", "haiku-image", "--prefix", str(prefix))
        assert res.exit_code == 0
        assert res.output.strip() == str(root)

    def test_info_human_and_json(self, prefix):
        make_image(prefix, "haiku-image")
        res = run("info", "haiku-image", "--prefix", str(prefix))
        assert res.exit_code == 0, res.output
        assert "virtio-blk" in res.output
        assert "backing file" in res.output  # the writable: false warning

        res = run("info", "haiku-image", "--prefix", str(prefix), "--json")
        assert json.loads(res.output)["boot"]["disk_bus"] == "virtio-blk"

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="`image env` emits POSIX-shell-evalable output; shlex.quote wraps "
        "a backslash path in quotes that no Windows shell would strip",
    )
    def test_env_is_evalable(self, prefix):
        root = make_image(prefix, "haiku-image")
        res = run("env", "haiku-image", "--prefix", str(prefix))
        assert res.exit_code == 0, res.output
        env = dict(line.split("=", 1) for line in res.output.strip().splitlines())
        assert env["CVCPKG_IMAGE_DISK"] == str(root / "disk.qcow2")
        assert env["CVCPKG_IMAGE_CPU_MIN"] == "4"

    def test_env_relative(self, prefix):
        make_image(prefix, "haiku-image")
        res = run("env", "haiku-image", "--prefix", str(prefix), "--relative")
        assert "CVCPKG_IMAGE_DISK=disk.qcow2" in res.output

    def test_verify_ok(self, prefix):
        make_image(prefix, "haiku-image")
        res = run("verify", "haiku-image", "--prefix", str(prefix))
        assert res.exit_code == 0, res.output
        assert "verified" in res.output

    def test_verify_detects_rot_and_exits_5(self, prefix):
        root = make_image(prefix, "haiku-image")
        (root / "disk.qcow2").write_bytes(b"bit-rot")
        res = run("verify", "haiku-image", "--prefix", str(prefix))
        assert res.exit_code == 5
        assert "FAILED" in res.output

    def test_verify_not_installed_exits_3(self, prefix):
        res = run("verify", "haiku-image", "--prefix", str(prefix))
        assert res.exit_code == 3

    def test_export(self, prefix, tmp_path):
        make_image(prefix, "haiku-image")
        dest = tmp_path / "out"
        res = run("export", "haiku-image", "--prefix", str(prefix), "--to", str(dest))
        assert res.exit_code == 0, res.output
        out = Path(res.output.strip())
        assert out.parent == dest
        assert out.name == "haiku-image-1.0.0-beta.5+cvc.1.qcow2"
        assert out.read_bytes() == b"qcow2-payload"

    def test_export_not_installed_exits_3(self, prefix, tmp_path):
        res = run("export", "haiku-image", "--prefix", str(prefix), "--to", str(tmp_path / "o"))
        assert res.exit_code == 3

    def test_prefix_comes_from_cvcpkg_prefix_env(self, prefix, monkeypatch):
        """The answer to 'the script runs where the prefix is not ./deps'."""
        root = make_image(prefix, "haiku-image")
        monkeypatch.setenv("CVCPKG_PREFIX", str(prefix))
        res = CliRunner().invoke(cli, ["image", "path", "haiku-image"])
        assert res.exit_code == 0, res.output
        assert res.output.strip() == str(root / "disk.qcow2")


# ── The shipped recipe honours the layout ───────────────────────

REPO = Path(__file__).resolve().parents[2]
HAIKU_RECIPE = REPO / "recipes" / "haiku-image"


@pytest.mark.skipif(not HAIKU_RECIPE.is_dir(), reason="haiku-image recipe not in this tree")
class TestHaikuImageRecipe:
    def test_declares_kind_image(self):
        doc = yaml.safe_load((HAIKU_RECIPE / "recipe.yaml").read_text())
        assert doc["recipe"]["kind"] == "image"

    def test_package_files_are_all_under_share_haiku_image(self):
        doc = yaml.safe_load((HAIKU_RECIPE / "recipe.yaml").read_text())
        files = doc["package"]["files"]
        assert files, "package.files must not be empty"
        assert all(f.startswith("share/haiku-image/") for f in files), files
        assert "share/haiku-image/image.yaml" in files
        assert "share/haiku-image/disk.qcow2" in files

    def test_build_script_stages_into_the_image_dir_only(self):
        """The staging section must not copy to CVC_INSTALL_DIR's root again."""
        script = (HAIKU_RECIPE / "build.sh").read_text()
        assert 'IMGDIR="${CVC_INSTALL_DIR}/share/${PKG_NAME}"' in script
        for stale in (
            '"${CVC_INSTALL_DIR}/metadata.yaml"',
            '"${CVC_INSTALL_DIR}/README-import.md"',
            '"${CVC_INSTALL_DIR}/haiku-builder.qcow2"',
        ):
            assert stale not in script, f"build.sh still stages {stale} at the prefix root"

    def test_recipe_validates(self):
        from cvcpkg import validation

        assert validation.validate_recipe_dir(HAIKU_RECIPE) == []


# ── `cvcpkg validate` enforces the layout for kind: image ───────


def _write_recipe(tmp_path: Path, *, kind: str, files: list[str]) -> Path:
    recipe_dir = tmp_path / "widget-image"
    recipe_dir.mkdir()
    (recipe_dir / "build.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    doc = {
        "schema_version": 1,
        "recipe": {
            "name": "widget-image",
            "upstream_version": "1.0.0",
            "cvc_revision": 1,
            **({"kind": kind} if kind else {}),
        },
        "source": {"type": "prebuilt", "url": "https://example.invalid/x"},
        "build": {"matrix": [{"platform": "linux", "script": "build.sh"}]},
        "package": {"files": files},
    }
    (recipe_dir / "recipe.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return recipe_dir


class TestValidateImageKind:
    def test_kind_image_is_accepted_by_the_schema(self, tmp_path):
        recipe = _write_recipe(
            tmp_path,
            kind="image",
            files=["share/widget-image/image.yaml", "share/widget-image/disk.qcow2"],
        )
        from cvcpkg import validation

        assert validation.validate_recipe_dir(recipe) == []

    def test_prefix_root_files_are_rejected(self, tmp_path):
        recipe = _write_recipe(
            tmp_path, kind="image", files=["disk.qcow2", "metadata.yaml", "README-import.md"]
        )
        from cvcpkg import validation

        errors = validation.validate_recipe_dir(recipe)
        assert any("share/widget-image/" in e and "package.files" in e for e in errors)

    def test_missing_descriptor_is_rejected(self, tmp_path):
        recipe = _write_recipe(tmp_path, kind="image", files=["share/widget-image/disk.qcow2"])
        from cvcpkg import validation

        errors = validation.validate_recipe_dir(recipe)
        assert any("image.yaml" in e for e in errors)

    def test_non_image_recipes_are_unaffected(self, tmp_path):
        recipe = _write_recipe(tmp_path, kind="", files=["lib/libwidget.so", "include/widget.h"])
        from cvcpkg import validation

        assert validation.validate_recipe_dir(recipe) == []
