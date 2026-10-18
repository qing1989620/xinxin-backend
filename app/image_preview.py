"""Bounded private previews; originals stay unchanged and require authentication."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from threading import BoundedSemaphore

from PIL import Image, ImageOps, UnidentifiedImageError

_decode_slot = BoundedSemaphore(1)


@lru_cache(maxsize=16)
def image_preview(path: Path, checksum: str) -> bytes:
    # Checksum makes cache identity follow immutable library content.
    with _decode_slot:
        try:
            with Image.open(path) as original:
                if original.format not in {"JPEG", "PNG", "WEBP", "GIF"}:
                    raise ValueError("此文件不支持图片预览，请下载原文件")
                if original.width * original.height > 25_000_000:
                    raise ValueError("图片尺寸过大，请下载原文件查看")
                original.draft("RGB", (1600, 1600))
                original.thumbnail((1600, 1600))
                picture = ImageOps.exif_transpose(original)
                if picture.mode not in {"RGB", "RGBA"}:
                    picture = picture.convert("RGBA" if "transparency" in picture.info else "RGB")
                output = BytesIO()
                picture.save(output, "WEBP", quality=84, method=3)
                return output.getvalue()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise ValueError("图片无法解码，请下载原文件检查或重新上传") from None
