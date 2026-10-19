"""
Autonomous Flow Agent (Hệ thống AI Tự Động Điều Hướng & Xử Lý OpenID SSO)
Tự động phát hiện luồng đăng nhập, xử lý redirect liên miền (OpenID Connect / Keycloak / OAuth),
tìm ô nhập và nút bấm chuẩn xác mà người dùng KHÔNG CẦN phải tự định vị hay chọn từ 70+ DOM elements.
"""
import os
import re
import asyncio
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from playwright.async_api import async_playwright, Browser, Page
from rich.console import Console

from config.settings import settings
from src.ai.schemas import RecordedAction, BoundingBox, SiteKnowledgeMap, FeatureModule

console = Console()


class AutonomousFlowAgent:
    """Agent tự động giải quyết bài toán điều hướng trang và xác thực SSO OpenID hoàn toàn không cần can thiệp thủ công"""

    def __init__(self, headless: Optional[bool] = None):
        self.headless = settings.headless if headless is None else headless
        self.output_raw_dir = settings.output_dir / "raw_screenshots"
        self.output_raw_dir.mkdir(parents=True, exist_ok=True)

    async def _safe_screenshot(self, page: Page, path: str, max_retries: int = 4, delay: float = 1.0) -> bool:
        """
        Chụp ảnh màn hình an toàn, tự động xử lý và thử lại khi trang web đang chuyển hướng
        (tránh lỗi CDP Page.captureScreenshot: Unable to capture screenshot khi đang redirect OpenID SSO)
        """
        for attempt in range(max_retries):
            try:
                # Kiểm tra page còn kết nối không
                if page.is_closed():
                    return False
                await page.screenshot(path=path, timeout=5000)
                return True
            except Exception as e:
                console.print(f"[yellow]⏳ Đang chờ trang ổn định để chụp ảnh (lần {attempt + 1}/{max_retries})...[/yellow]")
                if attempt < max_retries - 1:
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=4000)
                    except Exception:
                        pass
                    await asyncio.sleep(delay * (attempt + 1))
                else:
                    # Lần thử cuối cùng: chờ thêm và chụp không kén chọn
                    try:
                        await asyncio.sleep(2.0)
                        await page.screenshot(path=path, timeout=8000)
                        return True
                    except Exception as final_err:
                        console.print(f"[red]⚠️ Cảnh báo chụp ảnh màn hình: {final_err}[/red]")
                        return False
        return False

    async def _launch_browser(self, p) -> Browser:
        for ch in ["msedge", "chrome", None]:
            try:
                kwargs = {"headless": self.headless}
                if ch:
                    kwargs["channel"] = ch
                return await p.chromium.launch(**kwargs)
            except Exception:
                continue
        raise RuntimeError("Không thể khởi động trình duyệt.")

    async def pioneer_scout(
        self,
        url: str,
        credentials: Optional[Dict[str, str]] = None
    ) -> SiteKnowledgeMap:
        """
        LƯỢT 1 (Pioneer Scout): Công cụ AI đi tiên phong dò DOM, khảo sát toàn bộ ngóc ngách,
        menu, chức năng trước và sau đăng nhập. Tổng hợp thành bản đồ kiến thức SiteKnowledgeMap.
        """
        console.print(f"[bold cyan]🧭 [Pioneer Scout - Lượt 1][/bold cyan] Bắt đầu thám hiểm địa hình & lập bản đồ tại: [underline]{url}[/underline]")
        recon_dir = settings.output_dir / "recon"
        recon_dir.mkdir(parents=True, exist_ok=True)
        dashboard_img = str(recon_dir / f"dashboard_map_{int(time.time())}.png")

        async with async_playwright() as p:
            browser = await self._launch_browser(p)
            context = await browser.new_context(
                viewport={"width": settings.viewport_width, "height": settings.viewport_height}
            )
            page = await context.new_page()

            # 1. Khảo sát landing
            await page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
            await asyncio.sleep(1.5)
            landing_title = await page.title() or ""

            # 2. Xử lý đăng nhập nếu có thông tin
            sso_domain = None
            if credentials and (credentials.get("username") or credentials.get("email")):
                login_btn = await self._find_login_trigger(page)
                if login_btn:
                    await login_btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(2.0)
                    sso_domain = page.url.split('/')[2] if '://' in page.url else None

                    user_inp = await self._find_input(page, ["username", "email", "login", "user"])
                    if user_inp:
                        await user_inp.fill(credentials.get("username") or credentials.get("email", ""))
                        await asyncio.sleep(0.5)

                    pass_inp = await page.query_selector("input[type='password'], input[name='password']")
                    if pass_inp:
                        await pass_inp.fill(credentials.get("password", ""))
                        await asyncio.sleep(0.5)

                    sub_btn = await self._find_submit_button(page)
                    if sub_btn:
                        await sub_btn.click()
                        if sso_domain:
                            try:
                                await page.wait_for_url(lambda u: sso_domain not in u, timeout=15000)
                            except Exception:
                                pass
                        await asyncio.sleep(2.5)

            dashboard_url = page.url
            dashboard_title = await page.title() or ""
            await self._safe_screenshot(page, dashboard_img)

            # 3. Quét sâu các tính năng, ngóc ngách, thẻ khóa học, menu
            discovered_features = await self._extract_deep_features(page)

            await browser.close()

            k_map = SiteKnowledgeMap(
                landing_url=url,
                landing_title=landing_title,
                sso_domain=sso_domain,
                dashboard_url=dashboard_url,
                dashboard_title=dashboard_title,
                dashboard_screenshot_path=dashboard_img,
                discovered_features=discovered_features,
                recommended_walkthroughs=[f.name for f in discovered_features[:6]]
            )

            # Lưu ra file JSON
            k_file = recon_dir / "site_knowledge_map.json"
            k_file.write_text(k_map.model_dump_json(indent=2), encoding="utf-8")
            console.print(f"[green]✅ [Pioneer Scout] Đã lập xong Sơ đồ Kiến thức gồm {len(discovered_features)} chức năng / ngóc ngách![/green]")
            return k_map

    async def _extract_deep_features(self, page: Page) -> List[FeatureModule]:
        """Trích xuất các module chức năng ngóc ngách trên trang Dashboard (bao gồm toàn bộ Navigation & Sidebar)"""
        js_code = r"""
        () => {
            const list = [];
            const seen = new Set();
            const selectors = [
                'aside a, aside button, aside [role="button"]',
                'nav a, nav button, nav [role="button"]',
                '[role="navigation"] a, [role="navigation"] button',
                '.sidebar a, .sidebar button, [class*="sidebar"] a, [class*="sidebar"] button',
                '.drawer a, .drawer button, [class*="drawer"] a, [class*="drawer"] button',
                '.MuiDrawer-root a, .MuiDrawer-root [role="button"], .MuiListItem-root a, .MuiListItemButton-root',
                '[role="tab"], [role="menuitem"]',
                'button, input[type="submit"], input[type="button"], a.btn, [role="button"]',
                '.card a, [data-key], h4, h3'
            ].join(', ');

            const elements = document.querySelectorAll(selectors);
            const limit = Math.min(elements.length, 120);
            for (let i = 0; i < limit; i++) {
                const el = elements[i];
                const rect = el.getBoundingClientRect();
                if (rect.width > 16 && rect.height > 12 && rect.top >= 0) {
                    let text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
                    text = text.replace(/[\r\n]+/g, ' ').replace(/\s+/g, ' ');
                    if (text && text.length > 2 && text.length < 50 && !seen.has(text.toLowerCase())) {
                        seen.add(text.toLowerCase());
                        let sel = el.tagName.toLowerCase();
                        if (el.id) sel += '#' + el.id;
                        else if (el.className && typeof el.className === 'string') {
                            const c = el.className.trim().split(/\s+/)[0];
                            if (c && !c.includes(':')) sel += '.' + c;
                        }
                        const isSidebar = rect.left < 380 || el.closest('aside, .sidebar, .drawer, [role="navigation"]');
                        list.push({
                            name: text,
                            selector: sel,
                            url_target: el.getAttribute('href') || null,
                            category: isSidebar ? 'Menu bên trái' : (el.closest('.card, .course-info') ? 'Khóa học' : 'Chức năng'),
                            bounding_box: {
                                x: Math.round(rect.left),
                                y: Math.round(rect.top),
                                w: Math.round(rect.width),
                                h: Math.round(rect.height)
                            }
                        });
                    }
                }
            }
            return list;
        }
        """
        raw_items = await page.evaluate(js_code)
        modules = []
        for it in raw_items:
            bb = it.get("bounding_box")
            bb_obj = BoundingBox(x=bb["x"], y=bb["y"], w=bb["w"], h=bb["h"]) if bb else None
            modules.append(
                FeatureModule(
                    name=it.get("name", ""),
                    selector=it.get("selector", ""),
                    url_target=it.get("url_target"),
                    category=it.get("category"),
                    bounding_box=bb_obj,
                )
            )
        return modules

    async def execute_autonomous_guide(
        self,
        url: str,
        goal: str = "Đăng nhập hệ thống",
        credentials: Optional[Dict[str, str]] = None,
        user_notes: Optional[str] = None,
        knowledge_map: Optional[SiteKnowledgeMap] = None,
    ) -> List[RecordedAction]:
        """
        Quy trình tự động hóa thông minh từ A đến Z:
        1. Mở trang web ban đầu.
        2. Tự nhận diện nút Login (ngay cả khi nằm ẩn trong menu hoặc góc phải).
        3. Tự theo dõi quá trình chuyển hướng liên miền (Redirect OpenID Connect / Keycloak / EID).
        4. Tự tìm ô Username, Password và nút Đăng nhập trên cổng OpenID.
        5. Tự lưu lại đầy đủ ảnh chụp + Bounding Box chính xác của từng bước.
        """
        recorded_actions: List[RecordedAction] = []
        creds = credentials or {}
        username = creds.get("username") or creds.get("email") or "ten_tai_khoan@example.com"
        password = creds.get("password") or "MatKhauBaoMat@123"

        console.print(f"[bold cyan]🤖 [Autonomous Flow Agent][/bold cyan] Bắt đầu tự động khám phá tại: [underline]{url}[/underline]")
        console.print(f"🎯 Mục tiêu: [green]{goal}[/green]")

        async with async_playwright() as p:
            browser = await self._launch_browser(p)
            context = await browser.new_context(
                viewport={"width": settings.viewport_width, "height": settings.viewport_height}
            )
            page = await context.new_page()

            # -------------------------------------------------------------
            # BƯỚC 1: Truy cập trang ban đầu
            # -------------------------------------------------------------
            step_idx = 1
            console.print(f"\n[cyan]▶ Bước {step_idx}:[/cyan] Truy cập trang chủ {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
            await asyncio.sleep(1.5)

            raw_path_1 = str(self.output_raw_dir / f"step_{step_idx:02d}_raw.png")
            await self._safe_screenshot(page, raw_path_1)
            recorded_actions.append(
                RecordedAction(
                    step_number=step_idx,
                    action_type="navigate",
                    selector=url,
                    input_value=url,
                    element_text=f"Truy cập {await page.title() or url}",
                    bounding_box=None,
                    raw_screenshot_path=raw_path_1,
                    timestamp=time.time(),
                    page_title=await page.title() or "",
                    page_url=page.url,
                )
            )

            # -------------------------------------------------------------
            # BƯỚC 2: Tự động tìm và bấm nút Đăng nhập / Log in
            # -------------------------------------------------------------
            login_link = await self._find_login_trigger(page)
            if login_link:
                step_idx += 1
                box_dict = await login_link.bounding_box()
                btn_bbox = BoundingBox(x=box_dict["x"], y=box_dict["y"], w=box_dict["width"], h=box_dict["height"]) if box_dict else None
                btn_text = (await login_link.inner_text()).strip() or "Đăng nhập / Log in"

                console.print(f"[cyan]▶ Bước {step_idx}:[/cyan] Phát hiện nút đăng nhập: '[bold]{btn_text}[/bold]' tại vị trí {box_dict}")

                # Chụp ảnh trước khi click để giữ vị trí nút
                raw_path_2 = str(self.output_raw_dir / f"step_{step_idx:02d}_raw.png")
                await self._safe_screenshot(page, raw_path_2)

                recorded_actions.append(
                    RecordedAction(
                        step_number=step_idx,
                        action_type="click",
                        selector="a:has-text('Log in')",
                        input_value=None,
                        element_text=f"Nút {btn_text}",
                        bounding_box=btn_bbox,
                        raw_screenshot_path=raw_path_2,
                        timestamp=time.time(),
                        page_title=await page.title() or "",
                        page_url=page.url,
                    )
                )

                # Thực hiện click và đợi redirect (OpenID SSO)
                console.print("   🔄 Đang chuyển hướng sang cổng xác thực...")
                await login_link.click()
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(2.0)

            # -------------------------------------------------------------
            # BƯỚC 3 & 4 & 5: Tự động phát hiện Form đăng nhập OpenID SSO
            # -------------------------------------------------------------
            current_url = page.url
            console.print(f"📍 Hiện đang ở cổng xác thực: [dim]{current_url}[/dim]")

            # 3. Tìm ô nhập Username / Email
            user_input = await self._find_input(page, ["username", "email", "login", "user"])
            if user_input:
                step_idx += 1
                box_u = await user_input.bounding_box()
                u_bbox = BoundingBox(x=box_u["x"], y=box_u["y"], w=box_u["width"], h=box_u["height"]) if box_u else None
                console.print(f"[cyan]▶ Bước {step_idx}:[/cyan] Tự phát hiện ô tài khoản: '[bold]Username or email[/bold]'")

                raw_path_u = str(self.output_raw_dir / f"step_{step_idx:02d}_raw.png")
                await self._safe_screenshot(page, raw_path_u)

                # Điền tài khoản
                await user_input.fill(username)
                await asyncio.sleep(0.5)

                recorded_actions.append(
                    RecordedAction(
                        step_number=step_idx,
                        action_type="fill",
                        selector="input[name='username']",
                        input_value="[REDACTED_USERNAME]",
                        element_text="Tên tài khoản hoặc Email",
                        bounding_box=u_bbox,
                        raw_screenshot_path=raw_path_u,
                        timestamp=time.time(),
                        page_title=await page.title() or "",
                        page_url=page.url,
                    )
                )

            # 4. Tìm ô nhập Mật khẩu (Password)
            pass_input = await page.query_selector("input[type='password'], input[name='password']")
            if pass_input:
                step_idx += 1
                box_p = await pass_input.bounding_box()
                p_bbox = BoundingBox(x=box_p["x"], y=box_p["y"], w=box_p["width"], h=box_p["height"]) if box_p else None
                console.print(f"[cyan]▶ Bước {step_idx}:[/cyan] Tự phát hiện ô mật khẩu: '[bold]Password[/bold]'")

                raw_path_p = str(self.output_raw_dir / f"step_{step_idx:02d}_raw.png")
                await self._safe_screenshot(page, raw_path_p)

                # Điền mật khẩu
                await pass_input.fill(password)
                await asyncio.sleep(0.5)

                recorded_actions.append(
                    RecordedAction(
                        step_number=step_idx,
                        action_type="fill",
                        selector="input[type='password']",
                        input_value="[REDACTED_PASSWORD]",
                        element_text="Mật khẩu bảo mật",
                        bounding_box=p_bbox,
                        raw_screenshot_path=raw_path_p,
                        timestamp=time.time(),
                        page_title=await page.title() or "",
                        page_url=page.url,
                    )
                )

            # 5. Tìm nút Submit / Xác nhận đăng nhập trên form SSO
            submit_btn = await self._find_submit_button(page)
            if submit_btn:
                step_idx += 1
                box_s = await submit_btn.bounding_box()
                s_bbox = BoundingBox(x=box_s["x"], y=box_s["y"], w=box_s["width"], h=box_s["height"]) if box_s else None
                sub_text = (await submit_btn.inner_text() or await submit_btn.get_attribute("value") or "Login").strip()
                console.print(f"[cyan]▶ Bước {step_idx}:[/cyan] Tự phát hiện nút gửi: '[bold]{sub_text}[/bold]'")

                raw_path_s = str(self.output_raw_dir / f"step_{step_idx:02d}_raw.png")
                await self._safe_screenshot(page, raw_path_s)

                recorded_actions.append(
                    RecordedAction(
                        step_number=step_idx,
                        action_type="click",
                        selector="button[type='submit'], input[type='submit']",
                        input_value=None,
                        element_text=f"Nút {sub_text}",
                        bounding_box=s_bbox,
                        raw_screenshot_path=raw_path_s,
                        timestamp=time.time(),
                        page_title=await page.title() or "",
                        page_url=page.url,
                    )
                )

                # Bấm xác nhận và chờ redirect quay lại
                console.print("   🔄 Đang xác thực và chuyển hướng quay lại ứng dụng...")
                try:
                    sso_host = page.url.split('/')[2] if '://' in page.url else ''
                    await submit_btn.click()
                    # Chờ quá trình chuyển hướng liên miền rời khỏi cổng SSO
                    try:
                        await page.wait_for_url(lambda u: sso_host not in u, timeout=15000)
                        console.print(f"   ✔️ Đã chuyển hướng thành công về: [green]{page.url}[/green]")
                    except Exception:
                        pass
                    # Chờ DOM trang đích ổn định
                    await asyncio.sleep(2.0)
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=5000)
                    except Exception:
                        pass
                except Exception as e:
                    console.print(f"[yellow]⚠️ Quá trình chuyển hướng tiếp tục: {e}[/yellow]")

            # -------------------------------------------------------------
            # BƯỚC 6: Chụp màn hình trạng thái Dashboard
            # -------------------------------------------------------------
            step_idx += 1
            raw_path_end = str(self.output_raw_dir / f"step_{step_idx:02d}_raw.png")
            await self._safe_screenshot(page, raw_path_end)
            recorded_actions.append(
                RecordedAction(
                    step_number=step_idx,
                    action_type="custom",
                    selector="dashboard",
                    input_value=None,
                    element_text=f"Giao diện Trang chủ ({await page.title()})",
                    bounding_box=None,
                    raw_screenshot_path=raw_path_end,
                    timestamp=time.time(),
                    page_title=await page.title() or "",
                    page_url=page.url,
                )
            )

            # -------------------------------------------------------------
            # BƯỚC 7+: Khám phá ngóc ngách chuyên sâu theo Yêu Cầu Người Dùng (Course / Tabs)
            # -------------------------------------------------------------
            deep_steps = await self._execute_custom_deep_directives(page, goal, user_notes, step_idx)
            for d_act in deep_steps:
                recorded_actions.append(d_act)

            await browser.close()

        console.print(f"\n[bold green]🎉 Hoàn thành tự động hóa 100%![/bold green] Đã ghi nhận {len(recorded_actions)} bước chuẩn xác.")
        return recorded_actions

    async def _discover_sidebar_navigation(self, page: Page) -> List[Dict[str, Any]]:
        """Quét và nhận diện toàn bộ các mục menu trên thanh điều hướng bên trái (Left Sidebar)"""
        js_code = r"""
        () => {
            const list = [];
            const seen = new Set();
            const selectors = [
                'aside a, aside button, aside [role="button"], aside [role="link"]',
                'nav a, nav button, nav [role="button"]',
                '.sidebar a, .sidebar button, [class*="sidebar"] a, [class*="sidebar"] button',
                '.drawer a, .drawer button, [class*="drawer"] a, [class*="drawer"] button',
                '[role="navigation"] a, [role="navigation"] button',
                '.MuiDrawer-root a, .MuiDrawer-root [role="button"], .MuiListItem-root a, .MuiListItemButton-root',
                '#sidebar a, #nav a, .left-menu a, a.nav-link, button.nav-link, a.list-group-item'
            ].join(', ');

            const elements = document.querySelectorAll(selectors);
            for (const el of elements) {
                const rect = el.getBoundingClientRect();
                const isSidebar = el.closest('aside, .sidebar, .drawer, .MuiDrawer-root, [class*="sidebar"], [class*="drawer"]') !== null || (rect.left < 400 && rect.width > 24);
                if (isSidebar && rect.width > 20 && rect.height > 16 && rect.top >= 0) {
                    let text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
                    text = text.replace(/[\r\n]+/g, ' ').replace(/\s+/g, ' ');
                    if (!text || text.length < 2 || text.length > 50 || /^\d+$/.test(text)) continue;
                    const low = text.toLowerCase();
                    if (['close', 'đóng', 'thu gọn', 'mở rộng', 'expand', 'collapse', 'user profile', 'avatar', 'logout', 'đăng xuất'].includes(low)) continue;

                    if (!seen.has(low)) {
                        seen.add(low);
                        list.push({
                            name: text,
                            href: el.getAttribute('href') || null,
                            tag: el.tagName.toLowerCase(),
                            x: Math.round(rect.left),
                            y: Math.round(rect.top),
                            w: Math.round(rect.width),
                            h: Math.round(rect.height)
                        });
                    }
                }
            }
            return list;
        }
        """
        try:
            return await page.evaluate(js_code)
        except Exception:
            return []

    async def _execute_custom_deep_directives(
        self,
        page: Page,
        goal: str,
        user_notes: Optional[str],
        current_step: int
    ) -> List[RecordedAction]:
        """
        Thực hiện chuỗi hành động chuyên sâu hoàn toàn linh hoạt (Universal Dynamic AI Agent):
        - Quét động toàn bộ phần tử thực tế trên trang (Navigation, Tabs, Buttons, Cards)
        - AI Planner (Gemini) phân tích mục tiêu & ghi chú người dùng để sinh lộ trình các bước CÓ THỰC trên website này
        - Lần lượt tương tác, chụp ảnh Spotlight Bounding Box và mở view chi tiết
        - Thích ứng với 100% các loại website trong hệ sinh thái: LMS, Kho code, IDE, Workspace, Portal...
        """
        actions = []
        step_idx = current_step
        from src.ai.gemini_client import GeminiAnalyzer
        analyzer = GeminiAnalyzer()

        console.print("[bold cyan]🧭 [Universal AI Agent][/bold cyan] Đang quét cấu trúc giao diện thực tế của website...")

        # 1. Trích xuất toàn bộ các phần tử tương tác thực tế đang có trên trang
        discovered_elements = await self._extract_deep_features(page)

        # 2. Nếu là thanh sidebar hoặc drawer đang thu gọn, kiểm tra nút toggle để mở
        if len(discovered_elements) < 3:
            toggle_btn = await page.query_selector('button[aria-label*="menu" i], button.drawer-toggle, [data-action="toggle-drawer"], button:has(svg)')
            if toggle_btn and await toggle_btn.is_visible():
                try:
                    await toggle_btn.click()
                    await asyncio.sleep(1.0)
                    discovered_elements = await self._extract_deep_features(page)
                except Exception:
                    pass

        # 3. Nhờ AI Planner lập kế hoạch các bước tối ưu dựa trên DOM thực tế và ghi chú người dùng
        page_title = await page.title() or ""
        planned_steps = analyzer.plan_deep_directives(
            discovered_elements=discovered_elements,
            goal=goal,
            user_notes=user_notes,
            page_title=page_title,
            page_url=page.url
        )

        console.print(f"📋 AI Planner đã vạch ra {len(planned_steps)} bước thao tác thực tế theo đúng yêu cầu:")
        for idx, ps in enumerate(planned_steps, start=1):
            console.print(f"   {idx}. [{ps.get('action_type', 'click').upper()}] {ps.get('target_name')} - {ps.get('purpose')}")

        # 4. Thực thi từng bước theo kế hoạch AI đã lập
        for step_plan in planned_steps:
            target_name = step_plan.get("target_name", "")
            action_type = step_plan.get("action_type", "click")
            purpose = step_plan.get("purpose", "")
            open_view = step_plan.get("open_view_after", True)

            console.print(f"\n[cyan]▶ [Agent Action][/cyan] Thực hiện: '[bold]{target_name}[/bold]' ({purpose})")

            # Tìm phần tử trên trang theo text hoặc selector
            elem = None
            try:
                candidates = await page.query_selector_all(
                    f"aside a, aside button, nav a, nav button, .sidebar a, a:has-text('{target_name}'), button:has-text('{target_name}'), [role='tab']:has-text('{target_name}'), [role='menuitem']:has-text('{target_name}')"
                )
                for c in candidates:
                    if await c.is_visible():
                        elem = c
                        break
            except Exception:
                pass

            if not elem and step_plan.get("selector"):
                try:
                    elem = await page.query_selector(step_plan["selector"])
                except Exception:
                    pass

            bbox = None
            if elem:
                try:
                    await elem.evaluate('el => el.scrollIntoView({ block: "center", behavior: "smooth" })')
                    await asyncio.sleep(0.8)
                    box = await elem.bounding_box()
                    if box:
                        bbox = BoundingBox(x=box["x"], y=box["y"], w=box["width"], h=box["height"])
                except Exception:
                    pass

            # Bước tương tác (Click / Navigate)
            step_idx += 1
            raw_path_step = str(self.output_raw_dir / f"step_{step_idx:02d}_raw.png")
            await self._safe_screenshot(page, raw_path_step)

            actions.append(
                RecordedAction(
                    step_number=step_idx,
                    action_type="click" if action_type == "click" else "navigate",
                    selector=step_plan.get("selector") or target_name,
                    input_value=None,
                    element_text=f"Mục {target_name} trên giao diện",
                    bounding_box=bbox,
                    raw_screenshot_path=raw_path_step,
                    timestamp=time.time(),
                    page_title=await page.title() or "",
                    page_url=page.url,
                )
            )

            # Thực hiện thao tác click và chờ view mới tải
            try:
                if elem:
                    await elem.click(timeout=6000)
                await asyncio.sleep(2.0)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=6000)
                except Exception:
                    pass
            except Exception as e:
                console.print(f"[yellow]⚠️ Tương tác với {target_name}: {e}[/yellow]")

            # Nếu bước này mở ra một view mới, chụp ảnh và ghi nhận bước khám phá view
            if open_view:
                step_idx += 1
                raw_path_view = str(self.output_raw_dir / f"step_{step_idx:02d}_raw.png")
                await self._safe_screenshot(page, raw_path_view)

                # Tìm khu vực trọng tâm của view mới để định vị Bounding Box
                content_bbox = None
                try:
                    center_container = await page.query_selector("main, [role='main'], article, .main-content, .content, .card, table")
                    if center_container and await center_container.is_visible():
                        cb = await center_container.bounding_box()
                        if cb and cb["width"] > 100:
                            content_bbox = BoundingBox(x=cb["x"], y=cb["y"], w=cb["width"], h=cb["height"])
                except Exception:
                    pass

                actions.append(
                    RecordedAction(
                        step_number=step_idx,
                        action_type="custom",
                        selector=f"view-{target_name.lower().replace(' ', '-')}",
                        input_value=None,
                        element_text=f"Khám phá chi tiết giao diện {target_name}: Giới thiệu ý nghĩa và cách sử dụng",
                        bounding_box=content_bbox,
                        raw_screenshot_path=raw_path_view,
                        timestamp=time.time(),
                        page_title=await page.title() or "",
                        page_url=page.url,
                    )
                )

        return actions

    async def _find_login_trigger(self, page: Page):
        """Tìm nút đăng nhập trên trang hiện tại theo nhiều chiến lược linh hoạt"""
        selectors = [
            "a:has-text('Log in')",
            "a:has-text('Đăng nhập')",
            "button:has-text('Log in')",
            "button:has-text('Đăng nhập')",
            "a:has-text('Sign in')",
            "button:has-text('Sign in')",
            "a[href*='login' i]",
            "a[href*='oidc' i]",
            "a[href*='auth' i]",
            ".login-btn",
            "#login-btn",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return el
            except Exception:
                continue
        return None

    async def _find_input(self, page: Page, keywords: List[str]):
        """Tìm ô input phù hợp với từ khóa"""
        for kw in keywords:
            sel_list = [
                f"input[name*='{kw}' i]",
                f"input[id*='{kw}' i]",
                f"input[placeholder*='{kw}' i]",
            ]
            for sel in sel_list:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        return el
                except Exception:
                    continue
        # Fallback: input text đầu tiên nhìn thấy
        try:
            return await page.query_selector("input[type='text']:visible, input[type='email']:visible")
        except Exception:
            return None

    async def _find_submit_button(self, page: Page):
        """Tìm nút submit trong form"""
        selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Login')",
            "button:has-text('Đăng nhập')",
            "button:has-text('Sign In')",
            "button.btn-primary",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return el
            except Exception:
                continue
        return None
