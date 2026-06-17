import os
import re
import glob
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
from matplotlib.widgets import Button

# ===================== 你只需要改这里 =====================

INPUT_TEXT = """
2025-08-26	603363	傲农生物
2025-07-09	002356	赫美集团
2025-04-03	601008	连云港
2025-06-13	002109	兴化股份
2025-06-30	603601	再升科技
2025-09-23	002060	广东建工
2025-11-04	002333	罗普斯金
2025-12-10	000950	重药控股
2025-07-21	601399	国机重装
2025-09-15	000559	万向钱潮
2025-07-25	603528	多伦科技
2025-11-10	600429	三元股份
2025-08-14	002598	山东章鼓
2025-12-02	002686	亿利达
2025-07-03	000670	盈方微
2025-04-07	000713	国投丰乐
2025-01-06	002346	柘中股份
2025-07-21	600528	中铁工业
2025-04-01	002900	哈三联
2025-10-28	600516	方大炭素
2025-03-19	002204	大连重工
2025-09-11	002772	众兴菌业
2025-08-12	000417	合百集团
2025-03-25	603709	中源家居
2025-08-13	603221	爱丽家居
2025-06-17	600113	浙江东日
2025-07-07	605122	四方新材
2025-06-19	002125	湘潭电化
2025-11-13	000633	合金投资
2025-09-25	000020	深华发A
2025-09-18	600706	曲江文旅
2025-11-03	600308	华泰股份
2025-09-25	600769	祥龙电业
2025-11-12	003027	同兴科技
2025-02-21	000581	威孚高科
2025-08-28	603020	爱普股份
2025-11-10	000037	深南电A
2025-11-18	603877	太平鸟
2025-07-21	605598	上海港湾
2025-08-04	002148	北纬科技
2025-04-07	600598	北大荒
2025-08-29	600521	华海药业
2025-06-24	600095	湘财股份
2025-03-24	002735	王子新材
2025-06-12	002237	恒邦股份
2025-06-25	600435	北方导航
2025-07-11	002912	中新赛克
2025-09-01	600833	第一医药
2025-08-26	001296	长江材料
2025-09-01	603257	中国瑞林
2025-02-20	002978	安宁股份
2025-09-01	000661	长春高新
2025-12-12	601700	风范股份
2025-01-03	600664	哈药股份
2025-06-04	002969	嘉美包装
2025-09-26	001209	洪兴股份
2025-06-25	601162	天风证券
2025-08-14	002259	升达林业
2025-08-13	603158	腾龙股份
2025-08-19	603038	华立股份
2025-06-30	003031	中瓷电子
2025-04-07	002880	卫光生物
2025-07-10	600111	北方稀土
2025-09-05	600367	红星发展
2025-04-02	603578	三星新材
2025-06-25	601133	柏诚股份
2025-08-13	002911	佛燃能源
2025-03-27	603125	常青科技
2025-08-08	002164	宁波东力
2025-05-30	003009	中天火箭
2025-02-28	001359	平安电工
2025-03-26	603217	元利科技
2025-08-08	002800	天顺股份
2025-06-03	605006	山东玻纤
2025-06-20	603209	兴通股份
2025-11-18	002877	智能自控
2025-08-22	600058	五矿发展
2025-07-21	002097	山河智能
2025-07-16	000890	法尔胜
2025-08-11	002820	桂发祥
2025-05-29	600834	申通地铁
2025-06-04	603823	百合花
2025-10-21	600168	武汉控股
2025-07-31	002799	环球印务
2025-07-04	605162	新中港
2025-12-15	002697	红旗连锁
2025-11-05	002300	太阳电缆
2025-03-26	000633	合金投资
2025-03-20	600876	凯盛新能
2025-09-16	002172	澳洋健康
2025-08-22	002692	远程股份
2025-09-16	002154	报喜鸟
2025-08-18	600400	红豆股份
2025-08-18	000062	深圳华强
2025-07-21	600815	厦工股份
2025-04-02	002471	中超控股
2025-07-01	000153	丰原药业
2025-09-24	600848	上海临港
2025-08-20	000721	西安饮食
2025-07-23	605158	华达新材
2025-02-06	603786	科博达
2025-06-19	002805	丰元股份
2025-06-25	000519	中兵红箭
2025-12-05	002093	国脉科技
2025-07-17	603738	泰晶科技
2025-09-16	002535	林州重机
2025-08-26	603360	百傲化学
2025-03-19	601619	嘉泽新能
2025-12-02	000812	陕西金叶
2025-04-09	600391	航发科技
2025-05-29	000037	深南电A
2025-08-18	603950	长源东谷
2025-11-24	601238	广汽集团
2025-11-11	000008	神州高铁
2025-08-28	002423	中粮资本
2025-06-27	605277	新亚电子
2025-08-15	002909	集泰股份
2025-03-25	002366	融发核电
2025-07-23	002771	真视通
2025-10-20	002893	京能热力
2025-12-08	603042	华脉科技
2025-01-14	603989	艾华集团
2025-08-06	600654	中安科
2025-04-08	600108	亚盛集团
2025-07-29	600500	中化国际
2025-11-03	600444	国机通用
2025-10-21	002523	天桥起重
2025-04-03	603836	海程邦达
2025-07-21	000400	许继电气
2025-08-25	002091	江苏国泰
2025-04-30	002901	大博医疗
2025-09-15	603030	全筑股份
2025-07-04	600423	柳化股份
2025-08-25	600715	文投控股
2025-06-03	000599	青岛双星
2025-09-22	600375	汉马科技
2025-08-19	002421	达实智能
2025-08-29	601333	广深铁路
2025-03-10	002773	康弘药业
2025-09-02	002031	巨轮智能
2025-08-26	002555	三七互娱
2025-08-18	600226	亨通股份
2025-07-24	002889	东方嘉盛
2025-03-21	002907	华森制药
2025-08-22	603227	雪峰科技
2025-06-30	002725	跃岭股份
2025-10-22	002546	新联电子
2025-06-19	603533	掌阅科技
2025-07-02	600802	福建水泥
2025-02-07	603612	索通发展
2025-08-04	600879	航天电子
2025-08-26	002690	美亚光电
2025-03-03	002942	新农股份
2025-12-01	000829	天音控股
2025-11-25	002098	浔兴股份
2025-08-18	003009	中天火箭
2025-04-02	002201	九鼎新材
2025-07-10	002468	申通快递
2025-07-02	600320	振华重工
2025-03-24	003039	顺控发展
2025-01-08	000951	中国重汽
2025-03-31	603312	西典新能
2025-03-31	000025	特力A
2025-11-14	000039	中集集团
2025-03-14	603093	南华期货
2025-06-13	600871	石化油服
2025-08-15	600076	康欣新材
2025-07-22	600985	淮北矿业
2025-03-20	601886	江河集团
2025-08-15	603688	石英股份
2025-06-05	002090	金智科技
2025-09-15	002330	得利斯
2025-03-24	002391	长青股份
2025-12-12	605090	九丰能源
2025-12-08	603528	多伦科技
2025-08-20	002583	海能达
2025-07-22	600546	山煤国际
2025-10-27	001258	立新能源
2025-09-09	603878	武进不锈
2025-09-01	002422	科伦药业
2025-06-16	002208	合肥城建
2025-10-22	002481	双塔食品
2025-08-26	002410	广联达
2025-09-30	002045	国光电器
2025-08-01	002533	金杯电工
2025-04-01	603590	康辰药业
2025-12-18	603777	来伊份
2025-10-13	600363	联创光电
2025-04-08	002100	天康生物
2025-07-10	000635	英力特
2025-10-23	002912	中新赛克
2025-05-29	002224	三力士
2025-11-03	600759	洲际油气
2025-10-23	000027	深圳能源
2025-08-26	603823	百合花
2025-03-12	002056	横店东磁
2025-07-10	002674	兴业科技
2025-08-26	600725	云维股份
2025-09-18	600056	中国医药
2025-07-16	002995	天地在线
2025-07-07	603863	松炀资源
2025-08-13	001400	江顺科技
2025-12-15	600232	金鹰股份
2025-04-03	603718	海利生物
2025-10-17	002004	华邦健康
2025-08-27	603615	茶花股份
2025-07-21	603165	荣晟环保
2025-08-25	002191	劲嘉股份
2025-07-01	002810	山东赫达
2025-08-13	603897	长城科技
2025-06-12	002653	海思科
2025-04-17	002410	广联达
2025-08-13	002893	京能热力
2025-08-28	600210	紫江企业
2025-03-18	603268	*ST松发
2025-08-20	000420	吉林化纤
2025-07-28	002796	世嘉科技
2025-08-11	600877	电科芯片
2025-10-20	601686	友发集团
2025-10-22	000410	沈阳机床
2025-12-24	002829	星网宇达
2025-01-09	603255	鼎际得
2025-09-17	000045	深纺织A
2025-11-24	603727	博迈科
2025-12-04	002107	沃华医药
2025-07-25	002986	宇新股份
2025-12-18	601198	东兴证券
2025-07-02	000798	中水渔业
2025-03-12	600449	宁夏建材
2025-07-21	605255	天普股份
2025-08-26	600704	物产中大
2025-02-11	600422	昆药集团
2025-07-11	002821	凯莱英
2025-09-02	002347	泰尔股份
2025-07-16	002117	东港股份
2025-08-13	601702	华峰铝业
2025-04-08	603566	普莱柯
2025-09-05	002955	鸿合科技
2025-03-14	601336	新华保险
2025-04-10	600903	贵州燃气
2025-07-02	603280	南方路机
2025-07-02	000959	首钢股份
2025-08-25	003013	地铁设计
2025-09-29	000776	广发证券
2025-06-04	002191	劲嘉股份
2025-03-26	002982	湘佳股份
2025-09-11	002140	东华科技
2025-07-10	600233	圆通速递
2025-09-24	603931	格林达
2025-09-23	002998	优彩资源
2025-01-02	002778	中晟高科
2025-03-10	000905	厦门港务
2025-08-12	603610	麒盛科技
2025-11-24	002574	明牌珠宝
2025-09-02	600724	宁波富达
2025-03-27	001299	美能能源
2025-07-02	002069	獐子岛
2025-07-23	002778	中晟高科
2025-08-26	002691	冀凯股份
2025-06-24	002051	中工国际
2025-08-20	002114	罗平锌电
2025-07-09	002269	美邦服饰
2025-08-01	603052	可川科技
2025-10-10	601992	金隅集团
2025-03-25	002166	莱茵生物
2025-11-04	002752	昇兴股份
2025-06-19	002268	电科网安
2025-03-25	000151	中成股份
2025-03-27	600800	渤海化学
2025-07-11	600399	抚顺特钢
2025-03-27	001218	丽臣实业
2025-07-01	002786	银宝山新
2025-10-30	002769	普路通
2025-09-08	600158	中体产业
2025-06-06	601212	白银有色
2025-06-23	000547	航天发展
2025-11-10	002432	九安医疗
2025-11-26	000523	红棉股份
2025-03-27	600844	金煤科技
2025-07-24	002732	燕塘乳业
2025-11-12	002116	中国海诚
2025-07-28	603639	海利尔
2025-10-23	600740	山西焦化
2025-04-03	000677	恒天海龙
2025-12-12	000801	四川九洲
2025-11-26	603017	中衡设计
2025-12-10	002390	信邦制药
""".strip()

