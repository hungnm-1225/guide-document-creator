"""
Web UI Studio & API Server for Guide Document Creator
Giao diện trực quan cao cấp, Widescreen Full-Width (không khoảng trắng chết),
sử dụng chuẩn Typography Roboto, Custom Video Player tự chế và nút ẩn/hiện mật khẩu.
"""
import os
import sys
import shutil
import json
import asyncio
from pathlib import Path

# Khắc phục lỗi ConnectionResetError [WinError 10054] do ProactorEventLoop trên Windows khi client đóng tab/kết nối đột ngột
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass
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


def _scan_and_index_guides() -> List[Dict[str, Any]]:
    guides_dir = settings.output_dir / "guides"
    guides_dir.mkdir(parents=True, exist_ok=True)
    docs_dir = settings.output_dir / "documents"
    videos_dir = settings.output_dir / "videos"
    slides_dir = settings.output_dir / "slides"

    # 1. Tự động phục hồi metadata cho các phiên chạy trước từ file Markdown (.md)
    import re, datetime
    if docs_dir.exists():
        for md_file in docs_dir.glob("*.md"):
            m = re.search(r"guide_(\d+)", md_file.name)
            if not m:
                continue
            gid = f"guide_{m.group(1)}"
            g_json = guides_dir / f"{gid}.json"
            if not g_json.exists():
                try:
                    content = md_file.read_text(encoding="utf-8")
                    title_m = re.search(r"^#\s*(.+)$", content, re.MULTILINE)
                    title = title_m.group(1).strip() if title_m else md_file.stem
                    url_m = re.search(r"Hệ thống\*\*:\s*`([^`]+)`", content)
                    url = url_m.group(1) if url_m else "https://pythaverse.space/"
                    time_m = re.search(r"Thời gian\*\*:\s*([^\n]+)", content)
                    created_at = time_m.group(1).strip() if time_m else datetime.datetime.fromtimestamp(md_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M")

                    goal = title.replace("Hướng Dẫn", "").replace("Hướng dẫn:", "").strip()
                    notes = ""
                    if "1792414806" in gid:
                        goal = "Giới thiệu hệ thống Student Workspace và các chức năng"
                        notes = "Đăng nhập, sau đó truy cập từng chức năng ở bên Menu bên trái (ví dụ Dashboard, My Courses, ...), mở chức năng đó ra và giới thiệu chức năng, ý nghĩa của chức năng đó."

                    step_matches = re.findall(r"### Bước (\d+):\s*([^\n]+)\n\n(.*?)(?=\n### Bước|\Z)", content, re.DOTALL)
                    steps_data = []
                    for s_num, s_title, s_body in step_matches:
                        clean_body = s_body.split("![")[0].strip()
                        img_m = re.search(r"!\[.*?\]\((.*?)\)", s_body)
                        img_rel = img_m.group(1) if img_m else f"annotated_images/step_{int(s_num):02d}_highlighted.png"
                        if img_rel.startswith("../"):
                            img_rel = img_rel.replace("../", "")
                        steps_data.append({
                            "step_number": int(s_num),
                            "title": s_title.strip(),
                            "description": clean_body,
                            "image": img_rel
                        })

                    auto_guide_dict = {
                        "guide_id": gid,
                        "title": {"vi": title, "en": title},
                        "summary": {"vi": goal, "en": goal},
                        "user_goal": goal,
                        "user_notes": notes,
                        "target_url": url,
                        "created_at": created_at,
                        "total_steps": len(steps_data),
                        "steps": steps_data,
                    }
                    g_json.write_text(json.dumps(auto_guide_dict, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass

    # 2. Đọc toàn bộ guides/*.json và kết nối các file vật lý
    guide_list = []
    if guides_dir.exists():
        for f in guides_dir.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as jf:
                    data = json.load(jf)
                gid = data.get("guide_id") or f.stem

                # File Video
                video_file = None
                if videos_dir.exists():
                    for vf in sorted(videos_dir.glob(f"*{gid}*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
                        video_file = {
                            "name": vf.name,
                            "size_bytes": vf.stat().st_size,
                            "url": f"/static/output/videos/{vf.name}",
                            "rel_path": f"videos/{vf.name}"
                        }
                        break

                # File Word
                docx_file = None
                if docs_dir.exists():
                    for df in sorted(docs_dir.glob(f"*{gid}*.docx"), key=lambda x: x.stat().st_mtime, reverse=True):
                        docx_file = {
                            "name": df.name,
                            "size_bytes": df.stat().st_size,
                            "url": f"/static/output/documents/{df.name}",
                            "rel_path": f"documents/{df.name}"
                        }
                        break

                # File PowerPoint
                pptx_file = None
                if slides_dir.exists():
                    for sf in sorted(slides_dir.glob(f"*{gid}*.pptx"), key=lambda x: x.stat().st_mtime, reverse=True):
                        pptx_file = {
                            "name": sf.name,
                            "size_bytes": sf.stat().st_size,
                            "url": f"/static/output/slides/{sf.name}",
                            "rel_path": f"slides/{sf.name}"
                        }
                        break

                # File Markdown
                md_file = None
                if docs_dir.exists():
                    for mf in sorted(docs_dir.glob(f"*{gid}*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
                        md_file = {
                            "name": mf.name,
                            "size_bytes": mf.stat().st_size,
                            "url": f"/static/output/documents/{mf.name}",
                            "rel_path": f"documents/{mf.name}"
                        }
                        break

                title_val = data.get("title")
                title_str = title_val.get("vi") if isinstance(title_val, dict) else str(title_val or gid)

                # Format steps to be consistent
                raw_steps = data.get("steps") or []
                formatted_steps = []
                for s in raw_steps:
                    if isinstance(s, dict):
                        ai_c = s.get("ai_content") or {}
                        t_obj = ai_c.get("title") if isinstance(ai_c, dict) else None
                        t_text = t_obj.get("vi") if isinstance(t_obj, dict) else s.get("title", f"Bước {s.get('step_number', 1)}")

                        d_obj = ai_c.get("description") if isinstance(ai_c, dict) else None
                        d_text = d_obj.get("vi") if isinstance(d_obj, dict) else s.get("description", "")

                        v_obj = ai_c.get("voiceover_script") if isinstance(ai_c, dict) else None
                        v_text = v_obj.get("vi") if isinstance(v_obj, dict) else s.get("voiceover", "")

                        img_path = s.get("annotated_screenshot_path") or s.get("image") or ""
                        img_name = Path(img_path).name if img_path else ""
                        img_url = f"/static/output/annotated_images/{img_name}" if img_name else ""

                        formatted_steps.append({
                            "step_number": s.get("step_number", len(formatted_steps) + 1),
                            "title": t_text,
                            "description": d_text,
                            "voiceover": v_text,
                            "image_url": img_url
                        })

                sort_ts = f.stat().st_mtime

                guide_list.append({
                    "guide_id": gid,
                    "title": title_str,
                    "user_goal": data.get("user_goal") or data.get("summary", {}).get("vi") if isinstance(data.get("summary"), dict) else (data.get("user_goal") or title_str),
                    "user_notes": data.get("user_notes") or "",
                    "target_url": data.get("target_url") or "",
                    "created_at": data.get("created_at") or "",
                    "total_steps": len(formatted_steps) or data.get("total_steps", 0),
                    "video": video_file,
                    "docx": docx_file,
                    "pptx": pptx_file,
                    "md": md_file,
                    "steps": formatted_steps,
                    "sort_ts": sort_ts,
                })
            except Exception:
                pass

    guide_list.sort(key=lambda x: x.get("sort_ts", 0), reverse=True)
    return guide_list


@app.get("/api/guides")
async def get_guides_endpoint():
    """Trả về danh sách các phiên hướng dẫn đã sản xuất cùng metadata chi tiết"""
    return {"guides": _scan_and_index_guides()}


@app.get("/api/guides/{guide_id}")
async def get_single_guide_endpoint(guide_id: str):
    """Trả về chi tiết của một phiên hướng dẫn cụ thể"""
    for g in _scan_and_index_guides():
        if g["guide_id"] == guide_id:
            return g
    raise HTTPException(status_code=404, detail="Không tìm thấy phiên hướng dẫn")


@app.delete("/api/guides/{guide_id}")
async def delete_guide_endpoint(guide_id: str):
    """Xóa vĩnh viễn toàn bộ một phiên hướng dẫn và các file thành phẩm liên quan trên đĩa"""
    guides_dir = settings.output_dir / "guides"
    docs_dir = settings.output_dir / "documents"
    videos_dir = settings.output_dir / "videos"
    slides_dir = settings.output_dir / "slides"

    deleted_files = []
    # 1. Delete guide json
    g_json = guides_dir / f"{guide_id}.json"
    if g_json.exists():
        g_json.unlink()
        deleted_files.append(g_json.name)

    # 2. Delete videos matching guide_id
    if videos_dir.exists():
        for f in list(videos_dir.glob(f"*{guide_id}*")):
            f.unlink()
            deleted_files.append(f.name)

    # 3. Delete documents matching guide_id
    if docs_dir.exists():
        for f in list(docs_dir.glob(f"*{guide_id}*")):
            f.unlink()
            deleted_files.append(f.name)

    # 4. Delete slides matching guide_id
    if slides_dir.exists():
        for f in list(slides_dir.glob(f"*{guide_id}*")):
            f.unlink()
            deleted_files.append(f.name)

    return {"message": f"Đã xóa vĩnh viễn {len(deleted_files)} tệp thành phẩm của {guide_id}", "deleted": deleted_files}


@app.delete("/api/artifacts/file")
async def delete_single_file_endpoint(rel_path: str):
    """Xóa vĩnh viễn một tệp cụ thể khỏi thư mục output"""
    target = (settings.output_dir / rel_path).resolve()
    base = settings.output_dir.resolve()
    try:
        target.relative_to(base)
    except Exception:
        raise HTTPException(status_code=400, detail="Đường dẫn không an toàn")

    if target.exists() and target.is_file():
        target.unlink()
        return {"message": f"Đã xóa vĩnh viễn file {target.name}", "file": target.name}
    raise HTTPException(status_code=404, detail="File không tồn tại")


@app.get("/api/system/health")
async def get_system_health():
    """Kiểm tra và báo cáo chẩn đoán trạng thái hoạt động của toàn bộ hệ thống"""
    # 1. Gemini
    has_gemini_key = bool(settings.gemini_api_key and len(settings.gemini_api_key) > 5)
    model = settings.gemini_model
    quota_desc = "500 lượt/ngày (Miễn phí)" if "lite" in model else "20 lượt/ngày"

    # 2. VieNeu-TTS
    vieneu_ready = False
    try:
        from src.generators.video_gen import VieNeuTTSProvider
        vieneu_ready = VieNeuTTSProvider.get_engine() is not None
    except Exception:
        vieneu_ready = False

    # 3. FFmpeg
    ffmpeg_ready = shutil.which("ffmpeg") is not None

    # 4. Storage size
    total_size = 0
    total_files = 0
    for root, _, files in os.walk(settings.output_dir):
        for f in files:
            p = Path(root) / f
            total_size += p.stat().st_size
            total_files += 1

    return {
        "gemini": {
            "has_key": has_gemini_key,
            "model": model,
            "quota": quota_desc,
            "status": "Sẵn sàng (Đã kết nối API)" if has_gemini_key else "Chưa có Key (Dùng Offline Fallback)",
        },
        "vieneu": {
            "ready": vieneu_ready,
            "engine": settings.tts_engine_vi,
            "voice": settings.vieneu_voice,
            "mode": settings.vieneu_mode,
            "status": "Sẵn sàng (Thần kinh on-device)" if vieneu_ready else "Đang dùng Microsoft Edge-TTS dự phòng",
        },
        "ffmpeg": {
            "ready": ffmpeg_ready,
            "status": "Sẵn sàng (Đã kết nối FFmpeg)" if ffmpeg_ready else "Chưa tìm thấy FFmpeg",
        },
        "browser": {
            "ready": True,
            "engine": "Playwright Chromium / MS Edge",
            "viewport": f"{settings.viewport_width}x{settings.viewport_height}",
        },
        "storage": {
            "total_files": total_files,
            "size_mb": round(total_size / (1024 * 1024), 1),
            "env_path": str(settings.base_dir / ".env"),
        }
    }


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
            gb = result.get("guidebook")
            gid = getattr(gb, "guide_id", None) if gb else None
            pipeline_status["last_result"] = {
                "elapsed_seconds": result["elapsed_seconds"],
                "artifacts": result["artifacts"],
                "guide_id": gid,
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
            gb = result.get("guidebook")
            gid = getattr(gb, "guide_id", None) if gb else None
            pipeline_status["last_result"] = {
                "elapsed_seconds": result["elapsed_seconds"],
                "artifacts": result["artifacts"],
                "guide_id": gid,
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
            gb = result.get("guidebook")
            gid = getattr(gb, "guide_id", None) if gb else None
            pipeline_status["last_result"] = {
                "elapsed_seconds": result["elapsed_seconds"],
                "artifacts": result["artifacts"],
                "guide_id": gid,
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
            <textarea id="auto-notes" rows="2" placeholder="Ví dụ: Sau khi đăng nhập, hãy truy cập các mục trên thanh Menu bên trái (Dashboard, My Courses, Không gian làm việc...), mở từng chức năng ra và giới thiệu chức năng, ý nghĩa của chức năng đó..." class="w-full text-xs px-3.5 py-2 bg-slate-50 border border-slate-200 rounded-xl focus:bg-white focus:ring-2 focus:ring-rose-500 focus:outline-none transition"></textarea>
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
    <!-- TAB 3: THƯ VIỆN THÀNH PHẨM & LỊCH SỬ HƯỚNG DẪN (REDESIGNED) -->
    <!-- ============================================================== -->
    <section id="tab-artifacts" class="hidden space-y-6">

      <!-- Header Row with Search & Stats -->
      <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 bg-white border border-slate-200 rounded-2xl p-5 shadow-xs">
        <div>
          <div class="flex items-center gap-2">
            <h2 class="text-xl font-bold text-slate-900">Thư Viện Sản Phẩm Đầu Ra</h2>
            <span id="guide-count-badge" class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-rose-50 text-rose-700 border border-rose-200">0 phiên hướng dẫn</span>
          </div>
          <p class="text-xs text-slate-500 mt-0.5">Quản lý trọn bộ tài liệu theo từng phiên: đối chiếu yêu cầu/ghi chú ban đầu, xem video thuyết minh, tải Word/Slide và quản lý xóa vĩnh viễn.</p>
        </div>
        <div class="flex items-center gap-2 w-full sm:w-auto">
          <div class="relative flex-1 sm:w-64">
            <i data-lucide="search" class="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2"></i>
            <input type="text" id="guide-search-input" oninput="filterGuides(this.value)" placeholder="Tìm kiếm phiên hướng dẫn..." class="w-full pl-9 pr-3 py-2 text-xs bg-slate-50 border border-slate-200 rounded-xl focus:bg-white focus:outline-none focus:ring-2 focus:ring-rose-500">
          </div>
          <button onclick="loadGuides()" class="px-4 py-2 bg-slate-900 hover:bg-slate-800 text-white rounded-xl text-xs font-semibold shadow-xs transition flex items-center gap-1.5 cursor-pointer shrink-0">
            <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i> Làm Mới
          </button>
        </div>
      </div>

      <!-- Main Showcase Layout: 12 Cols Grid -->
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">

        <!-- LEFT (4 COLS): SESSIONS DIRECTORY -->
        <div class="lg:col-span-4 bg-white border border-slate-200 rounded-2xl p-4 shadow-xs space-y-3">
          <div class="flex items-center justify-between pb-2 border-b border-slate-100">
            <span class="text-xs font-bold uppercase tracking-wider text-slate-500 flex items-center gap-1.5">
              <i data-lucide="folder-clock" class="w-4 h-4 text-rose-600"></i> Lịch Sử Các Phiên Tạo
            </span>
            <span class="text-[11px] text-slate-400 font-mono" id="guide-dir-count">0 mục</span>
          </div>

          <div id="guides-list-container" class="space-y-2.5 max-h-[780px] overflow-y-auto pr-1">
            <div class="p-6 text-center text-xs text-slate-400 space-y-2">
              <i data-lucide="loader-2" class="w-6 h-6 animate-spin mx-auto text-slate-400"></i>
              <p>Đang tải danh sách thành phẩm...</p>
            </div>
          </div>
        </div>

        <!-- RIGHT (8 COLS): ACTIVE GUIDE SHOWCASE -->
        <div class="lg:col-span-8 space-y-5" id="guide-detail-container">
          <div class="bg-white border border-slate-200 rounded-2xl p-12 text-center text-slate-400 space-y-3 shadow-xs">
            <i data-lucide="sparkles" class="w-10 h-10 mx-auto text-rose-300"></i>
            <h3 class="font-bold text-base text-slate-700">Chọn một phiên hướng dẫn ở danh mục bên trái</h3>
            <p class="text-xs max-w-md mx-auto">Tại đây sẽ hiển thị chi tiết yêu cầu ban đầu, ghi chú nghiệp vụ bạn đã đưa vào, video thuyết minh và trọn bộ file Word / Slide 16:9.</p>
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
    <!-- TAB 5: CÀI ĐẶT HỆ THỐNG & TRUNG TÂM CHẨN ĐOÁN (REDESIGNED) -->
    <!-- ============================================================== -->
    <section id="tab-settings" class="hidden space-y-6">

      <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 bg-white border border-slate-200 rounded-2xl p-5 shadow-xs">
        <div>
          <h2 class="text-xl font-bold text-slate-900">Cấu Hình Hệ Thống & Trung Tâm Chẩn Đoán</h2>
          <p class="text-xs text-slate-500 mt-0.5">Quản lý API Key, chọn mô hình AI, tinh chỉnh giọng nói VieNeu-TTS và giám sát trạng thái phần cứng/phần mềm.</p>
        </div>
        <button onclick="loadSystemHealth()" class="px-4 py-2 bg-slate-900 hover:bg-slate-800 text-white rounded-xl text-xs font-semibold shadow-xs transition flex items-center gap-1.5 cursor-pointer shrink-0">
          <i data-lucide="activity" class="w-4 h-4 text-emerald-400"></i> Kiểm Tra Trạng Thái
        </button>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">

        <!-- LEFT (7 COLS): CONFIGURATION FORM -->
        <div class="lg:col-span-7 bg-white border border-slate-200 rounded-2xl p-6 shadow-xs space-y-5">
          <h3 class="text-sm font-bold text-slate-900 flex items-center gap-2 border-b border-slate-100 pb-3">
            <i data-lucide="sliders" class="w-4 h-4 text-rose-600"></i> Cấu Hình Trực Quan & AI
          </h3>

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
                  <option value="vieneu">🦜 VieNeu-TTS (pnnbao97/VieNeu-TTS on-device)</option>
                  <option value="edge-tts">⚡ Microsoft Edge-TTS</option>
                </select>
              </div>

              <div id="cfg-vieneu-box">
                <label class="block text-[11px] font-semibold text-slate-600 mb-1">Giọng Đọc VieNeu-TTS</label>
                <div class="flex items-center gap-2">
                  <select id="cfg-vieneu-voice" class="flex-1 text-xs px-3 py-2 bg-white border border-slate-200 rounded-lg focus:outline-none">
                    <option value="Ái Hân" selected>Ái Hân (Nữ · Nam · Tin tức truyền cảm)</option>
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

          <button onclick="saveSettings()" class="w-full py-3.5 bg-slate-900 hover:bg-slate-800 text-white font-bold rounded-xl text-sm transition cursor-pointer flex items-center justify-center gap-2 shadow-md">
            <i data-lucide="save" class="w-4 h-4"></i> Lưu Vĩnh Viễn Vào File .env
          </button>
        </div>

        <!-- RIGHT (5 COLS): SYSTEM HEALTH & LIVE PLAYGROUND -->
        <div class="lg:col-span-5 space-y-5">

          <!-- System Health Card -->
          <div class="bg-white border border-slate-200 rounded-2xl p-5 shadow-xs space-y-4">
            <div class="flex items-center justify-between border-b border-slate-100 pb-3">
              <h3 class="text-sm font-bold text-slate-900 flex items-center gap-2">
                <i data-lucide="shield-check" class="w-4 h-4 text-emerald-600"></i> Chẩn Đoán Sức Khỏe Hệ Thống
              </h3>
              <span class="text-[10px] px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 font-bold border border-emerald-200">Hoạt động tốt</span>
            </div>
            <div id="system-health-list" class="space-y-3">
              <div class="p-3 bg-slate-50 border border-slate-200 rounded-xl space-y-1">
                <div class="flex items-center justify-between text-xs font-bold text-slate-800">
                  <span class="flex items-center gap-1.5"><i data-lucide="brain" class="w-3.5 h-3.5 text-rose-600"></i> Google Gemini Multimodal</span>
                  <span id="sh-gemini-status" class="text-[11px] text-emerald-600 font-bold">Kiểm tra...</span>
                </div>
                <p id="sh-gemini-desc" class="text-[11px] text-slate-500">Mô hình: gemini-3.5-flash-lite · 500 RPD</p>
              </div>

              <div class="p-3 bg-slate-50 border border-slate-200 rounded-xl space-y-1">
                <div class="flex items-center justify-between text-xs font-bold text-slate-800">
                  <span class="flex items-center gap-1.5"><i data-lucide="mic-2" class="w-3.5 h-3.5 text-blue-600"></i> Công Cụ Giọng Nói VieNeu-TTS</span>
                  <span id="sh-tts-status" class="text-[11px] text-emerald-600 font-bold">Kiểm tra...</span>
                </div>
                <p id="sh-tts-desc" class="text-[11px] text-slate-500">Thần kinh On-device 48kHz / Dự phòng Edge-TTS</p>
              </div>

              <div class="p-3 bg-slate-50 border border-slate-200 rounded-xl space-y-1">
                <div class="flex items-center justify-between text-xs font-bold text-slate-800">
                  <span class="flex items-center gap-1.5"><i data-lucide="chrome" class="w-3.5 h-3.5 text-amber-600"></i> Playwright Trình Duyệt Tự Động</span>
                  <span id="sh-browser-status" class="text-[11px] text-emerald-600 font-bold">Sẵn sàng</span>
                </div>
                <p id="sh-browser-desc" class="text-[11px] text-slate-500">Chromium & MS Edge · Độ phân giải 1920x1080</p>
              </div>

              <div class="p-3 bg-slate-50 border border-slate-200 rounded-xl space-y-1">
                <div class="flex items-center justify-between text-xs font-bold text-slate-800">
                  <span class="flex items-center gap-1.5"><i data-lucide="film" class="w-3.5 h-3.5 text-indigo-600"></i> FFmpeg & MoviePy Rendering</span>
                  <span id="sh-ffmpeg-status" class="text-[11px] text-emerald-600 font-bold">Sẵn sàng</span>
                </div>
                <p id="sh-ffmpeg-desc" class="text-[11px] text-slate-500">Kết xuất Video MP4 độ phân giải cao 24fps</p>
              </div>
            </div>
          </div>

          <!-- Live Voice Audio Playground -->
          <div class="bg-white border border-slate-200 rounded-2xl p-5 shadow-xs space-y-3">
            <h3 class="text-sm font-bold text-slate-900 flex items-center gap-2">
              <i data-lucide="play-square" class="w-4 h-4 text-rose-600"></i> Phòng Thử Nghiệm Giọng Đọc Trực Tiếp
            </h3>
            <p class="text-xs text-slate-500">Gõ bất kỳ câu thoại nào để nghe thử trực tiếp âm thanh phát ra từ giọng đọc AI bạn đã chọn.</p>
            <div class="space-y-2">
              <textarea id="live-voice-text" rows="2" class="w-full text-xs p-3 bg-slate-50 border border-slate-200 rounded-xl focus:bg-white focus:outline-none focus:ring-2 focus:ring-rose-500">Xin chào, đây là giọng đọc thuyết minh tự động của hệ thống Student Workspace.</textarea>
              <div class="flex items-center justify-between gap-2">
                <button type="button" onclick="playLiveVoiceTest()" id="live-voice-btn" class="px-4 py-2 bg-rose-600 hover:bg-rose-500 text-white rounded-xl text-xs font-bold transition flex items-center gap-1.5 cursor-pointer shadow-xs">
                  <i data-lucide="volume-2" class="w-3.5 h-3.5 fill-current"></i> Nghe Thử Ngay
                </button>
                <span id="live-voice-status" class="text-[11px] text-slate-400 font-mono"></span>
              </div>
              <audio id="live-audio-player" class="w-full mt-2 hidden" controls></audio>
            </div>
          </div>

          <!-- Storage Specs Card -->
          <div class="bg-slate-50 border border-slate-200 rounded-2xl p-5 space-y-3">
            <h3 class="text-xs font-bold uppercase tracking-wider text-slate-700 flex items-center gap-2">
              <i data-lucide="hard-drive" class="w-4 h-4 text-slate-500"></i> Dung Lượng & Quy Chuẩn Lưu Trữ
            </h3>
            <div class="space-y-2 text-xs text-slate-600">
              <div class="flex items-center justify-between">
                <span>Dung lượng thư mục Output:</span>
                <span class="font-bold text-slate-900 font-mono" id="health-storage-size">-- MB</span>
              </div>
              <div class="flex items-center justify-between">
                <span>Tổng tệp thành phẩm:</span>
                <span class="font-bold text-slate-900 font-mono" id="health-storage-files">-- tệp</span>
              </div>
              <div class="pt-2 border-t border-slate-200 flex flex-col gap-1">
                <span class="text-[11px] text-slate-500 font-semibold">Tệp cấu hình lưu trữ:</span>
                <span class="font-mono text-[10px] text-slate-500 bg-white p-1.5 rounded border border-slate-200 break-all" id="health-env-path">...</span>
              </div>
            </div>
          </div>

        </div>

      </div>

    </section>

  </main>

  <!-- JAVASCRIPT & CUSTOM VIDEO PLAYER LOGIC -->
  <script>
    let customVideo = document.getElementById('custom-video');
    let scrubberBar = document.getElementById('scrubber-bar');
    let scrubberProgress = document.getElementById('scrubber-progress');
    let ctrlPlayIcon = document.getElementById('ctrl-play-icon');
    let bigPlayBtn = document.getElementById('big-play-btn');
    let bigPlayIcon = document.getElementById('big-play-icon');
    let ctrlTimeDisplay = document.getElementById('ctrl-time-display');

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

      if (tabName === 'artifacts') loadGuides();
      if (tabName === 'settings') loadSystemHealth();
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
      if (!customVideo || !customVideo.src) return;
      if (customVideo.paused) {
        customVideo.play();
        updatePlayIcons(true);
      } else {
        customVideo.pause();
        updatePlayIcons(false);
      }
    }

    function updatePlayIcons(isPlaying) {
      if (!ctrlPlayIcon || !bigPlayBtn) return;
      if (isPlaying) {
        ctrlPlayIcon.setAttribute('data-lucide', 'pause');
        bigPlayBtn.classList.add('opacity-0', 'pointer-events-none');
      } else {
        ctrlPlayIcon.setAttribute('data-lucide', 'play');
        bigPlayBtn.classList.remove('opacity-0', 'pointer-events-none');
      }
      lucide.createIcons();
    }

    if (customVideo) {
      customVideo.addEventListener('timeupdate', () => {
        if (!customVideo.duration) return;
        const pct = (customVideo.currentTime / customVideo.duration) * 100;
        if (scrubberProgress) scrubberProgress.style.width = pct + '%';
        if (ctrlTimeDisplay) ctrlTimeDisplay.innerText = formatTime(customVideo.currentTime) + ' / ' + formatTime(customVideo.duration);
      });

      customVideo.addEventListener('ended', () => {
        updatePlayIcons(false);
      });
    }

    // Nhấp vào thanh timeline để tua video
    if (scrubberBar) {
      scrubberBar.addEventListener('click', (e) => {
        const rect = scrubberBar.getBoundingClientRect();
        const pos = (e.clientX - rect.left) / rect.width;
        if (customVideo && customVideo.duration) {
          customVideo.currentTime = pos * customVideo.duration;
        }
      });
    }

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
              setTimeout(() => {
                switchTab('artifacts');
                if (st.last_result && st.last_result.guide_id) {
                  loadGuides(st.last_result.guide_id);
                } else {
                  loadGuides();
                }
              }, 1200);
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

    // ==============================================================
    // THƯ VIỆN THÀNH PHẨM THEO PHIÊN HƯỚNG DẪN (GUIDES SHOWCASE)
    // ==============================================================
    let allGuides = [];
    let selectedGuideId = null;

    async function loadGuides(preferredId = null) {
      const container = document.getElementById('guides-list-container');
      try {
        const res = await fetch('/api/guides');
        const data = await res.json();
        allGuides = data.guides || [];

        const countBadge = document.getElementById('guide-count-badge');
        if (countBadge) countBadge.innerText = `${allGuides.length} phiên hướng dẫn`;
        const dirCount = document.getElementById('guide-dir-count');
        if (dirCount) dirCount.innerText = `${allGuides.length} mục`;

        if (allGuides.length === 0) {
          container.innerHTML = `
            <div class="p-8 text-center text-slate-400 space-y-2">
              <i data-lucide="folder-x" class="w-8 h-8 mx-auto text-slate-300"></i>
              <p class="text-xs font-semibold">Chưa có phiên hướng dẫn nào.</p>
              <p class="text-[11px] text-slate-400">Hãy tạo hướng dẫn mới tại tab AI Tự Động Hóa.</p>
            </div>
          `;
          renderGuideDetail(null);
          lucide.createIcons();
          return;
        }

        if (preferredId && allGuides.some(g => g.guide_id === preferredId)) {
          selectedGuideId = preferredId;
        } else if (!selectedGuideId || !allGuides.some(g => g.guide_id === selectedGuideId)) {
          selectedGuideId = allGuides[0].guide_id;
        }

        renderGuideList(allGuides);
        const sel = allGuides.find(g => g.guide_id === selectedGuideId);
        renderGuideDetail(sel);
      } catch (e) {
        container.innerHTML = `<div class="p-4 text-xs text-rose-500">Lỗi nạp danh sách: ${e}</div>`;
      }
    }

    function renderGuideList(guides) {
      const container = document.getElementById('guides-list-container');
      container.innerHTML = '';

      guides.forEach(g => {
        const isSel = g.guide_id === selectedGuideId;
        const item = document.createElement('div');
        item.className = `p-3.5 rounded-xl border transition cursor-pointer space-y-2 relative group ${
          isSel 
            ? 'bg-rose-50/50 border-rose-400 shadow-xs ring-1 ring-rose-400/20' 
            : 'bg-slate-50 hover:bg-white border-slate-200 hover:border-slate-300'
        }`;
        item.onclick = (e) => {
          if (e.target.closest('.del-btn')) return;
          selectedGuideId = g.guide_id;
          renderGuideList(allGuides);
          renderGuideDetail(g);
        };

        const hasVideo = !!g.video;
        const hasDocx = !!g.docx;
        const hasPptx = !!g.pptx;

        item.innerHTML = `
          <div class="flex items-start justify-between gap-2">
            <h4 class="font-bold text-xs text-slate-900 line-clamp-2 ${isSel ? 'text-rose-700' : ''}">
              ${g.title}
            </h4>
            <button onclick="deleteGuide('${g.guide_id}')" class="del-btn p-1 text-slate-300 hover:text-rose-600 rounded-lg hover:bg-rose-50 transition opacity-0 group-hover:opacity-100 shrink-0 cursor-pointer" title="Xóa vĩnh viễn phiên này">
              <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
            </button>
          </div>

          <div class="flex flex-wrap items-center gap-1.5 text-[10px]">
            <span class="px-2 py-0.5 rounded-md bg-white border border-slate-200 font-mono text-slate-500 flex items-center gap-1">
              <i data-lucide="clock" class="w-3 h-3 text-slate-400"></i> ${g.created_at || 'Mới'}
            </span>
            <span class="px-2 py-0.5 rounded-md bg-white border border-slate-200 font-bold text-slate-600">
              ${g.total_steps || (g.steps ? g.steps.length : 0)} bước
            </span>
          </div>

          <div class="flex items-center gap-2 pt-1 border-t border-slate-100 text-[10px]">
            <span class="flex items-center gap-1 ${hasVideo ? 'text-rose-600 font-bold' : 'text-slate-300'}">
              <i data-lucide="film" class="w-3 h-3"></i> Video
            </span>
            <span>•</span>
            <span class="flex items-center gap-1 ${hasDocx ? 'text-blue-600 font-bold' : 'text-slate-300'}">
              <i data-lucide="file-text" class="w-3 h-3"></i> Word
            </span>
            <span>•</span>
            <span class="flex items-center gap-1 ${hasPptx ? 'text-amber-600 font-bold' : 'text-slate-300'}">
              <i data-lucide="presentation" class="w-3 h-3"></i> Slide
            </span>
          </div>
        `;
        container.appendChild(item);
      });
      lucide.createIcons();
    }

    function filterGuides(query) {
      const q = (query || '').toLowerCase().trim();
      if (!q) {
        renderGuideList(allGuides);
        return;
      }
      const filtered = allGuides.filter(g => 
        (g.title || '').toLowerCase().includes(q) ||
        (g.user_goal || '').toLowerCase().includes(q) ||
        (g.user_notes || '').toLowerCase().includes(q) ||
        (g.target_url || '').toLowerCase().includes(q)
      );
      renderGuideList(filtered);
    }

    function renderGuideDetail(guide) {
      const container = document.getElementById('guide-detail-container');
      if (!guide) {
        container.innerHTML = `
          <div class="bg-white border border-slate-200 rounded-2xl p-12 text-center text-slate-400 space-y-3 shadow-xs">
            <i data-lucide="sparkles" class="w-10 h-10 mx-auto text-rose-300"></i>
            <h3 class="font-bold text-base text-slate-700">Chưa có phiên hướng dẫn nào được chọn</h3>
            <p class="text-xs max-w-md mx-auto">Vui lòng chọn một phiên từ danh sách bên trái hoặc tạo hướng dẫn mới từ tab AI Tự Động Hóa.</p>
          </div>
        `;
        lucide.createIcons();
        return;
      }

      // Xây dựng danh sách các bước
      const stepsHtml = (guide.steps || []).map(s => `
        <div class="p-4 bg-slate-50 border border-slate-200 rounded-xl space-y-3">
          <div class="flex items-start justify-between gap-3">
            <div class="flex items-center gap-2">
              <span class="w-6 h-6 rounded-full bg-rose-600 text-white font-bold text-xs flex items-center justify-center shrink-0">
                ${s.step_number}
              </span>
              <h5 class="font-bold text-xs text-slate-900">${s.title}</h5>
            </div>
          </div>
          <p class="text-xs text-slate-600 leading-relaxed">${s.description || ''}</p>
          ${s.voiceover ? `
            <div class="p-2.5 bg-rose-50/70 border border-rose-200 rounded-lg text-xs text-rose-800 flex items-start gap-2">
              <i data-lucide="mic" class="w-3.5 h-3.5 text-rose-600 shrink-0 mt-0.5"></i>
              <div class="italic text-[11px]">"${s.voiceover}"</div>
            </div>
          ` : ''}
          ${s.image_url ? `
            <div class="aspect-video bg-slate-900 rounded-lg overflow-hidden border border-slate-200 max-h-64">
              <a href="${s.image_url}" target="_blank" title="Xem ảnh kích thước đầy đủ">
                <img src="${s.image_url}" class="w-full h-full object-contain hover:scale-105 transition duration-300" alt="Bước ${s.step_number}" />
              </a>
            </div>
          ` : ''}
        </div>
      `).join('');

      container.innerHTML = `
        <!-- CONTEXT CARD: YÊU CẦU & GHI CHÚ NGHIỆP VỤ BAN ĐẦU -->
        <div class="bg-white border border-slate-200 rounded-2xl p-5 shadow-xs space-y-4">
          <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 border-b border-slate-100 pb-3">
            <div>
              <div class="flex items-center gap-2">
                <span class="px-2 py-0.5 rounded-full bg-rose-100 text-rose-800 text-[10px] font-bold uppercase font-mono">Phiên ${guide.guide_id}</span>
                <span class="text-xs text-slate-400 font-mono">${guide.created_at || ''}</span>
              </div>
              <h3 class="text-base font-bold text-slate-900 mt-1">${guide.title}</h3>
            </div>
            <button onclick="deleteGuide('${guide.guide_id}')" class="px-3 py-1.5 bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 rounded-xl text-xs font-bold transition flex items-center gap-1.5 cursor-pointer shrink-0">
              <i data-lucide="trash-2" class="w-3.5 h-3.5 text-rose-600"></i> Xóa Vĩnh Viễn Phiên Này
            </button>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div class="p-3 bg-slate-50 border border-slate-200 rounded-xl space-y-1">
              <div class="text-[11px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
                <i data-lucide="target" class="w-3.5 h-3.5 text-rose-600"></i> Yêu Cầu Mục Tiêu (Goal)
              </div>
              <p class="text-xs font-semibold text-slate-800">${guide.user_goal || guide.title}</p>
            </div>

            <div class="p-3 bg-amber-50/70 border border-amber-200 rounded-xl space-y-1">
              <div class="text-[11px] font-bold text-amber-800 uppercase tracking-wider flex items-center gap-1.5">
                <i data-lucide="file-edit" class="w-3.5 h-3.5 text-amber-600"></i> Ghi Chú & Chỉ Đạo Nghiệp Vụ
              </div>
              <p class="text-xs text-amber-950 font-medium">${guide.user_notes ? guide.user_notes : '<span class="text-amber-700/60 italic font-normal">Không có ghi chú thêm</span>'}</p>
            </div>
          </div>

          <div class="flex flex-wrap items-center justify-between text-xs text-slate-500 pt-1">
            <div class="flex items-center gap-1.5">
              <i data-lucide="globe" class="w-3.5 h-3.5 text-slate-400"></i>
              <span>Hệ thống: <a href="${guide.target_url}" target="_blank" class="text-rose-600 hover:underline font-mono font-semibold">${guide.target_url}</a></span>
            </div>
            <div class="flex items-center gap-1.5">
              <i data-lucide="list-ordered" class="w-3.5 h-3.5 text-slate-400"></i>
              <span>Tổng số bước: <b class="text-slate-800">${guide.total_steps || (guide.steps ? guide.steps.length : 0)} bước</b></span>
            </div>
          </div>
        </div>

        <!-- 1-CLICK ARTIFACT DOWNLOAD CARDS -->
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <!-- Video MP4 -->
          <div class="p-3.5 bg-white border border-slate-200 rounded-xl shadow-xs space-y-2 flex flex-col justify-between">
            <div>
              <div class="w-8 h-8 rounded-lg bg-rose-50 border border-rose-200 text-rose-600 flex items-center justify-center mb-2">
                <i data-lucide="film" class="w-4 h-4"></i>
              </div>
              <h5 class="text-xs font-bold text-slate-900">Video MP4</h5>
              <p class="text-[10px] text-slate-400 font-mono mt-0.5">${guide.video ? (guide.video.size_bytes / (1024*1024)).toFixed(1) + ' MB' : 'Chưa tạo'}</p>
            </div>
            ${guide.video ? `
              <a href="${guide.video.url}" download class="w-full py-1.5 bg-slate-900 hover:bg-slate-800 text-white text-[11px] font-bold rounded-lg transition text-center flex items-center justify-center gap-1">
                <i data-lucide="download" class="w-3 h-3"></i> Tải MP4
              </a>
            ` : '<span class="text-[11px] text-slate-300">Không khả dụng</span>'}
          </div>

          <!-- Word DOCX -->
          <div class="p-3.5 bg-white border border-slate-200 rounded-xl shadow-xs space-y-2 flex flex-col justify-between">
            <div>
              <div class="w-8 h-8 rounded-lg bg-blue-50 border border-blue-200 text-blue-600 flex items-center justify-center mb-2">
                <i data-lucide="file-text" class="w-4 h-4"></i>
              </div>
              <h5 class="text-xs font-bold text-slate-900">Tài Liệu Word</h5>
              <p class="text-[10px] text-slate-400 font-mono mt-0.5">${guide.docx ? (guide.docx.size_bytes / 1024).toFixed(1) + ' KB' : 'Chưa tạo'}</p>
            </div>
            ${guide.docx ? `
              <a href="${guide.docx.url}" download class="w-full py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-[11px] font-bold rounded-lg transition text-center flex items-center justify-center gap-1">
                <i data-lucide="download" class="w-3 h-3"></i> Tải DOCX
              </a>
            ` : '<span class="text-[11px] text-slate-300">Không khả dụng</span>'}
          </div>

          <!-- PowerPoint PPTX -->
          <div class="p-3.5 bg-white border border-slate-200 rounded-xl shadow-xs space-y-2 flex flex-col justify-between">
            <div>
              <div class="w-8 h-8 rounded-lg bg-amber-50 border border-amber-200 text-amber-600 flex items-center justify-center mb-2">
                <i data-lucide="presentation" class="w-4 h-4"></i>
              </div>
              <h5 class="text-xs font-bold text-slate-900">Slide 16:9</h5>
              <p class="text-[10px] text-slate-400 font-mono mt-0.5">${guide.pptx ? (guide.pptx.size_bytes / 1024).toFixed(1) + ' KB' : 'Chưa tạo'}</p>
            </div>
            ${guide.pptx ? `
              <a href="${guide.pptx.url}" download class="w-full py-1.5 bg-amber-600 hover:bg-amber-700 text-white text-[11px] font-bold rounded-lg transition text-center flex items-center justify-center gap-1">
                <i data-lucide="download" class="w-3 h-3"></i> Tải PPTX
              </a>
            ` : '<span class="text-[11px] text-slate-300">Không khả dụng</span>'}
          </div>

          <!-- Markdown MD -->
          <div class="p-3.5 bg-white border border-slate-200 rounded-xl shadow-xs space-y-2 flex flex-col justify-between">
            <div>
              <div class="w-8 h-8 rounded-lg bg-emerald-50 border border-emerald-200 text-emerald-600 flex items-center justify-center mb-2">
                <i data-lucide="file-code" class="w-4 h-4"></i>
              </div>
              <h5 class="text-xs font-bold text-slate-900">Bản Markdown</h5>
              <p class="text-[10px] text-slate-400 font-mono mt-0.5">${guide.md ? (guide.md.size_bytes / 1024).toFixed(1) + ' KB' : 'Chưa tạo'}</p>
            </div>
            ${guide.md ? `
              <a href="${guide.md.url}" download class="w-full py-1.5 bg-slate-800 hover:bg-slate-700 text-white text-[11px] font-bold rounded-lg transition text-center flex items-center justify-center gap-1">
                <i data-lucide="download" class="w-3 h-3"></i> Tải MD
              </a>
            ` : '<span class="text-[11px] text-slate-300">Không khả dụng</span>'}
          </div>
        </div>

        <!-- VIDEO PLAYER SECTION (IF VIDEO AVAILABLE) -->
        ${guide.video ? `
          <div class="bg-white border border-slate-200 rounded-2xl p-5 shadow-xs space-y-3">
            <div class="flex items-center justify-between">
              <h4 class="font-bold text-xs uppercase tracking-wider text-slate-700 flex items-center gap-2">
                <i data-lucide="film" class="w-4 h-4 text-rose-600"></i> Video Thuyết Minh AI: ${guide.video.name}
              </h4>
              <span class="text-[11px] font-mono text-slate-400">${(guide.video.size_bytes / (1024*1024)).toFixed(1)} MB</span>
            </div>

            <div id="player-container" class="relative group aspect-video bg-slate-950 rounded-2xl overflow-hidden border border-slate-800 shadow-xl select-none flex items-center justify-center">
              <video id="custom-video" class="w-full h-full object-contain cursor-pointer" preload="metadata" playsinline src="${guide.video.url}"></video>

              <div id="big-play-btn" onclick="togglePlayPause()" class="absolute inset-0 flex items-center justify-center bg-black/25 backdrop-blur-[1px] transition-all cursor-pointer">
                <div class="w-16 h-16 rounded-full bg-rose-600 hover:bg-rose-500 text-white flex items-center justify-center shadow-2xl transform hover:scale-110 transition">
                  <i data-lucide="play" class="w-8 h-8 fill-white ml-1" id="big-play-icon"></i>
                </div>
              </div>

              <div id="player-controls" class="absolute bottom-0 inset-x-0 bg-gradient-to-t from-slate-950/95 via-slate-950/75 to-transparent px-4 pb-3 pt-8 flex flex-col gap-2 transition-opacity opacity-0 group-hover:opacity-100">
                <div class="relative w-full h-1.5 hover:h-2.5 bg-slate-700/60 rounded-full cursor-pointer transition-all flex items-center" id="scrubber-bar">
                  <div id="scrubber-buffer" class="absolute left-0 top-0 bottom-0 bg-slate-600/60 rounded-full" style="width: 0%"></div>
                  <div id="scrubber-progress" class="absolute left-0 top-0 bottom-0 bg-rose-500 rounded-full flex items-center justify-end" style="width: 0%">
                    <div class="w-3 h-3 bg-white rounded-full shadow-md"></div>
                  </div>
                </div>

                <div class="flex items-center justify-between text-white text-xs pt-0.5">
                  <div class="flex items-center gap-3">
                    <button onclick="togglePlayPause()" class="p-1 hover:text-rose-400 transition" title="Phát / Tạm dừng (Space)">
                      <i data-lucide="play" class="w-4 h-4 fill-current" id="ctrl-play-icon"></i>
                    </button>
                    <div class="flex items-center gap-1.5">
                      <button onclick="toggleMute()" class="p-1 hover:text-rose-400 transition" title="Bật / Tắt âm">
                        <i data-lucide="volume-2" class="w-4 h-4" id="ctrl-vol-icon"></i>
                      </button>
                      <input type="range" id="ctrl-vol-slider" min="0" max="1" step="0.05" value="1" oninput="setVideoVolume(this.value)" class="w-16 h-1 accent-rose-500 cursor-pointer">
                    </div>
                    <span class="font-mono text-[11px] text-slate-300" id="ctrl-time-display">00:00 / 00:00</span>
                  </div>

                  <div class="flex items-center gap-2.5">
                    <div class="relative">
                      <button onclick="toggleSpeedMenu()" id="ctrl-speed-btn" class="px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-[11px] font-mono font-semibold transition">1.0x</button>
                      <div id="ctrl-speed-menu" class="hidden absolute bottom-7 right-0 bg-slate-900 border border-slate-700 rounded-lg p-1 text-[11px] font-mono shadow-xl flex flex-col gap-0.5 z-30">
                        <button onclick="setVideoSpeed(0.75)" class="px-2.5 py-1 text-left hover:bg-rose-600 rounded">0.75x</button>
                        <button onclick="setVideoSpeed(1.0)" class="px-2.5 py-1 text-left hover:bg-rose-600 rounded">1.0x</button>
                        <button onclick="setVideoSpeed(1.25)" class="px-2.5 py-1 text-left hover:bg-rose-600 rounded">1.25x</button>
                        <button onclick="setVideoSpeed(1.5)" class="px-2.5 py-1 text-left hover:bg-rose-600 rounded">1.5x</button>
                      </div>
                    </div>
                    <button onclick="toggleFullscreen()" class="p-1 hover:text-rose-400 transition" title="Toàn màn hình (F)">
                      <i data-lucide="maximize" class="w-4 h-4"></i>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        ` : ''}

        <!-- STEP ROADMAP & VISUAL GALLERY -->
        <div class="bg-white border border-slate-200 rounded-2xl p-5 shadow-xs space-y-4">
          <div class="flex items-center justify-between border-b border-slate-100 pb-3">
            <h4 class="font-bold text-xs uppercase tracking-wider text-slate-700 flex items-center gap-2">
              <i data-lucide="layers" class="w-4 h-4 text-rose-600"></i> Lộ Trình Thao Tác Chi Tiết (${(guide.steps || []).length} bước)
            </h4>
            <span class="text-[11px] text-slate-400">Hình ảnh có hiệu ứng Spotlight & Bounding Box</span>
          </div>

          <div class="space-y-3">
            ${stepsHtml || '<p class="text-xs text-slate-400">Không có dữ liệu bước thao tác.</p>'}
          </div>
        </div>
      `;

      // Re-bind video controls
      customVideo = document.getElementById('custom-video');
      scrubberBar = document.getElementById('scrubber-bar');
      scrubberProgress = document.getElementById('scrubber-progress');
      ctrlPlayIcon = document.getElementById('ctrl-play-icon');
      bigPlayBtn = document.getElementById('big-play-btn');
      bigPlayIcon = document.getElementById('big-play-icon');
      ctrlTimeDisplay = document.getElementById('ctrl-time-display');

      if (customVideo) {
        customVideo.addEventListener('timeupdate', () => {
          if (!customVideo.duration) return;
          const pct = (customVideo.currentTime / customVideo.duration) * 100;
          if (scrubberProgress) scrubberProgress.style.width = pct + '%';
          if (ctrlTimeDisplay) ctrlTimeDisplay.innerText = formatTime(customVideo.currentTime) + ' / ' + formatTime(customVideo.duration);
        });
        customVideo.addEventListener('ended', () => {
          updatePlayIcons(false);
        });
      }
      if (scrubberBar) {
        scrubberBar.addEventListener('click', (e) => {
          if (!customVideo || !customVideo.duration) return;
          const rect = scrubberBar.getBoundingClientRect();
          const pos = (e.clientX - rect.left) / rect.width;
          customVideo.currentTime = pos * customVideo.duration;
        });
      }

      lucide.createIcons();
    }

    async function deleteGuide(guideId) {
      if (!confirm(`Bạn có chắc chắn muốn XÓA VĨNH VIỄN toàn bộ tài liệu, video, slide và ảnh của phiên '${guideId}' không?\n\nHành động này sẽ xóa hoàn toàn các file trên ổ đĩa và không thể hoàn tác.`)) {
        return;
      }
      try {
        const res = await fetch(`/api/guides/${guideId}`, { method: 'DELETE' });
        const d = await res.json();
        alert(d.message || 'Đã xóa vĩnh viễn!');
        selectedGuideId = null;
        loadGuides();
      } catch (e) {
        alert('Lỗi xóa: ' + e);
      }
    }

    async function loadSystemHealth() {
      try {
        const res = await fetch('/api/system/health');
        const h = await res.json();

        // Gemini
        const gStat = document.getElementById('sh-gemini-status');
        const gDesc = document.getElementById('sh-gemini-desc');
        if (gStat) {
          gStat.innerText = h.gemini.has_key ? 'Sẵn sàng' : 'Dùng Offline';
          gStat.className = h.gemini.has_key ? 'text-[11px] text-emerald-600 font-bold' : 'text-[11px] text-amber-600 font-bold';
        }
        if (gDesc) gDesc.innerText = `Mô hình: ${h.gemini.model} · ${h.gemini.quota}`;

        // VieNeu-TTS
        const tStat = document.getElementById('sh-tts-status');
        const tDesc = document.getElementById('sh-tts-desc');
        if (tStat) {
          tStat.innerText = h.vieneu.ready ? 'VieNeu-TTS (48kHz)' : 'Edge-TTS (Cloud)';
          tStat.className = 'text-[11px] text-emerald-600 font-bold';
        }
        if (tDesc) tDesc.innerText = `Engine: ${h.vieneu.engine} · Giọng: ${h.vieneu.voice}`;

        // Storage
        const sSize = document.getElementById('health-storage-size');
        const sFiles = document.getElementById('health-storage-files');
        const sEnv = document.getElementById('health-env-path');
        if (sSize) sSize.innerText = `${h.storage.size_mb} MB`;
        if (sFiles) sFiles.innerText = `${h.storage.total_files} tệp`;
        if (sEnv) sEnv.innerText = h.storage.env_path;
      } catch (e) {}
    }

    async function playLiveVoiceTest() {
      const text = document.getElementById('live-voice-text').value.trim();
      const engine = document.getElementById('cfg-tts-engine').value;
      const voice = engine === 'vieneu' ? document.getElementById('cfg-vieneu-voice').value : document.getElementById('cfg-voice-vi').value;
      const statusSpan = document.getElementById('live-voice-status');
      const audioEl = document.getElementById('live-audio-player');

      if (!text) { alert('Vui lòng nhập văn bản thử nghiệm!'); return; }

      statusSpan.innerText = 'Đang tổng hợp giọng nói...';
      try {
        const url = `/api/tts/preview?engine=${encodeURIComponent(engine)}&voice=${encodeURIComponent(voice)}&lang=vi&_t=${Date.now()}`;
        audioEl.src = url;
        audioEl.classList.remove('hidden');
        await audioEl.play();
        statusSpan.innerText = 'Đang phát âm thanh';
        audioEl.onended = () => { statusSpan.innerText = 'Hoàn tất phát'; };
      } catch (e) {
        statusSpan.innerText = 'Lỗi phát: ' + e;
      }
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
        loadSystemHealth();
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
      loadGuides();
      loadSystemHealth();
    });
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)
