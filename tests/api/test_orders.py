"""주문 API: 체결·거부 기록·중복 제출·원장 일치·동시 주문"""
import threading

from sqlalchemy import func, select

from server.config import settings
from server.db import SessionLocal
from server.models import Game, Holding, Order, Trade, User
from server.runtime import get_store
from server.services.games import GameService
from simulator import engine as E

from .helpers import new_game, order, signup, tick


def setup_game(client):
    signup(client)
    gid = new_game(client)
    tick(client, gid)
    return gid


def test_buy_then_sell(client, db):
    gid = setup_game(client)
    r = order(client, gid, "HGF", "BUY", 10)
    assert r.status_code == 201
    buy = r.json()
    assert buy["status"] == "FILLED" and buy["trade"]["qty"] == 10
    t = buy["trade"]
    assert t["amount"] == t["price"] * 10 and t["tax"] == 0 and t["realized_pnl"] is None
    assert t["cash_after"] == 10_000_000 - t["amount"] - t["fee"]
    pf = client.get(f"/api/v1/games/{gid}/portfolio").json()
    assert pf["cash"] == t["cash_after"] and pf["holdings"][0]["qty"] == 10
    assert pf["total"] == pf["cash"] + pf["stock_value"]

    tick(client, gid, 3)
    sell = order(client, gid, "HGF", "SELL", 10).json()
    s = sell["trade"]
    assert sell["status"] == "FILLED" and s["tax"] > 0 and s["realized_pnl"] is not None
    assert client.get(f"/api/v1/games/{gid}/portfolio").json()["holdings"] == []
    assert db.scalar(select(func.count()).select_from(Holding).where(Holding.game_id == gid)) == 0
    assert [x["side"] for x in client.get(f"/api/v1/games/{gid}/trades").json()] == ["BUY", "SELL"]


def test_rejected_order_is_recorded(client, db):
    gid = setup_game(client)
    r = order(client, gid, "MRE", "BUY", 1_000_000)
    assert r.status_code == 422
    o = r.json()
    assert o["status"] == "REJECTED" and o["reject_code"] == "INSUFFICIENT_CASH" and o["trade"] is None
    r = order(client, gid, "HBS", "SELL", 1)
    assert r.json()["reject_code"] == "INSUFFICIENT_QTY"
    codes = [x["reject_code"] for x in client.get(f"/api/v1/games/{gid}/orders").json()]
    assert codes == ["INSUFFICIENT_CASH", "INSUFFICIENT_QTY"]            # 거부도 감사 기록으로 남음
    assert client.get(f"/api/v1/games/{gid}/trades").json() == []
    assert db.get(Game, gid).cash == 10_000_000


def test_order_after_close_rejected(client):
    gid = setup_game(client)
    tick(client, gid, E.TICKS)
    assert order(client, gid, "HGF", "BUY", 1).json()["reject_code"] == "MARKET_CLOSED"


def test_invalid_order_payload(client):
    gid = setup_game(client)
    assert order(client, gid, "HGF", "HOLD", 1).status_code == 422
    assert order(client, gid, "HGF", "BUY", 0).status_code == 422
    assert order(client, gid, "NOPE", "BUY", 1).json()["reject_code"] == "UNKNOWN_STOCK"


def test_idempotent_order(client, db):
    """응답을 못 받아 같은 주문을 다시 보내도 한 번만 체결"""
    gid = setup_game(client)
    first = order(client, gid, "HGF", "BUY", 10, coid="abc-123")
    again = order(client, gid, "HGF", "BUY", 10, coid="abc-123")
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert db.scalar(select(func.count()).select_from(Trade).where(Trade.game_id == gid)) == 1
    assert client.get(f"/api/v1/games/{gid}/portfolio").json()["holdings"][0]["qty"] == 10


def test_ledger_matches_trades(client, db):
    """여러 번 사고판 뒤: DB 예수금 = 시작금 + Σ체결 현금 변화, 보유 = Σ매수 − Σ매도"""
    gid = setup_game(client)
    for i, (code, side, qty) in enumerate([("HGF", "BUY", 30), ("HBS", "BUY", 12), ("HGF", "SELL", 10),
                                           ("ONT", "BUY", 50), ("HBS", "SELL", 12), ("HGF", "BUY", 5)]):
        tick(client, gid, 2)
        assert order(client, gid, code, side, qty).status_code == 201
    trades = list(db.scalars(select(Trade).where(Trade.game_id == gid).order_by(Trade.id)))
    cash = 10_000_000
    for t in trades:
        cash += -(t.amount + t.fee) if t.side == "BUY" else t.amount - t.fee - t.tax
        assert t.cash_after == cash
    assert db.get(Game, gid).cash == cash
    held = {h.stock_code: h.qty for h in db.scalars(select(Holding).where(Holding.game_id == gid))}
    assert held == {"HGF": 25, "ONT": 50}


def test_concurrent_orders_cannot_overspend(client, db):
    """예수금의 60%씩 드는 주문 두 개가 동시에 와도 하나만 체결 (게임 행 잠금으로 직렬화)"""
    gid = setup_game(client)
    price = client.get(f"/api/v1/games/{gid}/stocks/MRE").json()["price"]
    qty = int(10_000_000 * 0.6 // price)
    user_id = db.scalar(select(User.id))
    results, barrier = [], threading.Barrier(2)

    def worker():
        with SessionLocal() as s:
            svc = GameService(s, get_store(), settings)
            barrier.wait()
            o, _ = svc.place_order(gid, s.get(User, user_id), "MRE", "BUY", qty)
            results.append(o.status)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["FILLED", "REJECTED"]
    db.expire_all()
    assert db.get(Game, gid).cash >= 0
    assert db.scalar(select(func.count()).select_from(Order).where(Order.game_id == gid, Order.status == "FILLED")) == 1
