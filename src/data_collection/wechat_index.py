import os
import re
import json
import time
import random
import hashlib
import configparser
from datetime import datetime, timedelta, date

import requests
import pandas as pd

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

# -------------------------- 微信接口配置 --------------------------
URL = "https://search.weixin.qq.com/cgi-bin/wxaweb/wxindex"
HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
        "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows "
        "WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541510) XWEB/17071"
    ),
    "Content-Type": "application/json",
    "xweb_xhr": "1",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Referer": "https://servicewechat.com/wxc026e7662ec26a3a/74/page-frame.html",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# -------------------------- 输出与抓取范围 --------------------------
OUTPUT_DIR = "沪深主板微信指数"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 凭证读取优先级：环境变量 > wechat_search_config.ini > 硬编码默认值
def _load_credentials() -> tuple[str, str]:
    openid = os.getenv("WECHAT_OPENID", "").strip()
    search_key = os.getenv("WECHAT_SEARCH_KEY", "").strip()
    if openid and search_key:
        return openid, search_key

    # 优先从 config/ 目录读取（项目根目录下），兼容直接放在脚本旁边的旧位置
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(os.path.dirname(_script_dir))
    ini_path = os.path.join(_project_root, "config", "wechat_search_config.ini")
    if not os.path.exists(ini_path):
        ini_path = os.path.join(_script_dir, "wechat_search_config.ini")
    if os.path.exists(ini_path):
        cfg = configparser.ConfigParser()
        cfg.read(ini_path, encoding="utf-8")
        openid = cfg.get("DEFAULT", "openid", fallback="").strip()
        search_key = cfg.get("DEFAULT", "search_key", fallback="").strip()
        if openid and search_key:
            print(f"[凭证] 从 wechat_search_config.ini 读取")
            return openid, search_key

    raise RuntimeError(
        "未找到有效凭证！请在 wechat_search_config.ini 中填写 openid 和 search_key，"
        "或设置环境变量 WECHAT_OPENID / WECHAT_SEARCH_KEY。\n"
        "凭证获取方式：用 Fiddler 抓取微信指数小程序的请求 Body。"
    )

OPENID, SEARCH_KEY = _load_credentials()

DEFAULT_DAYS = int(os.getenv("WECHAT_DAYS", "365"))

# 今天/昨天口径：微信指数通常是当天更新“昨天”的数据
TODAY = datetime.now().date()
YESTERDAY = TODAY - timedelta(days=1)

# 抓取窗口的 end 也用昨天（更符合你描述的更新规则，避免一直追今天导致“未到最新”）
END_DATE = YESTERDAY
START_DATE = END_DATE - timedelta(days=DEFAULT_DAYS)

# 文件名默认：公司名_代码.parquet（避免重名覆盖）
FILENAME_NAME_ONLY = os.getenv("FILENAME_NAME_ONLY", "0") == "1"

# -------------------------- 限速与重试策略（防封） --------------------------
# 只有“发送请求前”才 sleep
MIN_SLEEP = float(os.getenv("WECHAT_MIN_SLEEP", "0.5"))
MAX_SLEEP = float(os.getenv("WECHAT_MAX_SLEEP", "1.5"))

LONG_BREAK_EVERY = int(os.getenv("WECHAT_LONG_BREAK_EVERY", "100"))
LONG_BREAK_MIN = float(os.getenv("WECHAT_LONG_BREAK_MIN", "10"))
LONG_BREAK_MAX = float(os.getenv("WECHAT_LONG_BREAK_MAX", "30"))

MAX_RETRIES = int(os.getenv("WECHAT_MAX_RETRIES", "6"))
BACKOFF_BASE = float(os.getenv("WECHAT_BACKOFF_BASE", "1.6"))
BACKOFF_CAP = float(os.getenv("WECHAT_BACKOFF_CAP", "120"))

RETRY_STATUS_CODES = {401, 403, 408, 429, 500, 502, 503, 504}


# -------------------------- 工具函数 --------------------------
def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def md5_sign(search_key: str, query_list: list[str]) -> str:
    if not search_key:
        return ""
    raw = search_key + "".join(query_list)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def is_mainboard_a_share(code: str) -> bool:
    # 常见口径：上证主板 600/601/603/605；深证主板 000/001/002
    code = str(code).strip()
    return code.startswith(("600", "601", "603", "605", "000", "001", "002"))


