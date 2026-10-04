"""틱 처리 구간별 시간 측정 (성능 회귀 조사용): docker compose exec app python -m scripts.profile_tick"""
import time
from statistics import median

from sqlalchemy import select

from server.config import settings
from server.db import SessionLocal
from server.models import User
from server.runtime import get_store
from server.services.games import GameService
from simulator import engine as E


def main():
    store = get_store()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "zz_prof"))
        if not user:
            user = User(username="zz_prof", nickname="zz_prof", password_hash="x")
            db.add(user)
            db.commit()
        svc = GameService(db, store, settings)
        g, st = svc.create(user)
        parts = {k: [] for k in ("redis_get", "reconcile", "engine", "persist", "redis_put", "commit", "total")}
        for _ in range(40):
            t0 = time.perf_counter()
            g = svc._get(g.id, user, lock=True)
            a = time.perf_counter(); s = store.get(g.id); b = time.perf_counter()
            svc._reconcile(g, s); c = time.perf_counter()
            E.step_tick(s); g.current_tick = s["tick"]; d = time.perf_counter()
            svc._persist_news(g, s); e = time.perf_counter()
            store.put(g.id, s); f = time.perf_counter()
            db.commit(); h = time.perf_counter()
            for k, v in zip(parts, (b - a, c - b, d - c, e - d, f - e, h - f, h - t0)):
                parts[k].append(v * 1000)
        print("  ".join(f"{k} {median(v):.1f}ms" for k, v in parts.items()))


if __name__ == "__main__":
    main()
