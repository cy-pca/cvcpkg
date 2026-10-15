#!/usr/bin/env python3
"""Regenerate recipes/msvc/msvc.lock from Microsoft's Visual Studio manifests.

Visual Studio installs from a channel manifest (``https://aka.ms/vs/17/release/
channel`` for VS 2022) that points at a package manifest, ``VisualStudio.vsman``:
every workload, component and payload, each payload with its download URL and
sha256.  The MSVC compiler, its CRT and STL ship as VSIX packages (zip files
whose ``Contents/`` is the install tree); the Windows SDK ships as MSI
installers plus the CAB files they reference.  The msvc recipe's build.ps1
downloads exactly the payloads listed in msvc.lock and refuses any whose
sha256 differs.  PowerShell could read the vsman directly, but a lock keeps the
recipe pinned: the channel URL always serves the newest VS 2022 servicing
release, while the payload URLs in a given vsman stay valid.

Nothing here downloads the compiler.  The script reads the two manifests, the
SDK's MSI files (to learn which CABs each needs; a few MB) and two small VSIX
packages (to learn the MSVC and redist directory names), and verifies each
against the sha256 the manifest gives.

Bumping the toolset (see recipes/msvc/recipe.yaml):

    python scripts/msvc_lock.py --toolset 14.44 --sdk 26100

then set ``recipe.upstream_version`` to the ``toolset`` directory version the
lock records and reset ``cvc_revision`` to 1.

Usage:
    python scripts/msvc_lock.py [--channel URL] [--toolset X.Y] [--sdk BUILD]
                                [--output FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

CHANNEL_URL = "https://aka.ms/vs/17/release/channel"
SDK_LICENSE_URL = "https://aka.ms/WinSDKLicenseURL"
LANGUAGE = "en-US"

# The x64-hosted, x64-targeting compiler and what it needs to build and link a
# desktop C/C++ program: cl/link/lib and their en-US resources (cl.exe will not
# start without 1033\clui.dll), the CRT/STL headers, the libraries, the
# redistributable CRT DLLs (what CMake's InstallRequiredSystemLibraries
# copies), the code-analysis engine and the PGO headers/runtime.  The
# libraries are split across two packages that share no file: "Desktop" has
# the static CRT/STL (libcmt, libcpmt, /MT) and libomp.lib (-openmp:llvm);
# "Store", despite its name, has the /MD import libraries every DLL-CRT link
# needs (msvcrt, msvcprt, vcruntime, oldnames, legacy_stdio_definitions,
# delayimp, vcomp).  Deliberately left out: ATL/MFC, ASan (64 MB), the
# Spectre-mitigated libraries, ARM64 and x86 targets.  Each name is a suffix of
# ``Microsoft.VC.<toolset>.<vs-version>.``; add one here and regenerate.
MSVC_PACKAGES: tuple[str, ...] = (
    "Tools.HostX64.TargetX64.base",
    "Tools.HostX64.TargetX64.Res.base",
    "CRT.Headers.base",
    "CRT.x64.Desktop.base",
    "CRT.x64.Store.base",
    "CRT.Redist.X64.base",
    "Premium.Tools.HostX64.TargetX64.base",
    "PGO.Headers.base",
    "PGO.X64.base",
)

# The Windows SDK installers (``Installers\\<name>`` payloads of the
# Win11SDK_10.0.<build> package) that give a desktop x64 build its headers
# (um, shared, winrt, cppwinrt, ucrt), import libraries (um, ucrt) and the
# rc.exe / mt.exe tools CMake requires.  The x86 desktop headers are included
# because several shared headers are only installed by it.
SDK_MSIS: tuple[str, ...] = (
    "Universal CRT Headers Libraries and Sources-x86_en-us.msi",
    "Windows SDK Desktop Headers x64-x86_en-us.msi",
    "Windows SDK Desktop Headers x86-x86_en-us.msi",
    "Windows SDK Desktop Libs x64-x86_en-us.msi",
    "Windows SDK OnecoreUap Headers x64-x86_en-us.msi",
    "Windows SDK for Windows Store Apps Headers OnecoreUap-x86_en-us.msi",
    "Windows SDK for Windows Store Apps Headers-x86_en-us.msi",
    "Windows SDK for Windows Store Apps Libs-x86_en-us.msi",
    "Windows SDK for Windows Store Apps Tools-x86_en-us.msi",
)

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "recipes" / "msvc" / "msvc.lock"

Fetch = Callable[[str], bytes]


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:  # noqa: S310 -- Microsoft HTTPS URLs
        return resp.read()


def _verified(fetch: Fetch, url: str, sha256: str) -> bytes:
    data = fetch(url)
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256.lower():
        raise SystemExit(f"{url}: sha256 {actual}, manifest says {sha256.lower()}")
    return data


def msi_cabs(msi: bytes) -> list[str]:
    """The external cabinet files an SDK MSI's Media table names.

    The SDK's cabinets are named by a 32-digit hex hash; reading the names out
    of the compound file's string pool is enough (and needs no MSI library),
    because nothing else in these installers ends in ``.cab``.
    """
    names = {m.group(0).decode("ascii") for m in re.finditer(rb"[0-9a-fA-F]{32}\.cab", msi)}
    return sorted(names)


def vsix_dirs(vsix: bytes, parent: str) -> set[str]:
    """Names of the directories directly under ``Contents/<parent>/`` in a VSIX."""
    prefix = f"Contents/{parent}/"
    with zipfile.ZipFile(io.BytesIO(vsix)) as z:
        return {n[len(prefix) :].split("/", 1)[0] for n in z.namelist() if n.startswith(prefix)}


def _payload_line(kind: str, payload: dict, name: str) -> str:
    return f"{kind} {payload['sha256'].lower()} {payload['size']} {payload['url']} {name}"


def render(
    toolset: str,
    sdk: str,
    channel: bytes,
    fetch: Fetch = _fetch,
    channel_url: str = CHANNEL_URL,
) -> str:
    """Return the lock text for MSVC *toolset* (e.g. ``14.44``) and Windows SDK
    *sdk* (e.g. ``26100``) from the raw *channel* manifest bytes, read from
    *channel_url*."""
    ch = json.loads(channel)
    info = ch.get("info", {})
    items = {it["id"]: it for it in ch.get("channelItems", [])}
    vsman_item = items.get("Microsoft.VisualStudio.Manifests.VisualStudio")
    if not vsman_item or len(vsman_item.get("payloads", [])) != 1:
        raise SystemExit("channel manifest has no VisualStudio.vsman payload")
    vsman_payload = vsman_item["payloads"][0]
    # The channel's sha256 and size for VisualStudio.vsman describe the file as
    # signed (17.14: 30,443,537 bytes), not the re-serialized 17,954,732 bytes
    # the CDN serves, so they cannot be checked; the VS installer verifies the
    # embedded signature instead.  Record what was actually read so a reviewer
    # can re-derive the lock from it.  Every payload line below is pinned by its
    # own sha256, which does match what the CDN serves.
    vsman_raw = fetch(vsman_payload["url"])
    vsman = json.loads(vsman_raw)

    license_url = ""
    for res in items.get("Microsoft.VisualStudio.Product.BuildTools", {}).get(
        "localizedResources", []
    ):
        if res.get("language", "").lower() == LANGUAGE.lower():
            license_url = res.get("license", "")
    if not license_url:
        raise SystemExit("channel manifest has no en-US Build Tools license URL")

    packages: dict[tuple[str, str | None], dict] = {}
    for p in vsman["packages"]:
        packages[(p["id"], p.get("language"))] = p

    # Microsoft.VC.<toolset>.<vs-version>.Tools...: the vs-version part (17.14)
    # is not in the user's terms, so find it from the compiler package.
    pat = re.compile(
        rf"^Microsoft\.VC\.({re.escape(toolset)}\.\d+\.\d+)\.Tools\.HostX64\.TargetX64\.base$"
    )
    stems = sorted({m.group(1) for pid, _ in packages if (m := pat.match(pid))})
    if len(stems) != 1:
        raise SystemExit(f"MSVC {toolset}: expected one compiler package, found {stems or 'none'}")
    stem = f"Microsoft.VC.{stems[0]}"

    lines_vsix: list[str] = []
    vsix_data: dict[str, bytes] = {}
    cl_version = ""
    for suffix in MSVC_PACKAGES:
        pid = f"{stem}.{suffix}"
        pkg = packages.get((pid, None)) or packages.get((pid, LANGUAGE))
        if pkg is None:
            raise SystemExit(f"{pid}: not in the manifest")
        if len(pkg.get("payloads", [])) != 1:
            raise SystemExit(f"{pid}: expected one payload, found {len(pkg.get('payloads', []))}")
        if suffix == "Tools.HostX64.TargetX64.base":
            cl_version = pkg["version"]
        if suffix in ("CRT.Headers.base", "CRT.Redist.X64.base"):
            pl = pkg["payloads"][0]
            vsix_data[suffix] = _verified(fetch, pl["url"], pl["sha256"])
        lines_vsix.append(_payload_line("vsix", pkg["payloads"][0], pid))

    toolset_dirs = vsix_dirs(vsix_data["CRT.Headers.base"], "VC/Tools/MSVC")
    redist_dirs = vsix_dirs(vsix_data["CRT.Redist.X64.base"], "VC/Redist/MSVC")
    if len(toolset_dirs) != 1 or len(redist_dirs) != 1:
        raise SystemExit(f"unexpected VSIX layout: {toolset_dirs} / {redist_dirs}")
    toolset_dir, redist_dir = toolset_dirs.pop(), redist_dirs.pop()

    sdk_pkg = packages.get((f"Win11SDK_10.0.{sdk}", None))
    if sdk_pkg is None:
        raise SystemExit(f"Win11SDK_10.0.{sdk}: not in the manifest")
    sdk_payloads = {pl["fileName"]: pl for pl in sdk_pkg["payloads"]}
    lines_msi: list[str] = []
    cabs: set[str] = set()
    for msi in SDK_MSIS:
        pl = sdk_payloads.get(f"Installers\\{msi}")
        if pl is None:
            raise SystemExit(f"{msi}: not in Win11SDK_10.0.{sdk}")
        cabs.update(msi_cabs(_verified(fetch, pl["url"], pl["sha256"])))
        lines_msi.append(_payload_line("msi", pl, msi))
    lines_cab: list[str] = []
    for cab in sorted(cabs):
        pl = sdk_payloads.get(f"Installers\\{cab}")
        if pl is None:
            raise SystemExit(f"{cab}: referenced by an SDK msi but not a payload")
        lines_cab.append(_payload_line("cab", pl, cab))

    sdk_dir = f"10.0.{sdk}.0"
    display_version = info.get("productDisplayVersion", "?").split()[0]
    lines = [
        "# Generated by scripts/msvc_lock.py -- do not edit by hand.",
        f"# Channel: {channel_url}",
        f"#   {info.get('manifestName', '?')} {info.get('productDisplayVersion', '?')},"
        f" build {info.get('buildVersion', '?')}",
        f"# Manifest: {vsman_payload['url']}",
        f"# Manifest sha256 (as served): {hashlib.sha256(vsman_raw).hexdigest()}",
        "# Data lines:",
        "#   release <VS display version> <VS build>",
        "#   license <url>                   (one line per license to accept)",
        "#   toolset <VC\\Tools\\MSVC dir> <cl.exe package version>",
        "#   redist <VC\\Redist\\MSVC dir>",
        "#   sdk <Windows Kits\\10 Include/Lib dir> <SDK package version>",
        "#   <kind> <sha256> <size> <url> <name>",
        "# kind vsix: a VSIX (zip); its Contents/ is unpacked into <prefix>\\msvc.",
        "# kind msi:  a Windows SDK installer; build.ps1 runs `msiexec /a` on it.",
        "# kind cab:  a cabinet one of the msi files references; it sits beside them.",
        f"release {display_version} {info.get('buildVersion', '?')}",
        f"license {license_url}",
        f"license {SDK_LICENSE_URL}",
        f"toolset {toolset_dir} {cl_version}",
        f"redist {redist_dir}",
        f"sdk {sdk_dir} {sdk_pkg['version']}",
        *lines_vsix,
        *lines_msi,
        *lines_cab,
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--channel", default=CHANNEL_URL, help="VS channel manifest URL or file")
    ap.add_argument("--toolset", default="14.44", help="MSVC toolset, e.g. 14.44")
    ap.add_argument("--sdk", default="26100", help="Windows 11 SDK build, e.g. 26100")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args(argv)

    chan = Path(args.channel)
    raw = chan.read_bytes() if chan.is_file() else _fetch(args.channel)
    args.output.write_text(
        render(args.toolset, args.sdk, raw, channel_url=args.channel), encoding="utf-8"
    )
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
