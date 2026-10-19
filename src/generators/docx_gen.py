"""
DOCX & Markdown Document Generator
Tạo tài liệu hướng dẫn sử dụng định dạng Word (.docx) và Markdown (.md) chuẩn mực:
- Trang bìa và phần giới thiệu
- Bảng tóm tắt các bước
- Từng bước có Heading, mô tả chi tiết, ảnh minh họa có highlight
- Hộp mẹo (Callout Tips / Notes)
- Hỗ trợ xuất tiếng Việt (vi), tiếng Anh (en) hoặc song ngữ
"""
from pathlib import Path
from typing import List, Optional
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn

from config.settings import settings
from src.ai.schemas import GuideBook, ProcessedStep


class DocumentGenerator:
    """Generates professional user-guide DOCX documents and Markdown summaries"""

    def __init__(self):
        self.output_dir = settings.output_dir / "documents"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(self, guidebook: GuideBook) -> dict[str, str]:
        """Tạo cả bản tiếng Việt, tiếng Anh và Markdown"""
        vi_docx = self.generate_docx(guidebook, lang="vi")
        en_docx = self.generate_docx(guidebook, lang="en")
        vi_md = self.generate_markdown(guidebook, lang="vi")
        en_md = self.generate_markdown(guidebook, lang="en")

        return {
            "docx_vi": vi_docx,
            "docx_en": en_docx,
            "md_vi": vi_md,
            "md_en": en_md,
        }

    def generate_docx(self, guidebook: GuideBook, lang: str = "vi") -> str:
        """Sinh tài liệu Microsoft Word (.docx) chuyên nghiệp"""
        doc = docx.Document()

        # Thiết lập lề trang
        for section in doc.sections:
            section.top_margin = Inches(0.8)
            section.bottom_margin = Inches(0.8)
            section.left_margin = Inches(0.8)
            section.right_margin = Inches(0.8)

        # 1. Tiêu đề chính
        title_text = guidebook.title.vi if lang == "vi" else guidebook.title.en
        doc_title = doc.add_paragraph()
        doc_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_title = doc_title.add_run(f"HƯỚNG DẪN SỬ DỤNG: {title_text}" if lang == "vi" else f"USER GUIDE: {title_text}")
        run_title.font.name = "Arial"
        run_title.font.size = Pt(22)
        run_title.font.bold = True
        run_title.font.color.rgb = RGBColor(25, 42, 86)  # Dark Navy

        # 2. Metadata Box
        meta_p = doc.add_paragraph()
        meta_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        meta_run = meta_p.add_run(
            f"Hệ thống: {guidebook.target_url}  |  Ngày tạo: {guidebook.created_at}  |  Tổng số bước: {len(guidebook.steps)}"
            if lang == "vi" else
            f"Target System: {guidebook.target_url}  |  Created: {guidebook.created_at}  |  Total Steps: {len(guidebook.steps)}"
        )
        meta_run.font.size = Pt(9.5)
        meta_run.font.italic = True
        meta_run.font.color.rgb = RGBColor(100, 116, 139)

        doc.add_paragraph()  # khoảng cách

        # 3. Tóm tắt mục tiêu hướng dẫn
        summary_heading = doc.add_paragraph()
        run_sh = summary_heading.add_run("1. Tổng quan & Mục tiêu" if lang == "vi" else "1. Overview & Objectives")
        run_sh.font.size = Pt(14)
        run_sh.font.bold = True
        run_sh.font.color.rgb = RGBColor(30, 41, 59)

        summary_p = doc.add_paragraph()
        sum_text = guidebook.summary.vi if lang == "vi" else guidebook.summary.en
        run_sp = summary_p.add_run(sum_text)
        run_sp.font.size = Pt(11)

        # 4. Bảng danh mục các bước (Overview Table)
        doc.add_paragraph()
        tbl_heading = doc.add_paragraph()
        run_th = tbl_heading.add_run("2. Lộ trình thực hiện" if lang == "vi" else "2. Step Roadmap")
        run_th.font.size = Pt(14)
        run_th.font.bold = True
        run_th.font.color.rgb = RGBColor(30, 41, 59)

        table = doc.add_table(rows=1, cols=3)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        hdr_cells = table.rows[0].cells
        hdr_cells[0].text = "Bước" if lang == "vi" else "Step"
        hdr_cells[1].text = "Tiêu đề thao tác" if lang == "vi" else "Action Title"
        hdr_cells[2].text = "Mục tiêu" if lang == "vi" else "Target Element"
        
        # Style Header Row
        for cell in hdr_cells:
            shading = parse_xml(r'<w:shd {} w:fill="1E293B"/>'.format(nsdecls('w')))
            cell._tc.get_or_add_tcPr().append(shading)
            for p in cell.paragraphs:
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                for r in p.runs:
                    r.font.bold = True
                    r.font.color.rgb = RGBColor(255, 255, 255)
                    r.font.size = Pt(10)

        for step in guidebook.steps:
            row_cells = table.add_row().cells
            row_cells[0].text = f"#{step.step_number}"
            title_s = step.ai_content.title.vi if lang == "vi" else step.ai_content.title.en
            row_cells[1].text = title_s
            target_s = step.raw_action.element_text or step.raw_action.selector or "-"
            row_cells[2].text = target_s

        doc.add_paragraph()

        # 5. Chi tiết từng bước hướng dẫn (Detailed Steps with Highlighted Images)
        det_heading = doc.add_paragraph()
        run_dh = det_heading.add_run("3. Chi tiết các bước thực hiện" if lang == "vi" else "3. Detailed Step Instructions")
        run_dh.font.size = Pt(14)
        run_dh.font.bold = True
        run_dh.font.color.rgb = RGBColor(30, 41, 59)

        for step in guidebook.steps:
            # Tiêu đề bước
            step_title_text = step.ai_content.title.vi if lang == "vi" else step.ai_content.title.en
            sp = doc.add_paragraph()
            run_step_num = sp.add_run(f"Bước {step.step_number}: " if lang == "vi" else f"Step {step.step_number}: ")
            run_step_num.font.bold = True
            run_step_num.font.size = Pt(12)
            run_step_num.font.color.rgb = RGBColor(235, 35, 35)

            run_st = sp.add_run(step_title_text)
            run_st.font.bold = True
            run_st.font.size = Pt(12)
            run_st.font.color.rgb = RGBColor(15, 23, 42)

            # Mô tả chi tiết
            desc_text = step.ai_content.description.vi if lang == "vi" else step.ai_content.description.en
            dp = doc.add_paragraph()
            run_dp = dp.add_run(desc_text)
            run_dp.font.size = Pt(11)

            # Chèn ảnh đã highlight
            img_path = Path(step.annotated_screenshot_path)
            if img_path.exists():
                try:
                    img_p = doc.add_paragraph()
                    img_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    doc.add_picture(str(img_path), width=Inches(6.2))
                    # Chú thích ảnh
                    cap_p = doc.add_paragraph()
                    cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    cap_run = cap_p.add_run(
                        f"Hình {step.step_number}: Minh họa thao tác bước {step.step_number}"
                        if lang == "vi" else
                        f"Figure {step.step_number}: Illustration for step {step.step_number}"
                    )
                    cap_run.font.size = Pt(9)
                    cap_run.font.italic = True
                    cap_run.font.color.rgb = RGBColor(100, 116, 139)
                except Exception as e:
                    doc.add_paragraph(f"[Lỗi chèn ảnh: {e}]")

            # Mẹo hoặc lưu ý nghiệp vụ nếu có (Loại bỏ các câu sáo rỗng về khung viền đỏ)
            if step.ai_content.tip:
                tip_text = step.ai_content.tip.vi if lang == "vi" else step.ai_content.tip.en
                if tip_text and "viền đỏ" not in tip_text.lower() and "red highlight" not in tip_text.lower():
                    tip_p = doc.add_paragraph()
                    tip_run = tip_p.add_run(f"💡 Lưu ý: {tip_text}" if lang == "vi" else f"💡 Note: {tip_text}")
                    tip_run.font.size = Pt(9.5)
                    tip_run.font.italic = True
                    tip_run.font.color.rgb = RGBColor(180, 83, 9)  # Amber

            doc.add_paragraph()  # khoảng cách giữa các bước

        filename = f"guide_{lang}.docx"
        out_path = self.output_dir / filename
        doc.save(str(out_path))
        return str(out_path)

    def generate_markdown(self, guidebook: GuideBook, lang: str = "vi") -> str:
        """Sinh tài liệu Markdown (.md) chuẩn"""
        title = guidebook.title.vi if lang == "vi" else guidebook.title.en
        summary = guidebook.summary.vi if lang == "vi" else guidebook.summary.en
        lines = [
            f"# {title}",
            "",
            f"> **Hệ thống**: `{guidebook.target_url}` | **Thời gian**: {guidebook.created_at}",
            "",
            "## 1. Tổng quan" if lang == "vi" else "## 1. Overview",
            summary,
            "",
            "## 2. Các bước thực hiện" if lang == "vi" else "## 2. Step-by-Step Guide",
            "",
        ]

        for step in guidebook.steps:
            stitle = step.ai_content.title.vi if lang == "vi" else step.ai_content.title.en
            sdesc = step.ai_content.description.vi if lang == "vi" else step.ai_content.description.en
            lines.append(f"### Bước {step.step_number}: {stitle}" if lang == "vi" else f"### Step {step.step_number}: {stitle}")
            lines.append("")
            lines.append(sdesc)
            lines.append("")
            # Đường dẫn tương đối tới ảnh
            img_rel = Path(step.annotated_screenshot_path).name
            lines.append(f"![Bước {step.step_number}](../annotated_images/{img_rel})")
            lines.append("")
            if step.ai_content.tip:
                stip = step.ai_content.tip.vi if lang == "vi" else step.ai_content.tip.en
                if stip and "viền đỏ" not in stip.lower() and "red highlight" not in stip.lower():
                    lines.append(f"> 💡 **Lưu ý**: {stip}")
                    lines.append("")

        filename = f"guide_{lang}.md"
        out_path = self.output_dir / filename
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return str(out_path)
