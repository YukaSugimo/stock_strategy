"""
fetch_data.py

market-data skill から呼ばれる、または単独で実行するデータ取得スクリプト。
data/watchlist.csv の銘柄を yfinance で取得して data/raw/YYYYMMDD/ に保存する。

Usage:
    python scripts/fetch_data.py
    python scripts/fetch_data.py --date 20240415
    python scripts/fetch_data.py --tickers 7203.T 9984.T --days 90
    python scripts/fetch_data.py --watchlist data/watchlist_extended.csv
"""

import argparse
import csv
import json
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


# ── 定数 ──────────────────────────────────────────────────────────────────────

DEFAULT_WATCHLIST = "data/watchlist.csv"
DEFAULT_DAYS      = 90       # 直近90日取得（祝日バッファを含めて60営業日を確保）
RETRY_WAIT_SEC    = 2        # リトライ前の待機秒数
MAX_RETRIES       = 3        # 1銘柄あたりの最大リトライ回数
RATE_LIMIT_SLEEP  = 0.5      # 銘柄間のスリープ（レート制限対策）


# ── watchlist 読み込み ─────────────────────────────────────────────────────────

def load_tickers(watchlist_path: str) -> list[str]:
    """
    CSVからtickerカラムを読み込む。
    ヘッダーなしの場合は1列目をtickerとして扱う。
    """
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
            # ヘッダーなし: 1列目をticker扱い
            f.seek(0)
            for row in csv.reader(f):
                if row and row[0].strip():
                    tickers.append(row[0].strip())

    if not tickers:
        raise ValueError(f"有効な銘柄コードが見つかりません: {watchlist_path}")

    return tickers


# ── ticker の正規化 ────────────────────────────────────────────────────────────

def normalize_ticker(ticker: str) -> str:
    """
    日本株ティッカーの末尾に .T が付いていない場合に補完する。
    数字4桁のみの場合は .T を付加する。
    例: "7203" → "7203.T"、"7203.T" → "7203.T"
    """
    ticker = ticker.strip()
    if ticker.isdigit() and len(ticker) == 4:
        return f"{ticker}.T"
    return ticker


# ── 1銘柄のダウンロード ────────────────────────────────────────────────────────

def download_one(ticker: str, days: int, retries: int = MAX_RETRIES) -> pd.DataFrame:
    """
    1銘柄のOHLCVをダウンロードしてDataFrameで返す。
    失敗した場合はリトライし、それでも失敗なら例外を raise する。
    """
    end   = datetime.today()
    start = end - timedelta(days=days)

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            df = yf.download(
                ticker,
                start   = start.strftime("%Y-%m-%d"),
                end     = end.strftime("%Y-%m-%d"),
                interval= "1d",
                progress= False,
                auto_adjust=True,   # 分割・配当を自動調整済みの価格を取得
            )

            if df.empty:
                raise ValueError("データが空です（非営業日・上場廃止の可能性）")

            # MultiIndex列をフラット化（yfinance>=0.2.x対策）
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # 必要カラムの確認
            required = {"Open", "High", "Low", "Close", "Volume"}
            missing  = required - set(df.columns)
            if missing:
                raise ValueError(f"カラム不足: {missing}")

            return df[["Open", "High", "Low", "Close", "Volume"]]

        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(RATE_LIMIT_SLEEP * attempt)  # バックオフ
            continue

    raise RuntimeError(f"{ticker}: {retries}回試行しても失敗 → {last_error}")


# ── バッチ取得 ────────────────────────────────────────────────────────────────

def fetch_all(
    tickers:    list[str],
    output_dir: Path,
    days:       int,
    log_path:   Path,
) -> dict:
    """
    全銘柄を順次取得して output_dir に保存する。
    Returns: {"success": [...], "failed": [{"ticker": ..., "reason": ...}]}
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    success = []
    failed  = []

    total = len(tickers)
    for i, raw_ticker in enumerate(tickers, 1):
        ticker = normalize_ticker(raw_ticker)
        print(f"[{i:3d}/{total}] {ticker} ... ", end="", flush=True)

        try:
            df = download_one(ticker, days)

            # 保存（ticker名のスラッシュ等をアンダースコアに置換）
            safe_name = ticker.replace("/", "_").replace(":", "_")
            out_path  = output_dir / f"{safe_name}.csv"
            df.to_csv(out_path)

            rows = len(df)
            print(f"OK  ({rows}日分)")
            success.append(ticker)

        except Exception as e:
            reason = str(e)
            print(f"SKIP  ({reason})")
            failed.append({"ticker": ticker, "reason": reason})

        # レート制限対策: 銘柄間スリープ
        if i < total:
            time.sleep(RATE_LIMIT_SLEEP)

    # ログ書き出し
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "output_dir": str(output_dir),
        "success_count": len(success),
        "failed_count":  len(failed),
        "failed": failed,
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

    # 銘柄リストの決定
    if args.tickers:
        tickers = args.tickers
        print(f"[fetch_data] 個別指定: {tickers}")
    else:
        tickers = load_tickers(args.watchlist)
        print(f"[fetch_data] watchlist: {args.watchlist}  ({len(tickers)} 銘柄)")

    output_dir = Path(f"data/raw/{args.date}")
    log_path   = Path(f"logs/{args.date}.log")

    print(f"[fetch_data] 出力先: {output_dir}")
    print(f"[fetch_data] 取得期間: 直近 {args.days} 日\n")

    result = fetch_all(tickers, output_dir, args.days, log_path)

    # サマリー
    print(f"\n{'='*40}")
    print(f"完了: {len(result['success'])}/{len(tickers)} 銘柄")
    if result["failed"]:
        print(f"スキップ ({len(result['failed'])} 銘柄):")
        for f in result["failed"]:
            print(f"  - {f['ticker']}: {f['reason']}")

    # 全銘柄失敗の場合は終了コード1（AGENTS.mdの「全銘柄失敗」判定に使う）
    if len(result["success"]) == 0:
        print("[fetch_data] 全銘柄取得失敗", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
