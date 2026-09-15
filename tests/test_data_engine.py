"""数据引擎属性测试。"""

import sqlite3
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path

import pandas as pd
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine


def make_engine_in(tmp_dir: str) -> tuple[DataEngine, Settings]:
    """创建使用临时数据库的 DataEngine 实例。"""
    settings = Settings(
        db_path=str(Path(tmp_dir) / "test.db"),
        start_date="2024-01-01",
    )
    engine = DataEngine(settings)
    return engine, settings


# Property 4: (symbol, date) 唯一约束防止重复写入
@given(
    symbol=st.text(min_size=6, max_size=6, alphabet="0123456789"),
    trade_date=st.dates(min_value=date(2024, 1, 1), max_value=date(2025, 12, 31)),
)
@h_settings(max_examples=50, deadline=None)
def test_unique_symbol_date_constraint(symbol: str, trade_date: date) -> None:
    """相同 (symbol, date) 插入两次，数据库中该组合记录数应保持为 1。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        row = {
            "symbol": symbol, "date": str(trade_date),
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
            "volume": 1000.0, "turnover": 10500.0,
        }
        df = pd.DataFrame([row])
        with closing(sqlite3.connect(engine.db_path)) as conn:
            df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")
            try:
                df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")
            except sqlite3.IntegrityError:
                pass
            count = conn.execute(
                "SELECT COUNT(*) FROM stock_daily WHERE symbol=? AND date=?",
                (symbol, str(trade_date)),
            ).fetchone()[0]
        assert count == 1


def test_upsert_updates_existing_row_without_deleting_other_rows() -> None:
    """增量同步更新同一交易日时，不应删除本批次未返回的其他股票。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        original = pd.DataFrame([
            {
                "symbol": "000001", "date": "2025-01-02",
                "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
                "volume": 1000.0, "turnover": 10500.0,
            },
            {
                "symbol": "000002", "date": "2025-01-02",
                "open": 20.0, "high": 21.0, "low": 19.0, "close": 20.5,
                "volume": 2000.0, "turnover": 41000.0,
            },
        ])
        engine._upsert_daily_data(original)

        update = original.iloc[[0]].copy()
        update.loc[:, "close"] = 12.5
        engine._upsert_daily_data(update)

        with closing(sqlite3.connect(engine.db_path)) as conn:
            rows = conn.execute(
                "SELECT symbol, close FROM stock_daily WHERE date=? ORDER BY symbol",
                ("2025-01-02",),
            ).fetchall()

        assert rows == [("000001", 12.5), ("000002", 20.5)]


def test_get_recent_ohlcv_returns_chronological_tail() -> None:
    """最近行情查询应只返回指定条数，并保持日期升序。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        engine, _ = make_engine_in(tmp_dir)
        rows = []
        for day in range(1, 6):
            rows.append({
                "symbol": "000001", "date": f"2025-01-0{day}",
                "open": 10.0, "high": 11.0, "low": 9.0, "close": float(day),
                "volume": 1000.0, "turnover": 10500.0,
            })
        engine._upsert_daily_data(pd.DataFrame(rows))

        result = engine.get_recent_ohlcv("000001", limit=3)

        assert result["date"].tolist() == ["2025-01-03", "2025-01-04", "2025-01-05"]
