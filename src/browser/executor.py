"""
Browser Executor Module (Action Tracker & Automation)
Thực hiện kịch bản thao tác tự động từ A-Z:
- Mở website
- Tự gõ tài khoản, mật khẩu, click nút, chọn dropdown, scroll
- Lưu tọa độ bounding box chính xác của element
- Chụp ảnh màn hình raw trước/sau tương tác
"""
import asyncio
import time
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from playwright.async_api import async_playwright, Browser, Page, BrowserContext
from rich.console import Console

from config.settings import settings
from src.ai.schemas import RecordedAction, BoundingBox

console = Console()


class ActionStepConfig:
    """Cấu hình định nghĩa cho một bước tương tác của kịch bản"""
    def __init__(
        self,
        action: str,  # "navigate", "click", "fill", "select", "hover", "wait"
        target: Optional[str] = None,  # selector hoặc text
        value: Optional[str] = None,  # giá trị cần nhập/chọn
        label: Optional[str] = None,  # nhãn mô tả bước (nếu có)
        wait_after: float = 1.0  # số giây chờ sau tương tác
    ):
        self.action = action.lower()
        self.target = target
        self.value = value
        self.label = label
        self.wait_after = wait_after

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActionStepConfig":
        return cls(
            action=data.get("action", "click"),
            target=data.get("target") or data.get("selector"),
            value=data.get("value") or data.get("text"),
            label=data.get("label") or data.get("name"),
            wait_after=float(data.get("wait_after", 1.0))
        )


