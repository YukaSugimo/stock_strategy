---
name: daily-report
description: シグナル分析の結果をTelegramに配信する。AGENTS.mdのStep 5で使用。
version: 1.0.0
metadata:
  hermes:
    tags: [telegram, report, notification, stock]
    category: finance
    requires_toolsets: [terminal]
required_environment_variables:
  - name: TELEGRAM_BOT_TOKEN
    prompt: Telegram Bot Token（@BotFatherで取得）
    help: https://core.telegram.org/bots#creating-a-new-bot
    required_for: Telegram配信
  - name: TELEGRAM_CHAT_ID
    prompt: 配信先のChat ID（@userinfobot で確認）
    required_for: Telegram配信
---

# daily-report

## When to Use

AGENTS.mdのStep 5（Telegram配信）で呼ばれたとき。
`data/signals/YYYYMMDD.jsonl` を読み込み、整形してTelegramに送信する。

## Inputs

- `data/signals/YYYYMMDD.jsonl`：signal-analysisが出力したJSONL
- `mode`：`normal` または `alert`（警戒モード表示に使用）
- `nikkei_change`：日経平均前日比（%）

## Procedure

### 1. シグナルデータの読み込みと分類

```python
import json
from datetime import datetime

today = datetime.today().strftime("%Y%m%d")
signal_path = f"data/signals/{today}.jsonl"

confirmed = []
candidates = []

try:
    with open(signal_path) as f:
        for line in f:
            r = json.loads(line)
            if r["status"] == "confirmed":
                confirmed.append(r)
            elif r["status"] == "candidate":
                candidates.append(r)
except FileNotFoundError:
    pass  # シグナルなしとして処理
```

### 2. 銘柄名の取得

```python
# yfinanceから銘柄名を取得
import yfinance as yf

def get_name(ticker):
    try:
        return yf.Ticker(ticker).info.get("longName", ticker)
    except:
        return ticker
```

### 3. メッセージ整形

```python
date_str = datetime.today().strftime("%Y/%m/%d")
mode_str = "⚠️ 警戒モード中" if mode == "alert" else "通常"
nikkei_str = f"{nikkei_change:+.2f}%" if nikkei_change is not None else "取得失敗"

lines = [
    f"📊 本日の推奨銘柄（{date_str}）",
    f"モード：{mode_str}",
    "",
]

if confirmed:
    lines.append("🟢 確定シグナル")
    for r in confirmed:
        name = get_name(r["ticker"])
        ind = r["indicators"]
        reasons = []
        if ind.get("RSI_signal"):
            reasons.append(f"RSI {ind['RSI']:.1f} 反転")
        if ind.get("MACD_signal") == "buy":
            reasons.append("MACD GC")
        elif ind.get("MACD_signal") == "sell":
            reasons.append("MACD DC")
        if ind.get("BB_signal") == "buy":
            reasons.append("BB下限反発")
        elif ind.get("BB_signal") == "sell":
            reasons.append("BB上限反落")

        direction_str = "買い" if r["direction"] == "buy" else "売り"
        lines += [
            "━━━━━━━━━━━━━━",
            f"[{r['ticker']}] {name}",
            f"シグナル：{direction_str}",
            f"根拠：{' / '.join(reasons)}",
            f"現在値：{r['price']} 円",
            "（ロット・損切り・利確はrisk-managerの結果を参照）",
        ]
    lines.append("━━━━━━━━━━━━━━")
    lines.append("")

if candidates:
    lines.append("🟡 候補シグナル（要注意）")
    for r in candidates:
        name = get_name(r["ticker"])
        direction_str = "買い" if r["direction"] == "buy" else "売り"
        lines.append(f"・{r['ticker']} {name}：{direction_str}（2指標一致）")
    lines.append("")

if not confirmed and not candidates:
    lines.append("本日のシグナルはありません。")
    lines.append("")

lines += [
    "📌 市場メモ",
    f"日経平均前日比：{nikkei_str}",
]

message = "\n".join(lines)
```

### 4. Telegram送信

```python
import os
import urllib.request
import urllib.parse

token = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]

payload = urllib.parse.urlencode({
    "chat_id": chat_id,
    "text": message,
    "parse_mode": "HTML",
}).encode()

req = urllib.request.Request(
    f"https://api.telegram.org/bot{token}/sendMessage",
    data=payload,
    method="POST"
)

with urllib.request.urlopen(req) as resp:
    result = json.loads(resp.read())
    if not result.get("ok"):
        raise RuntimeError(f"Telegram送信失敗: {result}")

print("配信完了")
```

### 5. ログへの記録

```python
log_path = f"logs/{today}.log"
with open(log_path, "a") as f:
    f.write(f"[daily-report] 配信完了\n{message}\n---\n")
```

## Output

- Telegramへの配信
- `logs/YYYYMMDD.log` に配信内容を追記

## Pitfalls

- `TELEGRAM_BOT_TOKEN` と `TELEGRAM_CHAT_ID` が未設定だと送信失敗。`hermes setup` で登録する
- メッセージが4096文字を超えるとTelegram APIがエラーを返す。銘柄数が多い場合は分割して送信する
- `parse_mode: HTML` を使う場合、`<` `>` `&` はエスケープが必要。銘柄名にこれらが含まれる場合は除去する

## Verification

```bash
# 直近のログで配信内容を確認
tail -50 logs/$(date +%Y%m%d).log
```
