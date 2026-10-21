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
import time
import re
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
        self._last_request_time = 0.0
        self._init_client()

    def _wait_for_rate_limit(self, min_interval_seconds: float = 4.2):
        """
        Token Bucket / Pacing Guard:
        Hạn ngạch gói miễn phí (Free Tier) của Gemini là 15 RPM (15 requests per minute).
        Đảm bảo giữa 2 lượt gọi API luôn cách nhau tối thiểu 4.2 giây (tương đương tối đa ~14 RPM),
        triệt tiêu 100% việc dồn dập request dẫn đến lỗi 429 RESOURCE_EXHAUSTED.
        """
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < min_interval_seconds:
            sleep_needed = min_interval_seconds - elapsed
            time.sleep(sleep_needed)
        self._last_request_time = time.time()

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
        Có cơ chế Pacing Guard và Exponential Backoff tự động ngủ theo thời gian server yêu cầu khi chạm rate limit.
        """
        # Nếu chưa có API key hoặc client lỗi, dùng Smart Offline Fallback ngay
        if not self.client or not self.api_key:
            return self._smart_offline_step_analysis(action, total_steps, user_notes)

        # Áp dụng Pacing Guard (cách nhau tối thiểu 4.2s để không vượt 15 RPM)
        self._wait_for_rate_limit(min_interval_seconds=4.2)

        # Thử gọi API tối đa 4 lần với cơ chế xử lý Rate Limit 429 thông minh
        for attempt in range(1, 5):
            try:
                return self._call_gemini_multimodal(action, total_steps, guide_context, user_notes)
            except Exception as e:
                err_msg = str(e)
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    # Trích xuất số giây retry mà Google yêu cầu (ví dụ: 'Please retry in 37.42s')
                    retry_match = re.search(r"retry in ([0-9.]+)", err_msg, re.IGNORECASE)
                    wait_sec = float(retry_match.group(1)) + 2.0 if retry_match else (attempt * 15.0)
                    console.print(f"[yellow]⏳ [Rate Limit 15 RPM] Đang tạm dừng {wait_sec:.1f}s để hồi phục hạn ngạch Google Gemini (thử lại lần {attempt}/4)...[/yellow]")
                    time.sleep(wait_sec)
                    self._last_request_time = time.time()
                else:
                    console.print(f"[yellow]⚠️ Lỗi gọi Gemini: {e}. Sẽ thử lại sau 3s...[/yellow]")
                    time.sleep(3.0)

        console.print("[red]⚠️ Đã hết lượt thử lại Gemini API. Tự động chuyển sang Smart Offline Generator cho bước này.[/red]")
        return self._smart_offline_step_analysis(action, total_steps, user_notes)

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
3. THUYẾT MINH CHI TIẾT & PHÂN VAI RÕ RÀNG CHO BƯỚC CLICK VÀ BƯỚC VIEW:
   - Khi hành động là [CLICK] vào menu/phân hệ:
     * Lời thoại thuyết minh (voiceover_script) chỉ cần ngắn gọn, dứt khoát (khoảng 4-6 giây) thông báo điều hướng sang phân hệ đó.
     * TUYỆT ĐỐI KHÔNG giải thích dông dài toàn bộ chi tiết chức năng ở bước click!
   - Khi hành động là [CUSTOM] khám phá giao diện (view-...):
     * Đây là nơi tập trung thuyết minh sâu sắc, sinh động theo đúng Ghi Chú Nghiệp Vụ ("{user_notes or 'Không có'}"):
       - Tên phân hệ, ý nghĩa và vai trò cốt lõi.
       - Các khu vực dữ liệu, chỉ số thống kê hoặc các nút công cụ chính trên màn hình.
       - Cách người dùng khai thác phân hệ đó hiệu quả nhất.
4. ĐA DẠNG HÓA TỪ NỐI MỞ ĐẦU (CHỐNG LẶP TỪ TUYỆT ĐỐI):
   - TUYỆT ĐỐI KHÔNG mở đầu liên tục các câu bằng "Bây giờ, hãy...", "Bây giờ, chúng ta...", "Tiếp theo, bạn...".
   - Hãy luân phiên thay đổi các cách mở đầu tự nhiên, linh hoạt như một thuyết trình viên chuyên nghiệp:
     * "Tại giao diện này, ..."
     * "Để tiếp tục, bạn nhấn chọn ..."
     * "Quan sát trên thanh điều hướng, ..."
     * "Khu vực này mang đến cho bạn ..."
     * "Đối với phân hệ này, ..."
     * "Khi chuyển sang mục ..."

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

        elif act_type in ("click", "custom"):
            if is_login_btn:
                title_vi = "Nhấn nút Đăng nhập"
                title_en = "Click Login"
                desc_vi = "Nhấn nút 'Đăng nhập' (Login) để xác thực và truy cập vào hệ thống."
                desc_en = "Click the 'Login' button to authenticate and access the system."
                slide_vi = "Nhấn nút Đăng nhập để vào hệ thống."
                slide_en = "Click Login to enter the system."
                voice_vi = "Sau đó, bạn nhấn nút Đăng nhập để truy cập vào hệ thống."
                voice_en = "Then, click the Login button to enter the system."
            elif "khám phá chi tiết giao diện" in txt_lower or "khám phá phân hệ" in txt_lower or "giao diện" in txt_lower or "view" in txt_lower or act_type == "custom":
                clean_target = target_name.replace("Khám phá chi tiết giao diện ", "").replace("Khám phá phân hệ ", "").replace("Giao diện ", "").split(":")[0].strip()
                title_vi = f"Khám phá phân hệ {clean_target}"
                title_en = f"Explore {clean_target} Module"

                # Trích xuất nội dung thực tế phía sau dấu hai chấm (nếu có ngữ cảnh trinh sát)
                detail_context = ""
                if ":" in target_name:
                    detail_context = target_name.split(":", 1)[1].strip()

                # Đa dạng hóa lời thoại và mô tả theo số thứ tự bước (tránh lặp cụm từ máy móc)
                step_idx = action.step_number
                voice_variants_vi = [
                    f"Tại phân hệ {clean_target}, {detail_context if detail_context else 'bạn có thể theo dõi trực tiếp các thông tin chuyên môn, dữ liệu nghiệp vụ và các tính năng tương tác chính.'}",
                    f"Giao diện {clean_target} mang đến cho bạn không gian làm việc chuyên biệt, {detail_context if detail_context else 'hỗ trợ tra cứu chi tiết và thực hiện các thao tác một cách thuận tiện.'}",
                    f"Đối với khu vực {clean_target}, {detail_context if detail_context else 'hệ thống hiển thị rõ ràng các danh mục và công cụ cần thiết để bạn quản lý công việc và học tập.'}",
                    f"Khi mở phân hệ {clean_target}, {detail_context if detail_context else 'bạn dễ dàng nắm bắt được nội dung cụ thể, các chỉ số quan trọng và các tùy chọn thao tác.'}",
                    f"Khu vực {clean_target} cung cấp {detail_context if detail_context else 'toàn bộ tài nguyên và chức năng giúp người dùng khai thác tối đa tiện ích của hệ thống.'}",
                ]
                desc_variants_vi = [
                    f"Giao diện phân hệ '{clean_target}' hiển thị {detail_context if detail_context else 'các thông tin tổng quan, số liệu thống kê và công cụ nghiệp vụ giúp người dùng theo dõi và thao tác hiệu quả.'}",
                    f"Màn hình '{clean_target}' cho phép người dùng {detail_context if detail_context else 'tra cứu chi tiết các dữ liệu, tài liệu và thực hiện các tác vụ chuyên môn.'}",
                    f"Phân hệ '{clean_target}' cung cấp {detail_context if detail_context else 'các chức năng hỗ trợ chuyên sâu, giúp người dùng quản lý và nắm bắt thông tin nhanh chóng.'}",
                    f"Tại khu vực '{clean_target}', người dùng có thể {detail_context if detail_context else 'tương tác với các công cụ nghiệp vụ và theo dõi tiến độ một cách trực quan.'}",
                ]

                chosen_voice_vi = voice_variants_vi[step_idx % len(voice_variants_vi)]
                chosen_desc_vi = desc_variants_vi[step_idx % len(desc_variants_vi)]

                desc_vi = chosen_desc_vi
                desc_en = f"The '{clean_target}' interface displays comprehensive data metrics and functional tools for users to work efficiently."
                slide_vi = f"Phân hệ {clean_target}: Dữ liệu tổng quan & công cụ nghiệp vụ."
                slide_en = f"{clean_target} Module: Data overview and operational tools."
                voice_vi = chosen_voice_vi
                voice_en = f"In the {clean_target} section, the system provides a comprehensive overview and necessary tools to manage your tasks."
            elif "menu" in txt_lower or "thanh điều hướng" in txt_lower or "sidebar" in txt_lower or "mục" in txt_lower:
                clean_name = target_name.replace("Mục ", "").replace(" trên Menu bên trái", "").replace(" trên thanh điều hướng", "").replace(" trên giao diện", "").strip()
                title_vi = f"Truy cập phân hệ {clean_name}"
                title_en = f"Access {clean_name} Section"
                desc_vi = f"Nhấp vào '{clean_name}' trên thanh điều hướng để chuyển sang phân hệ này."
                desc_en = f"Click on '{clean_name}' in the navigation menu to switch to this section."
                slide_vi = f"Truy cập: {clean_name}."
                slide_en = f"Access: {clean_name}."
                voice_vi = f"Quan sát thanh điều hướng, bạn nhấn chọn mục {clean_name}."
                voice_en = f"Looking at the navigation menu, select {clean_name}."
            else:
                clean_name = target_name.replace("Nút ", "").replace("Mục ", "").strip()
                title_vi = f"Nhấp vào {clean_name}"
                title_en = f"Click on {clean_name}"
                desc_vi = f"Di chuyển con trỏ chuột đến '{clean_name}' và nhấn chuột trái."
                desc_en = f"Move cursor to '{clean_name}' and click."
                slide_vi = f"Thao tác: {clean_name}."
                slide_en = f"Action: {clean_name}."
                voice_vi = f"Để tiếp tục, bạn nhấn vào {clean_name} trên màn hình."
                voice_en = f"To proceed, click on {clean_name} on the screen."

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
        page_url: str = "",
        current_landing_module: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        AI Strategic Planner linh hoạt 100% (Universal Dynamic Strategic Planner):
        Dựa trên bản đồ địa hình thực tế (DOM + Layout) và trạng thái hiện tại (current_landing_module),
        kết hợp với mục tiêu (goal) và ghi chú (user_notes) của người dùng để lập lộ trình thông minh.
        - Triệt tiêu bước click thừa vào chính phân hệ đang đứng sẵn.
        - Hiểu rõ yêu cầu về vị trí không gian (ví dụ: 'menu bên trái', 'menu bên phải', 'thanh trên').
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
                    "index": d.get("index"),
                    "name": name.strip(),
                    "tag": d.get("tag", ""),
                    "category": d.get("category", ""),
                    "location": d.get("location", "center"),
                    "selector": d.get("selector", ""),
                    "is_expandable": d.get("is_expandable", False),
                    "url_target": d.get("url_target")
                })

        if not self.client or not self.api_key:
            return self._semantic_fallback_plan(normalized_elements, goal, user_notes, current_landing_module)

        compact_elements = normalized_elements[:45]
        prompt = f"""
