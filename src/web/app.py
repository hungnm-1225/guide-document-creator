"""
Web UI Studio & API Server for Guide Document Creator
Giao diện trực quan cao cấp, Widescreen Full-Width (không khoảng trắng chết),
sử dụng chuẩn Typography Roboto, Custom Video Player tự chế và nút ẩn/hiện mật khẩu.
"""
import os
import json
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config.settings import settings
from src.ai.schemas import ReconReport
from src.pipeline.orchestrator import GuidePipeline

app = FastAPI(
    title="Guide Document Creator Studio",
    description="Giao diện trực quan tự động tạo tài liệu hướng dẫn (DOCX, PPTX 16:9, Video MP4)",
    version="2.1.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Đảm bảo output dirs
settings.ensure_directories()

# Mount output folder để xem ảnh và video trực tiếp trên web
app.mount("/static/output", StaticFiles(directory=str(settings.output_dir)), name="output")

# Trạng thái tác vụ đang chạy
pipeline_status = {
    "is_running": False,
    "current_step": "",
    "logs": [],
    "last_result": None,
    "error": None,
}


class StepModel(BaseModel):
    action: str = "click"
    target: Optional[str] = None
    value: Optional[str] = None
    label: Optional[str] = None
    wait_after: float = 1.0


class ScenarioPayload(BaseModel):
    title_vi: str = "Hướng Dẫn Thao Tác Hệ Thống"
    title_en: str = "System User Guide"
    summary_vi: str = "Tài liệu hướng dẫn thao tác chi tiết từng bước."
    summary_en: str = "Step-by-step practical walk-through guide."
    url: str = ""
    languages: List[str] = ["vi", "en"]
    generate_video: bool = True
    steps: List[StepModel] = []
    credentials: Optional[Dict[str, str]] = None


class AutoPilotPayload(BaseModel):
    url: str = "https://learn.pythaverse.space"
    goal: str = "Hướng dẫn đăng nhập hệ thống Pythaverse và xem khóa học"
    username: Optional[str] = None
    password: Optional[str] = None
    user_notes: Optional[str] = None
    enable_two_pass: bool = True
    model: Optional[str] = "gemini-3.5-flash-lite"
    languages: List[str] = ["vi"]
    generate_video: bool = True


class RecorderPayload(BaseModel):
    url: str = "https://learn.pythaverse.space"
    title_vi: str = "Hướng Dẫn Thao Tác Hệ Thống"
    languages: List[str] = ["vi"]
    generate_video: bool = True


class SurveyPayload(BaseModel):
    url: str
    username: Optional[str] = None
    password: Optional[str] = None


class SettingsPayload(BaseModel):
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = "gemini-3.5-flash-lite"
    tts_engine_vi: Optional[str] = "vieneu"
    vieneu_voice: Optional[str] = "Ái Hân"
    vieneu_mode: Optional[str] = "v3nano"
    tts_voice_vi: Optional[str] = "vi-VN-HoaiMyNeural"
    tts_voice_en: Optional[str] = "en-US-AriaNeural"
    spotlight_opacity: Optional[float] = 0.35


# -------------------------------------------------------------
# API ENDPOINTS
# -------------------------------------------------------------

@app.get("/api/status")
async def get_status():
    return pipeline_status


@app.get("/api/examples")
async def get_examples():
    examples_dir = settings.base_dir / "examples"
    demos = []
    for f in examples_dir.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as jf:
                d = json.load(jf)
                demos.append({"filename": f.name, "data": d})
        except Exception:
            pass
    return demos


@app.get("/api/recon-latest")
async def get_latest_recon():
    recon_file = settings.output_dir / "recon" / "recon_report.json"
    if recon_file.exists():
        try:
            with open(recon_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            img_path = data.get("overview_screenshot_path")
            if img_path:
                rel_img = Path(img_path).name
                data["overview_url"] = f"/static/output/recon/{rel_img}"
            return data
        except Exception as e:
            return {"error": str(e)}
    return {"message": "Chưa có báo cáo khảo sát nào."}


@app.get("/api/artifacts")
async def get_artifacts():
    docs_dir = settings.output_dir / "documents"
    slides_dir = settings.output_dir / "slides"
    videos_dir = settings.output_dir / "videos"
    annotated_dir = settings.output_dir / "annotated_images"

    def list_files(d: Path, ext_list: list):
        items = []
        if d.exists():
            import datetime
            for f in sorted(d.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
                if f.suffix.lower() in ext_list and f.name != ".gitkeep":
                    created_str = datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%d/%m/%Y %H:%M:%S")
                    items.append({
                        "name": f.name,
                        "size_bytes": f.stat().st_size,
                        "updated_at": f.stat().st_mtime,
                        "created_time": created_str,
                        "rel_path": f.relative_to(settings.output_dir).as_posix(),
                        "url": f"/static/output/{f.relative_to(settings.output_dir).as_posix()}",
                    })
        return items

    return {
        "documents": list_files(docs_dir, [".docx", ".md"]),
        "slides": list_files(slides_dir, [".pptx"]),
        "videos": list_files(videos_dir, [".mp4"]),
        "annotated_images": list_files(annotated_dir, [".png", ".jpg"]),
    }


@app.post("/api/run-auto")
async def run_auto_endpoint(payload: AutoPilotPayload):
    if pipeline_status["is_running"]:
        raise HTTPException(status_code=400, detail="Một tác vụ khác đang được xử lý. Vui lòng đợi.")

    async def _async_run():
        pipeline_status["is_running"] = True
        pipeline_status["error"] = None
        pipeline_status["current_step"] = f"Khởi chạy AI Tự Động Hóa 2 lượt trên {payload.url}..."

        if payload.model:
            settings.gemini_model = payload.model
            from config.settings import persist_env
            persist_env({"GEMINI_MODEL": payload.model})

        pipeline = GuidePipeline(headless=True)
        creds = {"username": payload.username, "password": payload.password} if (payload.username or payload.password) else None

        try:
            pipeline_status["current_step"] = "Lượt 1: Pioneer Scout đang thám hiểm ngóc ngách hệ thống & chuyển hướng OpenID SSO..."
            result = await pipeline.run_autonomous_flow(
                url=payload.url,
                goal=payload.goal,
                credentials=creds,
                languages=payload.languages,
                generate_video=payload.generate_video,
                user_notes=payload.user_notes,
                enable_two_pass=payload.enable_two_pass,
            )
            pipeline_status["last_result"] = {
                "elapsed_seconds": result["elapsed_seconds"],
                "artifacts": result["artifacts"],
            }
            pipeline_status["current_step"] = "Hoàn tất thành công toàn bộ tài liệu & video chi tiết!"
        except Exception as e:
            pipeline_status["error"] = str(e)
            pipeline_status["current_step"] = "Gặp lỗi: " + str(e)
        finally:
            pipeline_status["is_running"] = False

    asyncio.create_task(_async_run())
    return {"message": "Đã bắt đầu xử lý chế độ AI Auto Pilot 2 lượt.", "status": "started"}


@app.post("/api/run-recorder")
async def run_recorder_endpoint(payload: RecorderPayload):
    if pipeline_status["is_running"]:
        raise HTTPException(status_code=400, detail="Một tác vụ khác đang được xử lý. Vui lòng đợi.")

    async def _async_run():
        pipeline_status["is_running"] = True
        pipeline_status["error"] = None
        pipeline_status["current_step"] = "Đang mở cửa sổ trình duyệt tương tác. Hãy thao tác tự do..."
        pipeline = GuidePipeline(headless=False)
        try:
            result = await pipeline.run_recorder_flow(
                initial_url=payload.url,
                title_vi=payload.title_vi,
                languages=payload.languages,
                generate_video=payload.generate_video
            )
            pipeline_status["last_result"] = {
                "elapsed_seconds": result["elapsed_seconds"],
                "artifacts": result["artifacts"],
            }
            pipeline_status["current_step"] = "Hoàn tất ghi và tạo tài liệu thành công!"
        except Exception as e:
            pipeline_status["error"] = str(e)
            pipeline_status["current_step"] = "Gặp lỗi: " + str(e)
        finally:
            pipeline_status["is_running"] = False

    asyncio.create_task(_async_run())
    return {"message": "Đang mở trình duyệt tương tác để ghi thao tác.", "status": "started"}


@app.get("/api/tts/preview")
async def preview_voice_endpoint(engine: str = "vieneu", voice: str = "Ái Hân", lang: str = "vi"):
    """Tạo nhanh file mẫu âm thanh 2-3 giây để người dùng nghe thử trước khi tạo video"""
    sample_text = f"Xin chào, đây là giọng đọc thử nghiệm {voice} của VieNeu T T S." if engine == "vieneu" else f"Xin chào, đây là giọng đọc thử nghiệm của hệ thống."
    if lang == "en":
        sample_text = f"Hello, this is a sample preview of {voice} voice."

    safe_v = voice.replace(" ", "_").replace("·", "")
    preview_file = settings.output_dir / "audio" / f"preview_{engine}_{safe_v}_{lang}.mp3"
    preview_file.parent.mkdir(parents=True, exist_ok=True)

    if engine == "vieneu":
        from src.generators.video_gen import VieNeuTTSProvider
        ok = VieNeuTTSProvider.generate_audio(sample_text, str(preview_file), voice=voice)
        if not ok:
            import edge_tts
            comm = edge_tts.Communicate(sample_text, settings.tts_voice_vi)
            await comm.save(str(preview_file))
    else:
        import edge_tts
        v = voice if voice and ("Neural" in voice or "en-" in voice) else settings.tts_voice_vi
        comm = edge_tts.Communicate(sample_text, v)
        await comm.save(str(preview_file))

    return FileResponse(str(preview_file), media_type="audio/mpeg")


@app.post("/api/run-scenario")
async def run_scenario_endpoint(payload: ScenarioPayload):
    if pipeline_status["is_running"]:
        raise HTTPException(status_code=400, detail="Một tác vụ khác đang được xử lý. Vui lòng đợi.")

    async def _async_run():
        pipeline_status["is_running"] = True
        pipeline_status["error"] = None
        pipeline_status["current_step"] = "Khởi chạy kịch bản tùy chỉnh..."
        pipeline = GuidePipeline(headless=True)
        try:
            result = await pipeline.run_scenario_pipeline(
                scenario_data=payload.model_dump(),
                languages=payload.languages,
                generate_video=payload.generate_video,
                cleanup_temp=False,
            )
            pipeline_status["last_result"] = {
                "elapsed_seconds": result["elapsed_seconds"],
                "artifacts": result["artifacts"],
            }
            pipeline_status["current_step"] = "Hoàn tất thành công!"
        except Exception as e:
            pipeline_status["error"] = str(e)
            pipeline_status["current_step"] = "Gặp lỗi!"
        finally:
            pipeline_status["is_running"] = False

    asyncio.create_task(_async_run())
    return {"message": "Đã bắt đầu xử lý quy trình trong nền.", "status": "started"}


@app.post("/api/survey")
async def survey_endpoint(payload: SurveyPayload):
    pipeline = GuidePipeline(headless=True)
    creds = {"username": payload.username, "password": payload.password} if (payload.username or payload.password) else None
    try:
        report = await pipeline.crawler.survey_url(payload.url, login_credentials=creds)
        report_file = settings.output_dir / "recon" / "recon_report.json"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))
        data = report.model_dump()
        rel_img = Path(report.overview_screenshot_path).name
        data["overview_url"] = f"/static/output/recon/{rel_img}"
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/settings")
async def get_settings():
    from config.settings import BASE_DIR
    env_file = BASE_DIR / ".env"
    return {
        "gemini_api_key": settings.gemini_api_key,
        "gemini_api_key_set": bool(settings.gemini_api_key and settings.gemini_api_key != "your_gemini_api_key_here"),
        "gemini_model": settings.gemini_model,
        "tts_engine_vi": getattr(settings, "tts_engine_vi", "vieneu"),
        "vieneu_voice": getattr(settings, "vieneu_voice", "hn_nu"),
        "vieneu_mode": getattr(settings, "vieneu_mode", "v3nano"),
        "tts_voice_vi": settings.tts_voice_vi,
        "tts_voice_en": settings.tts_voice_en,
        "spotlight_opacity": settings.theme.spotlight_opacity,
        "env_file_path": str(env_file.resolve()),
        "env_exists": env_file.exists(),
    }


@app.post("/api/settings")
async def update_settings(payload: SettingsPayload):
    from config.settings import persist_env
    updates = {}
    if payload.gemini_api_key is not None:
        updates["GEMINI_API_KEY"] = payload.gemini_api_key.strip()
    if payload.gemini_model:
        updates["GEMINI_MODEL"] = payload.gemini_model
    if payload.tts_engine_vi:
        updates["TTS_ENGINE_VI"] = payload.tts_engine_vi
    if payload.vieneu_voice:
        updates["VIENEU_VOICE"] = payload.vieneu_voice
    if payload.vieneu_mode:
        updates["VIENEU_MODE"] = payload.vieneu_mode
    if payload.tts_voice_vi:
        updates["TTS_VOICE_VI"] = payload.tts_voice_vi
    if payload.tts_voice_en:
        updates["TTS_VOICE_EN"] = payload.tts_voice_en
    if payload.spotlight_opacity is not None:
        settings.theme.spotlight_opacity = payload.spotlight_opacity

    env_path = persist_env(updates)
    return {
        "message": f"Đã lưu vĩnh viễn cấu hình vào tệp {env_path.name} thành công!",
        "env_path": str(env_path.resolve())
    }


# -------------------------------------------------------------
# WEB STUDIO DASHBOARD HTML (ROBOTO FONT + WIDESCREEN + CUSTOM PLAYER)
# -------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Guide Creator Studio - Automated User-Guide Content Factory</title>
  
  <!-- Font Roboto Chuẩn -->
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Roboto:ital,wght@0,300;0,400;0,500;0,700;0,900;1,400&family=Roboto+Mono:wght@400;500;700&display=swap" rel="stylesheet">
  
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/lucide@latest"></script>
  
  <style>
    * { font-family: 'Roboto', -apple-system, BlinkMacSystemFont, sans-serif; }
    code, pre, .font-mono { font-family: 'Roboto Mono', monospace; }
    
    /* Tùy chỉnh thanh cuộn mảnh mượt mà */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #f1f5f9; }
    ::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 9999px; }
    ::-webkit-scrollbar-thumb:hover { background: #94a3b8; }

    /* Custom video range slider */
    input[type=range]::-webkit-slider-thumb {
      height: 12px;
      width: 12px;
      border-radius: 50%;
      background: #ef4444;
      cursor: pointer;
      -webkit-appearance: none;
    }
  </style>
</head>
<body class="bg-slate-100 text-slate-800 min-h-screen flex flex-col antialiased">

  <!-- TOP APP BAR -->
  <header class="bg-white border-b border-slate-200 sticky top-0 z-50 shadow-xs">
    <div class="w-full px-6 lg:px-10 h-16 flex items-center justify-between">
      
      <!-- Brand & Title -->
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-lg bg-rose-600 flex items-center justify-center text-white shadow-sm font-black text-lg">
          G
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base font-bold text-slate-900 tracking-tight leading-none">Guide Creator Studio</h1>
            <span class="px-1.5 py-0.5 text-[10px] font-bold bg-slate-100 text-slate-600 rounded border border-slate-200">v2.1</span>
          </div>
          <p class="text-xs text-slate-500 font-normal mt-0.5">Xây dựng tài liệu hướng dẫn (Word DOCX · Slide 16:9 · Video MP4 AI)</p>
        </div>
      </div>

      <!-- Navigation Tabs -->
      <nav class="flex items-center gap-1.5 bg-slate-100 p-1 rounded-xl border border-slate-200 text-sm">
        <button onclick="switchTab('auto')" id="tab-btn-auto" class="px-3.5 py-1.5 rounded-lg font-medium transition-all bg-white text-rose-600 shadow-xs flex items-center gap-1.5">
          <i data-lucide="sparkles" class="w-4 h-4 text-rose-500"></i> AI Tự Động Hóa 100%
        </button>
        <button onclick="switchTab('recorder')" id="tab-btn-recorder" class="px-3.5 py-1.5 rounded-lg font-medium transition-all text-slate-600 hover:text-slate-900 flex items-center gap-1.5">
          <i data-lucide="video" class="w-4 h-4"></i> Trợ Lý Ghi Hình
        </button>
        <button onclick="switchTab('artifacts')" id="tab-btn-artifacts" class="px-3.5 py-1.5 rounded-lg font-medium transition-all text-slate-600 hover:text-slate-900 flex items-center gap-1.5">
          <i data-lucide="folder-check" class="w-4 h-4"></i> Thư Viện Thành Phẩm
        </button>
        <button onclick="switchTab('dev')" id="tab-btn-dev" class="px-3.5 py-1.5 rounded-lg font-medium transition-all text-slate-600 hover:text-slate-900 flex items-center gap-1.5">
          <i data-lucide="code" class="w-4 h-4"></i> Chế Độ Dev
        </button>
        <button onclick="switchTab('settings')" id="tab-btn-settings" class="px-3.5 py-1.5 rounded-lg font-medium transition-all text-slate-600 hover:text-slate-900 flex items-center gap-1.5">
          <i data-lucide="settings" class="w-4 h-4"></i> Cài Đặt
        </button>
      </nav>

    </div>
  </header>

  <!-- MAIN WIDESCREEN WORKSPACE (KHÔNG CÓ KHOẢNG TRẮNG CHẾT) -->
  <main class="flex-1 w-full px-6 lg:px-10 py-6">

    <!-- GLOBAL STATUS NOTIFIER -->
    <div id="status-bar" class="hidden mb-6 p-4 bg-white rounded-xl border border-rose-200 shadow-sm flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div id="status-spinner" class="animate-spin w-5 h-5 border-2 border-rose-600 border-t-transparent rounded-full"></div>
        <div>
          <div id="status-text" class="text-sm font-semibold text-slate-900">Đang khởi tạo...</div>
          <div class="text-xs text-slate-500">Hệ thống đang tự động điều hướng và xử lý tài nguyên.</div>
        </div>
      </div>
      <button onclick="checkArtifacts()" class="text-xs bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-1.5 rounded-lg font-medium transition">Làm mới dữ liệu</button>
    </div>

    <!-- ============================================================== -->
    <!-- TAB 1: AI TỰ ĐỘNG HÓA 100% (WIDESCREEN 2-COLUMN SPLIT) -->
    <!-- ============================================================== -->
    <section id="tab-auto" class="space-y-6">

      <!-- QUICK PRESETS BAR (Tối ưu không gian, không gradient sến) -->
      <div class="bg-white border border-slate-200 rounded-xl p-4 flex flex-wrap items-center justify-between gap-4 shadow-xs">
        <div class="flex items-center gap-2.5">
          <span class="px-2.5 py-1 bg-emerald-50 text-emerald-700 font-bold text-xs rounded border border-emerald-200 flex items-center gap-1">
            <i data-lucide="check-circle" class="w-3.5 h-3.5"></i> OpenID SSO Ready
          </span>
          <span class="text-xs text-slate-600">Tự động phát hiện nút <b>Log in</b> & tự chuyển hướng liên miền (như <code>learn.pythaverse.space ➔ eid.pythaverse.space</code>).</span>
        </div>

        <div class="flex items-center gap-2">
          <span class="text-xs font-semibold text-slate-500">Mẫu nạp nhanh:</span>
          <button onclick="applyAutoPreset('pythaverse')" class="px-3 py-1.5 bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 rounded-lg text-xs font-semibold transition flex items-center gap-1">
            <i data-lucide="zap" class="w-3.5 h-3.5 text-rose-600"></i> Pythaverse LMS (EID SSO)
          </button>
          <button onclick="applyAutoPreset('wiki')" class="px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-200 rounded-lg text-xs font-semibold transition flex items-center gap-1">
            <i data-lucide="book-open" class="w-3.5 h-3.5"></i> Wikipedia
          </button>
        </div>
      </div>

      <!-- MAIN DUAL-PANE DASHBOARD GRID -->
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">

        <!-- LEFT PANE: WORKFLOW INPUT ENGINE (7 COLS) -->
        <div class="lg:col-span-7 bg-white border border-slate-200 rounded-2xl p-6 shadow-xs space-y-5">
          
          <div class="border-b border-slate-100 pb-3 flex items-center justify-between">
            <h2 class="text-base font-bold text-slate-900 flex items-center gap-2">
              <i data-lucide="sliders-horizontal" class="w-4 h-4 text-rose-600"></i> Cấu Hình Yêu Cầu Tự Động Hóa
            </h2>
            <span class="text-xs text-slate-400">Không cần khai báo Selector thủ công</span>
          </div>

          <!-- API Key Status Indicator -->
          <div class="p-3 bg-slate-50 border border-slate-200 rounded-xl flex items-center justify-between text-xs">
            <div class="flex items-center gap-2">
              <span id="auto-api-badge" class="px-2 py-0.5 rounded font-bold bg-amber-100 text-amber-800 border border-amber-200">Đang kiểm tra API Key...</span>
              <span id="auto-api-text" class="text-slate-600">Được lưu vĩnh viễn trong tệp <code>.env</code></span>
            </div>
            <button onclick="switchTab('settings')" class="text-rose-600 font-bold hover:underline cursor-pointer">Quản lý API Key ➔</button>
          </div>

          <!-- URL Input -->
          <div>
            <label class="block text-xs font-bold text-slate-700 uppercase mb-1">1. Địa chỉ Website Mục tiêu (URL)</label>
            <div class="relative">
              <input type="text" id="auto-url" value="https://learn.pythaverse.space" placeholder="https://..." class="w-full text-sm px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl focus:bg-white focus:ring-2 focus:ring-rose-500 focus:outline-none transition font-medium">
            </div>
          </div>

          <!-- Goal Input -->
          <div>
            <label class="block text-xs font-bold text-slate-700 uppercase mb-1">2. Mục tiêu Hướng dẫn Cần Làm</label>
            <input type="text" id="auto-goal" value="Đăng nhập hệ thống Pythaverse và xem danh sách khóa học" placeholder="Ví dụ: Đăng nhập hệ thống và xem khóa học..." class="w-full text-sm px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl focus:bg-white focus:ring-2 focus:ring-rose-500 focus:outline-none transition">
          </div>

          <!-- Credentials Box with Eye Toggle -->
          <div class="bg-slate-50 border border-slate-200 rounded-xl p-4 space-y-3">
            <div class="text-xs font-bold text-slate-700 flex items-center justify-between">
              <span>3. Thông tin Đăng nhập (Tùy chọn nếu trang yêu cầu)</span>
              <span class="text-[11px] font-normal text-slate-500">Tự động điền vào form OpenID SSO</span>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
              <!-- Username -->
              <div>
                <label class="block text-[11px] font-semibold text-slate-600 mb-1">Tài khoản / Email</label>
                <input type="text" id="auto-user" value="demo_student@pythaverse.space" placeholder="user@company.com" class="w-full text-xs px-3 py-2 bg-white border border-slate-200 rounded-lg focus:ring-2 focus:ring-rose-500 focus:outline-none">
              </div>

              <!-- Password with EYE Toggle -->
              <div>
                <label class="block text-[11px] font-semibold text-slate-600 mb-1">Mật khẩu</label>
                <div class="relative">
                  <input type="password" id="auto-pass" value="MatKhauBaoMat@123" placeholder="••••••••" class="w-full text-xs px-3 py-2 pr-9 bg-white border border-slate-200 rounded-lg focus:ring-2 focus:ring-rose-500 focus:outline-none font-mono">
                  <button type="button" onclick="togglePasswordVisibility('auto-pass', 'pass-eye-icon')" class="absolute inset-y-0 right-0 pr-2.5 flex items-center text-slate-400 hover:text-slate-700 transition" title="Xem / Ẩn mật khẩu">
                    <i data-lucide="eye" class="w-4 h-4" id="pass-eye-icon"></i>
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- 4. Ghi Chú & Hướng Dẫn Bổ Sung (User Directives / Notes) -->
          <div>
            <div class="flex items-center justify-between mb-1">
              <label class="text-xs font-bold text-slate-700 uppercase">4. Ghi Chú & Hướng Dẫn Bổ Sung (Tùy Chỉnh Nghiệp Vụ)</label>
              <span class="text-[11px] text-indigo-600 font-semibold">Tự động lồng vào lời thoại & video</span>
            </div>
            <textarea id="auto-notes" rows="2" placeholder="Ví dụ: Sau khi đăng nhập, hãy hướng dẫn vào xem khóa học Leanbot ASP và xem tài liệu bài 1..." class="w-full text-xs px-3.5 py-2 bg-slate-50 border border-slate-200 rounded-xl focus:bg-white focus:ring-2 focus:ring-rose-500 focus:outline-none transition"></textarea>
          </div>

          <!-- Model & Two-Pass Options -->
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
            <div>
              <label class="block text-[11px] font-bold text-slate-700 uppercase mb-1">Mô Hình AI Gemini (Từ API Key Của Bạn)</label>
              <select id="auto-model" class="w-full text-xs px-3 py-2 bg-slate-50 border border-slate-200 rounded-xl focus:bg-white focus:outline-none font-medium">
                <option value="gemini-3.5-flash-lite" selected>⚡ Gemini 3.5 Flash Lite (500 RPD Miễn phí - Khuyên dùng)</option>
                <option value="gemini-3.1-flash-lite">⚡ Gemini 3.1 Flash Lite (500 RPD Miễn phí)</option>
                <option value="gemini-3.8-flash">🧠 Gemini 3.8 Flash (Thông minh, 20 RPD)</option>
                <option value="gemini-3.7-flash">Gemini 3.7 Flash (20 RPD)</option>
                <option value="gemini-3.5-flash">Gemini 3.5 Flash (20 RPD)</option>
                <option value="gemini-2.5-flash">Gemini 2.5 Flash (20 RPD)</option>
              </select>
            </div>
            <div class="flex flex-col justify-end">
              <label class="flex items-center gap-2 p-2 bg-slate-50 border border-slate-200 rounded-xl text-xs font-semibold text-slate-800 cursor-pointer">
                <input type="checkbox" id="auto-two-pass" checked class="accent-rose-600 w-4 h-4 rounded">
                <span>Khảo sát 2 lượt (Scout ➔ Deep Walkthrough)</span>
              </label>
            </div>
          </div>

          <!-- Output Format Options -->
          <div class="pt-2 flex flex-wrap items-center justify-between gap-4 text-xs font-medium text-slate-700">
            <div class="flex items-center gap-4">
              <label class="flex items-center gap-1.5 cursor-pointer">
                <input type="checkbox" id="auto-lang-vi" checked class="accent-rose-600 w-4 h-4 rounded"> Tiếng Việt 🇻🇳
              </label>
              <label class="flex items-center gap-1.5 cursor-pointer">
                <input type="checkbox" id="auto-lang-en" class="accent-rose-600 w-4 h-4 rounded"> English 🇬🇧
              </label>
            </div>
            <label class="flex items-center gap-2 cursor-pointer font-semibold text-slate-800">
              <input type="checkbox" id="auto-video" checked class="accent-rose-600 w-4 h-4 rounded">
              <span>Sản xuất Video Thuyết Minh AI (.mp4)</span>
            </label>
          </div>

          <!-- Big Action Button -->
          <button onclick="startAutoPilot()" class="w-full py-3.5 bg-rose-600 hover:bg-rose-700 text-white font-bold text-sm rounded-xl shadow-md transition flex items-center justify-center gap-2 cursor-pointer">
            <i data-lucide="play" class="w-4 h-4 fill-white"></i> BẮT ĐẦU TẠO HƯỚNG DẪN TỰ ĐỘNG
          </button>
        </div>

        <!-- RIGHT PANE: LIVE TELEMETRY & WORKFLOW ROADMAP (5 COLS) -->
        <div class="lg:col-span-5 bg-white border border-slate-200 rounded-2xl p-6 shadow-xs space-y-4">
          <div class="border-b border-slate-100 pb-3 flex items-center justify-between">
            <h3 class="text-sm font-bold text-slate-900 flex items-center gap-2">
              <i data-lucide="route" class="w-4 h-4 text-slate-600"></i> Lộ Trình Tự Động Xử Lý
            </h3>
            <span class="text-xs bg-slate-100 px-2 py-0.5 rounded text-slate-600 font-mono">Zero-Config</span>
          </div>

          <div class="space-y-3 text-xs">
            <div class="p-3 bg-slate-50 border border-slate-200 rounded-xl flex items-start gap-3">
              <div class="w-6 h-6 rounded-full bg-slate-900 text-white flex items-center justify-center font-bold text-[11px] shrink-0">1</div>
              <div>
                <div class="font-bold text-slate-800">Khảo sát & Tự tìm nút Đăng nhập</div>
                <div class="text-slate-500 mt-0.5">Tự quét DOM trang ban đầu, xác định nút Login / Sign In, đóng khung Bounding Box và chụp ảnh bước 1.</div>
              </div>
            </div>

            <div class="p-3 bg-slate-50 border border-slate-200 rounded-xl flex items-start gap-3">
              <div class="w-6 h-6 rounded-full bg-slate-900 text-white flex items-center justify-center font-bold text-[11px] shrink-0">2</div>
              <div>
                <div class="font-bold text-slate-800">Theo dõi Chuyển hướng OpenID SSO</div>
                <div class="text-slate-500 mt-0.5">Lắng nghe redirect liên miền (Keycloak, EID, OAuth) mà không bị đứt kết nối hoặc lỗi kịch bản.</div>
              </div>
            </div>

            <div class="p-3 bg-slate-50 border border-slate-200 rounded-xl flex items-start gap-3">
              <div class="w-6 h-6 rounded-full bg-slate-900 text-white flex items-center justify-center font-bold text-[11px] shrink-0">3</div>
              <div>
                <div class="font-bold text-slate-800">Tự điền Form & Xác thực</div>
                <div class="text-slate-500 mt-0.5">Định vị ô Username, Password, nút Submit. Chụp màn hình từng vị trí và submit để quay về app.</div>
              </div>
            </div>

            <div class="p-3 bg-slate-50 border border-slate-200 rounded-xl flex items-start gap-3">
              <div class="w-6 h-6 rounded-full bg-rose-600 text-white flex items-center justify-center font-bold text-[11px] shrink-0">4</div>
              <div>
                <div class="font-bold text-slate-800">Sản xuất Word DOCX, Slide 16:9 & Video AI</div>
                <div class="text-slate-500 mt-0.5">Phủ hiệu ứng Spotlight viền đỏ bo tròn, gọi Edge-TTS lồng tiếng và kết xuất trọn bộ thành phẩm.</div>
              </div>
            </div>
          </div>

          <div class="pt-2 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
            <span>Chi phí vận hành: <b>0đ (100% Free Stack)</b></span>
            <span>Độ phân giải: <b>1920x1080 Full HD</b></span>
          </div>
        </div>

      </div>
    </section>

    <!-- ============================================================== -->
    <!-- TAB 2: TRỢ LÝ GHI HÌNH TRỰC QUAN (TANGO / SCRIBE STYLE) -->
    <!-- ============================================================== -->
    <section id="tab-recorder" class="hidden space-y-6">
      
      <div class="bg-white border border-slate-200 rounded-xl p-4 flex items-center justify-between shadow-xs">
        <div>
          <h2 class="text-base font-bold text-slate-900">Trợ Lý Ghi Hình Trực Tiếp (Smart Visual Recorder)</h2>
          <p class="text-xs text-slate-500 mt-0.5">Mở trình duyệt thực tế để bạn thao tác tự do. Hệ thống tự lưu vết từng cú click và ảnh chụp tương ứng.</p>
        </div>
        <span class="px-2.5 py-1 bg-indigo-50 text-indigo-700 font-bold text-xs rounded border border-indigo-200">Interactive Mode</span>
      </div>

      <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-xs max-w-2xl space-y-4">
        <div>
          <label class="block text-xs font-bold text-slate-700 uppercase mb-1">URL Bắt Đầu Thao Tác</label>
          <input type="text" id="rec-url" value="https://learn.pythaverse.space" placeholder="https://..." class="w-full text-sm px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl focus:bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none transition">
        </div>

        <div>
          <label class="block text-xs font-bold text-slate-700 uppercase mb-1">Tiêu Đề Tài Liệu Hướng Dẫn</label>
          <input type="text" id="rec-title" value="Hướng Dẫn Thao Tác Nghiệp Vụ Pythaverse LMS" placeholder="Ví dụ: Hướng dẫn nộp bài tập..." class="w-full text-sm px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl focus:bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none transition">
        </div>

        <div class="p-4 bg-slate-50 border border-slate-200 rounded-xl text-xs text-slate-700 space-y-1">
          <div class="font-bold text-slate-900 flex items-center gap-1.5"><i data-lucide="info" class="w-4 h-4 text-indigo-600"></i> Hướng dẫn sử dụng:</div>
          <div>• Bấm nút bên dưới: Một cửa sổ trình duyệt thực tế sẽ bật lên màn hình.</div>
          <div>• Bạn cứ nhấp chuột, gõ phím hoặc chuyển trang bình thường trên trang web của mình.</div>
          <div>• Khi thao tác xong, bạn chỉ cần <b>ĐÓNG CỬA SỔ TRÌNH DUYỆT</b>. Hệ thống sẽ tự động xuất ra tài liệu Word, Slide và Video!</div>
        </div>

        <button onclick="startInteractiveRecorder()" class="w-full py-3.5 bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-sm rounded-xl shadow-md transition flex items-center justify-center gap-2 cursor-pointer">
          <i data-lucide="play-circle" class="w-4 h-4"></i> MỞ TRÌNH DUYỆT & BẮT ĐẦU GHI HÌNH
        </button>
      </div>

    </section>

    <!-- ============================================================== -->
    <!-- TAB 3: THƯ VIỆN THÀNH PHẨM (CUSTOM HANDMADE VIDEO PLAYER) -->
    <!-- ============================================================== -->
    <section id="tab-artifacts" class="hidden space-y-6">

      <div class="flex items-center justify-between">
        <div>
          <h2 class="text-xl font-bold text-slate-900">Thư Viện Sản Phẩm Đầu Ra</h2>
          <p class="text-xs text-slate-500">Xem video thuyết minh với trình phát tùy chỉnh, xem trước slide 16:9 và tải Word (.docx).</p>
        </div>
        <button onclick="checkArtifacts()" class="px-4 py-2 bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 rounded-xl text-xs font-semibold shadow-xs transition flex items-center gap-1.5 cursor-pointer">
          <i data-lucide="refresh-cw" class="w-4 h-4"></i> Làm Mới Dữ Liệu
        </button>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">

        <!-- LEFT: CUSTOM HANDMADE VIDEO PLAYER (7 COLS) -->
        <div class="lg:col-span-7 bg-white border border-slate-200 rounded-2xl p-6 shadow-xs space-y-4">
          
          <div class="flex items-center justify-between">
            <h3 class="font-bold text-slate-900 text-sm flex items-center gap-2">
              <i data-lucide="film" class="w-4 h-4 text-rose-600"></i> Video Hướng Dẫn Thuyết Minh AI
            </h3>
            <div id="video-select-group" class="flex gap-1.5"></div>
          </div>

          <!-- THE CUSTOM VIDEO PLAYER CONTAINER -->
          <div id="player-container" class="relative group aspect-video bg-slate-950 rounded-2xl overflow-hidden border border-slate-800 shadow-xl select-none flex items-center justify-center">
            
            <!-- Native Video Element (controls disabled) -->
            <video id="custom-video" class="w-full h-full object-contain cursor-pointer" preload="metadata" playsinline></video>

            <!-- Big Center Play / Pause Icon Button -->
            <div id="big-play-btn" onclick="togglePlayPause()" class="absolute inset-0 flex items-center justify-center bg-black/25 backdrop-blur-[1px] transition-all cursor-pointer">
              <div class="w-16 h-16 rounded-full bg-rose-600 hover:bg-rose-500 text-white flex items-center justify-center shadow-2xl transform hover:scale-110 transition">
                <i data-lucide="play" class="w-8 h-8 fill-white ml-1" id="big-play-icon"></i>
              </div>
            </div>

            <!-- Custom Floating Control Bar (Show on hover) -->
            <div id="player-controls" class="absolute bottom-0 inset-x-0 bg-gradient-to-t from-slate-950/95 via-slate-950/75 to-transparent px-4 pb-3 pt-8 flex flex-col gap-2 transition-opacity opacity-0 group-hover:opacity-100">
              
              <!-- Scrub Timeline Slider -->
              <div class="relative w-full h-1.5 hover:h-2.5 bg-slate-700/60 rounded-full cursor-pointer transition-all flex items-center" id="scrubber-bar">
                <div id="scrubber-buffer" class="absolute left-0 top-0 bottom-0 bg-slate-600/60 rounded-full" style="width: 0%"></div>
                <div id="scrubber-progress" class="absolute left-0 top-0 bottom-0 bg-rose-500 rounded-full flex items-center justify-end" style="width: 0%">
                  <div class="w-3 h-3 bg-white rounded-full shadow-md"></div>
                </div>
              </div>

              <!-- Controls Row -->
              <div class="flex items-center justify-between text-white text-xs pt-0.5">
                
                <div class="flex items-center gap-3">
                  <!-- Play / Pause Button -->
                  <button onclick="togglePlayPause()" class="p-1 hover:text-rose-400 transition" title="Phát / Tạm dừng (Space)">
                    <i data-lucide="play" class="w-4 h-4 fill-current" id="ctrl-play-icon"></i>
                  </button>

                  <!-- Volume Button & Slider -->
                  <div class="flex items-center gap-1.5">
                    <button onclick="toggleMute()" class="p-1 hover:text-rose-400 transition" title="Bật / Tắt âm">
                      <i data-lucide="volume-2" class="w-4 h-4" id="ctrl-vol-icon"></i>
                    </button>
                    <input type="range" id="ctrl-vol-slider" min="0" max="1" step="0.05" value="1" oninput="setVideoVolume(this.value)" class="w-16 h-1 accent-rose-500 cursor-pointer">
                  </div>

                  <!-- Time Display -->
                  <span class="font-mono text-[11px] text-slate-300" id="ctrl-time-display">00:00 / 00:00</span>
                </div>

                <div class="flex items-center gap-2.5">
                  <!-- Playback Speed Button -->
                  <div class="relative">
                    <button onclick="toggleSpeedMenu()" id="ctrl-speed-btn" class="px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-[11px] font-mono font-semibold transition">1.0x</button>
                    <div id="ctrl-speed-menu" class="hidden absolute bottom-7 right-0 bg-slate-900 border border-slate-700 rounded-lg p-1 text-[11px] font-mono shadow-xl flex flex-col gap-0.5 z-30">
                      <button onclick="setVideoSpeed(0.75)" class="px-2.5 py-1 text-left hover:bg-rose-600 rounded">0.75x</button>
                      <button onclick="setVideoSpeed(1.0)" class="px-2.5 py-1 text-left hover:bg-rose-600 rounded">1.0x</button>
                      <button onclick="setVideoSpeed(1.25)" class="px-2.5 py-1 text-left hover:bg-rose-600 rounded">1.25x</button>
                      <button onclick="setVideoSpeed(1.5)" class="px-2.5 py-1 text-left hover:bg-rose-600 rounded">1.5x</button>
                    </div>
                  </div>

                  <!-- Fullscreen Button -->
                  <button onclick="toggleFullscreen()" class="p-1 hover:text-rose-400 transition" title="Toàn màn hình (F)">
                    <i data-lucide="maximize" class="w-4 h-4"></i>
                  </button>
                </div>

              </div>

            </div>

          </div>

          <div id="video-download-btn-area"></div>
        </div>

        <!-- RIGHT: DOCUMENTS, SLIDES & HIGHLIGHT GALLERY (5 COLS) -->
        <div class="lg:col-span-5 space-y-4">
          
          <!-- DOCX & PPTX Files List -->
          <div class="bg-white border border-slate-200 rounded-2xl p-5 shadow-xs space-y-3">
            <h3 class="font-bold text-slate-900 text-sm flex items-center gap-2">
              <i data-lucide="file-check" class="w-4 h-4 text-rose-600"></i> Tài Liệu Văn Bản & Slides 16:9
            </h3>
            <div id="doc-files-list" class="space-y-2">
              <p class="text-xs text-slate-400">Đang quét thư mục thành phẩm...</p>
            </div>
          </div>

          <!-- Highlighted Screenshots Gallery -->
          <div class="bg-white border border-slate-200 rounded-2xl p-5 shadow-xs space-y-3">
            <h3 class="font-bold text-slate-900 text-sm flex items-center gap-2">
              <i data-lucide="image" class="w-4 h-4 text-rose-600"></i> Ảnh Minh Họa Từng Bước (Spotlight)
            </h3>
            <div id="annotated-gallery" class="grid grid-cols-2 gap-2.5 max-h-[280px] overflow-y-auto pr-1"></div>
          </div>

        </div>

      </div>

    </section>

    <!-- ============================================================== -->
    <!-- TAB 4: CHẾ ĐỘ NÂNG CAO CHO DEVELOPER (DOM SURVEY & SCRIPTING) -->
    <!-- ============================================================== -->
    <section id="tab-dev" class="hidden space-y-6">
      <div class="p-4 bg-slate-200/70 border border-slate-300 rounded-xl text-xs text-slate-700 flex items-center gap-2">
        <i data-lucide="terminal" class="w-4 h-4 text-slate-600"></i>
        <span><b>Dành riêng cho Lập trình viên:</b> Khu vực tra cứu bảng Indexed DOM, kiểm tra Bounding Box pixel và soạn thảo kịch bản JSON thủ công.</span>
      </div>

      <div class="bg-white border border-slate-200 rounded-2xl p-5 shadow-xs space-y-4">
        <div class="flex flex-col md:flex-row items-center gap-3">
          <input type="text" id="dev-survey-url" value="https://ptv-tasks-administrator.vercel.app/" class="flex-1 text-sm px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl font-mono">
          <button onclick="runDevSurvey()" class="px-5 py-2.5 bg-slate-900 hover:bg-slate-800 text-white font-bold rounded-xl text-xs transition flex items-center gap-1.5 cursor-pointer">
            <i data-lucide="radar" class="w-4 h-4 text-emerald-400"></i> Quét Cây DOM
          </button>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-12 gap-4">
          <div id="dev-recon-img-wrapper" class="lg:col-span-5 h-72 bg-slate-100 rounded-xl border border-slate-200 flex items-center justify-center overflow-auto text-xs text-slate-400">
            Chưa có ảnh khảo sát
          </div>
          <div class="lg:col-span-7 h-72 overflow-y-auto border border-slate-200 rounded-xl">
            <table class="w-full text-left text-xs">
              <thead class="bg-slate-50 sticky top-0 border-b border-slate-200">
                <tr><th class="p-2">ID</th><th class="p-2">Thẻ</th><th class="p-2">Tên / Text</th><th class="p-2">Selector</th></tr>
              </thead>
              <tbody id="dev-table-body" class="divide-y divide-slate-100 font-mono">
                <tr><td colspan="4" class="p-3 text-center text-slate-400">Bấm Quét để tra cứu.</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>

    <!-- ============================================================== -->
    <!-- TAB 5: CÀI ĐẶT HỆ THỐNG -->
    <!-- ============================================================== -->
    <section id="tab-settings" class="hidden max-w-2xl space-y-6">
      <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-xs space-y-5">
        <h2 class="text-base font-bold text-slate-900 flex items-center gap-2">
          <i data-lucide="sliders" class="w-4 h-4 text-rose-600"></i> Cấu Hình Hệ Thống & Trực Quan
        </h2>

        <!-- Google Gemini API Key with Eye Toggle & .env Path -->
        <div class="space-y-1.5">
          <div class="flex items-center justify-between">
            <label class="text-xs font-bold text-slate-700 uppercase">Google Gemini API Key</label>
            <span class="text-[11px] text-emerald-700 font-semibold font-mono flex items-center gap-1">
              <i data-lucide="file-check" class="w-3.5 h-3.5"></i> Lưu vĩnh viễn trong file .env
            </span>
          </div>
          <div class="relative">
            <input type="password" id="cfg-gemini-key" placeholder="AIzaSy..." class="w-full text-sm px-4 py-2.5 pr-10 bg-slate-50 border border-slate-200 rounded-xl focus:bg-white focus:ring-2 focus:ring-rose-500 focus:outline-none font-mono">
            <button type="button" onclick="togglePasswordVisibility('cfg-gemini-key', 'key-eye-icon')" class="absolute inset-y-0 right-0 pr-3 flex items-center text-slate-400 hover:text-slate-700 transition cursor-pointer" title="Xem / Ẩn Key">
              <i data-lucide="eye" class="w-4 h-4" id="key-eye-icon"></i>
            </button>
          </div>
          <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-1 text-[11px] text-slate-500">
            <span>Lấy key miễn phí tại <a href="https://aistudio.google.com/" target="_blank" class="text-rose-600 underline font-semibold">aistudio.google.com</a></span>
            <span class="font-mono text-[10px] text-slate-400" id="cfg-env-path">Đang tải vị trí lưu...</span>
          </div>
        </div>

        <!-- Gemini Model Dropdown -->
        <div>
          <label class="block text-xs font-bold text-slate-700 uppercase mb-1">Mô Hình Gemini AI (Mặc định)</label>
          <select id="cfg-gemini-model" class="w-full text-xs px-3.5 py-2.5 bg-slate-50 border border-slate-200 rounded-xl focus:bg-white focus:outline-none">
            <option value="gemini-3.5-flash-lite">⚡ Gemini 3.5 Flash Lite (500 RPD Miễn phí - Khuyên dùng)</option>
            <option value="gemini-3.1-flash-lite">⚡ Gemini 3.1 Flash Lite (500 RPD Miễn phí)</option>
            <option value="gemini-3.8-flash">🧠 Gemini 3.8 Flash (Thông minh, 20 RPD)</option>
            <option value="gemini-3.7-flash">Gemini 3.7 Flash (20 RPD)</option>
            <option value="gemini-3.5-flash">Gemini 3.5 Flash (20 RPD)</option>
            <option value="gemini-2.5-flash">Gemini 2.5 Flash (20 RPD)</option>
          </select>
        </div>

        <!-- Vietnamese Voice Engine Selector (VieNeu-TTS / Edge-TTS) -->
        <div class="p-4 bg-slate-50 border border-slate-200 rounded-xl space-y-3">
          <div class="text-xs font-bold text-slate-800 flex items-center justify-between">
            <span class="flex items-center gap-1.5"><i data-lucide="mic" class="w-4 h-4 text-rose-600"></i> Công Cụ Giọng Nói Tiếng Việt (TTS)</span>
            <span class="text-[10px] bg-rose-50 text-rose-700 border border-rose-200 px-1.5 py-0.5 rounded font-mono">VieNeu-TTS Integrated</span>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label class="block text-[11px] font-semibold text-slate-600 mb-1">Engine Giọng Đọc</label>
              <select id="cfg-tts-engine" onchange="toggleTtsOptions(this.value)" class="w-full text-xs px-3 py-2 bg-white border border-slate-200 rounded-lg focus:outline-none">
                <option value="vieneu">🦜 VieNeu-TTS (pnnbao97/VieNeu-TTS)</option>
                <option value="edge-tts">⚡ Microsoft Edge-TTS</option>
              </select>
            </div>

            <div id="cfg-vieneu-box">
              <label class="block text-[11px] font-semibold text-slate-600 mb-1">Giọng Đọc VieNeu-TTS</label>
              <div class="flex items-center gap-2">
                <select id="cfg-vieneu-voice" class="flex-1 text-xs px-3 py-2 bg-white border border-slate-200 rounded-lg focus:outline-none">
                  <option value="Ái Hân" selected>Ái Hân (Nữ · Nam · Tin tức truyền cảm - Khuyên dùng)</option>
                  <option value="Mỹ Duyên">Mỹ Duyên (Nữ · Bắc · Đọc truyện ngọt ngào)</option>
                  <option value="Mai Anh">Mai Anh (Nữ · Bắc · Tự nhiên, rõ ràng)</option>
                  <option value="Trúc Ly">Trúc Ly (Nữ · Bắc · Phong cách tự nhiên)</option>
                  <option value="Xuân Tiên">Xuân Tiên (Nữ · Bắc · Tin tức chuẩn)</option>
                  <option value="Đức Trí">Đức Trí (Nam · Bắc · Trầm ấm, truyền cảm)</option>
                  <option value="Hữu Quân">Hữu Quân (Nam · Bắc · Tin tức)</option>
                  <option value="Minh Quân">Minh Quân (Nam · Bắc · Tự nhiên)</option>
                  <option value="Mạnh Dũng">Mạnh Dũng (Nam · Bắc · Rõ ràng, dứt khoát)</option>
                  <option value="Anh Khôi">Anh Khôi (Nam · Bắc · Kể chuyện)</option>
                  <option value="Adam">Adam (Nam · Nam · Tự nhiên)</option>
                </select>
                <button type="button" onclick="previewSelectedVoice('vieneu')" class="px-2.5 py-1.5 bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-200 rounded-lg text-xs font-bold transition flex items-center gap-1 cursor-pointer shrink-0" title="Bấm để nghe thử giọng này">
                  <i data-lucide="play" class="w-3.5 h-3.5 fill-current"></i> Nghe thử
                </button>
              </div>
            </div>

            <div id="cfg-edgetts-box" class="hidden">
              <label class="block text-[11px] font-semibold text-slate-600 mb-1">Giọng Đọc Edge-TTS</label>
              <div class="flex items-center gap-2">
                <select id="cfg-voice-vi" class="flex-1 text-xs px-3 py-2 bg-white border border-slate-200 rounded-lg focus:outline-none">
                  <option value="vi-VN-HoaiMyNeural">vi-VN-HoaiMyNeural (Nữ ấm áp)</option>
                  <option value="vi-VN-NamMinhNeural">vi-VN-NamMinhNeural (Nam truyền cảm)</option>
                </select>
                <button type="button" onclick="previewSelectedVoice('edge-tts')" class="px-2.5 py-1.5 bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-200 rounded-lg text-xs font-bold transition flex items-center gap-1 cursor-pointer shrink-0" title="Bấm để nghe thử giọng này">
                  <i data-lucide="play" class="w-3.5 h-3.5 fill-current"></i> Nghe thử
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- English Voice -->
        <div>
          <label class="block text-xs font-bold text-slate-700 uppercase mb-1">English Voice (Thuyết minh tiếng Anh)</label>
          <div class="flex items-center gap-2">
            <select id="cfg-voice-en" class="flex-1 text-sm px-3.5 py-2.5 bg-slate-50 border border-slate-200 rounded-xl">
              <option value="en-US-AriaNeural">en-US-AriaNeural (Female)</option>
              <option value="en-US-GuyNeural">en-US-GuyNeural (Male)</option>
            </select>
            <button type="button" onclick="previewSelectedVoice('en')" class="px-3 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-200 rounded-xl text-xs font-bold transition flex items-center gap-1 cursor-pointer shrink-0" title="Listen sample">
              <i data-lucide="play" class="w-3.5 h-3.5 fill-current"></i> Listen
            </button>
          </div>
        </div>

        <div>
          <div class="flex items-center justify-between mb-1">
            <label class="text-xs font-bold text-slate-700 uppercase">Độ Mờ Hiệu Ứng Spotlight (Làm tối nền xung quanh)</label>
            <span id="cfg-spotlight-val" class="text-xs font-bold text-rose-600">35%</span>
          </div>
          <input type="range" id="cfg-spotlight" min="0" max="0.8" step="0.05" value="0.35" class="w-full accent-rose-600">
        </div>

        <button onclick="saveSettings()" class="w-full py-3 bg-slate-900 hover:bg-slate-800 text-white font-bold rounded-xl text-sm transition cursor-pointer flex items-center justify-center gap-2">
          <i data-lucide="save" class="w-4 h-4"></i> Lưu Vĩnh Viễn Vào File .env
        </button>
      </div>
    </section>

  </main>

  <!-- JAVASCRIPT & CUSTOM VIDEO PLAYER LOGIC -->
  <script>
    const customVideo = document.getElementById('custom-video');
    const scrubberBar = document.getElementById('scrubber-bar');
    const scrubberProgress = document.getElementById('scrubber-progress');
    const ctrlPlayIcon = document.getElementById('ctrl-play-icon');
    const bigPlayBtn = document.getElementById('big-play-btn');
    const bigPlayIcon = document.getElementById('big-play-icon');
    const ctrlTimeDisplay = document.getElementById('ctrl-time-display');

    // ==========================================
    // 1. TỰ ĐỘNG DỪNG VIDEO KHI CHUYỂN TAB / ẨN
    // ==========================================
    function pauseActiveVideos() {
      if (customVideo && !customVideo.paused) {
        customVideo.pause();
        updatePlayIcons(false);
      }
    }

    // Lắng nghe sự kiện chuyển tab của trình duyệt (tab browser khác)
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        pauseActiveVideos();
      }
    });

    // Chuyển đổi tab bên trong ứng dụng
    function switchTab(tabName) {
      // Dừng video ngay khi người dùng chuyển sang tab khác!
      pauseActiveVideos();

      ['auto', 'recorder', 'artifacts', 'dev', 'settings'].forEach(t => {
        const el = document.getElementById('tab-' + t);
        if (el) el.classList.add('hidden');
        const btn = document.getElementById('tab-btn-' + t);
        if (btn) {
          btn.classList.remove('bg-white', 'text-rose-600', 'shadow-xs');
          btn.classList.add('text-slate-600');
        }
      });

      const activeEl = document.getElementById('tab-' + tabName);
      if (activeEl) activeEl.classList.remove('hidden');
      const activeBtn = document.getElementById('tab-btn-' + tabName);
      if (activeBtn) {
        activeBtn.classList.remove('text-slate-600');
        activeBtn.classList.add('bg-white', 'text-rose-600', 'shadow-xs');
      }

      if (tabName === 'artifacts') checkArtifacts();
      lucide.createIcons();
    }

    // ==========================================
    // 2. NÚT CON MẮT ẨN / HIỆN MẬT KHẨU
    // ==========================================
    function togglePasswordVisibility(inputId, iconId) {
      const inp = document.getElementById(inputId);
      const icon = document.getElementById(iconId);
      if (!inp) return;
      if (inp.type === 'password') {
        inp.type = 'text';
        if (icon) icon.setAttribute('data-lucide', 'eye-off');
      } else {
        inp.type = 'password';
        if (icon) icon.setAttribute('data-lucide', 'eye');
      }
      lucide.createIcons();
    }

    // ==========================================
    // 3. CUSTOM HANDMADE VIDEO PLAYER LOGIC
    // ==========================================
    function togglePlayPause() {
      if (!customVideo.src) return;
      if (customVideo.paused) {
        customVideo.play();
        updatePlayIcons(true);
      } else {
        customVideo.pause();
        updatePlayIcons(false);
      }
    }

    function updatePlayIcons(isPlaying) {
      if (isPlaying) {
        ctrlPlayIcon.setAttribute('data-lucide', 'pause');
        bigPlayBtn.classList.add('opacity-0', 'pointer-events-none');
      } else {
        ctrlPlayIcon.setAttribute('data-lucide', 'play');
        bigPlayBtn.classList.remove('opacity-0', 'pointer-events-none');
      }
      lucide.createIcons();
    }

    customVideo.addEventListener('timeupdate', () => {
      if (!customVideo.duration) return;
      const pct = (customVideo.currentTime / customVideo.duration) * 100;
      scrubberProgress.style.width = pct + '%';
      ctrlTimeDisplay.innerText = formatTime(customVideo.currentTime) + ' / ' + formatTime(customVideo.duration);
    });

    customVideo.addEventListener('ended', () => {
      updatePlayIcons(false);
    });

    // Nhấp vào thanh timeline để tua video
    scrubberBar.addEventListener('click', (e) => {
      const rect = scrubberBar.getBoundingClientRect();
      const pos = (e.clientX - rect.left) / rect.width;
      if (customVideo.duration) {
        customVideo.currentTime = pos * customVideo.duration;
      }
    });

    function formatTime(secs) {
      const m = Math.floor(secs / 60);
      const s = Math.floor(secs % 60);
      return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
    }

    function setVideoVolume(val) {
      customVideo.volume = val;
      const volIcon = document.getElementById('ctrl-vol-icon');
      if (val == 0) {
        volIcon.setAttribute('data-lucide', 'volume-x');
      } else if (val < 0.5) {
        volIcon.setAttribute('data-lucide', 'volume-1');
      } else {
        volIcon.setAttribute('data-lucide', 'volume-2');
      }
      lucide.createIcons();
    }

    function toggleMute() {
      customVideo.muted = !customVideo.muted;
      const volIcon = document.getElementById('ctrl-vol-icon');
      volIcon.setAttribute('data-lucide', customVideo.muted ? 'volume-x' : 'volume-2');
      lucide.createIcons();
    }

    function toggleSpeedMenu() {
      const m = document.getElementById('ctrl-speed-menu');
      m.classList.toggle('hidden');
    }

    function setVideoSpeed(speed) {
      customVideo.playbackRate = speed;
      document.getElementById('ctrl-speed-btn').innerText = speed + 'x';
      document.getElementById('ctrl-speed-menu').classList.add('hidden');
    }

    function toggleFullscreen() {
      const playerWrapper = document.getElementById('player-container');
      if (!document.fullscreenElement) {
        playerWrapper.requestFullscreen().catch(() => {});
      } else {
        document.exitFullscreen().catch(() => {});
      }
    }

    // Phím tắt bàn phím tiện ích
    window.addEventListener('keydown', (e) => {
      // Chỉ áp dụng khi đang ở tab artifacts và không focus vào input
      if (document.getElementById('tab-artifacts').classList.contains('hidden')) return;
      if (['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;

      if (e.code === 'Space') {
        e.preventDefault();
        togglePlayPause();
      } else if (e.code === 'KeyF') {
        toggleFullscreen();
      } else if (e.code === 'KeyM') {
        toggleMute();
      }
    });

    // ==========================================
    // 4. API WORKFLOWS
    // ==========================================
    function applyAutoPreset(type) {
      if (type === 'pythaverse') {
        document.getElementById('auto-url').value = 'https://learn.pythaverse.space';
        document.getElementById('auto-goal').value = 'Đăng nhập hệ thống Pythaverse và xem khóa học';
        document.getElementById('auto-user').value = 'demo_student@pythaverse.space';
        document.getElementById('auto-pass').value = 'MatKhauBaoMat@123';
      } else if (type === 'wiki') {
        document.getElementById('auto-url').value = 'https://vi.wikipedia.org';
        document.getElementById('auto-goal').value = 'Tìm kiếm thông tin bách khoa toàn thư';
        document.getElementById('auto-user').value = '';
        document.getElementById('auto-pass').value = '';
      }
    }

    async function startAutoPilot() {
      const url = document.getElementById('auto-url').value.trim();
      const goal = document.getElementById('auto-goal').value.trim();
      const user = document.getElementById('auto-user').value.trim();
      const pass = document.getElementById('auto-pass').value.trim();
      const notes = document.getElementById('auto-notes').value.trim();
      const model = document.getElementById('auto-model').value;
      const twoPass = document.getElementById('auto-two-pass').checked;
      const langVi = document.getElementById('auto-lang-vi').checked;
      const langEn = document.getElementById('auto-lang-en').checked;
      const genVideo = document.getElementById('auto-video').checked;

      if (!url) { alert('Vui lòng nhập URL!'); return; }

      const languages = [];
      if (langVi) languages.push('vi');
      if (langEn) languages.push('en');
      if (languages.length === 0) languages.push('vi');

      showStatus('Đang khởi động AI Tự Động Hóa 2 lượt (Pioneer Scout ➔ Deep Walkthrough)...', true);
      try {
        await fetch('/api/run-auto', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url,
            goal: goal || 'Đăng nhập hệ thống và xem tổng quan',
            username: user || null,
            password: pass || null,
            user_notes: notes || null,
            enable_two_pass: twoPass,
            model: model,
            languages,
            generate_video: genVideo
          })
        });
        pollStatus();
      } catch (e) {
        alert('Lỗi: ' + e);
        hideStatus();
      }
    }

    async function startInteractiveRecorder() {
      const url = document.getElementById('rec-url').value.trim();
      const title = document.getElementById('rec-title').value.trim();
      if (!url) { alert('Vui lòng nhập URL!'); return; }

      showStatus('Đang mở trình duyệt tương tác. Hãy thao tác và đóng trình duyệt khi xong...', true);
      try {
        await fetch('/api/run-recorder', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url,
            title_vi: title || 'Hướng Dẫn Thao Tác Hệ Thống',
            languages: ['vi'],
            generate_video: true
          })
        });
        pollStatus();
      } catch (e) {
        alert('Lỗi: ' + e);
        hideStatus();
      }
    }

    let pollInterval = null;
    function pollStatus() {
      if (pollInterval) clearInterval(pollInterval);
      pollInterval = setInterval(async () => {
        try {
          const res = await fetch('/api/status');
          const st = await res.json();
          if (st.is_running) {
            showStatus(st.current_step || 'Đang xử lý tự động...', true);
          } else {
            clearInterval(pollInterval);
            if (st.error) {
              showStatus('Lỗi: ' + st.error, false);
            } else if (st.last_result) {
              showStatus(`Hoàn tất trong ${st.last_result.elapsed_seconds.toFixed(1)}s! Chuyển sang Thư viện thành phẩm...`, false);
              setTimeout(() => switchTab('artifacts'), 1200);
            } else {
              hideStatus();
            }
          }
        } catch (e) {}
      }, 2000);
    }

    function showStatus(text, loading = true) {
      const bar = document.getElementById('status-bar');
      bar.classList.remove('hidden');
      document.getElementById('status-text').innerText = text;
      document.getElementById('status-spinner').style.display = loading ? 'block' : 'none';
    }

    function hideStatus() {
      document.getElementById('status-bar').classList.add('hidden');
    }

    // Artifacts & Custom Player Attachment
    async function checkArtifacts() {
      try {
        const res = await fetch('/api/artifacts');
        const data = await res.json();

        const vSelectGroup = document.getElementById('video-select-group');
        const vBtnArea = document.getElementById('video-download-btn-area');
        vSelectGroup.innerHTML = '';

        if (data.videos && data.videos.length > 0) {
          const firstVid = data.videos[0];
          customVideo.src = firstVid.url;
          updatePlayIcons(false);

          vBtnArea.innerHTML = `
            <a href="${firstVid.url}" download class="inline-flex items-center gap-2 px-4 py-2 bg-slate-900 hover:bg-slate-800 text-white rounded-xl text-xs font-bold transition shadow-sm">
              <i data-lucide="download" class="w-4 h-4"></i> Tải Video MP4 (${firstVid.name})
            </a>
          `;

          data.videos.forEach(v => {
            const btn = document.createElement('button');
            btn.className = "px-2.5 py-1 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-bold rounded-lg transition cursor-pointer";
            btn.innerText = v.name;
            btn.onclick = () => {
              customVideo.src = v.url;
              updatePlayIcons(false);
              vBtnArea.innerHTML = `<a href="${v.url}" download class="inline-flex items-center gap-2 px-4 py-2 bg-slate-900 hover:bg-slate-800 text-white rounded-xl text-xs font-bold transition shadow-sm"><i data-lucide="download" class="w-4 h-4"></i> Tải Video MP4 (${v.name})</a>`;
              lucide.createIcons();
            };
            vSelectGroup.appendChild(btn);
          });
        }

        // Documents & Slides
        const docList = document.getElementById('doc-files-list');
        docList.innerHTML = '';
        const allDocs = [...(data.documents || []), ...(data.slides || [])];
        if (allDocs.length === 0) {
          docList.innerHTML = `<p class="text-xs text-slate-400">Chưa có tài liệu nào.</p>`;
        } else {
          allDocs.forEach(d => {
            const isDocx = d.name.endsWith('.docx');
            const isPptx = d.name.endsWith('.pptx');
            const iconName = isDocx ? 'file-text' : (isPptx ? 'presentation' : 'file-code');
            const badgeColor = isDocx ? 'bg-blue-50 text-blue-700 border-blue-200' : (isPptx ? 'bg-amber-50 text-amber-700 border-amber-200' : 'bg-slate-100 text-slate-700 border-slate-200');
            const row = document.createElement('div');
            row.className = "p-3 bg-slate-50 border border-slate-200 rounded-xl flex items-center justify-between";
            row.innerHTML = `
              <div class="flex items-center gap-2.5">
                <span class="p-2 rounded-lg border ${badgeColor}"><i data-lucide="${iconName}" class="w-4 h-4"></i></span>
                <div>
                  <div class="font-bold text-xs text-slate-900">${d.name}</div>
                  <div class="text-[10px] text-slate-500 flex items-center gap-2 mt-0.5">
                    <span>${(d.size_bytes / 1024).toFixed(1)} KB</span>
                    <span>•</span>
                    <span class="font-mono text-slate-400">${d.created_time || ''}</span>
                  </div>
                </div>
              </div>
              <a href="${d.url}" download class="p-2 hover:bg-white rounded-lg text-slate-600 hover:text-rose-600 transition border border-transparent hover:border-slate-200">
                <i data-lucide="download" class="w-4 h-4"></i>
              </a>
            `;
            docList.appendChild(row);
          });
        }

        // Gallery
        const gallery = document.getElementById('annotated-gallery');
        gallery.innerHTML = '';
        if (data.annotated_images) {
          data.annotated_images.forEach(img => {
            const el = document.createElement('a');
            el.href = img.url;
            el.target = "_blank";
            el.className = "block aspect-video bg-slate-100 rounded-xl overflow-hidden border border-slate-200 hover:border-rose-500 transition shadow-xs";
            el.innerHTML = `<img src="${img.url}" class="w-full h-full object-cover" alt="step" />`;
            gallery.appendChild(el);
          });
        }

        lucide.createIcons();
      } catch (e) {}
    }

    // Dev Survey
    async function runDevSurvey() {
      const url = document.getElementById('dev-survey-url').value.trim();
      if (!url) return;
      showStatus('Đang quét cây DOM...', true);
      try {
        const res = await fetch('/api/survey', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url })
        });
        const data = await res.json();
        if (data.overview_url) {
          document.getElementById('dev-recon-img-wrapper').innerHTML = `<img src="${data.overview_url}" class="max-w-full max-h-full object-contain" />`;
        }
        const tbody = document.getElementById('dev-table-body');
        tbody.innerHTML = '';
        (data.discovered_elements || []).forEach(el => {
          const tr = document.createElement('tr');
          tr.innerHTML = `<td class="p-2 text-rose-600 font-bold">@${el.index}</td><td class="p-2">${el.tag}</td><td class="p-2 max-w-[140px] truncate">${el.text || el.placeholder || '-'}</td><td class="p-2 text-[10px] text-slate-400">${el.selector}</td>`;
          tbody.appendChild(tr);
        });
        hideStatus();
      } catch (e) {
        alert(e);
        hideStatus();
      }
    }

    // Voice Preview Feature
    let previewAudio = null;
    function previewSelectedVoice(type) {
      if (previewAudio) {
        previewAudio.pause();
      }
      let engine = 'vieneu';
      let voice = 'Ái Hân';
      let lang = 'vi';

      if (type === 'vieneu') {
        engine = 'vieneu';
        voice = document.getElementById('cfg-vieneu-voice').value;
        lang = 'vi';
      } else if (type === 'edge-tts') {
        engine = 'edge-tts';
        voice = document.getElementById('cfg-voice-vi').value;
        lang = 'vi';
      } else if (type === 'en') {
        engine = 'edge-tts';
        voice = document.getElementById('cfg-voice-en').value;
        lang = 'en';
      }

      const url = `/api/tts/preview?engine=${encodeURIComponent(engine)}&voice=${encodeURIComponent(voice)}&lang=${lang}&_t=${Date.now()}`;
      previewAudio = new Audio(url);
      previewAudio.play().catch(e => {
        alert('Không thể phát âm thanh xem thử: ' + e);
      });
    }

    // Settings UI & Persistence
    function toggleTtsOptions(engine) {
      if (engine === 'vieneu') {
        document.getElementById('cfg-vieneu-box').classList.remove('hidden');
        document.getElementById('cfg-edgetts-box').classList.add('hidden');
      } else {
        document.getElementById('cfg-vieneu-box').classList.add('hidden');
        document.getElementById('cfg-edgetts-box').classList.remove('hidden');
      }
    }

    async function loadSettings() {
      try {
        const res = await fetch('/api/settings');
        const st = await res.json();
        
        // 1. API Key từ .env hoặc localStorage
        const savedKey = st.gemini_api_key || localStorage.getItem('gemini_api_key') || '';
        document.getElementById('cfg-gemini-key').value = savedKey;
        if (st.env_file_path) {
          document.getElementById('cfg-env-path').innerText = '📁 ' + st.env_file_path;
        }

        // Cập nhật Model Dropdown
        if (st.gemini_model) {
          document.getElementById('cfg-gemini-model').value = st.gemini_model;
          if (document.getElementById('auto-model')) {
            document.getElementById('auto-model').value = st.gemini_model;
          }
        }

        // Cập nhật Badge trên Tab 1 Auto Pilot
        const badge = document.getElementById('auto-api-badge');
        const bText = document.getElementById('auto-api-text');
        if (savedKey && savedKey !== 'your_gemini_api_key_here') {
          badge.className = "px-2 py-0.5 rounded font-bold bg-emerald-100 text-emerald-800 border border-emerald-200";
          badge.innerText = "🔑 Gemini API Key: Đã lưu (.env)";
          bText.innerText = "Đã lưu vĩnh viễn trên tệp .env";
        } else {
          badge.className = "px-2 py-0.5 rounded font-bold bg-amber-100 text-amber-800 border border-amber-200";
          badge.innerText = "⚠️ Chưa có API Key (Đang dùng Smart Offline)";
          bText.innerText = "Bấm để nhập và lưu vĩnh viễn vào .env";
        }

        // 2. TTS Voice Engine
        const engine = st.tts_engine_vi || 'vieneu';
        document.getElementById('cfg-tts-engine').value = engine;
        toggleTtsOptions(engine);
        if (st.vieneu_voice) document.getElementById('cfg-vieneu-voice').value = st.vieneu_voice;
        if (st.tts_voice_vi) document.getElementById('cfg-voice-vi').value = st.tts_voice_vi;
        if (st.tts_voice_en) document.getElementById('cfg-voice-en').value = st.tts_voice_en;

        // 3. Spotlight
        document.getElementById('cfg-spotlight').value = st.spotlight_opacity || 0.35;
        document.getElementById('cfg-spotlight-val').innerText = Math.round((st.spotlight_opacity || 0.35) * 100) + '%';
      } catch (e) {}
    }

    async function saveSettings() {
      const key = document.getElementById('cfg-gemini-key').value.trim();
      const model = document.getElementById('cfg-gemini-model').value;
      const engine = document.getElementById('cfg-tts-engine').value;
      const vVoice = document.getElementById('cfg-vieneu-voice').value;
      const vVi = document.getElementById('cfg-voice-vi').value;
      const vEn = document.getElementById('cfg-voice-en').value;
      const op = parseFloat(document.getElementById('cfg-spotlight').value);

      if (key) {
        localStorage.setItem('gemini_api_key', key);
      }

      const payload = {
        gemini_api_key: key,
        gemini_model: model,
        tts_engine_vi: engine,
        vieneu_voice: vVoice,
        vieneu_mode: 'v3nano',
        tts_voice_vi: vVi,
        tts_voice_en: vEn,
        spotlight_opacity: op
      };

      try {
        const res = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const d = await res.json();
        alert(d.message || 'Đã lưu vĩnh viễn vào tệp .env thành công!');
        loadSettings();
      } catch (e) {
        alert('Lỗi lưu: ' + e);
      }
    }

    document.getElementById('cfg-spotlight').oninput = function() {
      document.getElementById('cfg-spotlight-val').innerText = Math.round(this.value * 100) + '%';
    };

    window.addEventListener('DOMContentLoaded', () => {
      lucide.createIcons();
      loadSettings();
      checkArtifacts();
    });
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)
