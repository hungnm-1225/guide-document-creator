"""
Autonomous Flow Agent — Kiến trúc 2 lượt

LƯỢT 1 · TRINH SÁT (pioneer_scout)
  1. Đăng nhập (hỗ trợ OpenID SSO liên miền).
  2. Xác định khu vực menu mà người dùng yêu cầu (trái / trên / phải).
  3. ĐI THỰC TẾ từng mục: bấm mở, phát hiện menu cha (xổ menu con) và vào từng menu con,
     đọc nội dung thật của từng trang (tiêu đề, khối, cột, nút, số liệu).
  4. Gửi bản đồ + Mục tiêu + Ghi chú cho AI để lập KỊCH BẢN (FlowPlan).

LƯỢT 2 · VIẾT HƯỚNG DẪN (execute_autonomous_guide)
  Bám theo Kịch bản: click -> chụp ảnh (có toạ độ) -> mở màn hình -> chụp + đọc nội dung,
  gắn mục đích & ý cần thuyết minh của từng bước để bước sinh nội dung có đủ thông tin.
"""
import asyncio
import hashlib
import time
from typing import Optional, List, Dict, Any, Callable, Awaitable

from playwright.async_api import async_playwright, Browser, Page
from rich.console import Console

from config.settings import settings
from src.ai.schemas import RecordedAction, BoundingBox, SiteKnowledgeMap, FeatureModule, FlowPlan, FlowStep

console = Console()

# Không bao giờ bấm vào các mục này khi trinh sát (mất phiên đăng nhập / đổi trạng thái tài khoản)
DANGEROUS_WORDS = ("logout", "log out", "sign out", "đăng xuất", "thoát")
# Menu cha có các mục con mang tính "thao tác" — chỉ liệt kê, không bấm vào mục con
NO_DEEP_OPEN_WORDS = ("switch", "chuyển", "delete", "xóa", "remove", "language", "ngôn ngữ")

REGION_KEYWORDS = {
    "left": ("bên trái", "menu trái", "trái", "left", "sidebar", "thanh bên"),
    "top": ("thanh trên", "bên trên", "phía trên", "top", "header", "navbar"),
    "right": ("bên phải", "menu phải", "right"),
}

COLLECT_NAV_JS = r"""
(region) => {
  const vw = window.innerWidth;
  document.querySelectorAll('[data-guide-nav]').forEach(e => e.removeAttribute('data-guide-nav'));
  const sel = 'a, button, [role="button"], [role="menuitem"], [role="tab"], [role="link"], [role="treeitem"], ' +
              'li[class*="item" i], div[class*="ListItem" i], [class*="menu-item" i], [class*="nav-item" i], [class*="nav-link" i]';
  const byKey = new Map();
  const order = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 14) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity) === 0) continue;
    const text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').replace(/\s+/g, ' ').trim();
    if (!text || text.length < 2 || text.length > 50 || /^\d+$/.test(text)) continue;
    const absTop = r.top + window.scrollY;
    let reg = 'center';
    if (r.left < vw * 0.22 && r.right < vw * 0.32 && absTop > 40) reg = 'left';
    else if (absTop < 110 && r.height < 90) reg = 'top';
    else if (r.left > vw * 0.78) reg = 'right';
    if (region && reg !== region) continue;
    const key = text.toLowerCase();
    const prev = byKey.get(key);
    if (prev) {
      // Ưu tiên phần tử sâu hơn (bbox gọn hơn) nếu nằm bên trong phần tử trước
      if (prev.el.contains(el)) prev.el = el;
      continue;
    }
    const entry = { el, text, reg };
    byKey.set(key, entry);
    order.push(entry);
  }
  return order.map((e, i) => {
    const el = e.el;
    el.setAttribute('data-guide-nav', String(i));
    const r = el.getBoundingClientRect();
    const cls = (el.className && el.className.baseVal !== undefined) ? el.className.baseVal : (el.className || '');
    const active = /\b(active|selected|Mui-selected|current)\b/i.test(cls) ||
                   el.getAttribute('aria-current') !== null || el.getAttribute('aria-selected') === 'true' ||
                   !!el.closest('.active, .Mui-selected, [aria-current="page"]');
    return {
      i, text: e.text, region: e.reg, href: el.getAttribute('href'),
      x: Math.round(r.left), y: Math.round(r.top + window.scrollY), w: Math.round(r.width), h: Math.round(r.height),
      expanded: el.getAttribute('aria-expanded'), active
    };
  });
}
"""