class BrowserExecutor:
    """Manages browser session, executes scripted actions, and captures raw data"""

    def __init__(self, headless: Optional[bool] = None):
        self.headless = settings.headless if headless is None else headless
        self.output_raw_dir = settings.output_dir / "raw_screenshots"
        self.output_raw_dir.mkdir(parents=True, exist_ok=True)

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

    async def _launch_browser(self, p) -> Browser:
        """Launch browser with fallback to system channels"""
        channels = ["msedge", "chrome", None]
        last_error = None
        for ch in channels:
            try:
                kwargs = {"headless": self.headless}
                if ch:
                    kwargs["channel"] = ch
                return await p.chromium.launch(**kwargs)
            except Exception as e:
                last_error = e
                continue
        raise RuntimeError(f"Could not launch browser: {last_error}")

    async def execute_scenario(
        self,
        scenario_steps: List[Union[Dict[str, Any], ActionStepConfig]],
        initial_url: Optional[str] = None,
    ) -> List[RecordedAction]:
        """
        Thực hiện toàn bộ kịch bản từ A-Z và thu thập danh sách RecordedAction
        """
        recorded_actions: List[RecordedAction] = []
        step_configs = [
            s if isinstance(s, ActionStepConfig) else ActionStepConfig.from_dict(s)
            for s in scenario_steps
        ]

        console.print(f"[bold green]🚀 [Browser Executor][/bold green] Bắt đầu thực hiện kịch bản ({len(step_configs)} bước)...")

        async with async_playwright() as p:
            browser = await self._launch_browser(p)
            context = await browser.new_context(
                viewport={"width": settings.viewport_width, "height": settings.viewport_height}
            )
            page = await context.new_page()

            if initial_url:
                console.print(f"🌐 Truy cập điểm khởi đầu: [underline]{initial_url}[/underline]")
                await page.goto(initial_url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
                await asyncio.sleep(1.0)

            for idx, step_cfg in enumerate(step_configs, start=1):
                console.print(f"\n[cyan]▶ Bước {idx}:[/cyan] [{step_cfg.action.upper()}] {step_cfg.target or ''} {f'-> \"{step_cfg.value}\"' if step_cfg.value else ''}")
                
                action_rec = await self._execute_single_step(page, idx, step_cfg)
                if action_rec:
                    recorded_actions.append(action_rec)

            await browser.close()

        console.print(f"\n[bold green]✅ Kịch bản đã hoàn thành![/bold green] Đã ghi nhận {len(recorded_actions)} bước thao tác.")
        return recorded_actions

    async def _execute_single_step(
        self,
        page: Page,
        step_number: int,
        step_cfg: ActionStepConfig
    ) -> Optional[RecordedAction]:
        """Thực thi một bước đơn lẻ và ghi lại ảnh chụp + bounding box"""
        screenshot_filename = f"step_{step_number:02d}_raw.png"
        raw_screenshot_path = str(self.output_raw_dir / screenshot_filename)

        action_type = step_cfg.action
        target = step_cfg.target
        value = step_cfg.value

        bbox: Optional[BoundingBox] = None
        element_text: Optional[str] = None

        if action_type == "navigate":
            # Điều hướng URL
            url = target or value
            if not url:
                return None
            await page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
            await asyncio.sleep(step_cfg.wait_after)
            await self._safe_screenshot(page, raw_screenshot_path)
            
            return RecordedAction(
                step_number=step_number,
                action_type="navigate",
                selector=url,
                input_value=url,
                element_text=f"Truy cập {url}",
                bounding_box=None,
                raw_screenshot_path=raw_screenshot_path,
                timestamp=time.time(),
                page_title=await page.title() or "",
                page_url=page.url
            )

        # Với các action click, fill, select, hover: Cần tìm element trên DOM
        element = await self._find_element(page, target)
        if element:
            try:
                await element.evaluate('el => el.scrollIntoView({ block: "center", behavior: "smooth" })')
                await asyncio.sleep(0.8)
            except Exception:
                try:
                    await element.scroll_into_view_if_needed(timeout=3000)
                except Exception:
                    pass
            
            # Lấy thông tin bounding box
            box_dict = await element.bounding_box()
            if box_dict:
                bbox = BoundingBox(
                    x=box_dict["x"],
                    y=box_dict["y"],
                    w=box_dict["width"],
                    h=box_dict["height"]
                )

            # Lấy text mô tả phần tử
            try:
                text_val = await element.inner_text()
                if not text_val:
                    text_val = await element.get_attribute("placeholder") or await element.get_attribute("aria-label") or ""
                element_text = text_val.strip()[:100]
            except Exception:
                element_text = ""

        # Chụp ảnh raw TRƯỚC khi tương tác để thể hiện rõ trạng thái người dùng nhìn thấy
        await self._safe_screenshot(page, raw_screenshot_path)

        # Thực thi hành động cụ thể
        if action_type == "click":
            if element:
                await element.click()
            else:
                console.print(f"[yellow]⚠️ Không tìm thấy element '{target}', thử click bằng text hoặc selector fallback...[/yellow]")
                try:
                    await page.click(target, timeout=5000)
                except Exception as e:
                    console.print(f"[red]❌ Lỗi click: {e}[/red]")

        elif action_type == "fill":
            if element and value is not None:
                await element.fill(str(value))
            elif target and value is not None:
                try:
                    await page.fill(target, str(value))
                except Exception as e:
                    console.print(f"[red]❌ Lỗi fill: {e}[/red]")

        elif action_type == "select":
            if element and value is not None:
                await element.select_option(value)

        elif action_type == "hover":
            if element:
                await element.hover()

        elif action_type == "scroll":
            if element:
                await element.scroll_into_view_if_needed()
            else:
                scroll_y = int(value) if value and str(value).lstrip("-").isdigit() else 600
                await page.mouse.wheel(0, scroll_y)
            await asyncio.sleep(0.8)

        elif action_type == "wait":
            wait_time = float(value) if value else 2.0
            await asyncio.sleep(wait_time)

        # Chờ sau thao tác để animation/load hoàn thành
        await asyncio.sleep(step_cfg.wait_after)

        return RecordedAction(
            step_number=step_number,
            action_type=action_type,  # type: ignore
            selector=target,
            input_value=value,
            element_text=element_text or step_cfg.label or target,
            bounding_box=bbox,
            raw_screenshot_path=raw_screenshot_path,
            timestamp=time.time(),
            page_title=await page.title() or "",
            page_url=page.url
        )

    async def _find_element(self, page: Page, target: Optional[str]):
        """
        Tìm kiếm phần tử bằng nhiều chiến lược linh hoạt (CSS, XPath, Text, Role)
        và hỗ trợ Indexed Action Space (@index hoặc [index] theo phong cách Jev Ultrafast)
        """
        if not target:
            return None
        
        target = target.strip()

        # 0. Hỗ trợ Jev Ultrafast Indexed Target (@1, @2, [1], #1 nếu là số nguyên)
        if target.startswith("@") or (target.startswith("[") and target.endswith("]") and target[1:-1].isdigit()):
            idx_str = target.replace("@", "").replace("[", "").replace("]", "")
            if idx_str.isdigit():
                try:
                    elem = await page.query_selector(f"[data-guide-index='{idx_str}']")
                    if elem and await elem.is_visible():
                        return elem
                except Exception:
                    pass

        # 1. Thử trực tiếp với target selector
        try:
            elem = await page.query_selector(target)
            if elem and await elem.is_visible():
                return elem
        except Exception:
            pass

        # 2. Thử tìm theo Text
        try:
            elem = await page.query_selector(f"text={target}")
            if elem and await elem.is_visible():
                return elem
        except Exception:
            pass

        # 3. Thử tìm button hoặc link có text
        try:
            elem = await page.query_selector(f"button:has-text('{target}'), a:has-text('{target}')")
            if elem and await elem.is_visible():
                return elem
        except Exception:
            pass

        # 4. Thử tìm input theo placeholder
        try:
            elem = await page.query_selector(f"input[placeholder*='{target}' i]")
            if elem and await elem.is_visible():
                return elem
        except Exception:
            pass

        # 5. Thử fallback tìm phần tử đầu tiên khớp (kể cả chưa visible ngay)
        try:
            return await page.wait_for_selector(target, timeout=2000)
        except Exception:
            return None
