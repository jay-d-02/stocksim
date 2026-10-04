"""실제 뉴스: NAVER API HUB 뉴스 검색 → 요인(금리·환율·유가·경기) 분류 → 업종 영향 (게임의 민감도표로)

게임 시장과는 섞지 않는다 (게임은 시드로 정해지는 가상 시장이라 복구·복기·순위가 시드에 기대고 있다).
학습 탭에서 '게임에서 배운 눈으로 현실 뉴스 읽기' 용도.

수집: 화면을 열 때 마지막 수집이 REFRESH_SEC보다 오래됐으면 백그라운드로 한 번 (Redis 잠금으로 워커 하나만).
      손으로 돌리기: docker compose exec app python -m scripts.fetch_news
호출 상한: 하루 settings.news_daily_cap번. 버그로 반복 호출해도 무료 한도(월 775,000건)를 넘지 않도록.
분류: 키워드 규칙. 요인 단어 바로 뒤(없으면 바로 앞)의 방향 단어를 본다. 틀릴 수 있으므로 화면에
      '자동 분류'라고 밝히고 원문 링크를 함께 보인다. 본문은 받지도 저장하지도 않는다 (제목·요약·링크만).
"""
import html
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from simulator.catalog import FACTORS, SECTORS

from ..models import RealNews

log = logging.getLogger("realnews")

ENDPOINT = "https://naverapihub.apigw.ntruss.com/search/v1/news"
QUERIES = ["기준금리", "미국 연준 금리", "원달러 환율", "국제유가", "수출 실적", "경기 전망"]
PER_QUERY = 20             # 검색어마다 최신순으로 몇 건
REFRESH_SEC = 30 * 60      # 이보다 오래되면 화면을 열 때 새로 가져옴
KEEP_DAYS = 30
KEY_LAST, KEY_LOCK, KEY_ERR = "realnews:last", "realnews:lock", "realnews:error"


# ---------------------------------------------------------------- 분류
# 요인 단어. 헷갈리는 말은 앞뒤 글자로 가린다:
#   '고금리·고유가'는 변화가 아니라 수준, '경기도·경기지역·15경기'는 지명·운동, '수출주'는 수출 기업 주식,
#   '달러'는 금액 단위(10만 달러)로도 쓰이므로 방향 말이 바로 붙을 때만
FACTOR_RE = {
    "rate": r"(?<!고)금리",
    "fx": r"환율|원[·/]?달러|달러[-·/]?원|원화|달러 ?(?:강세|약세|가치|하락|상승|급락|급등)",
    "oil": r"(?<!고)유가|원유|WTI|브렌트유?|두바이유",
    "econ": r"(?<!\d)경기(?![도장력·]|지역)|수출(?![주입]|농가)|성장률|GDP|소비자? ?심리",
}
UP = ("인상", "상승", "급등", "오르", "올라", "올랐", "오른", "오름", "치솟", "뛰", "쑥", "최고", "증가", "급증", "늘어", "늘었",
      "회복", "개선", "호조", "반등", "확대", "강세", "돌파", "넘어", "↑")
DOWN = ("인하", "하락", "급락", "내려", "내렸", "내린", "내림", "떨어", "최저", "감소", "급감", "줄어", "줄었", "둔화", "침체",
        "부진", "반락", "축소", "위축", "약세", "뚝", "↓")
HOLD = ("동결",)
# 방향 말 뒤에 붙으면 뜻이 뒤집히는 말 ('인상 기대 후퇴' = 오를 거라는 기대가 줄어듦)
FLIP = ("후퇴", "주춤", "가능성 낮", "가능성 줄", "기대 꺾", "제동")
# 방향이 단어 그대로가 아닌 표현 (환율은 '원화가 강해지면' 내려간다)
FX_SPECIAL = (("원화 강세", -1), ("원화 가치 상승", -1), ("원화 약세", 1), ("원화 가치 하락", 1),
              ("달러 강세", 1), ("달러 약세", -1), ("달러 하락", -1), ("달러 급락", -1), ("달러 상승", 1),
              ("달러 급등", 1))
AFTER, BEFORE = 14, 8       # 요인 단어 뒤·앞으로 방향 단어를 찾을 글자 수
# 절 나누기. 숫자 사이의 쉼표·점(1,400원, 0.25%p)에서는 나누지 않는다
CLAUSE = re.compile(r"(?<!\d)[,.]|[,.](?!\d)|[!?…;]|\s[-–]\s")


def _direction(text, start, end, factor):
    """요인 단어(text[start:end]) 주변에서 방향 찾기: 1 오름 / -1 내림 / 0 그대로 / None 모름"""
    if factor == "fx":
        near = text[max(0, start - 2):end + AFTER]
        for phrase, d in FX_SPECIAL:
            if phrase in near:
                return d
    # 뒤쪽 창에서는 가장 앞의 단어, 앞쪽 창에서는 가장 뒤의 단어 (둘 다 요인 단어에 가장 가까운 것)
    after = text[end:end + AFTER]
    for window, nearest in ((after, min), (text[max(0, start - BEFORE):start], max)):
        hits = [(window.find(w), d) for words, d in ((HOLD, 0), (UP, 1), (DOWN, -1)) for w in words if w in window]
        if hits:
            i, d = nearest(hits)
            if window is after and any(f in window[i:] for f in FLIP):
                d = -d
            return d
    return None


