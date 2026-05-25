"""多表合并模块 — 扫描 source_input、合并去重、输出 raw_manifest.xlsx"""

import os
import re
import logging
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("merge_engine")

BASE = Path(__file__).resolve().parent.parent
XLSX_SOURCE = BASE / "xlsx" / "source_input"
OUTPUT_PATH = BASE / "xlsx" / "raw_manifest.xlsx"

TITLE_COL = "算法/论文简称"
CITE_COL = "引用次数"
FMT_COL = "完整学术引用格式"

DEDUP_THRESHOLD = 88


def normalize_title(title: str) -> str:
    if not isinstance(title, str):
        return ""
    t = title.lower().strip()
    t = re.sub(r'\s+', ' ', t)
    t = re.sub(r'[^\w一-鿿\s]', '', t)
    return t.strip()


def titles_are_duplicate(t1: str, t2: str) -> bool:
    n1, n2 = normalize_title(t1), normalize_title(t2)
    if not n1 or not n2:
        return False
    if n1 == n2:
        return True
    return fuzz.token_sort_ratio(n1, n2) >= DEDUP_THRESHOLD


def scan_source_files() -> list[Path]:
    files = sorted(XLSX_SOURCE.glob("*.xlsx"))
    return [f for f in files if not f.name.startswith("~$")]


def merge_all() -> pd.DataFrame:
    files = scan_source_files()
    if not files:
        logger.error("xlsx/source_input/ 下没有 .xlsx 文件")
        return pd.DataFrame()

    console = Console()
    table = Table(title="📊 多表合并治理看板", box=box.ROUNDED, title_style="bold")
    table.add_column("文件名", style="cyan", no_wrap=True)
    table.add_column("行数", justify="right", style="yellow")
    table.add_column("状态", style="green")

    all_dfs, total = [], 0
    for fp in files:
        try:
            df = pd.read_excel(fp, engine="openpyxl")
            r = len(df)
            total += r
            all_dfs.append(df)
            table.add_row(fp.name, str(r), "✅")
        except Exception as e:
            table.add_row(fp.name, "✗", f"[red]{e}[/red]")

    console.print(table)
    if not all_dfs:
        return pd.DataFrame()
    merged = pd.concat(all_dfs, ignore_index=True)
    console.print(f"\n📈 共 [cyan]{len(files)}[/cyan] 个文件，合并 [yellow]{total}[/yellow] 行\n")
    return merged


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    if TITLE_COL not in df.columns:
        logger.warning("缺少标题列 %s，跳过", TITLE_COL)
        return df

    df = df.dropna(subset=[TITLE_COL]).copy()
    df["_norm"] = df[TITLE_COL].apply(normalize_title)
    df = df[df["_norm"] != ""]
    if df.empty:
        return df

    def _pick_best(group):
        if CITE_COL in group.columns:
            vals = pd.to_numeric(group[CITE_COL], errors="coerce")
            if vals.isna().all():
                return group.iloc[0]
            return group.loc[vals.idxmax()]
        return group.iloc[0]

    exact = df.groupby("_norm", sort=False).apply(_pick_best, include_groups=False)
    exact = exact.reset_index(drop=True)

    titles = exact[TITLE_COL].tolist()
    drop = set()
    for i in range(len(titles)):
        if i in drop:
            continue
        for j in range(i + 1, len(titles)):
            if j in drop:
                continue
            if titles_are_duplicate(titles[i], titles[j]):
                if CITE_COL in exact.columns:
                    ci = pd.to_numeric(exact.iloc[i][CITE_COL], errors="coerce") or 0
                    cj = pd.to_numeric(exact.iloc[j][CITE_COL], errors="coerce") or 0
                    drop.add(i) if cj > ci else drop.add(j)
                else:
                    drop.add(j)

    result = exact.drop(index=list(drop)).reset_index(drop=True)
    if "_norm" in result.columns:
        result = result.drop(columns=["_norm"])
    return result


def print_report(original: int, final: int) -> None:
    removed = original - final
    pct = removed / original * 100 if original else 0
    console = Console()
    console.print(Panel(
        "[bold]📋 去重简报[/bold]\n\n"
        f"原始行数: [yellow]{original}[/yellow]\n"
        f"去重移除: [red]{removed}[/red] ({pct:.1f}%)\n"
        f"最终净值: [green]{final}[/green]",
        box=box.ROUNDED,
    ))


def run() -> pd.DataFrame:
    console = Console()
    console.print("\n[bold]🚀 多表合并引擎启动[/bold]\n")

    merged = merge_all()
    if merged.empty:
        return merged

    before = len(merged)
    cleaned = deduplicate(merged)
    after = len(cleaned)
    print_report(before, after)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    cleaned.to_excel(OUTPUT_PATH, index=False, engine="openpyxl")
    logger.info("已输出: %s (%d 行)", OUTPUT_PATH, after)

    console.print(f"\n✅ 合并完成 → [bold]{OUTPUT_PATH}[/bold]\n")
    return cleaned


if __name__ == "__main__":
    run()
