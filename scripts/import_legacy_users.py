"""예전(Flask) 버전의 회원 계정을 새 DB로 한 번 옮긴다. 비밀번호 해시 형식이 같아서 그대로 로그인된다.

docker compose exec app python -m scripts.import_legacy_users
(예전 DB 이름이 stocksim이 아니면 -e LEGACY_DB=이름, 다른 서버면 -e LEGACY_DATABASE_URL=주소)
"""
import os

import psycopg
from sqlalchemy.engine import make_url


def main():
    url = make_url(os.environ["DATABASE_URL"]).set(drivername="postgresql")
    target = url.render_as_string(hide_password=False)
    legacy = os.environ.get("LEGACY_DATABASE_URL") or \
        url.set(database=os.environ.get("LEGACY_DB", "stocksim")).render_as_string(hide_password=False)
    with psycopg.connect(legacy) as src:
        users = src.execute("SELECT username, pw_hash, created_at FROM users ORDER BY id").fetchall()
    with psycopg.connect(target) as dst:
        n = 0
        for username, pw_hash, created_at in users:
            # 닉네임은 아이디로. 아이디나 닉네임이 이미 있으면 건너뜀
            cur = dst.execute("INSERT INTO users (username, nickname, password_hash, created_at) VALUES (%s, %s, %s, %s) "
                              "ON CONFLICT DO NOTHING", (username, username, pw_hash, created_at))
            n += cur.rowcount
        dst.commit()
    print(f"예전 회원 {len(users)}명 중 {n}명을 옮겼습니다 (이미 있던 {len(users) - n}명은 건너뜀).")


if __name__ == "__main__":
    main()