def classify(title):
    """제목만 보고 {요인: 방향}. 검색 요약은 기사 중간을 잘라 온 것이라 주제와 무관한 숫자·문장이 섞여
    오분류가 많아서 쓰지 않는다 (2026-10-04 실제 뉴스 120건: 요약까지 보면 5개 중 1개꼴로만 맞았고, 제목만 보면 거의 다 맞음)"""
    out = {}
    for clause in CLAUSE.split(title):
        for factor, pat in FACTOR_RE.items():
            if factor in out:
                continue
            for m in re.finditer(pat, clause):
                d = _direction(clause, m.start(), m.end(), factor)
                if d is not None:
                    out[factor] = d
                    break
    return out


def sector_impacts(factors):
    """요인 방향 × 게임의 업종 민감도 → {업종: 점수}. 요인이 +1 움직일 때 주가 몇 %인지의 합 (크기는 상대 비교용)"""
    out = {}
    for sector, exp in SECTORS.items():
        v = sum(exp[f] * d for f, d in factors.items() if d)
        if abs(v) >= 0.3:
            out[sector] = round(v, 2)
    return out


def describe(factors):
    """화면용: [(요인 이름, 방향 글자)]"""
    word = {1: "↑ 오름", -1: "↓ 내림", 0: "→ 그대로"}
    return [(f, FACTORS[f], word[d], d) for f, d in factors.items()]


# ---------------------------------------------------------------- 가져오기
def _clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def parse_items(payload, query):
    """검색 응답 JSON → 저장할 행 목록 (요인을 하나도 못 찾은 뉴스는 버림)"""
    rows = []
    for it in payload.get("items", []):
        title, summary = _clean(it.get("title")), _clean(it.get("description"))
        link = it.get("originallink") or it.get("link")
        if not (title and link):
            continue
        factors = classify(title)
        if not factors:
            continue
        try:
            when = parsedate_to_datetime(it["pubDate"])
        except (KeyError, TypeError, ValueError):
            when = datetime.now(timezone.utc)
        rows.append({"link": link, "title": title, "summary": summary, "query": query, "published_at": when,
                     "source": urllib.parse.urlsplit(link).netloc, "factors": factors})
    return rows


def search(settings, query, timeout=5):
    url = f"{ENDPOINT}?{urllib.parse.urlencode({'query': query, 'display': PER_QUERY, 'sort': 'date'})}"
    req = urllib.request.Request(url, headers={"X-NCP-APIGW-API-KEY-ID": settings.naver_client_id,
                                               "X-NCP-APIGW-API-KEY": settings.naver_client_secret})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _take_call(r, cap):
    """오늘 호출 수를 하나 올리고 상한 안인지. 날짜는 한국 시간 기준"""
    key = f"realnews:calls:{datetime.now(timezone(timedelta(hours=9))):%Y%m%d}"
    n = r.incr(key)
    if n == 1:
        r.expire(key, 2 * 86400)
    return n <= cap


def collect(db, store, settings, fetch=search):
    """검색어마다 최신 뉴스를 가져와 저장. (새로 넣은 수, 오류 문장 또는 None)"""
    if not (settings.naver_client_id and settings.naver_client_secret):
        return 0, "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET이 설정되지 않았습니다."
    added, err = 0, None
    for q in QUERIES:
        if not _take_call(store.r, settings.news_daily_cap):
            err = f"오늘 호출 상한({settings.news_daily_cap}건)에 닿아 내일 다시 가져옵니다."
            break
        try:
            rows = parse_items(fetch(settings, q), q)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            err = f"뉴스 검색 실패 (HTTP {e.code}): {body}"
            break
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            err = f"뉴스 검색 실패: {e}"
            break
        if rows:
            res = db.execute(pg_insert(RealNews).values(rows).on_conflict_do_nothing(index_elements=[RealNews.link])
                             .returning(RealNews.id))
            added += len(res.all())
    db.execute(delete(RealNews).where(RealNews.published_at < datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)))
    db.commit()
    store.r.set(KEY_LAST, datetime.now(timezone.utc).isoformat())
    if err:
        store.r.set(KEY_ERR, err)
        log.warning(err)
    else:
        store.r.delete(KEY_ERR)
    return added, err


def needs_refresh(store):
    last = store.r.get(KEY_LAST)
    if not last:
        return True
    return datetime.now(timezone.utc) - datetime.fromisoformat(last.decode()) > timedelta(seconds=REFRESH_SEC)


def refresh_in_background(session_factory, store, settings):
    """화면 요청 뒤에 도는 작업. 여러 워커가 동시에 열어도 하나만 가져온다"""
    if not store.r.set(KEY_LOCK, "1", nx=True, ex=120):
        return
    try:
        if needs_refresh(store):
            with session_factory() as db:
                collect(db, store, settings)
    except Exception:                   # 화면과 무관한 백그라운드 작업: 실패는 로그만
        log.exception("실제 뉴스 수집 실패")
    finally:
        store.r.delete(KEY_LOCK)


def status(store, settings):
    last = store.r.get(KEY_LAST)
    err = store.r.get(KEY_ERR)
    calls = store.r.get(f"realnews:calls:{datetime.now(timezone(timedelta(hours=9))):%Y%m%d}")
    return {"configured": bool(settings.naver_client_id and settings.naver_client_secret),
            "last": datetime.fromisoformat(last.decode()) if last else None,
            "error": err.decode() if err else None, "calls": int(calls or 0), "cap": settings.news_daily_cap}


def recent(db, factor=None, limit=60):
    q = select(RealNews).order_by(RealNews.published_at.desc()).limit(limit)
    if factor:
        q = q.where(RealNews.factors.has_key(factor))
    return list(db.scalars(q))
