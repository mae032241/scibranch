"""Load only the two source bundles; subprocess tests copy and relocate them."""
from pathlib import Path
import sys
_ROOT = Path(__file__).resolve().parents[2]
for _skill in ("curve-analysis", "scientific-paper-evidence"):
    sys.path.insert(0, str(_ROOT / "skills" / _skill / "scripts"))
