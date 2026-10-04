"""저장 계층: DB 제약조건(마지막 방어선), Redis 유실 뒤 복구, 마이그레이션"""
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from server.models import News
from server.runtime import get_store
from simulator import engine as E

from .helpers import new_game, order, signup, tick


@pytest.mark.parametrize("sql", [
    "UPDATE games SET cash = -1",                                           # 예수금 음수
    "INSERT INTO games (user_id, seed, status, current_day, max_day, start_cash, cash, fee_rate, tax_rate) "
    "SELECT user_id, 1, 'ACTIVE', 1, 30, 1, 1, 0, 0 FROM games",            # 진행 중인 게임 2개
    "INSERT INTO orders (game_id, side, qty, status, game_day, game_tick) "
    "SELECT id, 'BUY', 1, 'REJECTED', 1, 0 FROM games",                     # 사유 없는 거부
    "INSERT INTO holdings (game_id, stock_code, qty, avg_price) SELECT id, 'HBS', 0, 1 FROM games",  # 0주 보유
    "UPDATE stock_prices SET high = low - 1",                               # 고가 < 저가
])
def test_db_constraints_reject_bad_data(client, db, sql):
    signup(client)
    new_game(client)
    with pytest.raises(IntegrityError):
        db.execute(text(sql))
        db.flush()
    db.rollback()


def test_recovers_after_redis_loss(client, db):
    """진행 중에 Redis 상태가 사라져도 같은 시각·가격·보유로 이어서 진행되고, 뉴스가 중복 저장되지 않음"""
    signup(client)
    gid = new_game(client)
    tick(client, gid, 20)
    assert order(client, gid, "HGF", "BUY", 10).status_code == 201
    before = tick(client, gid, 15)
    full = client.get(f"/api/v1/games/{gid}/state", params={"full": True}).json()

    get_store().delete(gid)                                     # Redis 유실

    after = client.get(f"/api/v1/games/{gid}/state", params={"full": True}).json()
    assert after["tick"] == before["tick"] == 35
    assert after["intra"] == full["intra"]                      # 장중 가격까지 똑같이 재현
    assert after["holdings"] == full["holdings"] and after["cash"] == full["cash"]
    assert [n["id"] for n in after["news"]] == [n["id"] for n in full["news"]]
    n_news = db.scalar(select(func.count()).select_from(News).where(News.game_id == gid))
    tick(client, gid, E.TICKS)                                  # 이어서 장 마감까지 (중복 저장이면 UNIQUE 위반)
    assert db.scalar(select(func.count()).select_from(News).where(News.game_id == gid)) >= n_news


def test_recovers_finished_day(client):
    signup(client)
    gid = new_game(client)
    closed = tick(client, gid, E.TICKS)
    get_store().delete(gid)
    again = client.get(f"/api/v1/games/{gid}/state").json()
    assert again["phase"] == "closed" and again["total"] == closed["total"]
    assert client.post(f"/api/v1/games/{gid}/next-day").json()["day"] == 2


def test_redis_ahead_of_db_is_corrected(client, db):
    """프로세스가 Redis 저장과 DB 커밋 사이에 죽은 상황: 돈은 DB 값으로 맞춰짐"""
    signup(client)
    gid = new_game(client)
    tick(client, gid)
    st = get_store().get(gid)
    st["cash"] = 999_999_999                                    # DB에 없는 돈
    st["holdings"] = {"HBS": {"qty": 1000, "avg": 1.0}}
    get_store().put(gid, st)
    pf = client.get(f"/api/v1/games/{gid}/portfolio").json()
    assert pf["cash"] == 10_000_000 and pf["holdings"] == []
    assert order(client, gid, "HBS", "SELL", 1).json()["reject_code"] == "INSUFFICIENT_QTY"


def test_migration_matches_models(db):
    """Alembic 마이그레이션으로 만든 DB가 모델 정의와 같은지 (모델만 고치고 마이그레이션을 빠뜨리면 실패)"""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from server.db import Base
    diff = compare_metadata(MigrationContext.configure(db.connection()), Base.metadata)
    assert diff == []
