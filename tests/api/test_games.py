"""게임 진행 API: 생성·틱·장 마감·다음 날·조회, 남의 게임 접근"""
from sqlalchemy import func, select

from server.models import Event, Game, MarketDaily, News, PortfolioHistory, StockPrice
from simulator import engine as E

from .helpers import new_game, signup, tick


def test_create_game(client, db):
    signup(client)
    gid = new_game(client)
    g = client.get(f"/api/v1/games/{gid}").json()
    assert g["status"] == "ACTIVE" and g["current_day"] == 1 and g["tick"] == 0 and g["clock"] == "09:00"
    assert g["max_day"] == E.DEFAULT_DAYS == 15                  # 길이를 안 고르면 빠른 판
    assert g["cash"] == g["total"] == 10_000_000
    # 게임 전 30일 차트가 일봉 테이블에
    n = db.scalar(select(func.count()).select_from(StockPrice).where(StockPrice.game_id == gid))
    assert n == len(E.STOCK) * E.PRE_DAYS


def test_choose_game_length(client, db):
    signup(client)
    gid = new_game(client, 30)
    assert db.get(Game, gid).max_day == 30
    assert client.get(f"/api/v1/games/{gid}/state").json()["max_day"] == 30
    assert client.post("/api/v1/games", json={"days": 7}).status_code == 422
    # 화면의 새 게임 버튼 (이상한 값이면 기본 길이)
    client.post("/game/new", data={"days": "30"})
    assert client.get("/api/v1/games/current").json()["max_day"] == 30
    client.post("/game/new", data={"days": "abc"})
    assert client.get("/api/v1/games/current").json()["max_day"] == 15


def test_new_game_abandons_old(client, db):
    signup(client)
    a, b = new_game(client), new_game(client)
    assert db.get(Game, a).status == "ABANDONED" and db.get(Game, b).status == "ACTIVE"
    assert client.get("/api/v1/games/current").json()["id"] == b


def test_state_hides_future(client):
    signup(client)
    gid = new_game(client)
    tick(client, gid, 5)
    st = client.get(f"/api/v1/games/{gid}/state", params={"full": True}).json()
    for secret in ("plan", "effects", "nmeta", "scheduled", "seed", "px"):
        assert secret not in st
    assert set(st["quotes"]) == set(E.STOCK)


def test_tick_publishes_news_to_db(client, db):
    signup(client)
    gid = new_game(client)
    snap = tick(client, gid, 40)
    assert snap["tick"] == 40 and snap["clock"] == E.clock(40)
    state_news = client.get(f"/api/v1/games/{gid}/state").json()["news"]
    api_news = client.get(f"/api/v1/games/{gid}/news").json()
    assert [n["seq"] for n in api_news] == [n["id"] for n in state_news]      # 공개된 뉴스 = DB 뉴스
    assert all(n["day"] == 1 for n in api_news)
    # 영향이 있는 뉴스는 사건(events)으로도 기록, 숨은 계획은 기록되지 않음
    with_event = db.scalar(select(func.count()).select_from(News).where(News.game_id == gid, News.event_id.is_not(None)))
    assert with_event == db.scalar(select(func.count()).select_from(Event).where(Event.game_id == gid))
    assert all(e.game_tick <= 40 for e in db.scalars(select(Event).where(Event.game_id == gid)))
    since = api_news[-1]["seq"] if api_news else 0
    assert client.get(f"/api/v1/games/{gid}/news", params={"since": since}).json() == []


