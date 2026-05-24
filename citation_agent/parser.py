"""智能解析器 — 从 API 返回的 HTML/JSON 中提取引文数量"""
import re
import json
import logging
from typing import Optional, Union

logger = logging.getLogger(__name__)


def parse_json_for_citations(data: Union[dict, str]) -> Optional[int]:
    """
    从 JSON 响应中提取被引次数。

    支持的字段路径（按优先级）：
    1. data['citationCount']
    2. data['cited_by_count']
    3. data['metrics']['citationCount']
    4. data['inline_links']['cited_by']['total']
    5. data['citations'][0]['count']
    6. data['paper']['citationCount']
    7. 正则回退：在 JSON 字符串中搜索 "cited_by_count": <数字>

    Args:
        data: API 返回的 JSON 字典或 JSON 字符串

    Returns:
        引文数量（int），或 None 如果无法提取
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return _fallback_json_regex(data)

    if not isinstance(data, dict):
        return None

    # 按优先级尝试提取路径
    extraction_paths = [
        lambda d: d.get("citationCount"),
        lambda d: d.get("cited_by_count"),
        lambda d: d.get("metrics", {}).get("citationCount"),
        lambda d: d.get("inline_links", {}).get("cited_by", {}).get("total"),
        lambda d: _extract_from_citations_list(d),
        lambda d: d.get("paper", {}).get("citationCount"),
        lambda d: d.get("paper", {}).get("cited_by_count"),
        lambda d: d.get("result", {}).get("citationCount"),
    ]

    for extract in extraction_paths:
        try:
            value = extract(data)
            if value is not None:
                parsed = int(value)
                logger.debug(f"JSON 解析成功: cited_by_count={parsed}")
                return parsed
        except (ValueError, TypeError, AttributeError):
            continue

    logger.debug("JSON 中未找到引文字段")
    return None


def _extract_from_citations_list(data: dict) -> Optional[int]:
    """尝试从 citations 列表中提取计数"""
    citations = data.get("citations")
    if isinstance(citations, list) and len(citations) > 0:
        first = citations[0]
        if isinstance(first, dict):
            return first.get("count")
    return None


def _fallback_json_regex(text: str) -> Optional[int]:
    """正则回退：当 JSON 解析失败时尝试在文本中搜索引文字段"""
    patterns = [
        r'"cited_by_count"\s*:\s*(\d+)',
        r'"citationCount"\s*:\s*(\d+)',
        r'"total"\s*:\s*(\d+)',
        r'被引用次数[：:]\s*(\d+)',
    ]
    for pat in patterns:
        match = re.search(pat, text)
        if match:
            return int(match.group(1))
    return None


def parse_html_for_citations(html: str) -> Optional[int]:
    """
    从 HTML 页面中提取被引次数，按优先级多重兜底。

    优先级顺序：
    1. Google Scholar <a> 标签 "被引用次数"
    2. Google Scholar <a> 标签 "Cited by"
    3. Google Scholar 纯文本 "Cited by"
    4. Semantic Scholar JSON 格式
    5. 通用元标签
    6. 通用 "<N> citations" 模式
    7. 中文 "N 被引" 模式

    Args:
        html: 页面 HTML 源码

    Returns:
        引文数量（int），或 None 如果无法提取
    """
    if not html:
        return None

    # 策略 1: Google Scholar <a> 标签 "被引用次数"
    m = re.search(r'被引用次数[：:]\s*(\d[\d,]*)\s*</a>', html)
    if m:
        return int(m.group(1).replace(",", ""))

    # 策略 2: Google Scholar <a> 标签 "Cited by"（最高优先级）
    m = re.search(r'<a[^>]*>Cited by\s*(\d[\d,]*)\s*</a>', html, re.IGNORECASE)
    if m:
        return int(m.group(1).replace(",", ""))

    # 策略 3: Google Scholar 纯文本 "Cited by"
    m = re.search(r'Cited by\s*(\d[\d,]*)', html, re.IGNORECASE)
    if m:
        return int(m.group(1).replace(",", ""))

    # 策略 4: Semantic Scholar 格式
    m = re.search(r'citationCount["\']?\s*[:=]\s*["\']?(\d+)', html)
    if m:
        return int(m.group(1))

    # 策略 5: 通用元标签
    m = re.search(
        r'<meta\s+name=["\']citation_[Cc]itation[Cc]ount["\']\s+content=["\'](\d+)["\']',
        html,
    )
    if m:
        return int(m.group(1))

    # 策略 6: 通用模式 "N citations"
    m = re.search(r'(\d[\d,]*)\s*citations?', html, re.IGNORECASE)
    if m:
        return int(m.group(1).replace(",", ""))

    # 策略 7: 中文 "N 被引"
    m = re.search(r'(\d[\d,]*)\s*被引', html)
    if m:
        return int(m.group(1).replace(",", ""))

    logger.debug("HTML 中未找到引文字段")
    return None


def extract_paper_title(html_or_json: Union[str, dict]) -> Optional[str]:
    """
    从搜索结果中提取第一条论文标题，用于相似度校验。
    """
    # JSON 模式
    if isinstance(html_or_json, dict):
        paths = [
            lambda d: d.get("title"),
            lambda d: d.get("paper", {}).get("title"),
            lambda d: d.get("result", {}).get("title"),
            lambda d: d.get("data", {}),
        ]
        for extract in paths:
            try:
                title = extract(html_or_json)
                if title and isinstance(title, str):
                    return title.strip()
            except (TypeError, AttributeError):
                continue

    # HTML 模式
    if isinstance(html_or_json, str):
        # <title> 标签
        title_match = re.search(r'<title>(.+?)</title>', html_or_json, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
            # 移除站点名后缀如 " - Google Scholar"
            title = re.sub(r'\s*[-–|]\s*(Google Scholar|Semantic Scholar|arXiv|PubMed).*', '', title)
            return title

        # 论文页面的 <meta> 标签
        meta_match = re.search(
            r'<meta\s+name=["\']citation_title["\']\s+content=["\'](.+?)["\']',
            html_or_json,
        )
        if meta_match:
            return meta_match.group(1).strip()

    return None