WINDOW_PRE_TRADING_DAYS  = 60   # 向前交易日
WINDOW_POST_TRADING_DAYS = 60   # 向后交易日

# 你本地的“股价 parquet（前复权日线）”保存目录（按需改）
BASE_KLINE_DIR = os.path.join("股价数据_parquet_fq", "kline_fq")

WX_DIR = "沪深主板微信指数"   # 目录：里面是 “公司名_代码.parquet”

# 均线参数（想改周期就改这里）
MA_SHORT = 5
MA_MID   = 20
MA_LONG  = 60

# 统计：SELECT_DATE 后 N 个交易日开盘买入
ENTRY_N_AFTER = 1
# 买入后统计交易日数（含入场日）
HOLD_N = 30

# ===== 关键修复参数：为了正确计算MA，向前额外取多少交易日的历史 =====
# 一般取 MA_LONG + 30 更稳
MA_LOOKBACK_TRADING_DAYS = MA_LONG + 30

# ==========================================================

plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

MA_COLOR = {
    MA_SHORT: "tab:blue",
    MA_MID:   "tab:orange",
    MA_LONG:  "tab:green",
}


# ---------- 6位代码 -> sh./sz. ----------
def bs_code_from_6digits(code6: str) -> str:
    code6 = str(code6).zfill(6)
    if code6.startswith(("600", "601", "603", "605")):
        return f"sh.{code6}"
    else:
        return f"sz.{code6}"


