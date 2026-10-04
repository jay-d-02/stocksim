"""성향별 전략 봇 대결: 같은 시장(시드)에서 여러 전략을 돌려 어떤 투자 성향이 유리한지 본다 (게임 밸런스 점검)

docker run --rm -v "$PWD:/app" -w /app stocksim-tests python -m scripts.strategy_sim 300 out.json

DB·Redis 없이 엔진만 돌린다. 봇은 화면에 보이는 정보만 쓴다: 뉴스 제목, 요인 실험실의 업종 민감도,
예고 일정, 단서. 사건의 숨은 결과·계획(plan, effects, truth)은 보지 않는다.
모든 전략이 같은 시드 목록에서 겨루므로 (짝지은 비교) 시장 운이 빠지고 전략 차이만 남는다.
"""
import json
import os
import random
import statistics as S
import sys
import time
from collections import Counter
from multiprocessing import Pool

from simulator import analysis as A
from simulator import engine as E
from simulator.catalog import CLUES, CORP_EVENTS, MACRO_EVENTS, RATE_MARKET, RATE_SHOCK
from simulator.ledger import FeeSchedule, OrderRejected

START = 10_000_000
FEES = FeeSchedule.of(0.00015, 0.002)

# ---------------------------------------------------------------- 공개 정보 읽기: 뉴스 → {종목: 예상 효과 %}
MACRO = {e["title"]: E.event_impacts(e) for e in MACRO_EVENTS}          # 업종 민감도표로 누구나 계산 가능
CORP = {E._fmt(e["title"], s["name"]): (c, e["move"]) for e in CORP_EVENTS for c, s in E.STOCK.items()}
CLUE = {E._fmt(ch[k][0], s["name"], s["sector"]): (1 if k == "pos" else -1)
        for chans in CLUES.values() for ch in chans for k in ("pos", "neg") for s in E.STOCK.values()}
RESULT = [("웃돌아", 6), ("밑돌아", -6), ("성공!", 22), ("실패…", -28), ("소문이 사실로", 5), ("사실무근", -9)]


class Reader:
    """뉴스를 읽어 종목별 예상 효과를 냄. 금리는 예고(D-2)의 확률을 기억해 두었다가 '예상 대비 놀라움'으로 계산"""

    def __init__(self):
        self.rate = None

    def read(self, st, n):
        for u in E.upcoming(st):
            if u["type"] == "rate" and "p" in u:
                self.rate = (u["dir"], u["p"])
        t, codes = n["title"], n["codes"]
        if t in MACRO:
            return MACRO[t]
        if t in CORP:
            c, move = CORP[t]
            return {c: move}
        if n["tag"] == "단서" and t in CLUE:
            return {codes[0]: 2.0 * CLUE[t]}
        if n["tag"] == "루머":
            return {codes[0]: 8}
        if n["tag"] == "금리" and self.rate:
            d, p = self.rate
            hit = ("인상" in t and d > 0) or ("인하" in t and d < 0)
            surprise = d * (1 - p) if hit else -d * p
            return E.event_impacts({"shock": {"rate": surprise * RATE_SHOCK}, "market": -surprise * RATE_MARKET})
        if codes and n["tag"] in ("실적", "바이오", "공시"):
            for word, move in RESULT:
                if word in t:
                    return {codes[0]: move}
        return {}


