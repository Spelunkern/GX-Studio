"""Decode client images and resolve icon sheets in GX and legacy formats."""

from dataclasses import dataclass
from pathlib import PurePosixPath

from PIL import Image
from PySide6.QtGui import QImage, QPixmap


@dataclass(frozen=True, slots=True)
class ImageData:
    pixels: bytes
    width: int
    height: int
    description: str

    def pixmap(self):
        image = QImage(self.pixels, self.width, self.height, self.width * 4, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(image)


def read_image(source):
    with Image.open(source) as image:
        width, height = image.size
        if width * height > 32_000_000:
            raise ValueError("The image exceeds 32 million pixels.")
        label = f"{width:,} × {height:,}  ·  {image.format}  ·  {image.mode}"
        return ImageData(image.convert("RGBA").tobytes(), width, height, label)


def icon_atlas(assets, filename):
    stem = PurePosixPath(filename).stem.removesuffix("_dds")
    for name in dict.fromkeys((filename, f"{stem}.dds", f"{stem}.tga", f"{stem}.png")):
        path = assets.resolve(f"interface/icon/{name}")
        if path is not None:
            try:
                return read_image(path).pixmap()
            except (OSError, ValueError, Image.DecompressionBombError):
                continue
    return QPixmap()
