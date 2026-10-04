"""게임 유스케이스: 엔진(simulator) + 진행 상태(Redis) + 원장·기록(PostgreSQL)

동시성
- 상태를 바꾸는 요청(틱·다음 날·주문·새 게임)은 모두 `SELECT … FROM games FOR UPDATE`로
  게임 행을 먼저 잠근다. 같은 게임의 요청은 한 줄로 서서 처리된다 (워커가 여러 개여도).
- Redis 저장은 잠금을 쥔 채로, DB 커밋 **직전에** 한다. 커밋이 실패하면 Redis 값을 지워
  다음 요청이 DB에서 복구하게 한다. 그래서 Redis가 커밋된 DB보다 앞서거나 뒤처진 채로 남지 않는다.

돈의 원본은 PostgreSQL
- 진행 상태를 읽을 때마다 예수금·보유·체결 목록을 DB 값으로 맞춘다 (reconcile).
  프로세스가 Redis 저장과 커밋 사이에 죽어도 돈은 틀어지지 않는다.

복구
- 장이 열릴 때마다 엔진 상태를 game_snapshots에 저장한다.
- Redis에서 상태가 사라지면 그 지점에서 시작해, DB에 기록된 마지막 틱까지 시장을 다시 돌린다.
  시장은 (시드, 날, 틱)으로만 정해지고 매매의 영향을 받지 않으므로 같은 뉴스·가격이 그대로 재현된다.
"""
import copy
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from simulator import analysis as A
from simulator import engine as E
from simulator import ledger as L

from ..models import (Event, Game, GameSnapshot, Holding, MarketDaily, News, NewsStock, Order,
                      PortfolioHistory, Stock, StockPrice, Trade, User)

MACRO_KEYS = {e["key"] for e in E.MACRO_EVENTS}
CORP_KEYS = {e["key"] for e in E.CORP_EVENTS}
EVENT_TYPE = {"rate_decision": "rate", "earnings": "earnings", "trial_result": "trial",
              "rumor": "rumor", "rumor_result": "rumor",
              "rumor_clue": "rumor", "earn_clue": "earnings", "trial_clue": "trial"}    # 발표 전 단서


class NotFound(Exception):
    pass


