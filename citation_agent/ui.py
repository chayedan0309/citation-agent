"""实时可视化 UI — 基于 Rich 的进度条、状态面板、日志窗口"""
import time
import logging
from collections import deque

from rich.console import Console
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text
from rich import box


class UILogHandler(logging.Handler):
    """将标准 logging 输出重定向到 UI 日志区"""

    def __init__(self, ui: "ProgressUI"):
        super().__init__()
        self.ui = ui
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        msg = self.format(record)
        style = ""
        if record.levelname == "WARNING":
            style = "yellow"
        elif record.levelname in ("ERROR", "CRITICAL"):
            style = "red"
        elif "✓" in msg:
            style = "green"
        self.ui.log(msg, style)


class ProgressUI:
    """实时可视化 UI 管理器"""

    def __init__(self, total: int, title: str = "学术引文采集智能体"):
        self.total = total
        self.processed = 0
        self.title = title
        self.current_paper = ""
        self.current_action = "初始化中..."
        self.start_time = time.time()
        self.stats = {"success": 0, "review": 0, "failed": 0, "skipped": 0}
        self.captcha_active = False
        self.log_messages: deque = deque(maxlen=8)
        self.console = Console()

        self.progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("•"),
            TextColumn("{task.completed}/{task.total} 篇"),
            TimeElapsedColumn(),
        )
        self._task = self.progress.add_task("总进度", total=total)

        self.live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=4,
            screen=True,
        )
        self.live.__enter__()

    # ─── 公开方法 ──────────────────────────────────────

    def update(self, paper: str = "", action: str = "", advance: int = 0, **stats) -> None:
        """更新 UI 状态"""
        if paper:
            self.current_paper = paper
        if action:
            self.current_action = action
        if stats:
            self.stats.update(stats)
        if advance:
            self.processed += advance
            self.progress.update(self._task, advance=advance)
        self.live.update(self._render())

    def log(self, message: str, style: str = "") -> None:
        """追加日志"""
        ts = time.strftime("%H:%M:%S")
        self.log_messages.append((ts, message, style))
        self.live.update(self._render())

    def alert_captcha(self) -> None:
        """验证码警告"""
        self.captcha_active = True
        self.current_action = "🚨 发现谷歌验证码！请在浏览器窗口手动验证！智能体已挂起等待..."
        self.live.update(self._render())

    def alert_normal(self) -> None:
        """解除验证码警告"""
        self.captcha_active = False

    def close(self) -> None:
        """关闭 UI，恢复终端"""
        try:
            self.live.__exit__(None, None, None)
        except Exception:
            pass

    # ─── 内部渲染 ──────────────────────────────────────

    def _render(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="log", size=8),
        )
        layout["header"].update(self._render_header())
        layout["body"].update(self._render_body())
        layout["log"].update(self._render_log())
        return layout

    def _render_header(self) -> Panel:
        elapsed = time.strftime("%H:%M:%S", time.gmtime(time.time() - self.start_time))
        caption = f"🏷️  {self.title}  ⏱️  {elapsed}"
        action = self.current_action
        if self.captcha_active:
            action = f"[bold red]{action}[/bold red]"
        header = Text.assemble(
            (" ", ""),
            (f"📄 当前: {self.current_paper[:50]}", "bold cyan" if self.current_paper else "dim"),
            "\n",
            ("⚙️  ", ""),
            (action, ""),
            "\n",
            ("[dim]提示: 按 D 键可标记删除当前论文[/dim]", "dim"),
        )
        return Panel(header, title=caption, box=box.ROUNDED)

    def _render_body(self) -> Panel:
        progress_table = Table.grid(padding=(0, 2))
        progress_table.add_row(self.progress)

        stats_table = Table.grid(padding=(1, 3))
        s = self.stats
        stats_table.add_row(
            f"[green]✅ 成功: {s['success']}[/green]",
            f"[yellow]⚠️ 复核: {s['review']}[/yellow]",
        )
        stats_table.add_row(
            f"[red]❌ 失败: {s['failed']}[/red]",
            f"[blue]⏭ 跳过: {s['skipped']}[/blue]",
        )

        body = Table.grid()
        body.add_row(progress_table)
        body.add_row(stats_table)
        return Panel(body, box=box.SIMPLE)

    def _render_log(self) -> Panel:
        lines = []
        for ts, msg, style in self.log_messages:
            if style == "green":
                lines.append(f"[dim]{ts}[/dim] [green]{msg}[/green]")
            elif style == "yellow":
                lines.append(f"[dim]{ts}[/dim] [yellow]{msg}[/yellow]")
            elif style == "red":
                lines.append(f"[dim]{ts}[/dim] [bold red]{msg}[/bold red]")
            else:
                lines.append(f"[dim]{ts}[/dim] {msg}")
        text = "\n".join(lines) if lines else "[dim]等待日志...</dim>"
        return Panel(text, title="📋 日志", box=box.ROUNDED, height=8)
