"""
indicators.py

RSI / MACD / Bollinger Bands の計算ライブラリ。
signal-analysis skill および単独実行の両方から使う。

Usage（単独実行 / 動作確認）:
    python scripts/indicators.py --ticker 7203.T --date 20240415
    python scripts/indicators.py --ticker 7203.T --days 90
"""

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import pandas as pd
    import numpy as np
except ImportError:
    print("[indicators] pandas / numpy が未インストールです。", file=sys.stderr)
    print("  pip install pandas numpy", file=sys.stderr)
    sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# データクラス
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RSIResult:
    value: float          # 最新RSI値
    prev:  float          # 前日RSI値
    signal: Optional[str] # "buy" / "sell" / None

@dataclass
class MACDResult:
    macd:        float
    signal_line: float
    histogram:   float
    prev_macd:        float
    prev_signal_line: float
    signal: Optional[str]  # "buy" / "sell" / None

@dataclass
class BBResult:
    upper:  float
    middle: float
    lower:  float
    price:  float
    prev_upper: float
    prev_lower: float
    prev_price: float
    pct_b:  float          # %B = (price - lower) / (upper - lower)
    signal: Optional[str]  # "buy" / "sell" / None

@dataclass
class SignalSummary:
    ticker:    str
    date:      str
    price:     float
    rsi:       RSIResult
    macd:      MACDResult
    bb:        BBResult
    direction: Optional[str]   # "buy" / "sell" / None
    status:    str             # "confirmed" / "candidate" / "none"
    match_count: int           # 一致した指標数
    matched_indicators: list[str]


# ──────────────────────────────────────────────────────────────────────────────
# RSI
# ──────────────────────────────────────────────────────────────────────────────

def calc_rsi(close: pd.Series, period: int = 14) -> RSIResult:
    """
    Wilder's Smoothed Moving Average による RSI を計算する。
    pandas の ewm(alpha=1/period) が Wilder's SMA に相当する。

    シグナル判定:
      buy  : 前日 <= 30 かつ 当日 > 前日（30以下から反転上昇）
      sell : 前日 >= 70 かつ 当日 < 前日（70以上から反転下落）
    """
    if len(close) < period + 1:
        raise ValueError(f"RSI計算に必要なデータが不足しています（{len(close)}行 < {period+1}行）")

    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)

    # Wilder's SMA: ewm with alpha=1/period, adjust=False
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    # ゼロ除算対策
    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(100)  # avg_loss=0（全勝）のとき RSI=100

    latest = float(rsi.iloc[-1])
    prev   = float(rsi.iloc[-2])

    if prev <= 30 and latest > prev:
        signal = "buy"
    elif prev >= 70 and latest < prev:
        signal = "sell"
    else:
        signal = None

    return RSIResult(value=round(latest, 2), prev=round(prev, 2), signal=signal)


# ──────────────────────────────────────────────────────────────────────────────
# MACD
# ──────────────────────────────────────────────────────────────────────────────

