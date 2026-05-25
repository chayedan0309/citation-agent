"""反检测引擎 — 针对 Google Scholar 的浏览器指纹隐藏、行为仿真与熔断保护

核心策略：
1. 指纹隐藏：selenium-stealth 原生集成（Chrome/Edge 统一），document-start 全内核抹除
2. Cookie 持久化：保存登录态，减少验证码频率
3. 行为仿真：微观打字、鼠标轨迹、滚动、输入节奏随机化
4. 搜索路径：真人类输入框搜索，禁止直连 URL（仅作极端 fallback）
5. 验证码处理：检测到阻塞时暂停等待人工介入，解决后刷新重置风控计数
"""
import os
import sys
import time
import json
import random
import pickle
import logging
from typing import Optional
from urllib.parse import quote

# 确保能导入上级目录的共享模块
_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    NoSuchElementException,
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
)

logger = logging.getLogger(__name__)

# ─── Chrome 可用检测 ──────────────────────────────────
_CHROME_AVAILABLE = None
_EDGE_AVAILABLE = None


def is_chrome_available() -> bool:
    global _CHROME_AVAILABLE
    if _CHROME_AVAILABLE is not None:
        return _CHROME_AVAILABLE
    import shutil
    _CHROME_AVAILABLE = (
        shutil.which("chrome") is not None
        or shutil.which("google-chrome") is not None
        or shutil.which("google-chrome-stable") is not None
        or os.path.exists("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe")
    )
    return _CHROME_AVAILABLE


def is_edge_available() -> bool:
    global _EDGE_AVAILABLE
    if _EDGE_AVAILABLE is not None:
        return _EDGE_AVAILABLE
    import shutil
    _EDGE_AVAILABLE = (
        shutil.which("msedge") is not None
        or os.path.exists("C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe")
    )
    return _EDGE_AVAILABLE


# ─── 自定义异常 ────────────────────────────────────────

class CircuitBreakerError(Exception):
    """熔断异常 — 检测到被封禁时抛出"""
    def __init__(self, reason: str, progress_data: Optional[dict] = None):
        self.reason = reason
        self.progress_data = progress_data
        super().__init__(f"[熔断] {reason}")


class BlockDetectedError(CircuitBreakerError):
    """检测到验证码或屏蔽页面"""
    pass


class CaptchaDetectedError(BlockDetectedError):
    """检测到 CAPTCHA 验证码，需要人工介入"""
    def __init__(self, message="检测到 CAPTCHA 验证码，等待人工处理"):
        super().__init__(message)


class RateLimitError(CircuitBreakerError):
    """429 限流异常"""
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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


def get_random_ua() -> str:
    return random.choice(UA_POOL)


# ─── 通用隐匿 JS 脚本（selenium-stealth 不可用时的回退） ──

STEALTH_JS_FALLBACK = """
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
const originalQuery = navigator.permissions.query.bind(navigator.permissions);
navigator.permissions.query = (p) => {
    if (p.name === 'notifications') return Promise.resolve({ state: 'denied' });
    return originalQuery(p);
};
const getParameterProxyHandler = {
    apply: function(target, thisArg, args) {
        const param = args[0];
        if (param === 37445) return 'Intel Inc.';
        if (param === 37446) return 'Intel(R) UHD Graphics';
        return Reflect.apply(target, thisArg, args);
    }
};
try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    if (gl) { gl.getParameter = new Proxy(gl.getParameter.bind(gl), getParameterProxyHandler); }
} catch(e) {}
Object.defineProperties(screen, {
    colorDepth: { get: () => 24 },
    pixelDepth: { get: () => 24 },
    availTop: { get: () => 0 },
    availLeft: { get: () => 0 },
});
"""


# ─── 统一浏览器驱动工厂 ───────────────────────────────

def _apply_common_options(options, ua: str, width: int, height: int,
                          headless: bool, user_data_dir: Optional[str] = None):
    """应用浏览器通用启动选项"""
    options.add_argument(f"--window-size={width},{height}")
    options.add_argument(f"--user-agent={ua}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--lang=en-US")
    options.add_argument("--disable-webrtc")
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-features=IsolateOrigins,site-per-process")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if user_data_dir:
        options.add_argument(f"--user-data-dir={user_data_dir}")
    if headless:
        options.add_argument("--headless=new")


