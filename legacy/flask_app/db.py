"""PostgreSQL 연결 관리 (커넥션 풀 + 스키마 생성)"""
import os
import time
import logging

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger("db")

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://stocksim:stocksim@localhost:5432/stocksim")

# gunicorn 워커마다 풀이 하나씩 생김 (워커 2개 × 최대 10개 연결)
pool = ConnectionPool(
    DATABASE_URL,
    min_size=1,
    max_size=int(os.environ.get("DB_POOL_MAX", 10)),
    kwargs={"row_factory": dict_row, "options": "-c timezone=Asia/Seoul"},
    open=False,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username    TEXT UNIQUE NOT NULL,
    pw_hash     TEXT NOT NULL,
    cash        BIGINT NOT NULL CHECK (cash >= 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS holdings (
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code        TEXT NOT NULL,
    qty         BIGINT NOT NULL CHECK (qty > 0),
    avg_price   DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (user_id, code)
);

CREATE TABLE IF NOT EXISTS trades (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code        TEXT NOT NULL,
    side        TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    qty         BIGINT NOT NULL,
    price       BIGINT NOT NULL,
    fee         BIGINT NOT NULL,
    tax         BIGINT NOT NULL,
    profit      BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS trades_user_idx ON trades (user_id, id DESC);

-- 투자 게임: 사람마다 진행 중인 게임 하나 (상태 전체를 JSON으로 보관)
CREATE TABLE IF NOT EXISTS games (
    user_id     BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    state       JSONB NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 끝까지 마친 게임 기록 (명예의 전당)
CREATE TABLE IF NOT EXISTS game_results (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    total       BIGINT NOT NULL,
    pct         DOUBLE PRECISION NOT NULL,
    trades      INT NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE game_results ADD COLUMN IF NOT EXISTS profile JSONB;   -- 투자 성향 요약
"""


def init_db(retries=30):
    """DB가 뜰 때까지 기다렸다가 테이블을 만듦"""
    for i in range(retries):
        try:
            psycopg.connect(DATABASE_URL, connect_timeout=3).close()
            break
        except psycopg.OperationalError as e:
            log.warning("DB 연결 대기 중 (%d/%d): %s", i + 1, retries, e)
            time.sleep(1)
    else:
        raise RuntimeError("PostgreSQL에 연결하지 못했습니다. DATABASE_URL을 확인하세요.")
    pool.open(wait=True, timeout=30)

    with pool.connection() as con:
        # 여러 워커가 동시에 테이블을 만들지 않도록 잠금
        con.execute("SELECT pg_advisory_lock(815001)")
        try:
            con.execute(SCHEMA)
            con.commit()
        finally:
            con.execute("SELECT pg_advisory_unlock(815001)")
            con.commit()


UniqueViolation = psycopg.errors.UniqueViolation
from psycopg.types.json import Jsonb  # noqa: E402  (JSONB 컬럼에 dict 저장용)