def test_day_close_and_next_day(client, db):
    signup(client)
    gid = new_game(client)
    r = client.post(f"/api/v1/games/{gid}/next-day")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "MARKET_OPEN"
    snap = tick(client, gid, E.TICKS)
    assert snap["phase"] == "closed" and snap["report"]["day"] == 1
    rows = list(db.scalars(select(StockPrice).where(StockPrice.game_id == gid, StockPrice.day == 1)))
    assert len(rows) == len(E.STOCK)
    assert all(p.low <= min(p.open, p.close) and p.high >= max(p.open, p.close) for p in rows)
    ph = db.get(PortfolioHistory, (gid, 1))
    assert ph.total == ph.cash + ph.stock_value
    assert db.get(MarketDaily, (gid, 1)) is not None
    nxt = client.post(f"/api/v1/games/{gid}/next-day").json()
    assert nxt["day"] == 2 and nxt["phase"] == "open" and len(nxt["intra"]["HBS"]) == 1
    assert db.get(Game, gid).current_day == 2
    hist = client.get(f"/api/v1/games/{gid}/history").json()
    assert [h["day"] for h in hist] == [1]


def test_stock_detail(client):
    signup(client)
    gid = new_game(client)
    tick(client, gid, 3)
    s = client.get(f"/api/v1/games/{gid}/stocks/HGF").json()
    assert s["sector"] == "은행" and len(s["candles"]) == E.PRE_DAYS and len(s["intraday"]) == 4
    assert s["sensitivity"]["rate"] > 0                          # 은행은 금리 상승에 유리
    assert len(client.get(f"/api/v1/games/{gid}/stocks").json()) == len(E.STOCK)
    assert client.get(f"/api/v1/games/{gid}/stocks/NOPE").status_code == 404


def test_other_users_game_is_404(client):
    signup(client, "alice")
    gid = new_game(client)
    client.post("/api/v1/auth/logout")
    signup(client, "mallory")
    for method, path in [("get", ""), ("get", "/state"), ("post", "/tick"), ("get", "/portfolio"),
                         ("get", "/trades"), ("get", "/news")]:
        assert getattr(client, method)(f"/api/v1/games/{gid}{path}").status_code == 404
    r = client.post(f"/api/v1/games/{gid}/orders", json={"stock_code": "HBS", "side": "BUY", "qty": 1})
    assert r.status_code == 404


def test_full_game_finishes_and_ranks(client, db):
    signup(client)
    gid = new_game(client)
    client.post(f"/api/v1/games/{gid}/orders", json={"stock_code": "HGF", "side": "BUY", "qty": 10})
    days = E.DEFAULT_DAYS
    for day in range(days):
        snap = tick(client, gid, E.TICKS)
        if day < days - 1:
            client.post(f"/api/v1/games/{gid}/next-day")
    assert snap["finished"]
    g = db.get(Game, gid)
    assert g.status == "FINISHED" and g.final_total == snap["total"] and g.profile["persona"]
    board = client.get("/api/v1/leaderboard").json()
    assert board[0]["game_id"] == gid and board[0]["trades"] == 1 and "username" not in board[0]
    assert client.get("/api/v1/leaderboard", params={"days": 30}).json() == []     # 길이가 다른 순위표엔 없음
    assert "15거래일" in client.get("/ranking").text and "15거래일" not in client.get("/ranking?days=30").text
    assert client.post(f"/api/v1/games/{gid}/next-day").json()["detail"]["code"] == "GAME_FINISHED"
    assert tick(client, gid)["tick"] == E.TICKS                 # 끝난 뒤엔 시간이 안 흐름
    assert len(client.get(f"/api/v1/games/{gid}/history").json()) == days
    # 끝난 게임 복기 화면 + 필터된 명예의 전당
    r = client.get(f"/game/replay/{gid}")
    assert r.status_code == 200 and "게임 복기" in r.text and '"codes": ["HGF"]' in r.text
    assert f"/game/replay/{gid}" in client.get("/game/style").text           # 지난 게임에서 복기로 가는 링크
    assert client.get("/api/v1/leaderboard", params={"period": "week", "experience": "beginner"}).json()[0]["game_id"] == gid
    assert client.get("/api/v1/leaderboard", params={"experience": "expert"}).json() == []
    other = new_game(client)                                     # 진행 중인 게임은 복기 불가 → 게임 화면으로
    assert client.get(f"/game/replay/{other}", follow_redirects=False).status_code == 303
