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
