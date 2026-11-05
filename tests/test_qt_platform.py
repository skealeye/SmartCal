import os

from PyQt6.QtWidgets import QApplication


def test_qt_platform_is_offscreen(qapp):
    if os.environ.get("SMARTCAL_TESTS_REAL_DISPLAY") == "1":
        return                                        # explicit opt-in to the real display
    assert QApplication.platformName() == "offscreen"
    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"
