import os
import glob
import pandas as pd
from tqdm import tqdm
import baostock as bs

# =========================
# 配置区（跟你第一次脚本保持一致）
# =========================
BASE_DIR = "股价数据_parquet_fq"
DATA_DIR = os.path.join(BASE_DIR, "kline_fq")  # 第一次脚本的日线前复权 parquet 目录

OUT_CSV = "2023涨停.csv"
START_DATE = "2023-01-01"
END_DATE = "2023-12-31"

LIMITUP_PCT = 9.9   # pctChg >= 9.9 视为涨停


def load_code_name_map() -> dict:
    """用 baostock 拉 code -> name 映射（code 形如 sh.600000）"""
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")

    try:
        rs = bs.query_stock_basic()
        if rs.error_code != "0":
            raise RuntimeError(f"query_stock_basic failed: {rs.error_msg}")
        df = rs.get_data()
        if df is None or df.empty:
            return {}
        # 常见字段：code, code_name
        if "code" not in df.columns:
            return {}
        name_col = "code_name" if "code_name" in df.columns else None
        if not name_col:
            return {}
        return dict(zip(df["code"].astype(str), df[name_col].astype(str)))
    finally:
        bs.logout()


def bs_code_to_6(code: str) -> str:
    """sh.600000 -> 600000"""
    if not isinstance(code, str):
        return ""
    if "." in code:
        return code.split(".")[-1].zfill(6)
    return code.zfill(6)


def main():
    if not os.path.exists(DATA_DIR):
        raise RuntimeError(f"找不到数据目录：{DATA_DIR}，请确认已运行过第一段脚本并生成 parquet。")

    # 1) 拉取股票名称映射（可选但推荐）
    code2name = load_code_name_map()

    # 2) 遍历所有 parquet
    paths = sorted(glob.glob(os.path.join(DATA_DIR, "*.parquet")))
    if not paths:
        raise RuntimeError(f"{DATA_DIR} 下没有找到任何 parquet 文件。")

    start_dt = pd.to_datetime(START_DATE)
    end_dt = pd.to_datetime(END_DATE)

    out_rows = []
    errors = []

    for p in tqdm(paths, desc="Scanning parquet for 2023 limit-up"):
        try:
            df = pd.read_parquet(p)

            if df is None or df.empty:
                continue
            if "date" not in df.columns or "pctChg" not in df.columns:
                continue

            # 统一类型
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df["pctChg"] = pd.to_numeric(df["pctChg"], errors="coerce")

            df = df.dropna(subset=["date", "pctChg"])
            df = df[(df["date"] >= start_dt) & (df["date"] <= end_dt)]
            if df.empty:
                continue

            df = df[df["pctChg"] >= LIMITUP_PCT]
            if df.empty:
                continue

            # code：优先用列里的（一般整列同一个），否则从文件名推断
            if "code" in df.columns and df["code"].notna().any():
                bs_code = str(df["code"].dropna().iloc[0])
            else:
                bs_code = os.path.splitext(os.path.basename(p))[0]  # {code}.parquet

            name = code2name.get(bs_code, "")

            # 生成输出行
            tmp = pd.DataFrame({
                "date": df["date"].dt.strftime("%Y-%m-%d"),
                "code": [bs_code_to_6(bs_code)] * len(df),
                "name": [name] * len(df),
            })

            # 同日同代码去重
            tmp = tmp.drop_duplicates(subset=["date", "code"], keep="first")
            out_rows.append(tmp)

        except Exception as e:
            errors.append((os.path.basename(p), str(e)))

    if not out_rows:
        print("没有筛到任何 2023 涨停记录（按 pctChg>=9.9）。")
        return

    result = pd.concat(out_rows, ignore_index=True)
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result = result.dropna(subset=["date", "code"])
    result = result.sort_values(["date", "code"]).drop_duplicates(subset=["date", "code"], keep="first")
    result["date"] = result["date"].dt.strftime("%Y-%m-%d")

    result.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"完成：写出 {len(result)} 条到 {OUT_CSV}")

    if errors:
        print("\n--- 读取失败（最多显示30个） ---")
        for fn, msg in errors[:30]:
            print(fn, "=>", msg)


if __name__ == "__main__":
    main()
