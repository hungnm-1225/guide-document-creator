"""
Gemini Multimodal AI Client
Xử lý hình ảnh screenshot + log hành động để sinh ra nội dung hướng dẫn người dùng:
- Model mặc định: gemini-3.5-flash-lite (Hạn ngạch cao nhất: 500 RPD miễn phí)
- Structured JSON Output chuẩn Pydantic
- Đồng thời sinh song ngữ Tiếng Việt (vi) và Tiếng Anh (en)
- Xử lý Rate-limit: pacing + retry đúng thời gian server yêu cầu (không nuốt lỗi 429)
- Fallback offline CÓ ĐÁNH DẤU (is_fallback) để giao diện báo rõ cho người dùng

Hai vai trò chính:
1. plan_flow(): Lượt 1 (Trinh Sát) — từ bản đồ trang thực tế + Mục tiêu + Ghi chú -> Kịch bản (FlowPlan)
2. analyze_step(): Lượt 2 (Viết Hướng Dẫn) — ảnh + nội dung trang + mục đích bước trong Kịch bản -> nội dung song ngữ
"""
import os
import json
import time
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from rich.console import Console
from PIL import Image

from config.settings import settings
from src.ai.schemas import (
    StepAIAnalysis,
    BilingualText,
    RecordedAction,
    ReconReport,
    SiteKnowledgeMap,
    FlowPlan,
    FlowStep,
)

console = Console()

# Kiểm tra thư viện Google GenAI
GENAI_SDK_AVAILABLE = False
legacy_genai = None
try:
    from google import genai
    from google.genai import types
    GENAI_SDK_AVAILABLE = True
except ImportError:
    try:
        import google.generativeai as legacy_genai
    except ImportError:
        legacy_genai = None


class GeminiUnavailable(Exception):
    """Gemini không trả về kết quả hợp lệ sau khi đã thử lại"""


