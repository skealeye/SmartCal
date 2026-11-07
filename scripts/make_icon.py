"""Write SmartCal's multi-size .exe icon (appicon.ICON_VARIANT, static glyph).

Usage: python scripts/make_icon.py <out.ico>   (scripts/build.ps1 runs this)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    from PyQt6.QtGui import QGuiApplication
    app = QGuiApplication(argv[:1])  # noqa: F841 - painting needs an application
    from smartcal.ui import appicon
    appicon.write_ico(argv[1])
    print(f"Wrote {argv[1]} ({appicon.ICON_VARIANT})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
