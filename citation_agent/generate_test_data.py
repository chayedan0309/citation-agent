"""模拟测试数据 papers.xlsx — 仅在首次运行或需要重置时执行"""
import os
import pandas as pd

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "xlsx")
os.makedirs(OUTPUT_DIR, exist_ok=True)
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "papers.xlsx")

SAMPLE_PAPERS = [
    {
        "Title": "Attention Is All You Need",
        "Authors": "Vaswani et al.",
        "Year": 2017,
        "Journal": "NeurIPS",
        "Citations": None,
    },
    {
        "Title": "Deep Residual Learning for Image Recognition",
        "Authors": "He et al.",
        "Year": 2016,
        "Journal": "CVPR",
        "Citations": None,
    },
    {
        "Title": "BERT: Pre-training of Deep Bidirectional Transformers",
        "Authors": "Devlin et al.",
        "Year": 2019,
        "Journal": "NAACL",
        "Citations": None,
    },
    {
        "Title": "Generative Adversarial Nets",
        "Authors": "Goodfellow et al.",
        "Year": 2014,
        "Journal": "NeurIPS",
        "Citations": None,
    },
    {
        "Title": "Very Deep Convolutional Networks for Large-Scale Image Recognition",
        "Authors": "Simonyan & Zisserman",
        "Year": 2015,
        "Journal": "ICLR",
        "Citations": None,
    },
    {
        "Title": "ImageNet Classification with Deep Convolutional Neural Networks",
        "Authors": "Krizhevsky et al.",
        "Year": 2012,
        "Journal": "NeurIPS",
        "Citations": None,
    },
    {
        "Title": "Playing Atari with Deep Reinforcement Learning",
        "Authors": "Mnih et al.",
        "Year": 2013,
        "Journal": "arXiv",
        "Citations": None,
    },
    {
        "Title": "Batch Normalization: Accelerating Deep Network Training",
        "Authors": "Ioffe & Szegedy",
        "Year": 2015,
        "Journal": "ICML",
        "Citations": None,
    },
    {
        "Title": "Dropout: A Simple Way to Prevent Neural Networks from Overfitting",
        "Authors": "Srivastava et al.",
        "Year": 2014,
        "Journal": "JMLR",
        "Citations": None,
    },
    {
        "Title": "Mask R-CNN",
        "Authors": "He et al.",
        "Year": 2017,
        "Journal": "ICCV",
        "Citations": None,
    },
]

df = pd.DataFrame(SAMPLE_PAPERS)
df.to_excel(OUTPUT_PATH, index=False, engine="openpyxl")
print(f"测试数据已生成: {OUTPUT_PATH}")
print(f"共 {len(df)} 条论文记录")
