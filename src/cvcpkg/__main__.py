# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Allow ``python -m cvcpkg`` (and the single-file binary's client entry)."""

import os
import sys

from cvcpkg.selfexec import restore_library_path

restore_library_path(os.environ)

from cvcpkg.cli import main  # noqa: E402

sys.exit(main())
