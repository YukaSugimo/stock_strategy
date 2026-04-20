"""
engine.py

バックテスト共通ロジック。
backtest.py と optimize.py の両方から呼び出す。

主な機能:
- 1銘柄のバックテスト実行
- 上場廃止・データ不足の自動スキップ
- エラーのログ出力
"""

import importlib
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import pandas as pd
    import yaml
    import yfinance as yf
except ImportError:
    print("[engine] 必要なライブラリが未インストールです。", file=sys.stderr)
    print("  pip install yfinance pandas pyyaml", file=sys.stderr)
    sys.exit(1)

STOP_LOSS_PCT   = 0.03
TAKE_PROFIT_PCT = 0.06
HOLD_DAYS_MAX   = 10


# ── ログ設定 ──────────────────────────────────────────────────────────────────

def get_logger(log_dir: str = "logs") -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{datetime.today().strftime('%Y%m%d')}.log")

    logger = logging.getLogger("engine")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    logger.addHandler(fh)
    return logger


# ── 戦略ロード ────────────────────────────────────────────────────────────────

def load_strategy(strategy_name: str):
    scripts_dir = Path(__file__).parent
    sys.path.insert(0, str(scripts_dir))

    module = importlib.import_module(f"strategies.{strategy_name}")
    class_name = "".join(part.capitalize() for part in strategy_name.split("_"))
    strategy_cls = getattr(module, class_name)

    params_path = scripts_dir.parent / "params" / f"{strategy_name}.yaml"
    if not params_path.exists():
        raise FileNotFoundError(f"パラメータファイルが見つかりません: {params_path}")

    with open(params_path, encoding="utf-8") as f:
        params = yaml.safe_load(f)

    return strategy_cls(params)


def load_strategy_with_params(strategy_name: str, params: dict):
    scripts_dir = Path(__file__).parent
    sys.path.insert(0, str(scripts_dir))

    module = importlib.import_module(f"strategies.{strategy_name}")
    class_name = "".join(part.capitalize() for part in strategy_name.split("_"))
    strategy_cls = getattr(module, class_name)
    return strategy_cls(params)


# ── データ取得 ────────────────────────────────────────────────────────────────

# 上場廃止・取得不可と判断するキーワード
_DELISTED_KEYWORDS = [
    "possibly delisted",
    "no timezone found",
    "not found",
    "404",
]


def fetch_ohlcv(ticker: str, days: int) -> pd.DataFrame | None:
    """
    yfinanceでOHLCVを取得する。
    上場廃止・404エラーの場合はNoneを返す（例外は出さない）。
    """
    logger = get_logger()

    end   = datetime.today()
    start = end - timedelta(days=days)

    # yfinanceのstderrノイズを抑制
    import io
    import contextlib

    stderr_capture = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr_capture):
            df = yf.download(
                ticker,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval="1d",
                progress=False,
                auto_adjust=True,
            )
    except Exception as e:
        logger.error(f"{ticker}: データ取得例外 {e}")
        return None

    stderr_text = stderr_capture.getvalue().lower()

    # 上場廃止・404を検出
    if any(kw in stderr_text for kw in _DELISTED_KEYWORDS):
        logger.warning(f"{ticker}: 上場廃止またはデータなし（スキップ）")
        return None

    if df is None or df.empty:
        logger.warning(f"{ticker}: 空データ（スキップ）")
        return None

    if len(df) < 40:
        logger.warning(f"{ticker}: データ不足 {len(df)}行（スキップ）")
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df


# ── 1銘柄バックテスト ─────────────────────────────────────────────────────────

