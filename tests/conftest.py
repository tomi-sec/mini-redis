"""Puts src/ on sys.path so every test file can `import resp`, `import store`, etc.

The server code itself has no package/__init__.py structure - modules
import each other flatly and rely on running with src/ as the script
directory (see main.py). Tests aren't run that way, so this is the one
place that bridges the gap.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
