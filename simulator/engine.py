"""시장 엔진: 가상 종목과 사건 시나리오로 움직이는 실시간 시장

하루(09:00~15:30)를 5분 단위 틱으로 나눈다. step_tick()을 부를 때마다 그 시각에 계획된
사건을 공개하고 가격을 움직인다. 오늘의 사건 계획(plan)과 남은 효과(effects)는 서버에만 있고
snapshot()으로 밖에 내보내지 않는다. 상태는 전부 JSON으로 저장 가능한 dict이며,
DB·웹과 무관한 순수 파이썬이다 (그래서 단위 테스트가 쉽다).
"""
import random

from . import ledger
from .catalog import (
    CLUE_ACC, CLUE_COUNT, CLUE_MOVE, CLUES, CORP_EVENTS, DEFAULT_DAYS, EVENT_PLACE, FACTORS, GAME_DAYS, HQ,
    INFORMED, ISSUE_TRUE, LIMIT, MACRO_EVENTS, PLACES, PRE_DAYS, RATE_DAYS, RATE_MARKET,
    RATE_SHOCK, RATE_TICK, SECTORS, STOCK, STOCKS, TAGS, TICKS, TICK_MIN, by_sector,
)

VERSION = 3        # 3: 인수설·실적·임상 결과를 예고 때 정하고 단서를 흘림 (2 이하 게임은 예전 규칙 그대로)


# ---------------------------------------------------------------- 유틸
def tick(p):
    """가격을 호가 단위에 맞춤"""
    for lim, t in ((2000, 1), (5000, 5), (20000, 10), (50000, 50),
                   (200000, 100), (500000, 500)):
        if p < lim:
            return max(t, int(round(p / t) * t))
    return int(round(p / 1000) * 1000)


def clock(t):
    m = 9 * 60 + t * TICK_MIN
    return f"{m // 60:02d}:{m % 60:02d}"


def event_impacts(ev):
    """거시 사건 → {종목코드: 효과%}"""
    out = {}
    for code, s in STOCK.items():
        exp = SECTORS[s["sector"]]
        v = sum(exp[f] * x for f, x in ev.get("shock", {}).items())
        v += ev.get("sector", {}).get(s["sector"], 0)
        v += ev.get("market", 0) * s["beta"]
        if abs(v) >= 0.05:
            out[code] = v
    return out


def _add_effect(state, code, total, now=0.6, days=2.0, quick=1):
    """효과 일부(now)는 뉴스가 뜬 그 순간 반영, 나머지는 며칠(days)에 걸쳐 천천히 반영.
    quick=1이면 뉴스를 본 뒤에 사도 이미 오른 가격이다 (실제 시장처럼 첫 반응은 공짜가 아님)"""
    imp = state.setdefault("_imp", {})      # 바로 뒤 _news()가 가져가 분석용 기록(nmeta)에 남김
    imp[code] = imp.get(code, 0) + total
    if now:
        state["effects"].append({"code": code, "per": total * now / quick, "left": quick})
    rest = total * (1 - now)
    if days and abs(rest) > 0.01:
        n = max(1, int(days * TICKS))
        state["effects"].append({"code": code, "per": rest / n, "left": n})


def _news(state, title, body, why="", factor=None, tag="시장", codes=(), kind="event",
          place=None, time=None, ekey=None, issue=None):
    state["seq"] += 1
    n = {"id": state["seq"], "day": state["day"], "time": time or clock(state["tick"]),
         "title": title, "body": body, "why": why, "factor": factor, "tag": tag,
         "codes": list(codes), "kind": kind}
    if issue:
        n["issue"] = issue          # 같은 사건(예고 → 단서 → 결과)의 뉴스끼리 묶는 번호
    if place in PLACES:
        n["place"], n["lat"], n["lon"] = PLACES[place]
        n["kr"] = place in ("sejong", "seoul")
    elif codes:
        n["place"], n["lat"], n["lon"] = HQ[codes[0]]
        n["kr"] = True
    state["news"].append(n)
    # 투자 성향 분석용: 이 뉴스가 움직인 종목과 그 순간 가격 (화면으로 보내지 않음)
    imp = {c: round(v, 2) for c, v in state.pop("_imp", {}).items() if abs(v) >= 1}
    if imp and state["intra"]:
        state.setdefault("nmeta", {})[str(n["id"])] = {
            "t": state["tick"], "key": ekey, "imp": imp, "px": {c: price(state, c) for c in imp}}


