"""DATABASE_URL의 데이터베이스가 없으면 만든다 (이미 쓰던 PostgreSQL 볼륨에서도 바로 실행되도록)"""
import os
import re
import sys
import time

import psycopg
from sqlalchemy.engine import make_url


def ensure(url_str, retries=30):
    url = make_url(url_str)
    name = url.database
    if not re.fullmatch(r"[A-Za-z0-9_]+", name or ""):
        sys.exit(f"데이터베이스 이름이 올바르지 않습니다: {name!r}")
    admin = url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    for i in range(retries):
        try:
            with psycopg.connect(admin, autocommit=True, connect_timeout=3) as c:
                if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                    c.execute(f'CREATE DATABASE "{name}"')
                    print(f"데이터베이스 {name} 생성")
                return
        except psycopg.OperationalError as e:
            print(f"PostgreSQL 연결 대기 중 ({i + 1}/{retries}): {e}", file=sys.stderr)
            time.sleep(1)
    sys.exit("PostgreSQL에 연결하지 못했습니다.")


if __name__ == "__main__":
    ensure(os.environ["DATABASE_URL"])