# ---------- 文件名安全 ----------
def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", str(name)).strip()


# ---------- 从 WX_DIR 找 parquet（优先用 code6，其次用 name） ----------
def find_wechat_parquet(wx_dir: str, stock_name: str, code6: str) -> str:
    if not os.path.isdir(wx_dir):
        raise FileNotFoundError(f"微信指数目录不存在：{wx_dir}")

    code6 = str(code6).zfill(6)
    stock_name_safe = safe_filename(stock_name)

    candidates = glob.glob(os.path.join(wx_dir, f"*_{code6}.parquet"))
    if not candidates:
        candidates = glob.glob(os.path.join(wx_dir, f"{stock_name_safe}_*.parquet"))
    if not candidates:
        candidates = glob.glob(os.path.join(wx_dir, f"*{stock_name_safe}*_*[0-9][0-9][0-9][0-9][0-9][0-9].parquet"))

    if not candidates:
        raise FileNotFoundError(f"未在 {wx_dir} 找到 {stock_name} / {code6} 的 parquet 文件")

    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


# ---------- 读取微信指数 parquet（长表） ----------
def load_mainboard_wechat_parquet(path: str) -> pd.DataFrame:
    try:
        dfp = pd.read_parquet(path)
    except Exception as e:
        print(f"读取 parquet 失败：{path}\n{e}")
        return pd.DataFrame(columns=["date", "wx_index"])

    if dfp is None or dfp.empty:
        return pd.DataFrame(columns=["date", "wx_index"])

    if "wechat_index" not in dfp.columns and "wx_index" in dfp.columns:
        dfp = dfp.rename(columns={"wx_index": "wechat_index"})

    dfp["date"] = pd.to_datetime(dfp["date"], errors="coerce")
    dfp["wx_index"] = pd.to_numeric(dfp.get("wechat_index"), errors="coerce")

    out = (
        dfp[["date", "wx_index"]]
        .dropna(subset=["date", "wx_index"])
        .sort_values("date")
        .reset_index(drop=True)
    )
    return out


