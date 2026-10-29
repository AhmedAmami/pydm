"""Draw the PyDM icon at every size the app, installer and extension need.

    .venv/Scripts/python tools/make_icons.py

Writes pydm/assets/icon-<size>.png, pydm/assets/pydm.ico and the extension's
icons. Each size is drawn separately (not scaled down) so small ones stay crisp.
Pillow is only needed for the .ico (pip install pillow).
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QBrush, QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "pydm" / "assets"
EXTENSION = ROOT / "browser_extension" / "icons"


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 64.0  # design grid: 64 x 64

    # Rounded tile, blue gradient from top-left to bottom-right.
    inset = 0 if size <= 16 else 2 * s
    tile = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    grad = QLinearGradient(tile.topLeft(), tile.bottomRight())
    grad.setColorAt(0, QColor("#4f9cff"))
    grad.setColorAt(1, QColor("#1849c6"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(tile, 14 * s, 14 * s)

    # Down arrow: shaft + head, then the "tray" it lands in.
    white = QColor("#ffffff")
    p.setBrush(white)
    shaft_w = 9 * s if size >= 32 else 11 * s
    p.drawRoundedRect(QRectF(32 * s - shaft_w / 2, 13 * s, shaft_w, 20 * s), 2.5 * s, 2.5 * s)
    head = QPainterPath()
    head.moveTo(QPointF(17 * s, 30 * s))
    head.lineTo(QPointF(47 * s, 30 * s))
    head.lineTo(QPointF(32 * s, 45 * s))
    head.closeSubpath()
    p.drawPath(head)
    p.drawRoundedRect(QRectF(16 * s, 49 * s, 32 * s, (5 if size >= 32 else 6) * s), 2.5 * s, 2.5 * s)
    p.end()
    return img


def main() -> None:
    QGuiApplication(sys.argv)
    ASSETS.mkdir(parents=True, exist_ok=True)
    EXTENSION.mkdir(parents=True, exist_ok=True)
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    for n in sizes:
        draw(n).save(str(ASSETS / f"icon-{n}.png"))
    for n in (16, 32, 48, 128):
        draw(n).save(str(EXTENSION / f"icon{n}.png"))
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not installed: skipped pydm.ico (pip install pillow)")
        return
    images = [Image.open(ASSETS / f"icon-{n}.png") for n in sizes]
    images[-1].save(ASSETS / "pydm.ico", sizes=[(n, n) for n in sizes], append_images=images[:-1])
    print("wrote", ASSETS / "pydm.ico")


if __name__ == "__main__":
    main()
