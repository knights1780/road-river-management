"""Validated, offline image-to-WebP storage for facility and repair photos.

This module deliberately does not write SQLite. Its caller must coordinate
file creation, DB metadata writes, and cleanup on rollback (phase 10-17C).
"""

from __future__ import annotations

import hashlib
import io
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "BMP", "HEIF", "HEIC"})
DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_PIXELS = 50_000_000

# HEIC/HEIF support is registered once, only if the optional dependency exists.
_heif_registered = False


class PhotoValidationError(ValueError):
    """Rejected upload: unsupported, malformed or over resource limits."""


@dataclass(frozen=True)
class StoredPhoto:
    stored_filename: str
    stored_media_type: str
    stored_bytes: int
    stored_sha256: str
    original_filename: str
    original_stored_filename: str | None
    original_media_type: str | None
    original_bytes: int | None
    original_sha256: str | None
    photographed_on: str | None
    input_format: str
    width: int
    height: int


def _register_heif() -> None:
    global _heif_registered
    if _heif_registered:
        return
    try:
        from pillow_heif import register_heif_opener
    except ImportError:
        return
    register_heif_opener(thumbnails=False)
    _heif_registered = True


def _read_bounded(stream: BinaryIO, limit: int) -> bytes:
    if limit < 1:
        raise ValueError("max_upload_bytes must be positive")
    result = stream.read(limit + 1)
    if not isinstance(result, bytes):
        raise PhotoValidationError("Image upload must be a binary stream")
    if not result:
        raise PhotoValidationError("Empty image upload")
    if len(result) > limit:
        raise PhotoValidationError("Image upload exceeds allowed size")
    return result


def _photo_date(exif: Image.Exif) -> str | None:
    for tag in (36867, 36868, 306):  # DateTimeOriginal, Digitized, DateTime
        raw = exif.get(tag)
        if not isinstance(raw, str):
            continue
        try:
            parsed = datetime.strptime(raw[:19], "%Y:%m:%d %H:%M:%S").date()
        except ValueError:
            continue
        if date(1900, 1, 1) <= parsed <= date(2200, 12, 31):
            return parsed.isoformat()
    return None


def _input_media_type(fmt: str) -> str:
    return {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
        "BMP": "image/bmp",
        "HEIF": "image/heif",
        "HEIC": "image/heic",
    }[fmt]


def _input_extension(fmt: str) -> str:
    return {
        "JPEG": ".jpg",
        "PNG": ".png",
        "WEBP": ".webp",
        "BMP": ".bmp",
        "HEIF": ".heif",
        "HEIC": ".heic",
    }[fmt]


def _original_name(filename: str) -> str:
    # Metadata only, never used in filesystem paths.
    name = filename.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:255] or "upload"


def _put_exclusive(path: Path, data: bytes) -> None:
    # Do not replace existing files even if generated IDs collide.
    with path.open("xb") as output:
        output.write(data)


def save_photo(
    stream: BinaryIO,
    *,
    original_filename: str,
    storage_root: str | Path,
    keep_original: bool = False,
    quality: int = 82,
    max_side: int | None = 2560,
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    max_pixels: int = DEFAULT_MAX_PIXELS,
) -> StoredPhoto:
    """Validate and store one image, returning 0017-compatible DB metadata.

    Relative returned paths are opaque and should be stored in SQLite. Do not
    construct paths from user-supplied file names. Decode failure creates no
    files. Any file-write error removes files created during this call.
    """
    if type(quality) is not int or not 1 <= quality <= 100:
        raise ValueError("quality must be 1..100")
    if max_side is not None and (type(max_side) is not int or max_side < 1):
        raise ValueError("max_side must be a positive integer or None")
    if type(max_pixels) is not int or max_pixels < 1:
        raise ValueError("max_pixels must be positive")
    if not isinstance(original_filename, str):
        raise TypeError("original_filename must be a string")

    _register_heif()
    raw = _read_bounded(stream, max_upload_bytes)
    try:
        with Image.open(io.BytesIO(raw)) as source:
            fmt = (source.format or "").upper()
            if fmt not in ALLOWED_FORMATS:
                raise PhotoValidationError("Unsupported image format")
            if source.width * source.height > max_pixels:
                raise PhotoValidationError("Image dimensions exceed allowed pixels")
            photographed_on = _photo_date(source.getexif())
            # Force all image decoding before any files are written.
            source.load()
            image = ImageOps.exif_transpose(source)
            # Do not retain EXIF, including geolocation, in generated WebP.
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            if max_side is not None:
                image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            width, height = image.size
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=quality, method=6)
            webp = output.getvalue()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        if isinstance(exc, PhotoValidationError):
            raise
        raise PhotoValidationError("Invalid or unreadable image") from exc

    base = Path(storage_root).expanduser().resolve()
    if base.exists() and not base.is_dir():
        raise ValueError("storage_root must be a directory")
    # Ensure all file names are internally generated, sharded to avoid large dirs.
    token = secrets.token_hex(16)
    subdir = Path(token[:2]) / token[2:4]
    webp_rel = subdir / f"{token}.webp"
    original_rel = subdir / f"{token}-original{_input_extension(fmt)}" if keep_original else None
    target_dir = base / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        _put_exclusive(target_dir / webp_rel.name, webp)
        created.append(target_dir / webp_rel.name)
        if original_rel is not None:
            _put_exclusive(target_dir / original_rel.name, raw)
            created.append(target_dir / original_rel.name)
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise

    return StoredPhoto(
        stored_filename=webp_rel.as_posix(),
        stored_media_type="image/webp",
        stored_bytes=len(webp),
        stored_sha256=hashlib.sha256(webp).hexdigest(),
        original_filename=_original_name(original_filename),
        original_stored_filename=original_rel.as_posix() if original_rel else None,
        original_media_type=_input_media_type(fmt) if keep_original else None,
        original_bytes=len(raw) if keep_original else None,
        original_sha256=hashlib.sha256(raw).hexdigest() if keep_original else None,
        photographed_on=photographed_on,
        input_format=fmt,
        width=width,
        height=height,
    )
