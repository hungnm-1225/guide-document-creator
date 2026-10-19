"""
Visual Annotator Module (Open-Design & OpenMontage Inspired)
Chịu trách nhiệm vẽ các lớp phủ thị giác chuyên nghiệp lên ảnh chụp màn hình:
1. Spotlight Effect: Làm tối nhẹ phần xung quanh, giữ sáng nổi bật phần tử cần thao tác.
2. Bounding Box: Khung viền đỏ bo tròn hiện đại, độ tương phản cao trên mọi nền.
3. Step Badge: Huy hiệu tròn ghi số bước ở góc phần tử.
4. Cursor Pointer & Click Ripple: Biểu tượng trỏ chuột / click chân thực.
"""
import math
from pathlib import Path
from typing import Optional, Tuple
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from config.settings import settings, VisualTheme
from src.ai.schemas import BoundingBox


class VisualAnnotator:
    """Renders professional overlays, spotlights and indicators onto screenshots"""

    def __init__(self, theme: Optional[VisualTheme] = None):
        self.theme = theme or settings.theme
        self.output_dir = settings.output_dir / "annotated_images"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def annotate_screenshot(
        self,
        raw_image_path: str,
        bounding_box: Optional[BoundingBox],
        step_number: int,
        output_filename: Optional[str] = None,
        draw_spotlight: bool = True,
        draw_badge: bool = True,
        draw_cursor: bool = True,
    ) -> str:
        """
        Vẽ highlight lên ảnh màn hình raw và lưu thành file mới.
        Trả về đường dẫn file ảnh đã được highlight (step_X_highlighted.png).
        """
        raw_path = Path(raw_image_path)
        if not raw_path.exists():
            raise FileNotFoundError(f"Không tìm thấy file ảnh gốc: {raw_image_path}")

        if output_filename is None:
            output_filename = f"step_{step_number:02d}_highlighted.png"
        out_path = self.output_dir / output_filename

        # Đọc ảnh gốc bằng OpenCV
        img_bgr = cv2.imread(str(raw_path))
        if img_bgr is None:
            # Fallback PIL nếu cv2 đọc lỗi
            pil_img = Image.open(str(raw_path)).convert("RGB")
            img_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

        h_img, w_img = img_bgr.shape[:2]

        # Nếu không có bounding box (ví dụ bước navigate toàn trang) -> chỉ vẽ watermark hoặc giữ nguyên
        if not bounding_box:
            cv2.imwrite(str(out_path), img_bgr)
            return str(out_path)

        # Tính toán tọa độ giới hạn hợp lệ trong kích thước ảnh
        pad = self.theme.box_padding
        x1 = max(0, int(bounding_box.x - pad))
        y1 = max(0, int(bounding_box.y - pad))
        x2 = min(w_img - 1, int(bounding_box.x + bounding_box.w + pad))
        y2 = min(h_img - 1, int(bounding_box.y + bounding_box.h + pad))

        # 1. Hiệu ứng Spotlight (Làm tối xung quanh, tập trung thị giác)
        if draw_spotlight:
            img_bgr = self._apply_spotlight(img_bgr, x1, y1, x2, y2)

        # Chuyển sang PIL để vẽ vector mượt mà (anti-aliasing, bo góc, text đẹp)
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        pil_canvas = Image.fromarray(img_rgb)
        draw = ImageDraw.Draw(pil_canvas, "RGBA")

        # 2. Khung viền đỏ bo góc (Rounded Bounding Box)
        border_col = (235, 35, 35, 255)       # Red
        border_thick = self.theme.box_border_thickness
        radius = self.theme.box_corner_radius

        # Vẽ viền ngoài phát sáng/tương phản (Outer white contour)
        draw.rounded_rectangle(
            [x1 - 1, y1 - 1, x2 + 1, y2 + 1],
            radius=radius + 1,
            outline=(255, 255, 255, 200),
            width=border_thick + 2
        )
        # Vẽ viền đỏ chính
        draw.rounded_rectangle(
            [x1, y1, x2, y2],
            radius=radius,
            outline=border_col,
            width=border_thick
        )

        # 3. Huy hiệu số bước (Step Number Badge)
        if draw_badge:
            self._draw_step_badge(draw, x1, y1, step_number)

        # 4. Con trỏ chuột (Mouse Cursor pointer)
        if draw_cursor:
            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2
            self._draw_mouse_cursor(draw, center_x, center_y)

        # Lưu ảnh hoàn thiện
        pil_canvas.save(str(out_path), quality=95)
        return str(out_path)

    def _apply_spotlight(
        self, img_bgr: np.ndarray, x1: int, y1: int, x2: int, y2: int
    ) -> np.ndarray:
        """Làm tối nền xung quanh ngoại trừ ô mục tiêu"""
        overlay = img_bgr.copy()
        dark_dim = (overlay * (1.0 - self.theme.spotlight_opacity)).astype(np.uint8)
        # Khôi phục vùng spotlight sáng rõ
        dark_dim[y1:y2, x1:x2] = img_bgr[y1:y2, x1:x2]
        return dark_dim

    def _draw_step_badge(self, draw: ImageDraw.ImageDraw, x1: int, y1: int, step_num: int):
        """Vẽ hình tròn màu đỏ chứa số bước tại góc trên của bounding box"""
        r = self.theme.badge_radius
        cx = max(r + 4, x1 - 6)
        cy = max(r + 4, y1 - 6)

        # Vẽ bóng mờ phía dưới huy hiệu (Drop shadow)
        draw.ellipse([cx - r + 2, cy - r + 3, cx + r + 2, cy + r + 3], fill=(0, 0, 0, 100))
        # Viền trắng bên ngoài
        draw.ellipse([cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2], fill=(255, 255, 255, 255))
        # Nền đỏ chính
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(235, 35, 35, 255))

        # Text số bước
        text = str(step_num)
        font = None
        font_size = 20
        # Thử lấy font hệ thống Windows
        font_paths = [
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/tahoma.ttf",
        ]
        for fp in font_paths:
            if Path(fp).exists():
                try:
                    font = ImageFont.truetype(fp, font_size)
                    break
                except Exception:
                    pass

        if not font:
            font = ImageFont.load_default()

        # Căn giữa số bước
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text((cx - tw / 2, cy - th / 2 - 1), text, fill=(255, 255, 255, 255), font=font)

    def _draw_mouse_cursor(self, draw: ImageDraw.ImageDraw, cx: int, cy: int):
        """Vẽ biểu tượng con trỏ chuột trỏ trực tiếp vào tâm nút thao tác"""
        # Tọa độ hình mũi tên con trỏ chuột phong cách Mac/Windows hiện đại
        cursor_points = [
            (cx, cy),
            (cx, cy + 24),
            (cx + 6, cy + 18),
            (cx + 12, cy + 26),
            (cx + 16, cy + 23),
            (cx + 10, cy + 15),
            (cx + 18, cy + 15),
        ]
        # Viền đen của chuột
        draw.polygon(cursor_points, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
        # Vòng sóng click ripple nhẹ tại đỉnh chuột
        draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], outline=(235, 35, 35, 220), width=2)