# ---------- 微信指数 y 轴格式化 ----------
def format_wechat_axis(ax):
    sf = mticker.ScalarFormatter(useOffset=False)
    sf.set_scientific(False)
    ax.yaxis.set_major_formatter(sf)
    ax.ticklabel_format(axis='y', style='plain', useOffset=False)
    ax.yaxis.get_offset_text().set_visible(False)


# ---------- 计算 select_dt 当日涨跌幅（close/prev_close-1） ----------
def calc_select_day_pct_change(dfk: pd.DataFrame, select_dt: pd.Timestamp):
    if dfk is None or dfk.empty:
        return None
    dfk = dfk.sort_values("date").reset_index(drop=True)

    idx_list = dfk.index[dfk["date"] == select_dt].tolist()
    if not idx_list:
        return None
    i = idx_list[-1]
    if i == 0:
        return None

    prev_close = float(dfk.loc[i - 1, "close"])
    today_close = float(dfk.loc[i, "close"])
    if prev_close <= 0:
        return None
    return (today_close / prev_close) - 1.0


# ---------- SELECT当日 红/绿柱 的高低区间幅度 ----------
def calc_select_day_hl_move(dfk: pd.DataFrame, select_dt: pd.Timestamp):
    if dfk is None or dfk.empty:
        return {"ok": False, "msg": "无K线数据"}

    dfk = dfk.sort_values("date").reset_index(drop=True)
    idx_list = dfk.index[dfk["date"] == select_dt].tolist()
    if not idx_list:
        return {"ok": False, "msg": "区间内无SELECT当日K线"}

    i = idx_list[-1]
    row = dfk.loc[i]
    o = float(row["open"])
    h = float(row["high"])
    l = float(row["low"])
    c = float(row["close"])

    if h <= 0 or l <= 0:
        return {"ok": False, "msg": "当日高低价无效"}

    if c >= o:
        move = (h - l) / l
        return {"ok": True, "msg": f"SELECT当日红柱：最低→最高上涨：{move*100:.2f}%（L={l:.2f}, H={h:.2f}）"}
    else:
        move = (h - l) / h
        return {"ok": True, "msg": f"SELECT当日绿柱：最高→最低下跌：{move*100:.2f}%（H={h:.2f}, L={l:.2f}）"}


# ---------- 收益/亏损（文本框统计） ----------
def calc_best_worst_after_entry(dfk: pd.DataFrame,
                                select_dt: pd.Timestamp,
                                entry_n_after: int = 1,
                                hold_n: int = 20):
    if dfk is None or dfk.empty:
        return {"ok": False, "msg": "无K线数据"}

    dfk = dfk.sort_values("date").reset_index(drop=True)
    idx_after = dfk.index[dfk["date"] > select_dt]

    if len(idx_after) < entry_n_after:
        return {"ok": False, "msg": f"SELECT 后不足{entry_n_after}个交易日，无法入场"}

    entry_idx = idx_after[entry_n_after - 1]
    entry_row = dfk.loc[entry_idx]
    entry_dt = entry_row["date"]
    buy_open = float(entry_row["open"])

    if pd.isna(buy_open) or buy_open <= 0:
        return {"ok": False, "msg": "入场日开盘价无效"}

    window = dfk.iloc[entry_idx: entry_idx + hold_n].copy()
    if len(window) < hold_n:
        return {"ok": False, "msg": f"入场后不足{hold_n}个交易日（仅{len(window)}个）"}

    max_high = float(window["high"].max())
    min_low = float(window["low"].min())

    best_ret = (max_high - buy_open) / buy_open
    worst_ret = (min_low - buy_open) / buy_open

    dt_max = window.loc[window["high"].idxmax(), "date"]
    dt_min = window.loc[window["low"].idxmin(), "date"]

    return {
        "ok": True,
        "entry_dt": entry_dt,
        "buy_open": buy_open,
        "hold_n": hold_n,
        "max_high": max_high,
        "min_low": min_low,
        "best_ret": best_ret,
        "worst_ret": worst_ret,
        "dt_max": dt_max,
        "dt_min": dt_min,
    }


# ---------- 核心修复：读取 parquet 时保证 MA 计算期足够 ----------
def _locate_kline_parquet(stock_code_bs: str) -> str | None:
    path = os.path.join(BASE_KLINE_DIR, f"{stock_code_bs}.parquet")
    if os.path.exists(path):
        return path

    cand = glob.glob(os.path.join("**", f"{stock_code_bs}.parquet"), recursive=True)
    cand = [p for p in cand if p.replace("\\", "/").endswith(f"{stock_code_bs}.parquet")]
    if cand:
        return cand[0]
    return None


