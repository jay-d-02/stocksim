"""시장 엔진 테스트: 재현성, 미래 정보 비공개, 가격 규칙, 주문 반영"""
import json

import pytest

from simulator import engine as E
from simulator.ledger import FeeSchedule, OrderRejected

FEES = FeeSchedule.of(0.00015, 0.002)
START = 10_000_000


def run_day(st):
    while st["phase"] == "open":
        E.step_tick(st)


def play(seed, days=30, length=30):
    st = E.new_game(START, seed, length)
    for d in range(days):
        run_day(st)
        if st["finished"] or d == days - 1:
            break
        E.begin_day(st)
    return st


def test_same_seed_same_market():
    a, b = play(42, days=3), play(42, days=3)
    assert a["prices"] == b["prices"]
    assert [n["title"] for n in a["news"]] == [n["title"] for n in b["news"]]


def test_different_seed_different_market():
    assert play(1, days=2)["prices"] != play(2, days=2)["prices"]


def test_snapshot_hides_future():
    """오늘 일어날 사건 계획·남은 효과·분석용 기록은 브라우저로 나가면 안 됨"""
    st = E.new_game(START, 7)
    for _ in range(20):
        E.step_tick(st)
    assert st["plan"] or st["effects"]                  # 숨길 게 실제로 있는 상태에서 확인
    snap = E.snapshot(st, full=True)
    for key in ("plan", "effects", "nmeta", "scheduled", "px", "seed", "recent", "expo", "issues"):
        assert key not in snap
    text = json.dumps(snap, ensure_ascii=False)
    assert '"per"' not in text and '"sid"' not in text


def test_upcoming_hides_rumor_and_unannounced_rate():
    """예전(v2) 게임의 인수설은 예정 일정에 안 보임"""
    st = E.new_game(START, 3)
    st["scheduled"].append({"id": 999, "day": 2, "type": "rumor", "code": "HBS", "hidden": True})
    types = [u["type"] for u in E.upcoming(st)]
    assert "rumor" not in types
    assert all(u["day"] - st["day"] <= 2 for u in E.upcoming(st) if u["type"] == "rate")


def play_until_issues(seed, n=3):
    st = E.new_game(START, seed)
    while len(st.get("issues", {})) < n and not st["finished"]:
        run_day(st)
        E.begin_day(st)
    return st


def test_issue_result_decided_early_but_never_sent():
    """결과·발표 시각·단서 일정은 예고 때 정해지지만 브라우저로는 묶음 번호만 나감"""
    st = play_until_issues(5)
    pending = [s for s in st["scheduled"] if "truth" in s]
    assert pending
    snap = E.snapshot(st, full=True)
    ups = {u["issue"]: u for u in snap["upcoming"] if "issue" in u}
    for s in pending:
        assert set(ups[s["id"]]) <= {"day", "type", "code", "exp", "issue"}     # truth·tick·clues 없음
    text = json.dumps(snap, ensure_ascii=False)
    assert '"truth"' not in text and '"clues"' not in text and '"ch"' not in text


def test_clues_come_before_result_and_result_follows_truth():
    st = play(13)
    news = {n["id"]: n for n in st["news"]}
    done = [i for i in st["issues"].values() if i["end"]]
    assert len(done) >= 3
    lo = {"rumor": 2, "earn": 1, "trial": 1}
    for iss in done:
        assert lo[iss["type"]] <= len(iss["clues"]) <= 3
        assert all((d, t) < tuple(iss["end"]) for d, t, _ in iss["clues"])      # 단서는 발표 전에만
    words = {"rumor": ("소문이 사실로", "사실무근"), "earn": ("웃돌아", "밑돌아"), "trial": ("성공", "실패")}
    for sid, iss in st["issues"].items():
        res = [n for n in news.values() if n.get("issue") == int(sid) and n["tag"] in ("공시", "실적", "바이오")
               and "조회공시" not in n["title"]]
        if iss["end"] and res:
            good, bad = words[iss["type"]]
            assert (good if iss["truth"] else bad) in res[-1]["title"]


def test_rumor_gets_disclosure_deadline_and_shows_in_upcoming():
    st = E.new_game(START, 1, 30)
    while not any(n["tag"] == "루머" for n in st["news"]) and not st["finished"]:
        run_day(st)
        E.begin_day(st)
    rumor = next(n for n in st["news"] if n["tag"] == "루머")
    notice = next(n for n in st["news"] if "조회공시" in n["title"])
    assert notice["issue"] == rumor["issue"] and notice["id"] == rumor["id"] + 1
    s = next((s for s in st["scheduled"] if s["id"] == rumor["issue"]), None)
    if s:                                               # 아직 발표 전이면 예정 일정에 보임
        assert any(u.get("issue") == s["id"] and u["type"] == "rumor" for u in E.upcoming(st))


def test_clues_point_to_truth_more_often_than_not():
    right = total = 0
    for seed in range(20):
        for iss in play(seed, days=15).get("issues", {}).values():
            for *_, sig in iss["clues"]:
                total += 1
                right += (sig > 0) == iss["truth"]
    assert total > 50 and 0.55 < right / total < 0.8     # 대체로 맞지만 틀린 단서도 섞임


