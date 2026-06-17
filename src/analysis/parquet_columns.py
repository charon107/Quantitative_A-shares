# -*- coding: utf-8 -*-
import os
import pandas as pd
import pyarrow.dataset as ds

PROFIT_DIR = r"parquet_A股_2007-2024_Q4/profit"

def quick_overview_with_pandas():
    """
    简单方式：pandas 直接读目录（会把目录下所有 parquet 合并为一个 DataFrame）
    适合：数据量不大 / 你想立刻看一下
    """
    df = pd.read_parquet(PROFIT_DIR, engine="pyarrow")
    print("=== pandas.read_parquet 读目录完成 ===")
    print("shape:", df.shape)
    print("columns:", list(df.columns))
    print("\nhead:")
    print(df.head(10))
    print("\ndtypes:")
    print(df.dtypes)
    return df


def arrow_dataset_overview(sample_rows=20):
    """
    推荐方式：pyarrow.dataset（不会一下子把全部数据读进内存）
    """
    dataset = ds.dataset(PROFIT_DIR, format="parquet")
    schema = dataset.schema

    print("=== pyarrow.dataset 概览 ===")
    print("目录:", PROFIT_DIR)
    print("字段数:", len(schema))
    print("字段列表:", [f.name for f in schema])
    print("schema:\n", schema)

    # 抽样读前 sample_rows 行（注意：是扫描顺序的前 N 行，不是随机）
    table = dataset.head(sample_rows)
    df_sample = table.to_pandas()
    print(f"\n=== sample head({sample_rows}) ===")
    print(df_sample.head(sample_rows))
    print("\n=== sample dtypes ===")
    print(df_sample.dtypes)

    return dataset


def query_by_code_and_year(code="sh.600000", year=2020, columns=None, limit=200):
    """
    用 dataset 做筛选：按股票 + 年份（reqYear）
    """
    dataset = ds.dataset(PROFIT_DIR, format="parquet")

    # 默认只取一些常用列，避免一次拉太多
    if columns is None:
        # 这些列你脚本里肯定加了：code/reqYear/reqQuarter
        # 其它列看你 baostock 的 profit 字段有哪些
        columns = ["code", "reqYear", "reqQuarter", "statDate"]

        # 有些文件可能没有 statDate（理论上 profit 有），所以兜底
        cols_available = set([f.name for f in dataset.schema])
        columns = [c for c in columns if c in cols_available]

    filt = (ds.field("code") == code) & (ds.field("reqYear") == year)
    table = dataset.to_table(filter=filt, columns=columns)

    df = table.to_pandas()
    print(f"=== 结果：code={code}, reqYear={year} ===")
    print("shape:", df.shape)

    if len(df) > limit:
        print(df.head(limit))
        print(f"...（只展示前 {limit} 行，共 {len(df)} 行）")
    else:
        print(df)

    return df


def list_some_files(n=10):
    """
    看看 profit 目录下分片文件长什么样
    """
    files = [f for f in os.listdir(PROFIT_DIR) if f.endswith(".parquet")]
    files.sort()
    print(f"profit 分片文件数: {len(files)}")
    print("前几个文件：")
    for f in files[:n]:
        print("  ", f)


def export_to_single_parquet(out_path=r"parquet_A股_2007-2024_Q4/profit_all.parquet"):
    """
    如果你想把 profit 目录合并成一个单文件 parquet（可能很大）
    """
    df = pd.read_parquet(PROFIT_DIR, engine="pyarrow")
    df.to_parquet(out_path, index=False, engine="pyarrow", compression="zstd")
    print("已导出单文件：", out_path)


if __name__ == "__main__":
    # 1) 先看目录里有多少分片
    list_some_files(n=10)

    # 2) 推荐：dataset 概览 + 抽样查看
    arrow_dataset_overview(sample_rows=30)

    # 3) 如果你就想直接看全部（可能很大，慎用）
    # df_all = quick_overview_with_pandas()

    # 4) 示例：按股票+年份筛选（把 code 换成你关心的）
    # query_by_code_and_year(code="sh.600000", year=2020)

    # 5) 如果需要合并成单文件
    # export_to_single_parquet()
