"""실제 뉴스 수집·화면: 가짜 검색 응답으로 저장·중복 제거·호출 상한·요인 필터를 확인 (네이버는 부르지 않음)"""
import dataclasses

from sqlalchemy import func, select

from server.config import settings
from server.models import RealNews
from server.runtime import get_store
from server.services import realnews as RN

ITEMS = {"items": [
    {"title": "원·달러 환율 1,400원 돌파", "description": "달러 강세가 이어졌다.",
     "originallink": "https://www.example.co.kr/fx", "pubDate": "Sat, 04 Oct 2026 10:00:00 +0900"},
    {"title": "국제유가 급등", "description": "", "originallink": "https://www.example.co.kr/oil",
     "pubDate": "Sat, 04 Oct 2026 11:00:00 +0900"},
    {"title": "연예 소식", "description": "", "originallink": "https://www.example.co.kr/etc",
     "pubDate": "Sat, 04 Oct 2026 12:00:00 +0900"},
]}


def keyed(**kw):
    return dataclasses.replace(settings, naver_client_id="id", naver_client_secret="secret", **kw)


def test_collect_dedupes_and_respects_daily_cap(db):
    calls = []
    fake = lambda s, q: calls.append(q) or ITEMS
    added, err = RN.collect(db, get_store(), keyed(news_daily_cap=3), fetch=fake)
    assert added == 2 and len(calls) == 3                       # 같은 뉴스는 한 번만, 분류 안 된 뉴스는 버림
    assert "호출 상한" in err
    assert db.scalar(select(func.count()).select_from(RealNews)) == 2
    added, err = RN.collect(db, get_store(), keyed(news_daily_cap=3), fetch=fake)
    assert added == 0 and len(calls) == 3                       # 오늘 상한에 이미 닿아 더 부르지 않음
    assert RN.status(get_store(), keyed(news_daily_cap=3))["error"] == err


def test_collect_without_keys_does_nothing(db):
    added, err = RN.collect(db, get_store(), dataclasses.replace(settings, naver_client_id=""),
                            fetch=lambda s, q: 1 / 0)
    assert added == 0 and "NAVER_CLIENT_ID" in err


def test_news_page_and_factor_filter(client, db):
    client.post("/register", data={"username": "webuser", "password": "secret123", "agree": "on"})
    assert "설정되지 않았습니다" in client.get("/learn/news").text           # 키가 없을 때 안내
    RN.collect(db, get_store(), keyed(), fetch=lambda s, q: ITEMS)
    page = client.get("/learn/news").text
    assert "원·달러 환율 1,400원 돌파" in page and "국제유가 급등" in page
    assert "환율 ↑ 오름" in page and "자동차" in page and 'rel="noopener noreferrer"' in page
    oil = client.get("/learn/news", params={"factor": "oil"}).text
    assert "국제유가 급등" in oil and "1,400원 돌파" not in oil
    assert "📰 오늘의 뉴스" in client.get("/learn").text                     # 투자 공부 탭에서 들어가는 길
