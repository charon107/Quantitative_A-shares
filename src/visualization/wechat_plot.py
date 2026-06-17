import requests
import matplotlib.pyplot as plt
from datetime import datetime
from matplotlib.ticker import ScalarFormatter      # 用于坐标轴不使用科学计数法
import matplotlib.dates as mdates                  # 日期转数值

# ========= 配置常量（只改这里就行） =========
KEYWORD    = '大众'                       # 关键词
SEARCH_KEY = '1766151497867496_306517630'    # 小程序接口密钥
K_TIME     = '20251001'                       # 开始日期：YYYYMMDD
E_TIME     = '20251205'                       # 结束日期：YYYYMMDD
# =======================================


class weChatNum:
    def __init__(self):
        self.headers = {
            'Content-Type': 'application/json',
        }

    def requestsAPI(self, keyName, search_key, kTime, eTime):
        json_data = {
            'openid': 'ov4ns0ACWRSm3bCD4-anBCyQgXkk',
            'search_key': search_key,
            'cgi_name': 'GetDefaultIndex',
            'query': [
                keyName,
            ],
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

    def clDatas(self, rDatas):
        if rDatas['code'] != 0:
            return f"请求失败,原因：{rDatas['msg']}"
        else:
            return rDatas["content"]["resp_list"][0]["indexes"][0]["time_indexes"]

    def server(self):
        # 使用全局配置常量
        rDatas = self.requestsAPI(KEYWORD, SEARCH_KEY, K_TIME, E_TIME)
        paramsData = self.clDatas(rDatas)  # 返回的是一个列表

        # 如果请求失败，直接打印错误信息并返回
        if isinstance(paramsData, str):
            print(paramsData)
            return

        # ========= 画曲线 =========
        # 将数字型日期转换为 datetime 对象
        times = [datetime.strptime(str(item['time']), "%Y%m%d") for item in paramsData]
        scores = [item['score'] for item in paramsData]

        plt.figure()
        ax = plt.gca()

        ax.plot(times, scores)
        ax.set_xlabel('Date')
        ax.set_ylabel('Score')
        ax.set_title(f'WeChat Index of {KEYWORD}')
        plt.xticks(rotation=45)
        plt.tight_layout()

        # 纵坐标刻度不使用科学计数法
        ax.yaxis.set_major_formatter(ScalarFormatter())
        ax.ticklabel_format(style='plain', axis='y')

        # 把时间列表转换成 matplotlib 内部的浮点数，方便做最近点查找
        xdata = mdates.date2num(times)

        # ======== 自定义右下角 (x, y) 显示格式 ========
        def format_coord(x, y):
            # 找到与当前鼠标 x 最接近的那个数据点索引
            idx = min(range(len(xdata)), key=lambda i: abs(xdata[i] - x))
            date_str = times[idx].strftime('%Y-%m-%d')
            value = scores[idx]
            # 这里故意不用参数 y，而是用该日期真实的纵坐标 value
            return f"Date: {date_str}, Score: {value}"

        ax.format_coord = format_coord
        # ==========================================

        plt.show()
        # ========= 画图结束 =========


if __name__ == '__main__':
    weChatNum = weChatNum()
    weChatNum.server()