def _apply_selenium_stealth(driver: WebDriver, browser: str):
    """统一应用指纹抹除 — Chrome 用 selenium-stealth，Edge 用 JS 注入"""
    if browser == "chrome":
        try:
            from selenium_stealth import stealth
            stealth(
                driver,
                languages=["en-US", "en", "zh-CN", "zh"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True,
            )
            logger.debug("selenium-stealth 已应用 (Chrome)")
            return
        except ImportError:
            logger.warning("selenium-stealth 未安装，使用 JS 注入")

    # Edge / Chrome fallback：CDP 注入
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": STEALTH_JS_FALLBACK,
    })
    logger.debug(f"JS 注入已应用 ({browser})")


def get_stealth_driver(
    headless: bool = False,
    browser: str = "edge",
    user_data_dir: Optional[str] = None,
) -> WebDriver:
    """创建配置了增强反检测措施的浏览器实例（Chrome/Edge 统一入口）"""
    ua = get_random_ua()
    logger.info(f"启动 {browser} 浏览器 | UA: {ua[:60]}...")

    window_w = WINDOW_WIDTH + random.randint(-WINDOW_WIDTH_VARY, WINDOW_WIDTH_VARY)
    window_h = WINDOW_HEIGHT + random.randint(-WINDOW_HEIGHT_VARY, WINDOW_HEIGHT_VARY)

    if browser == "chrome":
        options = ChromeOptions()
        _apply_common_options(options, ua, window_w, window_h, headless, user_data_dir)
        driver = webdriver.Chrome(options=options)
        _apply_selenium_stealth(driver, "chrome")
    else:
        options = EdgeOptions()
        _apply_common_options(options, ua, window_w, window_h, headless, user_data_dir)
        driver = webdriver.Edge(options=options)
        _apply_selenium_stealth(driver, "edge")

    return driver


# ─── Cookie 持久化 ─────────────────────────────────────

def save_cookies(driver: WebDriver, path: str = COOKIE_PATH) -> None:
    """保存浏览器 cookies 到文件"""
    try:
        cookies = driver.get_cookies()
        with open(path, "wb") as f:
            pickle.dump(cookies, f)
        logger.debug("已保存 %d 个 cookies", len(cookies))
    except Exception as e:
        logger.warning("保存 cookies 失败: %s", e)


def load_cookies(driver: WebDriver, path: str = COOKIE_PATH) -> bool:
    """从文件加载 cookies 到浏览器"""
    if not os.path.exists(path):
        return False
    try:
        with open(path, "rb") as f:
            cookies = pickle.load(f)
        for cookie in cookies:
            try:
                driver.add_cookie(cookie)
            except Exception:
                continue
        logger.debug("已加载 %d 个 cookies", len(cookies))
        return True
    except Exception as e:
        logger.warning("加载 cookies 失败: %s", e)
        return False


# ─── 行为仿真工具 ──────────────────────────────────────

_request_counter = 0


def random_delay(min_sec: float = None, max_sec: float = None,
                 status_cb=None) -> None:
    """随机延迟，使用三角分布更接近人类行为"""
    if status_cb:
        status_cb("wait", "⏳ 模拟学者停顿中...")
    min_sec = min_sec if min_sec is not None else DELAY_MIN
    max_sec = max_sec if max_sec is not None else DELAY_MAX
    mode = min_sec + (max_sec - min_sec) * 0.3
    delay = random.triangular(min_sec, max_sec, mode)
    logger.debug(f"延迟 {delay:.1f}s")
    time.sleep(delay)


def count_request() -> int:
    global _request_counter
    _request_counter += 1
    return _request_counter


def maybe_long_pause(status_cb=None) -> None:
    """每 N 次请求后执行长暂停，模拟人类休息"""
    count = count_request()
    if count % LONG_PAUSE_EVERY == 0:
        pause = random.uniform(LONG_PAUSE_MIN, LONG_PAUSE_MAX)
        logger.info(f"--- 长暂停 {pause:.0f}s (已处理 {count} 篇) ---")
        if status_cb:
            status_cb("pause", f"💤 人类疲劳期休息，暂停 {pause:.0f}s...")
        time.sleep(pause)


def human_type(element, text: str, status_cb=None) -> None:
    """微观打字仿真器 — 模拟人类逐字敲击键盘的不均匀节奏"""
    if status_cb:
        status_cb("typing", "⚙️ 模拟人类非均匀打字中...")
    for char in text:
        element.send_keys(char)
        time.sleep(random.triangular(0.05, 0.25, 0.1))
    time.sleep(random.uniform(0.5, 1.5))


