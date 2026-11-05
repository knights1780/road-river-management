"""Image storage regression tests (do not need SQLite or Flask)."""

import io
from pathlib import Path

import pytest
from PIL import Image

from road_river_management.services.photo_storage import (
    PhotoValidationError,
    save_photo,
)


def image_bytes(fmt: str, size=(120, 80)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (42, 91, 153)).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP", "BMP"])
def test_convert_supported_formats(tmp_path: Path, fmt: str):
    result = save_photo(
        io.BytesIO(image_bytes(fmt)),
        original_filename=f"bridge.{fmt.lower()}",
        storage_root=tmp_path,
    )
    with Image.open(tmp_path / result.stored_filename) as stored:
        assert stored.format == "WEBP"
        assert stored.size == (120, 80)
    assert result.original_stored_filename is None
    assert len(result.stored_sha256) == 64
    assert result.stored_media_type == "image/webp"


def test_resize_and_preserve_original(tmp_path: Path):
    raw = image_bytes("PNG", (640, 480))
    result = save_photo(
        io.BytesIO(raw), original_filename="../../bridge.png",
        storage_root=tmp_path, keep_original=True, max_side=160,
    )
    assert result.width == 160
    assert result.height == 120
    assert result.original_filename == "bridge.png"
    assert (tmp_path / result.original_stored_filename).read_bytes() == raw
    assert result.original_media_type == "image/png"


def test_invalid_image_does_not_write(tmp_path: Path):
    with pytest.raises(PhotoValidationError):
        save_photo(io.BytesIO(b"not an image"), original_filename="x.jpg", storage_root=tmp_path)
    assert list(tmp_path.rglob("*.webp")) == []


def test_upload_size_rejected(tmp_path: Path):
    with pytest.raises(PhotoValidationError, match="exceeds"):
        save_photo(io.BytesIO(image_bytes("PNG")), original_filename="x.png",
                   storage_root=tmp_path, max_upload_bytes=16)


def test_pixel_limit_rejected(tmp_path: Path):
    with pytest.raises(PhotoValidationError, match="pixels"):
        save_photo(io.BytesIO(image_bytes("PNG", (100, 100))),
                   original_filename="x.png", storage_root=tmp_path, max_pixels=9999)


def test_no_directory_traversal(tmp_path: Path):
    result = save_photo(io.BytesIO(image_bytes("JPEG")), original_filename="../../a.jpg",
                        storage_root=tmp_path)
    assert not Path(result.stored_filename).is_absolute()
    assert ".." not in Path(result.stored_filename).parts
    assert result.original_filename == "a.jpg"


def test_heic_support_when_decoder_is_available(tmp_path: Path):
    pillow_heif = pytest.importorskip("pillow_heif")
    # Actual HEIC encoding is available when libheif has an encoder.
    try:
        heif = pillow_heif.from_pillow(Image.new("RGB", (64, 48), "red"))
        buf = io.BytesIO()
        heif.save(buf, format="HEIF")
    except (OSError, ValueError):
        pytest.skip("HEIF encoder unavailable on this platform")
    result = save_photo(io.BytesIO(buf.getvalue()), original_filename="a.heic",
                        storage_root=tmp_path)
    with Image.open(tmp_path / result.stored_filename) as output:
        assert output.format == "WEBP"