def get_k_data_local_parquet(stock_code_bs: str,
                             display_start_date: str,
                             display_end_date: str,
                             ma_lookback_trading_days: int = 90) -> tuple[pd.DataFrame, dict]:
    """
    返回：(df_display, info)
    - df_display：已经带 MA 列、并裁剪到显示窗口的数据
    - info：一些诊断信息（是否历史不足导致MA前段NaN）
    """
    info = {"ok": False, "msg": "", "ma60_first_valid": None, "raw_min_date": None, "raw_rows": 0}

    path = _locate_kline_parquet(stock_code_bs)
    if not path:
        info["msg"] = f"未找到K线parquet：{stock_code_bs}"
        return pd.DataFrame(), info

    try:
        dfk = pd.read_parquet(path)
    except Exception as e:
        info["msg"] = f"[ERROR] 读取K线parquet失败: {path} / {e}"
        return pd.DataFrame(), info

    if dfk is None or dfk.empty:
        info["msg"] = f"{stock_code_bs} parquet为空"
        return pd.DataFrame(), info

    if "datetime" in dfk.columns and "date" not in dfk.columns:
        dfk = dfk.rename(columns={"datetime": "date"})

    dfk["date"] = pd.to_datetime(dfk["date"], errors="coerce")

    numeric_cols = ["open", "high", "low", "close", "volume", "amount", "turn", "pctChg"]
    for c in numeric_cols:
        if c in dfk.columns:
            dfk[c] = pd.to_numeric(dfk[c], errors="coerce")

    need = ["date", "open", "high", "low", "close"]
    for c in need:
        if c not in dfk.columns:
            info["msg"] = f"{stock_code_bs} parquet缺少字段 {c}，实际列：{dfk.columns.tolist()}"
            return pd.DataFrame(), info

    dfk = dfk.dropna(subset=need).sort_values("date").reset_index(drop=True)
    if dfk.empty:
        info["msg"] = f"{stock_code_bs} 清洗后无有效行"
        return pd.DataFrame(), info

    info["raw_rows"] = len(dfk)
    info["raw_min_date"] = dfk["date"].min()

    # ===== 关键：为了MA准确，计算均线时至少需要 ma_lookback_trading_days 的历史 =====
    disp_s = pd.to_datetime(display_start_date)
    disp_e = pd.to_datetime(display_end_date)

    # 先把“显示起点之前”的历史取出来，确保有足够交易日（按行数而非自然日更靠谱）
    # 找到显示起点在dfk里的位置
    idx_disp_start = dfk.index[dfk["date"] >= disp_s]
    if len(idx_disp_start) == 0:
        # 显示区间完全在数据末端之后
        info["msg"] = f"{stock_code_bs} 数据区间不覆盖显示窗口"
        return pd.DataFrame(), info

    i0 = int(idx_disp_start[0])
    i_hist_start = max(0, i0 - ma_lookback_trading_days)

    df_for_ma = dfk.iloc[i_hist_start:].copy()  # 包含显示窗口前的足够历史

    # ===== 先在 df_for_ma 上算MA（这样显示窗口起点附近就不容易NaN）=====
    df_for_ma[f"MA{MA_SHORT}"] = df_for_ma["close"].rolling(MA_SHORT, min_periods=MA_SHORT).mean()
    df_for_ma[f"MA{MA_MID}"]   = df_for_ma["close"].rolling(MA_MID,   min_periods=MA_MID).mean()
    df_for_ma[f"MA{MA_LONG}"]  = df_for_ma["close"].rolling(MA_LONG,  min_periods=MA_LONG).mean()

    # 记录 MA60 首个非空日期（用于诊断）
    ma_col = f"MA{MA_LONG}"
    if ma_col in df_for_ma.columns and df_for_ma[ma_col].notna().any():
        info["ma60_first_valid"] = df_for_ma.loc[df_for_ma[ma_col].notna(), "date"].min()
    else:
        info["ma60_first_valid"] = None

    # ===== 再裁剪到显示窗口 =====
    df_display = df_for_ma[(df_for_ma["date"] >= disp_s) & (df_for_ma["date"] <= disp_e)].copy().reset_index(drop=True)

    info["ok"] = True
    # 如果显示窗口内 MA60 仍然大量 NaN，提示历史不足
    if df_display.empty:
        info["msg"] = f"{stock_code_bs} 显示窗口内无数据"
    else:
        nan_ratio = df_display[ma_col].isna().mean() if ma_col in df_display.columns else 1.0
        if nan_ratio > 0.3:
            info["msg"] = f"MA{MA_LONG} 在显示窗口前段为NaN：很可能历史不足（parquet最早={info['raw_min_date']:%Y-%m-%d}）"
        else:
            info["msg"] = "OK"

    return df_display, info


