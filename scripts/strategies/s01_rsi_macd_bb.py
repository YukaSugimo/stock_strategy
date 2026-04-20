import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from strategies.base import BaseStrategy
from indicators import calc_rsi, calc_macd, calc_bb


class S01RsiMacdBb(BaseStrategy):

    def generate_signal(self, close: pd.Series, volume=None) -> dict:
        p = self.params

        rsi  = calc_rsi(close,  period=p.get("rsi_period", 14))
        macd = calc_macd(close, fast=p.get("macd_fast", 12),
                                slow=p.get("macd_slow", 26),
                                signal_span=p.get("macd_signal", 9))
        bb   = calc_bb(close,   period=p.get("bb_period", 20),
                                k=p.get("bb_std", 2.0))

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

        entry = p.get("entry_condition", "3of3")
        threshold = 3 if entry == "3of3" else 2

        buy_count  = sum(1 for v in signals.values() if v == "buy")
        sell_count = sum(1 for v in signals.values() if v == "sell")

        if buy_count >= threshold:
            return {
                "signal": "buy",
                "status": "confirmed" if buy_count == 3 else "candidate",
                "indicators": {
                    "rsi":      rsi.value,
                    "macd":     macd.macd,
                    "bb_pct_b": bb.pct_b,
                },
            }
        elif sell_count >= threshold:
            return {
                "signal": "sell",
                "status": "confirmed" if sell_count == 3 else "candidate",
                "indicators": {
                    "rsi":      rsi.value,
                    "macd":     macd.macd,
                    "bb_pct_b": bb.pct_b,
                },
            }
        else:
            return {
                "signal": None,
                "status": "none",
                "indicators": {
                    "rsi":      rsi.value,
                    "macd":     macd.macd,
                    "bb_pct_b": bb.pct_b,
                },
            }