def backtest_one(ticker: str, days: int, strategy_name: str, params: dict = None) -> dict:
    """
    1銘柄のバックテストを実行する。
    ProcessPoolExecutorから呼ばれるため、importは関数内で完結させる。

    Args:
        ticker:        銘柄コード
        days:          バックテスト期間（日数）
        strategy_name: 戦略名
        params:        パラメータ辞書。Noneの場合はyamlから読み込む

    Returns:
        {
            ticker, trades, trade_count, win_rate,
            avg_pnl, total_return, max_drawdown, error, skipped
        }
    """
    logger = get_logger()

    try:
        if params is not None:
            strategy = load_strategy_with_params(strategy_name, params)
        else:
            strategy = load_strategy(strategy_name)

        df = fetch_ohlcv(ticker, days)

        if df is None:
            return {
                "ticker":   ticker,
                "trades":   [],
                "trade_count": 0,
                "win_rate": None,
                "avg_pnl":  None,
                "total_return": 0.0,
                "max_drawdown": 0.0,
                "error":    None,
                "skipped":  True,
            }

        sl_pct  = strategy.params.get("stop_loss_pct",   STOP_LOSS_PCT)
        tp_pct  = strategy.params.get("take_profit_pct", TAKE_PROFIT_PCT)
        hd_max  = int(strategy.params.get("hold_days_max",   HOLD_DAYS_MAX))

        close  = df["Close"].astype(float).squeeze()
        volume = df["Volume"].astype(float).squeeze() if "Volume" in df.columns else None
        trades = []

        window      = 40
        i           = window
        in_trade    = False
        entry_price = 0.0
        entry_date  = None
        direction   = None
        hold_days   = 0

        while i < len(close):
            price = float(close.iloc[i])
            date  = str(close.index[i])[:10]

            if in_trade:
                hold_days += 1
                pnl_pct = (price - entry_price) / entry_price
                if direction == "sell":
                    pnl_pct = -pnl_pct

                result = None
                if pnl_pct <= -sl_pct:
                    result = "stop_loss"
                elif pnl_pct >= tp_pct:
                    result = "take_profit"
                elif hold_days >= hd_max:
                    result = "timeout"

                if result:
                    trades.append({
                        "ticker":      ticker,
                        "entry_date":  entry_date,
                        "exit_date":   date,
                        "direction":   direction,
                        "entry_price": round(entry_price, 2),
                        "exit_price":  round(price, 2),
                        "pnl_pct":     round(pnl_pct * 100, 2),
                        "result":      result,
                        "hold_days":   hold_days,
                    })
                    in_trade = False

                i += 1
                continue

            window_close  = close.iloc[i - window: i + 1]
            window_volume = volume.iloc[i - window: i + 1] if volume is not None else None

            try:
                sig = strategy.generate_signal(window_close, window_volume)
            except Exception as e:
                logger.debug(f"{ticker} i={i}: シグナル計算エラー {e}")
                i += 1
                continue

            if sig["signal"] in ("buy", "sell"):
                in_trade    = True
                entry_price = price
                entry_date  = date
                direction   = sig["signal"]
                hold_days   = 0

            i += 1

        if not trades:
            return {
                "ticker": ticker, "trades": [],
                "trade_count": 0, "win_rate": None,
                "avg_pnl": None, "total_return": 0.0,
                "max_drawdown": 0.0, "error": None, "skipped": False,
            }

        pnls         = [t["pnl_pct"] for t in trades]
        wins         = [p for p in pnls if p > 0]
        win_rate     = len(wins) / len(pnls) * 100
        avg_pnl      = sum(pnls) / len(pnls)
        total_return = sum(pnls)

        cumulative = 0.0
        peak       = 0.0
        max_dd     = 0.0
        for p in pnls:
            cumulative += p
            peak   = max(peak, cumulative)
            max_dd = max(max_dd, peak - cumulative)

        return {
            "ticker":       ticker,
            "trades":       trades,
            "trade_count":  len(trades),
            "win_rate":     round(win_rate, 1),
            "avg_pnl":      round(avg_pnl, 2),
            "total_return": round(total_return, 2),
            "max_drawdown": round(max_dd, 2),
            "error":        None,
            "skipped":      False,
        }

    except Exception as e:
        logger.error(f"{ticker}: バックテスト例外 {e}")
        return {"ticker": ticker, "error": str(e), "trades": [], "skipped": False}


# ── 集計 ──────────────────────────────────────────────────────────────────────

def calc_summary(results: list) -> dict:
    """バックテスト結果リストからサマリーを生成する。"""
    all_trades = []
    for r in results:
        all_trades.extend(r.get("trades", []))

    if not all_trades:
        return {
            "trade_count":   0,
            "win_rate":      None,
            "avg_pnl":       None,
            "profit_factor": None,
            "max_drawdown":  None,
            "total_return":  0.0,
        }

    pnls   = [t["pnl_pct"] for t in all_trades]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    cumulative = 0.0
    peak       = 0.0
    max_dd     = 0.0
    for p in pnls:
        cumulative += p
        peak   = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)

    return {
        "trade_count":   len(all_trades),
        "win_rate":      round(len(wins) / len(pnls) * 100, 1),
        "avg_pnl":       round(sum(pnls) / len(pnls), 2),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else None,
        "max_drawdown":  round(max_dd, 2),
        "total_return":  round(sum(pnls), 2),
    }