# ---------- 绘图：K线 + 成交额 + 微信指数 ----------
def draw_kline_volume_wechat(ax1, ax2, ax3, dfk: pd.DataFrame, dfw: pd.DataFrame,
                             title: str, start_dt: pd.Timestamp, end_dt: pd.Timestamp,
                             select_dt: pd.Timestamp | None = None):
    ax1.clear()
    ax2.clear()
    ax3.clear()

    # ===== K线 =====
    if dfk is None or dfk.empty:
        ax1.set_title(title + "（区间无K线数据）", fontsize=14, fontweight='bold', pad=12)
        ax1.grid(True, alpha=0.3, linestyle='--')
        ax2.grid(True, alpha=0.3, linestyle='--')
    else:
        for _, row in dfk.iterrows():
            dt_ = row['date']
            o, h, l, c = row['open'], row['high'], row['low'], row['close']
            color = 'red' if c >= o else 'green'

            ax1.plot([dt_, dt_], [l, h], color='black', linewidth=0.8)

            body_h = abs(c - o)
            body_b = min(o, c)
            if body_h < 0.01:
                ax1.plot([dt_, dt_], [o - 0.01, o + 0.01], color=color, linewidth=3)
            else:
                rect = Rectangle(
                    (mdates.date2num(dt_) - 0.3, body_b),
                    0.6, body_h,
                    facecolor=color, edgecolor='black', linewidth=0.5
                )
                ax1.add_patch(rect)

        # ===== 叠加均线（指定颜色）=====
        for ma in [MA_SHORT, MA_MID, MA_LONG]:
            col = f"MA{ma}"
            if col in dfk.columns:
                ax1.plot(
                    dfk["date"], dfk[col],
                    linewidth=1.8,
                    label=col,
                    color=MA_COLOR.get(ma, None)
                )
        ax1.legend(loc="best", fontsize=10, framealpha=0.8)

        ax1.set_title(title, fontsize=14, fontweight='bold', pad=12)
        ax1.set_ylabel('价格(元)')
        ax1.grid(True, alpha=0.3, linestyle='--')
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        ax1.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(dfk) // 10)))
        plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha='right')

        # 成交额（如果没有 amount，就尝试 volume）
        if "amount" in dfk.columns:
            bar_y = dfk["amount"]
            ax2.set_ylabel('成交额 (元)')
            ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x / 10000:.0f}万'))
        else:
            bar_y = dfk.get("volume", pd.Series([0] * len(dfk)))
            ax2.set_ylabel('成交量')
            ax2.yaxis.set_major_formatter(mticker.ScalarFormatter(useOffset=False))

        ax2.bar(
            dfk['date'], bar_y,
            color=['red' if c >= o else 'green' for o, c in zip(dfk['open'], dfk['close'])],
            alpha=0.6, width=0.8
        )
        ax2.grid(True, alpha=0.3, linestyle='--')
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        ax2.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(dfk) // 10)))
        plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')

    # ===== 微信指数 =====
    EMPTY = pd.DataFrame(columns=["date", "wx_index"])

    if dfw is None or dfw.empty:
        ax3.set_title("沪深主板微信指数（无数据/未找到Parquet）")
        ax3.set_ylabel("指数")
        ax3.set_xlabel("日期")
        ax3.grid(True, alpha=0.3, linestyle='--')
        format_wechat_axis(ax3)

        if select_dt is not None:
            vline_kwargs = dict(color="blue", linestyle="--", linewidth=1.2, alpha=0.8)
            ax1.axvline(select_dt, **vline_kwargs)
            ax2.axvline(select_dt, **vline_kwargs)
            ax3.axvline(select_dt, **vline_kwargs)
        return EMPTY

    dfx = dfw[(dfw["date"] >= start_dt) & (dfw["date"] <= end_dt)].copy()
    if dfx.empty:
        ax3.set_title("沪深主板微信指数（区间无数据）")
        ax3.set_ylabel("指数")
        ax3.set_xlabel("日期")
        ax3.grid(True, alpha=0.3, linestyle='--')
        format_wechat_axis(ax3)

        if select_dt is not None:
            vline_kwargs = dict(color="blue", linestyle="--", linewidth=1.2, alpha=0.8)
            ax1.axvline(select_dt, **vline_kwargs)
            ax2.axvline(select_dt, **vline_kwargs)
            ax3.axvline(select_dt, **vline_kwargs)
        return EMPTY

    ax3.plot(dfx["date"], dfx["wx_index"], linewidth=1.8)
    ax3.set_title("沪深主板微信指数")
    ax3.set_ylabel("指数")
    ax3.set_xlabel("日期")
    ax3.grid(True, alpha=0.3, linestyle='--')
    ax3.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    ax3.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(dfx) // 10)))
    plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45, ha='right')
    format_wechat_axis(ax3)

    if select_dt is not None:
        vline_kwargs = dict(color="blue", linestyle="--", linewidth=1.2, alpha=0.8)
        ax1.axvline(select_dt, **vline_kwargs)
        ax2.axvline(select_dt, **vline_kwargs)
        ax3.axvline(select_dt, **vline_kwargs)

        y0, y1 = ax3.get_ylim()
        ax3.text(
            select_dt, y1, "SELECT_DATE",
            ha="left", va="top",
            fontsize=9,
            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="blue", alpha=0.8)
        )

    return dfx.reset_index(drop=True)


