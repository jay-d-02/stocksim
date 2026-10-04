"""회원 관리: 로그인 잠금, 내 정보, 비밀번호 변경(다른 세션 종료), 탈퇴, 비밀번호 찾기"""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from server.models import Game, User

from .helpers import new_game, signup

LOGIN = "/api/v1/auth/login"


def login(client, name="alice", password="secret123"):
    return client.post(LOGIN, json={"username": name, "password": password})


@pytest.fixture
def mails(monkeypatch):
    """보낸 메일을 모음 (SMTP 대신)"""
    sent = []
    monkeypatch.setattr("server.mail.send", lambda to, subject, body: sent.append((to, subject, body)))
    return sent


def test_login_lockout_after_5_failures(client):
    signup(client, "alice")
    client.post("/api/v1/auth/logout")
    for _ in range(4):
        assert login(client, password="wrong-pass").status_code == 401
    r = login(client, password="wrong-pass")                       # 5번째: 여기서 잠김
    assert r.status_code == 429 and r.json()["detail"]["code"] == "TOO_MANY_ATTEMPTS"
    r = login(client)                                              # 잠긴 동안은 맞는 비밀번호도 거절
    assert r.status_code == 429 and 0 < int(r.headers["retry-after"]) <= 15 * 60
    assert login(client, "bob", "whatever1").status_code == 401     # 다른 아이디는 영향 없음


def test_success_resets_failure_count(client):
    signup(client, "alice")
    for _ in range(4):
        login(client, password="wrong-pass")
    assert login(client).status_code == 200
    for _ in range(4):                                             # 카운터가 0부터 다시
        assert login(client, password="wrong-pass").status_code == 401


def test_update_profile(client):
    signup(client, "alice", email="a@example.com")
    signup(TestClient(client.app), "bob", nickname="밥돌이")
    r = client.patch("/api/v1/auth/me", json={"nickname": "개미왕", "experience": "expert"})
    assert r.status_code == 200 and (r.json()["nickname"], r.json()["experience"], r.json()["email"]) == \
        ("개미왕", "expert", "a@example.com")                        # 안 보낸 이메일은 그대로
    assert client.patch("/api/v1/auth/me", json={"email": ""}).json()["email"] is None     # 빈 값 = 지우기
    r = client.patch("/api/v1/auth/me", json={"nickname": "밥돌이"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NICKNAME_TAKEN"
    assert client.get("/api/v1/auth/me").json()["nickname"] == "개미왕"


def test_change_password_ends_other_sessions(client):
    signup(client, "alice")
    other = TestClient(client.app)
    assert login(other).status_code == 200                           # 다른 기기에서도 로그인
    r = client.post("/api/v1/auth/password", json={"current_password": "wrong", "new_password": "newsecret1"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "WRONG_PASSWORD"
    assert client.post("/api/v1/auth/password", json={"current_password": "secret123", "new_password": "short"}).status_code == 422
    r = client.post("/api/v1/auth/password", json={"current_password": "secret123", "new_password": "newsecret1"})
    assert r.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200          # 바꾼 기기는 그대로
    assert other.get("/api/v1/auth/me").status_code == 401           # 다른 기기는 로그아웃
    assert login(other).status_code == 401 and login(other, password="newsecret1").status_code == 200


def test_delete_account_is_soft_and_anonymized(client, db):
    me = signup(client, "alice", email="alice@example.com", nickname="앨리스")
    gid = new_game(client)
    other = TestClient(client.app)
    assert login(other).status_code == 200
    assert client.request("DELETE", "/api/v1/auth/me", json={"password": "nope"}).status_code == 400
    assert client.request("DELETE", "/api/v1/auth/me", json={"password": "secret123"}).status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    assert other.get("/api/v1/auth/me").status_code == 401           # 다른 기기 세션도 끝남
    u = db.get(User, me["id"])                                       # 행은 남고 save_status = 'N'
    assert (u.save_status, u.username, u.nickname, u.email, u.permit) == ("N", None, None, None, "USER")
    assert u.withdrawn_at is not None and "secret123" not in u.password_hash
    assert db.get(Game, gid).status == "ABANDONED"                   # 게임 기록은 남고, 진행 중이던 판은 포기
    assert login(client).status_code == 401                          # 로그인은 save_status = 'Y'만
    signup(TestClient(client.app), "alice", nickname="앨리스")        # 아이디·닉네임은 다시 쓸 수 있음


def test_password_reset_flow(client, mails):
    signup(client, "alice", email="alice@example.com")
    client.post("/api/v1/auth/logout")
    r = client.post("/api/v1/auth/password-reset", json={"email": "nobody@example.com"})
    assert r.status_code == 202 and not mails                        # 없는 이메일도 같은 응답, 메일은 안 감
    assert client.post("/api/v1/auth/password-reset", json={"email": "ALICE@example.com"}).status_code == 202
    (to, _, body), = mails
    token = re.search(r"token=([\w.\-]+)", body).group(1)
    assert to == "alice@example.com" and "alice" in body             # 아이디도 알려 줌
    r = client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "brandnew1"})
    assert r.status_code == 200
    assert login(client, password="brandnew1").status_code == 200
    r = client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "another12"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "INVALID_TOKEN"   # 한 번만 쓸 수 있음
    assert client.post("/api/v1/auth/password-reset/confirm",
                       json={"token": "garbage", "new_password": "another12"}).status_code == 400


def test_password_reset_rate_limit(client, mails):
    for _ in range(3):
        assert client.post("/api/v1/auth/password-reset", json={"email": "x@example.com"}).status_code == 202
    assert client.post("/api/v1/auth/password-reset", json={"email": "x@example.com"}).status_code == 429


def test_reset_unlocks_login(client, mails):
    signup(client, "alice", email="alice@example.com")
    for _ in range(5):
        login(client, password="wrong-pass")
    assert login(client).status_code == 429
    client.post("/api/v1/auth/password-reset", json={"email": "alice@example.com"})
    token = re.search(r"token=([\w.\-]+)", mails[0][2]).group(1)
    client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "brandnew1"})
    assert login(client, password="brandnew1").status_code == 200


