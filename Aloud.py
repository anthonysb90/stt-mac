"""Bundle entry point.

py2app names the bundle's executable after this script, which is why it is
called ``Aloud.py`` and lives at the repo root rather than inside the package.

It forwards its arguments, which makes the built bundle testable from a
terminal without launching the UI::

    /Applications/Aloud.app/Contents/MacOS/Aloud --version   # does it start?
    /Applications/Aloud.app/Contents/MacOS/Aloud             # run it, with a
                                                             # visible traceback

That first line is what `scripts/build_app.sh` uses to catch a broken bundle at
build time, rather than letting you find out from a "Launch error" dialog with
the traceback thrown away.
"""

import sys
from pathlib import Path

# In an alias build the package lives in this checkout, not inside the bundle.
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from aloud.cli import main  # noqa: E402


def _arguments() -> list:
    """Our own arguments, minus anything the Finder added.

    Launch Services historically appended a `-psn_0_12345` process serial
    number when an app was opened from the Finder. It does not on current
    macOS, but the cost of tolerating it is one line and the cost of not
    tolerating it is an argparse error at launch.
    """
    given = [arg for arg in sys.argv[1:] if not arg.startswith("-psn_")]
    return given or ["run"]


if __name__ == "__main__":
    sys.exit(main(_arguments()))