def human_like_scroll(driver: WebDriver) -> None:
    """模拟人类随机滚动行为"""
    try:
        scrolls = random.randint(1, 4)
        for _ in range(scrolls):
            delta_y = random.randint(100, 400) * random.choice([1, 1, 1, -1])
            driver.execute_script(f"window.scrollBy(0, {delta_y});")
            time.sleep(random.uniform(0.3, 1.0))
        driver.execute_script(f"window.scrollTo(0, {random.randint(0, 300)});")
        time.sleep(random.uniform(0.5, 1.5))
    except Exception:
        pass


def randomize_viewport(driver: WebDriver) -> None:
    """随机化窗口大小"""
    w = WINDOW_WIDTH + random.randint(-WINDOW_WIDTH_VARY, WINDOW_WIDTH_VARY)
    h = WINDOW_HEIGHT + random.randint(-WINDOW_HEIGHT_VARY, WINDOW_HEIGHT_VARY)
    try:
        driver.set_window_size(w, h)
    except Exception:
        pass


# ─── 熔断检测 ──────────────────────────────────────────

def check_blocked(driver: WebDriver) -> None:
    """检测当前页面是否被屏蔽（CAPTCHA / 429 / 屏蔽关键词）"""
    try:
        page_text = driver.page_source.lower()
    except Exception:
        raise CircuitBreakerError("无法读取页面源码")

    # 检测 CAPTCHA
    captcha_indicators = [
        "recaptcha", "g-recaptcha", "cf-turnstile",
        "challenge-platform", "_cf_chl_opt",
        "please verify you are a human",
        "verify your identity",
        "unusual traffic from your network",
        "unusual traffic",
    ]
    for indicator in captcha_indicators:
        if indicator in page_text:
            raise CaptchaDetectedError(f"检测到验证码: {indicator}")

    # 检测 HTTP 429 / 403
    if "too many requests" in page_text or "429" in page_text:
        raise CircuitBreakerError("触发 429 限流")

    # 检测屏蔽关键词
    for kw in BLOCK_KEYWORDS:
        if kw.lower() in page_text:
            raise BlockDetectedError(f"检测到屏蔽关键词: {kw}")

    logger.debug("页面状态正常，未被屏蔽")


def wait_for_captcha_manual_solve(driver: WebDriver, timeout: int = 300,
                                  status_cb=None) -> bool:
    """
    检测到 CAPTCHA 时暂停，等待人工处理。
    解决后执行 refresh + 缓冲延迟，重置 Google 风控计数。

    Returns:
        bool: CAPTCHA 是否已解决
    """
    if status_cb:
        status_cb("captcha", "🚨 触发谷歌验证码！请在浏览器窗口手动完成验证！智能体已挂起等待...")
    logger.warning("=" * 60)
    logger.warning("检测到 CAPTCHA 验证码！")
    logger.warning("请在浏览器中手动完成验证...")
    logger.warning("将每隔 10 秒检测一次，最多等待 %d 秒", timeout)
    logger.warning("=" * 60)

    start = time.time()
    while time.time() - start < timeout:
        time.sleep(10)
        try:
            page_text = driver.page_source.lower()
            captcha_still_present = any(
                ind in page_text
                for ind in ["recaptcha", "g-recaptcha", "cf-turnstile",
                           "please verify you are a human"]
            )
            if not captcha_still_present:
                logger.info("CAPTCHA 已解决，执行刷新重置风控计数...")
                try:
                    driver.refresh()
                    # 深度缓冲延迟，防止刚解开就二次封锁
                    time.sleep(random.uniform(4, 8))
                except Exception:
                    pass
                return True
            remaining = int(timeout - (time.time() - start))
            logger.info("等待 CAPTCHA 解决中... 剩余 %d 秒", remaining)
        except Exception:
            logger.info("页面已变化，假定 CAPTCHA 已解决")
            return True

    logger.error("CAPTCHA 解决超时")
    return False


# ─── Google Scholar 专用工具 ──────────────────────────

def handle_gs_cookies(driver: WebDriver) -> None:
    """处理 Google Scholar 的 Cookie 同意对话框"""
    try:
        consent_selectors = [
            '//button[contains(text(), "Accept all")]',
            '//button[contains(text(), "Accept")]',
            '//button[contains(text(), "I agree")]',
            '//button[contains(text(), "Got it")]',
            '//form//button[contains(text(), "Accept")]',
            '//button[contains(text(), "接受")]',
            '//button[contains(text(), "同意")]',
        ]
        for selector in consent_selectors:
            try:
                btn = WebDriverWait(driver, 2).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                btn.click()
                logger.info("已处理 Cookie 同意对话框")
                time.sleep(random.uniform(1, 2))
                return
            except (TimeoutException, NoSuchElementException):
                continue
    except Exception:
        pass


