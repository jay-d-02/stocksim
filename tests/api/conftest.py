"""API·DB 통합 테스트 준비: 실제 PostgreSQL(테스트 전용 DB)과 Redis(15번 DB)를 쓴다

docker compose --profile test run --rm tests
DATABASE_URL이 없으면 (예: 엔진 테스트만 도커 한 줄로 돌릴 때) 이 폴더는 건너뛴다.
"""
import os

import pytest

if not os.environ.get("DATABASE_URL", "").endswith("_test"):
    pytest.skip("통합 테스트는 테스트 전용 DB(DATABASE_URL=..._test)가 있어야 실행됩니다", allow_module_level=True)

from alembic import command                                   # noqa: E402
from alembic.config import Config                              # noqa: E402
from fastapi.testclient import TestClient                      # noqa: E402
from sqlalchemy import text                                    # noqa: E402

from scripts.ensure_db import ensure                           # noqa: E402
from server.db import SessionLocal, engine                     # noqa: E402
from server.runtime import get_store                           # noqa: E402

TABLES = ["news_stocks", "news", "events", "trades", "orders", "holdings", "stock_prices", "portfolio_history",
          "market_daily", "game_snapshots", "games", "users", "real_news"]


@pytest.fixture(scope="session", autouse=True)
def database():
    ensure(os.environ["DATABASE_URL"])
    with engine.begin() as c:                                  # 매번 빈 DB에서 마이그레이션부터 검증
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    command.upgrade(Config("alembic.ini"), "head")
    yield


@pytest.fixture(autouse=True)
def clean():
    with engine.begin() as c:
        c.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
    get_store().r.flushdb()
    yield


@pytest.fixture
def client():
    from server.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s
