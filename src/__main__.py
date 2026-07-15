"""zipapp entry point.

`python3 -m zipapp src -o repipe` archives this directory; this file becomes
the archive's __main__. It keeps `repipe` (the package) intact so the modules
use normal package-relative imports, and it propagates the CLI's exit code.
"""

import sys

from repipe.cli import main

if __name__ == "__main__":
    sys.exit(main())
