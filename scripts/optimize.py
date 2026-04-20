"""
optimize.py

グリッドサーチエンジン。
params/{strategy}_grid.yaml に定義した値の全組み合わせでバックテストを実行し、
最適パラメータを探索する。

Usage:
    python scripts/optimize.py --strategy s01_rsi_macd_bb --days 365 --workers 4
"""

import argparse
import csv
import importlib
import itertools
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("[optimize] pyyaml が未インストールです。", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))
from engine import backtest_one, calc_summary, get_logger


def load_base_params(strategy_name: str) -> dict:
    path = Path(__file__).parent.parent / "params" / f"{strategy_name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"パラメータファイルが見つかりません: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_grid_params(strategy_name: str) -> dict:
    path = Path(__file__).parent.parent / "params" / f"{strategy_name}_grid.yaml"
    if not path.exists():
        raise FileNotFoundError(f"グリッドファイルが見つかりません: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_param_combinations(base_params: dict, grid_params: dict) -> list:
    keys   = list(grid_params.keys())
    values = [grid_params[k] for k in keys]
    combos = []
    for combo in itertools.product(*values):
        params = dict(base_params)
        for k, v in zip(keys, combo):
            params[k] = v
        combos.append(params)
    return combos


def run_one(args_tuple) -> dict:
    """1パラメータセット×全銘柄のバックテストをengine経由で実行する。"""
    strategy_name, params, tickers, days = args_tuple

    results = []
    for ticker in tickers:
        r = backtest_one(ticker, days, strategy_name, params)
        results.append(r)

    summary = calc_summary(results)
    return {"params": params, **summary}


def main():
    parser = argparse.ArgumentParser(description="グリッドサーチエンジン")
    parser.add_argument("--strategy",  required=True,         help="戦略名（例: s01_rsi_macd_bb）")
    parser.add_argument("--watchlist", default="data/watchlist.csv")
    parser.add_argument("--days",      type=int, default=365, help="バックテスト期間（日）")
    parser.add_argument("--workers",   type=int, default=4,   help="並列ワーカー数")
    args = parser.parse_args()

    logger = get_logger()
    logger.info(f"optimize 開始: strategy={args.strategy} days={args.days} workers={args.workers}")

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

    print(f"[optimize] 戦略: {args.strategy}")
    print(f"[optimize] 銘柄数: {len(tickers)}  期間: {args.days}日")
    print(f"[optimize] パラメータ組み合わせ数: {len(combinations)}")
    print(f"[optimize] ワーカー: {args.workers}")
    print()

    tasks      = [(args.strategy, p, tickers, args.days) for p in combinations]
    results    = []
    start_time = time.time()

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(run_one, task): i for i, task in enumerate(tasks)}

        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
            except Exception as e:
                result = {"params": combinations[idx], "error": str(e)}
                logger.error(f"optimize combo[{idx}]: {e}")

            results.append(result)
            done    = len(results)
            elapsed = time.time() - start_time
            eta     = (elapsed / done) * (len(combinations) - done) if done > 0 else 0

            if result.get("error"):
                status = f"ERROR: {result['error'][:40]}"
            else:
                status = (f"trades={result.get('trade_count', 0)}  "
                          f"win={result.get('win_rate', '-')}%  "
                          f"pf={result.get('profit_factor', '-')}")
            print(f"  [{done:4d}/{len(combinations)}] {status}  ETA: {eta/60:.1f}min", flush=True)

    # profit_factor降順でソート（Noneは末尾）
    valid   = [r for r in results if not r.get("error") and r.get("profit_factor") is not None]
    invalid = [r for r in results if r.get("error") or r.get("profit_factor") is None]
    valid.sort(key=lambda x: x["profit_factor"], reverse=True)
    sorted_results = valid + invalid

    os.makedirs("data", exist_ok=True)
    output_path = f"data/optimize_{args.strategy}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "strategy":     args.strategy,
            "days":         args.days,
            "tickers":      len(tickers),
            "generated_at": datetime.now().isoformat(),
            "results":      sorted_results,
        }, f, ensure_ascii=False, indent=2)

    logger.info(f"optimize 完了: combos={len(combinations)} valid={len(valid)} output={output_path}")

    print(f"\n{'='*50}")
    print(f"  グリッドサーチ完了")
    print(f"{'='*50}")
    print(f"  組み合わせ数: {len(combinations)}")
    print(f"  有効結果数 : {len(valid)}")
    print(f"\n  上位5パラメータ（profit_factor順）:")
    grid_keys = list(grid_params.keys())
    for r in sorted_results[:5]:
        param_str = "  ".join(f"{k}={r['params'].get(k)}" for k in grid_keys)
        print(f"    pf={r.get('profit_factor', '-')}  "
              f"win={r.get('win_rate', '-')}%  "
              f"trades={r.get('trade_count', 0)}  "
              f"| {param_str}")
    print(f"\n  結果ファイル: {output_path}")


if __name__ == "__main__":
    main()
