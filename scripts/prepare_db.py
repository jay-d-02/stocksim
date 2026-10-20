"""외부 DB(Neon 등) 준비: 마이그레이션 + 종목 마스터 맞춤. Vercel에 올리기 전과 마이그레이션이 추가될 때 실행

docker compose run --rm --no-deps -e DATABASE_URL="Neon 직접 연결 주소" app python -m scripts.prepare_db
"""
from alembic import command
from alembic.config import Config

from server.db import SessionLocal
from server.services.games import sync_stocks


def main():
    command.upgrade(Config("alembic.ini"), "head")
    with SessionLocal() as db:
        sync_stocks(db)
    print("마이그레이션·종목 마스터 완료")


if __name__ == "__main__":
    main()
