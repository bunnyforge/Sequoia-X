"""股票池增删 + 增量/全量日 K 同步。"""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Callable

from sequoia_x.db import column_names, connect, db_exists, ensure_parent_dir, table_exists

_UPSERT_SQL = """
INSERT INTO stock_daily
    (symbol, date, open, high, low, close, volume, turnover)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, date) DO UPDATE SET
    open = excluded.open,
    high = excluded.high,
    low = excluded.low,
    close = excluded.close,
    volume = excluded.volume,
    turnover = excluded.turnover
"""

_CREATE_STATE_SQL = """
CREATE TABLE IF NOT EXISTS stock_sync_state (
    symbol        TEXT PRIMARY KEY,
    last_date     TEXT NOT NULL,
    first_date    TEXT,
    failed_date   TEXT,
    failed_reason TEXT
)
"""

_UPSERT_STATE_SQL = """
INSERT INTO stock_sync_state (symbol, last_date, first_date, failed_date, failed_reason)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(symbol) DO UPDATE SET
    last_date = MAX(stock_sync_state.last_date, excluded.last_date),
    first_date = CASE
        WHEN stock_sync_state.first_date IS NULL THEN excluded.first_date
        WHEN excluded.first_date IS NULL THEN stock_sync_state.first_date
        ELSE MIN(stock_sync_state.first_date, excluded.first_date)
    END,
    failed_date = excluded.failed_date,
    failed_reason = excluded.failed_reason
"""

_thread_state = threading.local()
_write_lock = threading.Lock()
_sync_state_lock = threading.Lock()
_sync_state_ready: set[str] = set()
_writer_lock = threading.Lock()
_writers: dict[str, "_WriteCoordinator"] = {}
_writer_refs: dict[str, int] = {}

# Flush when enough symbols or rows are queued, or after a short idle.
_WRITE_BATCH_ITEMS = 24
_WRITE_BATCH_ROWS = 4_000
_WRITE_FLUSH_IDLE_S = 0.04


def _to_baostock_code(symbol: str) -> str:
    prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
    return f"{prefix}.{symbol}"


def _to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _day_before(value: str) -> str:
    return (date.fromisoformat(value) - timedelta(days=1)).strftime("%Y-%m-%d")


def ensure_sync_state(db_path: str) -> None:
    """维护每只股票的首尾日期，避免每次扫描全部日 K。"""
    if db_path in _sync_state_ready:
        return
    with _sync_state_lock:
        if db_path in _sync_state_ready:
            return
        ensure_parent_dir(db_path)
        with connect(db_path, immediate=True) as conn:
            conn.execute(_CREATE_STATE_SQL)
            columns = column_names(conn, "stock_sync_state")
            if "first_date" not in columns:
                conn.execute("ALTER TABLE stock_sync_state ADD COLUMN first_date TEXT")
            if "failed_date" not in columns:
                conn.execute("ALTER TABLE stock_sync_state ADD COLUMN failed_date TEXT")
            if "failed_reason" not in columns:
                conn.execute("ALTER TABLE stock_sync_state ADD COLUMN failed_reason TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_state_last_date "
                "ON stock_sync_state (last_date)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_state_failed "
                "ON stock_sync_state (failed_date) WHERE failed_date IS NOT NULL"
            )
            count = conn.execute("SELECT COUNT(*) FROM stock_sync_state").fetchone()[0]
            if count == 0 and table_exists(conn, "stock_daily"):
                conn.execute(
                    """
                    INSERT INTO stock_sync_state (symbol, last_date, first_date)
                    SELECT symbol, MAX(date), MIN(date) FROM stock_daily GROUP BY symbol
                    """
                )
            elif count > 0 and table_exists(conn, "stock_daily"):
                conn.execute(
                    """
                    UPDATE stock_sync_state
                    SET first_date = (
                        SELECT MIN(date) FROM stock_daily
                        WHERE stock_daily.symbol = stock_sync_state.symbol
                    )
                    WHERE first_date IS NULL
                    """
                )
        _sync_state_ready.add(db_path)


