#!/bin/bash
# setup-cron.sh
#
# 株価分析プロジェクトの Hermes cronジョブを登録するスクリプト。
# stock-analysis/ ディレクトリで実行すること。
#
# 前提:
#   - hermes gateway が起動済み（hermes gateway install 済み）
#   - TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID が hermes setup 済み
#   - CAPITAL（総資産）を環境変数またはここに直接記載する
#
# Usage:
#   bash cron/setup-cron.sh
#   CAPITAL=1000000 bash cron/setup-cron.sh

set -e

CAPITAL=${CAPITAL:-1000000}
PROJECT_DIR=$(pwd)

echo "========================================"
echo "  株価分析 cronジョブ セットアップ"
echo "  プロジェクトDir : $PROJECT_DIR"
echo "  総資産          : ${CAPITAL} 円"
echo "========================================"
echo ""

# ── ジョブ1: 毎朝の分析メイン（平日 08:00）────────────────────────────────────

echo "[1/2] 毎朝分析ジョブを登録..."

hermes cron create "0 8 * * 1-5" \
  --name "stock-analysis-daily" \
  --skill market-data \
  --skill signal-analysis \
  --skill daily-report \
  --deliver "telegram" \
  --cwd "$PROJECT_DIR" \
  -- \
"AGENTS.mdのワークフローを実行せよ。

プロジェクトディレクトリ: $PROJECT_DIR
総資産: ${CAPITAL}円

Step 0: 前日シグナル振り返り（data/signals/ の前日ファイルを読み、data/performance_log.csv に追記）
Step 1: 市場状況チェック（yfinanceで日経平均^N225の前日比を取得）
        - 前日比 -4.0% 以下 → Telegramに「市場急落のためスキップ」を送信して終了
        - 前日比 -2.0% 以下 または 日経VI 30超 → modeをalertに設定
        - それ以外 → modeをnormalに設定
Step 2: データ取得（python $PROJECT_DIR/scripts/fetch_data.py --date \$(date +%Y%m%d)）
        - 終了コード1（全銘柄失敗）の場合 → Telegramに通知して終了
Step 3: シグナル分析（skills/signal-analysis.md に従い delegate_task で並列実行）
        - watchlist.csvの銘柄を最大3並列でスキャン
        - 結果を data/signals/\$(date +%Y%m%d).jsonl に保存
Step 4: リスク計算（python $PROJECT_DIR/scripts/risk_calc.py --date \$(date +%Y%m%d) --capital ${CAPITAL} --mode \$mode）
Step 5: 配信（skills/daily-report.md に従いTelegramへ送信）

すべてのログを logs/\$(date +%Y%m%d).log に記録すること。"

echo "  → 登録完了"
echo ""

# ── ジョブ2: 月次レビュー（毎月末 20:00）─────────────────────────────────────
# 28〜31日に毎日起動し、Hermes自身が「本日が最終営業日か」を判断する。
# 最終営業日でなければ [SILENT] で終了（配信なし）。

echo "[2/2] 月次レビュージョブを登録..."

hermes cron create "0 20 28-31 * *" \
  --name "stock-analysis-monthly-review" \
  --skill signal-analysis \
  --deliver "telegram" \
  --cwd "$PROJECT_DIR" \
  -- \
"月次レビューを実行せよ。

プロジェクトディレクトリ: $PROJECT_DIR

まず今日の日付を確認し、今月の最終営業日（土日・祝日を除く最後の平日）でなければ
[SILENT] とだけ出力して終了すること。

最終営業日であれば以下を実行する:

1. data/performance_log.csv を読み込んで集計する
   - 勝率（take_profit / 全クローズ済みトレード）
   - 平均損益率
   - 総トレード数・確定シグナル数・候補シグナル数の内訳

2. 判定:
   - 勝率 50% 未満 または 平均損益率がマイナスの場合:
     a. .hermes/skills/signal-analysis.md の末尾に改善メモを追記する
        （現在の精度・課題・改善候補を箇条書きで）
     b. AGENTS.mdのシグナル確定条件の見直し案をTelegramに送信する

3. 集計結果を data/monthly_report/\$(date +%Y%m).md に保存する
   フォーマット: 月次サマリー（勝率・損益率・銘柄別成績・改善メモ）

すべてのログを logs/\$(date +%Y%m%d).log に記録すること。"

echo "  → 登録完了"
echo ""

# ── 確認 ──────────────────────────────────────────────────────────────────────

echo "========================================"
echo "  登録済みジョブ一覧:"
echo "========================================"
hermes cron list

echo ""
echo "セットアップ完了。"
echo ""
echo "次のステップ:"
echo "  1. gatewayが起動していることを確認: hermes gateway status"
echo "  2. 動作テスト（即時実行）:          hermes cron run stock-analysis-daily"
echo "  3. ログ確認:                        tail -f logs/\$(date +%Y%m%d).log"
