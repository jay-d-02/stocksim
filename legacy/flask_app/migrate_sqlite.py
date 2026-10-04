"""
예전 SQLite DB(data/stocksim.db)의 회원·보유종목·거래내역을 PostgreSQL로 옮깁니다.
실행:  docker compose run --rm stocksim python migrate_sqlite.py
"""
import os
import sqlite3
import sys

import db as dbmod

SRC = os.environ.get("SQLITE_PATH", "data/stocksim.db")
if not os.path.exists(SRC):
    sys.exit(f"{SRC} 파일이 없습니다. 옮길 데이터가 없으면 이 단계는 건너뛰세요.")

src = sqlite3.connect(SRC)
src.row_factory = sqlite3.Row
dbmod.init_db()

with dbmod.pool.connection() as pg:
    if pg.execute("SELECT count(*) AS n FROM users").fetchone()["n"]:
        sys.exit("PostgreSQL에 이미 회원이 있어서 중단합니다. 빈 DB에서만 실행하세요.")

    users = src.execute("SELECT * FROM users").fetchall()
    for u in users:
        pg.execute("INSERT INTO users (id, username, pw_hash, cash, created_at) "
                   "OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s,%s,%s)",
                   (u["id"], u["username"], u["pw_hash"], u["cash"], u["created_at"]))
    holdings = src.execute("SELECT * FROM holdings").fetchall()
    for h in holdings:
        pg.execute("INSERT INTO holdings (user_id, code, qty, avg_price) VALUES (%s,%s,%s,%s)",
                   (h["user_id"], h["code"], h["qty"], h["avg_price"]))
    trades = src.execute("SELECT * FROM trades").fetchall()
    for t in trades:
        pg.execute("INSERT INTO trades (id, user_id, code, side, qty, price, fee, tax, profit, "
                   "created_at) OVERRIDING SYSTEM VALUE VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   (t["id"], t["user_id"], t["code"], t["side"], t["qty"], t["price"],
                    t["fee"], t["tax"], t["profit"], t["created_at"]))
    # 다음 id가 옮긴 데이터 뒤부터 이어지도록 맞춤
    for table in ("users", "trades"):
        pg.execute(f"SELECT setval(pg_get_serial_sequence('{table}','id'), "
                   f"COALESCE((SELECT max(id) FROM {table}), 0) + 1, false)")
    pg.commit()

print(f"완료: 회원 {len(users)}명, 보유종목 {len(holdings)}건, 거래 {len(trades)}건")
