from __future__ import annotations

import io
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
    """Tăng độ tương phản để làm nổi bật nét mực viết tay trên nền giấy trắng."""
    enhancer = ImageEnhance.Contrast(image)
    return enhancer.enhance(factor)


def _compute_laplacian_var_numpy(gray_np: np.ndarray) -> float:
    """Fallback tính phương sai Laplacian bằng kernel 3x3 khi không có OpenCV."""
    h, w = gray_np.shape
    if h < 3 or w < 3:
        return 100.0
    sub = (
        gray_np[1:-1, 1:-1].astype(np.float64) * -4.0
        + gray_np[:-2, 1:-1]
        + gray_np[2:, 1:-1]
        + gray_np[1:-1, :-2]
        + gray_np[1:-1, 2:]
    )
    return float(np.var(sub))


def detect_blur(image: Image.Image, threshold: float = 80.0) -> tuple[bool, float]:
    """
    Tính độ sắc nét của ảnh dựa trên phương sai toán tử Laplacian.
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


def _order_quad_points(pts: np.ndarray) -> np.ndarray:
    """Sắp xếp 4 điểm góc theo thứ tự: top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def detect_and_warp_document(image: Image.Image) -> tuple[Image.Image, bool]:
    """
    Phát hiện 4 góc trang giấy bài thi chụp bằng điện thoại và duỗi phẳng phối cảnh
    (4-point Perspective Transform). Chỉ áp dụng khi phát hiện rõ khung giấy chiếm
    25%..95% diện tích ảnh và có độ nghiêng phối cảnh rõ rệt.
    """
    if not HAS_OPENCV:
        return image, False

    try:
        rgb = np.array(image.convert("RGB"), dtype=np.uint8)
        h, w = rgb.shape[:2]
        if min(h, w) < 120:
            return image, False

        # Thu nhỏ để dò contour nhanh & ổn định
        scale = 600.0 / float(max(h, w)) if max(h, w) > 600 else 1.0
        small = cv2.resize(rgb, (int(w * scale), int(h * scale))) if scale < 1.0 else rgb
        gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edged = cv2.Canny(blurred, 50, 150)
        edged = cv2.dilate(edged, np.ones((3, 3), np.uint8), iterations=1)

        contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return image, False

        small_area = float(small.shape[0] * small.shape[1])
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]

        best_quad = None
        for cnt in contours:
            area = cv2.contourArea(cnt)
            ratio = area / small_area
            if not (0.25 <= ratio <= 0.95):
                continue
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                best_quad = approx.reshape(4, 2).astype("float32") / scale
                break

        if best_quad is None:
            return image, False

        rect = _order_quad_points(best_quad)
        tl, tr, br, bl = rect

        width_a = float(np.linalg.norm(br - bl))
        width_b = float(np.linalg.norm(tr - tl))
        max_width = int(round(max(width_a, width_b)))

        height_a = float(np.linalg.norm(tr - br))
        height_b = float(np.linalg.norm(tl - bl))
        max_height = int(round(max(height_a, height_b)))

        if max_width < 100 or max_height < 100:
            return image, False

        dst = np.array(
            [
                [0, 0],
                [max_width - 1, 0],
                [max_width - 1, max_height - 1],
                [0, max_height - 1],
            ],
            dtype="float32",
        )
        matrix = cv2.getPerspectiveTransform(rect, dst)
        warped = cv2.warpPerspective(rgb, matrix, (max_width, max_height))
        return Image.fromarray(warped), True
    except Exception:
        return image, False


