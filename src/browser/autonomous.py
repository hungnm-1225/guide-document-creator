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
        """Trích xuất các module chức năng ngóc ngách trên trang Dashboard"""
        js_code = r"""
        () => {
            const list = [];
            const seen = new Set();
            const elements = document.querySelectorAll('header a, nav a, .card a, [role="tab"], [role="menuitem"], a.btn, h4, h3');
            const limit = Math.min(elements.length, 100);
            for (let i = 0; i < limit; i++) {
                const el = elements[i];
                const rect = el.getBoundingClientRect();
                if (rect.width > 20 && rect.height > 15 && rect.top > 0 && rect.top < 1080) {
                    const text = (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
                    if (text && text.length > 2 && text.length < 50 && !seen.has(text.toLowerCase())) {
                        seen.add(text.toLowerCase());
                        let sel = el.tagName.toLowerCase();
                        if (el.id) sel += '#' + el.id;
                        else if (el.className && typeof el.className === 'string') {
                            const c = el.className.trim().split(/\s+/)[0];
                            if (c && !c.includes(':')) sel += '.' + c;
                        }
                        list.push({
                            name: text,
                            selector: sel,
                            url_target: el.getAttribute('href') || null,
                            category: el.closest('nav, header') ? 'Điều hướng' : (el.closest('.card, .course-info') ? 'Khóa học' : 'Chức năng'),
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

    async def _execute_custom_deep_directives(
        self,
        page: Page,
        goal: str,
        user_notes: Optional[str],
        current_step: int
    ) -> List[RecordedAction]:
        """
        Thực hiện chuỗi hành động chuyên sâu theo đúng yêu cầu nghiệp vụ của người dùng:
        - Bấm vào khóa học cụ thể được chỉ định (e.g. LEANBOT Terra Protocol C++ (EN))
        - Lần lượt đi qua từng chức năng/tab yêu cầu (Course, Grades, Competencies, Notes)
        - Chụp ảnh và đóng khung Bounding Box cho từng vị trí!
        """
        actions = []
        step_idx = current_step
        prompt_text = f"{goal} {user_notes or ''}"

        # 1. Trích xuất các thực thể trong ngoặc kép hoặc từ khóa khóa học
        quotes = re.findall(r'[\"“\'](.*?)[\"”\']', prompt_text)
        course_target = None
        requested_tabs = []
        for q in quotes:
            q_str = q.strip()
            if any(k == q_str.lower() for k in ['course', 'grades', 'competencies', 'notes']):
                requested_tabs.append(q_str)
            elif len(q_str) > 3 and not course_target:
                course_target = q_str

        # Fallback từ khóa khóa học nếu không để trong ngoặc kép
        if not course_target:
            for kw in ["LEANBOT Terra Protocol C++ (EN)", "LEANBOT Terra Protocol", "Leanbot", "Course"]:
                if kw.lower() in prompt_text.lower():
                    course_target = kw
                    break

        # Nếu người dùng có yêu cầu về các tab Course, Grades, Competencies, Notes nhưng không đặt trong ngoặc
        if not requested_tabs:
            for tab_kw in ['Course', 'Grades', 'Competencies', 'Notes']:
                if tab_kw.lower() in prompt_text.lower():
                    requested_tabs.append(tab_kw)

        # 2. Tìm kiếm phần tử khóa học trên trang Dashboard
        course_elem = None
        if course_target:
            candidates = await page.query_selector_all(f"a:has-text('{course_target}'), button:has-text('{course_target}')")
            for c in candidates:
                if await c.is_visible():
                    course_elem = c
                    break

        if course_elem:
            step_idx += 1
            box_c = await course_elem.bounding_box()
            c_bbox = BoundingBox(x=box_c["x"], y=box_c["y"], w=box_c["width"], h=box_c["height"]) if box_c else None
            console.print(f"[cyan]▶ Bước {step_idx}:[/cyan] Truy cập khóa học: '[bold]{course_target}[/bold]'")

            raw_path_c = str(self.output_raw_dir / f"step_{step_idx:02d}_raw.png")
            await self._safe_screenshot(page, raw_path_c)
            actions.append(
                RecordedAction(
                    step_number=step_idx,
                    action_type="click",
                    selector=f"a:has-text('{course_target}')",
                    input_value=None,
                    element_text=f"Khóa học {course_target}",
                    bounding_box=c_bbox,
                    raw_screenshot_path=raw_path_c,
                    timestamp=time.time(),
                    page_title=await page.title() or "",
                    page_url=page.url,
                )
            )

            # Click vào khóa học và chờ điều hướng sang trang Course view
            try:
                await course_elem.click()
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
                await asyncio.sleep(2.5)
            except Exception as e:
                console.print(f"[yellow]⚠️ Lỗi điều hướng vào khóa học: {e}[/yellow]")

            # 3. Quét và ghi lại các Tab chuyên sâu được yêu cầu (Course, Grades, Competencies, Notes)
            for tab_name in requested_tabs:
                tab_candidates = await page.query_selector_all(f"a:has-text('{tab_name}'), button:has-text('{tab_name}')")
                target_tab = None
                for t in tab_candidates:
                    if await t.is_visible():
                        b = await t.bounding_box()
                        if b and b["width"] > 15:
                            target_tab = t
                            break

                if target_tab:
                    step_idx += 1
                    t_box = await target_tab.bounding_box()
                    tb_bbox = BoundingBox(x=t_box["x"], y=t_box["y"], w=t_box["width"], h=t_box["height"]) if t_box else None
                    console.print(f"[cyan]▶ Bước {step_idx}:[/cyan] Khám phá chức năng: '[bold]{tab_name}[/bold]'")

                    raw_path_tab = str(self.output_raw_dir / f"step_{step_idx:02d}_raw.png")
                    await self._safe_screenshot(page, raw_path_tab)
                    actions.append(
                        RecordedAction(
                            step_number=step_idx,
                            action_type="click",
                            selector=f"tab-{tab_name.lower()}",
                            input_value=None,
                            element_text=f"Chức năng {tab_name}",
                            bounding_box=tb_bbox,
                            raw_screenshot_path=raw_path_tab,
                            timestamp=time.time(),
                            page_title=await page.title() or "",
                            page_url=page.url,
                        )
                    )
        else:
            # Fallback nếu không có khóa học đặc thù: tìm chức năng điều hướng
            deep_target = await self._find_deep_feature_target(page, goal, user_notes)
            if deep_target:
                feat_elem, feat_name = deep_target
                step_idx += 1
                box_f = await feat_elem.bounding_box()
                f_bbox = BoundingBox(x=box_f["x"], y=box_f["y"], w=box_f["width"], h=box_f["height"]) if box_f else None
                console.print(f"[cyan]▶ Bước {step_idx}:[/cyan] Đi sâu vào chức năng: '[bold]{feat_name}[/bold]'")

                raw_path_f = str(self.output_raw_dir / f"step_{step_idx:02d}_raw.png")
                await self._safe_screenshot(page, raw_path_f)
                actions.append(
                    RecordedAction(
                        step_number=step_idx,
                        action_type="click",
                        selector=feat_name,
                        input_value=None,
                        element_text=f"Mục {feat_name}",
                        bounding_box=f_bbox,
                        raw_screenshot_path=raw_path_f,
                        timestamp=time.time(),
                        page_title=await page.title() or "",
                        page_url=page.url,
                    )
                )

        return actions

    async def _find_deep_feature_target(self, page: Page, goal: str, user_notes: Optional[str] = None):
        """Tìm phần tử chức năng ngóc ngách phù hợp nhất với mục tiêu hoặc ghi chú người dùng"""
        combined_text = f"{goal} {user_notes or ''}".lower()

        # 1. Thử tìm theo từ khóa người dùng yêu cầu
        keywords = ["leanbot", "categories", "ptools", "khóa học", "danh mục", "my courses", "recent", "home"]
        for kw in keywords:
            if kw in combined_text:
                try:
                    elem = await page.query_selector(f"a:has-text('{kw}' i), button:has-text('{kw}' i)")
                    if elem and await elem.is_visible():
                        t = (await elem.inner_text()).strip() or kw.title()
                        return elem, t
                except Exception:
                    pass

        # 2. Nếu không có từ khóa đặc thù, tìm chức năng nổi bật trên thanh điều hướng (Categories, PTools, Home)
        default_selectors = [
            "a:has-text('Categories')",
            "a:has-text('PTools')",
            "a:has-text('Home')",
            "nav a",
        ]
        for sel in default_selectors:
            try:
                elem = await page.query_selector(sel)
                if elem and await elem.is_visible():
                    t = (await elem.inner_text()).strip()
                    if t:
                        return elem, t
            except Exception:
                pass
        return None

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
