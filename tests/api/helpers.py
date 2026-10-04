"""통합 테스트 도우미: 가입·게임 생성·틱·주문을 API로"""


def signup(client, name="trader", password="secret123", **extra):
    r = client.post("/api/v1/auth/register",
                    json={"username": name, "password": password, "agree_privacy": True, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def new_game(client, days=None):
    r = client.post("/api/v1/games", json={"days": days} if days else None)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def tick(client, gid, n=1, since=0):
    for _ in range(n):
        r = client.post(f"/api/v1/games/{gid}/tick", params={"since": since})
        assert r.status_code == 200, r.text
    return r.json()


def order(client, gid, code, side, qty, coid=None):
    body = {"stock_code": code, "side": side, "qty": qty}
    if coid:
        body["client_order_id"] = coid
    return client.post(f"/api/v1/games/{gid}/orders", json=body)
