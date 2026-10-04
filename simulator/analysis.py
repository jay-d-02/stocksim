"""투자 게임 기록으로 투자 성향 분석

입력은 engine의 게임 상태(dict) 하나. 거래 기록(trades), 5분마다 누적한 자산 구성(expo),
매일 마감 자산(equity), 하루 고가·저가(hl), 뉴스별 영향 기록(nmeta)을 맞춰 보고
습관과 성향을 계산한다.
"""
from . import engine as gm

MIN_TRADES = 3            # 이보다 적으면 성향 판정을 하지 않음
LEVELS = ("beginner", "intermediate", "expert")               # 회원의 투자 경험 (users.experience)
COACH_LIMIT = {"beginner": 2, "intermediate": 3, "expert": 5}  # 코칭을 몇 개까지 보여 줄지
REACT_TICKS = 6           # 뉴스 뒤 30분 안의 거래 = 뉴스에 반응한 거래
DAY_KEY = gm.TICKS + 1


def _key(day, tick):
    """(날, 틱) → 정렬 가능한 시각 값. 하루 = 79칸 (밤사이는 1칸으로 침)"""
    return day * DAY_KEY + tick


def _interp(x, pts):
    """구간별 직선 보간으로 0~100 점수화"""
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def _sign(v):
    return (v > 0) - (v < 0)


def fmt_hold(minutes):
    """거래 시간(분) → '2시간 10분' 또는 '3.5거래일'"""
    if minutes is None:
        return "-"
    day = gm.TICKS * gm.TICK_MIN
    if minutes < day:
        h, m = divmod(int(round(minutes)), 60)
        return f"{h}시간 {m}분" if h else f"{m}분"
    return f"{minutes / day:.1f}거래일"


# ---------------------------------------------------------------- 분석
def analyze(state, level="intermediate"):
    """level: 투자 경험. 코칭 문구의 눈높이와 개수가 달라진다 (숫자 분석은 같음)"""
    if level not in LEVELS:
        level = "intermediate"
    trades = state["trades"]
    out = {"n_trades": len(trades), "day": state["day"], "done_days": len(state.get("equity", [])),
           "finished": state["finished"], "ready": len(trades) >= MIN_TRADES, "level": level}
    out.update(_performance(state))
    out.update(_trade_habits(state))
    out.update(_allocation(state))
    out.update(_news_habits(state))
    out.update(_clue_habits(state))
    out.update(_timing(state))
    out.update(_profile(out))
    out["coach"], out["praise"] = _coach(out, level)
    return out


def _performance(state):
    start, total, eq = state["start_cash"], gm.total_value(state), state.get("equity", [])
    ret, mkt = (total / start - 1) * 100, gm.index_value(state) / 10 - 100
    curve = [{"day": 0, "me": 0.0, "mkt": 0.0}] + [
        {"day": e["day"], "me": (e["total"] / start - 1) * 100, "mkt": e["index"] / 10 - 100} for e in eq]
    peak, mdd = start, 0.0
    for v in [start] + [e["total"] for e in eq] + [total]:
        peak = max(peak, v)
        mdd = min(mdd, (v / peak - 1) * 100)
    return {"ret": ret, "mkt": mkt, "excess": ret - mkt, "curve": curve, "mdd": mdd}


