"""가상 회원·게임 기록 넣기 (명예의 전당·관리자 화면·복기 화면을 채워 보는 용도)

docker compose exec app python -m scripts.seed_demo            # 회원 24명
docker compose exec app python -m scripts.seed_demo 40         # 회원 40명
docker compose exec app python -m scripts.seed_demo --clear    # 넣었던 가상 데이터만 지우기

실제 게임과 똑같이 GameService로 틱을 돌리고 성향이 다른 봇이 매매한다. 그래서 일봉·뉴스·체결·
투자 성향 분석이 전부 진짜 기록과 같은 모양으로 쌓인다. 끝난 시각은 최근 3주 안으로 흩어 둔다
('최근 7일' 순위에 일부만 걸리도록).
가상 회원은 아이디가 demo_ 로 시작하고 로그인할 수 없다 (비밀번호 해시가 아님).
다시 실행하면 예전 가상 데이터를 지우고 새로 넣는다.
"""
import random
import sys
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update

from server.config import settings
from server.db import SessionLocal
from server.models import Game, User
from server.runtime import get_store
from server.services.games import GameService, sync_stocks
from simulator import engine as E

PREFIX = "demo_"
NO_LOGIN = "!demo"         # 해시 형식이 아니라 어떤 비밀번호와도 맞지 않음
NICKS = ["불개미", "존버왕", "단타의신", "가치투자자", "배당러버", "차트장인", "새벽시장", "물타기달인",
         "익절은사랑", "손절의미학", "반도체덕후", "바이오도박사", "은행원김씨", "개미대장", "코스피요정",
         "환율읽는자", "유가감시단", "금리박사", "분산투자맨", "몰빵주의자", "현금부자", "뉴스헌터",
         "조용한고래", "초보탈출", "주린이", "장투족", "스윙트레이더", "역발상", "모멘텀러", "소문추적자",
         "실적분석가", "인내심", "타이밍장인", "평단관리", "포트폴리오", "기관흉내", "외인따라", "하한가줍줍",
         "상한가추격", "느긋한투자"]


# ---------------------------------------------------------------- 봇: 성향이 다른 가상 플레이어
def make_bot(rng):
    return {
        "trade": rng.uniform(0.005, 0.06),       # 틱마다 아무 종목이나 사고팔 확률
        "chase": rng.random() < 0.5,             # 기업 뉴스가 뜨면 그 종목을 따라 사는지
        "size": rng.uniform(0.08, 0.45),         # 한 번에 예수금의 몇 %를 쓰는지
        "sell": rng.uniform(0.3, 0.7),           # 매매할 때 팔 확률
        "favor": rng.sample(list(E.STOCK), rng.randint(2, len(E.STOCK))),   # 관심 종목
    }


def act(svc, g, user, st, bot, fresh, rng):
    """틱마다 주문은 많아야 하나 (st의 예수금·보유가 주문 뒤에도 맞도록)"""
    news = [n for n in fresh if n["kind"] == "event" and n["codes"]]
    if bot["chase"] and news and rng.random() < 0.6:
        side, code = "BUY", news[0]["codes"][0]
    elif rng.random() < bot["trade"]:
        if st["holdings"] and rng.random() < bot["sell"]:
            side, code = "SELL", rng.choice(list(st["holdings"]))
        else:
            side, code = "BUY", rng.choice(bot["favor"])
    else:
        return
    if side == "BUY":
        qty = int(st["cash"] * bot["size"] / E.price(st, code))
    else:
        held = st["holdings"].get(code, {}).get("qty", 0)
        qty = held if rng.random() < 0.6 else held // 2
    if qty > 0:
        svc.place_order(g.id, user, code, side, qty)


def play(svc, user, days, rng, stop_at=None):
    """한 판을 끝까지(stop_at이 있으면 그날까지만) 돌림"""
    g, st = svc.create(user, days)
    bot, seen = make_bot(rng), st["seq"]
    while True:
        while st["phase"] == "open":
            g, st = svc.tick(g.id, user)
            fresh = [n for n in st["news"] if n["id"] > seen]
            seen = st["seq"]
            act(svc, g, user, st, bot, fresh, rng)
            if stop_at and st["day"] >= stop_at and st["tick"] >= 30:
                return g
        if st["finished"]:
            return g
        g, st = svc.next_day(g.id, user)


# ---------------------------------------------------------------- 넣기·지우기
def clear(db, store):
    ids = list(db.scalars(select(User.id).where(User.username.like(f"{PREFIX}%"))))
    for gid in db.scalars(select(Game.id).where(Game.user_id.in_(ids))):
        store.delete(gid)
    db.execute(delete(User).where(User.id.in_(ids)))       # 게임·체결·뉴스 등은 CASCADE로 같이 지워짐
    db.commit()
    return len(ids)


def backdate(db, user, games, rng):
    """끝난 시각을 최근 3주 안으로 흩음 (가입 → 게임들 순서는 지킴)"""
    now = datetime.now(timezone.utc)
    t = now - timedelta(days=rng.uniform(8, 21))
    db.execute(update(User).where(User.id == user.id).values(created_at=t - timedelta(hours=rng.uniform(1, 48))))
    for g in games:
        start = t
        t = min(now, t + timedelta(minutes=g.max_day * rng.uniform(2, 5)))
        db.execute(update(Game).where(Game.id == g.id).values(started_at=start, finished_at=t))
        t += timedelta(days=rng.uniform(0.2, 6))
    db.commit()


def main(argv):
    store = get_store()
    with SessionLocal() as db:
        n = clear(db, store)
        if n:
            print(f"예전 가상 회원 {n}명과 그 게임을 지웠습니다.")
        if "--clear" in argv:
            return 0
        count = int(argv[0]) if argv and argv[0].isdigit() else 24
        sync_stocks(db)
        rng = random.Random()
        svc = GameService(db, store, settings)
        t0 = time.perf_counter()
        for i, nick in enumerate(rng.sample(NICKS, min(count, len(NICKS)))):
            user = User(username=f"{PREFIX}{i + 1:02d}", password_hash=NO_LOGIN, nickname=nick,
                        experience=rng.choice(["beginner", "beginner", "intermediate", "expert"]),
                        privacy_agreed_at=datetime.now(timezone.utc))
            db.add(user)
            db.commit()
            # 15일 판을 더 많이: 1~3판 끝까지, 셋 중 하나는 지금 진행 중인 판도
            done = [play(svc, user, rng.choice([15, 15, 30]), rng) for _ in range(rng.randint(1, 3))]
            backdate(db, user, done, rng)
            line = ", ".join(f"{g.max_day}일 {float(g.final_return_pct):+.2f}%" for g in done)
            if rng.random() < 0.35:
                days = rng.choice(E.GAME_DAYS)
                g = play(svc, user, days, rng, stop_at=rng.randint(1, days - 1))
                line += f" · 진행 중 {days}일 판 D{g.current_day}"
            print(f"[{i + 1:2d}] {nick:<8} ({user.experience}) {line}  {time.perf_counter() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
