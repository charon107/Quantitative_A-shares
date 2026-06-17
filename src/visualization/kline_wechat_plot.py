# ========= 配置常量（只改这里就行） =========
KEYWORD    = '上海九百'                     # 股票关键词（公司名或简称）
SEARCH_KEY = '1766124860637525_2935910276'  # 微信指数密钥
K_TIME     = '20250901'                     # 开始日期：YYYYMMDD
E_TIME     = '20251218'                     # 结束日期：YYYYMMDD
# ===========================================


import requests
import baostock as bs
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
from matplotlib.patches import Rectangle
from matplotlib.ticker import ScalarFormatter
import matplotlib.dates as mdates

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False


# ========== 股票部分函数 ==========
def get_code_by_name(name):
    """通过公司名称模糊查询，返回 code，code_name"""
    rs = bs.query_stock_basic(code_name=name)
    while rs.error_code == '0' and rs.next():
        row = rs.get_row_data()
        return row[0], row[1]
    return None, None


def get_k_data_by_date(stock_code, start_date, end_date, adjustflag="2"):
    """根据指定日期范围获取日 K 线数据"""
    start_date = datetime.strptime(start_date, "%Y%m%d").strftime("%Y-%m-%d")
    end_date   = datetime.strptime(end_date, "%Y%m%d").strftime("%Y-%m-%d")

    fields = "date,code,open,high,low,close,volume,amount,adjustflag"
    rs = bs.query_history_k_data_plus(
        stock_code,
        fields,
        start_date=start_date,
        end_date=end_date,
        frequency="d",
        adjustflag=adjustflag
    )

    data_list = []
    while rs.error_code == '0' and rs.next():
        data_list.append(rs.get_row_data())

    df = pd.DataFrame(data_list, columns=rs.fields)
    df['date'] = pd.to_datetime(df['date'])
    numeric_cols = ["open", "high", "low", "close", "volume", "amount"]
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce')
    return df


# ========== 微信指数函数 ==========
class WeChatIndex:
    def __init__(self):
        self.headers = {'Content-Type': 'application/json'}

    def requestsAPI(self, keyword, search_key, kTime, eTime):
        json_data = {
            'openid': 'ov4ns0ACWRSm3bCD4-anBCyQgXkk',
            'search_key': search_key,
            'cgi_name': 'GetDefaultIndex',
            'query': [keyword],
            'compound_word': [],
            'start_ymd': kTime,
            'end_ymd': eTime,
        }
        response = requests.post(
            'https://search.weixin.qq.com/cgi-bin/wxaweb/wxindex',
            headers=self.headers,
            json=json_data
        )
        return response.json()

    def parse_data(self, rDatas):
        if rDatas['code'] != 0:
            print(f"请求失败：{rDatas['msg']}")
            return None
        return rDatas["content"]["resp_list"][0]["indexes"][0]["time_indexes"]

    def get_index(self):
        r = self.requestsAPI(KEYWORD, SEARCH_KEY, K_TIME, E_TIME)
        return self.parse_data(r)


# ========== 新增：K线 Tooltip 悬停功能 ==========
def add_kline_hover(ax, df):
    """为K线图添加鼠标悬停提示"""

    # 日期 -> matplotlib 浮点数
    xdata = mdates.date2num(df['date'])

    # 计算 涨幅 / 振幅 / 换手率
    df['pct_change'] = df['close'].pct_change() * 100
    df['amplitude'] = (df['high'] - df['low']) / df['close'].shift(1) * 100
    df['turnover_rate'] = df['volume'] / 100000000 * 100  # 示例换手率（如有真实流通股本可替换）

    # Tooltip 文本框（默认隐藏）
    tooltip = ax.text(
        0.02, 0.95, "", transform=ax.transAxes,
        fontsize=10, va="top", ha="left",
        bbox=dict(boxstyle="round,pad=0.4", fc="black", alpha=0.7, ec="white")
    )
    tooltip.set_visible(False)

    def on_move(event):
        if event.inaxes != ax:
            tooltip.set_visible(False)
            ax.figure.canvas.draw_idle()
            return

        if event.xdata is None:
            return

        # 找最近日期
        idx = min(range(len(xdata)), key=lambda i: abs(xdata[i] - event.xdata))
        row = df.iloc[idx]

        pct = f"{row['pct_change']:.2f}%" if pd.notnull(row['pct_change']) else "--"
        amp = f"{row['amplitude']:.2f}%" if pd.notnull(row['amplitude']) else "--"
        turnover = f"{row['turnover_rate']:.2f}%" if pd.notnull(row['turnover_rate']) else "--"

        text = (
            f"时间: {row['date'].strftime('%Y-%m-%d')}\n"
            f"开盘: {row['open']:.2f}\n"
            f"收盘: {row['close']:.2f}\n"
            f"最高: {row['high']:.2f}\n"
            f"最低: {row['low']:.2f}\n"
            f"涨幅: {pct}\n"
            f"振幅: {amp}\n"
            f"成交量: {row['volume']/10000:.2f} 万\n"
            f"成交额: {row['amount']/1e8:.2f} 亿\n"
            f"换手率: {turnover}"
        )

        tooltip.set_text(text)
        tooltip.set_visible(True)
        ax.figure.canvas.draw_idle()

    ax.figure.canvas.mpl_connect("motion_notify_event", on_move)



