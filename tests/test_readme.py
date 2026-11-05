import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def test_readme_has_no_control_characters():
    files = [ROOT / "README.md", *sorted((ROOT / "scripts").glob("*.ps1"))]
    for f in files:
        m = BAD.search(f.read_text(encoding="utf-8"))
        assert m is None, f"{f.name}: control char {m.group()!r} at offset {m.start()}"
