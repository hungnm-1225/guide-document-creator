"""
Interactive Visual Recorder (Trình ghi thao tác trực quan theo phong cách Tango / Scribe)
Cho phép người dùng mở trình duyệt thật và thao tác tự do (kể cả đăng nhập OpenID SSO, click bất kỳ đâu).
Hệ thống ngầm tự động ghi nhận mọi cú click, tọa độ bounding box và ảnh chụp màn hình tương ứng.
"""
import asyncio
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from playwright.async_api import async_playwright, Browser, Page, BrowserContext
from rich.console import Console

from config.settings import settings
from src.ai.schemas import RecordedAction, BoundingBox

console = Console()


class InteractiveRecorder:
    """Smart recorder listening to real user interactions in a live browser"""

    def __init__(self):
        self.output_raw_dir = settings.output_dir / "raw_screenshots"
        self.output_raw_dir.mkdir(parents=True, exist_ok=True)
        self.recorded_actions: List[RecordedAction] = []
        self.is_recording = False
        self._step_counter = 0

    async def _safe_screenshot(self, page: Page, path: str, max_retries: int = 4, delay: float = 1.0) -> bool:
        """Chụp ảnh an toàn với cơ chế retry khi trang đang chuyển hướng"""
        for attempt in range(max_retries):
            try:
                if page.is_closed():
                    return False
                await page.screenshot(path=path, timeout=5000)
                return True
            except Exception:
                if attempt < max_retries - 1:
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=4000)
                    except Exception:
                        pass
                    await asyncio.sleep(delay * (attempt + 1))
                else:
                    try:
                        await asyncio.sleep(1.5)
                        await page.screenshot(path=path, timeout=8000)
                        return True
                    except Exception:
                        return False
        return False

    async def start_recording_session(self, initial_url: str = "https://learn.pythaverse.space") -> List[RecordedAction]:
        """
        Mở cửa sổ trình duyệt thực tế để người dùng thao tác tự do.
        Tự động ghi lại từng cú click và thay đổi ô nhập mà không cần cấu hình kỹ thuật.
        """
        self.recorded_actions = []
        self._step_counter = 0
        self.is_recording = True

        console.print("\n[bold magenta]🎥 [Interactive Recorder][/bold magenta] Đang mở trình duyệt tương tác...")
        console.print("[dim]Hãy thao tác bình thường trên trang web. Hệ thống sẽ tự động ghi lại từng bước.[/dim]\n")

        async with async_playwright() as p:
            # Mở trình duyệt có giao diện (headless=False)
            channels = ["msedge", "chrome", None]
            browser: Optional[Browser] = None
            for ch in channels:
                try:
                    kwargs = {"headless": False}
                    if ch:
                        kwargs["channel"] = ch
                    browser = await p.chromium.launch(**kwargs)
                    break
                except Exception:
                    continue

            if not browser:
                raise RuntimeError("Không thể mở cửa sổ trình duyệt tương tác.")

            context = await browser.new_context(
                viewport={"width": settings.viewport_width, "height": settings.viewport_height}
            )

            # Đăng ký hàm nhận sự kiện từ JavaScript trên trang web
            async def on_user_interaction(source, event_data):
                if not self.is_recording:
                    return
                await self._handle_client_event(source["page"], event_data)

            await context.expose_binding("recordGuideAction", on_user_interaction)

            # Inject script ghi nhận sự kiện trên mọi trang (kể cả redirect OpenID)
            inject_js = """
            (() => {
                let lastClickTime = 0;
                document.addEventListener('click', (e) => {
                    const now = Date.now();
                    if (now - lastClickTime < 500) return; // Debounce
                    lastClickTime = now;

                    const target = e.target.closest('button, a, input, select, textarea, [role="button"]') || e.target;
                    const rect = target.getBoundingClientRect();
                    const text = (target.innerText || target.value || target.placeholder || target.getAttribute('aria-label') || '').trim();

                    let sel = target.tagName.toLowerCase();
                    if (target.id) sel += '#' + target.id;
                    else if (target.className && typeof target.className === 'string') {
                        const c = target.className.trim().split(/\\s+/)[0];
                        if (c && !c.includes(':')) sel += '.' + c;
                    }

                    window.recordGuideAction({
                        action_type: 'click',
                        text: text.substring(0, 100),
                        selector: sel,
                        bounding_box: {
                            x: Math.round(rect.left),
                            y: Math.round(rect.top),
                            w: Math.round(rect.width),
                            h: Math.round(rect.height)
                        }
                    });
                }, true);

                document.addEventListener('change', (e) => {
                    const target = e.target;
                    if (target.tagName.toLowerCase() === 'input' || target.tagName.toLowerCase() === 'textarea') {
                        const rect = target.getBoundingClientRect();
                        const val = target.type === 'password' ? '••••••••' : target.value;
                        const label = target.placeholder || target.name || target.id || 'ô nhập';

                        window.recordGuideAction({
                            action_type: 'fill',
                            text: label,
                            value: val,
                            selector: target.tagName.toLowerCase() + (target.id ? '#' + target.id : ''),
                            bounding_box: {
                                x: Math.round(rect.left),
                                y: Math.round(rect.top),
                                w: Math.round(rect.width),
                                h: Math.round(rect.height)
                            }
                        });
                    }
                }, true);
            })();
            """
            await context.add_init_script(inject_js)

            page = await context.new_page()

            # Chụp bước đầu tiên: Mở URL
            self._step_counter += 1
            await page.goto(initial_url, wait_until="domcontentloaded")
            await asyncio.sleep(1.0)

            raw_path_init = str(self.output_raw_dir / f"step_{self._step_counter:02d}_raw.png")
            await self._safe_screenshot(page, raw_path_init)
            self.recorded_actions.append(
                RecordedAction(
                    step_number=self._step_counter,
                    action_type="navigate",
                    selector=initial_url,
                    input_value=initial_url,
                    element_text=f"Truy cập {await page.title() or initial_url}",
                    bounding_box=None,
                    raw_screenshot_path=raw_path_init,
                    timestamp=time.time(),
                    page_title=await page.title() or "",
                    page_url=page.url
                )
            )
            console.print(f"✔️ Đã ghi nhận Bước 1: Mở {initial_url}")

            # Giữ phiên ghi cho đến khi người dùng đóng trình duyệt
            console.print("[yellow]💡 Gợi ý: Hãy thao tác trên trình duyệt. Khi hoàn thành, chỉ cần ĐÓNG CỬA SỔ TRÌNH DUYỆT lại.[/yellow]")
            while browser.is_connected() and len(context.pages) > 0:
                await asyncio.sleep(0.5)

            console.print(f"\n[green]✅ Đã hoàn tất phiên ghi thao tác. Thu thập được {len(self.recorded_actions)} bước.[/green]")

        return self.recorded_actions

    async def _handle_client_event(self, page: Page, event_data: Dict[str, Any]):
        """Xử lý sự kiện gửi từ client trong lúc người dùng thao tác"""
        self._step_counter += 1
        bbox_data = event_data.get("bounding_box")
        bbox = BoundingBox(
            x=bbox_data["x"],
            y=bbox_data["y"],
            w=bbox_data["w"],
            h=bbox_data["h"]
        ) if bbox_data else None

        raw_path = str(self.output_raw_dir / f"step_{self._step_counter:02d}_raw.png")
        await self._safe_screenshot(page, raw_path)

        action_rec = RecordedAction(
            step_number=self._step_counter,
            action_type=event_data.get("action_type", "click"),
            selector=event_data.get("selector"),
            input_value=event_data.get("value"),
            element_text=event_data.get("text") or event_data.get("selector") or "Phần tử",
            bounding_box=bbox,
            raw_screenshot_path=raw_path,
            timestamp=time.time(),
            page_title=await page.title() or "",
            page_url=page.url
        )
        self.recorded_actions.append(action_rec)
        console.print(f"  📸 Đã tự động ghi nhận Bước {self._step_counter}: [{action_rec.action_type.upper()}] {action_rec.element_text}")
