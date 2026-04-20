# セットアップ手順

stock-analysis プロジェクトをゼロから動かすまでの手順。

---

## 1. Hermes Agentのインストール

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
hermes setup   # モデル・Telegram Bot Tokenなどを対話式で設定
```

---

## 2. プロジェクトディレクトリの準備

```bash
mkdir -p stock-analysis
cd stock-analysis

# 必要なディレクトリを作成
mkdir -p data/raw data/signals data/monthly_report logs scripts
mkdir -p .hermes/skills

# このプロジェクトのファイルを配置
# AGENTS.md          → stock-analysis/AGENTS.md
# scripts/*.py       → stock-analysis/scripts/
# .hermes/skills/*.md → stock-analysis/.hermes/skills/
# cron/setup-cron.sh → stock-analysis/cron/setup-cron.sh
```

---

## 3. watchlist.csvの作成

```csv
ticker
7203.T
9984.T
6758.T
8306.T
9432.T
```

- ティッカーは東証コード + `.T`（例: トヨタ → `7203.T`）
- 銘柄数は最初は5〜10銘柄推奨（3並列で処理するため）

`watchlist_extended.csv` はシグナル0件時のフォールバック用。
メインより広い20〜30銘柄を入れておく。

---

## 4. 動作確認（スクリプト単体）

```bash
cd stock-analysis

# データ取得テスト（2〜3銘柄で確認）
python scripts/fetch_data.py --tickers 7203.T 9984.T --days 30

# 指標計算テスト
python scripts/indicators.py --ticker 7203.T

# シグナル確認（JSONで出力）
python scripts/indicators.py --ticker 7203.T --json

# リスク計算テスト（サンプルのsignals JSONLが必要）
python scripts/risk_calc.py --date $(date +%Y%m%d) --capital 1000000
```

---

## 5. gatewayの起動

```bash
# ユーザーサービスとしてインストール（ログイン時に自動起動）
hermes gateway install

# 起動確認
hermes gateway status

# ログ確認
hermes gateway logs
```

---

## 6. Skillsの登録

Hermesはプロジェクト内の `.hermes/skills/` を自動で読む。
追加設定は不要。動作確認コマンド:

```bash
cd stock-analysis
hermes chat -q "利用可能なskillsを一覧表示して"
```

---

## 7. cronジョブの登録

```bash
cd stock-analysis
CAPITAL=1000000 bash cron/setup-cron.sh
```

登録後の確認:

```bash
hermes cron list
# stock-analysis-daily         毎平日 08:00
# stock-analysis-monthly-review 毎月28〜31日 20:00
```

---

## 8. 動作テスト（即時実行）

```bash
# 本番と同じフローを今すぐ実行
hermes cron run stock-analysis-daily

# 実行ログを確認
tail -f logs/$(date +%Y%m%d).log

# Telegramに配信されたか確認
# → スマホのTelegramで確認
```

---

## 9. よく使う運用コマンド

```bash
# ジョブ一覧
hermes cron list

# 手動実行
hermes cron run stock-analysis-daily

# 一時停止（休暇中など）
hermes cron pause stock-analysis-daily

# 再開
hermes cron resume stock-analysis-daily

# 総資産が変わったとき（ジョブのプロンプトを更新）
hermes cron edit stock-analysis-daily --prompt "..."

# 直近のcron実行ログ
ls ~/.hermes/cron/output/stock-analysis-daily/

# 当日の実行ログ
cat logs/$(date +%Y%m%d).log
```

---

## 10. 月次レビューの手動実行

```bash
hermes cron run stock-analysis-monthly-review
```

[SILENT] で終了した場合は最終営業日ではないと判定されている。
強制的に月次集計だけ走らせたい場合はCLIから直接実行:

```bash
hermes chat --cwd stock-analysis \
  -q "data/performance_log.csvを集計して今月の勝率と平均損益率を出して"
```

---

## トラブルシューティング

| 症状 | 確認場所 | 対処 |
|------|----------|------|
| Telegramに届かない | `logs/YYYYMMDD.log` | TELEGRAM_BOT_TOKEN / CHAT_IDを確認 |
| データ取得が全失敗 | `logs/YYYYMMDD.log` | yfinanceのレート制限 → 30分後にリトライ |
| シグナルが毎日0件 | `data/signals/` | watchlistの銘柄・指標パラメータを見直す |
| cronが起動しない | `hermes gateway status` | gatewayが落ちていれば `hermes gateway restart` |