Bạn là chuyên gia tự động hóa trình duyệt và kỹ sư thiết kế kịch bản hướng dẫn người dùng chuyên nghiệp.
Website đang thao tác: "{page_title}" ({page_url})
Mục tiêu hướng dẫn của người dùng: "{goal}"
Ghi chú nghiệp vụ / Yêu cầu chi tiết từ người dùng: "{user_notes or 'Không có ghi chú thêm'}"
PHÂN HỆ NGƯỜI DÙNG ĐANG ĐỨNG SẴN TRÊN MÀN HÌNH HIỆN TẠI: "{current_landing_module or 'Dashboard'}"

Danh sách các phần tử tương tác THỰC TẾ đã được AI Trinh Sát lập bản đồ trên giao diện:
{json.dumps(compact_elements, ensure_ascii=False, indent=2)}

QUY TẮC LẬP LỘ TRÌNH THÔNG MINH (BẮT BUỘC):
1. KHÔNG CLICK LẠI PHÂN HỆ ĐANG ĐỨNG SẴN:
   - Người dùng hiện tại ĐÃ ĐANG Ở SẴN phân hệ "{current_landing_module or 'Trang chủ'}".
   - TUYỆT ĐỐI KHÔNG lập bước click lại vào chính nút "{current_landing_module or 'Trang chủ'}" trên thanh điều hướng/menu vì thao tác này thừa thãi!
   - Hãy chọn các phân hệ TIẾP THEO trên menu để khám phá.