# ========== 绘图部分（整合三图） ==========
def plot_all(df, stock_name, stock_code, wx_times, wx_scores):
    fig = plt.figure(figsize=(12, 7))

    # -------------------- 1. K 线图 --------------------
    ax1 = fig.add_subplot(311)
    for i, row in df.iterrows():
        date = row['date']
        open_price = row['open']
        close_price = row['close']
        high_price = row['high']
        low_price = row['low']
        color = 'red' if close_price >= open_price else 'green'

        ax1.plot([date, date], [low_price, high_price], color='black', linewidth=0.8)

        body_height = abs(close_price - open_price)
        body_bottom = min(open_price, close_price)

        if body_height < 0.01:
            ax1.plot([date, date], [open_price - 0.01, open_price + 0.01], color=color, linewidth=3)
        else:
            rect = Rectangle((mdates.date2num(date) - 0.3, body_bottom),
                             0.6, body_height,
                             facecolor=color, edgecolor='black')
            ax1.add_patch(rect)

    ax1.set_title(f'{stock_name} ({stock_code}) - K线图', fontsize=15)
    ax1.grid(alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45)

    # ★★★ 添加悬停功能 ★★★
    add_kline_hover(ax1, df)


    # -------------------- 2. 成交量图 --------------------
    ax2 = fig.add_subplot(312)
    ax2.bar(df['date'], df['volume'],
            color=['red' if c >= o else 'green'
                   for o, c in zip(df['open'], df['close'])],
            alpha=0.7)

    ax2.set_title("成交量", fontsize=14)
    ax2.grid(alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x/10000:.0f}万'))


    # -------------------- 3. 微信指数图 --------------------
    ax3 = fig.add_subplot(313)
    ax3.plot(wx_times, wx_scores)

    ax3.set_title(f"微信指数 - {KEYWORD}", fontsize=14)
    ax3.grid(alpha=0.3)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45)

    ax3.yaxis.set_major_formatter(ScalarFormatter())
    ax3.ticklabel_format(style='plain', axis='y')

    # ======== 微信指数悬停功能（保留你的原逻辑）========
    xdata = mdates.date2num(wx_times)

    def format_coord(x, y):
        idx = min(range(len(xdata)), key=lambda i: abs(xdata[i] - x))
        date_str = wx_times[idx].strftime('%Y-%m-%d')
        value = wx_scores[idx]
        return f"Date: {date_str}, Score: {value}"

    ax3.format_coord = format_coord
    # ============================================

    plt.tight_layout()
    plt.show()



# ========== 主程序入口 ==========
if __name__ == "__main__":
    # 获取股票数据
    bs.login()
    code, real_name = get_code_by_name(KEYWORD)

    if code is None:
        print(f"未找到股票：{KEYWORD}")
        bs.logout()
        exit()

    df = get_k_data_by_date(code, K_TIME, E_TIME)
    bs.logout()

    # 获取微信指数
    wx = WeChatIndex()
    wx_data = wx.get_index()

    if wx_data is None:
        print("微信指数请求失败")
        exit()

    wx_times = [datetime.strptime(str(item['time']), "%Y%m%d") for item in wx_data]
    wx_scores = [item['score'] for item in wx_data]

    # 绘制三图
    plot_all(df, real_name, code, wx_times, wx_scores)
