"""v0.12.2: SmartCal's icon (tray, windows, the .exe), drawn in the UI's colors.

Windows draws a tray icon at a fixed size (16 px at 100%, 20 at 125%, 24 at 150%, 32
at 200%), so the icon looks bigger only when the artwork fills its square. Every size
is painted at its exact pixel size (no scaling of one bitmap), edges snapped to whole
pixels so it stays sharp at 16 px.

Variants (ICON_VARIANT picks the one in use; switching is that one line):
  "ring"  - dark tile, ACCENT ring, light calendar page with an ACCENT top band
  "solid" - solid ACCENT tile, dark calendar page outline with a dark top band and dots.
            The default (owner's pick): the strongest silhouette on a dark taskbar.
  "day"   - dark card with an ACCENT edge and top band, today's day number (a dot
            grid when there is no day, e.g. the .exe icon). Legible at 16 px.
  "legacy"- the pre-0.12.2 icon (white page, red band, day number): also the fallback

Nothing here raises: a failed paint falls back to the legacy icon, then to an empty
QIcon, so startup never stops over the icon.
"""
from __future__ import annotations

import io
import logging
import struct

from PyQt6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPixmap

from . import theme

log = logging.getLogger(__name__)

ICON_VARIANT = "solid"
VARIANTS = ("ring", "solid", "day")
SIZES = (16, 20, 24, 32, 40, 48, 64, 256)
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 256)
TILE = theme.BG            # the popup's surface
GLYPH = theme.TEXT
RADIUS_RATIO = 0.22        # corner radius / size: the cards' generous rounding


def _radius(s: int) -> float:
    return max(2.5, s * RADIUS_RATIO)


def _tile_path(s: int, inset: float = 0.0) -> QPainterPath:
    path = QPainterPath()
    r = max(0.5, _radius(s) - inset)
    path.addRoundedRect(QRectF(inset, inset, s - 2 * inset, s - 2 * inset), r, r)
    return path


