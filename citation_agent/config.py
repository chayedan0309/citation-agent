"""全局配置 — 所有可调参数集中管理"""
import os

# ─── 路径配置 ───────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_PATH = os.path.join(os.path.dirname(BASE_DIR), "xlsx", "Deep Supervised Point Cloud Registration Literature (2021-2024).xlsx")
PROGRESS_PATH = os.path.join(BASE_DIR, "progress.json")
COOKIE_PATH = os.path.join(BASE_DIR, "gs_cookies.pkl")

# ─── Excel 列名映射 ────────────────────────────────────
COLUMN_TITLE = "算法/论文简称"        # 论文短名称列
COLUMN_CITATIONS = "引用次数"         # 引用次数列（待填写）
COLUMN_CITATION_FMT = "完整学术引用格式"  # 完整引用格式列

# ─── 相似度校验 ────────────────────────────────────────
SIMILARITY_THRESHOLD = 0.2        # 标题匹配通过阈值（降低到 20% 以适应整段引用搜索）
SIMILARITY_REVIEW_LOW = 0.7       # 待复核区间下限
SIMILARITY_REVIEW_HIGH = 0.9      # 待复核区间上限

# ─── Google Scholar 搜索设置 ────────────────────────────
GS_BASE_URL = "https://scholar.google.com"
GS_SEARCH_URL = "https://scholar.google.com/scholar?hl=en&q={query}"
GS_TIMEOUT = 30                   # 单次搜索超时（秒）
GS_MAX_RETRIES = 3                # 失败重试次数

# ─── 行为仿真 ──────────────────────────────────────────
DELAY_MIN = 2                     # 最小延迟（秒）
DELAY_MAX = 8                     # 最大延迟（秒）
BATCH_SAVE_INTERVAL = 3           # 每处理 N 条增量保存一次
LONG_PAUSE_EVERY = 5              # 每爬取 N 条长暂停一次
LONG_PAUSE_MIN = 20               # 长暂停最短（秒）
LONG_PAUSE_MAX = 30               # 长暂停最长（秒）

# ─── 浏览器窗口 ────────────────────────────────────────
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800
WINDOW_WIDTH_VARY = 30            # 宽度随机偏移量
WINDOW_HEIGHT_VARY = 30           # 高度随机偏移量

# ─── 反检测 ────────────────────────────────────────────
BLOCK_KEYWORDS = [
    "captcha", "验证码", "please verify", "unusual traffic",
    "sorry, you have been blocked", "403 forbidden", "access denied",
    "please show you're not a robot", "please confirm you are a human",
    "我们的系统检测到", "detected unusual traffic",
    "automated queries", "too many requests",
]

# ─── 引文默认值 ────────────────────────────────────────
DEFAULT_CITATION_NOT_FOUND = 0    # 未找到时的默认值
DEFAULT_CITATION_ERROR = -1       # 出错时的标记值
