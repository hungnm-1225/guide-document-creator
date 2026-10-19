"""
Pipeline Orchestrator
Điều phối toàn bộ quy trình từ Khảo sát địa hình -> Thực thi kịch bản -> Phân tích AI -> Highlight ảnh -> Sản xuất tài liệu & video.
"""
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from config.settings import settings
from src.ai.schemas import (
    GuideBook,
    ProcessedStep,
    RecordedAction,
    StepAIAnalysis,
    BilingualText,
    ReconReport,
)
from src.browser.crawler import UIReconnaissance
from src.browser.executor import BrowserExecutor, ActionStepConfig
from src.browser.autonomous import AutonomousFlowAgent
from src.browser.recorder import InteractiveRecorder
from src.ai.gemini_client import GeminiAnalyzer
from src.visual.annotator import VisualAnnotator
from src.generators.docx_gen import DocumentGenerator
from src.generators.pptx_gen import SlideGenerator
from src.generators.video_gen import VideoGenerator

console = Console()


class GuidePipeline:
    """Master Pipeline orchestrating the automated creation of user guides"""

    def __init__(self, headless: Optional[bool] = None):
        settings.ensure_directories()
        self.headless = settings.headless if headless is None else headless
        self.crawler = UIReconnaissance(headless=self.headless)
        self.executor = BrowserExecutor(headless=self.headless)
        self.auto_agent = AutonomousFlowAgent(headless=self.headless)
        self.recorder = InteractiveRecorder()
        self.analyzer = GeminiAnalyzer()
        self.annotator = VisualAnnotator()
        self.doc_gen = DocumentGenerator()
        self.slide_gen = SlideGenerator()
        self.video_gen = VideoGenerator()

    async def run_scenario_pipeline(
        self,
        scenario_data: Dict[str, Any],
        languages: List[str] = ["vi", "en"],
        generate_video: bool = True,
        cleanup_temp: bool = False,
    ) -> Dict[str, Any]:
        """
        Quy trình chuẩn thực hiện kịch bản:
        1. Khảo sát địa hình trang web mục tiêu (Tránh đoán mò)
        2. Tự động tương tác và ghi nhận log + screenshot + bounding box
        3. Phân tích Multimodal (Gemini 2.5 Flash) sinh nội dung song ngữ
        4. Vẽ highlight spotlight, bounding box, badge số bước
        5. Xuất bản Document (DOCX), Slides (PPTX), Video MP4 (Edge-TTS)
        """
        start_time = time.time()
        title_vi = scenario_data.get("title_vi") or scenario_data.get("title", "Hướng dẫn sử dụng hệ thống")
        title_en = scenario_data.get("title_en", "System User Guide")
        summary_vi = scenario_data.get("summary_vi") or scenario_data.get("summary", "Tài liệu hướng dẫn thao tác chi tiết.")
        summary_en = scenario_data.get("summary_en", "Step-by-step practical guide.")
        target_url = scenario_data.get("url") or scenario_data.get("initial_url", "")
        steps_cfg = scenario_data.get("steps", [])

        console.print(Panel.fit(
            f"[bold yellow]🎯 BẮT ĐẦU QUY TRÌNH TẠO HƯỚNG DẪN[/bold yellow]\n"
            f"[bold]Tiêu đề:[/bold] {title_vi} / {title_en}\n"
            f"[bold]URL:[/bold] {target_url}\n"
            f"[bold]Số bước cấu hình:[/bold] {len(steps_cfg)}\n"
            f"[bold]Ngôn ngữ xuất:[/bold] {', '.join(languages).upper()}\n"
            f"[bold]Tạo video thuyết minh:[/bold] {'Có' if generate_video else 'Không'}",
            border_style="cyan"
        ))

        # ---------------------------------------------------------
        # BƯỚC 1: KHẢO SÁT ĐỊA HÌNH (RECONNAISSANCE)
        # ---------------------------------------------------------
        recon_report: Optional[ReconReport] = None
        if target_url:
            console.print("\n[bold cyan]📍 Giai đoạn 1: Khảo sát địa hình giao diện thực tế...[/bold cyan]")
            try:
                recon_report = await self.crawler.survey_url(
                    url=target_url,
                    login_credentials=scenario_data.get("credentials")
                )
            except Exception as e:
                console.print(f"[yellow]⚠️ Khảo sát gặp lỗi (sẽ tiếp tục thực thi kịch bản): {e}[/yellow]")

        # ---------------------------------------------------------
        # BƯỚC 2: THỰC THI KỊCH BẢN VÀ GHI NHẬN (ACTION RECORDING)
        # ---------------------------------------------------------
        console.print("\n[bold cyan]📍 Giai đoạn 2: Tự động thao tác trên trình duyệt và ghi nhận tọa độ...[/bold cyan]")
        recorded_actions = await self.executor.execute_scenario(
            scenario_steps=steps_cfg,
            initial_url=target_url
        )

        if not recorded_actions:
            raise RuntimeError("Không có hành động nào được ghi nhận thành công từ trình duyệt.")

        # ---------------------------------------------------------
        # BƯỚC 3 & 4: PHÂN TÍCH AI MULTIMODAL & HIGHLIGHT HÌNH ẢNH
        # ---------------------------------------------------------
        console.print("\n[bold cyan]📍 Giai đoạn 3 & 4: Phân tích Multimodal (Gemini) & Vẽ Highlight tương tác...[/bold cyan]")
        processed_steps: List[ProcessedStep] = []
        total_steps = len(recorded_actions)

        for action in recorded_actions:
            console.print(f"  ⚡ Đang phân tích bước {action.step_number}/{total_steps}: [dim]{action.element_text or action.selector}[/dim]")
            
            # Phân tích nội dung bước bằng Gemini API (hoặc fallback thông minh)
            step_ai = self.analyzer.analyze_step(
                action=action,
                total_steps=total_steps,
                guide_context=f"{title_vi} ({target_url})"
            )

            # Vẽ highlight viền đỏ bo tròn, spotlight, badge số bước lên screenshot
            annotated_img_path = self.annotator.annotate_screenshot(
                raw_image_path=action.raw_screenshot_path,
                bounding_box=action.bounding_box,
                step_number=action.step_number,
                draw_spotlight=True,
                draw_badge=True,
                draw_cursor=True
            )

            processed_step = ProcessedStep(
                step_number=action.step_number,
                raw_action=action,
                ai_content=step_ai,
                annotated_screenshot_path=annotated_img_path
            )
            processed_steps.append(processed_step)

        guidebook = GuideBook(
            guide_id=f"guide_{int(time.time())}",
            title=BilingualText(vi=title_vi, en=title_en),
            summary=BilingualText(vi=summary_vi, en=summary_en),
            target_url=target_url or "Web Application",
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            steps=processed_steps,
            recon_report=recon_report
        )

        # ---------------------------------------------------------
        # BƯỚC 5: SẢN XUẤT ARTIFACTS (DOCX, PPTX, VIDEO MP4)
        # ---------------------------------------------------------
        console.print("\n[bold cyan]📍 Giai đoạn 5: Sản xuất các sản phẩm đầu ra (DOCX, PPTX, MP4)...[/bold cyan]")
        artifacts: Dict[str, str] = {}

        # 1. Sinh DOCX & Markdown
        for lang in languages:
            docx_path = self.doc_gen.generate_docx(guidebook, lang=lang)
            md_path = self.doc_gen.generate_markdown(guidebook, lang=lang)
            artifacts[f"docx_{lang}"] = docx_path
            artifacts[f"md_{lang}"] = md_path
            console.print(f"  📄 Word Doc ({lang.upper()}): [green]{docx_path}[/green]")

        # 2. Sinh PPTX Slides 16:9
        for lang in languages:
            pptx_path = self.slide_gen.generate_pptx(guidebook, lang=lang)
            artifacts[f"pptx_{lang}"] = pptx_path
            console.print(f"  📊 Slide 16:9 ({lang.upper()}): [green]{pptx_path}[/green]")

        # 3. Sinh Video MP4 nếu được yêu cầu
        if generate_video:
            for lang in languages:
                # Tạo audio giọng đọc AI cho từng bước
                await self.video_gen.generate_speech_for_steps(guidebook, lang=lang)
                # Dựng video mp4
                video_path = self.video_gen.generate_video(guidebook, lang=lang)
                artifacts[f"video_{lang}"] = video_path
                console.print(f"  🎥 Video ({lang.upper()}): [green]{video_path}[/green]")

        guidebook.artifacts = artifacts
        try:
            guide_file = settings.output_dir / "guides" / f"{guidebook.guide_id}.json"
            guide_file.parent.mkdir(parents=True, exist_ok=True)
            guide_file.write_text(guidebook.model_dump_json(indent=2), encoding="utf-8")
        except Exception:
            pass

        # ---------------------------------------------------------
        # DỌN DẸP ASSETS TẠM NẾU BẬT CỜ
        # ---------------------------------------------------------
        if cleanup_temp:
            self._cleanup_temp_assets()

        elapsed = time.time() - start_time
        console.print(f"\n[bold green]✨ TOÀN BỘ QUY TRÌNH HOÀN THÀNH TRONG {elapsed:.1f} GIÂY![/bold green]")
        self._print_summary_table(artifacts)

        return {
            "guidebook": guidebook,
            "artifacts": artifacts,
            "elapsed_seconds": elapsed
        }

    async def run_autonomous_guide(
        self,
        url: str,
        user_goal: str,
        credentials: Optional[Dict[str, str]] = None,
        languages: List[str] = ["vi", "en"],
        generate_video: bool = True
    ) -> Dict[str, Any]:
        """
        Quy trình tự động theo Mục tiêu:
        Khảo sát địa hình -> Gemini lập kế hoạch thực tế -> Chạy kịch bản
        """
        console.print(f"[bold cyan]🔍 Bắt đầu khảo sát địa hình website:[/bold cyan] {url}")
        recon = await self.crawler.survey_url(url, login_credentials=credentials)

        console.print(f"[bold cyan]🤖 Gemini đang lên kế hoạch thao tác cho mục tiêu:[/bold cyan] '{user_goal}'")
        planned_steps = self.analyzer.plan_steps_from_recon(recon, user_goal)

        console.print(f"📋 Kế hoạch gồm {len(planned_steps)} bước thao tác thực tế:")
        for idx, s in enumerate(planned_steps, start=1):
            console.print(f"   {idx}. [{s.get('action')}] {s.get('target')} - {s.get('label')}")

        scenario_data = {
            "title_vi": f"Hướng dẫn: {user_goal}",
            "title_en": f"Guide: {user_goal}",
            "summary_vi": f"Tài liệu hướng dẫn tự động thực hiện: {user_goal} trên {url}.",
            "summary_en": f"Automated walk-through guide for: {user_goal} on {url}.",
            "url": url,
            "credentials": credentials,
            "steps": planned_steps
        }

        return await self.run_scenario_pipeline(
            scenario_data=scenario_data,
            languages=languages,
            generate_video=generate_video
        )

    async def run_autonomous_flow(
        self,
        url: str,
        goal: str = "Đăng nhập hệ thống và xem tổng quan",
        credentials: Optional[Dict[str, str]] = None,
        languages: List[str] = ["vi", "en"],
        generate_video: bool = True,
        user_notes: Optional[str] = None,
        enable_two_pass: bool = True,
    ) -> Dict[str, Any]:
        """
        Quy trình AI Tự Động Hóa (Kiến trúc 2 lượt):
        Lượt 1: Pioneer Scout đi tiền trạm, khảo sát ngóc ngách, sau đăng nhập.
        Lượt 2: Deep Guide Producer bám theo Sơ đồ kiến thức + Ghi chú người dùng để thực thi chi tiết.
        """
        start_time = time.time()
        title_vi = f"Hướng Dẫn {goal}"
        title_en = f"Walkthrough: {goal}"
        summary_vi = f"Tài liệu hướng dẫn tự động thực hiện: {goal} trên {url}."
        summary_en = f"Step-by-step practical guide for: {goal} on {url}."

        console.print(Panel.fit(
            f"[bold green]🌟 BẮT ĐẦU CHẾ ĐỘ AI TỰ ĐỘNG HÓA 2 LƯỢT (TWO-PASS AUTO PILOT)[/bold green]\n"
            f"[bold]Mục tiêu:[/bold] {goal}\n"
            f"[bold]URL ban đầu:[/bold] {url}\n"
            f"[bold]Ghi chú người dùng:[/bold] {user_notes or 'Không có'}\n"
            f"[bold]Chế độ 2 lượt (Scout ➔ Deep Walkthrough):[/bold] {'Bật' if enable_two_pass else 'Tắt'}",
            border_style="green"
        ))

        k_map = None
        if enable_two_pass:
            try:
                k_map = await self.auto_agent.pioneer_scout(url=url, credentials=credentials)
            except Exception as e:
                console.print(f"[yellow]⚠️ Lượt 1 (Scout) gặp cảnh báo: {e}[/yellow]")

        # Thực thi Deep Producer
        recorded_actions = await self.auto_agent.execute_autonomous_guide(
            url=url,
            goal=goal,
            credentials=credentials,
            user_notes=user_notes,
            knowledge_map=k_map
        )

        return await self._process_actions_to_artifacts(
            recorded_actions=recorded_actions,
            title_vi=title_vi,
            title_en=title_en,
            summary_vi=summary_vi,
            summary_en=summary_en,
            target_url=url,
            languages=languages,
            generate_video=generate_video,
            start_time=start_time,
            user_notes=user_notes,
            goal=goal
        )

    async def run_recorder_flow(
        self,
        initial_url: str = "https://learn.pythaverse.space",
        title_vi: str = "Hướng Dẫn Thao Tác Hệ Thống",
        title_en: str = "System Walkthrough Guide",
        languages: List[str] = ["vi", "en"],
        generate_video: bool = True
    ) -> Dict[str, Any]:
        """
        Quy trình Trợ Lý Ghi Hình Trực Quan (Tango / Scribe Style):
        Mở trình duyệt thật để người dùng click tự do, sau đó tự sinh DOCX, PPTX, Video.
        """
        start_time = time.time()
        recorded_actions = await self.recorder.start_recording_session(initial_url)
        if not recorded_actions:
            raise RuntimeError("Không có thao tác nào được ghi nhận.")

        return await self._process_actions_to_artifacts(
            recorded_actions=recorded_actions,
            title_vi=title_vi,
            title_en=title_en,
            summary_vi=f"Tài liệu hướng dẫn thao tác ghi lại trên {initial_url}.",
            summary_en=f"Interactive recorded guide for {initial_url}.",
            target_url=initial_url,
            languages=languages,
            generate_video=generate_video,
            start_time=start_time
        )

    async def _process_actions_to_artifacts(
        self,
        recorded_actions: List[RecordedAction],
        title_vi: str,
        title_en: str,
        summary_vi: str,
        summary_en: str,
        target_url: str,
        languages: List[str],
        generate_video: bool,
        start_time: float,
        user_notes: Optional[str] = None,
        goal: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Quy trình chung biến RecordedAction -> ProcessedStep -> DOCX, PPTX, Video"""
        total_steps = len(recorded_actions)
        processed_steps: List[ProcessedStep] = []

        console.print("\n[bold cyan]📍 Đang phân tích AI & Phủ hiệu ứng Spotlight lên từng bước...[/bold cyan]")
        for action in recorded_actions:
            console.print(f"  ⚡ Đang phân tích bước {action.step_number}/{total_steps}: [dim]{action.element_text}[/dim]")
            step_ai = self.analyzer.analyze_step(
                action=action,
                total_steps=total_steps,
                guide_context=f"{title_vi} ({target_url})",
                user_notes=user_notes
            )

            annotated_img_path = self.annotator.annotate_screenshot(
                raw_image_path=action.raw_screenshot_path,
                bounding_box=action.bounding_box,
                step_number=action.step_number,
                draw_spotlight=True,
                draw_badge=True,
                draw_cursor=True
            )

            processed_step = ProcessedStep(
                step_number=action.step_number,
                raw_action=action,
                ai_content=step_ai,
                annotated_screenshot_path=annotated_img_path
            )
            processed_steps.append(processed_step)

        gid = f"guide_{int(time.time())}"
        guidebook = GuideBook(
            guide_id=gid,
            title=BilingualText(vi=title_vi, en=title_en),
            summary=BilingualText(vi=summary_vi, en=summary_en),
            target_url=target_url,
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            user_goal=goal or title_vi,
            user_notes=user_notes,
            steps=processed_steps,
            recon_report=None
        )

        console.print("\n[bold cyan]📍 Đang xuất bản tài liệu Word, Slide 16:9 & Video thuyết minh AI...[/bold cyan]")
        artifacts: Dict[str, str] = {}
        for lang in languages:
            artifacts[f"docx_{lang}"] = self.doc_gen.generate_docx(guidebook, lang=lang)
            artifacts[f"md_{lang}"] = self.doc_gen.generate_markdown(guidebook, lang=lang)
            artifacts[f"pptx_{lang}"] = self.slide_gen.generate_pptx(guidebook, lang=lang)

        if generate_video:
            for lang in languages:
                await self.video_gen.generate_speech_for_steps(guidebook, lang=lang)
                artifacts[f"video_{lang}"] = self.video_gen.generate_video(guidebook, lang=lang)

        guidebook.artifacts = artifacts

        # Lưu manifest JSON của Guide để giao diện Thư viện Thành phẩm hiển thị chi tiết ngữ cảnh
        try:
            guide_file = settings.output_dir / "guides" / f"{gid}.json"
            guide_file.parent.mkdir(parents=True, exist_ok=True)
            guide_file.write_text(guidebook.model_dump_json(indent=2), encoding="utf-8")
        except Exception as e:
            console.print(f"[yellow]⚠️ Lưu guide manifest: {e}[/yellow]")

        elapsed = time.time() - start_time
        console.print(f"\n[bold green]✨ HOÀN TẤT TRONG {elapsed:.1f} GIÂY![/bold green]")
        self._print_summary_table(artifacts)
        return {"guidebook": guidebook, "artifacts": artifacts, "elapsed_seconds": elapsed}
        """Xóa các file audio và ảnh raw tạm thời nếu người dùng muốn dọn dẹp"""
        console.print("[dim]🧹 Đang dọn dẹp tài nguyên trung gian...[/dim]")
        # Không xóa output chính (docx, pptx, videos)
        for audio_file in (settings.output_dir / "audio").glob("*.mp3"):
            try:
                audio_file.unlink()
            except Exception:
                pass

    def _print_summary_table(self, artifacts: Dict[str, str]):
        """In bảng thống kê các file thành phẩm đã được tạo"""
        table = Table(title="📁 DANH SÁCH FILE THÀNH PHẨM ĐÃ XUẤT", show_lines=True)
        table.add_column("Loại tài liệu", style="cyan", no_wrap=True)
        table.add_column("Đường dẫn file", style="green")

        for key, path_str in artifacts.items():
            table.add_row(key.upper(), path_str)

        console.print(table)
