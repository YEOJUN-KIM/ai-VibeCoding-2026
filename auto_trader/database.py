"""PostgreSQL 연결 및 버전 1 스키마 초기화. 연결 실패 시 메모리로 대체하지 않는다."""
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .settings import settings


def connect():
    return psycopg.connect(
        host=settings.postgres_host, port=settings.postgres_port,
        dbname=settings.postgres_db, user=settings.postgres_user,
        password=settings.postgres_password, connect_timeout=5, row_factory=dict_row,
    )


def initialize():
    with connect() as conn:
        conn.execute(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