def _josa(name):
    """'이(가)' 정리: 받침 있으면 이, 없으면 가"""
    ch = name[-1]
    if "가" <= ch <= "힣":
        return name + ("이" if (ord(ch) - 0xAC00) % 28 else "가")
    return name + "이(가)"


def _fmt(text, name, sector=""):
    return text.replace("{name}이(가)", _josa(name)).replace("{name}", name).replace("{sector}", sector)


# ---------------------------------------------------------------- 게임 생성
def new_game(start_cash, seed=None, days=DEFAULT_DAYS):
    if days not in GAME_DAYS:
        raise ValueError(f"게임 길이는 {GAME_DAYS} 중 하나: {days}")
    seed = seed if seed is not None else random.randrange(1 << 30)
    rng = random.Random(seed)
    prices = {}
    for code, s in STOCK.items():
        # 시작가에서 거꾸로 걸어가 과거 차트를 만든 뒤 순서를 뒤집음
        p, hist = s["base"] * rng.uniform(0.9, 1.1), []
        for _ in range(PRE_DAYS):
            hist.append(tick(p))
            p /= 1 + rng.gauss(0.0005, s["vol"] / 100)
        prices[code] = hist[::-1]
    ind = {"rate": 3.50, "fx": 1330.0, "oil": 78.0}
    state = {
        "v": VERSION, "seed": seed, "day": 0, "tick": TICKS, "phase": "closed",
        "max_day": days, "finished": False, "recorded": False,
        "start_cash": start_cash, "cash": start_cash,
        "holdings": {}, "trades": [], "prices": prices, "intra": {}, "px": {},
        "effects": [], "plan": [], "scheduled": [], "news": [], "recent": {},
        "seq": 0, "sid": 0, "ind": ind, "ind_open": dict(ind), "report": None,
        # 투자 성향 분석용 기록
        "equity": [], "hl": {c: [] for c in STOCK}, "nmeta": {},
    }
    for d in RATE_DAYS:
        if d > days:
            break
        state["sid"] += 1
        state["scheduled"].append({"id": state["sid"], "day": d, "type": "rate", "notice": d - 2})
    begin_day(state)
    _news(state, f"투자 게임 시작! {days}거래일 동안 수익률을 겨뤄 보세요",
          "시계가 흐르면 세계 곳곳에서 사건이 터집니다. 지도와 알림을 보고 어떤 업종이 이득을 보고 "
          "손해를 볼지 판단해 보세요. 언제든 일시정지하고 생각할 수 있습니다.",
          "처음이라면 '가격변동 요인' 탭에서 금리·환율·유가가 업종별로 어떤 영향을 주는지 먼저 읽어 보세요.",
          tag="안내", kind="info", time="08:30")
    return state


# ---------------------------------------------------------------- 하루 시작 / 틱 / 마감
def begin_day(state):
    """장 시작: 아침 예고 뉴스를 내고, 오늘 일어날 사건 계획을 세움 (계획은 화면에 안 보임)"""
    if state["finished"] or state["phase"] == "open":
        return state
    state["day"] += 1
    state["tick"], state["phase"], state["report"] = 0, "open", None
    day = state["day"]
    rng = random.Random(f"{state['seed']}-{day}-plan")
    state["intra"] = {c: [state["prices"][c][-1]] for c in STOCK}
    state["px"] = {c: float(state["prices"][c][-1]) for c in STOCK}
    state["ind_open"] = dict(state["ind"])
    state["open_total"] = total_value(state)

    plan = []
    for s in state["scheduled"]:
        if s.get("notice") == day and not s.get("noticed"):
            _notice(state, s, rng)
            s["noticed"] = True
        if s["day"] == day:
            t = RATE_TICK if s["type"] == "rate" else s["tick"] if "tick" in s else rng.randint(4, TICKS - 8)
            plan.append({"tick": t, "kind": "resolve", "sid": s["id"]})
        for i, c in enumerate(s.get("clues", ())):
            if c["day"] == day:
                plan.append({"tick": c["tick"], "kind": "clue", "sid": s["id"], "i": i})

    recent = state["recent"]
    for _ in range((rng.random() < 0.5) + (rng.random() < 0.15)):
        pool = [e for e in MACRO_EVENTS if day - recent.get(e["key"], -99) > 6]
        if pool:
            ev = rng.choice(pool)
            recent[ev["key"]] = day
            t = 1 if ev["key"] == "us_crash" else rng.randint(2, TICKS - 4)
            plan.append({"tick": t, "kind": "macro", "key": ev["key"]})
    for _ in range((rng.random() < 0.45) + (rng.random() < 0.15)):
        plan.append({"tick": rng.randint(2, TICKS - 4), "kind": "corp"})
    if rng.random() < 0.55:
        plan.append({"tick": rng.randint(2, TICKS - 10), "kind": "schedule"})
    state["plan"] = sorted(plan, key=lambda p: p["tick"])
    return state


