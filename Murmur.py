"""Bundle entry point.

py2app names the bundle's executable after this script, which is why it is
called ``Murmur.py`` and lives at the repo root rather than inside the package.
It does nothing but hand off to the normal CLI entry point.
"""

import sys
from pathlib import Path

# In an alias build the package lives in this checkout, not inside the bundle.
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from murmur.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["run"]))
