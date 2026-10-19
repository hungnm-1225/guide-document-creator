"""
PPTX Slide Generator (Open-Design Inspired)
Tạo bài thuyết trình Slides PowerPoint (.pptx) chuẩn 16:9 hiện đại:
- Nguyên tắc: Maximize whitespace, minimize text, visual-first
- Cột trái: Số bước lớn + Tiêu đề + 1 câu tóm tắt cốt lõi
- Cột phải: Ảnh screenshot highlight kích thước lớn, chiếm phần lớn thị giác
- Hỗ trợ xuất tiếng Việt (vi) và tiếng Anh (en)
"""
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

from config.settings import settings
from src.ai.schemas import GuideBook, ProcessedStep


class SlideGenerator:
    """Generates clean, modern 16:9 widescreen presentation slides"""

    def __init__(self):
        self.output_dir = settings.output_dir / "slides"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(self, guidebook: GuideBook) -> dict[str, str]:
        """Tạo cả bản slide tiếng Việt và tiếng Anh"""
        vi_slides = self.generate_pptx(guidebook, lang="vi")
        en_slides = self.generate_pptx(guidebook, lang="en")
        return {"slides_vi": vi_slides, "slides_en": en_slides}

    def generate_pptx(self, guidebook: GuideBook, lang: str = "vi") -> str:
        """Tạo file Presentation .pptx tỉ lệ 16:9"""
        prs = Presentation()
        # Thiết lập kích thước 16:9 widescreen (13.333 x 7.5 inches)
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)
        blank_slide_layout = prs.slide_layouts[6]  # Blank layout

        # 1. Slide Bìa (Cover Slide)
        self._create_cover_slide(prs, blank_slide_layout, guidebook, lang)

        # 2. Các Slide từng bước (Step Slides)
        for step in guidebook.steps:
            self._create_step_slide(prs, blank_slide_layout, step, len(guidebook.steps), guidebook.target_url, lang)

        # 3. Slide Kết thúc (Summary Slide)
        self._create_outro_slide(prs, blank_slide_layout, guidebook, lang)

        gid = getattr(guidebook, "guide_id", None) or datetime.now().strftime("%Y%m%d_%H%M%S")
        versioned_filename = f"slides_{lang}_{gid}.pptx"
        out_path = self.output_dir / versioned_filename
        prs.save(str(out_path))

        # Lưu thêm bản latest để xem nhanh
        latest_path = self.output_dir / f"slides_{lang}.pptx"
        try:
            shutil.copyfile(str(out_path), str(latest_path))
        except Exception:
            pass

        return str(out_path)

    def _create_cover_slide(self, prs, layout, guidebook: GuideBook, lang: str):
        """Slide mở đầu phong cách Modern Minimalist"""
        slide = prs.slides.add_slide(layout)

        # Nền màu xám trắng rất nhẹ
        bg_shape = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5)
        )
        bg_shape.fill.solid()
        bg_shape.fill.fore_color.rgb = RGBColor(248, 250, 252)  # Slate-50
        bg_shape.line.fill.background()

        # Dải viền đỏ thương hiệu ở cạnh trái
        accent = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.4), Inches(7.5)
        )
        accent.fill.solid()
        accent.fill.fore_color.rgb = RGBColor(235, 35, 35)
        accent.line.fill.background()

        # Badge "HƯỚNG DẪN THỰC HÀNH / USER GUIDE"
        badge_box = slide.shapes.add_textbox(Inches(1.5), Inches(1.8), Inches(6.0), Inches(0.5))
        tf_badge = badge_box.text_frame
        p_badge = tf_badge.paragraphs[0]
        p_badge.text = "TÀI LIỆU HƯỚNG DẪN TRỰC QUAN" if lang == "vi" else "VISUAL USER GUIDE"
        p_badge.font.name = "Arial"
        p_badge.font.size = Pt(12)
        p_badge.font.bold = True
        p_badge.font.color.rgb = RGBColor(235, 35, 35)

        # Tiêu đề lớn
        title_box = slide.shapes.add_textbox(Inches(1.5), Inches(2.4), Inches(10.5), Inches(2.0))
        tf_title = title_box.text_frame
        tf_title.word_wrap = True
        p_title = tf_title.paragraphs[0]
        p_title.text = guidebook.title.vi if lang == "vi" else guidebook.title.en
        p_title.font.name = "Arial"
        p_title.font.size = Pt(36)
        p_title.font.bold = True
        p_title.font.color.rgb = RGBColor(15, 23, 42)

        # Mô tả ngắn
        desc_box = slide.shapes.add_textbox(Inches(1.5), Inches(4.5), Inches(10.0), Inches(1.2))
        tf_desc = desc_box.text_frame
        tf_desc.word_wrap = True
        p_desc = tf_desc.paragraphs[0]
        p_desc.text = guidebook.summary.vi if lang == "vi" else guidebook.summary.en
        p_desc.font.name = "Arial"
        p_desc.font.size = Pt(16)
        p_desc.font.color.rgb = RGBColor(71, 85, 105)

        # Footer thông tin
        foot_box = slide.shapes.add_textbox(Inches(1.5), Inches(6.2), Inches(10.0), Inches(0.5))
        tf_foot = foot_box.text_frame
        p_foot = tf_foot.paragraphs[0]
        p_foot.text = f"URL: {guidebook.target_url}  |  {len(guidebook.steps)} bước thực hiện" if lang == "vi" else f"URL: {guidebook.target_url}  |  {len(guidebook.steps)} Steps"
        p_foot.font.size = Pt(11)
        p_foot.font.color.rgb = RGBColor(148, 163, 184)

    def _create_step_slide(self, prs, layout, step: ProcessedStep, total_steps: int, url: str, lang: str):
        """Slide từng bước: Cột trái chữ ngắn gọn, Cột phải ảnh lớn trực quan"""
        slide = prs.slides.add_slide(layout)

        # CỘT TRÁI (LEFT COLUMN): Text & Thông tin bước (X: 0.8 -> 4.5 inches)
        # Số bước to hiện đại (01, 02...)
        step_num_str = f"{step.step_number:02d}"
        badge_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.8), Inches(3.6), Inches(1.0))
        tf_badge = badge_box.text_frame
        p_badge = tf_badge.paragraphs[0]
        p_badge.text = f"BƯỚC {step_num_str} / {total_steps:02d}" if lang == "vi" else f"STEP {step_num_str} / {total_steps:02d}"
        p_badge.font.name = "Arial"
        p_badge.font.size = Pt(13)
        p_badge.font.bold = True
        p_badge.font.color.rgb = RGBColor(235, 35, 35)

        # Tiêu đề bước
        title_box = slide.shapes.add_textbox(Inches(0.8), Inches(1.6), Inches(4.0), Inches(1.8))
        tf_title = title_box.text_frame
        tf_title.word_wrap = True
        p_title = tf_title.paragraphs[0]
        p_title.text = step.ai_content.title.vi if lang == "vi" else step.ai_content.title.en
        p_title.font.name = "Arial"
        p_title.font.size = Pt(26)
        p_title.font.bold = True
        p_title.font.color.rgb = RGBColor(15, 23, 42)

        # Nội dung tóm tắt ngắn (Slide Summary: 1-2 câu, ít chữ)
        summary_box = slide.shapes.add_textbox(Inches(0.8), Inches(3.4), Inches(4.0), Inches(2.2))
        tf_sum = summary_box.text_frame
        tf_sum.word_wrap = True
        p_sum = tf_sum.paragraphs[0]
        p_sum.text = step.ai_content.slide_summary.vi if lang == "vi" else step.ai_content.slide_summary.en
        p_sum.font.name = "Arial"
        p_sum.font.size = Pt(15)
        p_sum.font.color.rgb = RGBColor(51, 65, 85)

        # CỘT PHẢI (RIGHT COLUMN): Hình ảnh chụp màn hình lớn chiếm trung tâm (X: 5.0 -> 12.8 inches)
        img_path = Path(step.annotated_screenshot_path)
        if img_path.exists():
            try:
                # Kích thước ảnh: Chiều rộng ~7.5 inches, tự tính chiều cao tương ứng
                img_left = Inches(5.1)
                img_top = Inches(0.8)
                img_width = Inches(7.5)
                slide.shapes.add_picture(str(img_path), img_left, img_top, width=img_width)
            except Exception as e:
                err_box = slide.shapes.add_textbox(Inches(5.1), Inches(3.0), Inches(6.0), Inches(1.0))
                err_box.text_frame.text = f"[Ảnh không thể hiển thị: {e}]"

    def _create_outro_slide(self, prs, layout, guidebook: GuideBook, lang: str):
        """Slide kết thúc"""
        slide = prs.slides.add_slide(layout)

        box = slide.shapes.add_textbox(Inches(2.0), Inches(2.8), Inches(9.333), Inches(2.0))
        tf = box.text_frame
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        p.text = "HOÀN TẤT HƯỚNG DẪN!" if lang == "vi" else "GUIDE COMPLETED!"
        p.font.name = "Arial"
        p.font.size = Pt(36)
        p.font.bold = True
        p.font.color.rgb = RGBColor(235, 35, 35)

        p2 = tf.add_paragraph()
        p2.alignment = PP_ALIGN.CENTER
        p2.text = (
            f"Bạn đã hoàn thành toàn bộ các bước thao tác trên hệ thống {guidebook.target_url}."
            if lang == "vi" else
            f"You have successfully walked through all steps on {guidebook.target_url}."
        )
        p2.font.size = Pt(16)
        p2.font.color.rgb = RGBColor(71, 85, 105)