# ---------------------------------------------------------------- 화면
def test_account_pages(client, db, mails):
    for path in ["/privacy", "/forgot", "/reset?token=bad"]:
        assert client.get(path).status_code == 200, path
    assert "만료됐거나" in client.get("/reset?token=bad").text
    r = client.post("/register", data={"username": "webuser", "password": "secret123", "nickname": "주린이"})
    assert r.status_code == 422 and "동의해야" in r.text               # 동의 체크 없이
    client.post("/register", data={"username": "webuser", "password": "secret123", "nickname": "주린이",
                                   "email": "web@example.com", "agree": "on"})
    assert "주린이" in client.get("/account").text
    r = client.post("/account/profile", data={"nickname": "고수왕", "experience": "expert", "email": "web@example.com"})
    assert r.status_code == 200 and "저장했어요" in r.text
    r = client.post("/account/password", data={"current": "secret123", "password": "newsecret1", "password2": "different1"})
    assert r.status_code == 422 and "서로 다릅니다" in r.text
    r = client.post("/account/password", data={"current": "secret123", "password": "newsecret1", "password2": "newsecret1"})
    assert r.status_code == 200 and "비밀번호를 바꿨어요" in r.text
    # 로그인 화면 잠금 안내
    client.get("/logout")
    for _ in range(5):
        r = client.post("/login", data={"username": "webuser", "password": "nope"})
    assert r.status_code == 429 and "막혔어요" in r.text
    # 비밀번호 찾기 → 링크로 재설정
    assert "보냈어요" in client.post("/forgot", data={"email": "web@example.com"}).text
    token = re.search(r"token=([\w.\-]+)", mails[0][2]).group(1)
    r = client.post("/reset", data={"token": token, "password": "resetpass1", "password2": "resetpass1"})
    assert r.status_code == 200 and "비밀번호를 바꿨어요" in r.text
    # 탈퇴
    r = client.post("/account/delete", data={"password": "resetpass1"})
    assert r.status_code == 422                                      # 확인 체크 없이
    r = client.post("/account/delete", data={"password": "resetpass1", "confirm": "on"})
    assert r.status_code == 200 and "탈퇴했습니다" in r.text
    assert db.scalar(select(User).where(User.username == "webuser")) is None
    assert db.scalar(select(User.save_status)) == "N"


def test_leaderboard_filters(client):
    signup(client, "alice")
    assert client.get("/api/v1/leaderboard", params={"experience": "expert", "period": "week"}).json() == []
    assert client.get("/api/v1/leaderboard", params={"experience": "god"}).status_code == 422
    r = client.get("/ranking", params={"period": "week", "level": "beginner"})
    assert r.status_code == 200 and "최근 7일" in r.text


def test_pct_filter():
    from server.web.templating import pct
    assert (pct(-0.04), pct(0.04), pct(-1.26), pct(3.0), pct(-0.001, 2), pct(None)) == \
        ("0.0", "0.0", "-1.3", "+3.0", "0.00", "-")
