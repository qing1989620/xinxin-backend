"""Bounded private previews; originals stay unchanged and require authentication."""
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from threading import BoundedSemaphore, Lock

from PIL import Image, ImageOps, UnidentifiedImageError

_decode_slot = BoundedSemaphore(1)
_cache_lock = Lock()
_preview_cache = OrderedDict()
SAFE_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_PREVIEW_BYTES = 64 * 1024
MAX_PREVIEW_EDGE = 1024
MAX_CACHED_PREVIEWS = 64


def image_preview(path: Path, checksum: str) -> bytes:
    key = (path, checksum)
    def cached():
        with _cache_lock:
            value = _preview_cache.get(key)
            if value is not None:
                _preview_cache.move_to_end(key)
            return value
    value = cached()
    if value is not None:
        return value
    with _decode_slot:
        # Concurrent misses for the same image share one encode instead of queuing repeats.
        value = cached()
        if value is None:
            value = _encode_preview(path)
            with _cache_lock:
                _preview_cache[key] = value
                while len(_preview_cache) > MAX_CACHED_PREVIEWS:
                    _preview_cache.popitem(last=False)
        return value


def _encode_preview(path: Path) -> bytes:
    try:
        with Image.open(path) as original:
            if original.format not in {"JPEG", "PNG", "WEBP", "GIF"}:
                raise ValueError("此文件不支持图片预览，请下载原文件")
            if original.width * original.height > 25_000_000:
                raise ValueError("图片尺寸过大，请下载原文件查看")
            original.draft("RGB", (MAX_PREVIEW_EDGE, MAX_PREVIEW_EDGE))
            original.thumbnail((MAX_PREVIEW_EDGE, MAX_PREVIEW_EDGE))
            picture = ImageOps.exif_transpose(original)
            if picture.mode not in {"RGB", "RGBA"}:
                picture = picture.convert("RGBA" if "transparency" in picture.info else "RGB")
            quality = 70
            while True:
                output = BytesIO()
                picture.save(output, "WEBP", quality=quality, method=3)
                if output.tell() <= MAX_PREVIEW_BYTES:
                    return output.getvalue()
                if quality > 55:
                    quality = 55
                else:
                    # Keep a bounded transfer even for noisy photos/transparent images.
                    edge = max(picture.size)
                    if edge <= 128:
                        raise ValueError("无法生成轻量预览，请下载原文件查看")
                    reduced = max(128, int(edge * .75))
                    picture.thumbnail((reduced, reduced))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("图片无法解码，请下载原文件检查或重新上传") from None
