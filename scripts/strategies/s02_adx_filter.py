import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from strategies.base import BaseStrategy
from indicators import calc_rsi, calc_macd, calc_bb


def _calc_adx(close: pd.Series, period: int = 14) -> float:
    """
    終値のみを使ったADX近似値を計算する。
    engine.py が close しか渡さないため、高値・安値の代わりに終値変化幅を TR として使う。
    Wilder's smoothing (EWM alpha=1/period) で平滑化する。
    """
    delta = close.diff()

    plus_dm  = delta.clip(lower=0)
    minus_dm = (-delta).clip(lower=0)
    tr       = delta.abs()

    alpha = 1.0 / period
    sm_plus  = plus_dm.ewm(alpha=alpha, adjust=False).mean()
    sm_minus = minus_dm.ewm(alpha=alpha, adjust=False).mean()
    sm_tr    = tr.ewm(alpha=alpha, adjust=False).mean()

    tr_safe  = sm_tr.replace(0, float("nan"))
    plus_di  = 100.0 * sm_plus  / tr_safe
    minus_di = 100.0 * sm_minus / tr_safe

    denom = (plus_di + minus_di).replace(0, float("nan"))
    dx    = 100.0 * (plus_di - minus_di).abs() / denom
    adx   = dx.ewm(alpha=alpha, adjust=False).mean()

    return float(adx.iloc[-1])


class S02AdxFilter(BaseStrategy):

    def generate_signal(self, close: pd.Series, volume=None) -> dict:
        p = self.params

        adx_period    = p.get("adx_period",    14)
        adx_threshold = p.get("adx_threshold", 25)
        adx_value     = _calc_adx(close, period=adx_period)

        rsi  = calc_rsi(close,  period=p.get("rsi_period", 14))
        macd = calc_macd(close, fast=p.get("macd_fast", 12),
                                slow=p.get("macd_slow", 26),
                                signal_span=p.get("macd_signal", 9))
        bb   = calc_bb(close,   period=p.get("bb_period", 20),
                                k=p.get("bb_std", 2.0))

        indicators = {
            "rsi":      rsi.value,
            "macd":     macd.macd,
            "bb_pct_b": bb.pct_b,
            "adx":      round(adx_value, 2),
        }

        if not (adx_value > adx_threshold):  # NaN / adx<=threshold どちらもスキップ
            return {"signal": None, "status": "none", "indicators": indicators}

        buy_threshold  = p.get("rsi_buy_threshold",  30)
        sell_threshold = p.get("rsi_sell_threshold", 70)

        signals = {}
        if rsi.prev <= buy_threshold and rsi.signal == "buy":
            signals["RSI"] = "buy"
        if rsi.prev >= sell_threshold and rsi.signal == "sell":
            signals["RSI"] = "sell"
        if macd.signal:
            signals["MACD"] = macd.signal
        if bb.signal:
            signals["BB"] = bb.signal

        entry     = p.get("entry_condition", "2of3")
        threshold = 3 if entry == "3of3" else 2

        buy_count  = sum(1 for v in signals.values() if v == "buy")
        sell_count = sum(1 for v in signals.values() if v == "sell")

        if buy_count >= threshold:
            return {
                "signal": "buy",
                "status": "confirmed" if buy_count == 3 else "candidate",
                "indicators": indicators,
            }
        elif sell_count >= threshold:
            return {
                "signal": "sell",
                "status": "confirmed" if sell_count == 3 else "candidate",
                "indicators": indicators,
            }
        else:
            return {"signal": None, "status": "none", "indicators": indicators}