def step_tick(state):
    """5분 진행. 장중이 아니면 아무것도 안 함."""
    if state["phase"] != "open":
        return state
    state["tick"] += 1
    t = state["tick"]
    rng = random.Random(f"{state['seed']}-{state['day']}-{t}")

    for p in [p for p in state["plan"] if p["tick"] <= t]:
        _run_plan(state, p, rng)
    state["plan"] = [p for p in state["plan"] if p["tick"] > t]

    eff = {}
    for e in state["effects"]:
        eff[e["code"]] = eff.get(e["code"], 0) + e["per"]
        e["left"] -= 1
    state["effects"] = [e for e in state["effects"] if e["left"] > 0]

    # 가격 움직임 = 시장 전체 + 업종 + 개별 잡음 + 사건 효과 (하루 변동성을 틱 수로 나눔)
    k = (1 / TICKS) ** 0.5
    mkt = rng.gauss(0.08 / TICKS, 0.6 * k)
    sec = {s: rng.gauss(0, 0.4 * k) for s in SECTORS}
    for code, s in STOCK.items():
        r = s["beta"] * mkt + sec[s["sector"]] + rng.gauss(0, s["vol"] * 0.6 * k) + eff.get(code, 0)
        prev = state["prices"][code][-1]
        px = state["px"][code] * (1 + r / 100)
        px = max(prev * (1 - LIMIT / 100), min(prev * (1 + LIMIT / 100), px), 100)
        state["px"][code] = px
        state["intra"][code].append(tick(px))

    ind = state["ind"]
    ind["fx"] = round(max(1000, ind["fx"] + rng.gauss(0, 4 * k)), 1)
    ind["oil"] = round(max(30, ind["oil"] + rng.gauss(0, 0.8 * k)), 2)
    _track_exposure(state)

    if t >= TICKS:
        _close_day(state)
    return state


def _track_exposure(state):
    """투자 성향 분석용: 5분마다 현금 비중·베타·집중도·업종 비중을 누적 (장중 내내의 평균을 내기 위해)"""
    ex = state.setdefault("expo", {"n": 0, "cash": 0.0, "beta": 0.0, "inv_n": 0, "top": 0.0,
                                   "hold": 0.0, "sec": {}})
    alloc = {c: price(state, c) * h["qty"] for c, h in state["holdings"].items()}
    tot, inv = state["cash"] + sum(alloc.values()), sum(alloc.values())
    ex["n"] += 1
    ex["cash"] += state["cash"] / tot
    ex["beta"] += sum(v * STOCK[c]["beta"] for c, v in alloc.items()) / tot
    for c, v in alloc.items():
        s = STOCK[c]["sector"]
        ex["sec"][s] = ex["sec"].get(s, 0) + v / tot
    if inv > 0:
        ex["inv_n"] += 1
        ex["top"] += max(alloc.values()) / inv
        ex["hold"] += len(alloc)


def _close_day(state):
    day = state["day"]
    moves = []
    for c in STOCK:
        close = state["intra"][c][-1]
        moves.append(((close / state["prices"][c][-1] - 1) * 100, c))
        state["prices"][c].append(close)
        state.setdefault("hl", {}).setdefault(c, []).append(
            [max(state["intra"][c]), min(state["intra"][c])])
    moves.sort()
    state["phase"] = "closed"
    state.setdefault("equity", []).append({
        "day": day, "total": total_value(state), "cash": state["cash"], "index": index_value(state),
        "alloc": {c: price(state, c) * h["qty"] for c, h in state["holdings"].items()}})
    state["report"] = {
        "day": day, "open_total": state["open_total"], "total": total_value(state),
        "best": moves[-1][1], "best_pct": moves[-1][0],
        "worst": moves[0][1], "worst_pct": moves[0][0],
        "events": sum(1 for n in state["news"] if n["day"] == day and n["kind"] == "event"),
    }
    if day >= state["max_day"]:
        state["finished"] = True
        _news(state, "마지막 거래일이 끝났습니다", "최종 자산으로 순위가 기록됩니다.", tag="안내", kind="info")
    state["news"] = state["news"][-150:]
    keep = {str(n["id"]) for n in state["news"]}
    state["nmeta"] = {k: v for k, v in state.get("nmeta", {}).items() if k in keep}


