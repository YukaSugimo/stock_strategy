---
name: signal-analysis
description: RSI・MACD・Bollinger Bandsの3指標でシグナルを判定する。AGENTS.mdのStep 3でdelegate_taskから並列呼び出しされる。
version: 1.0.0
metadata:
  hermes:
    tags: [stock, signal, RSI, MACD, bollinger, technical-analysis]
    category: finance
    requires_toolsets: [terminal]
---

# signal-analysis

## When to Use

AGENTS.mdのStep 3（シグナル分析）で `delegate_task` から呼ばれたとき。
1回の呼び出しで1銘柄を処理する。最大3銘柄が並列実行される。

## Inputs

- `ticker`：対象銘柄コード（例: `7203.T`）
- `data_path`：OHLCVのCSVパス（例: `data/raw/20240415/7203.T.csv`）
- `mode`：`normal`（デフォルト）または `alert`（警戒モード）

## Procedure

### 1. データ読み込み

```python
import pandas as pd
import numpy as np

df = pd.read_csv(data_path, index_col=0, parse_dates=True)
close = df["Close"].squeeze()
```

### 2. 指標計算

#### RSI(14)

```python
delta = close.diff()
gain = delta.clip(lower=0)
loss = -delta.clip(upper=0)
avg_gain = gain.rolling(14).mean()
avg_loss = loss.rolling(14).mean()
rs = avg_gain / avg_loss
rsi = 100 - (100 / (1 + rs))
rsi_latest = rsi.iloc[-1]
rsi_prev = rsi.iloc[-2]
```

#### MACD

```python
ema12 = close.ewm(span=12, adjust=False).mean()
ema26 = close.ewm(span=26, adjust=False).mean()
macd = ema12 - ema26
signal_line = macd.ewm(span=9, adjust=False).mean()
macd_latest = macd.iloc[-1]
macd_prev = macd.iloc[-2]
sig_latest = signal_line.iloc[-1]
sig_prev = signal_line.iloc[-2]
```

#### Bollinger Bands(20, 2)

```python
sma20 = close.rolling(20).mean()
std20 = close.rolling(20).std()
upper = sma20 + 2 * std20
lower = sma20 - 2 * std20
price_latest = close.iloc[-1]
price_prev = close.iloc[-2]
lower_latest = lower.iloc[-1]
lower_prev = lower.iloc[-2]
upper_latest = upper.iloc[-1]
upper_prev = upper.iloc[-2]
```

### 3. シグナル判定

```python
signals = {}

# RSI
if rsi_prev <= 30 and rsi_latest > rsi_prev:
    signals["RSI"] = "buy"
elif rsi_prev >= 70 and rsi_latest < rsi_prev:
    signals["RSI"] = "sell"

# MACD
if macd_prev < sig_prev and macd_latest > sig_latest:
    signals["MACD"] = "buy"
elif macd_prev > sig_prev and macd_latest < sig_latest:
    signals["MACD"] = "sell"

# Bollinger Bands
if price_prev <= lower_prev and price_latest > lower_latest:
    signals["BB"] = "buy"
elif price_prev >= upper_prev and price_latest < upper_latest:
    signals["BB"] = "sell"
```

### 4. 確定・候補の分類

```python
buy_count = sum(1 for v in signals.values() if v == "buy")
sell_count = sum(1 for v in signals.values() if v == "sell")

# 警戒モードは追加条件を課す
def meets_alert_criteria(direction):
    if direction == "buy":
        return rsi_latest <= 25
    elif direction == "sell":
        return rsi_latest >= 75
    return False

if buy_count == 3:
    direction = "buy"
    status = "confirmed" if mode != "alert" or meets_alert_criteria("buy") else "candidate"
elif sell_count == 3:
    direction = "sell"
    status = "confirmed" if mode != "alert" or meets_alert_criteria("sell") else "candidate"
elif buy_count == 2:
    direction = "buy"
    status = "candidate"
elif sell_count == 2:
    direction = "sell"
    status = "candidate"
else:
    direction = None
    status = "none"
```

### 5. 結果の出力

```python
import json
from datetime import datetime

result = {
    "ticker": ticker,
    "date": datetime.today().strftime("%Y-%m-%d"),
    "direction": direction,
    "status": status,  # confirmed / candidate / none
    "indicators": {
        "RSI": round(rsi_latest, 2),
        "RSI_signal": signals.get("RSI"),
        "MACD_signal": signals.get("MACD"),
        "BB_signal": signals.get("BB"),
    },
    "price": round(price_latest, 2),
}

# シグナルファイルに追記
today = datetime.today().strftime("%Y%m%d")
signal_path = f"data/signals/{today}.jsonl"
with open(signal_path, "a") as f:
    f.write(json.dumps(result, ensure_ascii=False) + "\n")

print(json.dumps(result, ensure_ascii=False, indent=2))
```

## Output

- `data/signals/YYYYMMDD.jsonl`：1銘柄1行のJSONL形式
- `status` フィールド：`confirmed` / `candidate` / `none`

## Pitfalls

- データが60営業日未満の場合、指標の計算に `NaN` が含まれる。`dropna()` してから判定する
- 競合シグナル（buy/sellが混在）はカウントに含めない。同一方向のみを集計する
- Bollinger Bandsのシグナルは「タッチして反発」が条件。前日と当日の価格・バンドの位置を両方確認する

## Verification

```bash
cat data/signals/$(date +%Y%m%d).jsonl | python3 -c "
import sys, json
for line in sys.stdin:
    r = json.loads(line)
    print(r['ticker'], r['status'], r['direction'])
"
```
