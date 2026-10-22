"""Repository-only test import paths for independently packaged components."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
for relative in ("plugins/tcad_artifact", "plugins/cov_execution"):
    path = str(ROOT / relative)
    if path not in sys.path:
        sys.path.insert(0, path)
