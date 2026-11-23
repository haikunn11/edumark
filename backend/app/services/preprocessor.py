from __future__ import annotations

import io
import math
from typing import Tuple
import numpy as np
from PIL import Image, ImageEnhance, ImageOps

try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False

from backend.app.models.schemas import ImageQualityReport
from backend.app.core.config import settings


class PreprocessorError(Exception):
    """Lỗi khi tiền xử lý ảnh (file corrupt, format lạ)."""
    pass


def normalize_exif(image: Image.Image) -> tuple[Image.Image, bool]:
    """
    Xoay ảnh tự động theo EXIF orientation tag (hỗ trợ cả 8 giá trị EXIF 1-8).
    Nếu không có EXIF hoặc orientation == 1, giữ nguyên.
    """
    try:
        transposed = ImageOps.exif_transpose(image)
        if transposed is not None and transposed is not image:
            return transposed, True
        return transposed if transposed is not None else image, False
    except Exception:
        return image, False


def resize_if_needed(image: Image.Image, max_side: int = 4096) -> tuple[Image.Image, bool]:
    """
    Resize giữ nguyên tỷ lệ khung hình nếu cạnh dài nhất vượt quá max_side.
    Sử dụng thuật toán Image.LANCZOS cho độ sắc nét tối ưu.
    """
    width, height = image.size
    longest = max(width, height)
    if longest > max_side:
        scale = max_side / float(longest)
        new_width = max(1, int(round(width * scale)))
        new_height = max(1, int(round(height * scale)))
        resized = image.resize((new_width, new_height), Image.LANCZOS)
        return resized, True
    return image, False


def enhance_contrast(image: Image.Image, factor: float = 1.3) -> Image.Image:
    """
    Tăng độ tương phản để làm nổi bật nét mực viết tay trên nền giấy trắng.
    """
    enhancer = ImageEnhance.Contrast(image)
    return enhancer.enhance(factor)


def detect_blur(image: Image.Image, threshold: float = 80.0) -> tuple[bool, float]:
    """
    Tính độ sắc nét của ảnh dựa trên phương sai toán tử Laplacian (Laplacian Variance).
    variance < threshold -> Ảnh bị mờ (is_blurry = True).
    """
    gray_image = image.convert("L")
    gray_np = np.array(gray_image, dtype=np.uint8)

    if HAS_OPENCV:
        try:
            laplacian_var = float(cv2.Laplacian(gray_np, cv2.CV_64F).var())
        except Exception:
            laplacian_var = _compute_laplacian_var_numpy(gray_np)
    else:
        laplacian_var = _compute_laplacian_var_numpy(gray_np)

    laplacian_var = max(0.0, round(laplacian_var, 2))
    is_blurry = laplacian_var < threshold
    return is_blurry, laplacian_var


def _compute_laplacian_var_numpy(gray_np: np.ndarray) -> float:
    """Fallback tính phương sai Laplacian bằng kernel 3x3 khi không có OpenCV."""
    kernel = np.array([[0, 1, 0],
                       [1, -4, 1],
                       [0, 1, 0]], dtype=np.float64)
    # 2D correlation xấp xỉ
    h, w = gray_np.shape
    if h < 3 or w < 3:
        return 100.0
    sub = gray_np[1:-1, 1:-1] * -4.0 + \
          gray_np[:-2, 1:-1] + gray_np[2:, 1:-1] + \
          gray_np[1:-1, :-2] + gray_np[1:-1, 2:]
    return float(np.var(sub))


def detect_and_deskew(image: Image.Image, max_angle_deg: float = 15.0) -> tuple[Image.Image, float]:
    """
    Phát hiện góc nghiêng bài làm và nắn thẳng (deskew) nếu góc lệch trong khoảng cho phép.
    """
    if not HAS_OPENCV:
        return image, 0.0

    try:
        gray = np.array(image.convert("L"), dtype=np.uint8)
        # Binarize
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        
        # Tìm tọa độ các điểm mực
        coords = np.column_stack(np.where(thresh > 0))
        if len(coords) < 100:
            return image, 0.0

        angle = cv2.minAreaRect(coords)[-1]
        # Chuyển đổi góc về phạm vi [-45, 45]
        if angle < -45:
            angle = -(90 + angle)
        elif angle > 45:
            angle = 90 - angle

        # Chỉ nắn nếu góc nghiêng nhẹ và đáng kể (0.5 độ <= |angle| <= max_angle_deg)
        if 0.5 <= abs(angle) <= max_angle_deg:
            rotated = image.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))
            return rotated, round(angle, 2)
        return image, 0.0
    except Exception:
        return image, 0.0


class ImagePreprocessor:
    """Module tiền xử lý ảnh toàn diện: EXIF, Deskew, Resize, Denoise, Blur Check, Contrast."""

    def __init__(self, blur_threshold: float | None = None) -> None:
        self.blur_threshold = blur_threshold or settings.BLUR_THRESHOLD

    def process_image(self, image_bytes: bytes) -> tuple[bytes, ImageQualityReport]:
        applied_transforms: list[str] = []
        try:
            # 1. Đọc ảnh từ bytes
            try:
                image = Image.open(io.BytesIO(image_bytes))
                image.load()
            except Exception as exc:
                raise PreprocessorError(f"Không thể đọc file ảnh: {exc}")

            # 2. Chuẩn hóa EXIF orientation
            image, rotated_exif = normalize_exif(image)
            if rotated_exif:
                applied_transforms.append("exif_orientation_normalized")

            # 3. Nắn thẳng góc nghiêng (Deskew) nếu cần
            image, skew_angle = detect_and_deskew(image)
            if abs(skew_angle) > 0.0:
                applied_transforms.append(f"deskewed_{skew_angle}deg")

            # 4. Resize nếu vượt quá kích thước chuẩn (4096px)
            image, was_resized = resize_if_needed(image, max_side=4096)
            if was_resized:
                applied_transforms.append(f"resized_max_4096px_{image.size[0]}x{image.size[1]}")

            # 5. Kiểm tra độ nét / mờ ảnh (Laplacian Variance)
            is_blurry, blur_score = detect_blur(image, threshold=self.blur_threshold)

            # 6. Tăng cường độ tương phản cho chữ viết tay
            image = enhance_contrast(image, factor=1.3)
            applied_transforms.append("contrast_enhanced_1.3x")

            # 7. Xuất ảnh đã tiền xử lý thành JPEG 90%
            out_buf = io.BytesIO()
            rgb_image = image.convert("RGB")
            rgb_image.save(out_buf, format="JPEG", quality=90, optimize=True)
            processed_bytes = out_buf.getvalue()

            report = ImageQualityReport(
                is_blurry=is_blurry,
                blur_score=blur_score,
                applied_transforms=applied_transforms,
            )
            return processed_bytes, report

        except PreprocessorError as pe:
            return image_bytes, ImageQualityReport(
                is_blurry=True,
                blur_score=0.0,
                applied_transforms=[f"error: {str(pe)}"],
            )
        except Exception as e:
            return image_bytes, ImageQualityReport(
                is_blurry=False,
                blur_score=100.0,
                applied_transforms=[f"error_fallback: {str(e)}"],
            )


preprocessor = ImagePreprocessor()
