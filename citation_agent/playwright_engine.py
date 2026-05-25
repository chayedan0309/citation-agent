"""异步 Playwright 防封引擎 — 替代 Selenium 版 anti_detect.py

设计原则：
1. 使用 `playwright.async_api`，全面 async/await
2. BrowserContext 级别指纹隔离，每个搜索会话独立
3. 真实用户行为链仿真（逐字敲入、随机滚动、视觉残留停顿）
4. 保留与 anti_detect.py 一致的异常体系（CircuitBreakerError 等）
5. 使用系统 Chrome（channel="chrome"），无需额外下载 Chromium
"""
import os
import re
import random
import pickle
import asyncio
import logging
from typing import Optional
from urllib.parse import quote

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)

from config import (
    WINDOW_WIDTH,
    WINDOW_HEIGHT,
    WINDOW_WIDTH_VARY,
    WINDOW_HEIGHT_VARY,
    BLOCK_KEYWORDS,
    DELAY_MIN,
    DELAY_MAX,
    LONG_PAUSE_EVERY,
    LONG_PAUSE_MIN,
    LONG_PAUSE_MAX,
    COOKIE_PATH,
    GS_SEARCH_URL,
)

logger = logging.getLogger(__name__)

# ─── 自定义异常 ────────────────────────────────────────

class CircuitBreakerError(Exception):
    """熔断异常"""
    def __init__(self, reason: str, progress_data: Optional[dict] = None):
        self.reason = reason
        self.progress_data = progress_data
        super().__init__(f"[熔断] {reason}")

class BlockDetectedError(CircuitBreakerError):
    """检测到屏蔽"""
    pass

class CaptchaDetectedError(BlockDetectedError):
    """检测到 CAPTCHA"""
    def __init__(self, message="检测到 CAPTCHA 验证码，等待人工处理"):
        super().__init__(message)

class RateLimitError(CircuitBreakerError):
    """429 限流"""
    pass

# ─── 用户代理轮换池 ────────────────────────────────────

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

TIMEZONE_POOL = [
    "America/New_York", "Europe/London", "Asia/Shanghai",
    "Asia/Tokyo", "Australia/Sydney", "Europe/Berlin",
]

def get_random_ua() -> str:
    return random.choice(UA_POOL)

def get_random_timezone() -> str:
    return random.choice(TIMEZONE_POOL)

def get_random_geolocation() -> dict:
    locs = [
        {"latitude": 40.7128, "longitude": -74.0060},
        {"latitude": 51.5074, "longitude": -0.1278},
        {"latitude": 31.2304, "longitude": 121.4737},
        {"latitude": 35.6762, "longitude": 139.6503},
        {"latitude": 48.8566, "longitude": 2.3522},
    ]
    return random.choice(locs)


# ─── Playwright 浏览器引擎 ─────────────────────────────

