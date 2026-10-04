"""
종목 정보(기업 개요·투자지표) 랜덤 생성기.

연습용 가상 데이터입니다. 같은 종목은 하루 동안 같은 값이 나오도록
(종목코드 + 날짜)를 시드로 씁니다. 새로고침할 때마다 숫자가 바뀌지 않게 하려는 것.
"""
import random
from datetime import date

SECTORS = {
    "005930": "반도체", "000660": "반도체", "373220": "2차전지",
    "207940": "바이오", "005380": "자동차", "000270": "자동차",
    "035420": "인터넷", "035720": "인터넷", "068270": "바이오",
    "005490": "철강", "051910": "화학", "006400": "2차전지",
    "105560": "금융", "055550": "금융", "012330": "자동차부품",
    "028260": "지주·건설", "066570": "전자·가전", "003550": "지주",
    "017670": "통신", "034730": "지주", "259960": "게임",
    "352820": "엔터테인먼트", "247540": "2차전지", "086520": "2차전지",
}
KOSDAQ = {"247540", "086520"}
OTHER_SECTORS = ["반도체", "바이오", "자동차", "화학", "금융", "인터넷", "게임",
                 "유통", "건설", "음식료", "통신", "기계", "조선", "엔터테인먼트"]

OPINIONS = [("강력매수", 0.10), ("매수", 0.45), ("중립", 0.35), ("매도", 0.10)]

HEADLINES = [
    "{name}, {q}분기 영업이익 시장 예상치 {beat}",
    "외국인, {name} {days}거래일 연속 {buy_sell}",
    "{name} 신규 {biz} 사업 진출 검토",
    "증권가 \"{name} 목표주가 {tp_dir}\" 잇따라",
    "{sector} 업황 {cycle}… {name} 주목",
    "{name}, 자사주 {amt}억원 규모 매입 결정",
    "{name} 대규모 수주 공시… 계약 규모 {amt}억원",
    "기관 {buy_sell}에 {name} 장중 {move}",
]


def _pick_weighted(rnd, items):
    r, acc = rnd.random(), 0
    for v, w in items:
        acc += w
        if r < acc:
            return v
    return items[-1][0]


def generate(code, name, price, day=None):
    """
    price: 현재가(원). 실제 시세가 있으면 그걸 기준으로 지표를 만듦.
    반환: 템플릿에서 바로 쓰는 dict
    """
    day = day or date.today()
    rnd = random.Random(f"{code}-{day.isoformat()}")

    sector = SECTORS.get(code) or rnd.choice(OTHER_SECTORS)
    market = "KOSDAQ" if code in KOSDAQ else ("KOSPI" if code in SECTORS
                                              else rnd.choice(["KOSPI", "KOSDAQ"]))

    # 시세 관련
    prev = price / (1 + rnd.gauss(0, 0.015))
    open_ = int(round(prev * (1 + rnd.gauss(0, 0.006)), -1))
    high = int(round(max(price, open_) * (1 + abs(rnd.gauss(0, 0.008))), -1))
    low = int(round(min(price, open_) * (1 - abs(rnd.gauss(0, 0.008))), -1))
    w52_high = int(round(max(high, price * rnd.uniform(1.05, 1.6)), -1))
    w52_low = int(round(min(low, price * rnd.uniform(0.55, 0.95)), -1))

    # 시가총액 약 3천억 ~ 500조 원 사이에서 고르고 주식 수를 역산
    shares = max(1_000_000, int(10 ** rnd.uniform(11.5, 14.7) / price))
    market_cap = price * shares
    volume = int(shares * rnd.uniform(0.001, 0.02))
    turnover = volume * price

    # 투자지표
    per = round(rnd.uniform(4, 60), 2) if rnd.random() > 0.08 else None  # 적자면 PER 없음
    eps = int(price / per) if per else -int(price * rnd.uniform(0.01, 0.1))
    pbr = round(rnd.uniform(0.3, 6), 2)
    bps = int(price / pbr)
    roe = round((eps / bps) * 100, 2) if bps else 0
    div_yield = round(rnd.uniform(0, 5), 2) if rnd.random() > 0.2 else 0.0
    foreign = round(rnd.uniform(3, 55), 1)
    beta = round(rnd.uniform(0.5, 1.8), 2)

    opinion = _pick_weighted(rnd, OPINIONS)
    tp_mult = {"강력매수": (1.3, 1.6), "매수": (1.1, 1.35),
               "중립": (0.95, 1.1), "매도": (0.7, 0.95)}[opinion]
    target = int(round(price * rnd.uniform(*tp_mult), -2))
    analysts = rnd.randint(3, 28)

    # 가상 뉴스
    news = []
    for tpl in rnd.sample(HEADLINES, 3):
        news.append({
            "title": tpl.format(
                name=name, sector=sector, q=rnd.randint(1, 4),
                beat=rnd.choice(["상회", "하회", "부합"]),
                days=rnd.randint(3, 12), buy_sell=rnd.choice(["순매수", "순매도"]),
                biz=rnd.choice(["AI", "로봇", "헬스케어", "모빌리티", "클라우드", "친환경"]),
                tp_dir=rnd.choice(["상향", "하향"]),
                cycle=rnd.choice(["회복 기대", "둔화 우려", "바닥 탈출", "호황 지속"]),
                amt=rnd.choice([300, 500, 1000, 2000, 5000]),
                move=rnd.choice(["강세", "약세", "급등", "반등"])),
            "hours": rnd.randint(1, 23),
        })

    return {
        "sector": sector, "market": market,
        "open": open_, "high": high, "low": low,
        "w52_high": w52_high, "w52_low": w52_low,
        "w52_pos": (price - w52_low) / (w52_high - w52_low) * 100 if w52_high > w52_low else 50,
        "shares": shares, "market_cap": market_cap,
        "volume": volume, "turnover": turnover,
        "per": per, "eps": eps, "pbr": pbr, "bps": bps, "roe": roe,
        "div_yield": div_yield, "foreign": foreign, "beta": beta,
        "opinion": opinion, "target": target, "analysts": analysts,
        "upside": (target - price) / price * 100,
        "news": sorted(news, key=lambda n: n["hours"]),
    }


def fallback_price(code, day=None):
    """시세를 못 불러왔을 때 쓸 가상 현재가"""
    day = day or date.today()
    return random.Random(f"price-{code}-{day.isoformat()}").randint(5, 400) * 500