def _latest_known_date(db_path: str) -> str | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT MAX(last_date) FROM stock_sync_state").fetchone()
    return row[0] if row and row[0] else None


def first_trading_on_or_after(start_date: str) -> str:
    """起始日当天或之后的第一个交易日；失败则退回 start_date。"""
    end = (date.fromisoformat(start_date) + timedelta(days=20)).strftime("%Y-%m-%d")
    try:
        import baostock as bs
    except ImportError:
        return start_date
    login = bs.login()
    if login.error_code != "0":
        return start_date
    try:
        rs = bs.query_trade_dates(start_date=start_date, end_date=end)
        if rs.error_code != "0":
            return start_date
        while rs.next():
            row = rs.get_row_data()
            if len(row) >= 2 and str(row[1]) == "1":
                return str(row[0])
        return start_date
    finally:
        bs.logout()


def last_trading_day(today: str) -> str:
    """不晚于 today 的最近交易日；日历接口失败时退回 today。"""
    try:
        import baostock as bs
    except ImportError:
        return today

    start = (date.fromisoformat(today) - timedelta(days=14)).strftime("%Y-%m-%d")
    login = bs.login()
    if login.error_code != "0":
        return today
    try:
        rs = bs.query_trade_dates(start_date=start, end_date=today)
        if rs.error_code != "0":
            return today
        latest = today
        found = False
        while rs.next():
            row = rs.get_row_data()
            if len(row) >= 2 and str(row[1]) == "1":
                latest = str(row[0])
                found = True
        return latest if found else today
    finally:
        bs.logout()


def fetch_listed_symbols() -> list[str]:
    """当前上市 A 股代码。"""
    import baostock as bs

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"baostock 登录失败：{login.error_msg}")
    try:
        rs = bs.query_stock_basic(code_name="", code="")
        symbols: list[str] = []
        while rs.next():
            row = rs.get_row_data()
            code = row[0]
            status = row[4]
            stock_type = row[5]
            if status == "1" and stock_type == "1" and "." in code:
                symbols.append(code.split(".")[1])
        if not symbols:
            raise RuntimeError("未获取到上市股票列表")
        return symbols
    finally:
        bs.logout()


def apply_universe(db_path: str, listed: set[str], start_date: str) -> dict:
    """按最新股票池新增（从起始日前一天起算）并删除已退市代码。"""
    if not listed:
        raise RuntimeError("股票列表为空，已跳过增删以免误删本地数据")
    ensure_sync_state(db_path)
    seed = _day_before(start_date)
    with connect(db_path, immediate=True) as conn:
        local = {
            row[0]
            for row in conn.execute("SELECT symbol FROM stock_sync_state")
        }
        local.update(
            row[0]
            for row in conn.execute("SELECT DISTINCT symbol FROM stock_daily")
        )
        added = sorted(listed - local)
        removed = sorted(local - listed)
        for symbol in added:
            conn.execute(
                """
                INSERT INTO stock_sync_state (symbol, last_date, first_date, failed_date, failed_reason)
                VALUES (?, ?, NULL, NULL, NULL)
                ON CONFLICT(symbol) DO NOTHING
                """,
                (symbol, seed),
            )
        for symbol in removed:
            conn.execute("DELETE FROM stock_daily WHERE symbol = ?", (symbol,))
            conn.execute("DELETE FROM stock_sync_state WHERE symbol = ?", (symbol,))
        conn.commit()
    return {
        "listed": len(listed),
        "added": len(added),
        "removed": len(removed),
        "added_symbols": added[:30],
        "removed_symbols": removed[:30],
    }