def _run_plan(state, p, rng):
    if p["kind"] == "resolve":
        s = next((s for s in state["scheduled"] if s["id"] == p["sid"]), None)
        if s:
            _resolve(state, s, rng)
            state["scheduled"].remove(s)
    elif p["kind"] == "macro":
        ev = next(e for e in MACRO_EVENTS if e["key"] == p["key"])
        for code, v in event_impacts(ev).items():
            _add_effect(state, code, v * rng.uniform(0.8, 1.2))
        for k, dv in ev.get("ind", {}).items():
            state["ind"][k] += dv * rng.uniform(0.8, 1.2)
        _news(state, ev["title"], ev["body"], ev["why"], factor=ev["factor"],
              tag=TAGS.get(ev["factor"], "시장"), place=EVENT_PLACE.get(ev["key"]), ekey=ev["key"])
    elif p["kind"] == "corp":
        ev = rng.choices(CORP_EVENTS, weights=[e.get("w", 1) for e in CORP_EVENTS])[0]
        code = rng.choice([c for c, s in STOCK.items() if s["sector"] not in ev.get("skip", ())])
        name = STOCK[code]["name"]
        _add_effect(state, code, ev["move"] * rng.uniform(0.8, 1.2), now=0.65, days=1.5)
        _news(state, _fmt(ev["title"], name), _fmt(ev["body"], name), ev["why"],
              factor=ev["factor"], tag="공시", codes=[code], ekey=ev["key"])
    elif p["kind"] == "schedule":
        _schedule_random(state, rng)
    elif p["kind"] == "clue":
        s = next((s for s in state["scheduled"] if s["id"] == p["sid"]), None)
        if s:
            _clue(state, s, p["i"])


# ---------------------------------------------------------------- 예고가 있는 일정
def _add_scheduled(state, **kw):
    state["sid"] += 1
    state["scheduled"].append({"id": state["sid"], **kw})
    return state["scheduled"][-1]


def _v3(state):
    return state.get("v", 2) >= 3


def _setup_issue(state, s):
    """결과(truth)·발표 시각·단서 일정을 지금 정해 둔다. 전부 서버에만 있고 snapshot()으로 나가지 않는다.
    난수는 (시드, 일정 번호)로 따로 만들어 시장의 나머지 흐름을 바꾸지 않는다"""
    kind, day, now = s["type"], state["day"], state["tick"]
    rng = random.Random(f"{state['seed']}-{s['id']}-issue")
    s["truth"] = rng.random() < ISSUE_TRUE[kind]
    s["tick"] = rng.randint(4, TICKS - 8)
    # 단서는 지금부터 발표 직전 사이 아무 때나 (장중 틱만)
    slots = [(d, t) for d in range(day, s["day"] + 1) for t in range(1, TICKS)
             if (d, t) > (day, now + 1) and (d, t) < (s["day"], s["tick"] - 1)]
    lo, hi = CLUE_COUNT[kind]
    n = min(len(slots), rng.randint(lo, hi))
    chans = rng.sample(range(len(CLUES[kind])), n)
    s["clues"] = [{"day": d, "tick": t, "ch": ch, "pos": (rng.random() < CLUE_ACC[kind]) == s["truth"]}
                  for (d, t), ch in zip(sorted(rng.sample(slots, n)), chans)]
    for i, c in enumerate(s["clues"]):
        if c["day"] == day:                         # 오늘 나올 단서는 바로 계획에 (다음 날부터는 begin_day가)
            state["plan"].append({"tick": c["tick"], "kind": "clue", "sid": s["id"], "i": i})
    # 진실을 아는 쪽의 조용한 매매: 발표까지 조금씩 한쪽으로 기움 (차트를 보는 사람에게 주는 단서).
    # _add_effect를 쓰면 다음 뉴스의 분석 기록(nmeta)에 섞이므로 직접 넣는다
    left = max(1, (s["day"] - day) * TICKS + s["tick"] - now - 1)
    move = INFORMED[kind] * (1 if s["truth"] else -1)
    state["effects"].append({"code": s["code"], "per": move / left, "left": left})
    state.setdefault("issues", {})[str(s["id"])] = {
        "type": kind, "code": s["code"], "truth": s["truth"], "start": [day, now], "clues": [], "end": None}


