"""学术引文自动化采集智能体 — 主调度器（含实时 UI）"""
import sys
import os

# 使 v1/ 内的代码能导入上级目录的共享模块（config, excel_handler 等）
_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import time
import random
import logging
import argparse
from typing import Optional

import pandas as pd

from config import (
    EXCEL_PATH,
    SIMILARITY_THRESHOLD,
    DEFAULT_CITATION_NOT_FOUND,
    DEFAULT_CITATION_ERROR,
    GS_TIMEOUT,
)
from excel_handler import ExcelHandler
from v1.anti_detect import (
    get_stealth_driver,
    random_delay,
    maybe_long_pause,
    check_blocked,
    human_like_scroll,
    randomize_viewport,
    save_cookies,
    load_cookies,
    navigate_gs_search,
    extract_citations_from_gs,
    handle_gs_cookies,
    wait_for_captcha_manual_solve,
    CircuitBreakerError,
    BlockDetectedError,
    CaptchaDetectedError,
    RateLimitError,
)
from v1.parser import extract_paper_title
from similarity import check_title_match
from report import print_report
from ui import ProgressUI, UILogHandler
from rich.prompt import Prompt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

MAX_429_RETRIES = 5


class CitationAgent:
    """引文采集智能体 — Google Scholar 直搜版"""

    def __init__(self, excel_path: str = None, browser: str = "edge"):
        self.excel_handler = ExcelHandler(excel_path=excel_path)
        self.browser = browser
        self.driver: Optional["WebDriver"] = None
        self.results: list[dict] = []
        self.processed_count = 0
        self._rate_limited_queue: list[tuple[int, pd.Series]] = []
        self._ui: Optional[ProgressUI] = None

    def _setup_ui(self, total: int) -> None:
        """初始化 UI 并接管日志"""
        self._ui = ProgressUI(total=total)
        root = logging.getLogger()
        root.addHandler(UILogHandler(self._ui))

    def _make_status_cb(self):
        """创建状态回调函数，将动作状态推送到 UI"""
        def cb(state: str, detail: str):
            if self._ui and detail:
                self._ui.update(action=detail)
        return cb

    def _check_delete_key(self) -> bool:
        """非阻塞检查用户是否按下了 D 键（标记删除当前论文）"""
        try:
            import msvcrt
            if msvcrt.kbhit():
                key = msvcrt.getch()
                if key in (b'd', b'D'):
                    return True
        except ImportError:
            pass
        return False

    # ─── 主循环 ────────────────────────────────────────

    def run(self) -> list[dict]:
        """执行采集主循环"""
        logger.info("=" * 50)
        logger.info("学术引文采集智能体 (Google Scholar 直搜版) 启动")
        logger.info("=" * 50)

        pending = self._load_pending()
        if pending.empty:
            logger.info("没有待处理的论文，任务完成。")
            return self.results

        total = len(pending)
        logger.info("待处理 %d 篇论文", total)
        self._setup_ui(total)

        try:
            self._init_browser()
        except Exception as e:
            logger.critical("浏览器初始化失败: %s", e)
            self._close_ui()
            sys.exit(1)

        status_cb = self._make_status_cb()

        try:
            # ── 第一轮 ─────────────────────────────────
            for idx, paper in pending.iterrows():
                short_name = str(paper.get(self.excel_handler.col_title, "")).strip()
                self._ui.update(paper=short_name, action="⏳ 准备检索...")

                # 检查用户是否按 D 键标记删除
                if self._check_delete_key():
                    logger.info("  🗑 用户标记删除: %s", short_name)
                    self.excel_handler.mark_deleted(idx)
                    self.processed_count += 1
                    s = self._ui.stats
                    s["skipped"] += 1
                    self._ui.update(advance=1, action="🗑 已删除，跳过本篇", **s)
                    continue

                self._process_single_paper(idx, paper, total, status_cb)

                random_delay(status_cb=status_cb)

                if self.excel_handler.should_save():
                    self._flush_results()

                maybe_long_pause(status_cb=status_cb)

            # ── 第二轮：重试被限流跳过的 ────────────────
            if self._rate_limited_queue:
                logger.info("第一轮完成，开始重试 %d 篇被限流跳过的论文...",
                            len(self._rate_limited_queue))
                self._ui.update(action="🔄 第一轮完成，准备重试被跳过的论文...")
                time.sleep(random.uniform(30, 60))

                for idx, paper in self._rate_limited_queue:
                    short_name = str(paper.get(self.excel_handler.col_title, "")).strip()
                    self._ui.update(paper=short_name, action="🔄 重试被限流跳过的论文...")

                    # 同样支持 D 键删除
                    if self._check_delete_key():
                        logger.info("  🗑 用户标记删除: %s", short_name)
                        self.excel_handler.mark_deleted(idx)
                        self.processed_count += 1
                        s = self._ui.stats
                        s["skipped"] += 1
                        self._ui.update(advance=1, action="🗑 已删除，跳过本篇", **s)
                        continue

                    self._process_single_paper(idx, paper, total, status_cb, is_retry=True)
                    random_delay(status_cb=status_cb)
                    if self.excel_handler.should_save():
                        self._flush_results()

        except CaptchaDetectedError:
            self._ui.alert_captcha()
            saved = wait_for_captcha_manual_solve(self.driver, status_cb=status_cb)
            if saved:
                self._ui.alert_normal()
                self._ui.update(action="✅ CAPTCHA 已解决，继续...")
            else:
                logger.error("CAPTCHA 解决超时，保存进度后退出")
                self._emergency_save()
                self._close_ui()
                sys.exit(1)

        except CircuitBreakerError as e:
            logger.critical("熔断触发: %s", e.reason)
            self._emergency_save()
            self._close_ui()
            sys.exit(1)

        except KeyboardInterrupt:
            logger.warning("用户中断，正在保存进度...")
            self._emergency_save()
            self._close_ui()
            sys.exit(0)

        except Exception as e:
            logger.error("未预期异常: %s", e, exc_info=True)
            self._emergency_save()
            self._close_ui()
            raise

        finally:
            self._close_ui()

        self._flush_results()
        self.excel_handler.save_checkpoint(self.results)
        self.excel_handler.clear_checkpoint()
        print_report(self.results)

        return self.results

    # ─── 浏览器初始化 ──────────────────────────────────

    def _init_browser(self) -> None:
        """初始化反检测浏览器实例（带网络重试）"""
        self.driver = get_stealth_driver(browser=self.browser)
        randomize_viewport(self.driver)

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                logger.info("正在访问 Google Scholar (尝试 %d/%d)...", attempt, max_retries)
                self.driver.get("https://scholar.google.com")
                break
            except Exception as e:
                if "ERR_CONNECTION" in str(e) or "ERR_NAME_NOT_RESOLVED" in str(e):
                    logger.warning("  网络连接失败 (尝试 %d/%d): %s", attempt, max_retries, e)
                    if attempt < max_retries:
                        time.sleep(random.uniform(5, 10))
                        continue
                    raise
                raise

        time.sleep(random.uniform(2, 4))
        handle_gs_cookies(self.driver)

        if load_cookies(self.driver):
            logger.info("已加载历史 cookies，刷新页面...")
            self.driver.get("https://scholar.google.com")
            time.sleep(random.uniform(2, 3))
            handle_gs_cookies(self.driver)

        logger.info("浏览器初始化完成")

    # ─── 单条处理 ──────────────────────────────────────

    def _process_single_paper(self, idx: int, paper: pd.Series, total: int,
                              status_cb=None, is_retry: bool = False) -> None:
        """处理单篇论文的引文检索"""
        short_name = str(paper.get(self.excel_handler.col_title, "")).strip()
        author = self.excel_handler.extract_author_from_citation(paper)
        full_title = self.excel_handler.extract_title_from_citation(paper)

        if not short_name:
            logger.warning("跳过空标题行 (row=%d)", idx)
            return

        self.processed_count += 1
        prefix = "⟳ " if is_retry else ""
        logger.info("%s[%d/%d] %s", prefix, self.processed_count, total, short_name)

        result = {
            "row_index": idx, "title": short_name, "author": author,
            "citations": None, "success": False, "similarity_score": 0.0,
            "returned_title": None, "error": None,
        }

        search_query = str(paper.get(self.excel_handler.col_citation_fmt, "")).strip()
        if not search_query or pd.isna(paper.get(self.excel_handler.col_citation_fmt)):
            search_query = full_title if full_title and len(full_title) > len(short_name) else short_name
            if author:
                search_query = f"{search_query} {author}"

        gs_result = self._search_google_scholar(search_query, status_cb)

        if gs_result and gs_result.get("success"):
            result.update(gs_result)
            status = self._determine_status(gs_result)
            self.excel_handler.update_citation(
                idx, gs_result.get("citations", DEFAULT_CITATION_NOT_FOUND), status,
            )
            logger.info("  ✓ 引文: %s 次 (相似度: %.1f%%)",
                        gs_result["citations"], gs_result.get("similarity_score", 0))

            s = self._ui.stats
            s["success"] += 1
            if status == "review":
                s["review"] += 1
            self._ui.update(advance=1, action=f"✅ 完成: {gs_result['citations']} 次引文", **s)

        elif gs_result and gs_result.get("error") == "rate_limit_skip":
            logger.warning("  ⏭ 跳过 (429 限流)，稍后重试")
            self._rate_limited_queue.append((idx, paper))
            s = self._ui.stats
            s["skipped"] += 1
            self._ui.update(advance=1, action="⏭ 跳过 (429 限流)", **s)
            return
        else:
            error_msg = gs_result.get("error", "搜索无结果") if gs_result else "搜索失败"
            result["error"] = error_msg
            result["citations"] = DEFAULT_CITATION_ERROR
            self.excel_handler.update_citation(idx, DEFAULT_CITATION_ERROR, "error")
            logger.error("  ✗ 失败: %s", error_msg)
            s = self._ui.stats
            s["failed"] += 1
            self._ui.update(advance=1, action=f"❌ 失败: {error_msg}", **s)

        self.results.append(result)

    def _determine_status(self, gs_result: dict) -> str:
        score = gs_result.get("similarity_score", 100)
        if score < 70:
            return "mismatch"
        elif score < 90:
            return "review"
        return "done"

    # ─── Google Scholar 搜索核心 ────────────────────────

    def _search_google_scholar(self, query: str, status_cb=None) -> Optional[dict]:
        """在 Google Scholar 中搜索论文并提取引文数。"""
        result = {
            "success": False, "citations": None, "title_matched": False,
            "similarity_score": 0.0, "returned_title": None, "error": None,
        }
        _429_attempts = 0

        for attempt in range(1, 999):
            try:
                if attempt > 1:
                    delay = random.uniform(3, 5)
                    logger.info("  第 %d 次重试 (等待 %.1fs)...", attempt, delay)
                    time.sleep(delay)

                success = navigate_gs_search(
                    self.driver, query, timeout=GS_TIMEOUT, status_cb=status_cb,
                )
                if not success:
                    result["error"] = f"搜索页面加载失败 (尝试 {attempt})"
                    continue

                try:
                    check_blocked(self.driver)
                except CaptchaDetectedError:
                    self._ui.alert_captcha()
                    solved = wait_for_captcha_manual_solve(self.driver, status_cb=status_cb)
                    if solved:
                        self._ui.alert_normal()
                        continue
                    raise

                if status_cb:
                    status_cb("extracting", "📊 正在提取引文数据...")
                human_like_scroll(self.driver)

                returned_title = extract_paper_title(self.driver.page_source)
                citations = extract_citations_from_gs(self.driver)

                if returned_title:
                    result["returned_title"] = returned_title
                    is_match, score = check_title_match(
                        query, returned_title, SIMILARITY_THRESHOLD,
                    )
                    result["title_matched"] = is_match
                    result["similarity_score"] = score

                    if is_match and citations is not None:
                        result["success"] = True
                        result["citations"] = citations
                        return result
                    elif is_match and citations is None:
                        result["success"] = True
                        result["citations"] = DEFAULT_CITATION_NOT_FOUND
                        logger.info("  ∼ 标题匹配但未找到引文，写为 0")
                        return result
                    else:
                        logger.warning("  标题不匹配 (%.1f%%): 搜索=%s, 返回=%s",
                                       score, query[:40], returned_title[:40])
                        result["error"] = f"标题不匹配 ({score:.0f}%)"
                        continue
                else:
                    if citations is not None:
                        result["success"] = True
                        result["citations"] = citations
                        result["title_matched"] = True
                        result["similarity_score"] = 100.0
                        logger.info("  ∼ 无法提取结果标题，直接使用引文数: %s", citations)
                        return result
                    else:
                        result["error"] = "搜索结果无标题无引文"
                        continue

            except CaptchaDetectedError:
                raise
            except CircuitBreakerError as e:
                if "429" in str(e) or "限流" in str(e):
                    _429_attempts += 1
                    if _429_attempts >= MAX_429_RETRIES:
                        logger.warning("  429 限流已达 %d 次上限，跳过本篇", MAX_429_RETRIES)
                        result["error"] = "rate_limit_skip"
                        return result
                    backoff = min(60, 2 ** (_429_attempts - 1))
                    jitter = random.uniform(0, 3)
                    wait = backoff + jitter
                    if status_cb:
                        status_cb("rate_limited",
                                  f"⏳ 429 限流 (第 {_429_attempts}/{MAX_429_RETRIES} 次)，等待 {wait:.0f}s...")
                    logger.warning("  429 限流 (第 %d/%d 次)，等待 %.1fs 后重试...",
                                   _429_attempts, MAX_429_RETRIES, wait)
                    time.sleep(wait)
                    try:
                        self.driver.get("https://scholar.google.com")
                        time.sleep(random.uniform(3, 5))
                    except Exception:
                        pass
                    continue
                raise
            except Exception as e:
                logger.warning("  搜索异常 (尝试 %d): %s", attempt, e)
                result["error"] = str(e)
                continue

        return result

    # ─── 辅助方法 ──────────────────────────────────────

    def _load_pending(self) -> pd.DataFrame:
        pending = self.excel_handler.load_pending_papers()
        processed_indices = self.excel_handler.get_processed_indices()
        if processed_indices:
            before = len(pending)
            pending = pending[~pending.index.isin(processed_indices)]
            skipped = before - len(pending)
            if skipped:
                logger.info("跳过 %d 条已处理论文（断点续传）", skipped)
        return pending

    def _flush_results(self) -> None:
        self.excel_handler.save_checkpoint(self.results)
        if hasattr(self.excel_handler, '_update_count') and self.excel_handler._update_count > 0:
            logger.info("增量保存完成 (%d 条)", self.excel_handler._update_count)
            self.excel_handler.reset_save_counter()
        if self.driver:
            save_cookies(self.driver)

    def _emergency_save(self) -> None:
        try:
            self.excel_handler.save_checkpoint(self.results)
            self.excel_handler.force_save_all(self.results)
            if self.driver:
                save_cookies(self.driver)
            logger.info("紧急保存完成 (%d 条)", len(self.results))
        except Exception as e:
            logger.error("紧急保存失败: %s", e)

    def _close_ui(self) -> None:
        if self._ui:
            try:
                self._ui.close()
            except Exception:
                pass
            self._ui = None

    def _cleanup(self) -> None:
        self._close_ui()
        if self.driver:
            try:
                save_cookies(self.driver)
                self.driver.quit()
                logger.info("浏览器已关闭")
            except Exception:
                pass