def _range_for_symbol(last_date: str, target_date: str, failed_date: str | None) -> tuple[str, str] | None:
    """返回 (start, end)。失败日之后不再拉，只重试失败当天。"""
    end = target_date
    if failed_date:
        end = min(end, failed_date)
    start = (date.fromisoformat(str(last_date)) + timedelta(days=1)).strftime("%Y-%m-%d")
    if start <= end:
        return start, end
    if failed_date and failed_date <= target_date:
        return failed_date, failed_date
    return None


def build_missing_tasks(db_path: str, target_date: str) -> list[tuple[str, str, str]]:
    """增量：只补 last_date 落后于目标日的股票；失败票只重试失败日。"""
    ensure_sync_state(db_path)
    with connect(db_path) as conn:
        columns = column_names(conn, "stock_sync_state")
        failed_sql = "failed_date" if "failed_date" in columns else "NULL"
        rows = conn.execute(
            f"""
            SELECT symbol, last_date, {failed_sql}
            FROM stock_sync_state
            WHERE last_date < ? OR {failed_sql} IS NOT NULL
            ORDER BY last_date, symbol
            """,
            (target_date,),
        ).fetchall()
    tasks: list[tuple[str, str, str]] = []
    for symbol, last_date, failed_date in rows:
        rng = _range_for_symbol(str(last_date), target_date, str(failed_date) if failed_date else None)
        if rng:
            tasks.append((str(symbol), rng[0], rng[1]))
    return tasks


def build_full_tasks(
    db_path: str,
    start_date: str,
    target_date: str,
    first_open: str | None = None,
) -> list[tuple[str, str, str]]:
    """全量：从起始日补缺口；失败票截断到失败日，不往后写。"""
    ensure_sync_state(db_path)
    history_from = first_open or start_date
    with connect(db_path) as conn:
        columns = column_names(conn, "stock_sync_state")
        failed_sql = "failed_date" if "failed_date" in columns else "NULL"
        rows = conn.execute(
            f"""
            SELECT symbol, last_date, first_date, {failed_sql}
            FROM stock_sync_state
            ORDER BY symbol
            """
        ).fetchall()
    tasks: list[tuple[str, str, str]] = []
    for symbol, last_date, first_date, failed_date in rows:
        fail = str(failed_date) if failed_date else None
        needs_history = first_date is None or str(first_date) > history_from
        needs_tail = str(last_date) < target_date or fail is not None
        if not needs_history and not needs_tail:
            continue
        start = history_from if needs_history else (
            date.fromisoformat(str(last_date)) + timedelta(days=1)
        ).strftime("%Y-%m-%d")
        end = min(target_date, fail) if fail else target_date
        if start > end and fail:
            start, end = fail, fail
        if start > end:
            continue
        tasks.append((str(symbol), start, end))
    return tasks


def _session():
    import baostock as bs

    if getattr(_thread_state, "ready", False):
        return bs
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"baostock 登录失败：{login.error_msg}")
    _thread_state.ready = True
    _thread_state.since = 0
    return bs


def _reset_session() -> None:
    if not getattr(_thread_state, "ready", False):
        return
    import baostock as bs

    try:
        bs.logout()
    except Exception:
        pass
    _thread_state.ready = False


def _fetch_symbol(symbol: str, start: str, end: str) -> list[tuple]:
    bs = _session()
    _thread_state.since = getattr(_thread_state, "since", 0) + 1
    if _thread_state.since >= 200:
        _reset_session()
        bs = _session()
    rs = bs.query_history_k_data_plus(
        _to_baostock_code(symbol),
        "date,open,high,low,close,volume,amount",
        start_date=start,
        end_date=end,
        frequency="d",
        adjustflag="1",
    )
    if rs.error_code != "0":
        raise RuntimeError(rs.error_msg or "baostock 查询失败")
    rows: list[tuple] = []
    while rs.next():
        raw = rs.get_row_data()
        open_, high, low, close, volume, turnover = (
            _to_float(raw[1]),
            _to_float(raw[2]),
            _to_float(raw[3]),
            _to_float(raw[4]),
            _to_float(raw[5]),
            _to_float(raw[6]),
        )
        if close is None or volume is None or volume <= 0:
            continue
        rows.append((symbol, raw[0], open_, high, low, close, volume, turnover))
    return rows


