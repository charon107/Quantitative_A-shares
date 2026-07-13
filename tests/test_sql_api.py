"""只读 SQL 网关（POST /api/sql）测试：鉴权、只读防线、外部访问封锁、行数截断。"""
import os

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from src import config, db
from src.api.main import app

client = TestClient(app)

TOKEN = "test-token-abc123"


def _post(sql: str, token: str | None = TOKEN):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/api/sql", json={"sql": sql}, headers=headers)


def _read_arrow(resp) -> pa.Table:
    with pa.ipc.open_stream(resp.content) as reader:
        return reader.read_all()


@pytest.fixture
def sql_token(monkeypatch):
    monkeypatch.setattr(config, "SQL_API_TOKEN", TOKEN)


def test_endpoint_hidden_without_token_config(duck, monkeypatch):
    """未配置 SQL_API_TOKEN 时端点关闭（404），安全默认。"""
    monkeypatch.setattr(config, "SQL_API_TOKEN", "")
    assert _post("SELECT 1").status_code == 404


def test_wrong_token_rejected(duck, sql_token):
    assert _post("SELECT 1", token="bad-token").status_code == 403
    assert _post("SELECT 1", token=None).status_code == 403


def test_select_returns_arrow(duck, sql_token):
    resp = _post("SELECT code, COUNT(*) AS n FROM kline GROUP BY code ORDER BY code")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/vnd.apache.arrow.stream")
    tbl = _read_arrow(resp)
    assert tbl.column_names == ["code", "n"]
    assert tbl.num_rows == 3  # 合成夹具 3 只股票
    assert resp.headers["X-Truncated"] == "false"


def test_write_rejected_by_readonly(duck, sql_token):
    """INSERT 被只读连接拒绝（400），且库未被改动。"""
    resp = _post("INSERT INTO stock_meta VALUES ('sh.999999', '不存在')")
    assert resp.status_code == 400
    rows = db.query_df("SELECT COUNT(*) AS n FROM stock_meta WHERE code='sh.999999'")
    assert int(rows["n"].iloc[0]) == 0


def test_copy_to_file_rejected(duck, sql_token, tmp_path):
    """COPY TO 服务器文件被 enable_external_access=false 拦截（只读连接防不了这类）。"""
    out = str(tmp_path / "leak.parquet").replace("\\", "/")
    resp = _post(f"COPY (SELECT 1) TO '{out}' (FORMAT PARQUET)")
    assert resp.status_code == 400
    assert not os.path.exists(out)


def test_rows_truncated_at_limit(duck, sql_token, monkeypatch):
    monkeypatch.setattr(config, "SQL_MAX_ROWS", 50)
    resp = _post("SELECT * FROM kline ORDER BY code, date")  # 夹具共 120 行
    assert resp.status_code == 200
    assert resp.headers["X-Truncated"] == "true"
    assert _read_arrow(resp).num_rows == 50