def remove_shadows(image: Image.Image) -> tuple[Image.Image, bool]:
    """
    Khử bóng đổ & cân bằng ánh sáng cục bộ cho ảnh chụp điện thoại (bóng tay, bóng đèn).
    Chỉ áp dụng khi nền sáng có độ lệch đáng kể (std illumination > 12.0) để giữ nguyên
    ảnh sạch đã scan sẵn.
    """
    if not HAS_OPENCV:
        return image, False

    try:
        rgb = np.array(image.convert("RGB"), dtype=np.uint8)
        if min(rgb.shape[:2]) < 40:
            return image, False

        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
        l_chan, a_chan, b_chan = cv2.split(lab)

        kernel_size = max(15, (min(rgb.shape[:2]) // 20) | 1)
        kernel_size = min(kernel_size, 51)
        dilated = cv2.dilate(l_chan, np.ones((kernel_size, kernel_size), np.uint8))
        bg = cv2.medianBlur(dilated, kernel_size)

        # Chỉ khử bóng nếu nền thực sự không đều ánh sáng
        if float(np.std(bg)) < 12.0:
            return image, False

        # Chuẩn hóa độ sáng nền: divide để triệt bóng tối cục bộ
        norm_l = cv2.divide(l_chan, np.maximum(bg, 1), scale=255)
        blended_l = cv2.addWeighted(norm_l, 0.65, l_chan, 0.35, 0)
        merged = cv2.merge([blended_l, a_chan, b_chan])
        cleaned_rgb = cv2.cvtColor(merged, cv2.COLOR_LAB2RGB)
        return Image.fromarray(cleaned_rgb), True
    except Exception:
        return image, False


def detect_sideways_orientation_cw(image: Image.Image) -> int:
    """
    Phát hiện ảnh bài thi bị chụp xoay ngang 90° (không có EXIF tag) bằng OpenCV local (0 tốn token).
    Trả về góc cần xoay theo chiều kim đồng hồ:
      - 0: ảnh đã đứng thẳng (hoặc không đủ bằng chứng dòng kẻ/chữ nằm dọc).
      - 270: cần xoay 270° CW (tức 90° ngược chiều kim đồng hồ) để đứng thẳng.
      - 90: cần xoay 90° CW để đứng thẳng.
    """
    if not HAS_OPENCV:
        return 0

    try:
        rgb = np.array(image.convert("RGB"), dtype=np.uint8)
        h, w = rgb.shape[:2]
        if min(h, w) < 200:
            return 0

        scale = 800.0 / float(max(h, w)) if max(h, w) > 800 else 1.0
        small_rgb = cv2.resize(rgb, (int(w * scale), int(h * scale))) if scale < 1.0 else rgb
        gray = cv2.cvtColor(small_rgb, cv2.COLOR_RGB2GRAY)

        # 1. So sánh gradient ngang (gy: dòng kẻ ngang + dòng chữ) vs dọc (gx: dòng kẻ dọc khi chụp ngang 90°)
        gx = float(np.mean(np.abs(cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3))))
        gy = float(np.mean(np.abs(cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3))))
        if gx < 1e-6 or (gy / gx) >= 0.78:
            return 0

        # 2. So sánh phương sai chiếu ngang vs chiếu dọc (projection profile)
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        bw = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 10)
        h_var = float(np.var(np.mean(bw, axis=1)))
        v_var = float(np.var(np.mean(bw, axis=0)))
        if v_var < 1e-6 or (h_var / v_var) >= 0.65:
            return 0

        # 3. Xác nhận qua độ dài đường thẳng Hough (dòng kẻ giấy thi nằm dọc áp đảo dòng ngang)
        edges = cv2.Canny(gray, 30, 100)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=70, minLineLength=70, maxLineGap=15)
        h_len = 0.0
        v_len = 0.0
        if lines is not None:
            for x1, y1, x2, y2 in lines.reshape(-1, 4):
                dx, dy = abs(int(x2) - int(x1)), abs(int(y2) - int(y1))
                ang = float(np.degrees(np.arctan2(dy, dx)))
                length = float(np.hypot(dx, dy))
                if ang < 15.0:
                    h_len += length
                elif ang > 75.0:
                    v_len += length

        if v_len < max(1500.0, 2.0 * h_len):
            return 0

        # 4. Xác định chiều xoay (270° CW vs 90° CW) dựa trên mật độ mực viết tay ở góc đầu trang:
        #    Học sinh viết từ trên-xuống & trái-sang-phải:
        #    - Nếu trang bị xoay 90° CW (cần xoay 270° CW để dựng lại): đầu trang (top-left gốc) nằm ở góc TRÊN-PHẢI.
        #    - Nếu trang bị xoay 90° CCW (cần xoay 90° CW để dựng lại): đầu trang (top-left gốc) nằm ở góc DƯỚI-TRÁI.
        #    Loại bớt dòng kẻ dọc mỏng bằng morphological open để đo chủ yếu nét chữ viết tay.
        sh, sw = bw.shape
        vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 25))
        ruled_lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vert_kernel)
        ink = cv2.subtract(bw, ruled_lines)
        # Bỏ lề ngoài 5% để tránh viền bàn / nền tối
        my, mx = max(1, int(sh * 0.05)), max(1, int(sw * 0.05))
        ink_roi = ink[my : sh - my, mx : sw - mx]
        rh, rw = ink_roi.shape

        left_ink = float(np.mean(ink_roi[:, : rw // 2]))
        right_ink = float(np.mean(ink_roi[:, rw // 2 :]))

        # Đầu trang (phía trên của tờ giấy gốc) thường có mật độ viết dày hơn cuối trang (thường còn dòng trống):
        # - Ảnh bị xoay 90° CW (cần xoay 270° CW để dựng đứng): đầu trang nằm bên PHẢI -> right_ink >= left_ink.
        # - Ảnh bị xoay 90° CCW (cần xoay 90° CW để dựng đứng): đầu trang nằm bên TRÁI -> left_ink > right_ink.
        return 270 if right_ink >= left_ink else 90
    except Exception:
        return 0


def detect_and_deskew(image: Image.Image, max_angle_deg: float = 15.0) -> tuple[Image.Image, float]:
    """Phát hiện góc nghiêng bài làm và nắn thẳng (deskew) nếu góc lệch trong khoảng cho phép."""
    if not HAS_OPENCV:
        return image, 0.0

    try:
        gray = np.array(image.convert("L"), dtype=np.uint8)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        coords = np.column_stack(np.where(thresh > 0))
        if len(coords) < 100:
            return image, 0.0

        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        elif angle > 45:
            angle = 90 - angle

        if 0.5 <= abs(angle) <= max_angle_deg:
            rotated = image.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))
            return rotated, round(angle, 2)
        return image, 0.0
    except Exception:
        return image, 0.0


def assess_capture_quality(image_bytes: bytes, blur_threshold: float | None = None) -> ImageQualityReport:
    """
    Đánh giá nhanh chất lượng ảnh chụp điện thoại (độ nét + độ sáng) TRƯỚC KHI gửi AI.
    Phục vụ cảnh báo tức thì ngay trên Mobile App Camera.
    """
    threshold = blur_threshold if blur_threshold is not None else settings.BLUR_THRESHOLD
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except Exception as exc:
        return ImageQualityReport(
            is_blurry=True,
            blur_score=0.0,
            brightness=0.0,
            is_acceptable=False,
            warnings=[f"File ảnh không hợp lệ: {exc}"],
            recommendation="Vui lòng chụp lại ảnh rõ nét (JPEG/PNG).",
        )

    image, _ = normalize_exif(image)
    gray_np = np.array(image.convert("L"), dtype=np.uint8)
    brightness = round(float(np.mean(gray_np)), 1)
    is_too_dark = brightness < settings.MIN_BRIGHTNESS
    is_overexposed = brightness > settings.MAX_BRIGHTNESS and float(np.std(gray_np)) < 15.0
    is_blurry, blur_score = detect_blur(image, threshold=threshold)

    warnings: list[str] = []
    if is_blurry:
        warnings.append("Ảnh bị mờ hoặc rung tay — chữ viết tay khó đọc.")
    if is_too_dark:
        warnings.append("Ảnh thiếu sáng — hãy bật đèn flash hoặc chụp nơi đủ sáng.")
    if is_overexposed:
        warnings.append("Ảnh quá chói / trắng xóa — tránh ánh đèn chiếu thẳng vào giấy.")

    is_acceptable = not (is_blurry or is_too_dark or is_overexposed)
    if is_acceptable:
        recommendation = "Ảnh rõ nét, đủ sáng — sẵn sàng chấm bài!"
    else:
        recommendation = "Khuyên chụp lại: giữ chắc điện thoại song song mặt giấy và đủ ánh sáng."

    return ImageQualityReport(
        is_blurry=is_blurry,
        blur_score=blur_score,
        brightness=brightness,
        is_too_dark=is_too_dark,
        is_overexposed=is_overexposed,
        is_acceptable=is_acceptable,
        warnings=warnings,
        recommendation=recommendation,
    )


class ImagePreprocessor:
    """Module tiền xử lý ảnh toàn diện cho ảnh scan & chụp điện thoại."""

    def __init__(self, blur_threshold: float | None = None) -> None:
        self.blur_threshold = blur_threshold or settings.BLUR_THRESHOLD

    def process_image(self, image_bytes: bytes) -> tuple[bytes, ImageQualityReport]:
        applied_transforms: list[str] = []
        try:
            try:
                image = Image.open(io.BytesIO(image_bytes))
                image.load()
            except Exception as exc:
                raise PreprocessorError(f"Không thể đọc file ảnh: {exc}")

            # 1. Chuẩn hóa EXIF orientation
            image, rotated_exif = normalize_exif(image)
            if rotated_exif:
                applied_transforms.append("exif_orientation_normalized")

            # 1b. Tự phát hiện & xoay đứng ảnh chụp ngang 90° không có EXIF tag (local OpenCV)
            sideways_deg = detect_sideways_orientation_cw(image)
            if sideways_deg in (90, 270):
                image = image.rotate(-sideways_deg, expand=True)
                applied_transforms.append(f"auto_rotated_{sideways_deg}deg_cw")

            # 2. Đánh giá độ sáng thô trước khi xử lý
            raw_gray = np.array(image.convert("L"), dtype=np.uint8)
            brightness = round(float(np.mean(raw_gray)), 1)
            is_too_dark = brightness < settings.MIN_BRIGHTNESS
            is_overexposed = brightness > settings.MAX_BRIGHTNESS and float(np.std(raw_gray)) < 15.0

            # 3. Duỗi phẳng trang giấy chụp nghiêng (4-point Perspective Transform)
            image, perspective_corrected = detect_and_warp_document(image)
            if perspective_corrected:
                applied_transforms.append("perspective_warped")

            # 4. Nắn thẳng góc nghiêng nhẹ (Deskew)
            image, skew_angle = detect_and_deskew(image)
            if abs(skew_angle) > 0.0:
                applied_transforms.append(f"deskewed_{skew_angle}deg")

            # 5. Resize nếu vượt quá 4096px
            image, was_resized = resize_if_needed(image, max_side=4096)
            if was_resized:
                applied_transforms.append(f"resized_max_4096px_{image.size[0]}x{image.size[1]}")

            # 6. Kiểm tra độ nét / mờ (Laplacian Variance)
            is_blurry, blur_score = detect_blur(image, threshold=self.blur_threshold)

            # 7. Khử bóng đổ ánh sáng không đều (chụp điện thoại)
            image, shadow_removed = remove_shadows(image)
            if shadow_removed:
                applied_transforms.append("shadow_removed")

            # 8. Tăng tương phản làm nổi nét mực
            image = enhance_contrast(image, factor=1.3)
            applied_transforms.append("contrast_enhanced_1.3x")

            # 9. Xuất JPEG 90%
            out_buf = io.BytesIO()
            rgb_image = image.convert("RGB")
            rgb_image.save(out_buf, format="JPEG", quality=90, optimize=True)
            processed_bytes = out_buf.getvalue()

            warnings: list[str] = []
            if is_blurry:
                warnings.append("Ảnh hơi mờ, nên kiểm tra lại nét chữ.")
            if is_too_dark:
                warnings.append("Ảnh chụp thiếu sáng.")
            if is_overexposed:
                warnings.append("Ảnh chụp bị lóa sáng.")

            report = ImageQualityReport(
                is_blurry=is_blurry,
                blur_score=blur_score,
                brightness=brightness,
                is_too_dark=is_too_dark,
                is_overexposed=is_overexposed,
                perspective_corrected=perspective_corrected,
                shadow_removed=shadow_removed,
                is_acceptable=not (is_blurry or is_too_dark or is_overexposed),
                warnings=warnings,
                recommendation="Ảnh đạt chuẩn" if not warnings else " ".join(warnings),
                applied_transforms=applied_transforms,
            )
            return processed_bytes, report

        except PreprocessorError as pe:
            return image_bytes, ImageQualityReport(
                is_blurry=True,
                blur_score=0.0,
                is_acceptable=False,
                warnings=[str(pe)],
                applied_transforms=[f"error: {str(pe)}"],
            )
        except Exception as e:
            return image_bytes, ImageQualityReport(
                is_blurry=False,
                blur_score=100.0,
                applied_transforms=[f"error_fallback: {str(e)}"],
            )


preprocessor = ImagePreprocessor()
