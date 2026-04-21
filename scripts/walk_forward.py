"""
walk_forward.py

ウォークフォワード最適化スクリプト。
今日から遡って --years 年分を1年ずつスライドさせ、各期間でグリッドサーチを実行する。

Usage:
    python scripts/walk_forward.py --strategy s01_rsi_macd_bb --years 5 --workers 8
"""

import argparse
import csv
import itertools
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

try:
    import yaml
except ImportError:
    print("[walk_forward] pyyaml が未インストールです。", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))
from engine import backtest_one, calc_summary, get_logger
from optimize import load_base_params, load_grid_params, build_param_combinations, run_one


def build_periods(years: int) -> list:
    """今日から遡って years 年分の1年スライド期間リストを返す。"""
    today = datetime.today().date()
    periods = []
    for i in range(years):
        end   = today - timedelta(days=365 * i)
        start = end   - timedelta(days=365)
        periods.append((start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
    periods.reverse()
    return periods


def run_grid_search(
    strategy_name: str,
    tickers: list,
    combinations: list,
    start_date: str,
    end_date: str,
    workers: int,
    logger,
) -> list:
    """1期間分のグリッドサーチを実行して結果リストを返す。"""
    tasks = [(strategy_name, p, tickers, 365, start_date, end_date)
             for p in combinations]
    results = []

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run_one, task): i for i, task in enumerate(tasks)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
            except Exception as e:
                result = {"params": combinations[idx], "error": str(e)}
                logger.error(f"walk_forward combo[{idx}]: {e}")
            results.append(result)

    return results


def best_result(results: list) -> dict | None:
    """profit_factor が最大の有効結果を返す。"""
    valid = [r for r in results
             if not r.get("error") and r.get("profit_factor") is not None]
    if not valid:
        return None
    return max(valid, key=lambda x: x["profit_factor"])


def format_params(params: dict, grid_keys: list) -> str:
    return "  ".join(f"{k}={params.get(k)}" for k in grid_keys)


def main():
    parser = argparse.ArgumentParser(description="ウォークフォワード最適化")
    parser.add_argument("--strategy",  required=True,       help="戦略名（例: s01_rsi_macd_bb）")
    parser.add_argument("--watchlist", default="data/watchlist.csv")
    parser.add_argument("--years",     type=int, default=5, help="遡る年数（デフォルト: 5）")
    parser.add_argument("--workers",   type=int, default=4, help="並列ワーカー数")
    args = parser.parse_args()

    logger = get_logger()
    logger.info(
        f"walk_forward 開始: strategy={args.strategy} "
        f"years={args.years} workers={args.workers}"
    )

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

    base_params  = load_base_params(args.strategy)
    grid_params  = load_grid_params(args.strategy)
    combinations = build_param_combinations(base_params, grid_params)
    grid_keys    = list(grid_params.keys())
    periods      = build_periods(args.years)

    print(f"[walk_forward] 戦略: {args.strategy}")
    print(f"[walk_forward] 銘柄数: {len(tickers)}  期間数: {len(periods)}  "
          f"組み合わせ数/期間: {len(combinations)}  ワーカー: {args.workers}")
    print()

    all_period_results = []
    start_time = time.time()

    for period_idx, (start_date, end_date) in enumerate(periods, 1):
        print(f"[{period_idx}/{len(periods)}] {start_date} ~ {end_date} グリッドサーチ中...")

        period_results = run_grid_search(
            strategy_name=args.strategy,
            tickers=tickers,
            combinations=combinations,
            start_date=start_date,
            end_date=end_date,
            workers=args.workers,
            logger=logger,
        )

        best = best_result(period_results)

        period_entry = {
            "period_start":  start_date,
            "period_end":    end_date,
            "grid_results":  period_results,
            "best":          best,
        }
        all_period_results.append(period_entry)

        if best:
            elapsed = time.time() - start_time
            print(f"  -> 最良: pf={best.get('profit_factor', '-')}  "
                  f"win={best.get('win_rate', '-')}%  "
                  f"trades={best.get('trade_count', 0)}  "
                  f"| {format_params(best['params'], grid_keys)}")
        else:
            print("  -> 有効結果なし")
        print()

    # JSON 保存
    os.makedirs("data", exist_ok=True)
    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = f"data/walk_forward_{args.strategy}_{timestamp}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "strategy":     args.strategy,
            "years":        args.years,
            "tickers":      len(tickers),
            "generated_at": datetime.now().isoformat(),
            "periods":      all_period_results,
        }, f, ensure_ascii=False, indent=2)

    logger.info(f"walk_forward 完了: output={output_path}")

    # 結果テーブル出力
    header = f"{'期間':25s}  {'取引数':>6}  {'勝率':>7}  {'PF':>6}  最良パラメータ"
    print(f"\n{'='*80}")
    print(f"  ウォークフォワード結果サマリー")
    print(f"{'='*80}")
    print(f"  {header}")
    print(f"  {'-'*78}")

    for entry in all_period_results:
        period_label = f"{entry['period_start'][:7]}~{entry['period_end'][:7]}"
        best = entry.get("best")
        if best:
            trades   = best.get("trade_count", 0)
            win_rate = f"{best.get('win_rate') or 0:.1f}%"
            pf       = f"{best.get('profit_factor') or 0:.2f}"
            param_str = format_params(best["params"], grid_keys)
        else:
            trades, win_rate, pf, param_str = 0, "N/A", "N/A", "-"
        print(f"  {period_label:25s}  {trades:>6}  {win_rate:>7}  {pf:>6}  {param_str}")

    print(f"\n  結果ファイル: {output_path}")


if __name__ == "__main__":
    main()
