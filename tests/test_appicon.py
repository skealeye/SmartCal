"""v0.12.2: the app icon drawn in the UI's colors (smartcal.ui.appicon)."""
import struct

import pytest
from PyQt6.QtGui import QIcon

from smartcal.ui import appicon, theme, tray

DARK_TASKBAR, LIGHT_TASKBAR = "#1c1c1c", "#eeeeee"
CASES = [(v, d) for v in appicon.VARIANTS for d in ((None, 28) if v == "day" else (None,))]


def opaque_colors(img):
    out = []
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() == 255:
                out.append(c.name())
    return out


def lum_range(colors):
    lums = [theme.luminance(c) for c in set(colors)]
    return min(lums), max(lums)


def ratio(l1, l2):
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("variant,day", CASES)
@pytest.mark.parametrize("size", appicon.SIZES)
def test_every_variant_renders_at_its_exact_size(qapp, variant, day, size):
    img = appicon.render(variant, size, day)
    assert (img.width(), img.height()) == (size, size)
    assert opaque_colors(img) if size <= 64 else not img.isNull()


@pytest.mark.parametrize("variant,day", CASES)
@pytest.mark.parametrize("size", (16, 24))
def test_artwork_fills_the_square(qapp, variant, day, size):
    """At most 1 px of fully transparent margin on any side (the icon looks big)."""
    img = appicon.render(variant, size, day)

    def row_used(y):
        return any(img.pixelColor(x, y).alpha() > 0 for x in range(size))

    def col_used(x):
        return any(img.pixelColor(x, y).alpha() > 0 for y in range(size))

    assert row_used(0) or row_used(1)
    assert row_used(size - 1) or row_used(size - 2)
    assert col_used(0) or col_used(1)
    assert col_used(size - 1) or col_used(size - 2)


@pytest.mark.parametrize("variant,day", CASES)
@pytest.mark.parametrize("size", (16, 24, 32, 256))
def test_corners_are_rounded(qapp, variant, day, size):
    img = appicon.render(variant, size, day)
    for x, y in ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1)):
        assert img.pixelColor(x, y).alpha() < 128
    assert img.pixelColor(size // 2, size - 2).alpha() == 255   # the edge mid-side is solid


@pytest.mark.parametrize("variant,day", CASES)
def test_glyph_contrasts_with_its_tile_at_16px(qapp, variant, day):
    lo, hi = lum_range(opaque_colors(appicon.render(variant, 16, day)))
    assert ratio(lo, hi) >= 4.5


@pytest.mark.parametrize("variant,day", CASES)
@pytest.mark.parametrize("taskbar", (DARK_TASKBAR, LIGHT_TASKBAR))
def test_visible_on_dark_and_light_taskbars(qapp, variant, day, taskbar):
    """Some opaque part of the 16 px icon stands out (>= 3:1) against the taskbar."""
    lo, hi = lum_range(opaque_colors(appicon.render(variant, 16, day)))
    bg = theme.luminance(taskbar)
    assert max(ratio(lo, bg), ratio(hi, bg)) >= 3.0


def test_palette_contrast():
    assert theme.contrast_ratio(appicon.GLYPH, appicon.TILE) >= 7
    assert theme.contrast_ratio(theme.ACCENT, appicon.TILE) >= 4.5
    assert theme.contrast_ratio(theme.ACCENT, DARK_TASKBAR) >= 3


def test_day_variant_shows_the_day(qapp):
    a = appicon.render("day", 16, 6)
    b = appicon.render("day", 16, 28)
    assert a != b


def test_build_icon_holds_every_size(qapp):
    icon = appicon.build_icon(day=15)
    assert not icon.isNull()
    sizes = {s.width() for s in icon.availableSizes()}
    assert set(appicon.SIZES) <= sizes


def test_default_variant_is_a_known_one():
    assert appicon.ICON_VARIANT in appicon.VARIANTS


def test_failed_paint_falls_back_to_the_old_icon(qapp, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("paint failed")
    monkeypatch.setitem(appicon._PAINTERS, appicon.ICON_VARIANT, boom)
    icon = appicon.build_icon(day=3)
    assert not icon.isNull()
    assert {s.width() for s in icon.availableSizes()} == {64}   # the legacy pixmap


def test_unknown_variant_falls_back(qapp):
    assert not appicon.build_icon("nope", day=3).isNull()


def test_never_raises_even_if_the_fallback_fails(qapp, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("paint failed")
    for name in list(appicon._PAINTERS):
        monkeypatch.setitem(appicon._PAINTERS, name, boom)
    assert isinstance(appicon.build_icon(day=3), QIcon)


def test_ico_has_every_size(qapp, tmp_path):
    path = tmp_path / "SmartCal.ico"
    appicon.write_ico(path)
    data = path.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind, count) == (0, 1, len(appicon.ICO_SIZES))
    dims = []
    for i in range(count):
        w, h, _c, _r, planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        assert (planes, bpp) == (1, 32)
        assert data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n"
        assert offset + size <= len(data)
        dims.append(w or 256)
    assert dims == list(appicon.ICO_SIZES)


def test_tray_make_icon_uses_the_factory(qapp):
    icon = tray.make_icon(9)
    assert {16, 20, 24, 32} <= {s.width() for s in icon.availableSizes()}