def _clue(state, s, i):
    c = s["clues"][i]
    ch, st = CLUES[s["type"]][c["ch"]], STOCK[s["code"]]
    title, body = ch["pos" if c["pos"] else "neg"]
    sig = 1 if c["pos"] else -1
    _add_effect(state, s["code"], CLUE_MOVE * sig, now=0.8, days=0.5)
    _news(state, _fmt(title, st["name"], st["sector"]), _fmt(body, st["name"], st["sector"]), ch["why"],
          factor="sentiment" if s["type"] != "earn" else "earnings", tag="단서", codes=[s["code"]],
          ekey=f"{s['type']}_clue", issue=s["id"])
    log = state.get("issues", {}).get(str(s["id"]))
    if log is not None:
        log["clues"].append([state["day"], state["tick"], sig])


def _when(state, day):
    d = day - state["day"]
    return {0: "오늘 장중", 1: "내일 장중", 2: "모레 장중"}.get(d, f"{d}일 뒤 장중")


def _notice(state, s, rng):
    """아침 브리핑: 금리 결정 예고"""
    if s["type"] == "rate":
        s["dir"] = rng.choice([1, -1]) if state["ind"]["rate"] > 2.0 else 1
        s["p"] = rng.choice([0.2, 0.35, 0.5, 0.65, 0.8])
        word = "인상" if s["dir"] > 0 else "인하"
        _news(state, f"금리 결정 D-2: 시장은 기준금리 {word} 가능성 {int(s['p']*100)}% 예상",
              f"모레 오전 10시 기준금리를 결정하는 회의가 열립니다. 전문가들은 {word} 확률을 "
              f"{int(s['p']*100)}%로 보고 있습니다.",
              "시장은 예상되는 결과를 미리 가격에 반영합니다. 확률이 높은 결과가 나오면 주가 반응은 작고, "
              "예상 밖의 결과가 나올수록 반응이 커집니다.",
              factor="priced", tag="일정", kind="preview", place="seoul", time="08:30")


