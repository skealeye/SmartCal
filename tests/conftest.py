import os

# Tests never open real windows on the owner's screen: Qt runs offscreen, whatever the
# shell says (a stray QT_QPA_PLATFORM=windows included). Set before any PyQt6 import.
# Opt in to the real display only on purpose: SMARTCAL_TESTS_REAL_DISPLAY=1.
if os.environ.get("SMARTCAL_TESTS_REAL_DISPLAY") != "1":
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

# The dev machine also has PySide6; make pytest-qt use PyQt6 (pytest.ini: qt_api = pyqt6).
os.environ.setdefault("QT_API", "pyqt6")
