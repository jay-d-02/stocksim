"""매매 원장 계산: 수수료·거래세·평균 매수가·실현손익

DB·게임 상태와 무관한 순수 함수라서 단위 테스트로 규칙을 고정한다.
규칙
- 금액은 원 단위 정수. 수수료·세금은 원 미만 버림
- 비율 계산은 Decimal로 한다. 부동소수점으로 하면 10,000원 × 0.29% 가 28.999…원이 되어
  버림 후 28원(정답 29원)이 되는 식으로 1원씩 틀어진다
- 수수료는 매수·매도 모두, 거래세는 매도에만 붙는다
- 평균 매수가는 수수료를 뺀 체결가 기준 (국내 증권사 잔고 화면과 같은 방식)
- 실현손익 = 매도 정산금(체결금액 − 수수료 − 거래세) − 평균 매수가 × 매도 수량
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN


class OrderRejected(Exception):
    """주문 거부. code는 API 응답·주문 기록에 그대로 쓰는 사유 코드"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class FeeSchedule:
    fee_rate: Decimal      # 매수·매도 각각
    tax_rate: Decimal      # 매도만

    @classmethod
    def of(cls, fee_rate, tax_rate):
        """float·문자열 어느 쪽으로 받아도 표기 그대로의 Decimal로 (0.00015 → Decimal('0.00015'))"""
        return cls(Decimal(str(fee_rate)), Decimal(str(tax_rate)))


@dataclass(frozen=True)
class Position:
    qty: int
    avg: float             # 평균 매수가 (원, 소수 가능)


@dataclass(frozen=True)
class Fill:
    """체결 결과. 원장에 반영할 값만 담고, 반영은 호출한 쪽이 한다"""
    side: str              # "BUY" | "SELL"
    qty: int
    price: int
    amount: int            # 체결금액 = 가격 × 수량
    fee: int
    tax: int
    cash_delta: int        # 예수금 변화 (매수는 음수)
    profit: int | None     # 실현손익 (매도만)
    position: Position | None   # 체결 뒤 보유 (전량 매도면 None)


def charge(amount, rate):
    """금액 × 비율, 원 미만 버림"""
    return int((Decimal(amount) * rate).to_integral_value(rounding=ROUND_DOWN))


def _check(price, qty):
    if not isinstance(qty, int) or qty <= 0:
        raise OrderRejected("INVALID_QTY", "수량을 1주 이상으로 입력하세요.")
    if price <= 0:
        raise OrderRejected("INVALID_PRICE", "가격이 올바르지 않습니다.")


def buy(cash, position, price, qty, fees):
    _check(price, qty)
    amount = price * qty
    fee = charge(amount, fees.fee_rate)
    need = amount + fee
    if need > cash:
        raise OrderRejected("INSUFFICIENT_CASH", f"예수금이 부족합니다. 필요 {need:,}원 / 보유 {cash:,}원")
    if position:
        new_qty = position.qty + qty
        new = Position(new_qty, (position.avg * position.qty + amount) / new_qty)
    else:
        new = Position(qty, float(price))
    return Fill("BUY", qty, price, amount, fee, 0, -need, None, new)


def sell(position, price, qty, fees):
    _check(price, qty)
    held = position.qty if position else 0
    if held < qty:
        raise OrderRejected("INSUFFICIENT_QTY", f"보유 수량이 부족합니다. 보유 {held:,}주")
    amount = price * qty
    fee = charge(amount, fees.fee_rate)
    tax = charge(amount, fees.tax_rate)
    receive = amount - fee - tax
    profit = receive - round(position.avg * qty)
    left = position.qty - qty
    return Fill("SELL", qty, price, amount, fee, tax, receive, profit,
                Position(left, position.avg) if left else None)


def max_buy_qty(cash, price, fees):
    """수수료까지 내고 살 수 있는 최대 수량"""
    if price <= 0:
        return 0
    q = int(Decimal(cash) / (Decimal(price) * (1 + fees.fee_rate)))
    while q > 0 and price * q + charge(price * q, fees.fee_rate) > cash:
        q -= 1
    return q


# 수정확인