# ---------------------------------------------------------------- 전략
class Bot:
    name, label, intent = "", "", ""

    def __init__(self, rng):
        self.rng, self.reader, self.mem = rng, Reader(), {}

    def total(self, st):
        return E.total_value(st)

    def buy(self, st, code, frac):
        """총자산의 frac만큼 (예수금 안에서) 매수"""
        money = min(st["cash"], self.total(st) * frac) / (1 + float(FEES.fee_rate))
        qty = int(money // E.price(st, code))
        if qty > 0:
            self.order(st, code, "BUY", qty)

    def sell(self, st, code, part=1.0):
        qty = int(st["holdings"].get(code, {}).get("qty", 0) * part)
        if qty > 0:
            self.order(st, code, "SELL", qty)

    def order(self, st, code, side, qty):
        try:
            E.execute_order(st, code, side, qty, FEES)
        except OrderRejected:
            pass

    def weight(self, st, code):
        h = st["holdings"].get(code)
        return h["qty"] * E.price(st, code) / self.total(st) if h else 0.0

    def step(self, st, fresh):
        pass


class Cash(Bot):
    name, label, intent = "cash", "현금만 보유", "기준선: 아무것도 안 함"


class Index(Bot):
    name, label, intent = "index", "지수 따라 사서 보유", "기준선: 첫날 12종목을 똑같이 사서 끝까지"

    def step(self, st, fresh):
        if st["day"] == 1 and st["tick"] == 1:
            for c in E.STOCK:
                self.buy(st, c, 0.99 / len(E.STOCK))


class Defensive(Bot):
    name, label, intent = "defensive", "신중한 장기 보유", "🐢 저베타 방어주(통신·식품·은행) 60% + 현금 40%, 끝까지 보유"

    def step(self, st, fresh):
        if st["day"] == 1 and st["tick"] == 1:
            for c in ("ONT", "GHF", "HGF"):
                self.buy(st, c, 0.2)


class Random(Bot):
    name, label, intent = "random", "무작위 매매", "기준선: 판단 없이 가끔 아무 종목이나 사고팖"

    def step(self, st, fresh):
        if self.rng.random() < 0.03:
            if st["holdings"] and self.rng.random() < 0.5:
                self.sell(st, self.rng.choice(sorted(st["holdings"])))
            else:
                self.buy(st, self.rng.choice(list(E.STOCK)), 0.2)


class NewsChaser(Bot):
    name, label, intent = "news", "뉴스 추종", "📰 호재가 뜨면 바로 사고, 악재가 뜨면 바로 팖"

    def step(self, st, fresh):
        for n in fresh:
            for c, v in self.reader.read(st, n).items():
                if v >= 3 and self.weight(st, c) < 0.2:
                    self.buy(st, c, 0.25)
                elif v <= -3:
                    self.sell(st, c)


class NewsLate(NewsChaser):
    name, label, intent = "news_late", "뉴스 추종 (30분 늦게)", "📰 뉴스 추종과 같지만 뉴스를 보고 30분 뒤에 주문 (사람의 반응 속도)"

    def step(self, st, fresh):
        now = st["day"] * E.TICKS + st["tick"]
        self.mem.setdefault("q", []).extend((now + 6, n) for n in fresh)
        due = [n for t, n in self.mem["q"] if t <= now]
        self.mem["q"] = [(t, n) for t, n in self.mem["q"] if t > now]
        super().step(st, due)


class Contrarian(Bot):
    name, label, intent = "contrarian", "역발상", "🧭 악재로 떨어진 종목을 사서 하루 뒤 팖"

    def step(self, st, fresh):
        now = st["day"] * E.TICKS + st["tick"]
        for n in fresh:
            for c, v in self.reader.read(st, n).items():
                if v <= -3 and c not in st["holdings"]:
                    self.buy(st, c, 0.25)
                    self.mem[c] = now
        for c, t in list(self.mem.items()):
            if now - t >= E.TICKS:
                self.sell(st, c)
                del self.mem[c]


class Momentum(Bot):
    name, label, intent = "momentum", "모멘텀 단타", "⚡ 30분마다 최근 30분 가장 많이 오른 2종목으로 갈아탐"

    def step(self, st, fresh):
        t = st["tick"]
        if t < 6 or t % 6:
            return
        moves = sorted(E.STOCK, key=lambda c: st["intra"][c][-1] / st["intra"][c][-7], reverse=True)
        top = moves[:2]
        for c in list(st["holdings"]):
            if c not in top:
                self.sell(st, c)
        for c in top:
            if c not in st["holdings"]:
                self.buy(st, c, 0.45)


class Conviction(Bot):
    name, label, intent = "conviction", "확신형 몰빵", "🎯 큰 호재(+5% 이상)에만 총자산 80%를 한 종목에, 악재나 5일 지나면 정리"

    def step(self, st, fresh):
        for n in fresh:
            sig = self.reader.read(st, n)
            for c, v in sig.items():
                if c in st["holdings"] and v <= -3:
                    self.sell(st, c)
            best = max(sig.items(), key=lambda x: x[1], default=None)
            if best and best[1] >= 5 and best[0] not in st["holdings"]:
                for c in list(st["holdings"]):
                    self.sell(st, c)
                self.buy(st, best[0], 0.8)
                self.mem = {best[0]: st["day"]}
        for c, d in list(self.mem.items()):
            if st["day"] - d >= 5 and c in st["holdings"]:
                self.sell(st, c)
                self.mem = {}


class RumorChaser(Bot):
    name, label, intent = "rumor", "소문 추격", "🗣 [찌라시] 인수설이 뜨면 사서 회사 답변 때 팖"

    def step(self, st, fresh):
        for n in fresh:
            c = n["codes"][0] if n["codes"] else None
            if n["tag"] == "루머":
                self.buy(st, c, 0.3)
            elif c and ("소문이 사실로" in n["title"] or "사실무근" in n["title"]):
                self.sell(st, c)


class ClueReader(Bot):
    name, label, intent = "clue", "단서 분석가", "🔎 예고된 사건의 단서를 모아 같은 쪽이 우세할 때만 들어가고 발표 때 정리"

    def step(self, st, fresh):
        for n in fresh:
            iss = n.get("issue")
            if not iss or not n["codes"]:
                continue
            c = n["codes"][0]
            m = self.mem.setdefault(iss, {"sum": 0, "code": c, "kind": n["tag"]})
            if n["tag"] == "루머":
                m["kind"] = "rumor"
            if n["tag"] == "단서" and n["title"] in CLUE:
                m["sum"] += CLUE[n["title"]]
                need = 2 if m["kind"] == "rumor" else 1
                if m["sum"] >= need and self.weight(st, c) < 0.15:
                    self.buy(st, c, 0.3)
                elif m["sum"] <= -1:
                    self.sell(st, c)
            elif n["tag"] in ("실적", "바이오", "공시") and any(w in n["title"] for w, _ in RESULT):
                self.sell(st, c)
                self.mem.pop(iss, None)


BOTS = [Cash, Index, Defensive, Random, NewsChaser, NewsLate, Contrarian, Momentum, Conviction, RumorChaser, ClueReader]
BY_NAME = {b.name: b for b in BOTS}


# ---------------------------------------------------------------- 한 판
def run(job):
    name, seed, days = job
    st = E.new_game(START, seed, days)
    bot = BY_NAME[name](random.Random(f"{seed}-{name}"))
    seen = 0
    while True:
        while st["phase"] == "open":
            E.step_tick(st)
            fresh = [n for n in st["news"] if n["id"] > seen]
            seen = st["seq"]
            bot.step(st, fresh)
        if st["finished"]:
            break
        E.begin_day(st)
    a = A.analyze(st)
    return {"bot": name, "seed": seed, "days": days, "ret": a["ret"], "mkt": a["mkt"], "mdd": a["mdd"],
            "trades": a["n_trades"], "persona": a["persona"][1] if a["ready"] else None,
            "traits": [t[1] for t in a["traits"]] if a["ready"] else []}


# ---------------------------------------------------------------- 모으기
def stats(rows, index_by_seed):
    rets = [r["ret"] for r in rows]
    diff = [r["ret"] - index_by_seed[r["seed"]] for r in rows]       # 같은 시장의 지수 봇 대비
    n = len(rows)
    se = S.stdev(diff) / n ** 0.5 if n > 1 else 0
    q = S.quantiles(rets, n=20)
    personas = Counter(r["persona"] or "판정 안 됨" for r in rows)
    traits = Counter(t for r in rows for t in r["traits"])
    return {"n": n, "mean": S.mean(rets), "median": S.median(rets), "sd": S.stdev(rets), "p5": q[0], "p95": q[-1],
            "min": min(rets), "max": max(rets), "mdd": S.mean(r["mdd"] for r in rows),
            "vs_index": S.mean(diff), "vs_index_ci": [S.mean(diff) - 1.96 * se, S.mean(diff) + 1.96 * se],
            "beat_index": sum(d > 0 for d in diff) / n, "loss_share": sum(x < 0 for x in rets) / n,
            "trades_per_day": S.mean(r["trades"] for r in rows) / rows[0]["days"],
            "personas": personas.most_common(), "traits": [(t, c / n) for t, c in traits.most_common(4)],
            "hist": rets}


def main(argv):
    n_seeds = int(argv[0]) if argv else 200
    out = argv[1] if len(argv) > 1 else "strategy_sim.json"
    seeds = [10_000 + i for i in range(n_seeds)]
    jobs = [(b.name, s, d) for d in E.GAME_DAYS for s in seeds for b in BOTS]
    t0 = time.perf_counter()
    rows = []
    with Pool(os.cpu_count()) as pool:
        for i, r in enumerate(pool.imap_unordered(run, jobs, chunksize=4), 1):
            rows.append(r)
            if i % 200 == 0:
                print(f"{i}/{len(jobs)} 판  {time.perf_counter() - t0:.0f}s", flush=True)
    result = {"seeds": n_seeds, "bots": [{"name": b.name, "label": b.label, "intent": b.intent} for b in BOTS],
              "by_days": {}}
    for d in E.GAME_DAYS:
        mine = [r for r in rows if r["days"] == d]
        idx = {r["seed"]: r["ret"] for r in mine if r["bot"] == "index"}
        mkt = S.mean(r["mkt"] for r in mine if r["bot"] == "index")
        result["by_days"][d] = {"market": mkt,
                                "bots": {b.name: stats([r for r in mine if r["bot"] == b.name], idx) for b in BOTS}}
        print(f"\n== {d}거래일 ({n_seeds}판씩, 시장 평균 {mkt:+.2f}%) ==")
        print(f"{'전략':<14}{'평균':>8}{'중앙':>8}{'하위5%':>8}{'지수대비':>9}{'95% 구간':>18}{'지수이김':>8}{'거래/일':>8}  판정 성향")
        for b in sorted(BOTS, key=lambda b: -result["by_days"][d]["bots"][b.name]["mean"]):
            s = result["by_days"][d]["bots"][b.name]
            lo, hi = s["vs_index_ci"]
            print(f"{b.label:<12}{s['mean']:>+8.2f}{s['median']:>+8.2f}{s['p5']:>+8.2f}{s['vs_index']:>+9.2f}"
                  f"  [{lo:+6.2f}, {hi:+6.2f}]{s['beat_index'] * 100:>7.0f}%{s['trades_per_day']:>8.1f}  "
                  f"{s['personas'][0][0]} {s['personas'][0][1] / s['n'] * 100:.0f}%")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"\n{len(jobs)}판, {time.perf_counter() - t0:.0f}s → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