def test_v2_game_keeps_old_rules():
    """버전 2 상태로 진행하면 단서·이슈가 생기지 않음 (진행 중이던 예전 게임을 복구해도 같은 시장)"""
    st = E.new_game(START, 13)
    st["v"] = 2
    for _ in range(8):
        run_day(st)
        E.begin_day(st)
    assert not st.get("issues")
    assert not any(n["tag"] == "단서" or "issue" in n for n in st["news"])
    assert all(s.get("hidden") for s in st["scheduled"] if s["type"] == "rumor")


def test_daily_price_limit():
    st = play(5, days=10)
    for code in E.STOCK:
        closes = st["prices"][code]
        for prev, cur in zip(closes[E.PRE_DAYS - 1:], closes[E.PRE_DAYS:]):
            assert abs(cur / prev - 1) <= E.LIMIT / 100 + 0.01    # 호가 반올림 여유


@pytest.mark.parametrize("price,unit", [(1_999, 1), (4_995, 5), (19_990, 10), (49_950, 50),
                                        (199_900, 100), (499_500, 500), (512_000, 1000)])
def test_tick_size(price, unit):
    assert E.tick(price) % unit == 0
    assert E.tick(price + unit * 0.4) == price          # 가장 가까운 호가로


def test_all_prices_follow_tick_size():
    st = play(8, days=2)
    for code in E.STOCK:
        for p in st["prices"][code] + st["intra"][code]:
            assert E.tick(p) == p


@pytest.mark.parametrize("length", E.GAME_DAYS)
def test_full_game_finishes(length):
    st = play(11, length, length)
    assert st["finished"] and st["day"] == st["max_day"] == length
    assert all(len(st["prices"][c]) == E.PRE_DAYS + length for c in E.STOCK)
    assert all(s["day"] > length for s in st["scheduled"])        # 예고된 일정은 다 발표됨
    assert len(st["equity"]) == length
    rates = [s["day"] for s in E.new_game(START, 11, length)["scheduled"] if s["type"] == "rate"]
    assert rates == [d for d in E.RATE_DAYS if d <= length]       # 게임 안에 드는 금리 결정만 (15일 → 5, 12)


def test_unknown_length_rejected():
    with pytest.raises(ValueError):
        E.new_game(START, 1, 7)


def test_order_updates_state():
    st = E.new_game(START, 9)
    E.step_tick(st)
    p = E.price(st, "HGF")
    b = E.execute_order(st, "HGF", "BUY", 10, FEES)
    assert st["cash"] == START + b.cash_delta
    assert st["holdings"]["HGF"] == {"qty": 10, "avg": float(p)}
    s = E.execute_order(st, "HGF", "SELL", 10, FEES)
    assert "HGF" not in st["holdings"]
    assert st["cash"] == START + b.cash_delta + s.cash_delta
    assert [t["side"] for t in st["trades"]] == ["BUY", "SELL"]


def test_order_rejected_when_market_closed():
    st = E.new_game(START, 9)
    run_day(st)
    with pytest.raises(OrderRejected) as e:
        E.execute_order(st, "HGF", "BUY", 1, FEES)
    assert e.value.code == "MARKET_CLOSED"
    assert st["trades"] == []                           # 거부된 주문은 원장에 안 남음


def test_order_rejects_unknown_stock_and_side():
    st = E.new_game(START, 9)
    with pytest.raises(OrderRejected) as e:
        E.execute_order(st, "NOPE", "BUY", 1, FEES)
    assert e.value.code == "UNKNOWN_STOCK"
    with pytest.raises(OrderRejected) as e:
        E.execute_order(st, "HGF", "HOLD", 1, FEES)
    assert e.value.code == "INVALID_SIDE"


def test_cash_ledger_balances_over_a_game():
    """게임 내내 사고팔아도 예수금 = 시작금 − 매수 총액·수수료 + 매도 정산금"""
    st = E.new_game(START, 21)
    for day in range(5):
        for t in range(E.TICKS):
            E.step_tick(st)
            if t % 13 == 0:
                c = list(E.STOCK)[(day * 7 + t) % 12]
                try:
                    E.execute_order(st, c, "BUY", 5, FEES)
                except OrderRejected:
                    pass
            if t % 29 == 0 and st["holdings"]:
                c = next(iter(st["holdings"]))
                E.execute_order(st, c, "SELL", st["holdings"][c]["qty"], FEES)
        E.begin_day(st)
    expect = START
    for t in st["trades"]:
        amount = t["price"] * t["qty"]
        expect += -(amount + t["fee"]) if t["side"] == "BUY" else amount - t["fee"] - t["tax"]
    assert st["trades"] and st["cash"] == expect
    for c, h in st["holdings"].items():                 # 보유 수량 = 매수 합 − 매도 합
        net = sum(t["qty"] * (1 if t["side"] == "BUY" else -1) for t in st["trades"] if t["code"] == c)
        assert h["qty"] == net


def test_state_is_json_serializable():
    st = play(4, days=2)
    assert json.loads(json.dumps(st)) == st