def _clean_json_text(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _bi(obj: Any, fallback: str = "") -> BilingualText:
    """Chuyển dict {'vi','en'} (hoặc chuỗi) thành BilingualText an toàn"""
    if isinstance(obj, dict):
        vi = str(obj.get("vi") or obj.get("en") or fallback)
        en = str(obj.get("en") or obj.get("vi") or fallback)
        return BilingualText(vi=vi, en=en)
    if isinstance(obj, str) and obj:
        return BilingualText(vi=obj, en=obj)
    return BilingualText(vi=fallback, en=fallback)


class GeminiAnalyzer:
    """Multimodal Analyzer dùng Gemini để lập Kịch bản và viết nội dung hướng dẫn"""

    MAX_ATTEMPTS = 4

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self.api_key = api_key or settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
        self.model_name = model_name or settings.gemini_model or "gemini-3.5-flash-lite"
        self.client = None
        self._last_request_time = 0.0
        self.daily_quota_exhausted = False
        self.last_error: Optional[str] = None
        self._init_client()

    # ------------------------------------------------------------------
    # Hạ tầng gọi API
    # ------------------------------------------------------------------
    def _wait_for_rate_limit(self, min_interval_seconds: float = 4.2):
        """Pacing Guard: giữ tối đa ~14 request/phút cho gói miễn phí 15 RPM"""
        elapsed = time.time() - self._last_request_time
        if elapsed < min_interval_seconds:
            time.sleep(min_interval_seconds - elapsed)
        self._last_request_time = time.time()

    def _init_client(self):
        if not self.api_key:
            console.print("[yellow]⚠️ Chưa thiết lập GEMINI_API_KEY. Nội dung sẽ dùng bộ dự phòng offline (chất lượng thấp).[/yellow]")
            return
        try:
            if GENAI_SDK_AVAILABLE:
                self.client = genai.Client(api_key=self.api_key)
            elif legacy_genai is not None:
                legacy_genai.configure(api_key=self.api_key)
                self.client = "legacy"
            console.print(f"[green]✨ Gemini Client đã kích hoạt ({self.model_name})[/green]")
        except Exception as e:
            console.print(f"[yellow]⚠️ Không thể khởi tạo Gemini Client: {e}.[/yellow]")
            self.client = None

    @property
    def available(self) -> bool:
        return bool(self.client and self.api_key and not self.daily_quota_exhausted)

    def _raw_generate(self, contents: List[Any], temperature: float) -> str:
        if GENAI_SDK_AVAILABLE and not isinstance(self.client, str):
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=temperature,
                ),
            )
            return response.text
        model = legacy_genai.GenerativeModel(self.model_name)
        response = model.generate_content(contents)
        return response.text

    def _generate_json(self, prompt: str, image_path: Optional[str] = None, temperature: float = 0.3, label: str = "") -> Any:
        """
        Gọi Gemini và parse JSON. Thử lại khi gặp 429 (chờ đúng số giây server yêu cầu)
        hoặc JSON hỏng. Ném GeminiUnavailable nếu vẫn thất bại — KHÔNG tự âm thầm thay nội dung.
        """
        if not self.available:
            raise GeminiUnavailable(self.last_error or "Gemini chưa sẵn sàng (thiếu API key hoặc đã hết hạn ngạch ngày)")

        contents: List[Any] = [prompt]
        if image_path and Path(image_path).exists():
            contents.append(Image.open(image_path))

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            self._wait_for_rate_limit()
            try:
                text = self._raw_generate(contents, temperature)
                return json.loads(_clean_json_text(text))
            except json.JSONDecodeError as e:
                self.last_error = f"JSON không hợp lệ: {e}"
                console.print(f"[yellow]⚠️ [{label}] Gemini trả JSON hỏng (lần {attempt}/{self.MAX_ATTEMPTS}), thử lại...[/yellow]")
            except Exception as e:
                err = str(e)
                self.last_error = err[:300]
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    if "PerDay" in err or "per day" in err.lower():
                        self.daily_quota_exhausted = True
                        console.print(f"[red]⛔ [{label}] Đã hết hạn ngạch NGÀY của model {self.model_name}. Các bước còn lại sẽ dùng nội dung dự phòng.[/red]")
                        break
                    m = re.search(r"retry in ([0-9.]+)", err, re.IGNORECASE) or re.search(r"retryDelay['\"]?:\s*['\"]?([0-9.]+)s", err)
                    wait_sec = float(m.group(1)) + 2.0 if m else 15.0 * attempt
                    console.print(f"[yellow]⏳ [{label}] Chạm giới hạn tốc độ, chờ {wait_sec:.1f}s (lần {attempt}/{self.MAX_ATTEMPTS})...[/yellow]")
                    time.sleep(wait_sec)
                    self._last_request_time = time.time()
                else:
                    console.print(f"[yellow]⚠️ [{label}] Lỗi gọi Gemini: {err[:200]} (lần {attempt}/{self.MAX_ATTEMPTS})[/yellow]")
                    time.sleep(3.0 * attempt)

        raise GeminiUnavailable(self.last_error or "Gemini thất bại sau nhiều lần thử")

    # ------------------------------------------------------------------
    # LƯỢT 1: LẬP KỊCH BẢN TỪ BẢN ĐỒ TRINH SÁT
    # ------------------------------------------------------------------
    def plan_flow(self, site_map: SiteKnowledgeMap, goal: str, user_notes: Optional[str]) -> FlowPlan:
        """Từ bản đồ trang đã đi thực tế + Mục tiêu + Ghi chú -> Kịch bản các bước cho Lượt 2"""
        features_payload = []
        for f in site_map.discovered_features:
            features_payload.append({
                "click_path": f.click_path or [f.name],
                "type": "menu_cha (bấm để xổ menu con)" if f.children else "trang_chuc_nang",
                "children": f.children,
                "url": f.page_url,
                "actions_on_page": f.page_actions[:15],
                "page_content": (f.page_content or "")[:900],
            })

        prompt = f"""
Bạn là Instructional Designer kiêm kỹ sư tự động hóa trình duyệt. Nhiệm vụ: lập KỊCH BẢN hướng dẫn sử dụng website.

MỤC TIÊU CỦA NGƯỜI DÙNG: "{goal}"
GHI CHÚ / CHỈ ĐẠO CHI TIẾT CỦA NGƯỜI DÙNG (ƯU TIÊN CAO NHẤT): "{user_notes or 'Không có'}"

AI Trinh Sát đã đăng nhập và ĐI THỰC TẾ vào từng chức năng. Dữ liệu dưới đây là THẬT:
- Trang hạ cánh sau đăng nhập: "{site_map.default_landing_module or 'Dashboard'}" ({site_map.dashboard_url})
- Khu vực menu đã trinh sát: {site_map.explored_region}
- Nội dung trang hạ cánh: {(site_map.dashboard_content or '')[:900]}
- Danh sách chức năng (đúng thứ tự xuất hiện trên giao diện):
{json.dumps(features_payload, ensure_ascii=False, indent=1)}

QUY TẮC LẬP KỊCH BẢN (BẮT BUỘC):
1. Làm ĐÚNG phạm vi và thứ tự mà Ghi chú yêu cầu. Nếu người dùng nói "từng chức năng ở menu", phải bao quát ĐẦY ĐỦ mọi mục của menu đó theo thứ tự trên màn hình, không bỏ sót, không giới hạn số lượng.
2. Trang hạ cánh (đang đứng sẵn) dùng kind="view_current" với click_path=[] — KHÔNG click lại.
3. Menu cha (type=menu_cha): tạo 1 bước kind="expand" (click_path=[tên menu cha]) để giới thiệu nhóm, sau đó MỖI menu con một bước kind="open" với click_path=[tên menu cha, tên menu con].
4. Trang chức năng thường: kind="open", click_path=[tên mục].
5. Nếu Ghi chú yêu cầu đi sâu vào một nội dung bên trong trang (ví dụ một khóa học cụ thể), thêm bước kind="open" với click_path nối tiếp, tên lấy CHÍNH XÁC từ actions_on_page / page_content.
6. click_path chỉ dùng tên CHÍNH XÁC như trong dữ liệu. Tuyệt đối không bịa chức năng không tồn tại.
7. "talking_points": 2-5 ý CỤ THỂ cần thuyết minh ở bước đó, rút ra từ page_content thật (tên khu vực, cột, nút, số liệu, ý nghĩa nghiệp vụ). Không viết câu chung chung kiểu "cung cấp công cụ nghiệp vụ".
8. "purpose": 1 câu nêu ý nghĩa/vai trò của chức năng đối với người dùng.
9. guide_title và overview viết song ngữ; overview 2-4 câu giới thiệu hệ thống và những gì tài liệu sẽ hướng dẫn. Bản "en" phải hoàn toàn bằng tiếng Anh.

Chỉ trả về JSON hợp lệ theo mẫu:
{{
  "guide_title": {{"vi": "...", "en": "..."}},
  "overview": {{"vi": "...", "en": "..."}},
  "steps": [
    {{"click_path": [], "kind": "view_current", "title_hint": "...", "purpose": "...", "talking_points": ["...", "..."]}},
    {{"click_path": ["Tên menu cha"], "kind": "expand", "title_hint": "...", "purpose": "...", "talking_points": ["..."]}},
    {{"click_path": ["Tên menu cha", "Tên menu con"], "kind": "open", "title_hint": "...", "purpose": "...", "talking_points": ["..."]}}
  ]
}}
"""
        try:
            data = self._generate_json(prompt, temperature=0.2, label="Lập Kịch bản")
            steps: List[FlowStep] = []
            for s in data.get("steps", []):
                try:
                    fs = FlowStep(
                        click_path=[str(x) for x in (s.get("click_path") or [])],
                        kind=s.get("kind", "open") if s.get("kind") in ("open", "expand", "view_current") else "open",
                        title_hint=s.get("title_hint"),
                        purpose=s.get("purpose", ""),
                        talking_points=[str(t) for t in (s.get("talking_points") or [])][:6],
                    )
                    if fs.kind != "view_current" and not fs.click_path:
                        continue
                    steps.append(fs)
                except Exception:
                    continue
            if steps:
                return FlowPlan(
                    guide_title=_bi(data.get("guide_title"), goal),
                    overview=_bi(data.get("overview"), goal),
                    steps=steps[:45],
                    planned_by_ai=True,
                )
        except GeminiUnavailable as e:
            console.print(f"[red]⚠️ Không lập được Kịch bản bằng AI ({e}). Dùng kịch bản tuần tự theo bản đồ trinh sát.[/red]")

        return self.fallback_flow_plan(site_map, goal)

    def fallback_flow_plan(self, site_map: SiteKnowledgeMap, goal: str) -> FlowPlan:
        """Kịch bản dự phòng: đi tuần tự TOÀN BỘ chức năng đã trinh sát (không cắt bớt)"""
        steps: List[FlowStep] = [FlowStep(
            click_path=[], kind="view_current",
            title_hint=site_map.default_landing_module or "Dashboard",
            purpose=f"Giới thiệu trang {site_map.default_landing_module or 'Dashboard'} hiển thị ngay sau khi đăng nhập.",
        )]
        for f in site_map.discovered_features:
            path = f.click_path or [f.name]
            if len(path) == 1 and site_map.default_landing_module and path[0].strip().lower() == site_map.default_landing_module.strip().lower():
                continue
            steps.append(FlowStep(
                click_path=path,
                kind="expand" if f.children else "open",
                title_hint=path[-1],
                purpose=f"Giới thiệu chức năng {path[-1]}",
                talking_points=f.page_actions[:4],
            ))
        return FlowPlan(
            guide_title=BilingualText(vi=goal, en=goal),
            overview=BilingualText(vi=f"Tài liệu hướng dẫn: {goal}.", en=f"User guide: {goal}."),
            steps=steps,
            planned_by_ai=False,
        )

    # ------------------------------------------------------------------
    # LƯỢT 2: VIẾT NỘI DUNG TỪNG BƯỚC
    # ------------------------------------------------------------------
    def analyze_step(
        self,
        action: RecordedAction,
        total_steps: int = 1,
        guide_context: Optional[str] = None,
        user_notes: Optional[str] = None,
        goal: Optional[str] = None,
        prev_step_title: Optional[str] = None,
        next_step_hint: Optional[str] = None,
    ) -> StepAIAnalysis:
        """
        Ảnh + nội dung trang + mục đích bước trong Kịch bản -> nội dung song ngữ.
        Bước đăng nhập dùng mẫu cố định (bảo mật, ngắn gọn) — không tốn lượt gọi API.
        Nếu Gemini lỗi -> dùng dự phòng và đánh dấu is_fallback=True.
        """
        auth = self._auth_step_content(action)
        if auth is not None:
            return auth

        prompt = self._build_step_prompt(action, total_steps, guide_context, user_notes, goal, prev_step_title, next_step_hint)
        try:
            parsed = self._generate_json(prompt, image_path=action.raw_screenshot_path, temperature=0.35, label=f"Bước {action.step_number}")
            return StepAIAnalysis(
                step_number=action.step_number,
                title=_bi(parsed.get("title"), action.element_text or ""),
                description=_bi(parsed.get("description")),
                slide_summary=_bi(parsed.get("slide_summary")),
                voiceover_script=_bi(parsed.get("voiceover_script")),
                target_element_label=_bi(parsed.get("target_element_label"), action.element_text or "") if parsed.get("target_element_label") else None,
                tip=_bi(parsed.get("tip")) if isinstance(parsed.get("tip"), dict) and (parsed["tip"].get("vi") or parsed["tip"].get("en")) else None,
                is_fallback=False,
            )
        except GeminiUnavailable as e:
            console.print(f"[red]⚠️ Bước {action.step_number}: Gemini không phản hồi ({e}). Dùng nội dung DỰ PHÒNG (sẽ được đánh dấu).[/red]")
            return self._smart_offline_step_analysis(action, total_steps, user_notes)

    def _build_step_prompt(
        self,
        action: RecordedAction,
        total_steps: int,
        guide_context: Optional[str],
        user_notes: Optional[str],
        goal: Optional[str],
        prev_step_title: Optional[str],
        next_step_hint: Optional[str],
    ) -> str:
        role = action.step_role or ("view" if action.action_type == "custom" else "click")
        talking = "\n".join(f"   - {t}" for t in action.talking_points) or "   (không có — tự rút ra từ ảnh và nội dung trang)"

        if role == "click":
            role_rules = """LOẠI BƯỚC: [CLICK MỞ CHỨC NĂNG] — ảnh chụp TRƯỚC khi click, khung đỏ là mục cần bấm.
- description: 1-2 câu: mục nằm ở đâu (menu bên trái / thanh trên ...), bấm vào để mở gì, dùng để làm gì.
- voiceover_script: 1 câu ngắn (4-7 giây). Không giải thích chi tiết chức năng ở bước này (bước xem trang sau sẽ giải thích)."""
        elif role == "expand":
            role_rules = """LOẠI BƯỚC: [MỞ NHÓM MENU] — ảnh chụp SAU khi bấm, menu con đã xổ ra.
- description: giải thích đây là nhóm chức năng gì, liệt kê CÁC MENU CON thật (lấy từ nội dung bên dưới/ảnh) và mỗi mục dùng để làm gì (mỗi mục 1 dòng bắt đầu bằng "- ").
- voiceover_script: 2-3 câu giới thiệu nhóm và các mục con."""
        else:
            role_rules = """LOẠI BƯỚC: [GIỚI THIỆU MÀN HÌNH CHỨC NĂNG] — đây là bước QUAN TRỌNG NHẤT, phải chi tiết và cụ thể.
- description: 1 câu nêu ý nghĩa/vai trò của màn hình, sau đó 3-6 dòng bắt đầu bằng "- " mô tả TỪNG khu vực/khối/cột/nút THẬT trên màn hình (dùng đúng tên hiển thị, số liệu nếu có) và người dùng làm được gì với nó.
- voiceover_script: 3-5 câu tự nhiên như người hướng dẫn đang chỉ màn hình (khoảng 20-35 giây), bao phủ các ý cần thuyết minh.
- tip: một mẹo sử dụng thực tế, cụ thể cho màn hình này (hoặc null)."""

        return f"""
Bạn là Technical Writer chuyên viết tài liệu hướng dẫn sử dụng phần mềm cho người dùng cuối.

BỐI CẢNH TÀI LIỆU:
- Tài liệu: {guide_context or 'Hướng dẫn sử dụng hệ thống'}
- Mục tiêu người dùng: "{goal or ''}"
- Ghi chú / chỉ đạo của người dùng: "{user_notes or 'Không có'}"
- Vị trí: bước {action.step_number}/{total_steps}. Bước trước: "{prev_step_title or '—'}". Bước kế tiếp: "{next_step_hint or '—'}"

THÔNG TIN BƯỚC NÀY:
- Phần tử / chức năng: "{action.element_text}"
- Trang: "{action.page_title}" — {action.page_url}
- Mục đích bước (từ Kịch bản): "{action.plan_purpose or ''}"
- Các ý BẮT BUỘC phải thuyết minh:
{talking}
- NỘI DUNG THỰC TẾ ĐỌC ĐƯỢC TRÊN TRANG (nguồn sự thật, ưu tiên dùng):
\"\"\"
{(action.page_context or 'Không có — hãy đọc từ ảnh chụp màn hình')[:2500]}
\"\"\"

{role_rules}

QUY TẮC CHUNG:
1. Chỉ mô tả những gì THẬT SỰ có trên ảnh / nội dung trang. Không bịa tính năng.
2. Cấm câu sáo rỗng, chung chung như "cung cấp các công cụ nghiệp vụ", "giúp làm việc hiệu quả", "quan sát khung viền đỏ".
3. Không bao giờ ghi tên tài khoản, email, mật khẩu hay thông tin cá nhân của người dùng thật (tên học viên trên màn hình gọi chung là "tên của bạn").
4. Giữ nguyên tên nút/menu như hiển thị trên giao diện, đặt trong dấu nháy đơn.
5. Bản "vi" viết tiếng Việt tự nhiên. Bản "en" viết HOÀN TOÀN bằng tiếng Anh (không chèn câu tiếng Việt).
6. Không mở đầu lặp lại "Tiếp theo", "Bây giờ" — đổi cách dẫn dắt linh hoạt.
7. title dưới 10 từ, dạng hành động hoặc tên màn hình (ví dụ "Mở mục 'My Courses'", "Màn hình 'My Courses'").
8. slide_summary: tối đa 2 câu ngắn.

Chỉ trả về JSON hợp lệ:
{{
  "title": {{"vi": "...", "en": "..."}},
  "description": {{"vi": "...", "en": "..."}},
  "slide_summary": {{"vi": "...", "en": "..."}},
  "voiceover_script": {{"vi": "...", "en": "..."}},
  "target_element_label": {{"vi": "...", "en": "..."}},
  "tip": {{"vi": "...", "en": "..."}} hoặc null
}}
"""

    # ------------------------------------------------------------------
    # Nội dung cố định cho bước đăng nhập & dự phòng offline
    # ------------------------------------------------------------------
    def _auth_step_content(self, action: RecordedAction) -> Optional[StepAIAnalysis]:
        """Nội dung chuẩn, bảo mật cho bước truy cập/đăng nhập. Trả None nếu không phải bước xác thực."""
        role = action.step_role
        sel_lower = (action.selector or "").lower()
        txt_lower = (action.element_text or "").lower()
        if role not in ("auth", "navigate") and not (action.input_value or "").startswith("[REDACTED"):
            return None

        def mk(t_vi, t_en, d_vi, d_en, s_vi, s_en, v_vi, v_en):
            return StepAIAnalysis(
                step_number=action.step_number,
                title=BilingualText(vi=t_vi, en=t_en),
                description=BilingualText(vi=d_vi, en=d_en),
                slide_summary=BilingualText(vi=s_vi, en=s_en),
                voiceover_script=BilingualText(vi=v_vi, en=v_en),
                target_element_label=BilingualText(vi=t_vi, en=t_en),
                tip=None,
                is_fallback=False,
            )

        if action.action_type == "navigate" or role == "navigate":
            return mk(
                "Truy cập hệ thống", "Open the System",
                f"Mở trình duyệt và truy cập địa chỉ {action.page_url}. Chờ trang chủ tải xong.",
                f"Open your web browser and go to {action.page_url}. Wait for the home page to load.",
                f"Truy cập {action.page_url}", f"Go to {action.page_url}",
                "Đầu tiên, bạn mở trình duyệt và truy cập vào địa chỉ của hệ thống.",
                "First, open your browser and go to the system's address.",
            )
        if action.action_type == "fill" and any(k in sel_lower or k in txt_lower for k in ["pass", "mật khẩu", "pwd"]):
            return mk(
                "Nhập mật khẩu", "Enter Your Password",
                "Nhập mật khẩu của bạn vào ô Mật khẩu.", "Type your password into the Password field.",
                "Nhập mật khẩu.", "Enter your password.",
                "Tiếp đến, nhập mật khẩu của bạn vào ô Mật khẩu.", "Then type your password into the password field.",
            )
        if action.action_type == "fill":
            return mk(
                "Nhập tên tài khoản hoặc email", "Enter Username or Email",
                "Nhập tên tài khoản hoặc địa chỉ email của bạn vào ô đăng nhập.", "Type your username or email address into the login field.",
                "Nhập tên tài khoản hoặc email.", "Enter your username or email.",
                "Tại cổng đăng nhập, bạn nhập tên tài khoản hoặc email vào ô đầu tiên.", "On the sign-in page, type your username or email into the first field.",
            )
        if "submit" in sel_lower:
            return mk(
                "Xác nhận đăng nhập", "Confirm Sign-in",
                "Nhấn nút đăng nhập trên biểu mẫu để xác thực và vào hệ thống.", "Click the sign-in button on the form to authenticate and enter the system.",
                "Nhấn nút đăng nhập để vào hệ thống.", "Click sign in to enter the system.",
                "Sau đó, nhấn nút đăng nhập để vào hệ thống.", "Then click the sign-in button to enter the system.",
            )
        return mk(
            "Mở trang đăng nhập", "Open the Sign-in Page",
            "Nhấn nút 'Log in' ở góc trên bên phải trang chủ để chuyển sang cổng đăng nhập.", "Click the 'Log in' button at the top right of the home page to open the sign-in portal.",
            "Nhấn 'Log in' để mở cổng đăng nhập.", "Click 'Log in' to open the sign-in portal.",
            "Trên trang chủ, bạn nhấn nút Log in ở góc trên bên phải.", "On the home page, click the Log in button at the top right.",
        )

    def _smart_offline_step_analysis(self, action: RecordedAction, total_steps: int, user_notes: Optional[str] = None) -> StepAIAnalysis:
        """
        Dự phòng khi Gemini không khả dụng. Dựa trên Kịch bản + nội dung trang thật thay vì câu mẫu chung chung.
        Luôn đánh dấu is_fallback=True để giao diện cảnh báo người dùng.
        """
        auth = self._auth_step_content(action)
        if auth is not None:
            return auth

        name = (action.element_text or action.selector or "chức năng").strip()
        role = action.step_role or ("view" if action.action_type == "custom" else "click")
        purpose = (action.plan_purpose or "").strip()
        points = [p for p in action.talking_points if p][:5]

        if role == "click":
            t_vi, t_en = f"Mở mục '{name}'", f"Open '{name}'"
            d_vi = f"Nhấp vào mục '{name}' trên thanh điều hướng." + (f" {purpose}" if purpose else "")
            d_en = f"Click '{name}' in the navigation menu."
            v_vi, v_en = f"Bạn nhấn vào mục {name} trên thanh điều hướng.", f"Click {name} in the navigation menu."
        elif role == "expand":
            subs = (action.page_context or "").replace("Menu con:", "").strip()
            t_vi, t_en = f"Nhóm menu '{name}'", f"The '{name}' Menu Group"
            d_vi = f"Nhấp vào '{name}' để xổ các mục con." + (f" Các mục gồm: {subs}." if subs else "")
            d_en = f"Click '{name}' to expand its sub-items." + (f" Items: {subs}." if subs else "")
            v_vi, v_en = f"Nhấn vào {name} để mở các mục con bên trong.", f"Click {name} to reveal its sub-items."
        else:
            t_vi, t_en = f"Màn hình '{name}'", f"The '{name}' Screen"
            lines_vi = [purpose or f"Màn hình '{name}'."] + [f"- {p}" for p in points]
            d_vi = "\n".join(lines_vi)
            d_en = f"The '{name}' screen." + ("\n" + "\n".join(f"- {p}" for p in points) if points else "")
            v_vi = (purpose or f"Đây là màn hình {name}.") + (" " + ". ".join(points[:3]) + "." if points else "")
            v_en = f"This is the {name} screen."

        return StepAIAnalysis(
            step_number=action.step_number,
            title=BilingualText(vi=t_vi, en=t_en),
            description=BilingualText(vi=d_vi, en=d_en),
            slide_summary=BilingualText(vi=t_vi, en=t_en),
            voiceover_script=BilingualText(vi=v_vi, en=v_en),
            target_element_label=BilingualText(vi=name, en=name),
            tip=None,
            is_fallback=True,
        )

    # ------------------------------------------------------------------
    # Lập kế hoạch cho chế độ kịch bản JSON (run_autonomous_guide / CLI)
    # ------------------------------------------------------------------
    def plan_steps_from_recon(self, recon: ReconReport, user_goal: str) -> List[Dict[str, Any]]:
        """Dựa trên khảo sát thực tế (ReconReport) và mục tiêu, lập danh sách thao tác cụ thể"""
        prompt = f"""
Bạn là chuyên gia tự động hóa trình duyệt và kỹ sư tài liệu.
Khảo sát địa hình thực tế của trang web:
- URL: {recon.url}
- Tiêu đề trang: {recon.title}
- Các khu vực phát hiện được: {', '.join(recon.detected_sections)}
- Các phần tử tương tác khả dụng (đã quét thực tế):
{json.dumps([{'tag': el.tag, 'text': el.text, 'selector': el.selector, 'role': el.role, 'placeholder': el.placeholder} for el in recon.discovered_elements[:40]], ensure_ascii=False, indent=2)}

Mục tiêu của người dùng: "{user_goal}"

Chỉ dùng các phần tử THỰC TẾ ở trên. Trả về mảng JSON:
[
  {{"action": "click" | "fill" | "navigate" | "select" | "wait", "target": "selector hoặc text", "value": "giá trị nếu fill", "label": "Mô tả ngắn"}}
]
"""
        try:
            plan = self._generate_json(prompt, temperature=0.2, label="Lập kế hoạch kịch bản")
            if isinstance(plan, list) and plan:
                return plan
        except GeminiUnavailable as e:
            console.print(f"[yellow]⚠️ Lỗi lập kế hoạch Gemini: {e}. Sẽ dùng fallback.[/yellow]")
        return self._fallback_plan(recon, user_goal)

    def _fallback_plan(self, recon: ReconReport, user_goal: str) -> List[Dict[str, Any]]:
        """Kế hoạch dự phòng dựa trên các element đầu tiên tìm thấy"""
        steps = [{"action": "navigate", "target": recon.url, "label": "Mở trang web"}]
        for el in recon.discovered_elements[:4]:
            if el.tag == "input":
                steps.append({"action": "fill", "target": el.selector, "value": "sample", "label": f"Điền thông tin vào {el.placeholder or el.text or 'input'}"})
            elif el.tag in ("button", "a"):
                steps.append({"action": "click", "target": el.selector, "label": f"Nhấn vào {el.text or 'nút'}"})
        return steps
