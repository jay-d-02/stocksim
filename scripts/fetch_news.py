"""실제 뉴스 지금 바로 가져오기 (평소에는 '오늘의 뉴스' 화면을 열 때 30분마다 알아서 가져온다)

docker compose exec app python -m scripts.fetch_news
"""
import sys

from server.config import settings
from server.db import SessionLocal
from server.runtime import get_store
from server.services import realnews as RN


def main():
    store = get_store()
    with SessionLocal() as db:
        added, err = RN.collect(db, store, settings)
        st = RN.status(store, settings)
        print(f"새 뉴스 {added}건 · 오늘 호출 {st['calls']}/{st['cap']}건")
        if err:
            print(err)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