2. ĐÁP ỨNG ĐÚNG VỊ TRÍ KHÔNG GIAN ĐƯỢC YÊU CẦU:
   - Nếu người dùng yêu cầu tương tác tại khu vực nào (ví dụ "menu bên trái", "menu bên phải", "thanh trên", "thanh công cụ", "footer"...), AI PHẢI lọc đúng các phần tử có location và category tương ứng với khu vực đó.
3. KHÔNG ĐƯỢC NHẢY CÓC - ĐI TUẦN TỰ VÀ ĐẦY ĐỦ:
   - Khi người dùng yêu cầu "truy cập từng chức năng ở Menu" hoặc "giới thiệu các tính năng", bạn PHẢI lập danh sách tuần tự từ trên xuống dưới theo đúng thứ tự xuất hiện của các phần tử thực tế trên giao diện.
   - TUYỆT ĐỐI KHÔNG tự ý bỏ qua hoặc nhảy cóc các mục chức năng lớn xuất hiện trên thanh điều hướng. Bao quát đầy đủ toàn bộ các chức năng thuộc phạm vi người dùng yêu cầu.

Yêu cầu xuất ra DUY NHẤT một mảng JSON các bước:
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

        return self._semantic_fallback_plan(normalized_elements, goal, user_notes, current_landing_module)

    def _semantic_fallback_plan(
        self,
        discovered_elements: List[Dict[str, Any]],
        goal: str,
        user_notes: Optional[str] = None,
        current_landing_module: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Bộ lập kế hoạch ngữ nghĩa dự phòng (Universal Semantic Matcher):
        Không hardcode! Tự động trích xuất các từ khóa từ yêu cầu và ghi chú của người dùng,
        loại bỏ bước click vào chính phân hệ đang đứng sẵn, so khớp động với các phần tử thực tế trên DOM.
        """
        combined = f"{goal} {user_notes or ''}".lower()
        import re
        quotes = re.findall(r'[\"“\'](.*?)[\"”\']', f"{goal} {user_notes or ''}")
        extracted_terms = [q.strip().lower() for q in quotes if len(q.strip()) > 1]
        cur_landing = (current_landing_module or "dashboard").lower().strip()

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
            # BỎ QUA nếu trùng với phân hệ đang đứng sẵn (ví dụ đang ở sẵn Dashboard)
            if low == cur_landing or cur_landing in low:
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
                if low in seen_names or len(el_text) < 2 or low == cur_landing or cur_landing in low:
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
                    if len(matched_elements) >= 5:
                        break

        # 3. Fallback lấy các phần tử đầu tiên nếu chưa có
        if not matched_elements:
            for el in discovered_elements[:4]:
                el_text = (el.get("name") or el.get("text") or "").strip()
                if el_text and el_text.lower() != cur_landing:
                    matched_elements.append({
                        "target_name": el_text,
                        "action_type": "click",
                        "selector": el.get("selector", ""),
                        "purpose": f"Thao tác trên {el_text}",
                        "open_view_after": True
                    })

        return matched_elements[:5]