def get_mainboard_company_list_by_akshare() -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_info_a_code_name()

    # 兼容字段名（不同版本可能不一样）
    if "code" not in df.columns:
        for c in df.columns:
            if "代码" in c:
                df = df.rename(columns={c: "code"})
                break
    if "name" not in df.columns:
        for c in df.columns:
            if "名称" in c or "简称" in c:
                df = df.rename(columns={c: "name"})
                break

    df = df[["code", "name"]].copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["name"] = df["name"].astype(str).str.strip()

    df = df[df["code"].apply(is_mainboard_a_share)].reset_index(drop=True)
    df = df[df["name"].astype(bool)].reset_index(drop=True)
    return df


def parquet_path(code: str, name: str) -> str:
    if FILENAME_NAME_ONLY:
        return os.path.join(OUTPUT_DIR, f"{safe_filename(name)}.parquet")
    return os.path.join(OUTPUT_DIR, f"{safe_filename(name)}_{code}.parquet")


def read_last_date_if_exists(path: str) -> date | None:
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_parquet(path)
        if df.empty or "date" not in df.columns:
            return None
        last = pd.to_datetime(df["date"], errors="coerce").max()
        if pd.isna(last):
            return None
        return last.date()
    except Exception:
        # 读不出来当作不存在，让它重抓
        return None


def request_pre_sleep():
    time.sleep(random.uniform(MIN_SLEEP, MAX_SLEEP))


def long_break_if_needed(request_count: int):
    if LONG_BREAK_EVERY > 0 and request_count > 0 and request_count % LONG_BREAK_EVERY == 0:
        s = random.uniform(LONG_BREAK_MIN, LONG_BREAK_MAX)
        print(f"\n[限速] 已发送请求 {request_count} 次，长休息 {s:.1f}s ...\n")
        time.sleep(s)


def build_payload(openid: str, search_key: str, keyword: str, start_ymd: str, end_ymd: str) -> dict:
    return {
        "openid": openid,
        "search_key": search_key,
        "cgi_name": "GetMultiChannel",
        "query": [keyword],
        "start_ymd": start_ymd,
        "end_ymd": end_ymd,
        "is_beta": 1,
    }


def request_with_retry(session: requests.Session, payload: dict, keyword: str, timeout: int = 30) -> tuple[int, dict]:
    headers = HEADERS_BASE.copy()
    sign = md5_sign(payload.get("search_key", ""), [keyword])
    if sign:
        headers["X-Request-Sign"] = sign

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(URL, data=json.dumps(payload), headers=headers, timeout=timeout)

            if resp.status_code in RETRY_STATUS_CODES:
                wait = min(BACKOFF_CAP, (BACKOFF_BASE ** (attempt - 1)) + random.uniform(0, 1.5))
                print(f"[重试] {keyword} 状态码 {resp.status_code}，第 {attempt}/{MAX_RETRIES} 次，退避 {wait:.1f}s")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.status_code, resp.json()

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_err = e
            wait = min(BACKOFF_CAP, (BACKOFF_BASE ** (attempt - 1)) + random.uniform(0, 2.0))
            print(f"[重试] {keyword} 网络异常：{e}，第 {attempt}/{MAX_RETRIES} 次，退避 {wait:.1f}s")
            time.sleep(wait)

        except json.JSONDecodeError as e:
            last_err = e
            wait = min(BACKOFF_CAP, (BACKOFF_BASE ** (attempt - 1)) + random.uniform(0, 2.0))
            print(f"[重试] {keyword} 非JSON响应，第 {attempt}/{MAX_RETRIES} 次，退避 {wait:.1f}s")
            time.sleep(wait)

    raise RuntimeError(f"请求失败（重试已用尽）：{keyword}，最后错误：{last_err}")