def _resolve(state, s, rng):
    t = s["type"]
    log = state.get("issues", {}).get(str(s["id"]))
    if log is not None:
        log["end"] = [state["day"], state["tick"]]
    issue = s["id"] if "truth" in s else None
    if t == "rate":
        if "p" not in s:          # 예고 없이 온 경우 대비
            s["dir"], s["p"] = 1, 0.5
        happened = rng.random() < s["p"]
        word = "인상" if s["dir"] > 0 else "인하"
        if happened:
            state["ind"]["rate"] = round(state["ind"]["rate"] + 0.25 * s["dir"], 2)
            surprise = s["dir"] * (1 - s["p"])      # 예상했던 만큼은 이미 반영됨
            title = f"기준금리 0.25%p {word}… 연 {state['ind']['rate']:.2f}%"
            body = f"예상 확률 {int(s['p']*100)}%였던 금리 {word}{'이' if s['dir'] > 0 else '가'} 현실이 됐습니다."
        else:
            surprise = -s["dir"] * s["p"]
            title = f"기준금리 동결… 연 {state['ind']['rate']:.2f}% 유지"
            body = (f"시장은 {int(s['p']*100)}% 확률로 {word}{'을' if s['dir'] > 0 else '를'} 예상했지만 "
                    "금리는 그대로입니다.")
        up = "은행은 유리, 성장주(바이오·2차전지·게임)와 건설은 불리" if surprise > 0 else \
             "성장주(바이오·2차전지·게임)와 건설은 유리, 은행은 불리"
        ev = {"shock": {"rate": surprise * RATE_SHOCK}, "market": -surprise * RATE_MARKET}
        for code, v in event_impacts(ev).items():
            _add_effect(state, code, v, now=0.7, days=1.5)
        _news(state, title, body,
              f"실제 금리 변화보다 '예상과 얼마나 달랐는가'가 중요합니다. 이번 결과는 시장 예상보다 "
              f"{'금리가 높은' if surprise > 0 else '금리가 낮은'} 쪽이어서 {up}합니다. "
              "금리가 오르면 미래 이익의 현재 가치가 줄고 돈 빌리는 비용이 늘어납니다.",
              factor="rate", tag="금리", place="seoul", ekey="rate_decision")

    elif t == "earn":
        st = STOCK[s["code"]]
        good = s["truth"] if "truth" in s else rng.random() < 0.5
        move = {"high": (1, -10), "normal": (6, -6), "low": (9, -2)}[s["exp"]][0 if good else 1]
        _add_effect(state, s["code"], move, now=0.75, days=1)
        exp_word = {"high": "높았던", "normal": "보통이던", "low": "낮았던"}[s["exp"]]
        if good:
            title = f"{st['name']} 실적 발표: 시장 기대 웃돌아"
            why = ("기대가 높았던 만큼 좋은 실적은 이미 주가에 들어가 있어 추가 상승은 작습니다. "
                   "'소문에 사서 뉴스에 팔아라'라는 말이 여기서 나옵니다.") if s["exp"] == "high" else \
                  "예상보다 좋은 실적은 미래 이익 전망을 끌어올려 주가를 밀어 올립니다."
        else:
            title = f"{st['name']} 실적 발표: 시장 기대 밑돌아"
            why = {"low": "기대가 낮았기 때문에 실망스러운 실적도 크게 놀랍지 않아 하락폭이 작습니다.",
                   "normal": "예상보다 나쁜 실적은 앞으로의 이익 전망을 낮춰 주가를 끌어내립니다.",
                   "high": "기대가 높을수록 실망도 큽니다. 기대감에 미리 올라 있던 주가가 한꺼번에 빠집니다."}[s["exp"]]
        _news(state, title, f"기대가 {exp_word} {st['name']}의 이번 분기 실적이 발표됐습니다.",
              why, factor="earnings", tag="실적", codes=[s["code"]], ekey="earnings", issue=issue)

    elif t == "trial":
        st = STOCK[s["code"]]
        ok = s["truth"] if "truth" in s else rng.random() < 0.45
        _add_effect(state, s["code"], 22 if ok else -28, now=0.85, days=1)
        _news(state, f"{st['name']} 신약 임상 3상 {'성공! 허가 신청 예정' if ok else '실패… 주요 목표 달성 못 해'}",
              "발표 결과에 따라 투자자들이 한꺼번에 몰리거나 빠져나가고 있습니다.",
              "바이오 기업의 가치는 대부분 '신약이 성공하면 벌 돈'에 대한 기대입니다. 결과 하나로 그 기대가 "
              "현실이 되거나 사라지기 때문에 주가가 극단적으로 움직입니다. 결과 전에 몰빵하는 것은 도박에 가깝습니다.",
              factor="sentiment", tag="바이오", codes=[s["code"]], ekey="trial_result", issue=issue)

    elif t == "rumor":
        st = STOCK[s["code"]]
        clue = " 발표 전에 나온 단서들을 되짚어 보세요." if issue else ""
        if (s["truth"] if "truth" in s else rng.random() < 0.35):
            _add_effect(state, s["code"], 5, now=0.8, days=0.5)
            _news(state, f"{st['name']}, 인수합병 공식 발표… 소문이 사실로",
                  "회사가 인수합병 계약을 공식 발표했습니다.",
                  "소문이 사실로 확인되면 불확실성이 사라져 한 번 더 오릅니다. 하지만 이런 경우는 생각보다 드뭅니다." + clue,
                  factor="sentiment", tag="공시", codes=[s["code"]], ekey="rumor_result", issue=issue)
        else:
            _add_effect(state, s["code"], -9, now=0.8, days=0.5)
            _news(state, f"{st['name']} \"인수설은 사실무근\" 공시",
                  "회사가 인수합병설을 공식 부인했습니다. 소문을 믿고 샀던 투자자들이 서둘러 팔고 있습니다.",
                  "확인되지 않은 소문으로 오른 주가는 부인 공시 한 번에 제자리로 돌아옵니다. "
                  "소문은 늦게 들을수록 비싼 가격에 사게 됩니다." + clue,
                  factor="sentiment", tag="공시", codes=[s["code"]], ekey="rumor_result", issue=issue)


