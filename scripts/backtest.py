"""
backtest.py

大量銘柄対応のバックテストスクリプト。
watchlist.csv の全銘柄に対して過去データでシグナルを検証する。

特徴:
- 戦略プラグイン構造（--strategy で外部指定）
- 並列処理（最大ワーカー数を指定可能）
- チェックポイント機能（途中再開対応）
- 上場廃止・404エラーの自動スキップ
- エラーはlogsフォルダに記録

Usage:
    python scripts/backtest.py --strategy s01_rsi_macd_bb --days 365 --workers 4
    python scripts/backtest.py --strategy s01_rsi_macd_bb --resume
"""

import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("[backtest] pandas が未インストールです。", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))
from engine import backtest_one, calc_summary, get_logger

CHECKPOINT_FILE = "data/backtest_checkpoint.json"
RESULT_FILE     = "data/backtest_results.csv"
SUMMARY_FILE    = "data/backtest_summary.json"


def load_checkpoint() -> set:
    if not Path(CHECKPOINT_FILE).exists():
        return set()
    with open(CHECKPOINT_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return set(data.get("completed", []))


def save_checkpoint(completed: set):
    os.makedirs("data", exist_ok=True)
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump({"completed": list(completed), "updated_at": datetime.now().isoformat()}, f, ensure_ascii=False)


def append_results(results: list):
    os.makedirs("data", exist_ok=True)
    path   = Path(RESULT_FILE)
    is_new = not path.exists()
    fields = ["ticker", "entry_date", "exit_date", "direction",
              "entry_price", "exit_price", "pnl_pct", "result", "hold_days"]

    with open(path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if is_new:
            writer.writeheader()
        for r in results:
            for trade in r.get("trades", []):
                writer.writerow({k: trade.get(k, "") for k in fields})


def generate_summary(all_results: list) -> dict:
    all_trades = []
    for r in all_results:
        all_trades.extend(r.get("trades", []))

    base = calc_summary(all_results)

    if not all_trades:
        return {
            "generated_at":    datetime.now().isoformat(),
            "total_tickers":   len(all_results),
            "tickers_traded":  0,
            **base,
            "avg_win":         0.0,
            "avg_loss":        0.0,
            "top5_by_winrate": [],
            "result_breakdown": {"take_profit": 0, "stop_loss": 0, "timeout": 0},
            "error": "トレードなし",
        }

    pnls   = [t["pnl_pct"] for t in all_trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    ticker_summary = [
        {
            "ticker":       r["ticker"],
            "trade_count":  r["trade_count"],
            "win_rate":     r["win_rate"],
            "avg_pnl":      r["avg_pnl"],
            "total_return": r["total_return"],
            "max_drawdown": r["max_drawdown"],
        }
        for r in all_results if r.get("trade_count", 0) > 0
    ]
    top5 = sorted(ticker_summary, key=lambda x: x["win_rate"] or 0, reverse=True)[:5]

    return {
        "generated_at":    datetime.now().isoformat(),
        "total_tickers":   len(all_results),
        "tickers_traded":  len(ticker_summary),
        **base,
        "avg_win":         round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss":        round(sum(losses) / len(losses), 2) if losses else 0,
        "top5_by_winrate": top5,
        "result_breakdown": {
            "take_profit": sum(1 for t in all_trades if t["result"] == "take_profit"),
            "stop_loss":   sum(1 for t in all_trades if t["result"] == "stop_loss"),
            "timeout":     sum(1 for t in all_trades if t["result"] == "timeout"),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="大量銘柄対応バックテスト")
    parser.add_argument("--strategy",  required=True,              help="戦略名（例: s01_rsi_macd_bb）")
    parser.add_argument("--watchlist", default="data/watchlist.csv")
    parser.add_argument("--days",      type=int, default=365,      help="バックテスト期間（日）")
    parser.add_argument("--workers",   type=int, default=4,        help="並列ワーカー数")
    parser.add_argument("--resume",    action="store_true",        help="チェックポイントから再開")
    parser.add_argument("--chunk",     type=int, default=20,       help="一度に処理する銘柄数")
    args = parser.parse_args()

    logger = get_logger()
    logger.info(f"backtest 開始: strategy={args.strategy} days={args.days} workers={args.workers}")

    path = Path(args.watchlist)
    if not path.exists():
        print(f"[ERROR] {args.watchlist} が見つかりません", file=sys.stderr)
        sys.exit(1)

    tickers = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames and "ticker" in reader.fieldnames:
            tickers = [row["ticker"].strip() for row in reader if row["ticker"].strip()]
        else:
            f.seek(0)
            tickers = [row[0].strip() for row in csv.reader(f) if row and row[0].strip()]

    completed = load_checkpoint() if args.resume else set()
    if completed:
        print(f"[backtest] チェックポイントから再開: {len(completed)}銘柄完了済み")

    remaining = [t for t in tickers if t not in completed]
    total     = len(tickers)

    print(f"[backtest] 戦略: {args.strategy}")
    print(f"[backtest] 対象: {total}銘柄  残り: {len(remaining)}銘柄  "
          f"期間: {args.days}日  ワーカー: {args.workers}")
    print()

    if not args.resume and Path(RESULT_FILE).exists():
        os.remove(RESULT_FILE)

    all_results = []
    skipped     = []
    start_time  = time.time()

    for chunk_start in range(0, len(remaining), args.chunk):
        chunk = remaining[chunk_start: chunk_start + args.chunk]

        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(backtest_one, t, args.days, args.strategy): t for t in chunk}

            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = {"ticker": ticker, "error": str(e), "trades": [], "skipped": False}
                    logger.error(f"{ticker}: future例外 {e}")

                all_results.append(result)
                completed.add(ticker)

                if result.get("skipped"):
                    skipped.append(ticker)
                    status = "SKIP"
                elif result.get("error"):
                    status = f"ERROR:{result['error'][:30]}"
                else:
                    status = f"trades={result.get('trade_count', 0)}"

                done    = len(completed)
                elapsed = time.time() - start_time
                eta     = (elapsed / done) * (total - done) if done > 0 else 0
                print(f"  [{done:4d}/{total}] {ticker:12s} {status}  "
                      f"ETA: {eta/60:.1f}min", flush=True)

        chunk_results = all_results[len(all_results) - len(chunk):]
        append_results(chunk_results)
        save_checkpoint(completed)

    summary = generate_summary(all_results)
    os.makedirs("data", exist_ok=True)
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    logger.info(f"backtest 完了: trades={summary.get('trade_count', 0)} "
                f"win_rate={summary.get('win_rate')} pf={summary.get('profit_factor')}")

    print(f"\n{'='*50}")
    print(f"  バックテスト完了")
    print(f"{'='*50}")
    print(f"  戦略           : {args.strategy}")
    print(f"  対象銘柄数     : {summary.get('total_tickers', 0)}")
    print(f"  スキップ銘柄   : {len(skipped)}")
    print(f"  取引発生銘柄   : {summary.get('tickers_traded', 0)}")
    print(f"  総トレード数   : {summary.get('trade_count', 0)}")
    print(f"  勝率           : {summary.get('win_rate', 0) or 0:.1f}%")
    print(f"  平均損益率     : {summary.get('avg_pnl', 0) or 0:+.2f}%")
    print(f"  プロフィット   : {summary.get('profit_factor', 'N/A')}")
    print(f"  結果内訳       : 利確={summary['result_breakdown']['take_profit']}  "
          f"損切={summary['result_breakdown']['stop_loss']}  "
          f"タイムアウト={summary['result_breakdown']['timeout']}")
    if skipped:
        print(f"\n  スキップ銘柄: {', '.join(skipped)}")
    print(f"\n  勝率上位5銘柄:")
    for r in summary.get("top5_by_winrate", []):
        print(f"    {r['ticker']:12s} 勝率{r['win_rate']:5.1f}%  "
              f"平均{r['avg_pnl']:+.2f}%  {r['trade_count']}trades")
    print(f"\n  詳細: {RESULT_FILE}")
    print(f"  サマリー: {SUMMARY_FILE}")

    if Path(CHECKPOINT_FILE).exists():
        os.remove(CHECKPOINT_FILE)


if __name__ == "__main__":
    main()