def parse_result_to_df(result_list: list[dict], code: str, name: str) -> pd.DataFrame:
    rows = []
    for item in result_list or []:
        ds = item.get("ymd", None)
        if ds is None:
            ds = item.get("date", "")
        ds = str(ds).strip()

        dt = pd.to_datetime(ds, format="%Y%m%d", errors="coerce")
        if pd.isna(dt):
            dt = pd.to_datetime(ds, errors="coerce")

        cs = item.get("channel_score", {}) or {}
        rows.append(
            {
                "date": dt,
                "wechat_index": cs.get("total_score", None),
                "code": code,
                "name": name,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.dropna(subset=["date"]).reset_index(drop=True)
    return df


def save_merge_dedup(path: str, df_new: pd.DataFrame) -> tuple[bool, date | None]:
    if df_new is None or df_new.empty:
        return False, read_last_date_if_exists(path)

    if os.path.exists(path):
        try:
            df_old = pd.read_parquet(path)
            df_all = pd.concat([df_old, df_new], ignore_index=True)
        except Exception:
            df_all = df_new
    else:
        df_all = df_new

    df_all = df_all.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    df_all.to_parquet(path, index=False)

    last_after = read_last_date_if_exists(path)
    return True, last_after


def preview_json(data: dict, limit: int = 600) -> str:
    try:
        s = json.dumps(data, ensure_ascii=False)
        return s[:limit] + ("..." if len(s) > limit else "")
    except Exception:
        return str(data)[:limit]


def log_problem(
    kind: str,
    code: str,
    name: str,
    keyword: str,
    start: date,
    end: date,
    http_status: str,
    last_before: date | None,
    path: str,
    note: str,
    data_obj: dict | None = None,
):
    print("\n" + "=" * 90)
    print(f"[问题] {kind}")
    print(f"公司：{code} {name}")
    print(f"keyword：{keyword}")
    print(f"增量范围：{ymd(start)} ~ {ymd(end)}")
    print(f"HTTP状态码：{http_status}")
    print(f"文件：{path}")
    print(f"落盘前最后日期：{last_before}")
    if note:
        print(f"备注：{note}")
    if data_obj is not None:
        print(f"响应预览：{preview_json(data_obj)}")
    print("=" * 90 + "\n")


# -------------------------- 主程序 --------------------------
def main():
    if not OPENID or not SEARCH_KEY:
        raise RuntimeError("缺少 OPENID / SEARCH_KEY（环境变量或默认值为空）。")

    print("通过 akshare 获取 A 股公司列表，并筛选沪深主板...")
    df_companies = get_mainboard_company_list_by_akshare()
    total = len(df_companies)
    print(f"沪深主板公司数量：{total}")
    print(f"本次“最新口径”为：昨天 = {YESTERDAY}（今天是 {TODAY}）")
    print(f"默认抓取范围（首次/损坏时用）：{ymd(START_DATE)} ~ {ymd(END_DATE)}")
    print(f"输出目录：{os.path.abspath(OUTPUT_DIR)}\n")

    session = requests.Session()

    report_rows = []
    report_path = os.path.join(
        OUTPUT_DIR, f"run_report_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
    )

    cnt_skip_up_to_yesterday = 0
    cnt_saved_up_to_yesterday = 0
    cnt_saved_not_full = 0
    cnt_no_data = 0
    cnt_parse_empty = 0
    cnt_error = 0

    request_count = 0  # 仅统计真实请求次数

    items = list(df_companies.itertuples(index=False))
    if tqdm:
        items = tqdm(items, desc="抓取微信指数", ncols=100)

    for row in items:
        code = getattr(row, "code")
        name = getattr(row, "name")
        path = parquet_path(code, name)

        last_before = read_last_date_if_exists(path)

        # ✅ 修改点：以“昨天”为最新口径
        if os.path.exists(path) and last_before == YESTERDAY:
            cnt_skip_up_to_yesterday += 1
            report_rows.append({
                "code": code,
                "name": name,
                "status": "skip_up_to_yesterday",
                "keyword_used": "",
                "start_ymd": "",
                "end_ymd": "",
                "points": 0,
                "http_status": "",
                "last_date_before": str(last_before),
                "last_date_after": str(last_before),
                "path": path,
                "note": "",
            })
            continue

        # 增量计算（从最后日期+1 到 昨天）
        start = START_DATE if last_before is None else (last_before + timedelta(days=1))
        end = YESTERDAY
        if start > end:
            cnt_skip_up_to_yesterday += 1
            report_rows.append({
                "code": code,
                "name": name,
                "status": "skip_up_to_yesterday",
                "keyword_used": "",
                "start_ymd": "",
                "end_ymd": "",
                "points": 0,
                "http_status": "",
                "last_date_before": str(last_before),
                "last_date_after": str(last_before),
                "path": path,
                "note": "start>end after calc (already up-to-date by yesterday rule)",
            })
            continue

        keyword = name
        http_status = ""
        data_obj = None
        result_list = None

        try:
            # 发送请求前 sleep（跳过不 sleep）
            request_pre_sleep()
            request_count += 1
            long_break_if_needed(request_count)

            payload = build_payload(OPENID, SEARCH_KEY, keyword, ymd(start), ymd(end))
            status_code, data = request_with_retry(session, payload, keyword=keyword, timeout=30)
            http_status = str(status_code)
            data_obj = data

            result_list = (data or {}).get("content", {}).get("result_list", []) or []
            points = len(result_list)

            if not result_list:
                cnt_no_data += 1
                note = str((data_obj or {}).get("errmsg", ""))[:200]
                log_problem(
                    kind="no_data (result_list为空)",
                    code=code,
                    name=name,
                    keyword=keyword,
                    start=start,
                    end=end,
                    http_status=http_status,
                    last_before=last_before,
                    path=path,
                    note=note,
                    data_obj=data_obj,
                )
                report_rows.append({
                    "code": code,
                    "name": name,
                    "status": "no_data",
                    "keyword_used": keyword,
                    "start_ymd": ymd(start),
                    "end_ymd": ymd(end),
                    "points": 0,
                    "http_status": http_status,
                    "last_date_before": str(last_before),
                    "last_date_after": str(last_before),
                    "path": path,
                    "note": note,
                })
                continue

            df_new = parse_result_to_df(result_list, code, name)
            if df_new.empty:
                cnt_parse_empty += 1
                log_problem(
                    kind="parse_empty (有点数但日期解析失败)",
                    code=code,
                    name=name,
                    keyword=keyword,
                    start=start,
                    end=end,
                    http_status=http_status,
                    last_before=last_before,
                    path=path,
                    note="result_list非空，但date列全部解析为NaT",
                    data_obj={"first_item": (result_list[0] if result_list else None)},
                )
                report_rows.append({
                    "code": code,
                    "name": name,
                    "status": "parse_empty",
                    "keyword_used": keyword,
                    "start_ymd": ymd(start),
                    "end_ymd": ymd(end),
                    "points": points,
                    "http_status": http_status,
                    "last_date_before": str(last_before),
                    "last_date_after": str(last_before),
                    "path": path,
                    "note": "result_list not empty but date parse failed",
                })
                continue

            wrote, last_after = save_merge_dedup(path, df_new)

            if not wrote:
                cnt_error += 1
                log_problem(
                    kind="save_failed (写入未发生)",
                    code=code,
                    name=name,
                    keyword=keyword,
                    start=start,
                    end=end,
                    http_status=http_status,
                    last_before=last_before,
                    path=path,
                    note="df_new非空但写入返回False（理论不应发生）",
                    data_obj={"points": points},
                )
                report_rows.append({
                    "code": code,
                    "name": name,
                    "status": "save_failed",
                    "keyword_used": keyword,
                    "start_ymd": ymd(start),
                    "end_ymd": ymd(end),
                    "points": points,
                    "http_status": http_status,
                    "last_date_before": str(last_before),
                    "last_date_after": str(last_after),
                    "path": path,
                    "note": "df_new not empty but wrote=False",
                })
                continue

            if last_after == YESTERDAY:
                cnt_saved_up_to_yesterday += 1
                status = "saved_up_to_yesterday"
            else:
                cnt_saved_not_full += 1
                status = "saved_but_not_up_to_yesterday"
                log_problem(
                    kind="saved_but_not_up_to_yesterday (写入成功但最新日期未到昨天)",
                    code=code,
                    name=name,
                    keyword=keyword,
                    start=start,
                    end=end,
                    http_status=http_status,
                    last_before=last_before,
                    path=path,
                    note=f"写入后最大日期={last_after}，可能是接口数据滞后或返回不全",
                    data_obj={"points": points},
                )

            report_rows.append({
                "code": code,
                "name": name,
                "status": status,
                "keyword_used": keyword,
                "start_ymd": ymd(start),
                "end_ymd": ymd(end),
                "points": points,
                "http_status": http_status,
                "last_date_before": str(last_before),
                "last_date_after": str(last_after),
                "path": path,
                "note": "",
            })

        except Exception as e:
            cnt_error += 1
            log_problem(
                kind="error (异常抛出)",
                code=code,
                name=name,
                keyword=keyword,
                start=start,
                end=end,
                http_status=http_status,
                last_before=last_before,
                path=path,
                note=str(e),
                data_obj=data_obj,
            )
            report_rows.append({
                "code": code,
                "name": name,
                "status": "error",
                "keyword_used": keyword,
                "start_ymd": ymd(start),
                "end_ymd": ymd(end),
                "points": 0 if result_list is None else len(result_list),
                "http_status": http_status,
                "last_date_before": str(last_before),
                "last_date_after": str(read_last_date_if_exists(path)),
                "path": path,
                "note": str(e)[:300],
            })

    session.close()

    pd.DataFrame(report_rows).to_csv(report_path, index=False, encoding="utf-8-sig")

    print("\n========== 任务完成（严格口径：昨天为最新） ==========")
    print(f"跳过（已到昨天）：{cnt_skip_up_to_yesterday}")
    print(f"保存并更新到昨天：{cnt_saved_up_to_yesterday}")
    print(f"保存但未到昨天：{cnt_saved_not_full}")
    print(f"无数据（接口返回空）：{cnt_no_data}")
    print(f"解析为空（日期解析失败）：{cnt_parse_empty}")
    print(f"错误：{cnt_error}")
    print(f"报告文件：{report_path}")
    print(f"输出目录：{os.path.abspath(OUTPUT_DIR)}")


if __name__ == "__main__":
    main()
