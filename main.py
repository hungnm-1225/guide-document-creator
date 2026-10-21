"""
Main CLI Entrypoint for Guide Document Creator
Automated User-Guide Content Factory (Docs, Slides, Videos)
"""
import sys
import json
import asyncio
from pathlib import Path
from typing import Optional, List

# Khắc phục lỗi ConnectionResetError [WinError 10054] do ProactorEventLoop trên Windows khi client đóng tab/kết nối đột ngột
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass
import typer
from rich.console import Console
from rich.panel import Panel

from config.settings import settings
from src.pipeline.orchestrator import GuidePipeline

app = typer.Typer(
    name="guide-creator",
    help="Hệ thống tự động hóa khảo sát website và sản xuất tài liệu hướng dẫn người dùng (DOCX, Slides 16:9, Video MP4).",
    add_completion=False,
)
console = Console()


@app.command("run-scenario")
def run_scenario(
    scenario: Path = typer.Option(..., "--scenario", "-s", help="Đường dẫn tới file kịch bản JSON"),
    lang: str = typer.Option("both", "--lang", "-l", help="Ngôn ngữ xuất: 'vi', 'en', hoặc 'both'"),
    video: bool = typer.Option(True, "--video/--no-video", help="Bật/tắt việc tạo video thuyết minh MP4"),
    headless: bool = typer.Option(True, "--headless/--no-headless", help="Chạy trình duyệt ngầm (headless) hoặc hiện giao diện"),
    cleanup: bool = typer.Option(False, "--cleanup", help="Tự động dọn dẹp các tệp âm thanh trung gian sau khi hoàn tất"),
):
    """Thực thi một kịch bản thao tác có sẵn từ file JSON và sản xuất trọn bộ tài liệu."""
    if not scenario.exists():
        console.print(f"[bold red]❌ File kịch bản không tồn tại: {scenario}[/bold red]")
        raise typer.Exit(1)

    try:
        with open(scenario, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        console.print(f"[bold red]❌ Lỗi đọc file JSON: {e}[/bold red]")
        raise typer.Exit(1)

    # Xử lý ngôn ngữ
    if lang.lower() == "vi":
        languages = ["vi"]
    elif lang.lower() == "en":
        languages = ["en"]
    else:
        languages = ["vi", "en"]

    pipeline = GuidePipeline(headless=headless)
    asyncio.run(
        pipeline.run_scenario_pipeline(
            scenario_data=data,
            languages=languages,
            generate_video=video,
            cleanup_temp=cleanup,
        )
    )


@app.command("auto-guide")
def auto_guide(
    url: str = typer.Option(..., "--url", "-u", help="URL website cần tạo hướng dẫn"),
    goal: str = typer.Option(..., "--goal", "-g", help="Mục tiêu hướng dẫn (ví dụ: 'Đăng nhập và tìm kiếm sản phẩm')"),
    user: Optional[str] = typer.Option(None, "--user", help="Tài khoản đăng nhập (nếu có)"),
    password: Optional[str] = typer.Option(None, "--password", help="Mật khẩu đăng nhập (nếu có)"),
    lang: str = typer.Option("both", "--lang", "-l", help="Ngôn ngữ xuất: 'vi', 'en', hoặc 'both'"),
    video: bool = typer.Option(True, "--video/--no-video", help="Bật/tắt việc tạo video thuyết minh MP4"),
    headless: bool = typer.Option(True, "--headless/--no-headless", help="Chạy trình duyệt ngầm (headless)"),
):
    """Khảo sát thực tế website -> AI tự lập kế hoạch -> Thực thi và sản xuất tài liệu."""
    languages = ["vi"] if lang == "vi" else (["en"] if lang == "en" else ["vi", "en"])
    creds = {"username": user, "password": password} if (user or password) else None

    pipeline = GuidePipeline(headless=headless)
    asyncio.run(
        pipeline.run_autonomous_guide(
            url=url,
            user_goal=goal,
            credentials=creds,
            languages=languages,
            generate_video=video,
        )
    )


@app.command("survey")
def survey_only(
    url: str = typer.Option(..., "--url", "-u", help="URL website cần khảo sát"),
    user: Optional[str] = typer.Option(None, "--user", help="Tài khoản đăng nhập"),
    password: Optional[str] = typer.Option(None, "--password", help="Mật khẩu đăng nhập"),
    headless: bool = typer.Option(True, "--headless/--no-headless", help="Chạy trình duyệt ngầm"),
):
    """Chỉ thực hiện khảo sát địa hình giao diện thực tế (UI Reconnaissance) và xuất báo cáo."""
    from src.browser.crawler import UIReconnaissance

    crawler = UIReconnaissance(headless=headless)
    creds = {"username": user, "password": password} if (user or password) else None

    async def _do_survey():
        report = await crawler.survey_url(url, login_credentials=creds)
        report_file = settings.output_dir / "recon" / "recon_report.json"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))
        console.print(f"\n[bold green]✅ Báo cáo khảo sát đã được lưu vào:[/bold green] {report_file}")

    asyncio.run(_do_survey())


@app.command("demo")
def run_demo(
    video: bool = typer.Option(True, "--video/--no-video", help="Bật/tắt việc tạo video thuyết minh MP4"),
    lang: str = typer.Option("both", "--lang", "-l", help="Ngôn ngữ xuất: 'vi', 'en', hoặc 'both'"),
):
    """Chạy ngay một kịch bản demo hoàn chỉnh không cần kết nối mạng bên ngoài."""
    demo_html = Path(__file__).resolve().parent / "examples" / "demo_app.html"
    scenario_path = Path(__file__).resolve().parent / "examples" / "demo_scenario.json"

    with open(scenario_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Sử dụng file URI chuẩn cho browser
    data["url"] = demo_html.as_uri()

    languages = ["vi"] if lang == "vi" else (["en"] if lang == "en" else ["vi", "en"])
    pipeline = GuidePipeline(headless=True)
    asyncio.run(
        pipeline.run_scenario_pipeline(
            scenario_data=data,
            languages=languages,
            generate_video=video,
            cleanup_temp=False,
        )
    )


@app.command("web")
def run_web(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host lắng nghe của Web Server"),
    port: int = typer.Option(8000, "--port", "-p", help="Cổng lắng nghe của Web Server"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Tự động mở trình duyệt"),
):
    """Khởi chạy giao diện Web trực quan (Studio Dashboard) trên trình duyệt."""
    import uvicorn
    import webbrowser

    console.print(Panel.fit(
        f"[bold green]🚀 GUIDE CREATOR STUDIO ĐANG CHẠY[/bold green]\n\n"
        f"🌐 Truy cập giao diện trực quan tại: [bold underline cyan]http://{host}:{port}[/bold underline cyan]\n"
        f"✨ Thao tác kéo thả, tạo kịch bản, khảo sát địa hình DOM và xuất Word/Slide/Video trực quan.",
        border_style="green"
    ))

    if open_browser:
        async def _open():
            await asyncio.sleep(1.0)
            webbrowser.open(f"http://{host}:{port}")
        # schedule browser open
        import threading
        threading.Thread(target=lambda: webbrowser.open(f"http://{host}:{port}"), daemon=True).start()

    uvicorn.run("src.web.app:app", host=host, port=port, reload=False)


@app.command("ui")
def run_ui(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host"),
    port: int = typer.Option(8000, "--port", "-p", help="Port"),
):
    """Bí danh (alias) của lệnh 'web'."""
    run_web(host=host, port=port, open_browser=True)


if __name__ == "__main__":
    app()
