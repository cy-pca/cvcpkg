"""Allow ``python -m cvcpkg``."""

import sys

from cvcpkg.cli import main

sys.exit(main())
