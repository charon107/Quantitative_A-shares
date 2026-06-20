# 部署指南 — A股股价看板

## 前置条件

- Linux 服务器（Ubuntu 20.04+ 或类似）
- Python 3.11+
- `uv` 包管理工具（https://docs.astral.sh/uv/）
- 公网 IP 为 `47.109.138.67` 或其他目标服务器

## 部署步骤

### 1. SSH 到服务器

```bash
ssh user@47.109.138.67
```

### 2. 克隆项目

```bash
git clone https://github.com/YourUsername/wechatnum.git
cd wechatnum/WechatNum
```

### 3. 安装依赖

```bash
uv sync --all-extras
```

### 4. 配置环境变量

设置 Hugging Face token（用于从 HF dataset 下载数据）：

```bash
export HF_TOKEN="hf_YOUR_TOKEN_HERE"
```

或永久保存到 `/etc/environment` 或 `.bashrc`：

```bash
echo 'export HF_TOKEN="hf_YOUR_TOKEN_HERE"' >> ~/.bashrc
source ~/.bashrc
```

### 5. 手动测试数据拉取

```bash
uv run python -m src.data_collection.hf_sync download
```

应该看到：
```
[hf_sync] 下载数据从 Charon107/stock-price ...
[hf_sync] 下载完成，本地目录: 股价数据_parquet_fq
```

### 6. 手动测试看板

```bash
uv run streamlit run src/visualization/dashboard.py --server.port 8501
```

在浏览器打开 `http://47.109.138.67:8501`，应该看到 4 个 tabs（大盘概览、个股查询、排行榜、数据状态）。

### 7. 配置 systemd 自启动

复制 systemd 单元文件到系统目录：

```bash
sudo cp deploy/dashboard.service /etc/systemd/system/
sudo cp deploy/refresh_data.timer /etc/systemd/system/
sudo cp deploy/refresh_data.sh /etc/systemd/system/refresh_data.service
```

**注意**：`refresh_data.sh` 需要作为 service 文件被 timer 调用。创建一个对应的 `.service` 文件：

```bash
sudo cat > /etc/systemd/system/refresh_data.service <<EOF
[Unit]
Description=Refresh stock data from Hugging Face
After=network.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/home/wechatnum/Project/wechatnum/WechatNum
Environment="HF_TOKEN=${HF_TOKEN}"
ExecStart=/home/wechatnum/Project/wechatnum/WechatNum/deploy/refresh_data.sh

[Install]
WantedBy=multi-user.target
EOF
```

### 8. 启动 systemd 服务

```bash
# 重新加载 systemd 配置
sudo systemctl daemon-reload

# 启动看板服务（持久运行）
sudo systemctl start dashboard
sudo systemctl enable dashboard

# 启动定时器（每日 09:00 运行 refresh_data）
sudo systemctl start refresh_data.timer
sudo systemctl enable refresh_data.timer
```

### 9. 验证服务状态

```bash
# 查看看板服务状态
sudo systemctl status dashboard

# 查看定时器状态
sudo systemctl status refresh_data.timer

# 查看定时器的下一次运行时间
systemctl list-timers refresh_data.timer
```

### 10. 防火墙配置

确保 8501 端口对外开放：

```bash
# 如果使用 UFW
sudo ufw allow 8501

# 如果使用 iptables
sudo iptables -A INPUT -p tcp --dport 8501 -j ACCEPT
```

## 故障排查

### 看板无法启动

```bash
# 查看日志
sudo journalctl -u dashboard -f

# 检查 8501 端口是否已被占用
sudo lsof -i :8501
```

### 数据拉取失败

```bash
# 手动运行并查看错误
HF_TOKEN="your_token" uv run python -m src.data_collection.hf_sync download

# 检查 HF_TOKEN 是否正确
echo $HF_TOKEN
```

### 定时器未触发

```bash
# 查看 timer 日志
sudo journalctl -u refresh_data.timer -f

# 手动触发一次（用于测试）
sudo systemctl start refresh_data.service
```

## 访问看板

部署完成后，访问：

```
http://47.109.138.67:8501
```

4 个 tabs：
- **大盘概览**：市场宽度、等权指数、涨停/跌停走势
- **个股查询**：搜索个股、K线图、波动率曲线
- **排行榜**：涨幅/跌幅/成交额排行 Top10
- **数据状态**：数据新鲜度、覆盖范围

## 更新代码

当项目有新更新时，**一键更新**（拉取代码 → 按需 `uv sync` → 重启服务 → 打印状态）：

```bash
bash /home/wechatnum/Project/wechatnum/WechatNum/deploy/update.sh
```

> 脚本仅在 `pyproject.toml` / `uv.lock` 变更时才运行 `uv sync`，否则跳过以加快更新。

手动等价步骤（如需逐步排查）：

```bash
cd /home/wechatnum/Project/wechatnum/WechatNum
git pull origin main
uv sync          # 依赖无变更时可省略
sudo systemctl restart dashboard
```

## 备注

- 看板每日自动从 Hugging Face 拉取最新数据（北京时间 09:00）
- 数据是前复权日线行情，覆盖沪深主板全部股票（3300+）
- 数据缓存时间为 1 小时（减少重复计算等权指数等）

---

需要帮助？检查 `systemctl status dashboard` 或 `journalctl -u dashboard -f` 的日志。