def navigate_gs_search(
    driver: WebDriver,
    query: str,
    timeout: int = 30,
    allow_direct_url: bool = False,
    status_cb=None,
) -> bool:
    """
    真人类搜索路径：通过输入框输入查询，而非直连 URL。

    仅在 allow_direct_url=True（极端异常回退）时使用直连 URL。

    Returns:
        bool: 页面是否加载成功
    """
    if status_cb:
        status_cb("navigating", "🌐 正在访问 Google Scholar...")

    # ── 极端 Fallback：直连 URL ──────────────────────
    if allow_direct_url:
        search_url = f"https://scholar.google.com/scholar?hl=en&q={quote(query[:500])}"
        logger.warning("直连 URL Fallback: %s...", search_url[:80])
        try:
            driver.get(search_url)
            time.sleep(random.uniform(3, 5))
            handle_gs_cookies(driver)
            WebDriverWait(driver, timeout).until(
                lambda d: (
                    len(d.find_elements(By.XPATH, '//div[@id="gs_res_ccl"]//div[@class="gs_ri"]')) > 0
                    or "captcha" in d.page_source.lower()
                )
            )
            return True
        except TimeoutException:
            return False
        except WebDriverException as e:
            logger.warning("直连 URL 失败: %s", e)
            return False

    # ── 正常流程：输入框搜索 ──────────────────────────
    query_truncated = query[:500]
    logger.info(f"搜索框输入: {query_truncated[:60]}...")

    try:
        if status_cb:
            status_cb("navigating", "🌐 导航至 Google Scholar 首页...")
        driver.get("https://scholar.google.com/?hl=en")
        time.sleep(random.uniform(2, 3))
        handle_gs_cookies(driver)
    except WebDriverException as e:
        logger.warning("导航到 GS 首页失败: %s", e)
        return False

    # Step 2: 定位搜索框
    if status_cb:
        status_cb("locating", "🔍 定位搜索框...")
    search_box = None
    try:
        search_box = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.NAME, "q"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", search_box)
        time.sleep(random.uniform(0.3, 0.6))
        try:
            search_box.click()
        except Exception:
            driver.execute_script("arguments[0].click();", search_box)
        time.sleep(random.uniform(0.3, 0.6))
    except (TimeoutException, NoSuchElementException):
        logger.warning("未找到搜索框，尝试直连 URL 回退")
        return navigate_gs_search(driver, query, timeout, allow_direct_url=True)

    # Step 3: 填入搜索内容（模拟粘贴，一次性填入）
    if status_cb:
        status_cb("typing", "⚙️ 正在输入搜索词...")
    try:
        search_box.clear()
        time.sleep(random.uniform(0.2, 0.4))
    except Exception:
        pass

    search_box.send_keys(query_truncated)
    # 随机停顿后回车提交（像真人确认搜索内容）
    time.sleep(random.uniform(0.8, 1.5))
    search_box.send_keys(Keys.RETURN)

    # Step 5: 等待结果加载
    if status_cb:
        status_cb("searching", "🔎 正在搜索，等待 Google Scholar 返回结果...")
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: (
                len(d.find_elements(By.XPATH, '//div[@id="gs_res_ccl"]//div[@class="gs_ri"]')) > 0
                or "captcha" in d.page_source.lower()
            )
        )
    except TimeoutException:
        logger.warning("Google Scholar 搜索结果加载超时")
        return False

    return True


def extract_citations_from_gs(driver: WebDriver) -> Optional[int]:
    """从 Google Scholar 搜索结果页提取引用次数（多重兜底）"""
    try:
        page_source = driver.page_source
    except Exception:
        return None

    # 优先从 <a> 标签提取 "Cited by" 或 "被引用次数"
    try:
        cited_by_links = driver.find_elements(
            By.XPATH,
            '//a[contains(text(), "Cited by") or contains(text(), "被引用")]'
        )
        if cited_by_links:
            import re
            text = cited_by_links[0].text
            match = re.search(r'(\d[\d,]*)', text)
            if match:
                return int(match.group(1).replace(",", ""))
    except Exception:
        pass

    # 回退：使用 parser 的正则提取
    from parser import parse_html_for_citations
    citations = parse_html_for_citations(page_source)
    if citations is not None:
        return citations

    # 没有任何引文数据 → 返回 0（而不是 None，避免被误判为失败）
    return 0
