import os

# The dev machine also has PySide6; make pytest-qt use PyQt6.
os.environ.setdefault("QT_API", "pyqt6")
