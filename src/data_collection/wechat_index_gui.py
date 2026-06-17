import requests
import json
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import openpyxl
from datetime import datetime, timedelta
import os
import configparser
import platform
import hashlib
import re
import sys
import threading
import atexit

# 全局配置
URL = "https://search.weixin.qq.com/cgi-bin/wxaweb/wxindex"
HEADERS = {
    'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541510) XWEB/17071",
    'Content-Type': "application/json",
    'xweb_xhr': "1",
    'Sec-Fetch-Site': "cross-site",
    'Sec-Fetch-Mode': "cors",
    'Sec-Fetch-Dest': "empty",
    'Referer': "https://servicewechat.com/wxc026e7662ec26a3a/74/page-frame.html",
    'Accept-Language': "zh-CN,zh;q=0.9"
}

# 配置文件路径
CONFIG_FILE = "wechat_search_config.ini"
# 数据保存文件夹
DATA_FOLDER = "data"

# 全局会话对象（用于统一管理请求连接）
REQUEST_SESSION = requests.Session()


# -------------------------- 程序退出清理函数 --------------------------
def cleanup_before_exit():
    """程序退出前的清理工作"""
    global REQUEST_SESSION
    try:
        # 关闭requests会话
        if REQUEST_SESSION:
            REQUEST_SESSION.close()
        # 终止所有非守护线程
        for thread in threading.enumerate():
            if thread != threading.main_thread() and not thread.daemon:
                thread.join(timeout=1)
        print("程序清理完成，正常退出")
    except Exception as e:
        print(f"清理过程出错: {str(e)}")


# 注册退出清理函数
atexit.register(cleanup_before_exit)


