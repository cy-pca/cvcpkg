# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""cvcpkg-server — HTTP daemon for package publishing and serving.

Provides an authenticated REST API that:

- Serves bundle archives and catalog YAML to ``cvcpkg install`` clients
- Accepts authenticated package publishes from ``cvcpkg push``
- Maintains a tamper-evident audit log of every mutation
- Delegates storage to any cvcpkg storage backend (file://, s3://, etc.)

Quick start::

    # Generate a token for publishers
    cvcpkg-server token create --name ci-publisher --role publisher

    # Start the server
    cvcpkg-server run --storage file:///var/lib/cvcpkg --port 8420

    # Publish from CI
    cvcpkg push dist/*.tar.zst --dest http://pkgserver:8420/v1/publish \\
        --token $CVCPKG_TOKEN

    # Clients install normally
    cvcpkg install --catalog http://pkgserver:8420/v1/catalog
"""