FIND_CLICKABLE_JS = r"""
(text) => {
  document.querySelectorAll('[data-guide-target]').forEach(e => e.removeAttribute('data-guide-target'));
  const clean = text.toLowerCase().replace(/\s+/g, ' ').trim();
  const cands = Array.from(document.querySelectorAll(
    'a, button, [role="button"], [role="tab"], [role="menuitem"], [role="link"], li, div[class*="ListItem" i], [class*="card" i], h3, h4, h5'));
  const visible = (el) => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    return r.width > 8 && r.height > 8 && st.visibility !== 'hidden' && st.display !== 'none'; };
  const norm = (el) => (el.innerText || el.getAttribute('aria-label') || '').toLowerCase().replace(/\s+/g, ' ').trim();
  let best = null;
  for (const el of cands) { if (visible(el) && norm(el) === clean) { if (!best || best.contains(el)) best = el; } }
  if (!best) {
    for (const el of cands) {
      const t = norm(el);
      if (visible(el) && t.includes(clean) && t.length < clean.length + 80) { if (!best || best.contains(el)) best = el; }
    }
  }
  if (!best) return null;
  best.setAttribute('data-guide-target', '1');
  return true;
}
"""

PAGE_DIGEST_JS = r"""
([minX, minY]) => {
  const lines = []; const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const t = n.textContent.replace(/\s+/g, ' ').trim();
    if (!t || t.length < 2) continue;
    const el = n.parentElement; if (!el) continue;
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(el.tagName)) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.left < minX - 4) continue;
    if (r.top + window.scrollY < minY) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    if (seen.has(t)) continue; seen.add(t);
    const tag = el.closest('h1,h2,h3,h4,h5,th,button,a,[role="tab"],label');
    let prefix = '';
    if (tag) {
      const tg = tag.tagName;
      if (/^H[1-5]$/.test(tg)) prefix = '# ';
      else if (tg === 'TH') prefix = '[cột] ';
      else if (tg === 'BUTTON' || tag.getAttribute('role') === 'tab') prefix = '[nút] ';
      else if (tg === 'A') prefix = '[link] ';
      else if (tg === 'LABEL') prefix = '[nhãn] ';
    }
    lines.push(prefix + t.substring(0, 160));
    if (lines.length > 220) break;
  }
  const acts = [];
  for (const e of document.querySelectorAll('button, a, [role="tab"], [role="button"], select, input[type="search"], input[placeholder]')) {
    const r = e.getBoundingClientRect();
    if (r.width < 10 || r.height < 10 || r.left < minX - 4 || r.top + window.scrollY < minY) continue;
    const t = (e.innerText || e.getAttribute('aria-label') || e.getAttribute('placeholder') || '').replace(/\s+/g, ' ').trim();
    if (t && t.length > 1 && t.length < 60 && !acts.includes(t)) acts.push(t);
    if (acts.length >= 30) break;
  }
  return { title: document.title, url: location.href, text: lines.join('\n').substring(0, 3500), actions: acts };
}
"""


def _host(url: str) -> str:
    return url.split('/')[2] if '://' in url else ''


AUTH_ENTRY_EXACT = {"login", "log in", "sign in", "signin", "đăng nhập", "register", "sign up", "đăng ký"}


def _is_dangerous(name: str) -> bool:
    low = name.lower().strip()
    return low in AUTH_ENTRY_EXACT or any(w in low for w in DANGEROUS_WORDS)


def _no_deep_open(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in NO_DEEP_OPEN_WORDS)


