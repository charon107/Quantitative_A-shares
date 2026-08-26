"""fetch_earnings_daily 每日增量脚本回归测试。"""
import importlib
import os
import sys

import pandas as pd

fetch_daily = importlib.import_module("scripts.fetch_earnings_daily")


def test_empty_reports_on_trading_day_warns_and_completes(tmp_path, monkeypatch, capsys):
    """交易日正式财报名单为空时应告警并继续，不应因回溯天数变量退出。"""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fetch_earnings_daily.py",
            "--outdir",
            str(tmp_path),
            "--date",
            "2026-08-25",
        ],
    )
    monkeypatch.setattr(fetch_daily.tsc, "configure_rate_limiter", lambda *args: None)
    monkeypatch.setattr(fetch_daily.tsc, "fetch_forecast_by_date", lambda date: pd.DataFrame())
    monkeypatch.setattr(fetch_daily.tsc, "fetch_disclosed_report_codes", lambda date, tail_days: [])
    monkeypatch.setattr(
        fetch_daily.tsc,
        "fetch_trade_dates",
        lambda start_date, end_date: [pd.Timestamp("2026-08-25")],
    )

    written = []
    monkeypatch.setattr(
        pd.DataFrame,
        "to_parquet",
        lambda self, path, index=False: written.append(os.path.basename(path)),
    )

    fetch_daily.main()

    captured = capsys.readouterr()
    assert "2026-08-25 为交易日但正式财报名单为空（含回溯 2 天）" in captured.err
    assert set(written) == {
        "fundamental.parquet",
        "fundamental_quarterly.parquet",
        "dividend.parquet",
        "forecast.parquet",
        "express.parquet",
    }
