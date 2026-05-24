"""执行报告生成器 — 输出采集结果汇总与待复核清单"""
import logging

import pandas as pd

from config import SIMILARITY_REVIEW_LOW, SIMILARITY_REVIEW_HIGH
from similarity import classify_match_score

logger = logging.getLogger(__name__)


def generate_report(results: list[dict]) -> str:
    """
    生成格式化的执行报告。

    Args:
        results: 每条论文的处理结果列表，每项包含：
            - title, author, citations, success, similarity_score, error

    Returns:
        格式化报告字符串
    """
    total = len(results)
    success = [r for r in results if r.get("success") and r.get("citations") is not None]
    failed = [r for r in results if not r.get("success")]
    need_review = [
        r for r in results
        if r.get("similarity_score", 0) >= SIMILARITY_REVIEW_LOW * 100
        and r.get("similarity_score", 0) < SIMILARITY_REVIEW_HIGH * 100
        and r.get("success")
    ]

    lines = []
    lines.append("=" * 60)
    lines.append("  学术引文采集 — 执行报告")
    lines.append("=" * 60)
    lines.append(f"总处理: {total} 篇")
    lines.append(f"成功:   {len(success)} 篇")
    lines.append(f"失败:   {len(failed)} 篇")
    lines.append(f"待复核: {len(need_review)} 篇")
    lines.append("")

    # 成功明细
    if success:
        lines.append("── 成功采集 ──")
        for r in success:
            score_str = f" ({r.get('similarity_score', 0):.0f}%)" if r.get("similarity_score") else ""
            lines.append(
                f"  ✓ [{r.get('citations', '?')}次] {r.get('title', '')[:50]}"
                f"{score_str}"
            )
        lines.append("")

    # 失败明细
    if failed:
        lines.append("── 失败列表 ──")
        for r in failed:
            err = r.get("error", "未知错误")
            lines.append(f"  ✗ {r.get('title', '')[:50]} → {err}")
        lines.append("")

    # 待复核明细
    if need_review:
        lines.append("── 待人工复核（相似度 0.7-0.9）──")
        for r in need_review:
            lines.append(
                f"  ? {r.get('title', '')[:50]} "
                f"(相似度: {r.get('similarity_score', 0):.1f}%)"
            )
        lines.append("")
        lines.append("提示: 这些论文的标题匹配度在阈值附近，建议人工确认。")
        lines.append("")

    # 统计摘要
    total_citations = sum(r.get("citations") or 0 for r in success)
    lines.append(f"总引文数: {total_citations}")
    lines.append("=" * 60)

    return "\n".join(lines)


def print_report(results: list[dict]) -> None:
    """打印报告到控制台"""
    report = generate_report(results)
    print("\n" + report)


def export_report_to_csv(results: list[dict], output_path: str) -> None:
    """将结果导出为 CSV 文件"""
    rows = []
    for r in results:
        rows.append({
            "Title": r.get("title", ""),
            "Authors": r.get("author", ""),
            "Citations": r.get("citations", ""),
            "Success": r.get("success", False),
            "Similarity": f'{r.get("similarity_score", 0):.1f}%',
            "Error": r.get("error", ""),
        })

    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    logger.info("报告已导出: %s", output_path)