class AutonomousFlowAgent:
    """Agent tự động đăng nhập, trinh sát và ghi hình hướng dẫn theo Kịch bản"""

    def __init__(self, headless: Optional[bool] = None):
        self.headless = settings.headless if headless is None else headless
        self.output_raw_dir = settings.output_dir / "raw_screenshots"
        self.output_raw_dir.mkdir(parents=True, exist_ok=True)
        self.max_top_items = 25
        self.max_children = 8

    # ==================================================================
    # Tiện ích trình duyệt
    # ==================================================================
    async def _safe_screenshot(self, page: Page, path: str, max_retries: int = 4, delay: float = 1.0) -> bool:
        """Chụp ảnh an toàn, thử lại khi trang đang chuyển hướng"""
        for attempt in range(max_retries):
            try:
                if page.is_closed():
                    return False
                await page.screenshot(path=path, timeout=6000)
                return True
            except Exception:
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=4000)
                except Exception:
                    pass
                await asyncio.sleep(delay * (attempt + 1))
        console.print(f"[red]⚠️ Không chụp được ảnh: {path}[/red]")
        return False

    async def _launch_browser(self, p) -> Browser:
        for ch in ["msedge", "chrome", None]:
            try:
                kwargs = {
                    "headless": self.headless,
                    "args": ["--no-sandbox", "--disable-dev-shm-usage"]
                }
                if ch:
                    kwargs["channel"] = ch
                return await p.chromium.launch(**kwargs)
            except Exception:
                continue
        raise RuntimeError("Không thể khởi động trình duyệt.")

    async def _settle(self, page: Page, extra: float = 1.0):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        await asyncio.sleep(extra)

    async def _bbox_of(self, handle) -> Optional[BoundingBox]:
        try:
            b = await handle.bounding_box()
            if b:
                return BoundingBox(x=b["x"], y=b["y"], w=b["width"], h=min(b["height"], 260.0))
        except Exception:
            pass
        return None

    async def _collect_nav(self, page: Page, region: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            items = await page.evaluate(COLLECT_NAV_JS, region)
            return [it for it in items if not _is_dangerous(it["text"])]
        except Exception:
            return []

    async def _digest(self, page: Page, bounds: Dict[str, float]) -> Dict[str, Any]:
        try:
            return await page.evaluate(PAGE_DIGEST_JS, [bounds.get("minX", 0), bounds.get("minY", 0)])
        except Exception:
            return {"title": "", "url": page.url, "text": "", "actions": []}

    async def _content_bounds(self, page: Page, region: str) -> Dict[str, float]:
        """Vùng nội dung chính = bên phải sidebar trái và bên dưới banner (để không đọc lại menu)"""
        if region != "left":
            return {"minX": 0, "minY": 0}
        items = await self._collect_nav(page, "left")
        if not items:
            return {"minX": 0, "minY": 0}
        min_x = max(it["x"] + it["w"] for it in items) + 4
        min_y = max(0, min(it["y"] for it in items) - 80)
        return {"minX": min_x, "minY": min_y}

    async def _locate(self, page: Page, text: str, region: Optional[str]):
        """Tìm phần tử theo text: ưu tiên menu trong khu vực, sau đó toàn trang"""
        target = text.strip().lower()
        if region:
            for it in await self._collect_nav(page, region):
                if it["text"].strip().lower() == target:
                    loc = page.locator(f'[data-guide-nav="{it["i"]}"]').first
                    return loc
        try:
            found = await page.evaluate(FIND_CLICKABLE_JS, text)
            if found:
                return page.locator('[data-guide-target="1"]').first
        except Exception:
            pass
        return None

    async def _is_visible_text(self, page: Page, text: str, region: Optional[str]) -> bool:
        target = text.strip().lower()
        return any(it["text"].strip().lower() == target for it in await self._collect_nav(page, region))

    async def _click(self, page: Page, loc) -> bool:
        try:
            await loc.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        try:
            await loc.click(timeout=6000)
            return True
        except Exception:
            try:
                b = await loc.bounding_box()
                if b:
                    await page.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
                    return True
            except Exception:
                pass
        return False

    async def _open_path(self, page: Page, path: List[str], region: Optional[str], dashboard_url: Optional[str], retry: bool = True) -> bool:
        """
        Mở lần lượt các phần tử trong path (trừ phần tử cuối nếu chỉ cần chuẩn bị).
        Không bấm lại menu cha nếu menu con kế tiếp đã hiển thị (tránh thu gọn menu).
        """
        for idx, part in enumerate(path):
            if idx < len(path) - 1 and await self._is_visible_text(page, path[idx + 1], region):
                continue
            loc = await self._locate(page, part, region)
            if not loc:
                if retry and dashboard_url:
                    await page.goto(dashboard_url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
                    await self._settle(page, 1.2)
                    return await self._open_path(page, path, region, dashboard_url, retry=False)
                return False
            if not await self._click(page, loc):
                return False
            await self._settle(page, 1.0)
        return True

    async def _detect_landing(self, page: Page, region: str) -> str:
        items = await self._collect_nav(page, region)
        cur = page.url.lower().rstrip("/")
        for it in items:
            slug = it["text"].strip().lower().replace(" ", "-")
            if slug and (cur.endswith("/" + slug) or f"/{slug}/" in cur + "/"):
                return it["text"]
        for it in items:
            if it.get("active"):
                return it["text"]
        try:
            h = await page.evaluate("() => { const h = document.querySelector('main h1, main h2, h1'); return h ? h.innerText.trim() : ''; }")
            if h and 2 < len(h) < 50:
                return h
        except Exception:
            pass
        return "Dashboard"

    def _choose_region(self, goal: str, notes: Optional[str], counts: Dict[str, int]) -> str:
        text = f"{goal} {notes or ''}".lower()
        for reg, words in REGION_KEYWORDS.items():
            if any(w in text for w in words) and counts.get(reg, 0) > 0:
                return reg
        if counts.get("left", 0) >= 3:
            return "left"
        return "top" if counts.get("top", 0) >= 2 else max(counts, key=counts.get, default="left")

    # ==================================================================
    # Đăng nhập (dùng chung cho 2 lượt)
    # ==================================================================
    async def _login_flow(self, page: Page, creds: Dict[str, str], snap: Optional[Callable[..., Awaitable[None]]] = None):
        username = creds.get("username") or creds.get("email") or ""
        password = creds.get("password") or ""
        if not username:
            return

        login_link = await self._find_login_trigger(page)
        if login_link:
            text = ((await login_link.inner_text()) or "").strip() or "Log in"
            if snap:
                await snap("click", "auth", "login-trigger", f"Nút {text}", await self._bbox_of(login_link))
            await login_link.click()
            await self._settle(page, 1.5)

        sso_host = _host(page.url)
        user_input = await self._find_input(page, ["username", "email", "login", "user"])
        if user_input:
            if snap:
                await snap("fill", "auth", "input[name='username']", "Tên tài khoản hoặc Email", await self._bbox_of(user_input), input_value="[REDACTED_USERNAME]")
            await user_input.fill(username)
            await asyncio.sleep(0.4)

        pass_input = await page.query_selector("input[type='password'], input[name='password']")
        if pass_input:
            if snap:
                await snap("fill", "auth", "input[type='password']", "Mật khẩu", await self._bbox_of(pass_input), input_value="[REDACTED_PASSWORD]")
            await pass_input.fill(password)
            await asyncio.sleep(0.4)

        submit_btn = await self._find_submit_button(page)
        if submit_btn:
            if snap:
                await snap("click", "auth", "button[type='submit']", "Nút đăng nhập", await self._bbox_of(submit_btn))
            try:
                await submit_btn.click()
                try:
                    await page.wait_for_url(lambda u: sso_host not in u, timeout=20000)
                    console.print(f"   ✔️ Đăng nhập xong, đang ở: [green]{page.url}[/green]")
                except Exception:
                    pass
            except Exception as e:
                console.print(f"[yellow]⚠️ Lỗi bấm đăng nhập: {e}[/yellow]")
            await self._settle(page, 2.0)

    # ==================================================================
    # LƯỢT 1 · TRINH SÁT
    # ==================================================================
    async def pioneer_scout(
        self,
        url: str,
        credentials: Optional[Dict[str, str]] = None,
        goal: str = "",
        user_notes: Optional[str] = None,
        run_tag: str = "",
        analyzer=None,
    ) -> SiteKnowledgeMap:
        console.print(f"[bold cyan]🧭 [Lượt 1 · Trinh Sát][/bold cyan] Bắt đầu tại: [underline]{url}[/underline]")
        recon_dir = settings.output_dir / "recon"
        recon_dir.mkdir(parents=True, exist_ok=True)
        tag = run_tag or str(int(time.time()))

        async with async_playwright() as p:
            browser = await self._launch_browser(p)
            context = await browser.new_context(viewport={"width": settings.viewport_width, "height": settings.viewport_height})
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
            await self._settle(page, 1.0)
            landing_title = await page.title() or ""
            landing_img = str(recon_dir / f"{tag}_landing.png")
            await self._safe_screenshot(page, landing_img)

            await self._login_flow(page, credentials or {})
            sso_domain = None

            k_map = await self._build_knowledge_map(page, url, landing_title, goal, user_notes, analyzer)
            k_map.sso_domain = sso_domain
            k_map.landing_screenshot_path = landing_img
            dash_img = str(recon_dir / f"{tag}_dashboard.png")
            await self._safe_screenshot(page, dash_img)
            k_map.dashboard_screenshot_path = dash_img
            await browser.close()

        k_file = recon_dir / f"{tag}_site_knowledge_map.json"
        k_file.write_text(k_map.model_dump_json(indent=2), encoding="utf-8")
        (recon_dir / "site_knowledge_map.json").write_text(k_map.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"[green]✅ [Trinh Sát] Bản đồ + Kịch bản đã lưu: {k_file}[/green]")
        return k_map

    async def _build_knowledge_map(
        self, page: Page, url: str, landing_title: str, goal: str, user_notes: Optional[str], analyzer=None
    ) -> SiteKnowledgeMap:
        """Đứng tại trang sau đăng nhập: đi thực tế từng mục menu, đọc nội dung, rồi nhờ AI lập Kịch bản"""
        dashboard_url = page.url
        dashboard_title = await page.title() or ""

        all_items = await self._collect_nav(page, None)
        counts: Dict[str, int] = {}
        for it in all_items:
            counts[it["region"]] = counts.get(it["region"], 0) + 1
        region = self._choose_region(goal, user_notes, counts)
        landing = await self._detect_landing(page, region)
        bounds = await self._content_bounds(page, region)
        dash_digest = await self._digest(page, bounds)
        console.print(f"📍 Khu vực menu trinh sát: [bold]{region}[/bold] · Trang hạ cánh: [bold]{landing}[/bold] · Số mục: {counts}")

        top_items = (await self._collect_nav(page, region))[: self.max_top_items]
        top_names = [it["text"] for it in top_items]
        console.print(f"   Các mục menu: {top_names}")

        features: List[FeatureModule] = []
        for item in top_items:
            name = item["text"]
            if name.strip().lower() == landing.strip().lower():
                features.append(FeatureModule(
                    name=name, selector=name, location=region, click_path=[name],
                    page_url=dashboard_url, page_title=dashboard_title,
                    page_content=dash_digest.get("text"), page_actions=dash_digest.get("actions", []),
                ))
                continue
            try:
                fm_list = await self._scout_item(page, name, region, top_names, dashboard_url, bounds)
                features.extend(fm_list)
            except Exception as e:
                console.print(f"[yellow]⚠️ Trinh sát '{name}' lỗi: {e}[/yellow]")

        k_map = SiteKnowledgeMap(
            landing_url=url,
            landing_title=landing_title,
            dashboard_url=dashboard_url,
            dashboard_title=dashboard_title,
            default_landing_module=landing,
            discovered_features=features,
            left_sidebar_features=[f for f in features if region == "left"],
            top_nav_features=[f for f in features if region == "top"],
            right_sidebar_features=[f for f in features if region == "right"],
            explored_region=region,
            dashboard_content=dash_digest.get("text"),
            recommended_walkthroughs=top_names,
        )
        console.print(f"[green]🗺️  Đã đi thực tế {len(features)} chức năng.[/green]")

        if analyzer is not None:
            console.print("[cyan]🧠 AI đang chắp nối Bản đồ + Mục tiêu + Ghi chú để lập Kịch bản...[/cyan]")
            k_map.flow_plan = analyzer.plan_flow(k_map, goal, user_notes)
            fp = k_map.flow_plan
            console.print(f"📋 Kịch bản ({'AI' if fp.planned_by_ai else 'dự phòng tuần tự'}) gồm {len(fp.steps)} bước:")
            for i, s in enumerate(fp.steps, 1):
                console.print(f"   {i:02d}. [{s.kind}] {' › '.join(s.click_path) or '(trang hiện tại)'} — {s.purpose}")

        # Quay lại trang hạ cánh để trạng thái sạch
        try:
            await page.goto(dashboard_url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
            await self._settle(page, 1.0)
        except Exception:
            pass
        return k_map

    async def _scout_item(
        self, page: Page, name: str, region: str, top_names: List[str], dashboard_url: str, bounds: Dict[str, float]
    ) -> List[FeatureModule]:
        """Bấm 1 mục menu: nếu là menu cha thì ghi nhận & đi vào từng menu con; nếu là trang thì đọc nội dung"""
        results: List[FeatureModule] = []
        top_set = {t.lower() for t in top_names}

        before_items = await self._collect_nav(page, region)
        before = {it["text"].lower() for it in before_items}
        prev_url = page.url

        loc = await self._locate(page, name, region)
        if not loc:
            await page.goto(dashboard_url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
            await self._settle(page, 1.0)
            before = {it["text"].lower() for it in await self._collect_nav(page, region)}
            prev_url = page.url
            loc = await self._locate(page, name, region)
            if not loc:
                console.print(f"[yellow]   ⚠️ Không tìm thấy mục '{name}'[/yellow]")
                return results

        await self._click(page, loc)
        await self._settle(page, 1.0)

        after_items = await self._collect_nav(page, region)
        after = {it["text"].lower() for it in after_items}
        new_items = [it["text"] for it in after_items if it["text"].lower() not in before and it["text"].lower() not in top_set]
        disappeared = before - after

        # Menu cha đang mở sẵn bị thu gọn -> bấm lại để mở
        if not new_items and disappeared and page.url == prev_url:
            await self._click(page, await self._locate(page, name, region) or loc)
            await self._settle(page, 0.8)
            after_items = await self._collect_nav(page, region)
            new_items = [it["text"] for it in after_items if it["text"].lower() not in top_set and it["text"].lower() in disappeared]

        if new_items and page.url == prev_url:
            children = new_items[:10]
            console.print(f"   📂 '{name}' là menu cha → menu con: {children}")
            results.append(FeatureModule(
                name=name, selector=name, location=region, is_expandable=True,
                click_path=[name], children=children, page_url=page.url,
            ))
            if _no_deep_open(name):
                return results
            for child in children[: self.max_children]:
                ok = await self._open_path(page, [name, child], region, dashboard_url)
                if not ok:
                    console.print(f"[yellow]   ⚠️ Không mở được '{name} › {child}'[/yellow]")
                    continue
                dg = await self._digest(page, bounds)
                console.print(f"      ↳ {child}: {dg.get('url')}")
                results.append(FeatureModule(
                    name=child, selector=child, location=region, click_path=[name, child],
                    page_url=dg.get("url"), page_title=dg.get("title"),
                    page_content=dg.get("text"), page_actions=dg.get("actions", []),
                ))
                await self._dismiss_overlays(page)
            return results

        dg = await self._digest(page, bounds)
        console.print(f"   📄 '{name}' → {dg.get('url')}")
        results.append(FeatureModule(
            name=name, selector=name, location=region, click_path=[name],
            page_url=dg.get("url"), page_title=dg.get("title"),
            page_content=dg.get("text"), page_actions=dg.get("actions", []),
        ))
        await self._dismiss_overlays(page)
        return results

    async def _dismiss_overlays(self, page: Page):
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass

    # ==================================================================
    # LƯỢT 2 · VIẾT HƯỚNG DẪN (ghi hình theo Kịch bản)
    # ==================================================================
    async def execute_autonomous_guide(
        self,
        url: str,
        goal: str = "Đăng nhập hệ thống",
        credentials: Optional[Dict[str, str]] = None,
        user_notes: Optional[str] = None,
        knowledge_map: Optional[SiteKnowledgeMap] = None,
        run_tag: str = "",
        analyzer=None,
    ) -> List[RecordedAction]:
        tag = run_tag or str(int(time.time()))
        recorded: List[RecordedAction] = []
        console.print(f"[bold cyan]🎬 [Lượt 2 · Viết Hướng Dẫn][/bold cyan] Bắt đầu ghi hình tại: [underline]{url}[/underline]")

        async with async_playwright() as p:
            browser = await self._launch_browser(p)
            context = await browser.new_context(viewport={"width": settings.viewport_width, "height": settings.viewport_height})
            page = await context.new_page()

            async def snap(action_type: str, role: str, selector: str, text: str, bbox: Optional[BoundingBox],
                           input_value: Optional[str] = None, page_context: Optional[str] = None,
                           purpose: Optional[str] = None, points: Optional[List[str]] = None):
                idx = len(recorded) + 1
                path = str(self.output_raw_dir / f"{tag}_step_{idx:02d}_raw.png")
                await self._safe_screenshot(page, path)
                recorded.append(RecordedAction(
                    step_number=idx, action_type=action_type, selector=selector, input_value=input_value,
                    element_text=text, bounding_box=bbox, raw_screenshot_path=path, timestamp=time.time(),
                    page_title=await page.title() or "", page_url=page.url,
                    step_role=role, page_context=page_context, plan_purpose=purpose, talking_points=points or [],
                ))
                console.print(f"[cyan]▶ Bước {idx}[/cyan] [{role}] {text}")

            # 1. Truy cập + đăng nhập
            await page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
            await self._settle(page, 1.0)
            await snap("navigate", "navigate", url, f"Truy cập {await page.title() or url}", None, input_value=url)
            await self._login_flow(page, credentials or {}, snap)

            # 2. Nếu Lượt 1 không có kịch bản -> trinh sát ngay trong phiên này
            if not knowledge_map or not knowledge_map.flow_plan or not knowledge_map.flow_plan.steps:
                console.print("[yellow]⚠️ Chưa có Kịch bản từ Lượt 1 — trinh sát trực tiếp trong phiên ghi hình.[/yellow]")
                knowledge_map = await self._build_knowledge_map(page, url, "", goal, user_notes, analyzer)
                if not knowledge_map.flow_plan:
                    from src.ai.gemini_client import GeminiAnalyzer
                    knowledge_map.flow_plan = (analyzer or GeminiAnalyzer()).fallback_flow_plan(knowledge_map, goal)

            plan: FlowPlan = knowledge_map.flow_plan
            region = knowledge_map.explored_region or "left"
            dashboard_url = knowledge_map.dashboard_url or page.url
            bounds = await self._content_bounds(page, region)
            landing = knowledge_map.default_landing_module or "Dashboard"
            children_of = {f.name.lower(): f.children for f in knowledge_map.discovered_features if f.children}

            # 3. Màn hình hạ cánh sau đăng nhập
            view_cur = next((s for s in plan.steps if s.kind == "view_current"), None)
            dg = await self._digest(page, bounds)
            await self._scroll_top(page)
            await snap("custom", "view", "landing", landing, None,
                       page_context=dg.get("text"),
                       purpose=(view_cur.purpose if view_cur else f"Màn hình {landing} hiển thị ngay sau khi đăng nhập."),
                       points=(view_cur.talking_points if view_cur else []))

            # 4. Thực thi từng bước Kịch bản
            for s in plan.steps:
                if s.kind == "view_current" or not s.click_path:
                    continue
                if len(s.click_path) == 1 and s.click_path[0].strip().lower() == landing.strip().lower():
                    continue
                try:
                    await self._perform_plan_step(page, s, region, dashboard_url, bounds, children_of, snap)
                except Exception as e:
                    console.print(f"[yellow]⚠️ Bỏ qua bước '{' › '.join(s.click_path)}': {e}[/yellow]")

            await browser.close()

        console.print(f"\n[bold green]🎉 Lượt 2 hoàn tất: {len(recorded)} bước đã ghi hình.[/bold green]")
        return recorded

    async def _scroll_top(self, page: Page):
        try:
            await page.evaluate("window.scrollTo({ top: 0, behavior: 'instant' })")
            await asyncio.sleep(0.4)
        except Exception:
            pass

    async def _perform_plan_step(self, page: Page, s: FlowStep, region: str, dashboard_url: str,
                                 bounds: Dict[str, float], children_of: Dict[str, List[str]], snap):
        target = s.click_path[-1]
        prefix = s.click_path[:-1]

        # Chuẩn bị đường đi tới phần tử (mở menu cha nếu cần, không thu gọn menu đang mở)
        if prefix:
            if not await self._is_visible_text(page, target, region):
                if not await self._open_path(page, prefix, region, dashboard_url):
                    raise RuntimeError(f"không mở được {' › '.join(prefix)}")

        loc = await self._locate(page, target, region)
        if not loc:
            await page.goto(dashboard_url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
            await self._settle(page, 1.0)
            if prefix:
                await self._open_path(page, prefix, region, dashboard_url, retry=False)
            loc = await self._locate(page, target, region)
            if not loc:
                raise RuntimeError(f"không tìm thấy '{target}' trên giao diện")

        try:
            await loc.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass

        if s.kind == "expand":
            kids = children_of.get(target.lower(), [])
            already_open = bool(kids) and await self._is_visible_text(page, kids[0], region)
            if not already_open:
                await self._click(page, loc)
                await self._settle(page, 0.8)
            bbox = await self._group_bbox(page, target, kids, region)
            await snap("click", "expand", target, target, bbox,
                       page_context=("Menu con: " + ", ".join(kids)) if kids else None,
                       purpose=s.purpose, points=s.talking_points)
            return

        # kind == open: ảnh trước khi bấm (khung đỏ vào mục), rồi ảnh màn hình sau khi mở
        bbox = await self._bbox_of(loc)
        await snap("click", "click", target, target, bbox, purpose=s.purpose)
        await self._click(page, loc)
        await self._settle(page, 1.5)
        dg = await self._digest(page, bounds)
        await self._scroll_top(page)
        await snap("custom", "view", f"view-{target}", target, None,
                   page_context=dg.get("text"), purpose=s.purpose, points=s.talking_points)
        await self._dismiss_overlays(page)

    async def _group_bbox(self, page: Page, parent: str, kids: List[str], region: str) -> Optional[BoundingBox]:
        """Khung bao menu cha + các menu con vừa xổ ra"""
        items = await self._collect_nav(page, region)
        names = {parent.lower(), *[k.lower() for k in kids]}
        boxes = [it for it in items if it["text"].lower() in names]
        if not boxes:
            return None
        scroll_y = await page.evaluate("window.scrollY")
        x1 = min(b["x"] for b in boxes)
        y1 = min(b["y"] for b in boxes) - scroll_y
        x2 = max(b["x"] + b["w"] for b in boxes)
        y2 = max(b["y"] + b["h"] for b in boxes) - scroll_y
        return BoundingBox(x=x1, y=max(0, y1), w=x2 - x1, h=min(y2 - y1, settings.viewport_height))

    # ==================================================================
    # Bộ tìm phần tử đăng nhập
    # ==================================================================
    async def _find_login_trigger(self, page: Page):
        selectors = [
            "a:has-text('Log in')", "a:has-text('Đăng nhập')", "button:has-text('Log in')", "button:has-text('Đăng nhập')",
            "a:has-text('Sign in')", "button:has-text('Sign in')", "a[href*='login' i]", "a[href*='oidc' i]",
            "a[href*='auth' i]", ".login-btn", "#login-btn",
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
        for kw in keywords:
            for sel in (f"input[name*='{kw}' i]", f"input[id*='{kw}' i]", f"input[placeholder*='{kw}' i]"):
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        return el
                except Exception:
                    continue
        try:
            return await page.query_selector("input[type='text']:visible, input[type='email']:visible")
        except Exception:
            return None

    async def _find_submit_button(self, page: Page):
        selectors = [
            "button[type='submit']", "input[type='submit']", "button:has-text('Login')",
            "button:has-text('Đăng nhập')", "button:has-text('Sign In')", "button.btn-primary",
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return el
            except Exception:
                continue
        return None
