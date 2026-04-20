"""
fetch_data.py

market-data skill から呼ばれる、または単独で実行するデータ取得スクリプト。
data/watchlist.csv の銘柄を yfinance で取得して:
  1. data/raw/YYYYMMDD/ に CSV 保存（既存動作を維持）
  2. DB の ohlcv テーブルに差分保存（DB 接続可能な場合）

差分取得:
  初回       : yfinance で --days 分を全取得 -> ohlcv に保存
  2回目以降  : ohlcv の最終日付を確認 -> 不足分だけ yfinance で取得して追記

Usage:
    python scripts/fetch_data.py
    python scripts/fetch_data.py --date 20240415
    python scripts/fetch_data.py --tickers 7203.T 9984.T --days 60
    python scripts/fetch_data.py --watchlist data/watchlist_extended.csv
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


# ── 依存チェック ───────────────────────────────────────────────────────────────

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("[fetch_data] yfinance / pandas が未インストールです。", file=sys.stderr)
    print("  pip install yfinance pandas", file=sys.stderr)
    sys.exit(1)

# DB は optional: psycopg2 未インストールの場合は CSV のみ保存
sys.path.insert(0, str(Path(__file__).parent))
_DB_AVAILABLE = False
try:
    from db import get_conn, get_cursor
    _DB_AVAILABLE = True
except ImportError:
    pass


# ── 定数 ──────────────────────────────────────────────────────────────────────

DEFAULT_WATCHLIST = "data/watchlist.csv"
DEFAULT_DAYS      = 90
MAX_RETRIES       = 3
RATE_LIMIT_SLEEP  = 0.5

_DELISTED_KEYWORDS = ["possibly delisted", "no timezone found", "not found", "404"]


# ── ログ設定 ──────────────────────────────────────────────────────────────────

def _get_logger(log_dir: str = "logs") -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{datetime.today().strftime('%Y%m%d')}.log")

    logger = logging.getLogger("fetch_data")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)
    return logger


# ── watchlist 読み込み ─────────────────────────────────────────────────────────

def load_tickers(watchlist_path: str) -> list:
    path = Path(watchlist_path)
    if not path.exists():
        raise FileNotFoundError(f"watchlistが見つかりません: {watchlist_path}")

    tickers = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames and "ticker" in reader.fieldnames:
            for row in reader:
                t = row["ticker"].strip()
                if t:
                    tickers.append(t)
        else:
            f.seek(0)
            for row in csv.reader(f):
                if row and row[0].strip():
                    tickers.append(row[0].strip())

    if not tickers:
        raise ValueError(f"有効な銘柄コードが見つかりません: {watchlist_path}")

    return tickers


# ── ticker 正規化 ──────────────────────────────────────────────────────────────

def normalize_ticker(ticker: str) -> str:
    ticker = ticker.strip()
    if ticker.isdigit() and len(ticker) == 4:
        return f"{ticker}.T"
    return ticker


# ── 1銘柄ダウンロード ──────────────────────────────────────────────────────────

def download_one(ticker: str, days: int = DEFAULT_DAYS,
                 from_date: datetime = None, retries: int = MAX_RETRIES) -> pd.DataFrame:
    """
    1銘柄のOHLCVをダウンロードしてDataFrameで返す。
    from_date を指定した場合はその日から今日までを取得（差分取得用）。
    上場廃止・404エラーは ValueError として raise する。
    """
    import contextlib
    import io

    end   = datetime.today()
    start = from_date if from_date is not None else (end - timedelta(days=days))

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            stderr_buf = io.StringIO()
            with contextlib.redirect_stderr(stderr_buf):
                df = yf.download(
                    ticker,
                    start       = start.strftime("%Y-%m-%d"),
                    end         = end.strftime("%Y-%m-%d"),
                    interval    = "1d",
                    progress    = False,
                    auto_adjust = True,
                )

            stderr_text = stderr_buf.getvalue().lower()
            if any(kw in stderr_text for kw in _DELISTED_KEYWORDS):
                raise ValueError(f"上場廃止またはデータなし")

            if df is None or df.empty:
                raise ValueError("データが空です（非営業日・上場廃止の可能性）")

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            required = {"Open", "High", "Low", "Close", "Volume"}
            missing  = required - set(df.columns)
            if missing:
                raise ValueError(f"カラム不足: {missing}")

            return df[["Open", "High", "Low", "Close", "Volume"]]

        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(RATE_LIMIT_SLEEP * attempt)
            continue

    raise RuntimeError(f"{ticker}: {retries}回試行しても失敗 -> {last_error}")


# ── DB ヘルパー ────────────────────────────────────────────────────────────────

def _db_upsert_ticker(conn, code: str) -> int:
    """tickersテーブルにupsertしてticker_idを返す。"""
    with get_cursor(conn) as cur:
        cur.execute("""
            INSERT INTO tickers (code) VALUES (%s)
            ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code
            RETURNING id
        """, (code,))
        return cur.fetchone()["id"]


def _db_get_last_ohlcv_date(conn, ticker_id: int):
    """ohlcvテーブルの最終日付を返す。なければNone。"""
    with get_cursor(conn) as cur:
        cur.execute(
            "SELECT MAX(date) AS last_date FROM ohlcv WHERE ticker_id = %s",
            (ticker_id,),
        )
        row = cur.fetchone()
        return row["last_date"] if row and row["last_date"] else None


def _db_save_ohlcv(conn, ticker_id: int, df: pd.DataFrame) -> int:
    """
    ohlcvテーブルにDataFrameを保存する。重複行はスキップ。
    Returns: 挿入行数
    """
    inserted = 0
    with get_cursor(conn) as cur:
        for idx, row in df.iterrows():
            d = idx.date() if hasattr(idx, "date") else idx
            cur.execute("""
                INSERT INTO ohlcv (ticker_id, date, open, high, low, close, volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker_id, date) DO NOTHING
            """, (
                ticker_id, d,
                float(row["Open"]), float(row["High"]),
                float(row["Low"]),  float(row["Close"]),
                int(row["Volume"]),
            ))
            inserted += cur.rowcount
    conn.commit()
    return inserted


def _db_log_fetch(conn, ticker_id: int, fetched_from, fetched_to, status: str, error_msg):
    """fetch_logsテーブルに取得記録を残す。"""
    with get_cursor(conn) as cur:
        cur.execute("""
            INSERT INTO fetch_logs (ticker_id, fetched_from, fetched_to, status, error_msg)
            VALUES (%s, %s, %s, %s, %s)
        """, (ticker_id, fetched_from, fetched_to, status, error_msg))
    conn.commit()


def _db_get_ticker_and_last_date(ticker: str, logger) -> tuple:
    """
    DBからticker_idと最終OHLCV日付を返す。
    DB未接続・エラーの場合は (None, None) を返す（ベストエフォート）。
    """
    if not _DB_AVAILABLE:
        return None, None
    try:
        conn = get_conn()
        try:
            ticker_id = _db_upsert_ticker(conn, ticker)
            conn.commit()
            last_date = _db_get_last_ohlcv_date(conn, ticker_id)
            return ticker_id, last_date
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"{ticker}: DB接続失敗（CSVのみ保存） {e}")
        return None, None


# ── バッチ取得 ────────────────────────────────────────────────────────────────

def fetch_all(tickers: list, output_dir: Path, days: int, log_path: Path) -> dict:
    """
    全銘柄を順次取得して output_dir に CSV 保存し、DB にも差分保存する。
    Returns: {"success": [...], "failed": [{"ticker": ..., "reason": ...}]}
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger  = _get_logger(str(log_path.parent))
    success = []
    failed  = []
    total   = len(tickers)

    for i, raw_ticker in enumerate(tickers, 1):
        ticker = normalize_ticker(raw_ticker)
        print(f"[{i:3d}/{total}] {ticker} ... ", end="", flush=True)

        # DB: ticker_id と最終日付を確認
        ticker_id, last_date = _db_get_ticker_and_last_date(ticker, logger)

        # 差分取得の開始日を決定
        from_date = None
        if last_date is not None:
            next_day = datetime.combine(last_date, datetime.min.time()) + timedelta(days=1)
            if next_day.date() >= datetime.today().date():
                # DB が最新なのでスキップ
                print(f"OK (DB最新: {last_date} 差分なし)")
                success.append(ticker)
                if i < total:
                    time.sleep(RATE_LIMIT_SLEEP)
                continue
            from_date = next_day

        try:
            df = download_one(ticker, days=days, from_date=from_date)

            # CSV 保存
            safe_name = ticker.replace("/", "_").replace(":", "_")
            out_path  = output_dir / f"{safe_name}.csv"
            df.to_csv(out_path)

            rows = len(df)
            mode = f"差分+{rows}日" if from_date else f"{rows}日分"
            print(f"OK  ({mode})", flush=True)
            success.append(ticker)

            # DB 保存
            if ticker_id is not None:
                try:
                    conn = get_conn()
                    try:
                        inserted     = _db_save_ohlcv(conn, ticker_id, df)
                        fetched_from = df.index[0].date() if hasattr(df.index[0], "date") else df.index[0]
                        fetched_to   = df.index[-1].date() if hasattr(df.index[-1], "date") else df.index[-1]
                        _db_log_fetch(conn, ticker_id, fetched_from, fetched_to, "success", None)
                        logger.info(f"{ticker}: DB保存 {inserted}行 ({fetched_from} - {fetched_to})")
                    finally:
                        conn.close()
                except Exception as db_err:
                    logger.error(f"{ticker}: DB保存失敗 {db_err}")

        except Exception as e:
            reason = str(e)
            print(f"SKIP  ({reason[:60]})", flush=True)
            failed.append({"ticker": ticker, "reason": reason})
            logger.warning(f"{ticker}: スキップ {reason}")

            # fetch_logs にエラー記録
            if ticker_id is not None:
                try:
                    conn = get_conn()
                    try:
                        today = datetime.today().date()
                        _db_log_fetch(conn, ticker_id, today, today, "error", reason[:200])
                    finally:
                        conn.close()
                except Exception:
                    pass

        if i < total:
            time.sleep(RATE_LIMIT_SLEEP)

    # ファイルログ
    log_entry = {
        "timestamp":     datetime.now().isoformat(),
        "output_dir":    str(output_dir),
        "success_count": len(success),
        "failed_count":  len(failed),
        "failed":        failed,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[fetch_data] {json.dumps(log_entry, ensure_ascii=False)}\n")

    return {"success": success, "failed": failed}


# ── メイン ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="日本株OHLCVデータ取得")
    parser.add_argument(
        "--date",
        default=datetime.today().strftime("%Y%m%d"),
        help="出力フォルダ名に使う日付 (YYYYMMDD)。デフォルト: 今日",
    )
    parser.add_argument(
        "--watchlist",
        default=DEFAULT_WATCHLIST,
        help=f"watchlist CSVのパス。デフォルト: {DEFAULT_WATCHLIST}",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        help="個別指定する銘柄コード（watchlistより優先）",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"取得する過去日数。デフォルト: {DEFAULT_DAYS}",
    )
    args = parser.parse_args()

    if args.tickers:
        tickers = args.tickers
        print(f"[fetch_data] 個別指定: {tickers}")
    else:
        tickers = load_tickers(args.watchlist)
        print(f"[fetch_data] watchlist: {args.watchlist}  ({len(tickers)} 銘柄)")

    db_status = "利用可能" if _DB_AVAILABLE else "未接続（CSVのみ）"
    print(f"[fetch_data] DB: {db_status}")

    output_dir = Path(f"data/raw/{args.date}")
    log_path   = Path(f"logs/{args.date}.log")

    print(f"[fetch_data] 出力先: {output_dir}")
    print(f"[fetch_data] 取得期間: 直近 {args.days} 日\n")

    result = fetch_all(tickers, output_dir, args.days, log_path)

    print(f"\n{'='*40}")
    print(f"完了: {len(result['success'])}/{len(tickers)} 銘柄")
    if result["failed"]:
        print(f"スキップ ({len(result['failed'])} 銘柄):")
        for f in result["failed"]:
            print(f"  - {f['ticker']}: {f['reason'][:60]}")

    if len(result["success"]) == 0:
        print("[fetch_data] 全銘柄取得失敗", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
