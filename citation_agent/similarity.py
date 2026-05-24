"""语义相似度校验 — 基于 rapidfuzz 的论文标题模糊匹配"""
import logging
from rapidfuzz import fuzz, utils

from config import SIMILARITY_THRESHOLD

logger = logging.getLogger(__name__)


def check_title_match(
    input_title: str,
    returned_title: str,
    threshold: float = None,
) -> tuple[bool, float]:
    """
    对比输入标题与搜索返回标题，判断是否为同一篇论文。

    使用 token 排序比率（token_sort_ratio）提高包含子标题时的匹配精度。
    例如：
      "BERT: Pre-training of Deep Bidirectional Transformers"
      与 "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
      → 仍能获得高匹配分。

    Args:
        input_title: Excel 中的原始论文标题
        returned_title: API 返回的搜索结果标题
        threshold: 匹配通过阈值，默认 config.SIMILARITY_THRESHOLD

    Returns:
        (is_match: bool, score: float)
         - is_match: 是否通过阈值
         - score: 0-100 的相似度分数
    """
    if not input_title or not returned_title:
        logger.warning("标题为空无法比较: input=%r, returned=%r", input_title, returned_title)
        return False, 0.0

    threshold = threshold if threshold is not None else SIMILARITY_THRESHOLD

    # 预处理：统一小写、去除多余空格
    input_clean = utils.default_process(input_title)
    returned_clean = utils.default_process(returned_title)

    if not input_clean or not returned_clean:
        return False, 0.0

    # 主分数：token_sort_ratio（对语序不敏感，适合论文标题）
    score = fuzz.token_sort_ratio(input_clean, returned_clean)

    # 如果主分数接近阈值，用 partial_ratio 辅助判断
    if abs(score - threshold * 100) < 10:
        partial = fuzz.partial_ratio(input_clean, returned_clean)
        score = max(score, partial)

    # 特殊情况：一个标题完全包含另一个
    if input_clean in returned_clean or returned_clean in input_clean:
        score = max(score, 95.0)

    is_match = score >= threshold * 100
    logger.debug(
        "相似度: %.1f%% | 输入: %s… | 返回: %s… | 匹配: %s",
        score,
        input_title[:40],
        returned_title[:40],
        "✓" if is_match else "✗",
    )

    return is_match, score


def classify_match_score(score: float) -> str:
    """
    根据相似度分数分类匹配结果。

    Returns:
        "match"    — 明确匹配 (>= 0.9)
        "review"   — 需要人工复核 (0.7 ~ 0.9)
        "mismatch" — 明确不匹配 (< 0.7)
    """
    if score >= 90.0:
        return "match"
    elif score >= 70.0:
        return "review"
    else:
        return "mismatch"