def _new_image(s: int) -> QImage:
    img = QImage(s, s, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    return img


def _painter(img: QImage) -> QPainter:
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    return p


def _page_box(s: int, pad_ratio: float) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the calendar page, whole pixels, centered."""
    pad = max(2, round(s * pad_ratio))
    return pad, pad, s - 2 * pad, s - 2 * pad


def _dots(p: QPainter, x: int, y: int, w: int, h: int, color: str, s: int) -> None:
    """A grid of square-ish dots (the calendar's days) centered in (x, y, w, h): 2x2
    when it fits with clear space around every dot, else 2x1 or one dot, so small
    sizes never merge into a blob."""
    d = max(2, round(s * 0.11))
    gap = max(2, round(s * 0.08))
    margin = max(1, round(s * 0.06))
    cols = 2 if 2 * d + gap + 2 * margin <= w else 1
    rows = 2 if 2 * d + gap + 2 * margin <= h else 1
    tw, th = cols * d + (cols - 1) * gap, rows * d + (rows - 1) * gap
    ox = x + (w - tw) // 2
    oy = y + (h - th + 1) // 2
    p.setBrush(QColor(color))
    rr = 0 if d < 4 else d * 0.25
    for i in range(cols):
        for j in range(rows):
            p.drawRoundedRect(QRectF(ox + i * (d + gap), oy + j * (d + gap), d, d), rr, rr)


def _paint_ring(s: int, day: int | None) -> QImage:
    img = _new_image(s)
    p = _painter(img)
    p.setBrush(QColor(theme.ACCENT))
    p.drawPath(_tile_path(s))
    ring = max(1, round(s / 16))
    p.setBrush(QColor(TILE))
    p.drawPath(_tile_path(s, ring))
    x, y, w, h = _page_box(s, 0.22)
    pr = max(1.0, s * 0.08)
    p.setBrush(QColor(GLYPH))
    p.drawRoundedRect(QRectF(x, y, w, h), pr, pr)
    band = max(3, round(h * 0.34))
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(x, y, w, h), pr, pr)
    p.setClipPath(clip)
    p.setBrush(QColor(theme.ACCENT))
    p.drawRect(QRectF(x, y, w, band))
    p.setClipping(False)
    _dots(p, x, y + band, w, h - band, TILE, s)
    p.end()
    return img


def _paint_solid(s: int, day: int | None) -> QImage:
    img = _new_image(s)
    p = _painter(img)
    p.setBrush(QColor(theme.ACCENT))
    p.drawPath(_tile_path(s))
    x, y, w, h = _page_box(s, 0.19)
    stroke = max(1, round(s / 14))
    pr = max(1.0, s * 0.09)
    outer = QPainterPath()
    outer.addRoundedRect(QRectF(x, y, w, h), pr, pr)
    inner = QPainterPath()
    inner.addRoundedRect(QRectF(x + stroke, y + stroke, w - 2 * stroke, h - 2 * stroke),
                         max(0.5, pr - stroke), max(0.5, pr - stroke))
    band = max(3, round(h * 0.32))
    p.setBrush(QColor(TILE))
    p.drawPath(outer.subtracted(inner))        # the page's outline
    p.setClipPath(outer)
    p.drawRect(QRectF(x, y, w, band))          # its top band
    p.setClipping(False)
    _dots(p, x + stroke, y + band, w - 2 * stroke, h - band - stroke, TILE, s)
    p.end()
    return img


def _paint_day(s: int, day: int | None) -> QImage:
    img = _new_image(s)
    p = _painter(img)
    tile = _tile_path(s)
    # The popup cards' look: a dark card with an ACCENT edge (keeps the tile's outline
    # visible on a dark taskbar too).
    p.setBrush(QColor(theme.ACCENT))
    p.drawPath(tile)
    p.setBrush(QColor(TILE))
    p.drawPath(_tile_path(s, max(1, round(s / 20))))
    band = max(4, round(s * 0.3))
    p.setClipPath(tile)
    p.setBrush(QColor(theme.ACCENT))
    p.drawRect(QRectF(0, 0, s, band))
    p.setClipping(False)
    if day is None:
        _dots(p, 0, band, s, s - band, GLYPH, s)
    else:
        text = str(day)
        font = QFont("Segoe UI")
        font.setBold(True)
        font.setWeight(QFont.Weight.Black if s <= 24 else QFont.Weight.Bold)
        body = s - band
        font.setPixelSize(max(8, round(body * (0.98 if len(text) == 1 else 0.92))))
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        if len(text) > 1:
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.06 * s)
        p.setFont(font)
        p.setPen(QColor(GLYPH))
        p.drawText(QRectF(0, band - s * 0.04, s, body), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return img


def _paint_legacy(s: int, day: int | None) -> QImage:
    """The pre-0.12.2 tray icon (drawn at 64 px, scaled by Windows)."""
    img = _new_image(64)
    p = _painter(img)
    p.setBrush(QColor("#f1f3f4"))
    p.drawRoundedRect(4, 6, 56, 54, 8, 8)
    p.setBrush(QColor("#ea4335"))
    p.drawRoundedRect(4, 6, 56, 18, 8, 8)
    p.drawRect(4, 16, 56, 8)
    p.setPen(QColor("#202124"))
    font = QFont("Segoe UI")
    font.setBold(True)
    font.setPixelSize(30)
    p.setFont(font)
    p.drawText(4, 26, 56, 34, Qt.AlignmentFlag.AlignCenter, str(day if day else ""))
    p.end()
    if s == 64:
        return img
    return img.scaled(s, s, Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)


_PAINTERS = {"ring": _paint_ring, "solid": _paint_solid, "day": _paint_day,
             "legacy": _paint_legacy}


def render(variant: str, size: int, day: int | None = None) -> QImage:
    """The icon at exactly `size` x `size` pixels. Raises on an unknown variant."""
    return _PAINTERS[variant](int(size), day)


def legacy_icon(day: int | None = None) -> QIcon:
    try:
        return QIcon(QPixmap.fromImage(_paint_legacy(64, day)))
    except Exception:  # noqa: BLE001
        log.exception("legacy icon failed")
        return QIcon()


def build_icon(variant: str | None = None, day: int | None = None,
               sizes=SIZES) -> QIcon:
    """A QIcon holding one pixmap per size, each painted at that size. Falls back to
    the legacy icon if anything fails (never raises)."""
    name = variant or ICON_VARIANT
    try:
        icon = QIcon()
        for s in sizes:
            img = render(name, s, day)
            if img.isNull():
                raise ValueError(f"empty {name} icon at {s} px")
            icon.addPixmap(QPixmap.fromImage(img))
        if icon.isNull():
            raise ValueError(f"empty {name} icon")
        return icon
    except Exception:  # noqa: BLE001
        log.exception("drawing the %s icon failed; using the old icon", name)
        return legacy_icon(day)


def png_bytes(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def ico_bytes(variant: str | None = None, sizes=ICO_SIZES) -> bytes:
    """A multi-size .ico (PNG-compressed entries, Windows Vista+), static glyph."""
    name = variant or ICON_VARIANT
    images = [(s, png_bytes(render(name, s, None))) for s in sizes]
    out = io.BytesIO()
    out.write(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for s, data in images:
        dim = 0 if s >= 256 else s
        out.write(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    for _s, data in images:
        out.write(data)
    return out.getvalue()


def write_ico(path, variant: str | None = None) -> None:
    with open(path, "wb") as f:
        f.write(ico_bytes(variant))
