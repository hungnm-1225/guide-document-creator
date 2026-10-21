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
1. BẢO MẬT VÀ NGẮN GỌN TUYỆT ĐỐI CHO CÁC BƯỚC ĐĂNG NHẬP / FORM XÁC THỰC:
   - TUYỆT ĐỐI KHÔNG BAO GIỜ đọc hoặc in chuỗi username, email, mật khẩu thật hoặc chuỗi mã hóa ("hsdttemd", "Leanbot@2024", "••••••••") ra văn bản, slide hay lời thoại video!
   - ĐẶC BIỆT LƯU Ý VỀ ĐỘ DÀI VÀ NGỮ CẢNH: Các bước đăng nhập phải CỰC KỲ NGẮN GỌN, CHÍNH XÁC theo thao tác nguyên tử (atomic), TUYỆT ĐỐI KHÔNG văn vở, không phóng đại nói rằng "để bắt đầu truy cập vào hệ thống..." khi người dùng mới chỉ đang nhập tên tài khoản hoặc mật khẩu!
     * Thao tác nhập Tên tài khoản / Email:
       - title: "Nhập tên tài khoản hoặc email"
       - description: "Nhập tên tài khoản hoặc địa chỉ email của bạn vào ô đăng nhập."
       - slide_summary: "Nhập tên tài khoản hoặc email vào ô đăng nhập."
       - voiceover_script: "Đầu tiên, bạn nhập tên tài khoản hoặc email vào ô đăng nhập." (chỉ 1 câu ngắn gọn!)
     * Thao tác nhập Mật khẩu:
       - title: "Nhập mật khẩu"
       - description: "Nhập mật khẩu bảo mật của bạn vào ô Mật khẩu."
       - slide_summary: "Nhập mật khẩu vào ô Mật khẩu."
       - voiceover_script: "Tiếp theo, nhập mật khẩu của bạn vào ô Mật khẩu." (chỉ 1 câu ngắn gọn!)
     * Thao tác nhấn Đăng nhập (Submit Login):
       - title: "Nhấn nút Đăng nhập"
       - description: "Nhấn nút 'Đăng nhập' (Login) để xác thực và truy cập vào hệ thống."
       - slide_summary: "Nhấn nút Đăng nhập để vào hệ thống."
       - voiceover_script: "Sau đó, bạn nhấn nút Đăng nhập để truy cập vào hệ thống." (chỉ 1 câu ngắn gọn!)
2. LOẠI BỎ CÂU RÁC SÁO RỖNG:
   - TUYỆT ĐỐI KHÔNG sinh các câu như "Quan sát kỹ vị trí khung viền đỏ", "Nhìn vào ô màu đỏ", "Bấm vào khung viền". Hãy mô tả theo đúng tên nút và mục đích nghiệp vụ của phần mềm!
