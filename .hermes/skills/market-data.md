---
name: market-data
description: 日本株の市場データをyfinanceで取得してdata/raw/に保存する。AGENTS.mdのStep 2で使用。
version: 1.0.0
metadata:
  hermes:
    tags: [stock, japan, yfinance, data]
    category: finance
    requires_toolsets: [terminal]
---

# market-data

## When to Use

AGENTS.mdのStep 2（データ取得）で呼ばれたとき。
`delegate_task` から起動されることを想定している。

## Inputs

- `data/watchlist.csv`：取得対象の銘柄コード一覧（列名: `ticker`、形式: `7203.T` など）
- 引数として日付を受け取った場合はその日付、なければ今日の日付を使う

## Procedure

### 1. 依存ライブラリの確認

```bash
pip show yfinance pandas > /dev/null 2>&1 || pip install yfinance pandas --quiet
```

### 2. watchlist.csvの読み込み

```python
import pandas as pd
tickers = pd.read_csv("data/watchlist.csv")["ticker"].tolist()
```

### 3. OHLCVデータの取得

```python
import yfinance as yf
from datetime import datetime, timedelta
import os

today = datetime.today().strftime("%Y%m%d")
output_dir = f"data/raw/{today}"
os.makedirs(output_dir, exist_ok=True)

failed = []
for ticker in tickers:
    try:
        df = yf.download(ticker, period="90d", interval="1d", progress=False)
        if df.empty:
            raise ValueError("empty dataframe")
        df.to_csv(f"{output_dir}/{ticker}.csv")
    except Exception as e:
        failed.append({"ticker": ticker, "reason": str(e)})

# 失敗銘柄をログに記録
if failed:
    import json
    with open(f"logs/{today}.log", "a") as f:
        f.write(f"[market-data] skipped: {json.dumps(failed)}\n")
```

### 4. 出力の検証

取得できた銘柄数と失敗した銘柄数を返す。

```python
success = len(tickers) - len(failed)
print(f"取得完了: {success}/{len(tickers)} 銘柄")
if failed:
    print(f"スキップ: {[f['ticker'] for f in failed]}")
```

## Output

- `data/raw/YYYYMMDD/{ticker}.csv`：OHLCV（Open/High/Low/Close/Volume）
- 失敗銘柄は `logs/YYYYMMDD.log` に記録

## Pitfalls

- yfinanceの日本株ティッカーは末尾に `.T` が必要（例: `7203.T`）
- 祝日・非営業日はデータが返らないことがある。`df.empty` チェックで判定する
- レート制限に引っかかった場合は `time.sleep(1)` を各ループに追加する
- `90d` で取得して直近60営業日分を確保する（祝日バッファ）

## Verification

```bash
ls data/raw/$(date +%Y%m%d)/ | wc -l
# watchlist.csvの行数と一致することを確認
```
