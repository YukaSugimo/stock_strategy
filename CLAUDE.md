# 株分析ツール

## プロジェクト概要

日本株短期トレードのシグナル生成システム。
yfinanceでOHLCVを取得し、RSI/MACD/BBの3指標でシグナルを判定する。
Discord通知機能は未実装（後回し）。

## 環境

- OS: Windows 11 / PowerShell
- Python: 3.12
- プロジェクトルート: `C:\Users\yuucy\OneDrive\デスクトップ\起業\claude_code\株分析ツール`

## ファイル構成

```
株分析ツール/
├── CLAUDE.md
├── AGENTS.md
├── .env                        # DB接続情報（Gitにコミットしない）
├── .claude/
│   └── settings.json
├── scripts/
│   ├── fetch_data.py           # yfinanceでOHLCV取得 + DBへの差分保存
│   ├── indicators.py           # RSI/MACD/BB計算
│   ├── risk_calc.py            # Kelly基準でポジションサイズ計算
│   ├── backtest.py             # 大量銘柄対応バックテスト（DB書き込み対応）
│   ├── optimize.py             # グリッドサーチエンジン
│   ├── engine.py               # backtest共通ロジック（fetch/simulate/summary）
│   ├── db.py                   # DB接続モジュール（psycopg2ラッパー）
│   └── strategies/
│       ├── base.py             # 戦略基底クラス（変更禁止）
│       └── s01_rsi_macd_bb.py  # RSI/MACD/BB 3指標一致戦略
├── params/
│   ├── s01_rsi_macd_bb.yaml        # 固定パラメータ
│   └── s01_rsi_macd_bb_grid.yaml   # グリッドサーチ用
├── data/
│   ├── watchlist.csv           # 監視銘柄（ticker列、例: 7203.T）
│   ├── raw/                    # OHLCV生データ（data/raw/YYYYMMDD/{ticker}.csv）
│   ├── signals/                # シグナル出力（data/signals/YYYYMMDD.jsonl）
│   ├── backtest_results.csv    # トレード一覧
│   ├── backtest_summary.json   # バックテストサマリー
│   └── monthly_report/
├── doc/
│   ├── 基本設計/               # DB接続仕様書・戦略実装仕様書・運用マニュアル
│   └── 詳細設計/               # create_tables.sql
└── logs/                       # 実行ログ（logs/YYYYMMDD.log）
```

## スクリプトの実行方法

```powershell
# データ取得（--days 60以上を指定すること）
python scripts/fetch_data.py --tickers 7203.T 9984.T --days 60

# watchlist全銘柄を取得
python scripts/fetch_data.py --days 60

# 指標計算
python scripts/indicators.py --ticker 7203.T

# リスク計算（data/signals/YYYYMMDD.jsonlが必要）
python scripts/risk_calc.py --date 20260414 --capital 1000000

# バックテスト（--strategy は必須）
python scripts/backtest.py --strategy s01_rsi_macd_bb --days 365 --workers 4
python scripts/backtest.py --strategy s01_rsi_macd_bb --resume

# グリッドサーチ
python scripts/optimize.py --strategy s01_rsi_macd_bb --days 365 --workers 4
```

## DB接続

- DB名: stock_quant
- ポート: 1220
- ホスト: localhost（.envで変更可能）
- 接続モジュール: scripts/db.py

```
# .env（プロジェクトルートに配置）
DB_HOST=localhost
DB_PORT=1220
DB_NAME=stock_quant
DB_USER=postgres
DB_PASSWORD=your_password
```

DB未接続の場合、fetch_data.py / backtest.py は CSVファイルのみに保存して動作を継続する（ベストエフォート）。

PostgreSQL起動コマンド（未起動の場合）:
```powershell
Start-Service -Name "postgresql-x64-18"
```

## Windows固有ルール（必ず守ること）

### ファイル操作
- `open()` には必ず `encoding="utf-8"` を明示する
- BOM付きUTF-8を読む場合は `encoding="utf-8-sig"` を使う
- 絵文字・特殊文字（例: 🟢🟡⚪）はWindowsのcp932で壊れるため使わない
- ファイル修正は必ずPythonで行う。PowerShellの `Set-Content` は使わない

```python
# ファイル修正の正しい方法
content = open('file.py', encoding='utf-8').read()
content = content.replace('old', 'new')
open('file.py', 'w', encoding='utf-8').write(content)
```

### ディレクトリ
- スクリプト実行前に必要なディレクトリの存在を確認する
- 作成は `os.makedirs(path, exist_ok=True)` を使う

## よくあるエラーと対処

| エラー | 原因 | 対処 |
|--------|------|------|
| `UnicodeDecodeError: cp932` | `open()` にencoding未指定 | `encoding="utf-8"` を追加 |
| `SyntaxError: unterminated string literal` | Set-Contentで文字化け | Pythonでファイルを書き直す |
| `データ行数が不足` | `--days` が短い | `--days 60` 以上を指定 |
| `FileNotFoundError` | ディレクトリ未作成 | `os.makedirs` で事前に作成 |
| `could not connect to server` | PostgreSQL未起動 | `Start-Service -Name "postgresql-x64-18"` |
| `--strategy is required` | backtest.pyに--strategy未指定 | `--strategy s01_rsi_macd_bb` を追加 |

## 動作確認済みスクリプト

- fetch_data.py: 動作確認済み（DB差分取得対応済み）
- indicators.py: 動作確認済み（絵文字をASCIIに修正済み）
- risk_calc.py: 動作確認済み（文字化けを修正済み）
- backtest.py: 動作確認済み（engine.py呼び出し構造・DB書き込み対応済み）
- optimize.py: 動作確認済み（engine.py呼び出し構造）
- engine.py: 動作確認済み
