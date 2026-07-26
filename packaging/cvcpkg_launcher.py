# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Multi-call entry for the *combined* single binary.

One executable serves both tools, busybox-style: it dispatches to the client CLI
(``cvcpkg``) or the server (``cvcpkg-server``) by the name it was invoked as. Ship
the binary plus a ``cvcpkg-server`` symlink (or copy on Windows); where symlinks
are unavailable, set ``CVCPKG_ENTRY=server``.

Only used for the server-bundled build (CVCPKG_BUNDLE_SERVER); the lean client
build uses cvcpkg/__main__.py directly.
"""

import os
import sys


def _want_server() -> bool:
    entry = os.environ.get("CVCPKG_ENTRY", "").strip().lower()
    if entry in ("server", "cvcpkg-server"):
        return True
    if entry in ("client", "cli", "cvcpkg"):
        return False
    # Default: dispatch on the invoked program name.
    return "server" in os.path.basename(sys.argv[0]).lower()


def main() -> int:
    if _want_server():
        from cvcpkg.server.cli import server_cli

        return server_cli()  # click group; exits via SystemExit
    from cvcpkg.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
