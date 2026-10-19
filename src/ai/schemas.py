"""
Pydantic Schemas for Guide Document Creator
Defines data structures for UI Reconnaissance, Browser Actions, Multimodal AI outputs, and Guides.
"""
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """Bounding box coordinates of an interactive element on the screen"""
    x: float = Field(..., description="Top-left X coordinate")
    y: float = Field(..., description="Top-left Y coordinate")
    w: float = Field(..., description="Width of the element")
    h: float = Field(..., description="Height of the element")

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)


class UIElementInfo(BaseModel):
    """Metadata of an interactive element discovered during reconnaissance (Jev Ultrafast Indexed Action Space)"""
    index: int = Field(0, description="Chỉ số định danh phần tử [1], [2]...")
    tag: str
    text: str = ""
    role: Optional[str] = None
    selector: str
    placeholder: Optional[str] = None
    aria_label: Optional[str] = None
    bounding_box: Optional[BoundingBox] = None
    element_type: Optional[str] = None  # button, link, input, select, etc.
    is_occluded: bool = Field(False, description="Có bị phần tử khác che khuất hay không")


class ReconReport(BaseModel):
    """Result of UI Reconnaissance / Pre-survey before planning guide"""
    url: str
    title: str
    overview_screenshot_path: str
    discovered_elements: List[UIElementInfo] = Field(default_factory=list)
    detected_sections: List[str] = Field(default_factory=list)
    login_required: bool = False
    notes: Optional[str] = None


class FeatureModule(BaseModel):
    """Một chức năng hoặc ngóc ngách cụ thể được AI Pioneer Scout khám phá"""
    name: str
    selector: str
    url_target: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    bounding_box: Optional[BoundingBox] = None


class SiteKnowledgeMap(BaseModel):
    """Sơ đồ kiến thức hệ thống tổng hợp từ lượt 1 (Pioneer Scout) để lượt 2 đi sâu vào ngóc ngách"""
    landing_url: str
    landing_title: str
    sso_domain: Optional[str] = None
    dashboard_url: Optional[str] = None
    dashboard_title: Optional[str] = None
    dashboard_screenshot_path: Optional[str] = None
    discovered_features: List[FeatureModule] = Field(default_factory=list)
    recommended_walkthroughs: List[str] = Field(default_factory=list)


class RecordedAction(BaseModel):
    """Raw record captured directly from browser interaction"""
    step_number: int
    action_type: Literal["navigate", "click", "fill", "select", "hover", "scroll", "custom"]
    selector: Optional[str] = None
    input_value: Optional[str] = None
    element_text: Optional[str] = None
    bounding_box: Optional[BoundingBox] = None
    raw_screenshot_path: str
    timestamp: float
    page_title: str
    page_url: str


class BilingualText(BaseModel):
    """Strictly aligned bilingual text pair (Vietnamese and English)"""
    vi: str = Field(..., description="Nội dung tiếng Việt")
    en: str = Field(..., description="English content")


class StepAIAnalysis(BaseModel):
    """Multimodal AI interpretation of a step based on raw log + screenshot"""
    step_number: int
    title: BilingualText = Field(..., description="Tiêu đề ngắn gọn của bước thao tác")
    description: BilingualText = Field(..., description="Mô tả chi tiết cách thao tác cho tài liệu văn bản / docx")
    slide_summary: BilingualText = Field(..., description="Câu tóm tắt cực ngắn gọn (1-2 câu), visual-first cho slide")
    voiceover_script: BilingualText = Field(..., description="Kịch bản lời thoại AI thuyết minh tự nhiên cho video")
    target_element_label: Optional[BilingualText] = None
    tip: Optional[BilingualText] = Field(None, description="Mẹo nhỏ hoặc lưu ý nếu có")


class ProcessedStep(BaseModel):
    """Complete step data ready for document, slide, and video generators"""
    step_number: int
    raw_action: RecordedAction
    ai_content: StepAIAnalysis
    annotated_screenshot_path: str
    audio_path_vi: Optional[str] = None
    audio_path_en: Optional[str] = None
    audio_duration_vi: float = 0.0
    audio_duration_en: float = 0.0


class GuideBook(BaseModel):
    """The master representation of the entire generated user guide"""
    guide_id: str
    title: BilingualText
    summary: BilingualText
    target_url: str
    created_at: str
    user_goal: Optional[str] = None
    user_notes: Optional[str] = None
    artifacts: Dict[str, str] = Field(default_factory=dict)
    steps: List[ProcessedStep] = Field(default_factory=list)
    recon_report: Optional[ReconReport] = None
