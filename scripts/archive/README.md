# 已完成的一次性脚本（存档）

这里的脚本都已在生产库上跑完，保留仅为审计与回溯，**日常运维不需要执行**。

| 脚本 | 用途 | 状态 |
|---|---|---|
| `migrate_parquet_to_duckdb.py` | 最初从 `股价数据_parquet_fq/` 目录建 DuckDB | 已完成；依赖的 parquet 目录架构已废弃，实际不可再跑 |
| `migrate_schema_v2.py` | schema v2 迁移（删 `kline.adjustflag`、比率列 DOUBLE→FLOAT、建 `meta_kv`） | 已完成；幂等，版本号存 `meta_kv` |

需要重建库请走 `scripts/reingest_all.py` + `.github/workflows/deploy_db.yml`。
