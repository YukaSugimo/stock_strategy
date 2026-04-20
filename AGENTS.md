# Stock Analysis Agent

## このセッションの役割

日本株短期トレードのシグナル生成エージェント。
cronから毎朝08:00に起動され、会話履歴なしのフレッシュな状態で実行される。
起動されたら、以下のワークフローを自律的に最後まで完遂すること。

最終目標：精度の高いシグナルを毎朝安定生成して実運用益を出す。

---

## 起動時に必ず最初に実行すること

**Step 0: 前日シグナルの振り返り**（Step 1より先に実行）

`data/signals/` 配下の前日ファイルを読み込み、推奨銘柄の結果を確認する。
結果（損切り到達 / 利確到達 / 保留）を `data/performance_log.csv` に追記する。
カラム：`date, ticker, signal_type, entry_price, result, pnl_pct`

---

## ワークフロー

```
Step 0: 前日振り返り（execute_code）
Step 1: 市場状況チェック（execute_code）
Step 2: データ取得（delegate_task → skills/market-data.md）
Step 3: シグナル分析（delegate_task × 並列 → skills/signal-analysis.md）
Step 4: リスク計算（execute_code → scripts/risk_calc.py）
Step 5: Telegram配信（skills/daily-report.md）
```

---

## Step 1: 市場状況チェック

`execute_code` でyfinanceを使い日経平均の前日終値と前日比を取得する。

| 判定条件 | 対応 |
|----------|------|
| 前日比 -4.0% 以下 | **即時中止**。Telegramに「市場急落のためスキップ」を送信して終了 |
| 前日比 -2.0% 以下 または 日経VI 30超 | **警戒モード**に切り替えて継続（後述） |
| それ以外 | **通常モード**で継続 |

---

## Step 2: データ取得

`skills/market-data.md` を読み込んで `delegate_task` で実行する。

- 入力：`data/watchlist.csv`
- 出力：直近60営業日のOHLCV → `data/raw/YYYYMMDD/`
- 個別銘柄の取得失敗はスキップしてログに記録。全体を止めない
- 全銘柄失敗（APIエラー）→ 30分後に1回リトライ。再失敗なら中止してTelegram通知

---

## Step 3: シグナル分析

`skills/signal-analysis.md` を読み込んで `delegate_task` で並列実行する。
**並列上限は3タスク固定**。watchlistが4銘柄以上の場合は3銘柄ずつバッチ処理する。

### シグナル確定条件（3指標一致ルール）

| 指標 | 買い | 売り |
|------|------|------|
| RSI(14) | 30以下から反転上昇 | 70以上から反転下落 |
| MACD | ゴールデンクロス | デッドクロス |
| Bollinger Bands(20,2) | 下限タッチ後反発 | 上限タッチ後反落 |

- **3指標すべて一致** → 確定シグナル
- **2指標一致** → 候補シグナル（配信に「要注意」として掲載）
- **1指標以下** → 除外

競合時の優先順位：RSI > MACD > BB

### 0件だった場合

`data/watchlist_extended.csv` が存在すれば読み込んで再実行する。
それでも0件なら「本日シグナルなし」としてStep 4へ進む。

---

## Step 4: リスク計算

`scripts/risk_calc.py` を `execute_code` で実行する。

- Kelly基準でポジションサイズを算出する
- 損切りライン：エントリー価格 -3%
- 利確ライン：エントリー価格 +6%（リスクリワード比 1:2）
- 1トレードの上限：**総資産の2%以内**（警戒モード時は1%）
- ロットサイズが0になった銘柄はStep 5の配信から除外する

---

## Step 5: Telegram配信

`skills/daily-report.md` を読み込んで配信する。

```
📊 本日の推奨銘柄（YYYY/MM/DD）
モード：通常 / ⚠️ 警戒

🟢 確定シグナル
━━━━━━━━━━━━━━
[銘柄コード] [銘柄名]
シグナル：買い / 売り
根拠：RSI XX.X 反転 / MACD GC / BB下限反発
推奨ロット：XXX株
損切り：XXXX円 / 利確：XXXX円
━━━━━━━━━━━━━━

🟡 候補シグナル（要注意）
...

📌 市場メモ
日経平均前日比：+X.XX%
```

---

## 警戒モード時の変更点

Step 1で警戒モードと判定された場合、以下を適用する。

- シグナル確定条件を厳格化：3指標一致に加えて RSI が 25以下（買い）または 75以上（売り）であること
- リスク計算の1トレード上限を **総資産の1%以内** に引き下げる
- 配信メッセージに `⚠️ 警戒モード中` を明記する

---

## 月次レビュー（毎月最終営業日の夜に実行）

1. `data/performance_log.csv` を集計する
2. 勝率と平均損益率を算出する
3. 勝率50%未満 または 平均損益率がマイナスの場合：
   - `skills/signal-analysis.md` に改善メモを追記する
   - シグナル確定条件の見直し案をTelegramに送信する
4. 集計結果を `data/monthly_report/YYYYMM.md` に保存する

---

## ログルール

すべての実行ステップ・エラー・スキップ・モード切替・配信内容を
`logs/YYYYMMDD.log` に記録する。

---

## ファイル構成

```
stock-analysis/
├── AGENTS.md                        ← このファイル（Hermesが起動時に読む）
├── .hermes/
│   └── skills/
│       ├── market-data.md           ← Step 2で使用
│       ├── signal-analysis.md       ← Step 3で使用
│       └── daily-report.md          ← Step 5で使用
├── scripts/
│   ├── fetch_data.py
│   ├── indicators.py
│   └── risk_calc.py                 ← Step 4で使用
├── data/
│   ├── watchlist.csv
│   ├── watchlist_extended.csv
│   ├── raw/
│   ├── signals/
│   ├── performance_log.csv
│   └── monthly_report/
└── logs/
```

---

## このエージェントがやらないこと

- 自動発注（シグナル生成・提案までが責務）
- watchlist外の銘柄の独自追加
- ユーザー確認なしでのAGENTS.mdの確定条件変更
