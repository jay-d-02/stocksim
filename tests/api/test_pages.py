"""HTML 화면·운영 엔드포인트가 뜨는지 (폼 로그인 → 각 화면 200)"""
def test_pages_render(client):
    assert client.get("/game", follow_redirects=False).status_code == 303      # 로그인 전엔 로그인 화면으로
    client.post("/register", data={"username": "webuser", "password": "secret123", "agree": "on"})
    for path in ["/game", "/game/stocks/HBS", "/game/news", "/game/style", "/learn", "/factors", "/ranking",
                 "/account", "/privacy"]:
        r = client.get(path)
        assert r.status_code == 200, (path, r.text[:300])
    game = client.get("/game").text
    assert "const INIT" in game and "tour.js" in game and '"auto": true' not in game
    assert "auto: true" in game                                                # 초보(기본값)는 사용법 자동 안내
    r = client.post("/game/stocks/HBS/order", data={"side": "BUY", "qty": "1"})
    assert r.status_code == 200 and "매수했습니다" in r.text                 # 리다이렉트 뒤 알림


def test_form_register_profile(client, db):
    from sqlalchemy import select
    from server.models import User
    assert "투자 경험" in client.get("/register").text
    r = client.post("/register", data={"username": "webuser", "password": "secret123", "agree": "on", "nickname": "x", "email": "me@a.com"})
    assert r.status_code == 422 and "닉네임은 2~20자" in r.text and 'value="me@a.com"' in r.text   # 입력값 유지
    r = client.post("/register", data={"username": "webuser", "password": "secret123", "agree": "on", "nickname": "주린이",
                                       "experience": "intermediate", "email": ""}, follow_redirects=False)
    assert r.status_code == 303
    u = db.scalar(select(User).where(User.username == "webuser"))
    assert (u.nickname, u.experience, u.email) == ("주린이", "intermediate", None)
    assert "주린이" in client.get("/game").text                              # 상단에 닉네임


def test_style_level_switch(client, db):
    from sqlalchemy import select
    from server.models import User
    client.post("/register", data={"username": "webuser", "password": "secret123", "agree": "on", "nickname": "주린이"})
    r = client.post("/game/style/level", data={"experience": "expert"})
    assert r.status_code == 200 and "자신 있어요" in r.text                       # 바꿨다는 알림
    assert db.scalar(select(User.experience).where(User.username == "webuser")) == "expert"
    gid = client.get("/api/v1/games/current").json()["id"]
    assert client.get(f"/api/v1/games/{gid}/analysis").json()["level"] == "expert"
    assert client.get(f"/api/v1/games/{gid}/analysis?level=beginner").json()["level"] == "beginner"


def test_form_login(client):
    client.post("/register", data={"username": "webuser", "password": "secret123", "agree": "on"})
    client.get("/logout")
    r = client.post("/login", data={"username": "webuser", "password": "nope"})
    assert r.status_code == 401
    r = client.post("/login", data={"username": "webuser", "password": "secret123", "agree": "on"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/game"


def test_ops_endpoints(client):
    h = client.get("/health").json()
    assert h == {"status": "ok", "postgres": "ok", "redis": "ok"}
    spec = client.get("/openapi.json").json()
    assert "/api/v1/games/{game_id}/orders" in spec["paths"]
    assert client.get("/docs").status_code == 200
    assert client.get("/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")
    assert client.get("/sw.js").status_code == 200
