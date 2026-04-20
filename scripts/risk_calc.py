"""
risk_calc.py

data/signals/YYYYMMDD.jsonl を読み込み、Kelly基準でポジションサイズを計算して結果を返す。

Usage:
    python scripts/risk_calc.py --date 20260414 --capital 1000000 --mode normal
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


# ── 定数 ──────────────────────────────────────────────────────────────────────

STOP_LOSS_PCT   = 0.03   # 損切り: エントリー価格から -3%
TAKE_PROFIT_PCT = 0.06   # 利確:   エントリー価格から +6%  (RR比 1:2)
MAX_RISK_NORMAL = 0.02   # 通常モード: 1トレードあたり資産の 2%
MAX_RISK_ALERT  = 0.01   # 警戒モード: 1トレードあたり資産の 1%

# Kelly計算に使うデフォルト期待値（ペイオフ比）
# performance_log.csv が貯まってきたら実績値に置き換える
DEFAULT_WIN_RATE  = 0.50   # 勝率 50%（保守的スタート）
DEFAULT_PAYOFF    = TAKE_PROFIT_PCT / STOP_LOSS_PCT  # = 2.0


# ── Kelly基準 ─────────────────────────────────────────────────────────────────

def kelly_fraction(win_rate: float, payoff: float) -> float:
    """
    Kelly fraction = (b*p - q) / b
      b = payoff ratio (平均勝ち / 平均負け)
      p = 勝率
      q = 1 - p

    負の値（期待値がマイナス）は 0 に丸める。
    過剰レバレッジを避けるため Half-Kelly（÷2）で使う。
    """
    q = 1.0 - win_rate
    raw = (payoff * win_rate - q) / payoff
    half_kelly = max(raw / 2.0, 0.0)
    return half_kelly


# ── 実績値の読み込み ───────────────────────────────────────────────────────────

def load_empirical_stats(log_path: str) -> tuple[float, float]:
    """
    data/performance_log.csv から実績の勝率・ペイオフ比を計算する。
    データが30件未満の場合はデフォルト値を返す（サンプル不足）。
    Returns:
        (win_rate, payoff)
    """
    path = Path(log_path)
    if not path.exists():
        return DEFAULT_WIN_RATE, DEFAULT_PAYOFF

    import csv
    wins, losses = [], []

    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            result = row.get("result", "")
            pnl = float(row.get("pnl_pct", 0) or 0)
            if result == "take_profit":
                wins.append(pnl)
            elif result == "stop_loss":
                losses.append(abs(pnl))

    total = len(wins) + len(losses)
    if total < 30:
        return DEFAULT_WIN_RATE, DEFAULT_PAYOFF

    win_rate = len(wins) / total
    avg_win  = sum(wins)  / len(wins)  if wins   else TAKE_PROFIT_PCT
    avg_loss = sum(losses) / len(losses) if losses else STOP_LOSS_PCT
    payoff   = avg_win / avg_loss if avg_loss > 0 else DEFAULT_PAYOFF

    return win_rate, payoff


# ── ポジションサイズ計算 ───────────────────────────────────────────────────────

def calc_position(
    ticker: str,
    price: float,
    direction: str,
    capital: float,
    mode: str,
    win_rate: float,
    payoff: float,
) -> dict:
    """
    1銘柄のポジションサイズ・損切り・利確を計算する。
    Returns:
        dict with keys:
            ticker, direction, price,
            lot, investment,
            stop_loss, take_profit,
            risk_amount, risk_pct,
            kelly_fraction, status
    """
    max_risk_pct = MAX_RISK_ALERT if mode == "alert" else MAX_RISK_NORMAL

    # Kelly比率 → 投資額比率（資産上限でキャップ）
    kf = kelly_fraction(win_rate, payoff)
    invest_pct = min(kf, max_risk_pct * (1.0 / STOP_LOSS_PCT))
    # 上記: ケリーが示す投資比率と、「損切り%で最大損失<=max_risk以内」の小さい方
    # 別途: 直接リスクベースで計算して小さい方を採用
    risk_based_invest = (capital * max_risk_pct) / (price * STOP_LOSS_PCT)

    # 株数（最小単位: 100株単位に切り捨て）
    lot_raw   = min(invest_pct * capital / price, risk_based_invest)
    lot       = int(lot_raw // 100) * 100  # 単元株に切り捨て
    investment = lot * price

    if lot == 0:
        return {
            "ticker":    ticker,
            "direction": direction,
            "price":     price,
            "lot":       0,
            "investment": 0,
            "stop_loss":  None,
            "take_profit": None,
            "risk_amount": 0,
            "risk_pct":   0,
            "kelly_fraction": round(kf, 4),
            "status":    "excluded",  # ロット0 → reporter から除外
            "reason":    "ポジションサイズが単元株未満",
        }

    # 損切り・利確価格
    if direction == "buy":
        stop_loss   = round(price * (1 - STOP_LOSS_PCT),   0)
        take_profit = round(price * (1 + TAKE_PROFIT_PCT), 0)
    else:  # sell (空売り想定)
        stop_loss   = round(price * (1 + STOP_LOSS_PCT),   0)
        take_profit = round(price * (1 - TAKE_PROFIT_PCT), 0)

    risk_amount = lot * price * STOP_LOSS_PCT
    risk_pct    = risk_amount / capital * 100

    return {
        "ticker":         ticker,
        "direction":      direction,
        "price":          price,
        "lot":            lot,
        "investment":     round(investment, 0),
        "stop_loss":      stop_loss,
        "take_profit":    take_profit,
        "risk_amount":    round(risk_amount, 0),
        "risk_pct":       round(risk_pct, 3),
        "kelly_fraction": round(kf, 4),
        "status":         "ok",
    }


# ── メイン ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Risk calculator for stock signals")
    parser.add_argument("--date",    default=datetime.today().strftime("%Y%m%d"))
    parser.add_argument("--capital", type=float, required=True, help="運用資産（円）")
    parser.add_argument("--mode",    default="normal", choices=["normal", "alert"])
    parser.add_argument("--log",     default="data/performance_log.csv")
    args = parser.parse_args()

    signal_path = f"data/signals/{args.date}.jsonl"
    if not Path(signal_path).exists():
        print(json.dumps({"error": f"{signal_path} が見つかりません"}, ensure_ascii=False))
        sys.exit(1)

    # 実績値の読み込み
    win_rate, payoff = load_empirical_stats(args.log)
    print(f"[risk_calc] 勝率={win_rate:.2%}  ペイオフ比={payoff:.2f}  Kelly={kelly_fraction(win_rate, payoff):.4f}", file=sys.stderr)

    results = []
    with open(signal_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r["status"] not in ("confirmed", "candidate"):
                continue

            result = calc_position(
                ticker    = r["ticker"],
                price     = r["price"],
                direction = r["direction"],
                capital   = args.capital,
                mode      = args.mode,
                win_rate  = win_rate,
                payoff    = payoff,
            )
            result["signal_status"] = r["status"]  # confirmed / candidate
            results.append(result)

    # 結果をファイルに書き出し（daily-report が読む）
    today = args.date
    out_path = f"data/signals/{today}_risk.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # ログ
    os.makedirs("logs", exist_ok=True)
    with open(f"logs/{today}.log", "a", encoding="utf-8") as f:
        excluded = [r["ticker"] for r in results if r["status"] == "excluded"]
        ok       = [r["ticker"] for r in results if r["status"] == "ok"]
        f.write(f"[risk_calc] ok={ok}  excluded={excluded}\n")

    # 標準出力に要約（AGENTS.mdのStep 4確認用）
    for r in results:
        if r["status"] == "ok":
            print(
                f"  {r['ticker']:10s} {r['direction']:4s} "
                f"lot={r['lot']:5d}株  "
                f"損切={r['stop_loss']:,.0f}  利確={r['take_profit']:,.0f}  "
                f"リスク={r['risk_pct']:.2f}%"
            )
        else:
            print(f"  {r['ticker']:10s} 除外: {r.get('reason','')}")

    print(f"\n出力: {out_path}")


if __name__ == "__main__":
    main()
