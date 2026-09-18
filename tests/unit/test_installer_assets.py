"""Guard the bundled installer scripts against the pre-relocation repo (issue #25).

The `curl | sh` / `irm | iex` installers fetch the standalone binary from a
GitHub repo baked into the script.  After the move to ``cy-pca/cvcpkg`` the old
``transfix/libcvc-deps`` must not reappear, or the one-liner install silently
resolves "latest" against a frozen personal repo.
"""

from __future__ import annotations

from importlib.resources import files as _res_files

OLD_REPO = "transfix/libcvc-deps"
NEW_REPO = "cy-pca/cvcpkg"


def _asset(name: str) -> str:
    return _res_files("cvcpkg.server").joinpath("assets", name).read_text(encoding="utf-8")


def test_install_sh_targets_cy_pca():
    text = _asset("install.sh")
    assert OLD_REPO not in text, "install.sh still references the old release repo"
    assert f'REPO="${{CVCPKG_REPO:-{NEW_REPO}}}"' in text


def test_install_ps1_targets_cy_pca():
    text = _asset("install.ps1")
    assert OLD_REPO not in text, "install.ps1 still references the old release repo"
    assert f'else {{ "{NEW_REPO}" }}' in text


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
