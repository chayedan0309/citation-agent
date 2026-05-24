# 学术引文自动化采集智能体

基于 Selenium 的 Google Scholar 引文采集工具，通过模拟真人浏览器操作来获取论文引用次数。

## 环境要求

- **操作系统**: Windows 11
- **Python**: 3.10+
- **浏览器**: Microsoft Edge（默认）或 Google Chrome

## 安装

```bash
# 进入项目目录
cd F:/课题组/lhl/agent

# 安装依赖
pip install -r citation_agent/requirements.txt
```

## 快速使用

```bash
python citation_agent/main.py
```

启动后会让你选择 Excel 文件：

```
📂 可用的 Excel 文件:
  [1] 5.24.xlsx  (45KB)
  [2] Deep Supervised Point Cloud Registration Literature (2021-2024).xlsx  (28KB)

请选择文件编号，或直接输入完整路径 [1]:
```

输入编号回车即可，它会自动打开 Edge 浏览器开始搜索。

## Excel 文件格式

需要包含以下列（列名可配置）：

| 列名 | 是否必填 | 说明 |
|------|---------|------|
| 算法/论文简称 | 是 | 论文短名称，显示用 |
| 完整学术引用格式 | 推荐 | 整段引用文本，用于搜索 |
| 引用次数 | 是 | 程序自动填写结果 |

示例行：

| 年份 | 算法/论文简称 | 引用次数 | 完整学术引用格式 |
|------|-------------|---------|----------------|
| 2021 | SpinNet | *(留空)* | Ao, S., ... (2021). Spinnet: ... |

程序会把查询到的引用次数自动填入"引用次数"列。如果搜索失败则填 `-1`。

## 运行参数

| 参数 | 作用 |
|------|------|
| `--excel "路径"` | 指定 Excel 文件（跳过交互选择） |
| `--chrome` | 使用 Chrome 浏览器（默认 Edge） |
| `--reset` | 忽略已有进度，全部重新处理 |

示例：

```bash
# 用 Chrome 重新跑全部论文
python citation_agent/main.py --chrome --reset

# 指定文件
python citation_agent/main.py --excel "xlsx/论文清单.xlsx"
```

## 运行说明

1. **运行前关掉 Excel 文件** — 程序需要写入，文件被占用会报错退出
2. **浏览器会弹出** — 不是无头模式，你可以看到搜索过程
3. **遇到验证码** — 在浏览器里手动点验证，程序会自动继续
4. **被限流(429)** — 自动等一会儿重试，5 次不行先跳过，搜完再回头重试
5. **随时 Ctrl+C 中断** — 进度会自动保存，下次接着跑

## 运行节奏

```
每篇之间:   随机停顿 2-8 秒
每 5 篇后:  随机停顿 20-30 秒
被限流:     指数退避 1s → 2s → 4s → 8s → 16s，5 次后跳过
```

## 目录结构

```
agent/
├── citation_agent/           # 主要代码
│   ├── main.py               # 入口，主调度器
│   ├── ui.py                 # 实时进度条界面
│   ├── anti_detect.py        # 反检测引擎（指纹隐藏、行为仿真）
│   ├── config.py             # 配置文件
│   ├── excel_handler.py      # Excel 读写
│   ├── parser.py             # 引文解析
│   ├── similarity.py         # 标题相似度匹配
│   ├── report.py             # 报告生成
│   ├── requirements.txt      # Python 依赖
│   ├── progress.json         # 运行进度（自动生成）
│   └── gs_cookies.pkl        # 浏览器 cookies（自动生成）
├── xlsx/                     # Excel 数据文件
│   └── *.xlsx
└── readme.md                 # 本文件
```

## 常见问题

**Q: 报错 `ModuleNotFoundError: No module named 'rich'`**
A: 没装依赖，运行 `pip install -r citation_agent/requirements.txt`

**Q: 报错 `Permission denied: 'xxx.xlsx'`**
A: Excel 文件被打开了，关掉再运行

**Q: 浏览器打开了但没反应**
A: 检查网络能不能访问 `scholar.google.com`

**Q: 一直弹出验证码**
A: 首次运行正常，跑几篇后 cookies 存下来就好了