class Conflict(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _tick_of(time_str):
    """'10:35' → 틱 번호. 아침 브리핑('08:30')은 0"""
    h, m = map(int, time_str.split(":"))
    return max(0, (h * 60 + m - 9 * 60) // E.TICK_MIN)


def sync_stocks(db):
    """종목 마스터를 catalog.py 기준으로 맞춤 (앱 시작 때, 여러 워커가 동시에 해도 안전)"""
    rows = [dict(code=c, name=s["name"], sector=s["sector"], base_price=s["base"], beta=s["beta"],
                 volatility=s["vol"], description=s["desc"], hq_name=E.HQ[c][0]) for c, s in E.STOCK.items()]
    stmt = pg_insert(Stock).values(rows)
    db.execute(stmt.on_conflict_do_update(index_elements=[Stock.code],
                                          set_={k: stmt.excluded[k] for k in rows[0] if k != "code"}))
    db.commit()


class GameService:
    def __init__(self, db, store, settings):
        self.db, self.store, self.settings = db, store, settings

    # ------------------------------------------------------------ 조회 도우미
    def _get(self, game_id, user, lock=False):
        q = select(Game).where(Game.id == game_id)
        g = self.db.scalar(q.with_for_update() if lock else q)
        if not g or g.user_id != user.id:          # 남의 게임은 존재 자체를 숨김
            raise NotFound()
        return g

    def active_game(self, user):
        return self.db.scalar(select(Game).where(Game.user_id == user.id, Game.status == "ACTIVE"))

    def latest_game(self, user):
        return self.db.scalar(select(Game).where(Game.user_id == user.id).order_by(Game.id.desc()).limit(1))

    def fees(self, g):
        return L.FeeSchedule(g.fee_rate.normalize(), g.tax_rate.normalize())

    # ------------------------------------------------------------ 진행 상태: 읽기·맞추기·복구
    def state(self, g, locked=True):
        st = self.store.get(g.id)
        if st is None:
            if not locked:
                # 복구는 잠금 안에서만: 잠금 없이 되살려 쓰면, 동시에 잠금을 쥔 요청이 저장한
                # 더 새로운 상태를 덮어쓸 수 있다
                self.db.execute(select(Game.id).where(Game.id == g.id).with_for_update())
                st = self.store.get(g.id)
            if st is None:
                st = self._recover(g)
                self.store.put(g.id, st)
            if not locked:
                self.db.commit()
        self._reconcile(g, st)
        return st

    def _reconcile(self, g, st):
        """돈은 DB가 원본: 예수금·보유·체결 목록을 DB 값으로 덮어씀"""
        st["cash"] = g.cash
        st["holdings"] = {h.stock_code: {"qty": h.qty, "avg": float(h.avg_price)}
                          for h in self.db.scalars(select(Holding).where(Holding.game_id == g.id))}
        n = self.db.scalar(select(func.count()).select_from(Trade).where(Trade.game_id == g.id))
        if n != len(st["trades"]):
            st["trades"] = [{"day": t.game_day, "tick": t.game_tick, "time": E.clock(t.game_tick),
                             "code": t.stock_code, "side": t.side, "qty": t.qty, "price": t.price,
                             "fee": t.fee, "tax": t.tax, "profit": t.realized_pnl}
                            for t in self.db.scalars(select(Trade).where(Trade.game_id == g.id).order_by(Trade.id))]

    def _recover(self, g):
        snap = self.db.get(GameSnapshot, g.id)
        if snap is None:
            raise Conflict("NO_SNAPSHOT", "게임 상태를 복구할 수 없습니다.")
        st = copy.deepcopy(snap.state)
        self._reconcile(g, st)
        # 장 시작 지점부터 DB에 기록된 현재 틱까지 다시 돌림 (시장은 매매와 무관하게 결정적)
        while st["phase"] == "open" and st["tick"] < g.current_tick:
            E.step_tick(st)
        st["db_seq"] = self.db.scalar(select(func.max(News.seq)).where(News.game_id == g.id)) or 0
        return st

    def _save(self, g, st):
        """잠금을 쥔 채로 Redis 저장 → 커밋. 커밋이 실패하면 Redis 값을 지워 DB에서 다시 읽게 함"""
        self.store.put(g.id, st)
        try:
            self.db.commit()
        except Exception:
            self.store.delete(g.id)
            raise

    # ------------------------------------------------------------ 기록: 엔진 → 테이블
    def _persist_news(self, g, st):
        done = st.get("db_seq", 0)
        for n in st["news"]:
            if n["id"] <= done:
                continue
            meta = st.get("nmeta", {}).get(str(n["id"]))
            ev = None
            if meta:
                key = meta.get("key")
                etype = "preview" if n["kind"] == "preview" else EVENT_TYPE.get(key) or (
                    "macro" if key in MACRO_KEYS else "corp" if key in CORP_KEYS else "other")
                ev = Event(game_id=g.id, day=n["day"], game_tick=meta["t"], type=etype, event_key=key,
                           impacts=meta["imp"], prices_at=meta["px"])
                self.db.add(ev)
                self.db.flush()
            self.db.add(News(game_id=g.id, event_id=ev.id if ev else None, seq=n["id"], day=n["day"],
                             game_tick=meta["t"] if meta else _tick_of(n["time"]), time=n["time"],
                             kind=n["kind"], tag=n["tag"], factor=n["factor"], title=n["title"],
                             body=n["body"], why=n["why"], place=n.get("place"), lat=n.get("lat"),
                             lon=n.get("lon"), domestic=bool(n.get("kr")),
                             stocks=[NewsStock(stock_code=c) for c in n["codes"]]))
            done = n["id"]
        st["db_seq"] = done

    def _save_snapshot(self, g, st):
        stmt = pg_insert(GameSnapshot).values(game_id=g.id, day=st["day"], state=st)
        self.db.execute(stmt.on_conflict_do_update(index_elements=[GameSnapshot.game_id],
                                                   set_={"day": stmt.excluded.day, "state": stmt.excluded.state,
                                                         "saved_at": func.now()}))

    def _persist_close(self, g, st):
        day = st["day"]
        self.db.execute(pg_insert(StockPrice), [
            dict(game_id=g.id, stock_code=c, day=day, open=st["intra"][c][0], high=st["hl"][c][-1][0],
                 low=st["hl"][c][-1][1], close=st["prices"][c][-1]) for c in E.STOCK])
        eq = st["equity"][-1]
        self.db.add(PortfolioHistory(game_id=g.id, day=day, total=eq["total"], cash=eq["cash"],
                                     stock_value=eq["total"] - eq["cash"], allocation=eq["alloc"]))
        self.db.add(MarketDaily(game_id=g.id, day=day, index_value=round(eq["index"], 2),
                                base_rate=st["ind"]["rate"], fx=st["ind"]["fx"], oil=st["ind"]["oil"]))
        if st["finished"]:
            g.status, g.finished_at = "FINISHED", func.now()
            g.final_total = eq["total"]
            g.final_return_pct = round((eq["total"] / g.start_cash - 1) * 100, 4)
            g.profile = A.summary(A.analyze(st))

    # ------------------------------------------------------------ 유스케이스
    def create(self, user, days=E.DEFAULT_DAYS):
        """새 게임 (days: 거래일 수, E.GAME_DAYS 중 하나). 진행 중이던 게임은 포기 처리"""
        if days not in E.GAME_DAYS:
            raise Conflict("INVALID_DAYS", f"게임 길이는 {', '.join(map(str, E.GAME_DAYS))}거래일 중에서 고르세요.")
        old = list(self.db.scalars(select(Game).where(Game.user_id == user.id, Game.status == "ACTIVE")
                                   .with_for_update()))
        for o in old:
            o.status, o.finished_at = "ABANDONED", func.now()
        self.db.flush()
        seed = random.randrange(1 << 31)
        st = E.new_game(self.settings.start_cash, seed, days)
        g = Game(user_id=user.id, seed=seed, status="ACTIVE", current_day=st["day"], current_tick=0, max_day=days,
                 start_cash=st["start_cash"], cash=st["cash"], fee_rate=self.settings.fee_rate,
                 tax_rate=self.settings.tax_rate)
        self.db.add(g)
        self.db.flush()
        self.db.execute(pg_insert(StockPrice), [
            dict(game_id=g.id, stock_code=c, day=i - (E.PRE_DAYS - 1), open=p, high=p, low=p, close=p)
            for c in E.STOCK for i, p in enumerate(st["prices"][c])])
        self._persist_news(g, st)
        self._save_snapshot(g, st)
        self._save(g, st)
        for o in old:
            self.store.delete(o.id)
        return g, st

    def tick(self, game_id, user):
        g = self._get(game_id, user, lock=True)
        st = self.state(g)
        was_open = st["phase"] == "open"
        E.step_tick(st)
        g.current_tick = st["tick"]
        self._persist_news(g, st)
        if was_open and st["phase"] == "closed":
            self._persist_close(g, st)
        self._save(g, st)
        return g, st

    def next_day(self, game_id, user):
        g = self._get(game_id, user, lock=True)
        st = self.state(g)
        if st["finished"]:
            raise Conflict("GAME_FINISHED", "끝난 게임입니다. 새 게임을 시작하세요.")
        if st["phase"] == "open":
            raise Conflict("MARKET_OPEN", "아직 장이 열려 있습니다.")
        E.begin_day(st)
        g.current_day, g.current_tick = st["day"], st["tick"]
        self._persist_news(g, st)
        self._save_snapshot(g, st)
        self._save(g, st)
        return g, st

    def place_order(self, game_id, user, code, side, qty, client_order_id=None):
        """(주문 행, 새로 처리했는지). 거부돼도 주문은 사유와 함께 기록된다"""
        g = self._get(game_id, user, lock=True)
        if client_order_id:
            dup = self.db.scalar(select(Order).where(Order.game_id == g.id, Order.client_order_id == client_order_id))
            if dup:
                return dup, False
        st = self.state(g)
        order = Order(game_id=g.id, stock_code=code if code in E.STOCK else None, side=side, qty=qty,
                      client_order_id=client_order_id, game_day=st["day"], game_tick=st["tick"])
        try:
            fill = E.execute_order(st, code, side, qty, self.fees(g))
        except L.OrderRejected as e:
            order.status, order.reject_code, order.reject_message = "REJECTED", e.code, e.message
            self.db.add(order)
            self.db.commit()                        # 진행 상태는 그대로이므로 Redis는 건드리지 않음
            return order, True
        order.status = "FILLED"
        g.cash = st["cash"]
        if fill.position:
            stmt = pg_insert(Holding).values(game_id=g.id, stock_code=code, qty=fill.position.qty,
                                             avg_price=round(fill.position.avg, 4))
            self.db.execute(stmt.on_conflict_do_update(
                index_elements=[Holding.game_id, Holding.stock_code],
                set_={"qty": stmt.excluded.qty, "avg_price": stmt.excluded.avg_price, "updated_at": func.now()}))
        else:
            self.db.execute(delete(Holding).where(Holding.game_id == g.id, Holding.stock_code == code))
        self.db.add(order)
        self.db.flush()
        self.db.add(Trade(order_id=order.id, game_id=g.id, stock_code=code, side=side, qty=qty,
                          price=fill.price, amount=fill.amount, fee=fill.fee, tax=fill.tax,
                          realized_pnl=fill.profit, cash_after=g.cash, game_day=st["day"], game_tick=st["tick"]))
        self._save(g, st)
        return order, True

    # ------------------------------------------------------------ 조회
    def view(self, game_id, user):
        g = self._get(game_id, user)
        return g, self.state(g, locked=False)

    def portfolio(self, game_id, user):
        g, st = self.view(game_id, user)
        rows, stock_value = [], 0
        for c, h in sorted(st["holdings"].items()):
            cur = E.price(st, c)
            value, cost = cur * h["qty"], round(h["avg"] * h["qty"])
            stock_value += value
            rows.append({"stock_code": c, "name": E.STOCK[c]["name"], "qty": h["qty"], "avg_price": round(h["avg"], 2),
                         "price": cur, "value": value, "unrealized_pnl": value - cost,
                         "unrealized_pct": round((value - cost) / cost * 100, 2) if cost else 0.0})
        total = g.cash + stock_value
        realized = self.db.scalar(select(func.coalesce(func.sum(Trade.realized_pnl), 0)).where(Trade.game_id == g.id))
        return {"game_id": g.id, "day": st["day"], "cash": g.cash, "stock_value": stock_value, "total": total,
                "start_cash": g.start_cash, "return_pct": round((total / g.start_cash - 1) * 100, 4),
                "realized_pnl": realized, "holdings": rows}

    def list_trades(self, game_id, user):
        g = self._get(game_id, user)
        return list(self.db.scalars(select(Trade).where(Trade.game_id == g.id).order_by(Trade.id)))

    def list_orders(self, game_id, user):
        g = self._get(game_id, user)
        return list(self.db.scalars(select(Order).where(Order.game_id == g.id).order_by(Order.id)))

    def list_news(self, game_id, user, since=0, limit=200):
        g = self._get(game_id, user)
        return list(self.db.scalars(select(News).where(News.game_id == g.id, News.seq > since)
                                    .order_by(News.seq).limit(limit)))

    def daily_prices(self, game_id, user, code):
        g = self._get(game_id, user)
        return list(self.db.scalars(select(StockPrice).where(StockPrice.game_id == g.id, StockPrice.stock_code == code)
                                    .order_by(StockPrice.day)))

    def history(self, game_id, user):
        g = self._get(game_id, user)
        return list(self.db.scalars(select(PortfolioHistory).where(PortfolioHistory.game_id == g.id)
                                    .order_by(PortfolioHistory.day)))

    def past_games(self, user, limit=10):
        n = select(func.count()).select_from(Trade).where(Trade.game_id == Game.id).scalar_subquery()
        return [{"id": g.id, "total": g.final_total, "pct": float(g.final_return_pct), "trades": cnt,
                 "profile": g.profile, "finished_at": g.finished_at}
                for g, cnt in self.db.execute(select(Game, n).where(Game.user_id == user.id, Game.status == "FINISHED")
                                              .order_by(Game.id.desc()).limit(limit))]

    def replay(self, game_id, user):
        """끝난 게임 복기: 자산 vs 지수, 거래한 종목의 일봉과 매매 시점, 공개된 뉴스 (전부 DB 기록)"""
        g = self._get(game_id, user)
        if g.status != "FINISHED":
            raise NotFound()
        rows = lambda q: list(self.db.scalars(q))
        trades = rows(select(Trade).where(Trade.game_id == g.id).order_by(Trade.id))
        codes = sorted({t.stock_code for t in trades}, key=lambda c: -sum(t.stock_code == c for t in trades))
        idx = {m.day: float(m.index_value) for m in rows(select(MarketDaily).where(MarketDaily.game_id == g.id))}
        curve = [{"day": 0, "me": 0.0, "mkt": 0.0}] + [
            {"day": p.day, "me": (p.total / g.start_cash - 1) * 100, "mkt": idx.get(p.day, 1000) / 10 - 100}
            for p in rows(select(PortfolioHistory).where(PortfolioHistory.game_id == g.id).order_by(PortfolioHistory.day))]
        candles = {}
        for p in rows(select(StockPrice).where(StockPrice.game_id == g.id, StockPrice.stock_code.in_(codes),
                                               StockPrice.day >= 0).order_by(StockPrice.day)):
            candles.setdefault(p.stock_code, []).append({"day": p.day, "close": p.close, "high": p.high, "low": p.low})
        news = [{"day": n.day, "time": n.time, "kind": n.kind, "tag": n.tag, "title": n.title, "why": n.why,
                 "codes": [s.stock_code for s in n.stocks]}
                for n in rows(select(News).where(News.game_id == g.id, News.kind != "info").order_by(News.seq))]
        return {"game": g, "curve": curve, "codes": codes, "candles": candles, "news": news,
                "trades": [{"day": t.game_day, "time": E.clock(t.game_tick), "code": t.stock_code,
                            "name": E.STOCK[t.stock_code]["name"], "side": t.side, "qty": t.qty, "price": t.price,
                            "pnl": t.realized_pnl} for t in trades]}


def week_ago():
    return datetime.now(timezone.utc) - timedelta(days=7)


def leaderboard(db, limit=10, experience=None, since=None, days=E.DEFAULT_DAYS):
    """끝난 게임 최종 자산 순위. 게임 길이(days)가 같은 게임끼리만 겨룸.
    experience: 회원의 (현재) 투자 경험으로 거름, since: 이 시각 이후에 끝난 게임만"""
    n = select(func.count()).select_from(Trade).where(Trade.game_id == Game.id).scalar_subquery()
    q = (select(Game, User.nickname, User.experience, n).join(User, User.id == Game.user_id)
         .where(Game.status == "FINISHED", Game.max_day == days,
                User.save_status == "Y"))       # 탈퇴 회원의 기록은 순위에서 뺌
    if experience:
        q = q.where(User.experience == experience)
    if since:
        q = q.where(Game.finished_at >= since)
    q = q.order_by(Game.final_total.desc()).limit(limit)
    return [{"game_id": g.id, "user_id": g.user_id, "nickname": name, "experience": exp, "total": g.final_total,
             "pct": float(g.final_return_pct), "trades": cnt, "persona": (g.profile or {}).get("persona"),
             "finished_at": g.finished_at} for g, name, exp, cnt in db.execute(q)]
