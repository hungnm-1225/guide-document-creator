"""
UI Reconnaissance Module (Survey & Surface Explorer)
Thực hiện khảo sát địa hình giao diện thực tế trước khi lên hướng dẫn:
- Tránh đoán mò
- Quét DOM, tìm các nút bấm, input, menu điều hướng
- Chụp ảnh tổng quan trang web
- Nhận diện các chức năng và layout
"""
import asyncio
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from playwright.async_api import async_playwright, Browser, Page, ElementHandle
from rich.console import Console

from config.settings import settings
from src.ai.schemas import ReconReport, UIElementInfo, BoundingBox

console = Console()


class UIReconnaissance:
    """Survey and map the target website's UI surface structure without guessing"""

    def __init__(self, headless: Optional[bool] = None):
        self.headless = settings.headless if headless is None else headless
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None

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
        """Launch browser trying system edge/chrome channels first for maximum compatibility"""
        channels = ["msedge", "chrome", None]
        last_error = None
        for ch in channels:
            try:
                launch_kwargs = {"headless": self.headless}
                if ch:
                    launch_kwargs["channel"] = ch
                browser = await p.chromium.launch(**launch_kwargs)
                return browser
            except Exception as e:
                last_error = e
                continue
        raise RuntimeError(f"Could not launch browser: {last_error}")

    async def survey_url(
        self,
        url: str,
        login_credentials: Optional[Dict[str, str]] = None,
        survey_output_dir: Optional[Path] = None,
    ) -> ReconReport:
        """
        Khảo sát địa hình toàn diện trang web:
        1. Mở trang web và kiểm tra đăng nhập nếu có
        2. Chụp ảnh tổng quan giao diện
        3. Thu thập danh sách các interactive elements kèm tọa độ thực tế
        4. Trả về ReconReport
        """
        out_dir = survey_output_dir or (settings.output_dir / "recon")
        out_dir.mkdir(parents=True, exist_ok=True)
        overview_img_path = str(out_dir / f"overview_{int(time.time())}.png")

        console.print(f"[bold cyan]🔍 [UI Reconnaissance][/bold cyan] Bắt đầu khảo sát địa hình tại: [underline]{url}[/underline]")

        async with async_playwright() as p:
            browser = await self._launch_browser(p)
            context = await browser.new_context(
                viewport={"width": settings.viewport_width, "height": settings.viewport_height}
            )
            page = await context.new_page()

            # 1. Truy cập URL
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
                await asyncio.sleep(1.5)  # cho UI render hoàn chỉnh
            except Exception as e:
                console.print(f"[yellow]⚠️ Cảnh báo tải trang: {e}[/yellow]")

            # 2. Xử lý đăng nhập nếu có thông tin credentials
            login_detected = False
            if login_credentials:
                login_detected = await self._attempt_auto_login(page, login_credentials)

            # 3. Lấy Page Title và Screenshot toàn cảnh
            page_title = await page.title() or "Trang không có tiêu đề"
            await self._safe_screenshot(page, overview_img_path)
            console.print(f"📸 Đã chụp ảnh tổng quan giao diện: [dim]{overview_img_path}[/dim]")

            # 4. Quét các interactive elements (Buttons, Inputs, Links, Dropdowns, Menus)
            discovered_elements = await self._extract_interactive_elements(page)
            detected_sections = await self._detect_major_sections(page)

            console.print(
                f"✅ [green]Khảo sát hoàn tất:[/green] Phát hiện {len(discovered_elements)} phần tử tương tác, "
                f"{len(detected_sections)} khu vực giao diện chính."
            )

            await browser.close()

            report = ReconReport(
                url=url,
                title=page_title,
                overview_screenshot_path=overview_img_path,
                discovered_elements=discovered_elements,
                detected_sections=detected_sections,
                login_required=login_detected,
                notes=f"Đã khảo sát ở độ phân giải {settings.viewport_width}x{settings.viewport_height}",
            )
            return report

    async def _attempt_auto_login(self, page: Page, credentials: Dict[str, str]) -> bool:
        """Tự động phát hiện form đăng nhập và điền thông tin nếu có"""
        username = credentials.get("username") or credentials.get("user") or credentials.get("email")
        password = credentials.get("password") or credentials.get("pass")

        if not username and not password:
            return False

        console.print("[cyan]🔑 Phát hiện yêu cầu đăng nhập, đang kiểm tra form...[/cyan]")
        user_selectors = [
            "input[type='email']",
            "input[name='email']",
            "input[name='username']",
            "input[name='user']",
            "input[id*='user']",
            "input[placeholder*='email' i]",
            "input[placeholder*='tài khoản' i]",
            "input[placeholder*='username' i]",
            "input[type='text']",
        ]
        pass_selectors = [
            "input[type='password']",
            "input[name='password']",
            "input[id*='pass']",
            "input[placeholder*='mật khẩu' i]",
            "input[placeholder*='password' i]",
        ]

        found_user_input = None
        for sel in user_selectors:
            elem = await page.query_selector(sel)
            if elem and await elem.is_visible():
                found_user_input = elem
                break

        found_pass_input = None
        for sel in pass_selectors:
            elem = await page.query_selector(sel)
            if elem and await elem.is_visible():
                found_pass_input = elem
                break

        if found_user_input and username:
            await found_user_input.fill(username)
            console.print("   -> Đã điền tên tài khoản")
        if found_pass_input and password:
            await found_pass_input.fill(password)
            console.print("   -> Đã điền mật khẩu")

        # Tìm nút Submit / Đăng nhập
        btn_selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Đăng nhập')",
            "button:has-text('Login')",
            "button:has-text('Sign In')",
            "button.btn-primary",
        ]
        for bsel in btn_selectors:
            btn = await page.query_selector(bsel)
            if btn and await btn.is_visible():
                await btn.click()
                console.print("   -> Đã nhấn nút Đăng nhập")
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(2)
                return True

        return False

    async def _extract_interactive_elements(self, page: Page) -> List[UIElementInfo]:
        """
        Trích xuất danh sách tất cả các interactive elements theo triết lý Jev Ultrafast (Indexed Action Space):
        - Hỗ trợ đầy đủ button, link, input, label, toggle switch, checkbox, custom div/span có cursor:pointer
        - Tự động nhận diện custom toggle switch (khi input bị ẩn nhưng label hiển thị)
        - Trích xuất tên thông minh từ aria-label, title, innerText
        - Đánh số chỉ mục [1], [2], [3]... và gán data-guide-index
        - Kiểm tra che khuất (Occlusion check)
        """
        js_code = """
        () => {
            const results = [];
            const seen = new Set();
            
            // 1. Bộ chọn mở rộng bao gồm cả label, toggle switch, vai trò tương tác
            const query = 'button, a, input, select, textarea, label, [role="button"], [role="switch"], [role="checkbox"], [role="tab"], [role="menuitem"], [role="link"], [onclick]';
            const initialList = Array.from(document.querySelectorAll(query));
            
            // 2. Bổ sung các thẻ div, span có style cursor: pointer (nút custom hiện đại)
            const allElements = document.querySelectorAll('div, span, svg');
            for (const el of allElements) {
                const style = window.getComputedStyle(el);
                if (style.cursor === 'pointer' && !initialList.includes(el)) {
                    if (!el.closest('button, a, label, [role="button"]')) {
                        initialList.push(el);
                    }
                }
            }

            let idx = 1;
            for (let el of initialList) {
                // Xử lý thông minh cho Toggle Switch: Nếu input ẩn trong label, dùng label làm target
                let targetEl = el;
                if (el.tagName.toLowerCase() === 'input' && (el.type === 'checkbox' || el.type === 'radio')) {
                    const parentLabel = el.closest('label');
                    if (parentLabel) {
                        const lRect = parentLabel.getBoundingClientRect();
                        if (lRect.width > 8 && lRect.height > 8) {
                            targetEl = parentLabel;
                        }
                    }
                }

                if (seen.has(targetEl)) continue;
                seen.add(targetEl);

                const rect = targetEl.getBoundingClientRect();
                const style = window.getComputedStyle(targetEl);

                // Bỏ qua nếu bị ẩn hoàn toàn
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                if (rect.width < 8 || rect.height < 8) continue;
                // Bỏ qua nếu hoàn toàn ngoài lề trái hoặc phải
                if (rect.right < 0 || rect.left > window.innerWidth) continue;

                // Tên hiển thị thông minh: innerText -> aria-label -> title -> placeholder -> value
                let text = (
                    targetEl.getAttribute('aria-label') ||
                    targetEl.getAttribute('title') ||
                    targetEl.innerText ||
                    (targetEl.querySelector('input') && targetEl.querySelector('input').getAttribute('aria-label')) ||
                    (targetEl.querySelector('input') && targetEl.querySelector('input').getAttribute('title')) ||
                    targetEl.value ||
                    targetEl.getAttribute('placeholder') ||
                    ''
                ).trim().replace(/\\s+/g, ' ');

                if (text.length > 80) text = text.substring(0, 80) + '...';

                // Tự gán nhãn cho toggle switch nếu không có chữ
                if (!text) {
                    if (targetEl.classList.contains('pty-theme-switch') || targetEl.querySelector('.pty-theme-slider')) {
                        text = 'Chuyển Đổi Giao Diện Sáng / Tối (Theme Switch)';
                    } else if (targetEl.getAttribute('id')) {
                        text = '#' + targetEl.getAttribute('id');
                    } else {
                        text = targetEl.tagName.toLowerCase();
                    }
                }

                // Gán thuộc tính data-guide-index
                targetEl.setAttribute('data-guide-index', idx.toString());

                // Kiểm tra che khuất (Occlusion Check)
                const cx = rect.left + rect.width / 2;
                const cy = rect.top + rect.height / 2;
                let isOccluded = false;
                if (cx >= 0 && cx <= window.innerWidth && cy >= 0 && cy <= window.innerHeight) {
                    const topEl = document.elementFromPoint(cx, cy);
                    isOccluded = topEl ? (!targetEl.contains(topEl) && !topEl.contains(targetEl)) : false;
                }

                // Nhận diện kiểu phần tử
                let elType = targetEl.tagName.toLowerCase();
                if (targetEl.tagName.toLowerCase() === 'label' && targetEl.querySelector('input[type="checkbox"]')) {
                    elType = 'toggle switch';
                }

                // Tạo selector
                let sel = targetEl.tagName.toLowerCase();
                if (targetEl.id) {
                    sel += '#' + targetEl.id;
                } else if (targetEl.className && typeof targetEl.className === 'string') {
                    const firstClass = targetEl.className.trim().split(/\\s+/)[0];
                    if (firstClass && !firstClass.includes(':')) sel += '.' + firstClass;
                }

                results.push({
                    index: idx,
                    tag: targetEl.tagName.toLowerCase(),
                    text: text,
                    role: targetEl.getAttribute('role'),
                    placeholder: targetEl.getAttribute('placeholder'),
                    aria_label: targetEl.getAttribute('aria-label') || targetEl.getAttribute('title'),
                    selector: sel,
                    element_type: elType,
                    is_occluded: isOccluded,
                    bounding_box: {
                        x: Math.round(rect.left),
                        y: Math.round(rect.top),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height)
                    }
                });
                idx++;
            }
            return results;
        }
        """
        raw_items = await page.evaluate(js_code)
        elements: List[UIElementInfo] = []
        for item in raw_items:
            bbox = item.get("bounding_box")
            bb_obj = BoundingBox(x=bbox["x"], y=bbox["y"], w=bbox["w"], h=bbox["h"]) if bbox else None
            elements.append(
                UIElementInfo(
                    index=item.get("index", 0),
                    tag=item.get("tag", ""),
                    text=item.get("text", ""),
                    role=item.get("role"),
                    selector=item.get("selector", ""),
                    placeholder=item.get("placeholder"),
                    aria_label=item.get("aria_label"),
                    element_type=item.get("element_type"),
                    is_occluded=item.get("is_occluded", False),
                    bounding_box=bb_obj,
                )
            )
        return elements

    async def _detect_major_sections(self, page: Page) -> List[str]:
        """Nhận diện các khối bố cục chính trên giao diện (Nav, Sidebar, Header, Form, v.v.)"""
        js_code = """
        () => {
            const sections = [];
            const queries = [
                { name: "Header Navigation", sel: "header, nav, .navbar, .header, #header" },
                { name: "Sidebar Menu", sel: "aside, .sidebar, #sidebar, [role='navigation']" },
                { name: "Main Content", sel: "main, #main, .main-content, #content" },
                { name: "Search Bar", sel: "input[type='search'], .search-box, form[role='search']" },
                { name: "Interactive Forms", sel: "form" },
                { name: "Footer", sel: "footer, .footer, #footer" }
            ];
            for (const q of queries) {
                const found = document.querySelector(q.sel);
                if (found && found.getBoundingClientRect().height > 10) {
                    sections.push(q.name);
                }
            }
            return sections;
        }
        """
        return await page.evaluate(js_code)