# ===================== 解析输入列表 =====================
def parse_input(text: str) -> list[dict]:
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"[\t, ]+", line)
        if len(parts) < 3:
            raise ValueError(f"无法解析行（至少需要 日期 代码 名称）：{line}")
        date_str, code6, name = parts[0], parts[1], "".join(parts[2:]) if len(parts) > 3 else parts[2]
        dt = pd.to_datetime(date_str)
        items.append({"select_dt": dt, "code6": str(code6).zfill(6), "name": name})
    return items


stocks = parse_input(INPUT_TEXT)
if not stocks:
    raise ValueError("INPUT_TEXT 为空，没有任何股票。")

# ===================== 主图 & 翻页状态 =====================
fig, (ax1, ax2, ax3) = plt.subplots(
    3, 1, figsize=(14, 12),
    gridspec_kw={'height_ratios': [3, 1, 1], 'hspace': 0.25}
)
plt.subplots_adjust(bottom=0.12)

state = {
    "idx": 0,
    "wx_dfx": pd.DataFrame(columns=["date", "wx_index"]),
    "wx_annot": None,
    "ax3": ax3,
}


def build_trading_window_from_df(df_all: pd.DataFrame,
                                 select_dt: pd.Timestamp):
    """
    用交易日行号构造窗口
    """
    if df_all is None or df_all.empty:
        return None, None, None, None

    df_all = df_all.sort_values("date").reset_index(drop=True)

    idx_list = df_all.index[df_all["date"] == select_dt].tolist()
    if not idx_list:
        return None, None, None, None

    i = idx_list[0]

    start_i = max(0, i - WINDOW_PRE_TRADING_DAYS)
    end_i   = min(len(df_all) - 1, i + WINDOW_POST_TRADING_DAYS)

    start_dt = df_all.loc[start_i, "date"]
    end_dt   = df_all.loc[end_i, "date"]

    return start_dt, end_dt, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")



