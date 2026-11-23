import io
import pytest
import numpy as np
from PIL import Image, ImageFilter, ImageDraw

from backend.app.services.preprocessor import (
    normalize_exif,
    resize_if_needed,
    enhance_contrast,
    detect_blur,
    preprocessor,
    PreprocessorError,
)


def create_sample_image(width: int = 500, height: int = 500, sharp: bool = True) -> Image.Image:
    """Tạo ảnh mẫu giả lập trang giấy thi có chữ viết tay."""
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    # Vẽ các nét tương phản cao
    for y in range(50, height - 50, 40):
        draw.line([(50, y), (width - 50, y)], fill=(20, 20, 20), width=4)
        draw.text((60, y - 20), f"Bai toan buoc giai y = {y}", fill=(0, 0, 0))
    if not sharp:
        # Làm mờ mạnh
        img = img.filter(ImageFilter.GaussianBlur(radius=8))
    return img


def test_ut02_resize_image_greater_than_max():
    """UT-02: Resize ảnh > 4096px -> cạnh lớn nhất thành 4096px, giữ nguyên tỷ lệ."""
    orig = Image.new("RGB", (6000, 3000), color=(255, 255, 255))
    resized, was_resized = resize_if_needed(orig, max_side=4096)
    assert was_resized is True
    assert resized.size[0] == 4096
    assert resized.size[1] == 2048


def test_ut03_resize_image_less_than_max():
    """UT-03: Resize ảnh < 4096px -> không thay đổi kích thước."""
    orig = Image.new("RGB", (1920, 1080), color=(255, 255, 255))
    resized, was_resized = resize_if_needed(orig, max_side=4096)
    assert was_resized is False
    assert resized.size == (1920, 1080)


def test_ut04_blur_detection_blurry():
    """UT-04: Ảnh mờ (Gaussian blur) -> is_blurry = True, variance < threshold."""
    blurry_img = create_sample_image(sharp=False)
    is_blurry, variance = detect_blur(blurry_img, threshold=80.0)
    assert is_blurry is True
    assert variance < 80.0


def test_ut05_blur_detection_sharp():
    """UT-05: Ảnh sắc nét -> is_blurry = False, variance > threshold."""
    sharp_img = create_sample_image(sharp=True)
    is_blurry, variance = detect_blur(sharp_img, threshold=80.0)
    assert is_blurry is False
    assert variance >= 80.0


def test_ut06_contrast_enhancement():
    """UT-06: Tăng tương phản không crash, trả về ảnh có cùng kích thước."""
    img = create_sample_image()
    enhanced = enhance_contrast(img, factor=1.3)
    assert enhanced.size == img.size


def test_ut07_invalid_image_bytes():
    """UT-07: Byte rác không phải ảnh -> trả về báo cáo lỗi an toàn."""
    garbage_bytes = b"NOT_AN_IMAGE_FILE_DATA_12345"
    out_bytes, report = preprocessor.process_image(garbage_bytes)
    assert report.is_blurry is True
    assert any("error" in t for t in report.applied_transforms)


def test_full_pipeline_process_image():
    """Test tổng thể hàm process_image."""
    sharp_img = create_sample_image(800, 600, sharp=True)
    buf = io.BytesIO()
    sharp_img.save(buf, format="JPEG")
    img_bytes = buf.getvalue()

    processed_bytes, report = preprocessor.process_image(img_bytes)
    assert len(processed_bytes) > 0
    assert report.is_blurry is False
    assert report.blur_score > 0.0
    assert "contrast_enhanced_1.3x" in report.applied_transforms