# -------------------------- 微信搜索工具类 --------------------------
class WeChatSearchTool:
    def __init__(self, root):
        self.root = root
        self.root.title("微信搜索数据查询工具")

        # 存储after任务ID，用于关闭时取消
        self.after_ids = []

        # 1. 先隐藏窗口（解决启动闪烁）
        self.root.withdraw()

        # 2. 设置窗口基础尺寸
        self.window_width = 600
        self.window_height = 750  # 加高窗口以适配多行关键词输入框
        self.root.geometry(f"{self.window_width}x{self.window_height}")
        self.root.resizable(True, True)

        # 3. 窗口居中显示（核心优化）
        self._center_window()

        # 4. 加载配置文件
        self.config = self._load_config()

        # 5. 创建所有界面组件
        self._create_widgets()

        # 6. 填充默认值（日期+保存的OpenID/Key）
        self._fill_default_values()

        # 7. 绑定窗口关闭事件（关键：解决进程残留）
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        # 8. 强制更新布局，再显示窗口（避免闪烁）
        self.root.update_idletasks()
        self.root.deiconify()

    def _generate_x_request_sign(self, search_key: str, query_list: list) -> str:
        """
        新增：生成X-Request-Sign签名
        :param search_key: 搜索key
        :param query_list: 查询关键词列表
        :return: 32位小写MD5签名
        """
        if not search_key:
            self._log("警告：Search Key为空，无法生成X-Request-Sign签名")
            return ""
        # 拼接search_key + query数组拼接后的字符串
        query_str = "".join(query_list)
        raw_str = search_key + query_str
        # 计算MD5哈希（UTF-8编码）
        md5_obj = hashlib.md5(raw_str.encode("utf-8"))
        sign = md5_obj.hexdigest()
        self._log(f"生成签名：{raw_str} -> {sign}")
        return sign

    def _center_window(self):
        """窗口居中显示（适配不同分辨率）"""
        # 获取屏幕宽高
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        # 计算窗口居中坐标
        x = int((screen_width - self.window_width) / 2)
        y = int((screen_height - self.window_height) / 2)

        # 设置窗口位置
        self.root.geometry(f"{self.window_width}x{self.window_height}+{x}+{y}")

    def _load_config(self):
        """加载配置文件（OpenID/Search Key）"""
        config = configparser.ConfigParser()
        # 如果配置文件存在则加载，否则创建新配置
        if os.path.exists(CONFIG_FILE):
            config.read(CONFIG_FILE, encoding="utf-8")
        # 确保配置节存在
        if "DEFAULT" not in config.sections():
            config["DEFAULT"] = {"openid": "", "search_key": ""}
        return config

    def _save_config(self):
        """保存配置文件（OpenID/Search Key）"""
        # 获取当前输入的OpenID和Search Key
        openid = self.openid_entry.get().strip()
        search_key = self.search_key_entry.get().strip()
        # 更新配置
        self.config["DEFAULT"]["openid"] = openid
        self.config["DEFAULT"]["search_key"] = search_key
        # 写入文件
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            self.config.write(f)
        self._log("OpenID和Search Key已本地保存")

    def _get_default_dates(self):
        """获取默认日期范围（近365天）"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)
        # 格式化为YYYYMMDD
        start_ymd = start_date.strftime("%Y%m%d")
        end_ymd = end_date.strftime("%Y%m%d")
        return start_ymd, end_ymd

    def _fill_default_values(self):
        """填充默认值"""
        # 1. 日期默认值（近365天）
        start_ymd, end_ymd = self._get_default_dates()
        self.start_ymd_entry.delete(0, tk.END)
        self.start_ymd_entry.insert(0, start_ymd)
        self.end_ymd_entry.delete(0, tk.END)
        self.end_ymd_entry.insert(0, end_ymd)

        # 2. OpenID和Search Key（从配置文件加载）
        openid = self.config["DEFAULT"].get("openid", "")
        self.openid_entry.delete(0, tk.END)
        self.openid_entry.insert(0, openid)
        search_key = self.config["DEFAULT"].get("search_key", "")
        self.search_key_entry.delete(0, tk.END)
        self.search_key_entry.insert(0, search_key)

    def _parse_keywords(self):
        """解析输入的关键词，支持空格、中英文逗号、换行分隔，去重去空"""
        # 获取输入的文本
        text = self.query_text.get(1.0, tk.END).strip()
        if not text:
            return []
        # 替换所有分隔符为换行符，统一分割规则
        text = text.replace('，', '\n').replace(',', '\n').replace(' ', '\n')
        # 按换行分割，去重、去空字符串，保留有效关键词
        keywords = list(set([kw.strip() for kw in text.split('\n') if kw.strip()]))
        return keywords

    def _create_widgets(self):
        """创建界面组件（布局优化）"""
        # 1. 输入区域（新增OpenID/Search Key输入框）
        input_frame = ttk.LabelFrame(self.root, text="查询参数", padding="10")
        input_frame.pack(fill=tk.X, padx=10, pady=5)

        # 调整网格布局，让输入框更紧凑且居中对齐
        input_frame.columnconfigure(1, weight=1)  # 输入框列自适应宽度
        input_frame.columnconfigure(0, minsize=120)  # 标签列最小宽度

        # OpenID输入
        ttk.Label(input_frame, text="OpenID：").grid(row=0, column=0, padx=5, pady=5, sticky=tk.E)
        self.openid_entry = ttk.Entry(input_frame)
        self.openid_entry.grid(row=0, column=1, padx=5, pady=5, sticky=tk.EW)

        # Search Key输入
        ttk.Label(input_frame, text="Search Key：").grid(row=1, column=0, padx=5, pady=5, sticky=tk.E)
        self.search_key_entry = ttk.Entry(input_frame)
        self.search_key_entry.grid(row=1, column=1, padx=5, pady=5, sticky=tk.EW)

        # 多关键词输入框（替换原单行输入框）
        ttk.Label(input_frame, text="搜索词：").grid(row=2, column=0, padx=5, pady=5, sticky=tk.NE)
        self.query_text = scrolledtext.ScrolledText(input_frame, height=4, wrap=tk.WORD)
        self.query_text.grid(row=2, column=1, padx=5, pady=5, sticky=tk.EW)
        # 添加提示文字
        self.query_text.insert(tk.END, "")

        # 时间范围
        ttk.Label(input_frame, text="开始日期(YYYYMMDD)：").grid(row=3, column=0, padx=5, pady=5, sticky=tk.E)
        self.start_ymd_entry = ttk.Entry(input_frame)
        self.start_ymd_entry.grid(row=3, column=1, padx=5, pady=5, sticky=tk.EW)

        ttk.Label(input_frame, text="结束日期(YYYYMMDD)：").grid(row=4, column=0, padx=5, pady=5, sticky=tk.E)
        self.end_ymd_entry = ttk.Entry(input_frame)
        self.end_ymd_entry.grid(row=4, column=1, padx=5, pady=5, sticky=tk.EW)

        # 按钮区域
        btn_frame = ttk.Frame(input_frame)
        btn_frame.grid(row=5, column=0, columnspan=2, padx=5, pady=10)

        # 查询按钮
        self.query_btn = ttk.Button(btn_frame, text="执行查询并导出Excel", command=self.execute_query)
        self.query_btn.pack(side=tk.LEFT, padx=5)

        # 保存配置按钮
        self.save_config_btn = ttk.Button(btn_frame, text="保存OpenID/Key", command=self._save_config)
        self.save_config_btn.pack(side=tk.LEFT, padx=5)

        # 2. 日志显示区域
        log_frame = ttk.LabelFrame(self.root, text="运行日志", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, height=22)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _log(self, message):
        """日志输出"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        # 记录after任务ID，用于关闭时取消
        task_id = self.root.after(100, lambda: None)
        self.after_ids.append(task_id)
        self.root.update_idletasks()

    def _cancel_all_after_tasks(self):
        """取消所有tkinter的after定时任务"""
        for task_id in self.after_ids:
            try:
                self.root.after_cancel(task_id)
            except:
                pass
        self.after_ids.clear()

        # 递归取消所有子组件的after任务
        def _cancel_child_after(widget):
            for child in widget.winfo_children():
                _cancel_child_after(child)
                try:
                    # 尝试获取并取消子组件的after任务
                    task_ids = child.after_info()
                    while task_ids:
                        child.after_cancel(task_ids)
                        task_ids = child.after_info()
                except:
                    pass

        _cancel_child_after(self.root)

    def _close_all_resources(self):
        """关闭所有占用的资源"""
        global REQUEST_SESSION
        try:
            # 关闭requests会话
            if REQUEST_SESSION:
                REQUEST_SESSION.close()
            self._log("已关闭网络请求会话")
        except Exception as e:
            self._log(f"关闭网络会话出错: {str(e)}")

        try:
            # 清理Excel相关资源
            openpyxl.Workbook().close()
        except:
            pass

    def _terminate_all_threads(self):
        """终止所有非守护线程"""
        for thread in threading.enumerate():
            if thread != threading.main_thread() and not thread.daemon:
                try:
                    thread.join(timeout=2)  # 等待线程结束
                    self._log(f"已终止线程: {thread.name}")
                except Exception as e:
                    self._log(f"终止线程出错: {str(e)}")

    def _on_closing(self):
        """窗口关闭事件处理（核心：解决进程残留）"""
        if messagebox.askokcancel("确认退出", "确定要退出程序吗？"):
            self._log("开始清理资源并退出程序...")

            # 1. 取消所有after定时任务
            self._cancel_all_after_tasks()

            # 2. 终止所有非守护线程
            self._terminate_all_threads()

            # 3. 关闭所有资源
            self._close_all_resources()

            # 4. 强制销毁窗口
            self.root.destroy()

            # 5. 强制退出Python进程（彻底解决残留问题）
            os._exit(0)

    def _export_to_excel(self, data_list, keyword):
        """修改：单个关键词生成独立的Excel文件，保存到data文件夹"""
        if not data_list:
            self._log(f"【{keyword}】无数据可导出")
            return

        # 使用with语句确保Excel文件正确关闭
        try:
            # 1. 创建data文件夹（如果不存在）
            if not os.path.exists(DATA_FOLDER):
                os.makedirs(DATA_FOLDER)
                self._log(f"已创建数据保存文件夹：{os.path.abspath(DATA_FOLDER)}")

            # 2. 创建工作簿
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = f"{keyword}搜索数据"

            # 3. 表头（A列=关键词 + ymd + channel_score所有字段）
            headers = [
                "关键词", "ymd", "ad_score", "emoji_score", "extlink_score",
                "finder_score", "live_score", "miniapp_score", "mpdoc_score",
                "query_score", "score_exp", "total_score", "w1w_score"
            ]
            ws.append(headers)

            # 4. 写入数据
            for data in data_list:
                row = [
                    data.get("keyword", ""),  # A列：关键词
                    data.get("ymd", ""),
                    data.get("channel_score", {}).get("ad_score", 0),
                    data.get("channel_score", {}).get("emoji_score", 0),
                    data.get("channel_score", {}).get("extlink_score", 0),
                    data.get("channel_score", {}).get("finder_score", 0),
                    data.get("channel_score", {}).get("live_score", 0),
                    data.get("channel_score", {}).get("miniapp_score", 0),
                    data.get("channel_score", {}).get("mpdoc_score", 0),
                    data.get("channel_score", {}).get("query_score", 0),
                    data.get("channel_score", {}).get("score_exp", 0),
                    data.get("channel_score", {}).get("total_score", 0),
                    data.get("channel_score", {}).get("w1w_score", 0)
                ]
                ws.append(row)

            # 5. 调整列宽（优化显示）
            for col in ws.columns:
                max_length = 0
                column = col[0].column_letter
                for cell in col:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                ws.column_dimensions[column].width = adjusted_width

            # 6. 处理文件名和保存路径
            safe_keyword = re.sub(r'[\\/:*?"<>|]', '_', keyword)  # 替换非法文件名字符
            filename = f"微信搜索数据_{safe_keyword}_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"
            # 拼接data文件夹路径
            file_path = os.path.join(DATA_FOLDER, filename)

            # 7. 保存文件
            wb.save(file_path)
            wb.close()  # 显式关闭工作簿
            self._log(f"【{keyword}】Excel文件已保存：{os.path.abspath(file_path)}")
        except Exception as e:
            self._log(f"【{keyword}】Excel保存失败：{str(e)}")
            messagebox.showerror("错误", f"【{keyword}】Excel保存失败：{str(e)}")

    def execute_query(self):
        """执行多关键词查询，每个关键词生成独立Excel文件"""
        # 先保存配置（自动保存）
        self._save_config()

        # 获取所有输入参数
        openid = self.openid_entry.get().strip()
        search_key = self.search_key_entry.get().strip()
        start_ymd = self.start_ymd_entry.get().strip()
        end_ymd = self.end_ymd_entry.get().strip()

        # 解析关键词列表
        keywords = self._parse_keywords()
        if not keywords:
            messagebox.showwarning("警告", "请输入至少一个搜索词！")
            return

        # 清空日志
        self.log_text.delete(1.0, tk.END)
        self._log(f"开始执行查询...共解析到{len(keywords)}个关键词：{', '.join(keywords)}")
        self._log(
            f"参数：OpenID={openid or '空'}, SearchKey={search_key or '空'}, 时间范围={start_ymd}~{end_ymd}")

        # 记录成功导出的文件数
        success_count = 0

        # 循环处理每个关键词
        for idx, keyword in enumerate(keywords, 1):
            self._log(f"\n===== 开始查询第{idx}/{len(keywords)}个关键词：【{keyword}】 =====")

            # 构造请求体
            payload = {
                "openid": openid,
                "search_key": search_key,
                "cgi_name": "GetMultiChannel",
                "query": [keyword],
                "start_ymd": start_ymd,
                "end_ymd": end_ymd,
                "is_beta": 1
            }

            try:
                # 生成X-Request-Sign签名
                sign = self._generate_x_request_sign(search_key, [keyword])
                # 复制全局headers并添加签名（避免修改全局headers）
                request_headers = HEADERS.copy()
                if sign:
                    request_headers["X-Request-Sign"] = sign

                # 发送POST请求（使用全局会话 + 带签名的headers）
                self._log(f"正在为【{keyword}】发送请求...")
                response = REQUEST_SESSION.post(
                    URL,
                    data=json.dumps(payload),
                    headers=request_headers,  # 使用带签名的请求头
                    timeout=30  # 超时时间30秒
                )
                self._log(f"【{keyword}】响应状态码：{response.status_code}")

                # 解析响应
                response.raise_for_status()  # 抛出HTTP错误
                result = response.json()
                self._log(f"【{keyword}】响应数据预览：{json.dumps(result, ensure_ascii=False, indent=2)[:500]}...")

                # 提取目标数据
                result_list = result.get("content", {}).get("result_list", [])
                if not result_list:
                    self._log(f"【{keyword}】未找到有效数据")
                    continue

                self._log(f"【{keyword}】提取到{len(result_list)}条数据")

                # 为每条数据添加关键词标识
                for data in result_list:
                    data["keyword"] = keyword

                # 单个关键词导出独立Excel文件
                self._export_to_excel(result_list, keyword)
                success_count += 1

            except requests.exceptions.Timeout:
                self._log(f"【{keyword}】请求超时")
            except requests.exceptions.ConnectionError:
                self._log(f"【{keyword}】网络连接错误")
            except requests.exceptions.HTTPError as e:
                self._log(f"【{keyword}】HTTP错误：{e}")
            except json.JSONDecodeError:
                self._log(f"【{keyword}】响应数据不是有效的JSON格式")
            except Exception as e:
                self._log(f"【{keyword}】未知错误：{str(e)}")

        # 所有关键词查询完成
        self._log(f"\n===== 所有关键词查询完成 =====")
        self._log(f"成功导出 {success_count}/{len(keywords)} 个关键词的Excel文件")
        if success_count > 0:
            messagebox.showinfo("成功", f"共成功导出 {success_count} 个关键词的Excel文件！\n文件保存在：{os.path.abspath(DATA_FOLDER)}")
        else:
            messagebox.showwarning("提示", "没有关键词导出成功，请检查日志信息！")


if __name__ == "__main__":
    # 解决tkinter中文显示/高分屏适配问题（Windows）
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
        windll.user32.SetProcessDPIAware()
    except:
        pass

    # ========== 启动主程序 ==========
    # 优化tkinter启动速度（禁用高DPI缩放兼容）
    temp_root = tk.Tk()
    temp_root.wm_withdraw()  # 预加载tkinter核心组件
    temp_root.destroy()

    # 创建主窗口
    root = tk.Tk()
    app = WeChatSearchTool(root)

    # 启动主循环，结束后强制退出
    try:
        root.mainloop()
    finally:
        # 确保进程彻底退出
        os._exit(0)