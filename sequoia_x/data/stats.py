"""从本地 SQLite 汇总控制台指标。"""

from sequoia_x.db import connect, db_exists, table_exists


def collect_dashboard_stats(db_path: str) -> dict:
    """从日 K 表汇总仪表盘指标，不触发同步。"""
    empty: dict = {
        "latest_trade_date": None,
        "symbol_count": 0,
        "latest_symbol_count": 0,
        "synced_records": 0,
        "invalid_rows": 0,
        "stale_symbols": 0,
        "correctness_rate": 100.0,
        "completeness_rate": 100.0,
        "duplicate_rate": 0.0,
        "success_rate": 100.0,
        "pending_alerts": 0,
        "tasks_today": 0,
        "trend": [],
    }
    if not db_exists(db_path):
        return empty

    with connect(db_path) as conn:
        if not table_exists(conn, "stock_daily"):
            return empty

        total = conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
        if not total:
            return empty

        latest = conn.execute("SELECT MAX(date) FROM stock_daily").fetchone()[0]
        symbol_count = conn.execute(
            "SELECT COUNT(DISTINCT symbol) FROM stock_daily"
        ).fetchone()[0]
        latest_count = conn.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE date = ?",
            (latest,),
        ).fetchone()[0]
        stale_symbols = max(int(symbol_count) - int(latest_count), 0)
        invalid_rows = conn.execute(
            """
            SELECT COUNT(*) FROM stock_daily
            WHERE high < low
               OR close > high
               OR close < low
               OR COALESCE(volume, 0) <= 0
               OR open IS NULL
               OR high IS NULL
               OR low IS NULL
               OR close IS NULL
            """
        ).fetchone()[0]
        trend_rows = conn.execute(
            """
            SELECT date, COUNT(*) AS n
            FROM stock_daily
            GROUP BY date
            ORDER BY date DESC
            LIMIT 14
            """
        ).fetchall()

    correctness = round((total - invalid_rows) / total * 100, 2)
    completeness = (
        round((symbol_count - stale_symbols) / symbol_count * 100, 2)
        if symbol_count
        else 100.0
    )
    trend = [
        {"date": row[0], "records": int(row[1])}
        for row in reversed(trend_rows)
    ]
    pending = int(invalid_rows) + int(stale_symbols)
    return {
        "latest_trade_date": latest,
        "symbol_count": int(symbol_count),
        "latest_symbol_count": int(latest_count),
        "synced_records": int(total),
        "invalid_rows": int(invalid_rows),
        "stale_symbols": int(stale_symbols),
        "correctness_rate": correctness,
        "completeness_rate": completeness,
        "duplicate_rate": 0.0,
        "success_rate": correctness,
        "pending_alerts": pending,
        "tasks_today": int(latest_count),
        "trend": trend,
    }


def list_data_alerts(db_path: str, limit: int = 200) -> dict:
    """列出缺最新交易日的股票和行情字段异常，供异常中心展示。"""
    empty = {"latest_trade_date": None, "stale": [], "invalid": []}
    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    if not db_exists(db_path):
        return empty

    with connect(db_path) as conn:
        if not table_exists(conn, "stock_daily"):
            return empty

        latest_row = conn.execute("SELECT MAX(date) FROM stock_daily").fetchone()
        latest = latest_row[0] if latest_row else None
        if not latest:
            return empty

        stale = conn.execute(
            """
            SELECT symbol, MAX(date) AS last_date, COUNT(*) AS bars
            FROM stock_daily
            GROUP BY symbol
            HAVING MAX(date) < ?
            ORDER BY last_date, symbol
            LIMIT ?
            """,
            (latest, limit),
        ).fetchall()
        invalid = conn.execute(
            """
            SELECT
                symbol,
                date,
                open,
                high,
                low,
                close,
                volume,
                CASE
                    WHEN high < low THEN '最高价低于最低价'
                    WHEN close > high OR close < low THEN '收盘价超出高低价区间'
                    WHEN COALESCE(volume, 0) <= 0 THEN '成交量无效'
                    ELSE '开高低收存在空值'
                END AS reason
            FROM stock_daily
            WHERE high < low
               OR close > high
               OR close < low
               OR COALESCE(volume, 0) <= 0
               OR open IS NULL
               OR high IS NULL
               OR low IS NULL
               OR close IS NULL
            ORDER BY date DESC, symbol
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return {
        "latest_trade_date": latest,
        "stale": [
            {
                "symbol": row[0],
                "last_date": row[1],
                "bars": int(row[2]),
                "reason": f"最新数据停在 {row[1]}，落后于市场最新交易日 {latest}",
            }
            for row in stale
        ],
        "invalid": [
            {
                "symbol": row[0],
                "date": row[1],
                "open": row[2],
                "high": row[3],
                "low": row[4],
                "close": row[5],
                "volume": row[6],
                "reason": row[7],
            }
            for row in invalid
        ],
    }