def _schedule_random(state, rng):
    """실적 발표·임상·루머처럼 예고가 있는 기업 사건"""
    day, last = state["day"], state["max_day"]
    busy = {s.get("code") for s in state["scheduled"]}
    free = [c for c in STOCK if c not in busy]
    if not free:
        return
    roll = rng.random()
    v3 = _v3(state)
    hint = " 발표 전까지 나오는 [단서] 뉴스를 모아 보면 결과를 가늠할 수 있습니다." if v3 else ""
    if roll < 0.7 and day + 2 <= last:
        code = rng.choice(free)
        exp = rng.choice(["high", "normal", "low"])
        s = _add_scheduled(state, day=day + 2, type="earn", code=code, exp=exp)
        pre = {"high": 3.0, "normal": 0, "low": -2.5}[exp]
        if pre:
            _add_effect(state, code, pre, now=0, days=1.5)
        name = STOCK[code]["name"]
        word = {"high": "좋을 것", "normal": "평범할 것", "low": "부진할 것"}[exp]
        _news(state, f"{name} 실적 발표 D-2: 증권가 \"{word}\"",
              f"모레 {name}의 분기 실적이 나옵니다. 증권사들은 실적이 {word}으로 보고 있습니다.",
              "시장 예상치(컨센서스)는 이미 주가에 반영돼 있습니다. 주가는 실적이 '좋은가'보다 "
              "'예상보다 좋은가'에 반응합니다." + hint,
              factor="priced", tag="일정", codes=[code], kind="preview", ekey="earnings_preview",
              issue=s["id"] if v3 else None)
        if v3:
            _setup_issue(state, s)
    elif roll < 0.82 and day + 3 <= last and "SBB" in free:
        s = _add_scheduled(state, day=day + 3, type="trial", code="SBB")
        _add_effect(state, "SBB", 5, now=0, days=2.5)
        _news(state, "새봄바이오 신약 임상 3상 결과 D-3",
              "사흘 뒤 새봄바이오가 개발 중인 신약의 최종 임상 결과가 공개됩니다. 기대감에 주가가 들썩입니다.",
              "결과를 앞두고 기대감에 오르는 것은 흔한 일입니다. 하지만 결과는 성공 아니면 실패, 둘 중 하나입니다." + hint,
              factor="sentiment", tag="일정", codes=["SBB"], kind="preview", ekey="trial_preview",
              issue=s["id"] if v3 else None)
        if v3:
            _setup_issue(state, s)
    elif day + 2 <= last:
        code = rng.choice(free)
        # v3부터는 숨기지 않음: 조회공시 답변 기한이 '예정된 일정'에 뜬다 (결과는 여전히 숨김)
        s = _add_scheduled(state, day=day + rng.randint(1, 2), type="rumor", code=code,
                           **({} if v3 else {"hidden": True}))
        _add_effect(state, code, 8, now=0.8, days=0.5)
        name = STOCK[code]["name"]
        _news(state, f"[찌라시] \"{name}, 해외 대기업이 인수 추진\"",
              f"메신저를 통해 {name} 인수설이 퍼지며 주가가 급등했습니다. 회사는 아직 입장을 내지 않았습니다.",
              "공식 발표가 아닌 소문입니다. 소문에 올라탄 주가는 사실 여부가 확인되는 순간 크게 움직입니다.",
              factor="sentiment", tag="루머", codes=[code], ekey="rumor", issue=s["id"] if v3 else None)
        if v3:
            _setup_issue(state, s)
            _news(state, f"[조회공시] 거래소, {name}에 인수설 사실 여부 답변 요구",
                  f"한국거래소가 주가 급등과 관련해 {name}에 인수설이 사실인지 밝히라고 요구했습니다. "
                  f"회사는 {_when(state, s['day'])}까지 답해야 합니다.",
                  "조회공시는 소문으로 주가가 크게 움직일 때 거래소가 회사에 사실 확인을 요구하는 제도입니다. "
                  "답변 기한이 곧 결과가 나오는 때입니다. 그 전까지 나오는 [단서] 뉴스(인수 후보의 반응, "
                  "누가 사고 파는지, 내부자 매매)와 차트의 흐름을 모아 판단해 보세요. 단서 하나는 틀릴 수도 있습니다.",
                  factor="sentiment", tag="공시", codes=[code], issue=s["id"])


