"""
Gemini Multimodal AI Client
Xử lý hình ảnh screenshot + log hành động để sinh ra nội dung hướng dẫn người dùng:
- Model mặc định: gemini-3.5-flash-lite (Hạn ngạch cao nhất: 500 RPD miễn phí)
- Structured JSON Output chuẩn Pydantic
- Đồng thời sinh song ngữ Tiếng Việt (vi) và Tiếng Anh (en)
- Xử lý Rate-limit bằng Tenacity exponential backoff
- Fallback thông minh khi chưa có API key hoặc offline
"""
import os
import json
import base64
from pathlib import Path
from typing import Optional, List, Dict, Any
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from rich.console import Console
from PIL import Image

from config.settings import settings
from src.ai.schemas import (
    StepAIAnalysis,
    BilingualText,
    RecordedAction,
    ReconReport,
    BoundingBox,
)

console = Console()

# Kiểm tra thư viện Google GenAI
GENAI_SDK_AVAILABLE = False
try:
    from google import genai
    from google.genai import types
    GENAI_SDK_AVAILABLE = True
except ImportError:
    try:
        import google.generativeai as legacy_genai
    except ImportError:
        legacy_genai = None


class GeminiAnalyzer:
    """Multimodal Analyzer using Gemini 2.5 Flash for user guide generation"""

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self.api_key = api_key or settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        self.model_name = model_name or settings.gemini_model or "gemini-3.5-flash-lite"
        self.client = None
        self._init_client()

    def _init_client(self):
        """Khởi tạo Google GenAI Client nếu có API key"""
        if not self.api_key:
            console.print("[yellow]⚠️ Chưa thiết lập GEMINI_API_KEY. Sẽ tự động dùng Smart Offline Fallback Generator.[/yellow]")
            return

        try:
            if GENAI_SDK_AVAILABLE:
                self.client = genai.Client(api_key=self.api_key)
            else:
                legacy_genai.configure(api_key=self.api_key)
                self.client = "legacy"
            console.print(f"[green]✨ Gemini Client đã kích hoạt ({self.model_name})[/green]")
        except Exception as e:
            console.print(f"[yellow]⚠️ Không thể khởi tạo Gemini Client: {e}. Sẽ dùng Offline Fallback.[/yellow]")
            self.client = None

    def analyze_step(
        self,
        action: RecordedAction,
        total_steps: int = 1,
        guide_context: Optional[str] = None,
        user_notes: Optional[str] = None,
    ) -> StepAIAnalysis:
        """
        Phân tích multimodal: Ảnh chụp màn hình + Thông số hành động -> JSON song ngữ chuẩn
        Tự động bảo mật thông tin tài khoản/mật khẩu và áp dụng ghi chú nghiệp vụ của người dùng.
        """
        # Nếu chưa có API key hoặc client lỗi, dùng Smart Offline Fallback ngay
        if not self.client or not self.api_key:
            return self._smart_offline_step_analysis(action, total_steps, user_notes)

        try:
            return self._call_gemini_multimodal(action, total_steps, guide_context, user_notes)
        except Exception as e:
            console.print(f"[red]⚠️ Lỗi gọi Gemini API (sẽ fallback offline): {e}[/red]")
            return self._smart_offline_step_analysis(action, total_steps, user_notes)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=8),
        reraise=True,
    )
    def _call_gemini_multimodal(
        self,
        action: RecordedAction,
        total_steps: int,
        guide_context: Optional[str],
        user_notes: Optional[str] = None,
    ) -> StepAIAnalysis:

        prompt = f"""
Bạn là chuyên gia thiết kế tài liệu hướng dẫn sử dụng phần mềm (Technical Writer & Instructional Designer).
Hãy phân tích hình ảnh chụp màn hình kèm thông tin hành động của người dùng sau đây:

Thông tin hành động:
- Bước số: {action.step_number} / {total_steps}
- Loại thao tác: {action.action_type.upper()}
- Tên phần tử/Nút/Ô nhập: "{action.element_text}"
- Selector hoặc đối tượng: "{action.selector}"
- Tiêu đề trang web: "{action.page_title}"
- URL trang web: "{action.page_url}"
- Bounding Box mục tiêu (x, y, w, h): {action.bounding_box.model_dump() if action.bounding_box else 'Toàn màn hình / Navigate'}
- Bối cảnh toàn bộ hướng dẫn: {guide_context or 'Hướng dẫn thao tác hệ thống'}
- GHI CHÚ / YÊU CẦU NGHIỆP VỤ BỔ SUNG TỪ NGƯỜI DÙNG: "{user_notes or 'Không có ghi chú thêm'}"

QUY TẮC BẢO MẬT & CHUYÊN NGHIỆP TUYỆT ĐỐI (BẮT BUỘC):
1. BẢO MẬT TÀI KHOẢN & MẬT KHẨU:
   - TUYỆT ĐỐI KHÔNG BAO GIỜ đọc hoặc in chuỗi username, email, mật khẩu thật hoặc chuỗi mã hóa ("hsdttemd", "Leanbot@2024", "••••••••") ra văn bản, slide hay lời thoại video!
   - Nếu thao tác là nhập tài khoản/email: Hãy viết "Nhập tên tài khoản hoặc email của bạn vào ô đăng nhập".
   - Nếu thao tác là nhập mật khẩu: Hãy viết "Nhập mật khẩu bảo mật của bạn vào ô Mật khẩu".
2. LOẠI BỎ CÂU RÁC SÁO RỖNG:
   - TUYỆT ĐỐI KHÔNG sinh các câu như "Quan sát kỹ vị trí khung viền đỏ", "Nhìn vào ô màu đỏ", "Bấm vào khung viền". Hãy mô tả theo đúng tên nút và mục đích nghiệp vụ của phần mềm!
3. KẾT HỢP GHI CHÚ NGƯỜI DÙNG:
   - Nếu có Ghi chú/Yêu cầu bổ sung từ người dùng, hãy lồng ghép khéo léo vào kịch bản lời thoại và phần mô tả để video và tài liệu đạt độ chi tiết cao nhất.

Nhiệm vụ:
Tạo nội dung hướng dẫn cho bước này ở cả hai ngôn ngữ: Tiếng Việt (vi) và Tiếng Anh (en).
1. "title": Tiêu đề súc tích, chuyên nghiệp cho bước này (dưới 10 từ).
2. "description": Hướng dẫn chi tiết, rõ ràng cho tài liệu Word/Văn bản docx (nêu rõ click vào đâu, mục đích nghiệp vụ là gì).
3. "slide_summary": Câu tóm tắt cực ngắn gọn cho Slide trình chiếu (tối đa 1-2 câu, ít chữ, visual-first).
4. "voiceover_script": Lời thoại thuyết minh tự nhiên, truyền cảm, thân thiện cho video lồng tiếng AI (vi-VN và en-US).
5. "target_element_label": Tên ngắn gọn của nút/ô tương tác.
6. "tip": Mẹo nhỏ hữu ích về nghiệp vụ (nếu có, nếu không thì để null).

Yêu cầu xuất ra DUY NHẤT một chuỗi JSON hợp lệ theo định dạng:
{{
  "step_number": {action.step_number},
  "title": {{ "vi": "...", "en": "..." }},
  "description": {{ "vi": "...", "en": "..." }},
  "slide_summary": {{ "vi": "...", "en": "..." }},
  "voiceover_script": {{ "vi": "...", "en": "..." }},
  "target_element_label": {{ "vi": "...", "en": "..." }},
  "tip": null
}}
"""
        try:
            image_path = Path(action.raw_screenshot_path)
            if not image_path.exists():
                return self._smart_offline_step_analysis(action, total_steps)

            pil_img = Image.open(image_path)

            if GENAI_SDK_AVAILABLE and isinstance(self.client, genai.Client):
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=[prompt, pil_img],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )
                text_content = response.text
            else:
                # Legacy SDK fallback
                model = legacy_genai.GenerativeModel(self.model_name)
                response = model.generate_content([prompt, pil_img])
                text_content = response.text

            # Làm sạch JSON string nếu có bọc trong markdown ```json ... ```
            cleaned_json = text_content.strip()
            if cleaned_json.startswith("```json"):
                cleaned_json = cleaned_json[7:]
            if cleaned_json.startswith("```"):
                cleaned_json = cleaned_json[3:]
            if cleaned_json.endswith("```"):
                cleaned_json = cleaned_json[:-3]
            cleaned_json = cleaned_json.strip()

            parsed = json.loads(cleaned_json)
            return StepAIAnalysis(
                step_number=action.step_number,
                title=BilingualText(**parsed["title"]),
                description=BilingualText(**parsed["description"]),
                slide_summary=BilingualText(**parsed["slide_summary"]),
                voiceover_script=BilingualText(**parsed["voiceover_script"]),
                target_element_label=BilingualText(**parsed["target_element_label"]) if parsed.get("target_element_label") else None,
                tip=BilingualText(**parsed["tip"]) if parsed.get("tip") else None,
            )

        except Exception as e:
            console.print(f"[red]⚠️ Lỗi gọi Gemini API (sẽ fallback offline): {e}[/red]")
            return self._smart_offline_step_analysis(action, total_steps)

    def _smart_offline_step_analysis(self, action: RecordedAction, total_steps: int, user_notes: Optional[str] = None) -> StepAIAnalysis:
        """
        Trình tạo nội dung thông minh cục bộ:
        - Tuyệt đối không đọc username/password thật
        - Không sinh câu sáo rỗng về khung viền đỏ
        - Tự động nhận diện ngữ cảnh nghiệp vụ (đăng nhập, tìm kiếm, điều hướng)
        """
        act_type = action.action_type
        target_name = action.element_text or action.selector or "phần tử"
        sel_lower = (action.selector or "").lower()
        txt_lower = (action.element_text or "").lower()

        # Kiểm tra phần tử có phải là ô mật khẩu hoặc tài khoản hay không
        is_password = any(k in sel_lower or k in txt_lower for k in ["pass", "mật khẩu", "pwd"])
        is_username = any(k in sel_lower or k in txt_lower for k in ["user", "tài khoản", "email", "login"]) and not is_password
        is_login_btn = any(k in sel_lower or k in txt_lower for k in ["login", "đăng nhập", "sign in", "submit"]) and act_type == "click"

        if act_type == "navigate":
            title_vi = f"Truy cập vào hệ thống"
            title_en = f"Access the System"
            desc_vi = f"Mở trình duyệt và truy cập vào địa chỉ {action.page_url}. Đợi trang web tải hoàn tất giao diện."
            desc_en = f"Open your browser and navigate to {action.page_url}. Wait for the interface to fully load."
            slide_vi = f"Mở trình duyệt và truy cập: {action.page_url}"
            slide_en = f"Navigate to: {action.page_url}"
            voice_vi = f"Bước đầu tiên, các bạn mở trình duyệt và truy cập vào hệ thống tại địa chỉ {action.page_url}."
            voice_en = f"For the first step, open your browser and navigate to {action.page_url}."

        elif act_type == "fill":
            if is_password:
                title_vi = "Nhập mật khẩu tài khoản"
                title_en = "Enter Account Password"
                desc_vi = "Điền mật khẩu bảo mật của bạn vào ô Mật khẩu để xác thực quyền truy cập."
                desc_en = "Enter your secure account password into the Password field to authenticate."
                slide_vi = "Nhập mật khẩu bảo mật của tài khoản."
                slide_en = "Enter your secure account password."
                voice_vi = "Tiếp theo, bạn nhập mật khẩu bảo mật của mình vào ô Mật khẩu."
                voice_en = "Next, enter your secure password into the password field."
            elif is_username:
                title_vi = "Nhập tên tài khoản hoặc email"
                title_en = "Enter Username or Email"
                desc_vi = "Điền tên tài khoản hoặc địa chỉ email của bạn vào ô đăng nhập."
                desc_en = "Enter your registered username or email address into the login field."
                slide_vi = "Nhập tên tài khoản hoặc địa chỉ email."
                slide_en = "Enter your username or email address."
                voice_vi = "Bạn điền tên tài khoản hoặc địa chỉ email được cấp vào ô đăng nhập."
                voice_en = "Enter your registered username or email address into the input field."
            else:
                title_vi = f"Nhập thông tin {target_name}"
                title_en = f"Enter {target_name}"
                desc_vi = f"Tìm đến ô {target_name} trên màn hình và điền nội dung cần thiết."
                desc_en = f"Locate the {target_name} field on the screen and enter the required information."
                slide_vi = f"Điền thông tin vào ô {target_name}."
                slide_en = f"Enter information into {target_name}."
                voice_vi = f"Tiếp theo, bạn nhập thông tin cần thiết vào ô {target_name}."
                voice_en = f"Next, enter the required information into the {target_name} field."

        elif act_type == "click":
            if is_login_btn:
                title_vi = "Xác nhận Đăng nhập"
                title_en = "Submit Login"
                desc_vi = "Nhấn nút Đăng nhập để hệ thống xác thực tài khoản và chuyển hướng vào trang quản trị."
                desc_en = "Click the Login button to authenticate and proceed to your dashboard."
                slide_vi = "Nhấn nút Đăng nhập để truy cập hệ thống."
                slide_en = "Click Login to access the system."
                voice_vi = "Bây giờ, bạn bấm vào nút Đăng nhập để truy cập vào hệ thống."
                voice_en = "Now, click the Login button to access the system."
            elif "leanbot" in txt_lower or "khóa học" in txt_lower:
                c_name = target_name.replace("Khóa học ", "")
                title_vi = f"Truy cập khóa học {c_name}"
                title_en = f"Access Course {c_name}"
                desc_vi = f"Trong danh sách các khóa học đã đăng ký, nhấp vào {c_name} để mở nội dung học tập."
                desc_en = f"In your enrolled courses list, click on {c_name} to open the course curriculum."
                slide_vi = f"Truy cập khóa học: {c_name}."
                slide_en = f"Open course: {c_name}."
                voice_vi = f"Tại danh sách khóa học của bạn, bạn nhấp vào khóa học {c_name} để bắt đầu học tập."
                voice_en = f"In your enrolled courses list, click on {c_name} to begin learning."
            elif "course" in txt_lower:
                title_vi = "Chức năng Course (Nội dung bài học)"
                title_en = "Course Feature (Curriculum)"
                desc_vi = "Mục Course hiển thị toàn bộ lộ trình bài giảng, tài liệu hướng dẫn và bài tập thực hành theo từng học phần."
                desc_en = "The Course section displays all lecture units, learning guides, and practical exercises."
                slide_vi = "Khu vực bài học & tài liệu học tập (Course)."
                slide_en = "Course curriculum and study materials."
                voice_vi = "Mục Course là nơi hiển thị toàn bộ lộ trình bài giảng và tài liệu học tập của khóa học."
                voice_en = "The Course section presents the complete curriculum and lesson materials."
            elif "grades" in txt_lower:
                title_vi = "Chức năng Grades (Sổ điểm học tập)"
                title_en = "Grades Feature (Gradebook)"
                desc_vi = "Mục Grades cho phép học sinh theo dõi điểm số chi tiết và tiến độ hoàn thành các bài tập thực hành."
                desc_en = "The Grades tab allows students to monitor grades, assignments, and evaluation progress."
                slide_vi = "Theo dõi điểm số và kết quả đánh giá (Grades)."
                slide_en = "Track grades and evaluation results."
                voice_vi = "Mục Grades là sổ điểm điện tử, giúp học sinh kiểm tra điểm số và nhận xét từ giảng viên."
                voice_en = "The Grades tab serves as your gradebook to review scores and instructor feedback."
            elif "competencies" in txt_lower:
                title_vi = "Chức năng Competencies (Khung năng lực)"
                title_en = "Competencies Feature"
                desc_vi = "Mục Competencies hiển thị các chuẩn đầu ra và tiêu chí kỹ năng mà học viên đạt được qua từng bài học."
                desc_en = "The Competencies section displays skill standards and learning outcomes achieved."
                slide_vi = "Khung chuẩn năng lực học viên (Competencies)."
                slide_en = "Competencies and learning outcomes."
                voice_vi = "Mục Competencies hiển thị các chuẩn năng lực và kỹ năng thực tế mà bạn đạt được trong khóa học."
                voice_en = "The Competencies section outlines the practical skills and competencies acquired."
            elif "notes" in txt_lower:
                title_vi = "Chức năng Notes (Ghi chú học tập)"
                title_en = "Notes Feature"
                desc_vi = "Mục Notes cho phép ghi chép và lưu trữ các lưu ý quan trọng trong suốt quá trình học tập."
                desc_en = "The Notes tab lets students create and manage personal study notes."
                slide_vi = "Ghi chú và lưu trữ kiến thức (Notes)."
                slide_en = "Take and organize study notes."
                voice_vi = "Mục Notes cho phép bạn tạo và quản lý các ghi chú học tập cá nhân để ôn tập kiến thức."
                voice_en = "The Notes feature allows you to maintain personal notes for effective review."
            else:
                title_vi = f"Nhấn vào {target_name}"
                title_en = f"Click on {target_name}"
                desc_vi = f"Di chuyển con trỏ chuột đến mục {target_name} trên giao diện và nhấn chuột trái."
                desc_en = f"Move your cursor to the {target_name} element and click it."
                slide_vi = f"Bấm vào mục: {target_name}"
                slide_en = f"Click: {target_name}"
                voice_vi = f"Bây giờ, bạn bấm vào mục {target_name} trên màn hình."
                voice_en = f"Now, click on the {target_name} option on the screen."

        else:
            title_vi = f"Thao tác trên {target_name}"
            title_en = f"Interact with {target_name}"
            desc_vi = f"Thực hiện thao tác tiếp theo trên khu vực {target_name}."
            desc_en = f"Perform the next interaction on the {target_name} section."
            slide_vi = f"Thao tác: {target_name}"
            slide_en = f"Interact: {target_name}"
            voice_vi = f"Tại bước này, bạn thực hiện thao tác trên khu vực {target_name}."
            voice_en = f"At this step, interact with the {target_name} area."

        # Nếu có ghi chú từ người dùng, lồng ghép vào mô tả
        if user_notes:
            desc_vi += f" Lưu ý nghiệp vụ: {user_notes}."
            desc_en += f" Business note: {user_notes}."

        return StepAIAnalysis(
            step_number=action.step_number,
            title=BilingualText(vi=title_vi, en=title_en),
            description=BilingualText(vi=desc_vi, en=desc_en),
            slide_summary=BilingualText(vi=slide_vi, en=slide_en),
            voiceover_script=BilingualText(vi=voice_vi, en=voice_en),
            target_element_label=BilingualText(vi=target_name, en=target_name),
            tip=None
        )

    def plan_steps_from_recon(self, recon: ReconReport, user_goal: str) -> List[Dict[str, Any]]:
        """
        Dựa trên khảo sát thực tế (ReconReport) và mục tiêu người dùng,
        Gemini lập danh sách các bước thao tác cụ thể (không đoán mò).
        """
        if not self.client or not self.api_key:
            return self._fallback_plan(recon, user_goal)

        prompt = f"""
Bạn là chuyên gia tự động hóa trình duyệt và kỹ sư tài liệu.
Chúng tôi vừa thực hiện khảo sát địa hình thực tế của trang web:
- URL: {recon.url}
- Tiêu đề trang: {recon.title}
- Các khu vực phát hiện được: {', '.join(recon.detected_sections)}
- Danh sách các phần tử tương tác khả dụng trên màn hình (đã quét thực tế):
{json.dumps([{'tag': el.tag, 'text': el.text, 'selector': el.selector, 'role': el.role, 'placeholder': el.placeholder} for el in recon.discovered_elements[:25]], ensure_ascii=False, indent=2)}

Mục tiêu của người dùng: "{user_goal}"

Dựa vào các phần tử THỰC TẾ có trên trang web ở trên (KHÔNG ĐƯỢC ĐOÁN MÒ các nút không tồn tại):
Hãy vạch ra kịch bản thao tác từng bước gồm mảng JSON các object theo schema:
[
  {{
    "action": "click" | "fill" | "navigate" | "select" | "wait",
    "target": "selector hoặc text chính xác của phần tử",
    "value": "giá trị nhập vào nếu là fill",
    "label": "Mô tả ngắn gọn hành động"
  }}
]
Chỉ xuất ra JSON, không thêm văn bản khác.
"""
        try:
            if GENAI_SDK_AVAILABLE and isinstance(self.client, genai.Client):
                res = self.client.models.generate_content(
                    model=self.model_name,
                    contents=[prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
                text = res.text
            else:
                model = legacy_genai.GenerativeModel(self.model_name)
                res = model.generate_content([prompt])
                text = res.text

            cleaned = text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            plan = json.loads(cleaned.strip())
            return plan
        except Exception as e:
            console.print(f"[yellow]⚠️ Lỗi lập kế hoạch Gemini: {e}. Sẽ dùng fallback.[/yellow]")
            return self._fallback_plan(recon, user_goal)

    def _fallback_plan(self, recon: ReconReport, user_goal: str) -> List[Dict[str, Any]]:
        """Kế hoạch dự phòng dựa trên các element đầu tiên tìm thấy"""
        steps = [{"action": "navigate", "target": recon.url, "label": "Mở trang web"}]
        # Lọc các nút hoặc input khả dụng
        for el in recon.discovered_elements[:4]:
            if el.tag == "input":
                steps.append({
                    "action": "fill",
                    "target": el.selector,
                    "value": "sample",
                    "label": f"Điền thông tin vào {el.placeholder or el.text or 'input'}"
                })
            elif el.tag in ("button", "a"):
                steps.append({
                    "action": "click",
                    "target": el.selector,
                    "label": f"Nhấn vào {el.text or 'nút'}"
                })
        return steps
