"""인증 API: 가입·로그인·검증"""
from .helpers import signup

REG = "/api/v1/auth/register"


def reg(client, **kw):
    return client.post(REG, json={"password": "secret123", "agree_privacy": True, **kw})


def test_register_login_logout(client):
    user = signup(client, "alice")
    assert client.get("/api/v1/auth/me").json() == user
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    r = client.post("/api/v1/auth/login", json={"username": "alice", "password": "secret123"})
    assert r.status_code == 200 and r.json()["username"] == "alice"


def test_wrong_password_and_duplicate(client):
    signup(client, "alice")
    r = client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong-pass"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "INVALID_CREDENTIALS"
    r = reg(client, username="alice", password="another12")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "USERNAME_TAKEN"


def test_validation(client):
    r = client.post(REG, json={"username": "ab", "password": "x", "agree_privacy": True})
    assert r.status_code == 422                                  # Pydantic 검증
    for extra in ({"nickname": "a"}, {"experience": "god"}, {"email": "not-an-email"},
                  {"password": "short7!"},                       # 8자 미만
                  {"password": "Alice"*2, "username": "alicealice"},   # 아이디와 같은 비밀번호
                  {"agree_privacy": False}):                     # 개인정보 동의 안 함
        r = reg(client, **{"username": "alice", **extra})
        assert r.status_code == 422, extra
    assert client.post(REG, json={"username": "alice", "password": "secret123"}).status_code == 422   # 동의 누락


def test_profile_fields(client, db):
    from sqlalchemy import select
    from server.models import User
    r = reg(client, username="alice", nickname="개미왕", experience="expert", email=" Alice@Example.com ")
    assert r.status_code == 201
    assert {k: r.json()[k] for k in ("nickname", "experience", "email")} == \
        {"nickname": "개미왕", "experience": "expert", "email": "alice@example.com"}
    assert db.scalar(select(User.privacy_agreed_at).where(User.username == "alice")) is not None   # 동의 시각 기록
    # 닉네임을 비우면 아이디, 경험은 처음, 이메일은 없음
    bob = signup(client, "bob")
    assert (bob["nickname"], bob["experience"], bob["email"]) == ("bob", "beginner", None)


def test_nickname_and_email_unique(client):
    assert reg(client, username="alice", nickname="개미왕", email="a@example.com").status_code == 201
    r = reg(client, username="bob", nickname="개미왕")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NICKNAME_TAKEN"
    r = reg(client, username="carol", email="A@EXAMPLE.COM")               # 대소문자만 다른 이메일도 중복
    assert r.status_code == 409 and r.json()["detail"]["code"] == "EMAIL_TAKEN"


def test_game_api_requires_login(client):
    assert client.get("/api/v1/games/current").status_code == 401
    assert client.post("/api/v1/games").status_code == 401


def test_password_hash_not_plaintext(client, db):
    from sqlalchemy import select
    from server.models import User
    signup(client, "alice")
    h = db.scalar(select(User.password_hash).where(User.username == "alice"))
    assert "secret123" not in h and h.startswith(("scrypt:", "pbkdf2:"))
