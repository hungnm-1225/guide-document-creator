"""
Configuration and Environment Settings
"""
import os
from pathlib import Path
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
TEMPLATES_DIR = BASE_DIR / "templates"


class VisualTheme(BaseModel):
    """Visual theme settings for highlighting, overlays and cards (Open-Design inspired)"""
    # Spotlight overlay
    spotlight_opacity: float = 0.35  # Darken non-target area by 35%
    spotlight_blur: bool = False
    
    # Target highlight box
    box_border_color: tuple[int, int, int] = (235, 35, 35)      # BGR/RGB Red: #EB2323
    box_border_thickness: int = 4
    box_corner_radius: int = 12
    box_padding: int = 10
    
    # Badge (step number circle)
    badge_bg_color: tuple[int, int, int] = (235, 35, 35)        # Vibrant Red
    badge_text_color: tuple[int, int, int] = (255, 255, 255)    # White
    badge_radius: int = 22
    
    # Cursor pointer
    draw_cursor: bool = True
    cursor_color: tuple[int, int, int] = (255, 69, 0)


class Settings(BaseModel):
    """Global Project Settings"""
    base_dir: Path = BASE_DIR
    output_dir: Path = OUTPUT_DIR
    templates_dir: Path = TEMPLATES_DIR
    
    # AI / Gemini Settings
    gemini_api_key: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = Field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"))
    
    # TTS Settings (VieNeu-TTS & Edge-TTS)
    tts_engine_vi: str = Field(default_factory=lambda: os.getenv("TTS_ENGINE_VI", "vieneu"))
    vieneu_voice: str = Field(default_factory=lambda: os.getenv("VIENEU_VOICE", "hn_nu"))
    vieneu_mode: str = Field(default_factory=lambda: os.getenv("VIENEU_MODE", "v3nano"))
    tts_voice_vi: str = Field(default_factory=lambda: os.getenv("TTS_VOICE_VI", "vi-VN-HoaiMyNeural"))
    tts_voice_en: str = Field(default_factory=lambda: os.getenv("TTS_VOICE_EN", "en-US-AriaNeural"))
    
    # Browser Settings
    headless: bool = Field(default_factory=lambda: os.getenv("HEADLESS", "true").lower() in ("true", "1", "yes"))
    browser_timeout_ms: int = Field(default_factory=lambda: int(os.getenv("BROWSER_TIMEOUT_MS", "30000")))
    viewport_width: int = Field(default_factory=lambda: int(os.getenv("VIEWPORT_WIDTH", "1920")))
    viewport_height: int = Field(default_factory=lambda: int(os.getenv("VIEWPORT_HEIGHT", "1080")))
    
    # Video Render Settings
    video_fps: int = Field(default_factory=lambda: int(os.getenv("VIDEO_FPS", "24")))
    video_width: int = 1920
    video_height: int = 1080
    audio_pause_after_step: float = 1.0  # seconds to pause between steps in video
    
    # Visual Theme
    theme: VisualTheme = VisualTheme()

    def ensure_directories(self):
        """Ensure necessary output folders exist"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "raw_screenshots").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "annotated_images").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "audio").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "documents").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "slides").mkdir(parents=True, exist_ok=True)
        (self.output_dir / "videos").mkdir(parents=True, exist_ok=True)


settings = Settings()


def persist_env(updates: dict[str, str]) -> Path:
    """
    Ghi vĩnh viễn các cấu hình (API Key, Voice, Engine) vào file .env trên đĩa.
    Đảm bảo sau khi người dùng nhập sẽ không bao giờ bị mất lại khi reload hoặc khởi động lại.
    """
    env_file = BASE_DIR / ".env"
    lines = []
    if env_file.exists():
        lines = env_file.read_text(encoding="utf-8").splitlines()
    elif (BASE_DIR / ".env.example").exists():
        lines = (BASE_DIR / ".env.example").read_text(encoding="utf-8").splitlines()

    updated_keys = set()
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                new_lines.append(f"{k}={updates[k]}")
                updated_keys.add(k)
                continue
        new_lines.append(line)

    for k, v in updates.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}")
            updated_keys.add(k)

    content = "\n".join(new_lines) + "\n"
    env_file.write_text(content, encoding="utf-8")

    # Cập nhật ngay vào môi trường runtime
    for k, v in updates.items():
        os.environ[k] = v
        if k == "GEMINI_API_KEY":
            settings.gemini_api_key = v
        elif k == "GEMINI_MODEL":
            settings.gemini_model = v
        elif k == "TTS_ENGINE_VI":
            settings.tts_engine_vi = v
        elif k == "VIENEU_VOICE":
            settings.vieneu_voice = v
        elif k == "VIENEU_MODE":
            settings.vieneu_mode = v
        elif k == "TTS_VOICE_VI":
            settings.tts_voice_vi = v
        elif k == "TTS_VOICE_EN":
            settings.tts_voice_en = v

    return env_file
