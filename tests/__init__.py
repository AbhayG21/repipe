"""Test package bootstrap: put the src/ package on the import path.

Runs once when `unittest discover` imports the `tests` package, so every test
module can `from repipe import …` without a PYTHONPATH dance.
"""

import os
import sys

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