class PlaywrightEngine:
    """Playwright 浏览器引擎上下文管理器

    用法:
        async with PlaywrightEngine() as (page, context):
            await navigate_gs_search(page, query)
    """

    def __init__(self, headless: bool = False):
        self.headless = headless
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def __aenter__(self) -> tuple[Page, BrowserContext]:
        self._pw = await async_playwright().__aenter__()
        ua = get_random_ua()
        logger.info(f"启动 Playwright (Chrome) | UA: {ua[:60]}...")

        self._browser = await self._pw.chromium.launch(
            channel="chrome",
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-webrtc",
                "--disable-web-security",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-dev-shm-usage",
                "--lang=en-US",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )

        viewport_w = WINDOW_WIDTH + random.randint(-WINDOW_WIDTH_VARY, WINDOW_WIDTH_VARY)
        viewport_h = WINDOW_HEIGHT + random.randint(-WINDOW_HEIGHT_VARY, WINDOW_HEIGHT_VARY)
        geo = get_random_geolocation()

        self._context = await self._browser.new_context(
            user_agent=ua,
            locale="en-US",
            timezone_id=get_random_timezone(),
            viewport={"width": viewport_w, "height": viewport_h},
            permissions=["geolocation"],
            geolocation=geo,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Sec-CH-UA": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
                "Sec-CH-UA-Mobile": "?0",
                "Sec-CH-UA-Platform": '"Windows"',
            },
        )

        self._page = await self._context.new_page()

        await self._page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                { name: 'Native Client', filename: 'internal-nacl-plugin' },
            ],
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en', 'zh-CN', 'zh'],
        });
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        if (!window.chrome) { window.chrome = {}; }
        window.chrome.runtime = {
            id: 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
            connect: () => {},
            sendMessage: () => {},
            getManifest: () => ({ version: '1.0' }),
        };
        """)

        logger.info("Playwright 引擎初始化完成")
        return self._page, self._context

    async def __aexit__(self, *args):
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.__aexit__(*args)
        except Exception:
            pass
        logger.info("Playwright 引擎已关闭")


# ─── Cookie 持久化 ─────────────────────────────────────

async def async_save_cookies(context: BrowserContext, path: str = COOKIE_PATH) -> None:
    try:
        cookies = await context.cookies()
        with open(path, "wb") as f:
            pickle.dump(cookies, f)
        logger.debug("已保存 %d 个 cookies", len(cookies))
    except Exception as e:
        logger.warning("保存 cookies 失败: %s", e)

async def async_load_cookies(context: BrowserContext, path: str = COOKIE_PATH) -> bool:
    if not os.path.exists(path):
        return False
    try:
        with open(path, "rb") as f:
            cookies = pickle.load(f)
        await context.add_cookies(cookies)
        logger.debug("已加载 %d 个 cookies", len(cookies))
        return True
    except Exception as e:
        logger.warning("加载 cookies 失败: %s", e)
        return False


# ─── 行为仿真工具 ──────────────────────────────────────

_request_counter = 0

async def async_random_delay(min_sec: float = None, max_sec: float = None,
                              status_cb=None) -> None:
    """异步随机延迟，三角分布更接近人类行为"""
    if status_cb:
        status_cb("wait", "⏳ 模拟学者停顿中...")
    min_sec = min_sec if min_sec is not None else DELAY_MIN
    max_sec = max_sec if max_sec is not None else DELAY_MAX
    mode = min_sec + (max_sec - min_sec) * 0.3
    delay = random.triangular(min_sec, max_sec, mode)
    logger.debug(f"延迟 {delay:.1f}s")
    await asyncio.sleep(delay)

def count_request() -> int:
    global _request_counter
    _request_counter += 1
    return _request_counter

async def async_maybe_long_pause(status_cb=None) -> None:
    """每 N 次请求后长暂停"""
    count = count_request()
    if count % LONG_PAUSE_EVERY == 0:
        pause = random.uniform(LONG_PAUSE_MIN, LONG_PAUSE_MAX)
        logger.info(f"--- 长暂停 {pause:.0f}s (已处理 {count} 篇) ---")
        if status_cb:
            status_cb("pause", f"💤 人类疲劳期休息，暂停 {pause:.0f}s...")
        await asyncio.sleep(pause)

async def async_human_type(page: Page, selector: str, text: str,
                            status_cb=None) -> None:
    """异步打字仿真 — 逐字敲入，随机间隔 50-250ms，敲完后视觉残留停顿"""
    if status_cb:
        status_cb("typing", "⚙️ 模拟人类非均匀打字中...")

    await page.click(selector)
    await asyncio.sleep(random.uniform(0.2, 0.5))
    await page.fill(selector, "")
    await asyncio.sleep(random.uniform(0.1, 0.3))

    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(random.triangular(0.05, 0.25, 0.1))

    await asyncio.sleep(random.uniform(0.5, 1.5))

async def async_human_scroll(page: Page) -> None:
    """模拟人类随机滚动行为"""
    try:
        scrolls = random.randint(1, 4)
        for _ in range(scrolls):
            delta_y = random.randint(100, 400) * random.choice([1, 1, 1, -1])
            await page.evaluate(f"window.scrollBy(0, {delta_y});")
            await asyncio.sleep(random.uniform(0.3, 1.0))
        await page.evaluate(f"window.scrollTo(0, {random.randint(0, 300)});")
        await asyncio.sleep(random.uniform(0.5, 1.5))
    except Exception:
        pass


# ─── 阻塞检测 ──────────────────────────────────────────

async def check_blocked(page: Page) -> None:
    """检测当前页面是否被屏蔽"""
    try:
        page_text = await page.evaluate("document.body.innerText") or ""
        content = page_text.lower()
    except Exception:
        raise CircuitBreakerError("无法读取页面内容")

    captcha_indicators = [
        "recaptcha", "g-recaptcha", "cf-turnstile",
        "challenge-platform", "_cf_chl_opt",
        "please verify you are a human",
        "verify your identity",
        "unusual traffic from your network",
        "unusual traffic",
    ]
    for indicator in captcha_indicators:
        if indicator in content:
            raise CaptchaDetectedError(f"检测到验证码: {indicator}")

    if "too many requests" in content or "429" in content:
        raise CircuitBreakerError("触发 429 限流")

    for kw in BLOCK_KEYWORDS:
        if kw.lower() in content:
            raise BlockDetectedError(f"检测到屏蔽关键词: {kw}")

    logger.debug("页面状态正常，未被屏蔽")


async def wait_for_captcha_manual_solve(page: Page, timeout: int = 300,
                                         status_cb=None) -> bool:
    """CAPTCHA 人工处理等待"""
    if status_cb:
        status_cb("captcha", "🚨 触发谷歌验证码！请在浏览器窗口手动完成验证！智能体已挂起等待...")
    logger.warning("=" * 60)
    logger.warning("检测到 CAPTCHA 验证码！")
    logger.warning("请在浏览器中手动完成验证...")
    logger.warning("将每隔 10 秒检测一次，最多等待 %d 秒", timeout)
    logger.warning("=" * 60)

    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        await asyncio.sleep(10)
        try:
            content = await page.evaluate("document.body.innerText") or ""
            captcha_still_present = any(
                ind in content.lower()
                for ind in ["recaptcha", "g-recaptcha", "cf-turnstile",
                           "please verify you are a human"]
            )
            if not captcha_still_present:
                logger.info("CAPTCHA 已解决，执行刷新重置风控计数...")
                try:
                    await page.reload()
                    await asyncio.sleep(random.uniform(4, 8))
                except Exception:
                    pass
                return True
            remaining = int(timeout - (asyncio.get_event_loop().time() - start))
            logger.info("等待 CAPTCHA 解决中... 剩余 %d 秒", remaining)
        except Exception:
            logger.info("页面已变化，假定 CAPTCHA 已解决")
            return True

    logger.error("CAPTCHA 解决超时")
    return False


# ─── Google Scholar 专用操作 ───────────────────────────

async def handle_gs_cookies(page: Page) -> None:
    """处理 Google Scholar Cookie 同意对话框"""
    consent_texts = ["Accept all", "Accept", "I agree", "Got it", "接受", "同意"]
    for text in consent_texts:
        try:
            btn = page.locator(f'button:has-text("{text}")')
            if await btn.count() > 0 and await btn.first.is_visible():
                await btn.first.click()
                logger.info("已处理 Cookie 同意对话框")
                await asyncio.sleep(random.uniform(1, 2))
                return
        except Exception:
            continue


async def navigate_gs_search(
    page: Page,
    query: str,
    timeout: int = 30,
    allow_direct_url: bool = True,
    status_cb=None,
) -> bool:
    """Google Scholar 异步搜索 — 默认直连 URL，备选输入框路径"""
    if status_cb:
        status_cb("navigating", "🌐 正在访问 Google Scholar...")

    query_truncated = query[:500]
    search_url = GS_SEARCH_URL.format(query=quote(query_truncated))

    # ── 主路径：直连 URL（更稳定） ────────────────────
    logger.info(f"直连 URL 搜索: {query_truncated[:60]}...")
    try:
        await page.goto(search_url, timeout=timeout * 1000)
        await asyncio.sleep(random.uniform(3, 5))
        await handle_gs_cookies(page)
        await page.wait_for_selector(
            '#gs_res_ccl .gs_ri', timeout=timeout * 1000,
        )
        return True
    except Exception as e:
        logger.warning("直连 URL 失败: %s", e)

    # ── 备选路径：输入框搜索 ──────────────────────────
    logger.info(f"备选: 搜索框输入 {query_truncated[:60]}...")

    try:
        if status_cb:
            status_cb("navigating", "🌐 导航至 Google Scholar 首页...")
        await page.goto("https://scholar.google.com/?hl=en", timeout=timeout * 1000)
        await asyncio.sleep(random.uniform(2, 3))
        await handle_gs_cookies(page)
    except Exception as e:
        logger.warning("导航到 GS 首页失败: %s", e)
        return False

    if status_cb:
        status_cb("locating", "🔍 定位搜索框...")
    try:
        await page.wait_for_selector('input[name="q"]', timeout=timeout * 1000)
    except Exception:
        logger.warning("未找到搜索框，搜索失败")
        return False

    await async_human_type(page, 'input[name="q"]', query_truncated, status_cb)
    await page.keyboard.press("Enter")

    if status_cb:
        status_cb("searching", "🔎 正在搜索，等待 Google Scholar 返回结果...")
    try:
        await page.wait_for_selector(
            '#gs_res_ccl .gs_ri', timeout=timeout * 1000,
        )
    except Exception:
        logger.warning("Google Scholar 搜索结果加载超时")
        return False

    return True


async def extract_citations_from_gs(page: Page) -> Optional[int]:
    """从 Google Scholar 页面提取引用次数（异步）"""
    try:
        script = """
        () => {
            const links = document.querySelectorAll('a');
            for (const link of links) {
                const text = link.textContent.trim();
                const match = text.match(/Cited by\\s*(\\d[\\d,]*)|被引用次数[：:]\\s*(\\d[\\d,]*)/i);
                if (match) {
                    const num = (match[1] || match[2]).replace(/,/g, '');
                    return parseInt(num, 10);
                }
            }
            const body = document.body.innerText;
            const m = body.match(/Cited by\\s*(\\d[\\d,]*)/i);
            if (m) return parseInt(m[1].replace(/,/g, ''), 10);
            return null;
        }
        """
        result = await page.evaluate(script)
        if result is not None:
            return result
    except Exception:
        pass

    try:
        from parser import parse_html_for_citations
        html = await page.content()
        return parse_html_for_citations(html)
    except Exception:
        return 0


async def extract_paper_title(page: Page) -> Optional[str]:
    """从 Google Scholar 结果页提取第一条论文标题（异步）"""
    try:
        script = """
        () => {
            const el = document.querySelector('#gs_res_ccl .gs_ri .gs_rt a');
            if (el) return el.textContent.trim();
            return document.title;
        }
        """
        title = await page.evaluate(script)
        if title:
            title = re.sub(r'\s*[-–|]\s*(Google Scholar|Semantic Scholar|arXiv|PubMed).*', '', title)
            return title.strip()
    except Exception:
        pass
    return None