def fetch_trading_days(start_date: str, end_date: str) -> list[str]:
    """闭区间内的交易日；接口失败则返回空列表（此时不按缺口截断）。"""
    try:
        import baostock as bs
    except ImportError:
        return []
    login = bs.login()
    if login.error_code != "0":
        return []
    try:
        rs = bs.query_trade_dates(start_date=start_date, end_date=end_date)
        if rs.error_code != "0":
            return []
        days: list[str] = []
        while rs.next():
            row = rs.get_row_data()
            if len(row) >= 2 and str(row[1]) == "1":
                days.append(str(row[0]))
        return days
    finally:
        bs.logout()


def take_consecutive_bars(
    rows: list[tuple],
    start: str,
    end: str,
    trading_days: list[str],
) -> tuple[list[tuple], str | None]:
    """按交易日连续写入；某一天没有有效 K 线则丢掉后面的天，返回缺口日。"""
    if not trading_days:
        return rows, None
    by_date = {str(item[1]): item for item in rows}
    kept: list[tuple] = []
    for day in trading_days:
        if day < start:
            continue
        if day > end:
            break
        bar = by_date.get(day)
        if bar is None:
            return kept, day
        kept.append(bar)
    return kept, None


def _row_bounds(rows: list[tuple]) -> dict[str, tuple[str, str]]:
    bounds: dict[str, tuple[str, str]] = {}
    for item_symbol, trade_date, *_rest in rows:
        day = str(trade_date)
        previous = bounds.get(item_symbol)
        if previous is None:
            bounds[item_symbol] = (day, day)
        else:
            last_date, first_date = previous
            bounds[item_symbol] = (max(last_date, day), min(first_date, day))
    return bounds


def _apply_upserts(conn, items: list[dict]) -> None:
    """Write many symbol upserts in one IMMEDIATE transaction."""
    all_rows: list[tuple] = []
    state_rows: list[tuple] = []
    clear_fail: list[str] = []
    empty_fail: list[tuple] = []
    for item in items:
        rows = item["rows"]
        symbol = item["symbol"]
        failed_date = item["failed_date"]
        failed_reason = item["failed_reason"]
        if rows:
            all_rows.extend(rows)
            for code, (last_date, first_date) in _row_bounds(rows).items():
                state_rows.append((code, last_date, first_date, failed_date, failed_reason))
        else:
            empty_fail.append((failed_date, failed_reason, symbol))
        if failed_date is None:
            clear_fail.append(symbol)
    if all_rows:
        conn.executemany(_UPSERT_SQL, all_rows)
    if state_rows:
        conn.executemany(_UPSERT_STATE_SQL, state_rows)
    for failed_date, failed_reason, symbol in empty_fail:
        conn.execute(
            """
            UPDATE stock_sync_state
            SET failed_date = ?, failed_reason = ?
            WHERE symbol = ?
            """,
            (failed_date, failed_reason, symbol),
        )
    for symbol in clear_fail:
        conn.execute(
            """
            UPDATE stock_sync_state
            SET failed_date = NULL, failed_reason = NULL
            WHERE symbol = ?
            """,
            (symbol,),
        )


def _upsert_now(
    db_path: str,
    rows: list[tuple],
    *,
    symbol: str,
    failed_date: str | None,
    failed_reason: str | None,
) -> int:
    with _write_lock:
        with connect(db_path, immediate=True) as conn:
            _apply_upserts(
                conn,
                [
                    {
                        "rows": rows,
                        "symbol": symbol,
                        "failed_date": failed_date,
                        "failed_reason": failed_reason,
                    }
                ],
            )
            conn.commit()
    return len(rows)


