"""验证并重新编码图片，丢弃文件名和 EXIF；不接受 SVG 或任意文件。"""

import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000


def normalize_image(raw: bytes) -> bytes:
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("图片不能为空且不能超过 5 MB。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("只支持 JPEG、PNG 和 WebP 图片。")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise ValueError("图片分辨率不能超过 2000 万像素。")
                source.load()
                source = ImageOps.exif_transpose(source)
                source.thumbnail((2048, 2048))
                rgba = source.convert("RGBA")
                target = Image.new("RGB", rgba.size, "white")
                target.paste(rgba, mask=rgba.getchannel("A"))
                result = io.BytesIO()
                target.save(result, format="JPEG", quality=85)
                return result.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError("无法读取这张图片，请选择有效的 JPEG、PNG 或 WebP 文件。") from exc
