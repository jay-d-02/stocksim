"""투자 성향 분석 테스트: 서로 다른 가상 플레이어가 서로 다른 성향으로 판정되는지"""
from simulator import analysis as A
from simulator import engine as E
from simulator.ledger import FeeSchedule, OrderRejected

FEES = FeeSchedule.of(0.00015, 0.002)


def play(bot, seed):
    st = E.new_game(10_000_000, seed, 30)
    seen = 0
    while True:
        while st["phase"] == "open":
            E.step_tick(st)
            fresh = [n for n in st["news"] if n["id"] > seen]
            seen = st["seq"]
            bot(st, fresh)
        if st["finished"]:
            return st
        E.begin_day(st)


def chaser(st, fresh):
    """뉴스 추종 단타: 기업 호재·루머가 뜨면 전부 팔고 그 종목에 몰빵"""
    for n in fresh:
        if n["codes"] and n["kind"] == "event":
            for c in list(st["holdings"]):
                E.execute_order(st, c, "SELL", st["holdings"][c]["qty"], FEES)
            if any(w in n["title"] for w in ("수주", "흥행", "인수", "자사주", "성공")):
                c = n["codes"][0]
                q = int(st["cash"] // (E.price(st, c) * 1.001))
                if q:
                    E.execute_order(st, c, "BUY", q, FEES)


def holder(st, fresh):
    """분산 장기: 첫 5분에 6개 업종을 나눠 사고 끝까지 보유"""
    if st["day"] == 1 and st["tick"] == 1:
        for c in ["HGF", "ONT", "GHF", "NRM", "HBS", "DYO"]:
            E.execute_order(st, c, "BUY", int(st["cash"] * 0.12 // E.price(st, c)), FEES)


def contrarian(st, fresh):
    """나쁜 단서가 뜰 때마다 그 종목을 조금 삼 (경고 무시)"""
    for n in fresh:
        if n["tag"] == "단서":
            iss = st["issues"][str(n["issue"])]
            if sum(s for *_, s in iss["clues"]) < 0:
                E.execute_order(st, n["codes"][0], "BUY", 1, FEES)


def clue_reader(st, fresh):
    """단서 합계가 좋으면 사고 나쁘면 판다"""
    for n in fresh:
        if n["tag"] == "단서":
            c, net = n["codes"][0], sum(s for *_, s in st["issues"][str(n["issue"])]["clues"])
            if net > 0:
                E.execute_order(st, c, "BUY", 1, FEES)
            elif c in st["holdings"]:
                E.execute_order(st, c, "SELL", st["holdings"][c]["qty"], FEES)


def test_ignoring_negative_clues_is_coached():
    a = A.analyze(play(contrarian, 11))
    assert a["clue_ignored"] >= 2
    assert any(c["tip"] == "tip-06" and "단서" in c["title"] for c in a["coach"] + A.analyze(play(contrarian, 11), "expert")["coach"])


def test_reading_clues_is_praised():
    a = A.analyze(play(clue_reader, 11))
    assert a["clue_heeded"] >= 2 and a["clue_ignored"] == 0
    assert any("단서" in p for p in a["praise"])


def test_not_ready_without_trades():
    a = A.analyze(E.new_game(10_000_000, 1))
    assert not a["ready"] and a["n_trades"] == 0


def test_chaser_vs_holder():
    c = A.analyze(play(chaser, 11))
    h = A.analyze(play(holder, 11))
    assert h["persona"][1] == "신중한 장기 보유자"
    assert ("🧺", "분산 투자") in h["traits"]
    axes = lambda a: {x["key"]: x["v"] for x in a["axes"]}
    assert axes(c)["news"] > 70 > axes(h)["news"]
    assert axes(c)["activity"] > axes(h)["activity"]
    assert axes(c)["focus"] > axes(h)["focus"]
    assert c["chase_share"] >= 0.5                       # 뉴스 뒤 이미 오른 가격에 삼
    titles = [x["title"] for x in c["coach"]]
    assert any("이미 가격에 반영" in t for t in titles)


def test_disposition_effect_detected():
    """이익 난 종목은 빨리, 손실 난 종목은 오래 들고 있으면 '손절이 느림'"""
    st = E.new_game(10_000_000, 2)
    trades = []
    for i, (hold, profit) in enumerate([(2, 5000), (3, 4000), (40, -7000), (50, -9000)]):
        trades.append({"day": 1, "tick": 0, "time": "09:00", "code": "HGF", "side": "BUY",
                       "qty": 1, "price": 50_000, "fee": 7, "tax": 0, "profit": None})
        trades.append({"day": 1, "tick": hold, "time": "", "code": "HGF", "side": "SELL",
                       "qty": 1, "price": 50_000, "fee": 7, "tax": 100, "profit": profit})
    trades.sort(key=lambda t: t["tick"])
    st["trades"] = trades
    a = A.analyze(st)
    assert a["disposition"]
    assert ("😬", "손절이 느림") in a["traits"]
    assert a["coach"][0]["tip"] == "tip-10"


def test_coaching_by_level():
    """같은 기록이라도 초보는 쉬운 말로 적게(+해 볼 행동), 고수는 수치·용어로 많이"""
    st = play(chaser, 11)
    b, i, e = (A.analyze(st, lv) for lv in ("beginner", "intermediate", "expert"))
    assert len(b["coach"]) <= 2 < len(e["coach"]) and len(i["coach"]) == 3
    assert all(c["try"] for c in b["coach"]) and not any(c["try"] for c in i["coach"] + e["coach"])
    assert b["coach"][0]["tip"] == i["coach"][0]["tip"] == e["coach"][0]["tip"]   # 같은 습관을
    assert len({b["coach"][0]["title"], i["coach"][0]["title"], e["coach"][0]["title"]}) == 3   # 다르게 말함
    assert (b["axes"], b["ret"]) == (e["axes"], e["ret"])                         # 숫자 분석은 같음
    assert A.analyze(st, "nonsense")["level"] == "intermediate"


def test_fmt_hold():
    assert A.fmt_hold(45) == "45분"
    assert A.fmt_hold(125) == "2시간 5분"
    assert A.fmt_hold(E.TICKS * E.TICK_MIN * 2.5) == "2.5거래일"
    assert A.fmt_hold(None) == "-"


def test_summary_is_json_friendly():
    import json
    s = A.summary(A.analyze(play(holder, 3)))
    assert json.loads(json.dumps(s, ensure_ascii=False))["persona"]
