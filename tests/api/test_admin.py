"""관리자: permit = 'ADMIN'만 회원 정보를 볼 수 있고, 권한 변경·탈퇴 처리"""
from fastapi.testclient import TestClient
from sqlalchemy import update

from scripts.set_permit import main as set_permit
from server.models import User

from .helpers import new_game, signup


def make_admin(db, user_id):
    db.execute(update(User).where(User.id == user_id).values(permit="ADMIN"))
    db.commit()


def test_only_admin_can_see_users(client, db):
    me = signup(client, "alice")
    assert client.get("/api/v1/admin/users").status_code == 403
    assert client.get(f"/api/v1/admin/users/{me['id']}").status_code == 403
    r = client.get("/admin/users")                                   # 화면은 게임으로 돌려보냄
    assert r.status_code == 200 and "관리자만" in r.text and "회원 관리" not in r.text
    assert TestClient(client.app).get("/api/v1/admin/users").status_code == 401   # 로그인 안 함
    make_admin(db, me["id"])
    assert client.get("/api/v1/auth/me").json()["permit"] == "ADMIN"
    assert client.get("/api/v1/admin/users").json()["total"] == 1
    assert "회원 관리" in client.get("/game").text                    # 관리자에게만 메뉴


def test_list_filter_detail(client, db):
    admin = signup(client, "admin1")
    make_admin(db, admin["id"])
    bob = TestClient(client.app)
    b = signup(bob, "bob", email="bob@example.com")
    new_game(bob)
    signup(TestClient(client.app), "carol")
    page = client.get("/api/v1/admin/users", params={"q": "bob@"}).json()
    assert page["total"] == 1 and page["items"][0]["user"]["username"] == "bob" and page["items"][0]["games"] == 1
    assert client.get("/api/v1/admin/users", params={"permit": "ADMIN"}).json()["total"] == 1
    detail = client.get(f"/api/v1/admin/users/{b['id']}").json()
    assert detail["user"]["email"] == "bob@example.com" and detail["games"][0]["status"] == "ACTIVE"
    assert client.get("/api/v1/admin/users/9999").status_code == 404
    # 탈퇴 처리 → N 목록으로 이동, 개인정보 삭제
    r = client.post(f"/api/v1/admin/users/{b['id']}/withdraw")
    assert r.status_code == 200 and (r.json()["save_status"], r.json()["username"], r.json()["email"]) == ("N", None, None)
    assert r.json()["display_name"] == f"탈퇴한 회원 #{b['id']}"
    assert bob.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/admin/users").json()["total"] == 2          # 기본은 사용 중(Y)만
    assert client.get("/api/v1/admin/users", params={"status": "N"}).json()["total"] == 1
    assert client.post(f"/api/v1/admin/users/{b['id']}/withdraw").status_code == 409      # 이미 탈퇴


def test_permit_rules(client, db):
    admin = signup(client, "admin1")
    make_admin(db, admin["id"])
    b = signup(TestClient(client.app), "bob")
    r = client.patch(f"/api/v1/admin/users/{b['id']}/permit", json={"permit": "ADMIN"})
    assert r.status_code == 200 and r.json()["permit"] == "ADMIN"
    assert client.patch(f"/api/v1/admin/users/{b['id']}/permit", json={"permit": "GOD"}).status_code == 422
    r = client.patch(f"/api/v1/admin/users/{admin['id']}/permit", json={"permit": "USER"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SELF_DEMOTE"
    assert client.post(f"/api/v1/admin/users/{admin['id']}/withdraw").json()["detail"]["code"] == "SELF_WITHDRAW"
    client.patch(f"/api/v1/admin/users/{b['id']}/permit", json={"permit": "USER"})
    # 마지막 관리자는 스스로 탈퇴 불가
    r = client.request("DELETE", "/api/v1/auth/me", json={"password": "secret123"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "LAST_ADMIN"


def test_withdrawn_hidden_from_leaderboard(client, db):
    from simulator import engine as E
    from .helpers import tick
    me = signup(client, "alice")
    gid = new_game(client)
    for day in range(E.DEFAULT_DAYS):
        tick(client, gid, E.TICKS)
        if day < E.DEFAULT_DAYS - 1:
            client.post(f"/api/v1/games/{gid}/next-day")
    assert client.get("/api/v1/leaderboard").json()[0]["game_id"] == gid
    client.request("DELETE", "/api/v1/auth/me", json={"password": "secret123"})
    assert client.get("/api/v1/leaderboard").json() == []
    assert db.get(User, me["id"]).save_status == "N"


def test_admin_pages_and_script(client, db, capsys):
    signup(client, "admin1")
    assert set_permit(["admin1", "admin"]) == 0 and "ADMIN" in capsys.readouterr().out
    assert set_permit(["nobody", "ADMIN"]) == 1
    b = signup(TestClient(client.app), "bob", nickname="밥돌이")
    r = client.get("/admin/users", params={"q": "밥"})
    assert r.status_code == 200 and "밥돌이" in r.text
    assert "save_status" in client.get(f"/admin/users/{b['id']}").text
    r = client.post(f"/admin/users/{b['id']}/permit", data={"permit": "ADMIN"})
    assert r.status_code == 200 and "관리자" in r.text
    r = client.post(f"/admin/users/{b['id']}/withdraw", data={})
    assert "체크해 주세요" in r.text
    r = client.post(f"/admin/users/{b['id']}/withdraw", data={"confirm": "on"})
    assert "탈퇴 처리했어요" in r.text and f"탈퇴한 회원 #{b['id']}" in r.text
