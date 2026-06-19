# GitHub Actions + Hugging Face 每日数据同步指南

## 概览

该项目现已集成 GitHub Actions + Hugging Face 自动化数据管理方案：
- **每个工作日 UTC 23:30**（北京时间次日 07:30）自动运行一次数据更新
- 数据存储在 Hugging Face dataset 仓库（不进 git，规避体积限制）
- 增量更新：智能复用本地已有数据，避免全量重拉（~80 分钟耗时）

## 架构

```
GitHub Actions Workflow
├─ Download from HF        （从 HF 拉取上次的数据）
├─ Run stock_price.py      （本地增量/回补/全量更新）
└─ Upload to HF            （把更新后的全部数据推回 HF）
```

### 关键特性

1. **无本地状态依赖**：HF 充当远程持久化磁盘，每次容器创建都从 HF 下载完整状态
2. **智能增量**：`stock_price.py` 依赖本地 parquet 文件判断是否需要增量更新，download 步骤确保"本地状态"总是从 HF 恢复过来
3. **容错**：首次运行时 HF repo 可为空，脚本自动进入全量初始化（冷启动）
4. **幂等性**：重复执行不会重复上传相同的数据，HF commit 信息有时间戳区分

## 使用流程

### 1. GitHub 仓库配置

确保已推送到 GitHub 的 main 分支：
```bash
git push origin main
```

前往 GitHub 仓库 **Settings → Secrets and variables → Actions**，添加一个 secret：
- **Name**: `HF_TOKEN`
- **Value**: 你的 Hugging Face API token（需要对 dataset repo 有 Write 权限）

### 2. 定时触发（自动）

workflow 已配置为：
- **cron**: `30 23 * * 1-5`（工作日 UTC 23:30）
- **手动触发**: 可在 GitHub Actions 标签页点击 `workflow_dispatch` 手动运行（用于测试）

### 3. 本地测试

若要在本地验证整套流程：

```bash
# 设置 HF token
export HF_TOKEN="你的token"

# 模拟 download 步骤
uv run python -m src.data_collection.hf_sync download

# 手动跑数据更新脚本
uv run python src/data_collection/stock_price.py

# 模拟 upload 步骤
uv run python -m src.data_collection.hf_sync upload
```

## 数据目录结构

同步的文件夹：`股价数据_parquet_fq/`

```
股价数据_parquet_fq/
├── kline_fq/          （前复权日线数据，~3300+ 只股票）
│   ├── sh.601988.parquet
│   ├── sz.000001.parquet
│   └── ...
├── adj_factor/        （复权因子，~1000+ 只股票）
│   ├── sh.601988.parquet
│   ├── sz.000001.parquet
│   └── ...
└── state.json         （上次运行时间戳）
```

## 故障排查

### 症状：Action 每次都重新全量拉取（stats 中大量 INIT）

**原因**：download 步骤失败但被静默吞掉，导致本地没有旧数据文件

**检查**：
1. 确认 `HF_TOKEN` secret 正确设置
2. 确认 HF dataset repo ID 与 `src/data_collection/hf_sync.py` 中的 `REPO_ID` 一致
3. 查看 GitHub Actions 日志，download 步骤是否有错误信息

### 症状：Upload 步骤失败

**原因**：网络问题或 token 权限不足

**检查**：
1. 确保 token 有该 dataset repo 的 **Write** 权限
2. 本地用同样 token 和 REPO_ID 手动跑 `hf_sync upload` 测试
3. 查看 HF repo 页面，是否有之前成功的 commit（验证 repo 可访问）

### 症状：stock_price.py 执行报错（网络、baostock 连接等）

**容错机制**：`if: always()` 确保即使个别股票报错，也会上传已更新的部分

**手动恢复**：
```bash
# 本地手动跑一次更新
uv run python src/data_collection/stock_price.py
# 再手动上传
HF_TOKEN="token" uv run python -m src.data_collection.hf_sync upload
```

## 脚本说明

### `src/data_collection/hf_sync.py`

```python
python -m src.data_collection.hf_sync download  # 从 HF 下载数据
python -m src.data_collection.hf_sync upload    # 上传到 HF
```

**环境变量**：`HF_TOKEN`（必须）

**关键 API**：
- `snapshot_download()`：递归下载 HF repo 的完整文件夹
- `api.upload_folder()`：递归上传本地文件夹（覆盖式更新，不删除远端多出文件）

### `src/data_collection/stock_price.py`

既有的数据爬取脚本，无需修改。依赖相对路径 `BASE_DIR = "股价数据_parquet_fq"`，必须在项目根目录下运行。

## HF dataset repo 管理

### 首次设置（用户已完成）

1. 在 huggingface.co 创建 Dataset repo，如 `your-username/wechatnum-stock-price`（可设为 private）
2. Settings → Access Tokens，生成 fine-grained token，勾选该 repo 的 **Write** 权限
3. 复制 token → GitHub Settings → Secrets → 新增 `HF_TOKEN`
4. 本地首次 upload，把初始数据种上去

### 查看数据更新

在 HF repo 主页（如 https://huggingface.co/datasets/your-username/wechatnum-stock-price）：
- **Files and versions** 标签：看完整的文件列表和每个文件的最后更新时间
- **Commits** 标签：看自动生成的 commit 历史（含时间戳）

## 成本考虑

- **Hugging Face**：免费用户 dataset 存储无限制，free tier 够用
- **GitHub Actions**：公开仓库 Actions 免费，私有仓库每月 2000 分钟免费额度（这里每次最多 ~100 分钟，月度足够）
- **baostock 数据源**：免费，无需 token

## 后续优化（可选）

1. **增加监控告警**：在 workflow 中添加 Slack/钉钉通知，当 upload 失败时告警
2. **数据备份**：在 HF repo 上做定期 snapshot（按周/按月创建标签版本）
3. **版本管理**：用 HF dataset cards (README) 记录数据更新日志
4. **增量备份到其他存储**：若需要二级备份，可在 upload 后再上传到 S3/OSS 等

## 常见问题

**Q: 为什么用 Hugging Face 而不是 GitHub Releases？**
A: GitHub Releases 也有 100MB 单文件限制，且手动管理繁琐。HF 提供版本管理、访问控制、数据共享等一站式服务，对数据类项目更友好。

**Q: 数据会一直增长，HF 会不会满？**
A: 对于公开共享的学术/开源数据集，HF 通常给予更多空间。如果持续增长成问题，可定期清理（如只保留最近 2-3 年数据）或升级为 Pro/Org 账户。

**Q: 能否改变更新频率（如改为每天而不是工作日）？**
A: 可以，编辑 `.github/workflows/update_stock_data.yml` 的 cron 表达式：
```yaml
cron: "30 23 * * *"  # 每天 UTC 23:30
```
然后 push 到 GitHub 即可自动生效。

**Q: 能否用 GitHub Pages 或其他存储？**
A: GitHub 总大小限制 1GB（推荐），存数据不方便。AWS S3、Google Cloud Storage、Azure Blob 等云存储也可以，但需要额外配置 IAM 和管理成本。HF dataset 对此类用途最简洁。
