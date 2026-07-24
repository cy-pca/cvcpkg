# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Allow ``python -m cvcpkg``."""

import sys

from cvcpkg.cli import main

sys.exit(main())