3. THUYẾT MINH CHI TIẾT & GIẢI THÍCH Ý NGHĨA CHỨC NĂNG (KHI ĐÃ VÀO PHÂN HỆ CHÍNH):
   - Chỉ khi thao tác là truy cập một chức năng bên trong hệ thống (Dashboard, Menu, Khóa học, Kho code, Cài đặt, v.v.):
   - Lúc này mới áp dụng GHI CHÚ NGƯỜI DÙNG: "{user_notes or 'Không có'}". Lời thoại và mô tả giải thích sâu sắc, tự nhiên:
     * Chức năng này tên là gì và có ý nghĩa, vai trò gì đối với người dùng?
     * Trên màn hình của chức năng đó đang có những thông tin, dữ liệu hay công cụ chính nào?
     * Hướng dẫn cách theo dõi và khai thác chức năng đó để công việc/học tập đạt hiệu quả cao nhất.

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
                title_vi = "Nhập mật khẩu"
                title_en = "Enter Password"
                desc_vi = "Nhập mật khẩu bảo mật vào ô Mật khẩu."
                desc_en = "Enter your password into the Password field."
                slide_vi = "Nhập mật khẩu vào ô Mật khẩu."
                slide_en = "Enter your password into the Password field."
                voice_vi = "Tiếp theo, nhập mật khẩu của bạn vào ô Mật khẩu."
                voice_en = "Next, enter your password into the password field."
            elif is_username:
                title_vi = "Nhập tên tài khoản hoặc email"
                title_en = "Enter Username or Email"
                desc_vi = "Nhập tên tài khoản hoặc địa chỉ email của bạn vào ô đăng nhập."
                desc_en = "Enter your username or email address into the login field."
                slide_vi = "Nhập tên tài khoản hoặc email vào ô đăng nhập."
                slide_en = "Enter your username or email into the login field."
                voice_vi = "Đầu tiên, bạn nhập tên tài khoản hoặc email vào ô đăng nhập."
                voice_en = "First, enter your username or email into the login field."
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
                title_vi = "Nhấn nút Đăng nhập"
                title_en = "Click Login"
                desc_vi = "Nhấn nút 'Đăng nhập' (Login) để xác thực và truy cập vào hệ thống."
                desc_en = "Click the 'Login' button to authenticate and access the system."
                slide_vi = "Nhấn nút Đăng nhập để vào hệ thống."
                slide_en = "Click Login to enter the system."
                voice_vi = "Sau đó, bạn nhấn nút Đăng nhập để truy cập vào hệ thống."
                voice_en = "Then, click the Login button to enter the system."
            elif "menu bên trái" in txt_lower:
                clean_name = target_name.replace("Mục ", "").replace(" trên Menu bên trái", "").strip()
                title_vi = f"Truy cập {clean_name} trên Menu bên trái"
                title_en = f"Access {clean_name} from Left Menu"
                desc_vi = f"Tại thanh điều hướng bên trái màn hình, nhấp vào mục '{clean_name}' để chuyển tới giao diện chức năng tương ứng."
                desc_en = f"On the left navigation menu, click on '{clean_name}' to navigate to this feature module."
                slide_vi = f"Mở chức năng: {clean_name} trên Menu bên trái."
                slide_en = f"Access: {clean_name} on left sidebar."
                voice_vi = f"Tại thanh điều hướng bên trái, bạn nhấp vào mục {clean_name} để khám phá các công cụ và nội dung của chức năng này."
                voice_en = f"From the left navigation menu, click on {clean_name} to explore this feature and its available tools."
            elif "khám phá chi tiết giao diện" in txt_lower or "giao diện" in txt_lower or "view" in txt_lower:
                clean_target = target_name.replace("Khám phá chi tiết giao diện ", "").split(":")[0].strip()
                title_vi = f"Khám phá giao diện {clean_target}"
                title_en = f"Explore {clean_target} Interface"
                desc_vi = f"Giao diện phân hệ '{clean_target}' hiển thị toàn bộ các thông tin tổng quan, dữ liệu nghiệp vụ và các công cụ tương tác chính. Người dùng có thể quan sát tiến độ, kiểm tra các hạng mục và thực hiện các tác vụ chuyên môn tại đây."
                desc_en = f"The '{clean_target}' interface presents an overview of core metrics, data items, and functional tools for users to track progress and complete workflows."
                slide_vi = f"Giao diện {clean_target}: Theo dõi dữ liệu & công cụ nghiệp vụ."
                slide_en = f"{clean_target} Interface: Overview and operational tools."
                voice_vi = f"Chào mừng bạn đến với phân hệ {clean_target}. Tại giao diện này, hệ thống cung cấp đầy đủ các thông tin và công cụ cần thiết để bạn quản lý công việc và thao tác hiệu quả."
                voice_en = f"Welcome to the {clean_target} module. Here you can explore all relevant data, tools, and options to manage your work effectively."
            elif "menu" in txt_lower or "thanh điều hướng" in txt_lower or "sidebar" in txt_lower:
                clean_name = target_name.replace("Mục ", "").replace(" trên Menu bên trái", "").replace(" trên thanh điều hướng", "").strip()
                title_vi = f"Truy cập phân hệ {clean_name}"
                title_en = f"Access {clean_name} Section"
                desc_vi = f"Trên thanh điều hướng của hệ thống, nhấp vào mục '{clean_name}' để chuyển tới khu vực làm việc tương ứng."
                desc_en = f"On the navigation bar, click on '{clean_name}' to access this workspace section."
                slide_vi = f"Mở chức năng: {clean_name} trên thanh điều hướng."
                slide_en = f"Open section: {clean_name}."
                voice_vi = f"Tại thanh điều hướng, bạn nhấp vào mục {clean_name} để truy cập vào khu vực chức năng này."
                voice_en = f"On the navigation bar, click on {clean_name} to enter this feature area."
            else:
                clean_name = target_name.replace("Nút ", "").replace("Mục ", "").strip()
                title_vi = f"Nhấp vào {clean_name}"
                title_en = f"Click on {clean_name}"
                desc_vi = f"Di chuyển con trỏ chuột đến '{clean_name}' trên giao diện và nhấn chuột trái để thực hiện tác vụ."
                desc_en = f"Move your cursor to '{clean_name}' and click to perform this action."
                slide_vi = f"Thao tác: {clean_name}."
                slide_en = f"Action: {clean_name}."
                voice_vi = f"Bây giờ, bạn nhấp vào mục {clean_name} trên màn hình để tiếp tục."
                voice_en = f"Now, click on {clean_name} on the screen to proceed."

        else:
            title_vi = f"Thao tác trên {target_name}"
            title_en = f"Interact with {target_name}"
            desc_vi = f"Thực hiện thao tác tiếp theo trên khu vực {target_name}."
            desc_en = f"Perform the next interaction on the {target_name} section."
            slide_vi = f"Thao tác: {target_name}"
            slide_en = f"Interact: {target_name}"
            voice_vi = f"Tại bước này, bạn thực hiện thao tác trên khu vực {target_name}."
            voice_en = f"At this step, interact with the {target_name} area."

        # Nếu có ghi chú từ người dùng, lồng ghép vào mô tả (CHỈ KHI ĐÃ VÀO PHÂN HỆ NGHIỆP VỤ, KHÔNG GẮN VÀO BƯỚC ĐĂNG NHẬP)
        is_auth_step = is_password or is_username or is_login_btn or act_type == "navigate"
        if user_notes and not is_auth_step:
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

    def plan_deep_directives(
        self,
        discovered_elements: List[Any],
        goal: str,
        user_notes: Optional[str] = None,
        page_title: str = "",
        page_url: str = ""
    ) -> List[Dict[str, Any]]:
        """
        AI Planner linh hoạt 100% (Universal Dynamic Planner):
        Dựa trên các phần tử DOM thực tế quét được trên màn hình, kết hợp với mục tiêu (goal)
        và ghi chú (user_notes) của người dùng để lập kế hoạch chuỗi bước đi tối ưu.
        Thích ứng với mọi website trong hệ sinh thái (LMS, Kho code, IDE, Workspace, Portal...).
        """
        # Chuẩn hóa danh sách phần tử
        normalized_elements = []
        for el in discovered_elements:
            if hasattr(el, "model_dump"):
                d = el.model_dump()
            elif isinstance(el, dict):
                d = el
            else:
                continue
            name = d.get("name") or d.get("text", "")
            if name and len(name.strip()) > 1:
                normalized_elements.append({
                    "name": name.strip(),
                    "tag": d.get("tag", ""),
                    "category": d.get("category", ""),
                    "selector": d.get("selector", ""),
                    "url_target": d.get("url_target")
                })

        if not self.client or not self.api_key:
            return self._semantic_fallback_plan(normalized_elements, goal, user_notes)

        compact_elements = normalized_elements[:45]
        prompt = f"""
Bạn là chuyên gia tự động hóa trình duyệt và kỹ sư thiết kế kịch bản hướng dẫn người dùng.
Website đang thao tác: "{page_title}" ({page_url})
Mục tiêu hướng dẫn của người dùng: "{goal}"
Ghi chú nghiệp vụ / Yêu cầu chi tiết từ người dùng: "{user_notes or 'Không có ghi chú thêm'}"

Danh sách các phần tử tương tác THỰC TẾ đang có trên giao diện website hiện tại:
{json.dumps(compact_elements, ensure_ascii=False, indent=2)}

Nhiệm vụ:
Phân tích kỹ lưỡng mục tiêu và các ghi chú của người dùng để chọn ra chuỗi các phần tử CÓ THỰC trên giao diện này cần tương tác.
- Nếu người dùng yêu cầu duyệt qua các chức năng / menu (ví dụ menu bên trái): Hãy chọn các mục menu có thật trên trang phù hợp với yêu cầu.
- Nếu người dùng yêu cầu thực hiện một quy trình cụ thể (như tạo repo, chỉnh sửa, xem báo cáo, mở bài học): Hãy chọn tuần tự các bước hợp lý để hoàn thành quy trình đó.

Yêu cầu xuất ra DUY NHẤT một mảng JSON các bước (tối đa 4 - 6 bước trọng tâm):
[
  {{
    "target_name": "Tên text chính xác của phần tử trên màn hình",
    "selector": "[data-guide-index='X'] chính xác từ danh sách phần tử được cung cấp",
    "action_type": "click",
    "purpose": "Mục đích thao tác hoặc nội dung muốn giới thiệu",
    "open_view_after": true
  }}
]
Chỉ trả về chuỗi JSON hợp lệ, không thêm văn bản giải thích.
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
            if isinstance(plan, list) and len(plan) > 0:
                return plan
        except Exception as e:
            console.print(f"[yellow]⚠️ Lỗi Gemini Planner ({e}), chuyển sang Semantic Fallback Planner...[/yellow]")

        return self._semantic_fallback_plan(normalized_elements, goal, user_notes)

    def _semantic_fallback_plan(
        self,
        discovered_elements: List[Dict[str, Any]],
        goal: str,
        user_notes: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Bộ lập kế hoạch ngữ nghĩa dự phòng (Universal Semantic Matcher):
        Không hardcode! Tự động trích xuất các từ khóa từ yêu cầu và ghi chú của người dùng,
        rồi so khớp động với các phần tử thực tế trên DOM.
        """
        combined = f"{goal} {user_notes or ''}".lower()
        import re
        quotes = re.findall(r'[\"“\'](.*?)[\"”\']', f"{goal} {user_notes or ''}")
        extracted_terms = [q.strip().lower() for q in quotes if len(q.strip()) > 1]

        # Tách các từ khóa có nghĩa từ prompt
        words = [w for w in re.split(r'[,.\s;:()]+', combined) if len(w) > 2 and w not in ["đăng", "nhập", "sau", "đó", "hướng", "dẫn", "các", "từng", "mở", "xem", "cho", "vào", "một", "nhiều"]]

        matched_elements = []
        seen_names = set()

        # 1. Khớp ưu tiên theo các từ khóa được trích xuất từ người dùng
        for el in discovered_elements:
            el_text = (el.get("name") or el.get("text") or "").strip()
            if not el_text or len(el_text) < 2:
                continue
            low = el_text.lower()
            if low in seen_names:
                continue

            # Ưu tiên khớp thực thể trong ngoặc kép
            is_quote_match = any(t in low for t in extracted_terms)
            # Khớp từ khóa người dùng nhắc tới
            is_word_match = any(w in low for w in words)

            if is_quote_match or is_word_match:
                seen_names.add(low)
                matched_elements.append({
                    "index": el.get("index"),
                    "target_name": el_text,
                    "action_type": "click",
                    "selector": el.get("selector") or f"[data-guide-index='{el.get('index', '')}']",
                    "purpose": f"Khám phá phân hệ {el_text}",
                    "open_view_after": True,
                    "priority": 1 if is_quote_match else 2
                })

        matched_elements.sort(key=lambda x: x.get("priority", 3))

        # 2. Nếu chưa đủ bước hoặc người dùng muốn duyệt navigation/sidebar
        if len(matched_elements) < 4:
            for el in discovered_elements:
                el_text = (el.get("name") or el.get("text") or "").strip()
                low = el_text.lower()
                if low in seen_names or len(el_text) < 2:
                    continue
                # Nhận diện phần tử điều hướng
                cat = el.get("category", "")
                if "menu" in cat.lower() or "sidebar" in cat.lower() or "điều hướng" in cat.lower():
                    seen_names.add(low)
                    matched_elements.append({
                        "index": el.get("index"),
                        "target_name": el_text,
                        "action_type": "click",
                        "selector": el.get("selector") or f"[data-guide-index='{el.get('index', '')}']",
                        "purpose": f"Truy cập chức năng {el_text}",
                        "open_view_after": True,
                        "priority": 3
                    })
                    if len(matched_elements) >= 6:
                        break

        # 3. Fallback lấy các phần tử đầu tiên nếu chưa có
        if not matched_elements:
            for el in discovered_elements[:4]:
                el_text = (el.get("name") or el.get("text") or "").strip()
                if el_text:
                    matched_elements.append({
                        "target_name": el_text,
                        "action_type": "click",
                        "selector": el.get("selector", ""),
                        "purpose": f"Thao tác trên {el_text}",
                        "open_view_after": True
                    })

        return matched_elements[:6]