def calc_macd(
    close:       pd.Series,
    fast:        int = 12,
    slow:        int = 26,
    signal_span: int = 9,
) -> MACDResult:
    """
    MACD = EMA(fast) - EMA(slow)
    Signal = EMA(MACD, signal_span)
    Histogram = MACD - Signal

    シグナル判定（クロス検出）:
      buy  : 前日 MACD < Signal かつ 当日 MACD > Signal（ゴールデンクロス）
      sell : 前日 MACD > Signal かつ 当日 MACD < Signal（デッドクロス）
    """
    if len(close) < slow + signal_span:
        raise ValueError(f"MACD計算に必要なデータが不足しています（{len(close)}行）")

    ema_fast = close.ewm(span=fast,        adjust=False).mean()
    ema_slow = close.ewm(span=slow,        adjust=False).mean()
    macd     = ema_fast - ema_slow
    sig_line = macd.ewm(span=signal_span,  adjust=False).mean()
    hist     = macd - sig_line

    m_latest  = float(macd.iloc[-1])
    m_prev    = float(macd.iloc[-2])
    s_latest  = float(sig_line.iloc[-1])
    s_prev    = float(sig_line.iloc[-2])
    h_latest  = float(hist.iloc[-1])

    if m_prev < s_prev and m_latest > s_latest:
        signal = "buy"
    elif m_prev > s_prev and m_latest < s_latest:
        signal = "sell"
    else:
        signal = None

    return MACDResult(
        macd             = round(m_latest, 4),
        signal_line      = round(s_latest, 4),
        histogram        = round(h_latest, 4),
        prev_macd        = round(m_prev,   4),
        prev_signal_line = round(s_prev,   4),
        signal           = signal,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Bollinger Bands
# ──────────────────────────────────────────────────────────────────────────────

def calc_bb(
    close:  pd.Series,
    period: int   = 20,
    k:      float = 2.0,
) -> BBResult:
    """
    Middle = SMA(period)
    Upper  = Middle + k * σ
    Lower  = Middle - k * σ

    %B = (price - Lower) / (Upper - Lower)
      0.0 = 下限ちょうど、1.0 = 上限ちょうど

    シグナル判定（タッチして反発）:
      buy  : 前日 price <= 前日 Lower かつ 当日 price > 当日 Lower
      sell : 前日 price >= 前日 Upper かつ 当日 price < 当日 Upper
    """
    if len(close) < period + 1:
        raise ValueError(f"BB計算に必要なデータが不足しています（{len(close)}行 < {period+1}行）")

    sma    = close.rolling(period).mean()
    std    = close.rolling(period).std(ddof=1)
    upper  = sma + k * std
    lower  = sma - k * std

    p_latest = float(close.iloc[-1])
    p_prev   = float(close.iloc[-2])
    u_latest = float(upper.iloc[-1])
    u_prev   = float(upper.iloc[-2])
    l_latest = float(lower.iloc[-1])
    l_prev   = float(lower.iloc[-2])
    m_latest = float(sma.iloc[-1])

    band_width = u_latest - l_latest
    pct_b = (p_latest - l_latest) / band_width if band_width != 0 else 0.5

    if p_prev <= l_prev and p_latest > l_latest:
        signal = "buy"
    elif p_prev >= u_prev and p_latest < u_latest:
        signal = "sell"
    else:
        signal = None

    return BBResult(
        upper      = round(u_latest, 2),
        middle     = round(m_latest, 2),
        lower      = round(l_latest, 2),
        price      = round(p_latest, 2),
        prev_upper = round(u_prev,   2),
        prev_lower = round(l_prev,   2),
        prev_price = round(p_prev,   2),
        pct_b      = round(pct_b,    4),
        signal     = signal,
    )


# ──────────────────────────────────────────────────────────────────────────────
# シグナル統合
# ──────────────────────────────────────────────────────────────────────────────

def summarize(
    ticker: str,
    close:  pd.Series,
    mode:   str = "normal",   # "normal" or "alert"
    date:   Optional[str] = None,
) -> SignalSummary:
    """
    3指標を計算してシグナルを統合する。
    AGENTS.md の判定ルールをそのまま実装する。

    警戒モード (mode="alert") 時の追加条件:
      buy  かつ RSI <= 25
      sell かつ RSI >= 75
    """
    rsi_result  = calc_rsi(close)
    macd_result = calc_macd(close)
    bb_result   = calc_bb(close)

    date_str = date or datetime.today().strftime("%Y-%m-%d")
    price    = float(close.iloc[-1])

    # 方向ごとにシグナルを集計
    signals = {
        "RSI":  rsi_result.signal,
        "MACD": macd_result.signal,
        "BB":   bb_result.signal,
    }

    buy_indicators  = [k for k, v in signals.items() if v == "buy"]
    sell_indicators = [k for k, v in signals.items() if v == "sell"]

    # 方向の決定（多数決: buy/sell が混在する場合は多い方）
    if len(buy_indicators) >= len(sell_indicators):
        direction    = "buy"  if buy_indicators  else None
        matched      = buy_indicators
    else:
        direction    = "sell"
        matched      = sell_indicators

    match_count = len(matched)

    # ステータス判定
    if match_count == 3:
        if mode == "alert":
            # 警戒モード追加条件
            if direction == "buy"  and rsi_result.value <= 25:
                status = "confirmed"
            elif direction == "sell" and rsi_result.value >= 75:
                status = "confirmed"
            else:
                status = "candidate"
        else:
            status = "confirmed"
    elif match_count == 2:
        status = "candidate"
    else:
        direction = None
        status    = "none"
        matched   = []

    return SignalSummary(
        ticker              = ticker,
        date                = date_str,
        price               = price,
        rsi                 = rsi_result,
        macd                = macd_result,
        bb                  = bb_result,
        direction           = direction,
        status              = status,
        match_count         = match_count,
        matched_indicators  = matched,
    )


# ──────────────────────────────────────────────────────────────────────────────
# CSV からロードして実行
# ──────────────────────────────────────────────────────────────────────────────

def run_from_csv(csv_path: str, ticker: str, mode: str = "normal") -> SignalSummary:
    """
    data/raw/YYYYMMDD/{ticker}.csv を読み込んで summarize を実行する。
    signal-analysis skill のメインフローから呼ぶ。
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"データファイルが見つかりません: {csv_path}")

    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df = df.dropna(subset=["Close"])

    if len(df) < 30:
        raise ValueError(f"データ行数が不足しています（{len(df)}行）。最低30行必要です。")

    close = df["Close"].astype(float)
    date  = df.index[-1].strftime("%Y-%m-%d") if hasattr(df.index[-1], "strftime") else None

    return summarize(ticker=ticker, close=close, mode=mode, date=date)


# ──────────────────────────────────────────────────────────────────────────────
# 単独実行（動作確認用）
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="指標計算・シグナル確認ツール")
    parser.add_argument("--ticker",    required=True, help="銘柄コード（例: 7203.T）")
    parser.add_argument("--date",      default=datetime.today().strftime("%Y%m%d"),
                        help="data/raw/ 配下の日付フォルダ (YYYYMMDD)")
    parser.add_argument("--mode",      default="normal", choices=["normal", "alert"])
    parser.add_argument("--csv",       help="CSVパスを直接指定（--date より優先）")
    parser.add_argument("--json",      action="store_true", help="結果をJSON形式で出力")
    args = parser.parse_args()

    ticker   = args.ticker.strip()
    safe     = ticker.replace("/", "_").replace(":", "_")
    csv_path = args.csv or f"data/raw/{args.date}/{safe}.csv"

    try:
        summary = run_from_csv(csv_path, ticker, args.mode)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        # signal-analysis skill がパースしやすいJSON形式で出力
        out = asdict(summary)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    # 人間が読みやすいサマリー
    s = summary
    print(f"\n{'='*48}")
    print(f"  {s.ticker}  {s.date}  現在値: {s.price:,.0f} 円")
    print(f"{'='*48}")
    print(f"  RSI  ({s.rsi.value:5.1f})  前日: {s.rsi.prev:5.1f}  → {s.rsi.signal or '-'}")
    print(f"  MACD ({s.macd.macd:+.2f})  Sig: {s.macd.signal_line:+.2f}  Hist: {s.macd.histogram:+.2f}  → {s.macd.signal or '-'}")
    print(f"  BB   %B={s.bb.pct_b:.2f}  上: {s.bb.upper:,.0f}  中: {s.bb.middle:,.0f}  下: {s.bb.lower:,.0f}  → {s.bb.signal or '-'}")
    print(f"{'─'*48}")
    status_icon = {"confirmed": "[OK]", "candidate": "[--]", "none": "[  ]"}.get(s.status, "")
    print(f"  {status_icon} {s.status.upper()}  方向: {s.direction or 'なし'}  一致: {s.match_count}/3  [{', '.join(s.matched_indicators)}]")
    print(f"{'='*48}\n")


if __name__ == "__main__":
    main()