def render_one(i: int):
    item = stocks[i]
    name = item["name"]
    code6 = item["code6"]
    select_dt = item["select_dt"]

    code_bs = bs_code_from_6digits(code6)

    # 先读取完整K线（不裁剪）
    full_path = _locate_kline_parquet(code_bs)
    if not full_path:
        dfk = pd.DataFrame()
        kinfo = {"ok": False, "msg": "找不到K线"}
        start_dt = end_dt = None
        DATE_START = DATE_END = ""
    else:
        df_all = pd.read_parquet(full_path)
        df_all["date"] = pd.to_datetime(df_all["date"])
        df_all = df_all.sort_values("date").reset_index(drop=True)

        # 用交易日构造窗口
        start_dt, end_dt, DATE_START, DATE_END = build_trading_window_from_df(df_all, select_dt)

        if start_dt is None:
            dfk = pd.DataFrame()
            kinfo = {"ok": False, "msg": "SELECT_DATE 不在数据内"}
        else:
            # 再调用原函数裁剪 + 计算MA
            dfk, kinfo = get_k_data_local_parquet(
                code_bs,
                DATE_START,
                DATE_END,
                ma_lookback_trading_days=MA_LOOKBACK_TRADING_DAYS
            )

    # 1) 微信指数 parquet
    dfw = pd.DataFrame(columns=["date", "wx_index"])
    wx_err = None
    try:
        wx_path = find_wechat_parquet(WX_DIR, name, code6)
        dfw = load_mainboard_wechat_parquet(wx_path)
    except Exception as e:
        wx_err = str(e)

    # 2) K线：关键修复版读取 + MA计算
    code_bs = bs_code_from_6digits(code6)
    dfk, kinfo = get_k_data_local_parquet(
        code_bs,
        DATE_START,
        DATE_END,
        ma_lookback_trading_days=MA_LOOKBACK_TRADING_DAYS
    )

    # SELECT_DATE 当日涨跌幅
    sel_pct = calc_select_day_pct_change(dfk, select_dt)

    # SELECT_DATE 当日红/绿柱高低区间幅度
    hl_move = calc_select_day_hl_move(dfk, select_dt)

    # 买入后统计（只用于文本框）
    stats = calc_best_worst_after_entry(dfk, select_dt, entry_n_after=ENTRY_N_AFTER, hold_n=HOLD_N)

    # 3) 画图标题
    extra = f"  |  SELECT={select_dt:%Y-%m-%d}  |  第 {i+1}/{len(stocks)} 只"
    title = f"{name}（{code6} / {code_bs}） 区间:{DATE_START} ~ {DATE_END}{extra}"
    title += f"  |  均线: MA{MA_SHORT}, MA{MA_MID}, MA{MA_LONG}"

    # 诊断信息提示
    if not kinfo.get("ok"):
        title += f"\nK线：{kinfo.get('msg','读取失败')}"
    else:
        if kinfo.get("msg") and kinfo["msg"] != "OK":
            title += f"\n提示：{kinfo['msg']}"
        if kinfo.get("ma60_first_valid") is not None:
            title += f"（MA{MA_LONG}首个有效={kinfo['ma60_first_valid']:%Y-%m-%d}）"

    if wx_err:
        title += f"\n微信指数：{wx_err}"

    wx_dfx = draw_kline_volume_wechat(ax1, ax2, ax3, dfk, dfw, title, start_dt, end_dt, select_dt)

    # ===== 文本框 =====
    lines = []
    if sel_pct is None:
        lines.append("SELECT当日涨跌幅：NA（缺少前一交易日或当日收盘）")
    else:
        lines.append(f"SELECT当日涨跌幅：{sel_pct*100:+.2f}%")

    if not hl_move.get("ok"):
        lines.append(f"SELECT当日高低区间：NA（{hl_move.get('msg','无法计算')}）")
    else:
        lines.append(hl_move["msg"])

    if stats.get("ok"):
        lines.append(f"入场：SELECT后第{ENTRY_N_AFTER}个交易日 开盘买入 @ {stats['buy_open']:.2f}（{stats['entry_dt']:%Y-%m-%d}）")
        lines.append(f"入场后{HOLD_N}日最高收益：{stats['best_ret']*100:+.2f}%（高点 {stats['dt_max']:%Y-%m-%d}）")
        lines.append(f"入场后{HOLD_N}日最高亏损：{stats['worst_ret']*100:+.2f}%（低点 {stats['dt_min']:%Y-%m-%d}）")
    else:
        lines.append(f"收益统计：{stats.get('msg','无法计算')}")

    ax1.text(
        0.01, 0.98, "\n".join(lines),
        transform=ax1.transAxes,
        ha="left", va="top",
        fontsize=10,
        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="gray", alpha=0.85)
    )

    # 4) 重新创建 annotation（因为 ax3.clear() 会清掉旧的）
    wx_annot = ax3.annotate(
        "",
        xy=(0, 0), xytext=(15, 15),
        textcoords="offset points",
        bbox=dict(boxstyle="round", fc="white", ec="gray", alpha=0.85),
        arrowprops=dict(arrowstyle="->", color="gray")
    )
    wx_annot.set_visible(False)

    state["wx_dfx"] = wx_dfx
    state["wx_annot"] = wx_annot

    fig.canvas.draw_idle()


def on_hover(event):
    ax3_local = state["ax3"]
    wx_annot = state["wx_annot"]
    dfx = state["wx_dfx"]

    if wx_annot is None:
        return

    if event.inaxes != ax3_local:
        if wx_annot.get_visible():
            wx_annot.set_visible(False)
            fig.canvas.draw_idle()
        return

    if dfx is None or dfx.empty or event.xdata is None:
        if wx_annot.get_visible():
            wx_annot.set_visible(False)
            fig.canvas.draw_idle()
        return

    x_dt = mdates.num2date(event.xdata)
    x_dt = pd.to_datetime(x_dt).tz_localize(None)

    idx_near = (dfx["date"] - x_dt).abs().idxmin()
    row = dfx.loc[idx_near]
    dt_near = row["date"]
    wx_val = row["wx_index"]

    wx_annot.xy = (dt_near, wx_val)
    wx_annot.set_text(f"{dt_near:%Y-%m-%d}\n微信指数：{wx_val:,.0f}")
    wx_annot.set_visible(True)
    fig.canvas.draw_idle()


def goto(delta: int):
    n = len(stocks)
    state["idx"] = (state["idx"] + delta) % n
    render_one(state["idx"])


fig.canvas.mpl_connect("motion_notify_event", on_hover)

# ===================== 翻页按钮 =====================
ax_prev = plt.axes([0.35, 0.02, 0.12, 0.06])
ax_next = plt.axes([0.53, 0.02, 0.12, 0.06])

btn_prev = Button(ax_prev, "上一页")
btn_next = Button(ax_next, "下一页")

btn_prev.on_clicked(lambda event: goto(-1))
btn_next.on_clicked(lambda event: goto(+1))

def on_key(event):
    if event.key in ["left", "a"]:
        goto(-1)
    elif event.key in ["right", "d"]:
        goto(+1)

fig.canvas.mpl_connect("key_press_event", on_key)

# ===================== 先画第一页 =====================
render_one(state["idx"])
plt.show()