def _trade_habits(state):
    """먼저 산 것부터 판다고 보고 매수·매도를 짝지어 보유 시간과 손익을 구함"""
    trades, now = state["trades"], _key(state["day"], state["tick"])
    lots, sells, open_holds = {}, [], []
    for t in trades:
        k = _key(t["day"], t.get("tick", 0))
        if t["side"] == "BUY":
            lots.setdefault(t["code"], []).append([t["qty"], t["price"], k])
            continue
        q, held, cost = t["qty"], 0, 0
        while q and lots.get(t["code"]):
            lot = lots[t["code"]][0]
            m = min(q, lot[0])
            held += m * (k - lot[2]); cost += m * lot[1]
            lot[0] -= m; q -= m
            if lot[0] == 0:
                lots[t["code"]].pop(0)
        sells.append({"code": t["code"], "name": gm.STOCK[t["code"]]["name"], "day": t["day"],
                      "time": t["time"], "qty": t["qty"], "profit": t["profit"] or 0,
                      "pct": (t["profit"] or 0) / cost * 100 if cost else 0,
                      "hold": held / t["qty"] * gm.TICK_MIN})
    for ls in lots.values():
        open_holds += [(now - k) * gm.TICK_MIN for _, _, k in ls]
    wins = [s for s in sells if s["profit"] > 0]
    losses = [s for s in sells if s["profit"] <= 0]
    fees = sum(t["fee"] + t["tax"] for t in trades)
    days = len(state.get("equity", [])) or state["day"]
    avg_win, avg_loss = _mean(s["pct"] for s in wins), _mean(s["pct"] for s in losses)
    return {
        "n_sells": len(sells), "n_wins": len(wins), "n_losses": len(losses),
        "win_rate": len(wins) / len(sells) if sells else None,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "payoff": avg_win / -avg_loss if avg_win is not None and avg_loss else None,   # 손익비 = 평균 이익률 / 평균 손실률
        "hold_win": _mean(s["hold"] for s in wins), "hold_loss": _mean(s["hold"] for s in losses),
        "hold_all": _mean([s["hold"] for s in sells] + open_holds),
        "realized": sum(s["profit"] for s in sells), "fees": fees,
        "fee_pct": fees / state["start_cash"] * 100,
        "best": max(sells, key=lambda s: s["profit"]) if sells else None,
        "worst": min(sells, key=lambda s: s["profit"]) if sells else None,
        "tpd": len(trades) / max(1, days),
    }


def _allocation(state):
    """장중 5분마다 누적한 평균 (예전 게임은 매일 마감 기록으로 대신)"""
    ex = state.get("expo")
    if ex and ex["n"]:
        n = ex["n"]
        return {"cash_ratio": ex["cash"] / n, "beta": ex["beta"] / n,
                "top_share": ex["top"] / ex["inv_n"] if ex["inv_n"] else None,
                "n_hold": ex["hold"] / ex["inv_n"] if ex["inv_n"] else None,
                "sectors": sorted(((s, v / n * 100) for s, v in ex["sec"].items() if v / n > 1e-5),
                                  key=lambda x: -x[1])}
    pts = [(e["total"], e["cash"], e["alloc"]) for e in state.get("equity", [])]
    pts.append((gm.total_value(state), state["cash"],
                {c: gm.price(state, c) * h["qty"] for c, h in state["holdings"].items()}))
    cash_r, top_r, n_hold, beta, sec = [], [], [], [], {}
    for tot, cash, alloc in pts:
        inv = sum(alloc.values())
        cash_r.append(cash / tot)
        beta.append(sum(v * gm.STOCK[c]["beta"] for c, v in alloc.items()) / tot)
        for c, v in alloc.items():
            s = gm.STOCK[c]["sector"]
            sec[s] = sec.get(s, 0) + v / tot / len(pts)
        if inv > 0:
            top_r.append(max(alloc.values()) / inv)
            n_hold.append(len(alloc))
    return {"cash_ratio": _mean(cash_r), "top_share": _mean(top_r), "n_hold": _mean(n_hold),
            "beta": _mean(beta),
            "sectors": sorted(((s, v * 100) for s, v in sec.items() if v > 1e-5), key=lambda x: -x[1])}


