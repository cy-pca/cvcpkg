"""Guard the bundled installer scripts' baked-in defaults.

The `curl | sh` / `irm | iex` installers install the registry's own
``cvcpkg`` package and fall back to the standalone binary on a GitHub repo's
releases.  After the move to ``cy-pca/cvcpkg`` the old ``transfix/libcvc-deps``
must not reappear (issue #25), or the fallback silently resolves "latest"
against a frozen personal repo.  And the copy a deployment serves must point at
that deployment, not at the official one.
"""

from __future__ import annotations

from importlib.resources import files as _res_files

import pytest

from cvcpkg.server import landing

OLD_REPO = "transfix/libcvc-deps"
NEW_REPO = "cy-pca/cvcpkg"


def _asset(name: str) -> str:
    return _res_files("cvcpkg.server").joinpath("assets", name).read_text(encoding="utf-8")


def test_install_sh_targets_cy_pca():
    text = _asset("install.sh")
    assert OLD_REPO not in text, "install.sh still references the old release repo"
    assert f'DEFAULT_GITHUB_REPO="{NEW_REPO}"' in text
    assert 'REPO="${CVCPKG_REPO:-$DEFAULT_GITHUB_REPO}"' in text


def test_install_ps1_targets_cy_pca():
    text = _asset("install.ps1")
    assert OLD_REPO not in text, "install.ps1 still references the old release repo"
    assert f'$DefaultGitHubRepo = "{NEW_REPO}"' in text
    assert "else { $DefaultGitHubRepo }" in text


def test_install_sh_supports_install_dir_flag():
    # First-class install-location flags served by the shell installer (issue #17).
    text = _asset("install.sh")
    assert "--install-dir" in text, "install.sh missing the --install-dir flag"
    assert "while [ $# -gt 0 ]" in text, "install.sh missing the argument-parse loop"


def test_install_ps1_supports_install_dir_param():
    # First-class install-location flags served by the PowerShell installer (issue #17).
    text = _asset("install.ps1")
    assert "param(" in text, "install.ps1 missing a param() block"
    assert "$InstallDir" in text, "install.ps1 missing the $InstallDir parameter"


def test_github_repo_defaults_are_cy_pca():
    # The server's "GitHub" link default (landing) and RSS channel link (app)
    # must default to the new repo when CVCPKG_GITHUB_REPO is unset.
    landing_src = _res_files("cvcpkg.server").joinpath("landing.py").read_text(encoding="utf-8")
    assert f'"CVCPKG_GITHUB_REPO", "{OLD_REPO}"' not in landing_src
    assert f'"CVCPKG_GITHUB_REPO", "{NEW_REPO}"' in landing_src
    app_src = _res_files("cvcpkg.server").joinpath("app.py").read_text(encoding="utf-8")
    assert f"'CVCPKG_GITHUB_REPO', '{OLD_REPO}'" not in app_src
    assert f"'CVCPKG_GITHUB_REPO', '{NEW_REPO}'" in app_src


# ── Served copies carry the serving deployment's defaults ───────
#
# The installers install the registry's own `cvcpkg` package, falling back to
# a GitHub repo's releases.  A fork's deployment must hand out an installer
# that points at the FORK, so the server bakes its CVCPKG_SITE_URL and
# CVCPKG_GITHUB_REPO into the copy it serves.


@pytest.fixture()
def deployment(monkeypatch):
    def configure(site_url: str, repo: str) -> None:
        monkeypatch.setattr(landing, "_SITE_URL", site_url)
        monkeypatch.setattr(landing, "_GITHUB_REPO", repo)
        landing.install_script_asset.cache_clear()

    yield configure
    landing.install_script_asset.cache_clear()


@pytest.mark.parametrize(
    ("name", "url_line", "repo_line"),
    [
        ("install.sh", 'DEFAULT_SERVER_URL="{}"', 'DEFAULT_GITHUB_REPO="{}"'),
        ("install.ps1", '$DefaultServerUrl = "{}"', '$DefaultGitHubRepo = "{}"'),
    ],
)
def test_served_installer_points_at_its_own_deployment(deployment, name, url_line, repo_line):
    raw = _asset(name)
    # Exactly one assignment each, or the substitution could miss or double up.
    assert raw.count(url_line.format("https://cvcpkg.org")) == 1
    assert raw.count(repo_line.format(NEW_REPO)) == 1

    deployment("https://pkgs.example.org", "example/cvcpkg-fork")
    served = landing.install_script_asset(name)
    assert url_line.format("https://pkgs.example.org") in served
    assert repo_line.format("example/cvcpkg-fork") in served
    assert url_line.format("https://cvcpkg.org") not in served
    assert repo_line.format(NEW_REPO) not in served


@pytest.mark.parametrize("name", ["install.sh", "install.ps1"])
def test_unsafe_deployment_values_keep_the_official_defaults(deployment, name):
    # Both values are pasted inside a double-quoted shell string.
    deployment('https://evil.example"; rm -rf ~; echo "', "a/b$(id)")
    assert landing.install_script_asset(name) == _asset(name)


def test_installers_ask_the_registry_first():
    # The lookup both scripts make; the server side is pinned in
    # test_install_from_registry.py.
    assert '"$SERVER_URL/v1/packages/cvcpkg/latest"' in _asset("install.sh")
    assert '"$Server/v1/packages/cvcpkg/latest?$Query"' in _asset("install.ps1")
