"""매매 원장 계산 테스트: 수수료·거래세·평균 매수가·실현손익·거부 사유"""
import pytest

from simulator.ledger import FeeSchedule, OrderRejected, Position, buy, charge, max_buy_qty, sell

FEES = FeeSchedule.of(0.00015, 0.002)      # 수수료 0.015%, 거래세 0.2% (게임 기본값)


def test_buy():
    f = buy(cash=1_000_000, position=None, price=10_000, qty=10, fees=FEES)
    assert f.side == "BUY"
    assert f.amount == 100_000
    assert f.fee == 15                                  # 100,000 × 0.015%
    assert f.tax == 0                                   # 거래세는 매도에만
    assert f.cash_delta == -100_015
    assert f.profit is None
    assert f.position == Position(10, 10_000.0)


def test_sell():
    f = sell(Position(10, 10_000.0), price=12_000, qty=4, fees=FEES)
    assert f.side == "SELL"
    assert f.amount == 48_000
    assert f.cash_delta == 48_000 - f.fee - f.tax
    assert f.position == Position(6, 10_000.0)         # 판 만큼만 줄고 평단은 그대로


def test_sell_all_clears_position():
    f = sell(Position(10, 10_000.0), price=9_000, qty=10, fees=FEES)
    assert f.position is None


def test_insufficient_cash():
    with pytest.raises(OrderRejected) as e:
        buy(cash=100_000, position=None, price=10_000, qty=10, fees=FEES)   # 수수료 15원이 모자람
    assert e.value.code == "INSUFFICIENT_CASH"


def test_buy_exactly_all_cash():
    f = buy(cash=100_015, position=None, price=10_000, qty=10, fees=FEES)
    assert f.cash_delta == -100_015


def test_insufficient_quantity():
    with pytest.raises(OrderRejected) as e:
        sell(Position(3, 10_000.0), price=10_000, qty=4, fees=FEES)
    assert e.value.code == "INSUFFICIENT_QTY"
    with pytest.raises(OrderRejected) as e:
        sell(None, price=10_000, qty=1, fees=FEES)      # 아예 안 가진 종목
    assert e.value.code == "INSUFFICIENT_QTY"


@pytest.mark.parametrize("qty", [0, -1, 1.5])
def test_invalid_quantity(qty):
    with pytest.raises(OrderRejected) as e:
        buy(cash=10**9, position=None, price=10_000, qty=qty, fees=FEES)
    assert e.value.code == "INVALID_QTY"


def test_average_price():
    # 10주 @10,000 + 30주 @12,000 → (100,000 + 360,000) / 40 = 11,500
    first = buy(10**9, None, 10_000, 10, FEES).position
    second = buy(10**9, first, 12_000, 30, FEES).position
    assert second.qty == 40
    assert second.avg == pytest.approx(11_500)


def test_average_price_excludes_fee():
    pos = buy(10**9, None, 33_333, 3, FEES).position
    assert pos.avg == 33_333                            # 수수료는 평단에 넣지 않음


def test_fee():
    assert buy(10**9, None, 1_000_000, 1, FEES).fee == 150          # 1,000,000 × 0.015%
    assert buy(10**9, None, 6_600, 1, FEES).fee == 0                # 0.99원 → 원 미만 버림
    assert sell(Position(1, 1.0), 1_000_000, 1, FEES).fee == 150    # 매도에도 수수료


def test_fee_uses_decimal_not_float():
    # 10,000 × 0.0029 를 float로 하면 28.999999999999996 → 버림 28원. 정답은 29원
    assert int(10_000 * 0.0029) == 28
    assert charge(10_000, FeeSchedule.of(0.0029, 0).fee_rate) == 29


def test_tax():
    f = sell(Position(10, 10_000.0), price=10_000, qty=10, fees=FEES)
    assert f.tax == 200                                 # 100,000 × 0.2%
    assert f.fee == 15
    assert f.cash_delta == 100_000 - 15 - 200


def test_profit():
    # 평단 10,000 × 10주를 12,000에 전량 매도
    f = sell(Position(10, 10_000.0), price=12_000, qty=10, fees=FEES)
    # 정산 120,000 − 수수료 18 − 거래세 240 = 119,742 ; 원가 100,000
    assert f.profit == 19_742


def test_profit_loss_includes_costs():
    # 같은 가격에 사고팔면 비용만큼 손실: 매도 수수료 15 + 세금 200 (매수 수수료는 평단에 없음)
    f = sell(Position(10, 10_000.0), price=10_000, qty=10, fees=FEES)
    assert f.profit == -215


def test_round_trip_cash_matches_profit():
    """사고 판 뒤 예수금 변화 = 실현손익 − 매수 수수료"""
    cash = 1_000_000
    b = buy(cash, None, 25_750, 17, FEES)
    s = sell(b.position, 27_300, 17, FEES)
    assert cash + b.cash_delta + s.cash_delta - cash == s.profit - b.fee


def test_max_buy_qty():
    q = max_buy_qty(1_000_000, 10_000, FEES)
    assert q == 99                                      # 100주는 수수료 150원이 모자람
    buy(1_000_000, None, 10_000, q, FEES)               # 최대 수량은 실제로 살 수 있어야 함
    with pytest.raises(OrderRejected):
        buy(1_000_000, None, 10_000, q + 1, FEES)