def _news_key(n):
    """뉴스 시각('10:35', 아침 브리핑은 '08:30') → 시각 값"""
    h, m = map(int, n["time"].split(":"))
    return _key(n["day"], max(0, (h * 60 + m - 9 * 60) // gm.TICK_MIN))


def _preview_windows(state):
    """{종목: [(예고 시각, 결과 발표 시각)]} — 이 사이에 한 거래 = 예고를 보고 미리 움직인 거래"""
    out, ns = {}, state["news"]
    for j, n in enumerate(ns):
        if n["kind"] != "preview" or not n["codes"]:
            continue
        c, start = n["codes"][0], _news_key(n)
        res = next((_news_key(r) for r in ns[j + 1:] if c in r["codes"] and r["kind"] == "event"
                    and r["tag"] in ("실적", "바이오")), None)
        out.setdefault(c, []).append((start, res if res is not None else start + 3 * DAY_KEY))
    return out


def _news_habits(state):
    trades, news = state["trades"], {str(n["id"]): n for n in state["news"]}
    events = sorted((_key(news[i]["day"], m["t"]), i, m)
                    for i, m in state.get("nmeta", {}).items() if i in news)
    windows = _preview_windows(state)
    reactive = follow = chase = rumor = planned = react_buys = 0
    for t in trades:
        k, c, buy = _key(t["day"], t.get("tick", 0)), t["code"], t["side"] == "BUY"
        # 30분 안에 이 종목을 크게 움직인 사건이 있었나
        near = [(ek, i, m) for ek, i, m in events if c in m["imp"] and 0 <= k - ek <= REACT_TICKS
                and abs(m["imp"][c]) >= 2 and news[i]["kind"] == "event"]
        if near:
            _, i, m = near[-1]
            d = _sign(m["imp"][c])
            reactive += 1
            follow += (buy and d > 0) or (not buy and d < 0)
            if buy and d > 0:
                react_buys += 1
                # 예상 움직임의 절반 이상 이미 오른 뒤에 샀으면 '뒤늦은 추격'
                chase += t["price"] >= m["px"][c] * (1 + m["imp"][c] * 0.5 / 100)
        if buy and any(news[i]["tag"] == "루머" and c in news[i]["codes"] and 0 <= k - ek <= 2 * DAY_KEY
                       for ek, i, m in events):
            rumor += 1
        if any(a <= k < b for a, b in windows.get(c, ())):
            planned += 1

    # 성공/실패로 갈리는 사건(임상 결과) 순간 바이오에 건 비중
    binary, eq = 0.0, state.get("equity", [])
    for ek, i, m in events:
        n = news[i]
        if n["tag"] != "바이오" or n["kind"] != "event":
            continue
        for c in n["codes"]:
            qty = sum(t["qty"] * (1 if t["side"] == "BUY" else -1) for t in trades
                      if t["code"] == c and _key(t["day"], t.get("tick", 0)) < ek)
            prev = next((e["total"] for e in reversed(eq) if e["day"] < n["day"]), state["start_cash"])
            binary = max(binary, qty * m["px"][c] / prev)
    return {"reactive": reactive, "react_share": reactive / len(trades) if trades else 0,
            "follow_share": follow / reactive if reactive else None,
            "react_buys": react_buys, "chase": chase,
            "chase_share": chase / react_buys if react_buys else None,
            "rumor": rumor, "planned": planned, "binary": binary}


def _clue_habits(state):
    """단서가 나온 사건(인수설·실적·임상, v3 게임)의 예고~발표 사이 매매를 그때까지 나온 단서 합계와 맞춰 봄
    ignored: 나쁜 단서가 더 많은데 산 것 / heeded: 단서 방향대로 사거나 팔았고 결과도 그쪽으로 난 것"""
    ignored = heeded = 0
    for iss in state.get("issues", {}).values():
        if not iss["end"]:
            continue
        a, b = _key(*iss["start"]), _key(*iss["end"])
        clues = [(_key(d, t), sig) for d, t, sig in iss["clues"]]
        for t in state["trades"]:
            k = _key(t["day"], t.get("tick", 0))
            if t["code"] != iss["code"] or not a <= k < b:
                continue
            net = sum(sig for ck, sig in clues if ck <= k)
            buy = t["side"] == "BUY"
            if buy and net < 0:
                ignored += 1
            elif (buy and net > 0 and iss["truth"]) or (not buy and net < 0 and not iss["truth"]):
                heeded += 1
    return {"clue_ignored": ignored, "clue_heeded": heeded}


def _timing(state):
    """그날 가격 범위에서 어디쯤에서 샀나 (0 = 그날 최저가, 1 = 최고가). 마감된 날만"""
    hl, bpos, spos, bdays = state.get("hl", {}), [], [], set()
    for t in state["trades"]:
        day_hl = hl.get(t["code"], [])
        if t["day"] - 1 < len(day_hl):
            hi, lo = day_hl[t["day"] - 1]
            if hi > lo:
                pos = (t["price"] - lo) / (hi - lo)
                if t["side"] == "BUY":
                    bpos.append(pos); bdays.add(t["day"])
                else:
                    spos.append(pos)
    return {"buy_pos": _mean(bpos), "sell_pos": _mean(spos), "buy_days": len(bdays)}


def _profile(o):
    axes = [
        {"key": "activity", "name": "매매 빈도", "lo": "느긋한 보유", "hi": "활발한 매매",
         "v": _interp(o["tpd"], [(0, 0), (0.5, 30), (1, 50), (2, 72), (4, 90), (8, 100)]),
         "note": f"하루 평균 {o['tpd']:.1f}회 거래"},
        {"key": "risk", "name": "위험 선호", "lo": "안정 추구", "hi": "공격적",
         "v": _interp(o["beta"] or 0, [(0, 0), (0.3, 25), (0.6, 50), (0.9, 75), (1.2, 100)]),
         "note": f"계좌 전체 베타 {o['beta'] or 0:.2f} (현금 포함)"},
        {"key": "focus", "name": "집중도", "lo": "분산", "hi": "집중",
         "v": _interp(o["top_share"] or 0, [(0, 0), (0.25, 10), (0.4, 35), (0.6, 60), (0.8, 80), (1, 100)]),
         "note": f"주식 중 가장 큰 종목 비중 평균 {(o['top_share'] or 0) * 100:.0f}% (주식 비중 {(1 - (o['cash_ratio'] or 0)) * 100:.0f}%)"},
        {"key": "news", "name": "뉴스 민감도", "lo": "내 판단대로", "hi": "뉴스에 즉각 반응",
         "v": _interp(o["react_share"], [(0, 0), (0.2, 35), (0.4, 60), (0.7, 90), (1, 100)]),
         "note": f"거래의 {o['react_share'] * 100:.0f}%가 관련 뉴스 30분 안"},
    ]
    a = {x["key"]: x["v"] for x in axes}
    persona = {
        (True, True): ("⚡", "모멘텀 트레이더", "빠르게 사고팔며 큰 움직임을 노립니다. 기회를 잘 잡지만 비용과 실수도 빨리 쌓입니다."),
        (True, False): ("🔁", "부지런한 조율자", "자주 비중을 조절하지만 위험은 조심스럽게 관리합니다. 작은 수익을 꾸준히 쌓는 편입니다."),
        (False, True): ("🎯", "확신형 투자자", "거래는 적지만 한 번 들어가면 크게 겁니다. 판단이 맞으면 크게 벌고, 틀리면 크게 흔들립니다."),
        (False, False): ("🐢", "신중한 장기 보유자", "서두르지 않고 위험을 낮게 유지합니다. 큰 손실은 피하지만 기회를 흘려보낼 때도 있습니다."),
    }[(a["activity"] >= 50, a["risk"] >= 55)]

    disp = bool(o["n_wins"] >= 2 and o["n_losses"] >= 2 and o["hold_loss"] > o["hold_win"] * 1.3)
    traits = []
    if o["follow_share"] is not None and o["reactive"] >= 2:
        if o["follow_share"] >= 0.6:
            traits.append(("📰", "뉴스 추종형"))
        elif o["follow_share"] <= 0.4:
            traits.append(("🧭", "역발상형"))
    invested = 1 - (o["cash_ratio"] or 0)          # 주식 비중이 작으면 '집중'은 의미가 없음
    if a["focus"] >= 70 and invested >= 0.3:
        traits.append(("🎯", "집중 투자"))
    elif (o["n_hold"] or 0) >= 4:
        traits.append(("🧺", "분산 투자"))
    if (o["cash_ratio"] or 0) >= 0.6:
        traits.append(("💰", "현금 선호"))
    if disp:
        traits.append(("😬", "손절이 느림"))
    if o["rumor"]:
        traits.append(("🗣", "소문에 민감"))
    if o["clue_heeded"] >= 3 and o["clue_heeded"] >= 2 * o["clue_ignored"]:
        traits.append(("🔎", "단서 분석가"))
    if o["win_rate"] is not None and o["n_sells"] >= 3 and o["win_rate"] >= 0.6:
        traits.append(("✅", "높은 승률"))
    if o["excess"] >= 3:
        traits.append(("🏆", "시장보다 잘함"))
    return {"axes": axes, "persona": persona, "traits": traits, "disposition": disp}


def _coach(o, level="intermediate"):
    """고칠 점 (심한 것부터 레벨별 개수만큼) + 잘한 점

    같은 습관이라도 투자 경험에 맞춰 다르게 말한다.
    beginner: 쉬운 말, 적게(2개), 다음 판에 해 볼 행동 하나(try)
    intermediate: 원인과 방법 (3개)
    expert: 수치·용어 그대로, 더 많이(5개), 규칙으로 만들 수 있는 제안
    """
    tips = []

    def add(prio, tip, beginner, intermediate, expert):
        title, body, *rest = {"beginner": beginner, "intermediate": intermediate, "expert": expert}[level]
        tips.append((prio, {"title": title, "body": body, "tip": tip, "try": rest[0] if rest else None}))

    pct = lambda v: f"{v * 100:.0f}%"
    if o["disposition"]:
        hw, hl = fmt_hold(o["hold_win"]), fmt_hold(o["hold_loss"])
        ratio = f"{o['hold_loss'] / o['hold_win']:.1f}배" if o["hold_win"] else "훨씬 김"
        payoff = f"손익비 {o['payoff']:.2f}" if o["payoff"] is not None else "손익비 계산 불가"
        add(9, "tip-10",
            ("손해 본 주식을 오래 붙잡고 있어요",
             f"오른 주식은 {hw} 만에 팔았는데, 떨어진 주식은 {hl} 동안 들고 있었어요. "
             "'조금만 기다리면 다시 오르겠지' 하는 마음은 누구나 들지만, 기다리는 동안 손해가 더 커지기 쉬워요.",
             "사기 전에 '몇 % 떨어지면 판다'를 먼저 정해 두세요. 예: -5%"),
            ("손실 난 종목을 더 오래 들고 있어요",
             f"이익 난 종목은 평균 {hw} 만에 팔았지만, 손실 난 종목은 {hl} 들고 있었습니다. "
             "'본전 오면 팔자'는 마음(처분 효과)은 손실을 키웁니다. 들어가기 전에 손절 기준을 정해 보세요."),
            (f"처분 효과: 손실 포지션 보유 기간 {ratio}",
             f"보유 기간 이익 {hw} / 손실 {hl}, 평균 수익 {o['avg_win']:+.1f}% / 손실 {o['avg_loss']:+.1f}% ({payoff}). "
             "진입 시점에 손절선을 기계적으로 걸고, 이익 쪽은 추적 손절로 늘려 손익비를 1 이상으로 끌어올리세요."))
    if o["rumor"]:
        n = o["rumor"]
        add(8, "tip-06",
            (f"소문만 듣고 {n}번 샀어요",
             "'OO가 인수한대' 같은 소문은 공식 발표가 아니에요. 사실이 아닌 경우가 더 많아서, 믿고 샀다가 손해 보기 쉬워요.",
             "뉴스 태그가 '루머'면 일단 지켜보고, '공시'가 뜬 뒤에 판단해 보세요."),
            (f"소문을 보고 {n}번 샀어요",
             "공식 발표가 아닌 인수설은 사실무근으로 끝날 때가 더 많습니다. 공시가 나온 뒤에 움직여도 늦지 않습니다."),
            (f"루머 기반 매수 {n}건",
             "미확인 인수설은 부인 공시로 되돌려지는 경우가 많아 기대값이 음수에 가깝습니다. "
             "거래한다면 확인 공시 전까지 자산의 5% 이내로 제한하고, 부인 공시가 나오면 바로 청산하는 규칙을 두세요."))
    if o["clue_ignored"]:
        n = o["clue_ignored"]
        add(8, "tip-06",
            ("나쁜 단서가 나왔는데도 샀어요",
             f"결과 발표 전에 '[단서]' 뉴스가 나쁜 쪽을 더 많이 가리키고 있었는데 {n}번 샀어요. "
             "단서는 결과를 미리 엿볼 수 있는 힌트예요.",
             "알림 창 '예정된 일정' 아래에 모인 단서를 세어 보고, 나쁜 단서가 더 많으면 사지 말아 보세요."),
            (f"경고 단서를 무시하고 {n}번 샀어요",
             "결과 발표 전에 나온 단서가 나쁜 쪽이 더 많았는데 샀습니다. 단서 하나는 틀릴 수 있지만, "
             "여러 개가 같은 쪽을 가리키면 결과도 그쪽일 가능성이 높습니다."),
            (f"부정 단서 우세 구간 매수 {n}건",
             "단서는 채널마다 따로 나오는 신호라 같은 방향이 겹칠수록 확률이 빠르게 쏠립니다 "
             "(인수설 단서 3개가 모두 부정적이면 사실일 확률은 한 자릿수). 단서 합계가 음수인 구간의 신규 매수는 기대값이 음수입니다."))
    if o["binary"] >= 0.3:
        b = pct(o["binary"])
        add(8, "tip-05",
            ("결과를 모르는 발표에 돈을 너무 많이 걸었어요",
             f"임상 결과처럼 '성공 아니면 실패'인 발표 때 자산의 {b}를 바이오 주식에 넣었어요. "
             "실패하면 하루 만에 크게 떨어질 수 있어서 동전 던지기와 비슷해요.",
             "결과 발표 전에는 전체 돈의 10%까지만 넣어 보세요."),
            (f"임상 결과 발표 때 자산의 {b}를 바이오에 걸었어요",
             "성공 아니면 실패인 사건에 큰 비중을 거는 건 도박에 가깝습니다. 결과 전에는 비중을 10% 이하로 줄여 보세요."),
            (f"이진 이벤트 노출 {b}",
             "임상 결과는 급등 아니면 급락으로 갈려 분산이 매우 큽니다. "
             "발표 직전 비중을 10% 이하로 줄이거나, 결과가 공개되고 방향을 확인한 뒤 들어가 갭 위험을 피하세요."))
    if o["chase_share"] is not None and o["chase_share"] >= 0.5 and o["chase"] >= 2:
        rb, c = o["react_buys"], o["chase"]
        add(7, "tip-01",
            ("좋은 뉴스를 보고 사면 이미 늦을 때가 많아요",
             f"뉴스 보고 산 {rb}번 중 {c}번은 가격이 이미 많이 오른 뒤였어요. "
             "뉴스가 뜨는 순간 다른 사람들도 같이 사기 때문에 가격이 먼저 올라 버려요.",
             "'이틀 뒤 실적 발표' 같은 예고 뉴스를 보고 미리 준비해 보세요."),
            ("호재가 이미 가격에 반영된 뒤에 사는 경우가 많아요",
             f"뉴스를 보고 산 {rb}번 중 {c}번은 예상 상승폭의 절반 이상 오른 뒤였습니다. "
             "뉴스가 뜬 순간 가격은 이미 움직입니다. 예고된 일정을 미리 준비하는 쪽이 유리합니다."),
            (f"추격 매수 {c}/{rb}건 (예상 반응의 50% 이상 반영 후 진입)",
             "첫 반응은 뉴스가 공개되는 틱에 바로 반영되고, 나머지 효과만 며칠에 걸쳐 나옵니다. "
             "공개 뒤 추격보다는 예고 일정에서 기대 대비 서프라이즈 방향에 미리 포지션을 두는 쪽이 기대값이 높습니다."))
    if o["axes"][2]["v"] >= 75 and o["ready"] and 1 - (o["cash_ratio"] or 0) >= 0.3:
        ts = pct(o["top_share"])
        add(6, "tip-04",
            ("한 주식에 돈이 너무 몰려 있어요",
             f"가진 주식 중 평균 {ts}가 한 종목이었어요. 그 회사에 나쁜 뉴스가 하나만 나와도 내 돈 전체가 크게 흔들려요.",
             "반대로 움직이는 업종을 섞어 3~4종목으로 나눠 보세요. 예: 금리가 오르면 좋은 은행 + 금리에 약한 건설"),
            ("한 종목에 너무 몰려 있어요",
             f"주식 중 평균 {ts}가 한 종목이었습니다. 반대로 움직이는 업종을 섞으면 사건 하나에 덜 흔들립니다."),
            (f"집중도: 최대 종목 비중 {ts}, 평균 {o['n_hold'] or 0:.1f}종목 보유",
             "종목 하나의 사건 위험이 계좌 변동성을 좌우합니다. 요인 실험실에서 금리·환율·유가 민감도가 반대인 업종을 짝지어 "
             "요인 노출을 상쇄하고, 종목당 비중 상한(예: 25%)을 두세요."))
    if o["fees"] and (o["fee_pct"] >= 0.8 or (o["realized"] > 0 and o["fees"] > o["realized"] * 0.3)):
        f, fp = o["fees"], o["fee_pct"]
        vs = f" 실현손익의 {f / o['realized'] * 100:.0f}%에 해당합니다." if o["realized"] > 0 else ""
        add(6, "tip-09",
            (f"사고팔 때 드는 비용으로 {f:,}원을 썼어요",
             f"주식은 살 때마다 수수료, 팔 때마다 세금이 붙어요. 이번 게임에서 처음 돈의 {fp:.2f}%가 비용으로 나갔어요.",
             "사기 전에 '왜 사는지'를 한 줄로 말할 수 있을 때만 사 보세요."),
            (f"수수료·세금으로 {f:,}원이 나갔어요",
             f"시작 자금의 {fp:.2f}%입니다. 자주 사고팔수록 비용이 수익을 갉아먹습니다."),
            (f"거래비용 {f:,}원 (원금 대비 {fp:.2f}%, 하루 {o['tpd']:.1f}회)",
             f"매도세 때문에 왕복 비용이 편도 수수료보다 훨씬 큽니다.{vs} "
             "기대 수익이 왕복 비용의 몇 배인지 따져 보고, 신호가 약한 회전 매매를 줄이세요."))
    if o["buy_pos"] is not None and o["buy_days"] >= 3 and o["buy_pos"] >= 0.65:
        bp = pct(o["buy_pos"])
        sp = f"매도 위치 {pct(o['sell_pos'])}와 비교해" if o["sell_pos"] is not None else "매도 위치와 함께"
        add(5, "tip-07",
            ("그날 비쌀 때 사는 편이에요",
             f"산 가격이 그날 가격 중 위쪽({bp})이었어요. 0%가 그날 제일 쌀 때, 100%가 제일 비쌀 때예요. "
             "오르는 걸 보고 급하게 따라 사면 이렇게 돼요.",
             "사고 싶어도 게임 시간으로 30분쯤 지켜본 뒤 사 보세요."),
            ("그날 비싼 시간대에 사는 편이에요",
             f"매수 가격이 그날 가격 범위의 평균 {bp} 위치였습니다(0% = 그날 최저가). "
             "급하게 따라 사기보다 한 번 쉬어 가며 사 보세요."),
            (f"매수 체결 위치: 일중 범위 상단 {bp}",
             f"추세를 확인하고 들어가는 것 자체는 전략일 수 있지만, {sp} 범위 대비 손익을 점검하세요. "
             "분할 매수로 평균 단가를 범위 중앙에 가깝게 가져가는 방법도 있습니다."))
    if (o["cash_ratio"] or 0) >= 0.7 and o["done_days"] >= 3:
        cr = pct(o["cash_ratio"])
        add(4, "tip-11",
            ("돈을 대부분 쓰지 않고 있어요",
             f"평균 {cr}가 현금이었어요. 조심하는 건 좋지만, 주식을 안 사면 배울 기회도 적어요.",
             "확신이 드는 뉴스가 오면 전체 돈의 10~20%만 먼저 사 보세요."),
            ("현금을 너무 많이 들고 있어요",
             f"평균 {cr}가 현금이었습니다. 신중한 건 좋지만, 확신이 드는 사건이 오면 조금씩 들어가 보세요."),
            (f"평균 현금 비중 {cr} (계좌 베타 {o['beta'] or 0:.2f})",
             f"시장 수익률({o['mkt']:+.1f}%)을 놓치는 기회비용이 큽니다. "
             "베타가 낮은 업종으로 기본 노출을 깔아 두고, 사건이 오면 비중을 늘리는 코어-새틀라이트 구성을 고려하세요."))
    if o["cash_ratio"] is not None and o["cash_ratio"] <= 0.05 and o["done_days"] >= 3:
        add(4, "tip-11",
            ("현금을 거의 안 남겨 둬요",
             "돈을 전부 주식에 넣어 두면, 시장이 크게 떨어졌을 때 싸게 살 돈이 없어요.",
             "항상 20~30%는 현금으로 남겨 두는 연습을 해 보세요."),
            ("현금을 거의 남기지 않아요",
             "늘 전부 투자해 두면 큰 하락 사건 때 싸게 살 여력이 없습니다. 20~30%는 남겨 두는 연습을 해 보세요."),
            (f"현금 비중 {pct(o['cash_ratio'])}: 늘 전액 투자 상태",
             f"최대 낙폭 {o['mdd']:.1f}% 구간에서 추가 매수 여력이 없었습니다. "
             "20~30% 현금 완충은 하락 사건 때 옵션처럼 쓸 수 있습니다."))
    if o["planned"] == 0 and o["ready"]:
        add(3, "tip-01",
            ("미리 알려 준 일정을 놓쳤어요",
             "금리 발표·실적 발표는 이틀 전에 '예고' 뉴스로 알려 줘요. 미리 알면 준비할 시간이 있어요.",
             "알림 창의 '예정된 일정'을 보고, 결과가 좋으면 어떻게 하고 나쁘면 어떻게 할지 적어 두세요."),
            ("예고된 일정을 활용하지 않았어요",
             "금리 결정·실적 발표는 이틀 전에 예고됩니다. 결과별로 무엇을 할지 미리 정해 두면 뉴스에 휘둘리지 않습니다."),
            ("예고 이벤트 미활용",
             "금리·실적은 이틀 전에 예고되고 시장 기대치도 함께 공개됩니다. 반응 크기는 기대 대비 서프라이즈에 비례하므로 "
             "상회·부합·하회 시나리오별 진입·청산 계획을 미리 세워 두세요."))
    tips.sort(key=lambda x: -x[0])
    coach = [t for _, t in tips[:COACH_LIMIT[level]]]
    praise = []
    if o["ready"] and o["excess"] > 0:
        praise.append(f"시장(종합지수 {o['mkt']:+.1f}%)보다 {o['excess']:.1f}%p 더 벌었습니다.")
    if o["win_rate"] is not None and o["n_sells"] >= 3 and o["win_rate"] >= 0.6:
        praise.append(f"판 거래 {o['n_sells']}번 중 {o['n_wins']}번이 이익이었습니다.")
    if o["ready"] and o["mdd"] > -5:
        praise.append(f"가장 크게 떨어졌을 때도 {o['mdd']:.1f}%로 손실을 잘 관리했습니다.")
    if o["planned"] >= 2:
        praise.append(f"예고된 일정을 보고 {o['planned']}번 미리 움직였습니다.")
    if o["clue_heeded"] >= 2:
        praise.append(f"발표 전 단서를 읽고 {o['clue_heeded']}번 결과와 같은 쪽으로 움직였습니다.")
    if level == "expert" and o["payoff"] is not None and o["payoff"] >= 1.5 and o["n_sells"] >= 3:
        praise.append(f"손익비 {o['payoff']:.2f}: 벌 때 크게, 잃을 때 작게 끊었습니다.")
    if level == "beginner" and o["ready"] and not praise:
        praise.append(f"직접 {o['n_trades']}번 거래하며 시장을 겪어 봤어요. 이게 가장 중요한 첫걸음이에요.")
    return coach, praise


def summary(a):
    """명예의 전당에 같이 저장할 요약"""
    return {"persona": list(a["persona"][:2]), "axes": {x["key"]: round(x["v"]) for x in a["axes"]},
            "traits": [list(t) for t in a["traits"]], "ret": a["ret"], "excess": a["excess"],
            "ready": a["ready"]}