class _WriteCoordinator:
    """Single writer thread: fetch workers never hold the write lock during I/O,
    and many symbols share one COMMIT (fewer WAL frames / busy wakes)."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._loop, name="sequoia-sqlite-writer", daemon=True
        )
        self._thread.start()

    def upsert(
        self,
        rows: list[tuple],
        *,
        symbol: str,
        failed_date: str | None,
        failed_reason: str | None,
    ) -> int:
        done = threading.Event()
        box: dict = {}
        self._queue.put(
            {
                "rows": rows,
                "symbol": symbol,
                "failed_date": failed_date,
                "failed_reason": failed_reason,
                "done": done,
                "box": box,
            }
        )
        if not done.wait(timeout=120):
            raise TimeoutError(f"SQLite 写入超时：{symbol}")
        if "exc" in box:
            raise box["exc"]
        return int(box.get("n") or 0)

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=60)

    def _loop(self) -> None:
        batch: list[dict] = []
        stopping = False
        while not stopping:
            timeout = _WRITE_FLUSH_IDLE_S if batch else None
            try:
                item = self._queue.get(timeout=timeout)
            except queue.Empty:
                self._flush(batch)
                batch = []
                continue
            if item is None:
                stopping = True
            else:
                batch.append(item)
            if stopping or _should_flush(batch):
                self._flush(batch)
                batch = []
        self._flush(batch)

    def _flush(self, batch: list[dict]) -> None:
        if not batch:
            return
        try:
            with _write_lock:
                with connect(self.db_path, immediate=True) as conn:
                    _apply_upserts(conn, batch)
                    conn.commit()
        except Exception as exc:
            for item in batch:
                item["box"]["exc"] = exc
                item["done"].set()
            return
        for item in batch:
            item["box"]["n"] = len(item["rows"])
            item["done"].set()


def _should_flush(batch: list[dict]) -> bool:
    if len(batch) >= _WRITE_BATCH_ITEMS:
        return True
    rows = sum(len(item["rows"]) for item in batch)
    return rows >= _WRITE_BATCH_ROWS


def _upsert(
    db_path: str,
    rows: list[tuple],
    *,
    symbol: str,
    failed_date: str | None,
    failed_reason: str | None,
) -> int:
    with _writer_lock:
        coordinator = _writers.get(db_path)
    if coordinator is not None:
        return coordinator.upsert(
            rows, symbol=symbol, failed_date=failed_date, failed_reason=failed_reason
        )
    return _upsert_now(
        db_path,
        rows,
        symbol=symbol,
        failed_date=failed_date,
        failed_reason=failed_reason,
    )


def _acquire_writer(db_path: str) -> "_WriteCoordinator":
    with _writer_lock:
        refs = _writer_refs.get(db_path, 0)
        if refs == 0:
            _writers[db_path] = _WriteCoordinator(db_path)
        _writer_refs[db_path] = refs + 1
        return _writers[db_path]


def _release_writer(db_path: str) -> None:
    with _writer_lock:
        refs = _writer_refs.get(db_path, 0) - 1
        if refs <= 0:
            writer = _writers.pop(db_path, None)
            _writer_refs.pop(db_path, None)
        else:
            _writer_refs[db_path] = refs
            writer = None
    if writer is not None:
        writer.close()


def _sync_one(
    db_path: str,
    symbol: str,
    start: str,
    end: str,
    trading_days: list[str],
    sleep_seconds: int = 0,
) -> tuple[str, int, str | None]:
    try:
        raw = _fetch_symbol(symbol, start, end)
        kept, gap = take_consecutive_bars(raw, start, end, trading_days)
        if gap:
            reason = f"{symbol} 在 {gap} 无有效日K，已停止后续更新"
            written = _upsert(
                db_path,
                kept,
                symbol=symbol,
                failed_date=gap,
                failed_reason=reason,
            )
            return symbol, written, reason
        written = _upsert(
            db_path,
            kept,
            symbol=symbol,
            failed_date=None,
            failed_reason=None,
        )
        return symbol, written, None
    except Exception as exc:
        reason = str(exc)
        _upsert(
            db_path,
            [],
            symbol=symbol,
            failed_date=start,
            failed_reason=reason,
        )
        return symbol, 0, reason
    finally:
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)


def _resolve_target(db_path: str) -> str:
    today = date.today().strftime("%Y-%m-%d")
    target = last_trading_day(today)
    known = _latest_known_date(db_path)
    if known and target < known:
        return known
    return target


def _run_tasks(
    db_path: str,
    tasks: list[tuple[str, str, str]],
    trading_days: list[str],
    on_progress: Callable[[int, int, str], None] | None,
    concurrency: int = 8,
    sleep_seconds: int = 0,
) -> tuple[int, list[dict]]:
    written = 0
    failed: list[dict] = []
    if not tasks:
        return written, failed
    workers = max(1, min(int(concurrency), len(tasks)))
    completed = 0
    if on_progress:
        on_progress(0, len(tasks), "")
    _acquire_writer(db_path)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _sync_one, db_path, symbol, start, end, trading_days, sleep_seconds
                ): symbol
                for symbol, start, end in tasks
            }
            for future in as_completed(futures):
                symbol, count, error = future.result()
                completed += 1
                written += count
                if error:
                    failed.append({"symbol": symbol, "error": error})
                if on_progress:
                    on_progress(completed, len(tasks), symbol)
    finally:
        _release_writer(db_path)
        _reset_session()
    return written, failed


def run_market_sync(
    db_path: str,
    mode: str = "incremental",
    start_date: str = "2024-01-01",
    on_progress: Callable[[int, int, str], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    concurrency: int = 8,
    sleep_seconds: int = 0,
) -> dict:
    """先同步股票池，再按增量或全量补日 K。"""
    if not db_exists(db_path):
        raise FileNotFoundError(f"数据库不存在：{db_path}")
    if mode not in {"incremental", "full"}:
        raise ValueError(f"未知同步模式：{mode}")
    try:
        import baostock as bs  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("未安装 baostock，无法从行情源同步") from exc

    ensure_sync_state(db_path)
    if on_status:
        on_status("正在同步股票列表")
    universe = apply_universe(db_path, set(fetch_listed_symbols()), start_date)
    target = _resolve_target(db_path)
    first_open = first_trading_on_or_after(start_date) if mode == "full" else start_date
    if mode == "full":
        tasks = build_full_tasks(db_path, start_date, target, first_open=first_open)
        label = "全量"
    else:
        tasks = build_missing_tasks(db_path, target)
        label = "增量"

    if on_status:
        on_status(
            f"股票池 +{universe['added']}/-{universe['removed']}，{label}待拉 {len(tasks)} 只"
        )
    calendar_start = min((item[1] for item in tasks), default=start_date)
    trading_days = fetch_trading_days(calendar_start, target)
    written, failed = _run_tasks(
        db_path,
        tasks,
        trading_days,
        on_progress,
        concurrency=concurrency,
        sleep_seconds=sleep_seconds,
    )
    return {
        "written": written,
        "targets": len(tasks),
        "failed": failed,
        "target_date": target,
        "mode": mode,
        "universe": universe,
        "message": (
            f"{label}同步到 {target}：股票池 +{universe['added']}/-{universe['removed']}，"
            f"写入 {written} 条，目标 {len(tasks)} 只，失败 {len(failed)} 只"
        ),
    }


def run_incremental_sync(
    db_path: str,
    on_progress: Callable[[int, int, str], None] | None = None,
    start_date: str = "2024-01-01",
    on_status: Callable[[str], None] | None = None,
    concurrency: int = 8,
    sleep_seconds: int = 0,
) -> dict:
    return run_market_sync(
        db_path,
        mode="incremental",
        start_date=start_date,
        on_progress=on_progress,
        on_status=on_status,
        concurrency=concurrency,
        sleep_seconds=sleep_seconds,
    )
