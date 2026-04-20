# DB接続仕様書

## 接続情報

| 項目 | 値 |
|------|-----|
| ホスト | localhost |
| ポート | 1220 |
| DB名 | stock_quant |
| ユーザー | postgres |

## 使用ライブラリ

`psycopg2` を使う。`SQLAlchemy` は使わない。

```powershell
pip install psycopg2-binary python-dotenv
```

## 共通接続モジュール

`scripts/db.py` を作成して全スクリプトから共通で使う。
各スクリプトで個別に接続情報を書かない。

```python
# scripts/db.py
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     "localhost"),
    "port":     int(os.getenv("DB_PORT", "1220")),
    "dbname":   os.getenv("DB_NAME",     "stock_quant"),
    "user":     os.getenv("DB_USER",     "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "options":  "-c client_encoding=UTF8",
}

def get_conn():
    """
    新しい接続を返す。
    呼び出し元で明示的に conn.close() するか、
    try/finally または contextlib.closing() で閉じること。
    """
    return psycopg2.connect(**DB_CONFIG)

def get_cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)
```

## 呼び出し方

### 読み取り

```python
import contextlib
from db import get_conn, get_cursor

conn = get_conn()
try:
    with get_cursor(conn) as cur:
        cur.execute("SELECT * FROM tickers WHERE is_active = TRUE")
        rows = cur.fetchall()
finally:
    conn.close()
```

### 書き込み（トランザクション）

```python
conn = get_conn()
try:
    with get_cursor(conn) as cur:
        cur.execute(
            "INSERT INTO tickers (code, name) VALUES (%s, %s)",
            ("7203.T", "トヨタ自動車")
        )
    conn.commit()
except Exception as e:
    conn.rollback()
    raise
finally:
    conn.close()
```

> **注意**: `psycopg2` の接続オブジェクトをコンテキストマネージャ（`with conn:`）として使うと
> トランザクションの管理は行われますが接続自体は閉じられません。
> 必ず `finally` ブロックで `conn.close()` を呼ぶこと。

## ルール

- 接続情報をコードに直書きしない。必ず `db.py` 経由で使う
- `finally` で必ず `conn.close()` を呼ぶ
- プレースホルダーは `%s` を使う。f文字列でSQLを組み立てない（SQLインジェクション対策）
- 書き込み後は必ず `conn.commit()` を呼ぶ
- エラー時は `conn.rollback()` してから `raise` する

## 環境変数での接続情報管理

`.env` ファイルをプロジェクトルートに作成する。Gitにコミットしない。

```
# .env（Gitにコミットしない）
DB_HOST=localhost
DB_PORT=1220
DB_NAME=stock_quant
DB_USER=postgres
DB_PASSWORD=your_password
```

`.gitignore` に必ず追加すること：

```
.env
```

## よくあるエラーと対処

| エラー | 原因 | 対処 |
|--------|------|------|
| `could not connect to server` | PostgreSQLが起動していない | PowerShellで `Start-Service -Name "postgresql-x64-18"` |
| `password authentication failed` | パスワード間違い | `.env` の `DB_PASSWORD` を確認 |
| `database "stock_quant" does not exist` | DB未作成 | `psql -U postgres -p 1220 -c "CREATE DATABASE stock_quant"` |
| `UnicodeDecodeError` | エンコーディング問題 | `DB_CONFIG` の `options` に `"-c client_encoding=UTF8"` が設定されているか確認 |
| `could not translate host name` | ホスト名解決失敗 | `DB_HOST` を `localhost` から `127.0.0.1` に変更 |