def check_excel_not_open(path: str) -> bool:
    try:
        with open(path, "r+b"):
            pass
        return True
    except PermissionError:
        return False
    except Exception:
        return True


def select_excel_file() -> str:
    """交互式选择 Excel 文件"""
    xlsx_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "xlsx")
    files = sorted([f for f in os.listdir(xlsx_dir) if f.endswith(".xlsx") and not f.startswith("~$")])

    if not files:
        logger.error("xlsx/ 目录下没有找到 .xlsx 文件")
        sys.exit(1)

    print("\n📂 可用的 Excel 文件:")
    for i, f in enumerate(files, 1):
        path = os.path.join(xlsx_dir, f)
        size = os.path.getsize(path)
        print(f"  [{i}] {f}  ({size // 1024}KB)")
    print()

    choice = Prompt.ask(
        "请选择文件编号，或直接输入完整路径",
        default="1",
    )

    # 判断是编号还是路径
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(files):
            return os.path.join(xlsx_dir, files[idx])
        print(f"⚠️ 编号 {choice} 无效，使用默认")
        return os.path.join(xlsx_dir, files[0])

    # 直接输入的路径
    if os.path.exists(choice):
        return choice
    print(f"⚠️ 路径不存在: {choice}，使用默认")
    return os.path.join(xlsx_dir, files[0])


def main():
    parser = argparse.ArgumentParser(
        description="学术引文自动化采集智能体 (Google Scholar 直搜版)",
    )
    parser.add_argument("--excel", type=str, default=None, help="论文 Excel 文件路径（留空则交互选择）")
    parser.add_argument("--chrome", action="store_true", help="使用 Chrome 浏览器（默认 Edge）")
    parser.add_argument("--reset", action="store_true", help="忽略已有检查点，重新处理所有论文")
    args = parser.parse_args()

    if args.excel is None:
        args.excel = select_excel_file()
    else:
        logger.info("使用指定文件: %s", args.excel)

    if args.reset:
        from config import PROGRESS_PATH
        if os.path.exists(PROGRESS_PATH):
            os.remove(PROGRESS_PATH)
            logger.info("已清除检查点，将重新处理所有论文")

    logger.info("Excel 路径: %s", os.path.abspath(args.excel))
    if not check_excel_not_open(args.excel):
        logger.critical("Excel 文件被其他程序打开了！请关闭后再运行。")
        sys.exit(1)

    browser = "chrome" if args.chrome else "edge"
    agent = CitationAgent(excel_path=args.excel, browser=browser)
    agent.run()


if __name__ == "__main__":
    main()
