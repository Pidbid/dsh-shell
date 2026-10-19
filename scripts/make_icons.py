#!/usr/bin/env python3
"""Generate square PNG/ICO assets from the supplied artwork.

Uses Qt's image plugins (already a runtime dependency) instead of Pillow.
The multi-size ICO is assembled here because Qt's ICO writer stores only one
image, while Windows picks a size per DPI and surface.
"""

from __future__ import annotations

import shutil
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRect, Qt
from PySide6.QtGui import QImage, QPainter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SOURCE = Path(sys.argv[1]) if len(sys.argv) > 1 else ASSETS / "icon-source.png"

PNG_SIZES = (32, 48, 64, 128, 256, 512)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def alpha_bbox(image: QImage) -> QRect:
    """Bounding box of non-transparent pixels."""
    width, height = image.width(), image.height()
    stride = image.bytesPerLine()
    data = bytes(image.constBits())
    min_x, min_y, max_x, max_y = width, height, -1, -1
    for y in range(height):
        row = data[y * stride : y * stride + width * 4]
        for x in range(width):
            if row[x * 4 + 3]:
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
    if max_x < 0:
        return QRect(0, 0, width, height)
    return QRect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def square(source: QImage, size: int, padding: float = 0.06) -> QImage:
    image = source.convertToFormat(QImage.Format.Format_RGBA8888)
    image = image.copy(alpha_bbox(image))
    inner = max(1, round(size * (1 - padding * 2)))
    scaled = image.scaled(
        inner,
        inner,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    canvas = QImage(size, size, QImage.Format.Format_RGBA8888)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    painter.end()
    return canvas


def png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise SystemExit("failed to encode PNG")
    payload = bytes(buffer.data())
    buffer.close()
    return payload


def write_ico(path: Path, images: list[QImage]) -> None:
    """Write an ICO whose entries are PNG-compressed (supported since Vista)."""
    payloads = [png_bytes(image) for image in images]
    header = struct.pack("<HHH", 0, 1, len(payloads))
    offset = 6 + 16 * len(payloads)
    entries = []
    for image, payload in zip(images, payloads, strict=True):
        side = image.width()
        dimension = 0 if side >= 256 else side
        entries.append(struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(payload), offset))
        offset += len(payload)
    path.write_bytes(header + b"".join(entries) + b"".join(payloads))


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"source image not found: {SOURCE}")
    source = QImage(str(SOURCE))
    if source.isNull():
        raise SystemExit(f"cannot decode image: {SOURCE}")
    ASSETS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE, ASSETS / "icon-source.png")
    for size in PNG_SIZES:
        if not square(source, size).save(str(ASSETS / f"icon-{size}.png"), "PNG"):
            raise SystemExit(f"failed to write icon-{size}.png")
    if not square(source, 512).save(str(ASSETS / "icon.png"), "PNG"):
        raise SystemExit("failed to write icon.png")
    write_ico(ASSETS / "icon.ico", [square(source, size) for size in ICO_SIZES])
    print(f"wrote icons to {ASSETS}")


if __name__ == "__main__":
    main()