# ---------------------------------------------------------------- 매매
def execute_order(state, code, side, qty, fees):
    """시장가 주문을 지금 가격으로 체결하고 상태에 반영. 거부되면 ledger.OrderRejected"""
    if code not in STOCK:
        raise ledger.OrderRejected("UNKNOWN_STOCK", "없는 종목입니다.")
    if side not in ("BUY", "SELL"):
        raise ledger.OrderRejected("INVALID_SIDE", "매수 또는 매도를 고르세요.")
    if state["phase"] != "open":
        raise ledger.OrderRejected("MARKET_CLOSED", "장이 마감됐습니다. 다음 날 장이 열리면 주문하세요.")
    h = state["holdings"].get(code)
    pos = ledger.Position(h["qty"], h["avg"]) if h else None
    p = price(state, code)
    fill = (ledger.buy(state["cash"], pos, p, qty, fees) if side == "BUY"
            else ledger.sell(pos, p, qty, fees))
    state["cash"] += fill.cash_delta
    if fill.position:
        state["holdings"][code] = {"qty": fill.position.qty, "avg": fill.position.avg}
    else:
        state["holdings"].pop(code, None)
    state["trades"].append({"day": state["day"], "tick": state["tick"], "time": clock(state["tick"]),
                            "code": code, "side": side, "qty": qty, "price": p, "fee": fill.fee,
                            "tax": fill.tax, "profit": fill.profit})
    return fill


def trade(state, code, side, qty, fee_rate, tax_rate):
    """화면용 래퍼: (성공 여부, 메시지)"""
    try:
        fill = execute_order(state, code, side, qty, ledger.FeeSchedule.of(fee_rate, tax_rate))
    except ledger.OrderRejected as e:
        return False, e.message
    name = STOCK[code]["name"]
    if fill.side == "BUY":
        return True, f"{name} {qty:,}주를 {fill.price:,}원에 매수했습니다."
    return True, f"{name} {qty:,}주를 {fill.price:,}원에 매도했습니다. 실현손익 {fill.profit:+,}원"


# ---------------------------------------------------------------- 조회
def price(state, code):
    return state["intra"][code][-1]


def prev_close(state, code):
    """오늘 등락 기준가 (장중엔 어제 종가, 마감 후엔 그 전날 종가)"""
    return state["prices"][code][-1 if state["phase"] == "open" else -2]


def total_value(state):
    return state["cash"] + sum(price(state, c) * h["qty"] for c, h in state["holdings"].items())


def index_value(state, prev=False):
    """가상 종합지수 (게임 시작 전날 = 1,000)"""
    i0 = PRE_DAYS - 1
    get = prev_close if prev else price
    vals = [get(state, c) / state["prices"][c][i0] for c in STOCK]
    return 1000 * sum(vals) / len(vals)


def upcoming(state):
    """화면에 보여도 되는 예정 일정 (결과는 숨김)"""
    out = []
    for s in sorted(state["scheduled"], key=lambda s: s["day"]):
        if s.get("hidden") or (s["type"] == "rate" and not s.get("noticed")):
            continue
        u = {k: s[k] for k in ("day", "type", "code", "exp", "p", "dir") if k in s}
        if "truth" in s:                # 결과(truth)·발표 시각·단서 일정은 빼고 묶음 번호만
            u["issue"] = s["id"]
        out.append(u)
    return out


def snapshot(state, since=0, full=False):
    """브라우저로 보낼 상태. 앞으로 일어날 사건(plan, effects)은 절대 넣지 않음"""
    out = {
        "day": state["day"], "max_day": state["max_day"], "tick": state["tick"], "ticks": TICKS,
        "clock": clock(state["tick"]), "phase": state["phase"], "finished": state["finished"],
        "cash": state["cash"], "start_cash": state["start_cash"], "total": total_value(state),
        "holdings": state["holdings"],
        "quotes": {c: {"cur": price(state, c), "prev": prev_close(state, c)} for c in STOCK},
        "ind": state["ind"], "ind_open": state["ind_open"],
        "index": index_value(state), "index_prev": index_value(state, prev=True),
        "news": [n for n in state["news"] if n["id"] > since],
        "upcoming": upcoming(state), "report": state["report"],
        "open_total": state.get("open_total", state["start_cash"]),
    }
    if full:
        out["intra"] = state["intra"]
        out["stocks"] = {c: {"name": s["name"], "sector": s["sector"], "beta": s["beta"],
                             "hq": HQ[c][0]} for c, s in STOCK.items()}
        out["clocks"] = [clock(t) for t in range(TICKS + 1)]
    return out


def day_label(i):
    """가격 목록 인덱스 → 'D-3', 'D0', 'D12'"""
    return f"D{i - (PRE_DAYS - 1)}"
