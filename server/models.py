"""테이블 정의 (docs/database.md 의 ERD)

돈: 원 단위 BIGINT. 평균 매수가: NUMERIC(18,4). 비율: NUMERIC(8,6). 부동소수점 컬럼에 돈을 넣지 않는다.
제약조건은 코드 버그에 대한 마지막 방어선이다 (예수금 음수, 진행 중 게임 2개, 사유 없는 거부 등).
comment= 는 PostgreSQL의 COMMENT ON TABLE/COLUMN 으로 들어가 DB 도구(DBeaver·pgAdmin·psql \\d+)에서 보인다.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer,
                        Numeric, SmallInteger, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

MONEY = BigInteger
NOW = dict(server_default=func.now())

# 여러 테이블에 같은 뜻으로 나오는 컬럼
GAME_FK = "게임 ID (games.id)"
STOCK_FK = "종목 코드 (stocks.code)"
GAME_DAY = "게임 속 거래일 (1부터)"
GAME_TICK = "게임 속 시각: 그날 몇 번째 5분인지 (0 = 09:00, 78 = 15:30)"


EXPERIENCE = {"beginner": "처음이에요", "intermediate": "해 봤어요", "expert": "자신 있어요"}
PERMITS = {"USER": "일반 회원", "ADMIN": "관리자"}


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("nickname", name="users_nickname_key"),
        UniqueConstraint("email", name="users_email_key"),
        CheckConstraint("experience IN ('beginner', 'intermediate', 'expert')", name="experience_valid"),
        CheckConstraint("save_status IN ('Y', 'N')", name="save_status_valid"),
        CheckConstraint("permit IN ('USER', 'ADMIN')", name="permit_valid"),
        # 탈퇴(N)하면 아이디·닉네임을 지우므로 NULL 허용. 사용 중(Y)인 회원은 반드시 있어야 함
        CheckConstraint("save_status = 'N' OR (username IS NOT NULL AND nickname IS NOT NULL)",
                        name="active_has_identity"),
        CheckConstraint("(save_status = 'N') = (withdrawn_at IS NOT NULL)", name="withdrawn_has_time"),
        {"comment": "회원. 탈퇴해도 행은 남기고(save_status = 'N') 개인정보만 지운다"},
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="회원 ID")
    username: Mapped[str | None] = mapped_column(Text, unique=True,
                                                 comment="로그인 아이디 (3~30자, 중복 불가). 탈퇴하면 NULL")
    password_hash: Mapped[str] = mapped_column(Text, comment="비밀번호 해시 (werkzeug scrypt 형식, 평문 저장 안 함). "
                                                             "탈퇴하면 어떤 비밀번호와도 맞지 않는 값")
    nickname: Mapped[str | None] = mapped_column(Text, comment="화면·명예의 전당에 보이는 이름 (중복 불가). "
                                                               "로그인 아이디는 남에게 보이지 않는다. 탈퇴하면 NULL")
    experience: Mapped[str] = mapped_column(Text, server_default="beginner",
                                            comment="투자 경험: beginner 처음 / intermediate 해 봤음 / expert 자신 있음")
    email: Mapped[str | None] = mapped_column(Text, comment="이메일 (선택, 소문자로 저장, 중복 불가). 비밀번호 찾기용")
    privacy_agreed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="개인정보 수집·이용 동의 시각. 동의 절차 전에 가입한 회원은 NULL")
    save_status: Mapped[str] = mapped_column(Text, server_default="Y",
                                             comment="Y 사용 중 / N 탈퇴. 로그인은 Y만 가능하고, N은 명예의 전당에서도 빠진다")
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="탈퇴 시각 (save_status = 'N'일 때만)")
    permit: Mapped[str] = mapped_column(Text, server_default="USER",
                                        comment="권한: USER 일반 회원 / ADMIN 관리자 (회원 정보 열람·권한 변경)")

    @property
    def active(self):
        return self.save_status == "Y"

    @property
    def is_admin(self):
        return self.active and self.permit == "ADMIN"

    @property
    def display_name(self):
        return self.nickname if self.active else f"탈퇴한 회원 #{self.id}"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **NOW, comment="가입 시각")


class Stock(Base):
    """종목 마스터. simulator/catalog.py 가 원본이고 앱 시작 때 동기화된다"""
    __tablename__ = "stocks"
    __table_args__ = {"comment": "가상 종목 마스터. 원본은 simulator/catalog.py 이고 앱이 시작할 때 이 테이블을 맞춘다"}
    code: Mapped[str] = mapped_column(Text, primary_key=True, comment="종목 코드 (예: HBS)")
    name: Mapped[str] = mapped_column(Text, comment="종목 이름 (예: 한빛반도체)")
    sector: Mapped[str] = mapped_column(Text, comment="업종. 금리·환율·유가·경기 민감도가 업종별로 다르다")
    base_price: Mapped[int] = mapped_column(MONEY, comment="게임 시작 가격의 기준값 (원). 실제 시작가는 ±10% 무작위")
    beta: Mapped[Decimal] = mapped_column(Numeric(4, 2), comment="베타: 시장 전체가 1% 움직일 때 평균 몇 % 움직이는지")
    volatility: Mapped[Decimal] = mapped_column(Numeric(5, 2), comment="하루 변동성 (%, 표준편차)")
    description: Mapped[str] = mapped_column(Text, comment="회사 소개")
    hq_name: Mapped[str] = mapped_column(Text, comment="본사 위치 (국내 지도 핀)")


class Game(Base):
    __tablename__ = "games"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="게임 ID")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), comment="플레이어 (users.id)")
    seed: Mapped[int] = mapped_column(BigInteger, comment="시장 난수 시드. 같은 시드면 같은 가격·사건이 재현된다 (복구에 사용)")
    status: Mapped[str] = mapped_column(Text, server_default="ACTIVE",
                                        comment="ACTIVE 진행 중 / FINISHED 끝까지 마침 / ABANDONED 새 게임을 시작해 포기")
    current_day: Mapped[int] = mapped_column(SmallInteger, comment="현재 거래일 (1 ~ max_day)")
    current_tick: Mapped[int] = mapped_column(SmallInteger, server_default="0",
                                              comment="현재 시각(틱). Redis 상태가 사라지면 장 시작 지점부터 여기까지 다시 돌려 복구")
    max_day: Mapped[int] = mapped_column(SmallInteger, comment="게임 길이 (거래일)")
    start_cash: Mapped[int] = mapped_column(MONEY, comment="시작 자금 (원)")
    cash: Mapped[int] = mapped_column(MONEY, comment="현재 예수금 (원). 돈의 원본은 이 컬럼이며 Redis 값은 읽을 때마다 여기에 맞춘다")
    fee_rate: Mapped[Decimal] = mapped_column(Numeric(8, 6), comment="매매 수수료율 (매수·매도 각각, 예: 0.00015 = 0.015%)")
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(8, 6), comment="거래세율 (매도만, 예: 0.002 = 0.2%)")
    final_total: Mapped[int | None] = mapped_column(MONEY, comment="최종 총자산 (원). 끝난 게임만")
    final_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), comment="최종 수익률 (%). 끝난 게임만")
    profile: Mapped[dict | None] = mapped_column(JSONB, comment="투자 성향 분석 요약 (스타일·점수·특징). 끝난 게임만")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **NOW, comment="시작 시각")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="끝난(또는 포기한) 시각")

    user: Mapped[User] = relationship()

    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'FINISHED', 'ABANDONED')", name="status_valid"),
        CheckConstraint("cash >= 0", name="cash_not_negative"),
        CheckConstraint("current_day BETWEEN 1 AND max_day", name="day_in_range"),
        CheckConstraint("current_tick BETWEEN 0 AND 78", name="tick_in_range"),
        # 한 사람이 진행 중인 게임은 하나만
        Index("games_one_active_per_user", "user_id", unique=True, postgresql_where=text("status = 'ACTIVE'")),
        Index("games_leaderboard", "status", text("final_total DESC")),
        {"comment": "게임 한 판. 예수금의 원본. 상태를 바꾸는 요청은 모두 이 행을 SELECT … FOR UPDATE로 잠그고 처리한다"},
    )


class Holding(Base):
    __tablename__ = "holdings"
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), primary_key=True, comment=GAME_FK)
    stock_code: Mapped[str] = mapped_column(ForeignKey("stocks.code"), primary_key=True, comment=STOCK_FK)
    qty: Mapped[int] = mapped_column(BigInteger, comment="보유 수량 (주). 0이 되면 행을 지운다")
    avg_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), comment="평균 매수가 (원, 수수료 제외한 체결가 기준)")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **NOW, onupdate=func.now(),
                                                 comment="마지막 변경 시각")
    __table_args__ = (CheckConstraint("qty > 0", name="qty_positive"),
                      {"comment": "보유 종목 (게임별 잔고)"})


class Order(Base):
    """주문. 거부된 주문도 사유와 함께 남긴다 (감사 추적)"""
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="주문 ID")
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), comment=GAME_FK)
    stock_code: Mapped[str | None] = mapped_column(ForeignKey("stocks.code"),
                                                   comment="종목 코드 (stocks.code). 없는 종목으로 거부된 주문이면 NULL")
    side: Mapped[str] = mapped_column(Text, comment="BUY 매수 / SELL 매도")
    qty: Mapped[int] = mapped_column(BigInteger, comment="주문 수량 (주)")
    status: Mapped[str] = mapped_column(Text, comment="FILLED 체결 / REJECTED 거부")
    reject_code: Mapped[str | None] = mapped_column(
        Text, comment="거부 사유 코드: INSUFFICIENT_CASH, INSUFFICIENT_QTY, MARKET_CLOSED, UNKNOWN_STOCK 등")
    reject_message: Mapped[str | None] = mapped_column(Text, comment="거부 사유 (화면에 보여 준 문장)")
    client_order_id: Mapped[str | None] = mapped_column(
        Text, comment="클라이언트가 붙인 주문 번호. 같은 게임에서 같은 값이 다시 오면 새로 체결하지 않음 (중복 제출 방지)")
    game_day: Mapped[int] = mapped_column(SmallInteger, comment=GAME_DAY)
    game_tick: Mapped[int] = mapped_column(SmallInteger, comment=GAME_TICK)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **NOW, comment="접수 시각 (실제 시각)")

    trade: Mapped["Trade | None"] = relationship(back_populates="order", uselist=False)

    __table_args__ = (
        CheckConstraint("side IN ('BUY', 'SELL')", name="side_valid"),
        CheckConstraint("status IN ('FILLED', 'REJECTED')", name="status_valid"),
        CheckConstraint("(status = 'REJECTED') = (reject_code IS NOT NULL)", name="reject_has_reason"),
        # 같은 주문 번호로 두 번 들어오면 한 번만 처리 (네트워크 재시도 대비)
        UniqueConstraint("game_id", "client_order_id", name="orders_idempotency"),
        Index("orders_game", "game_id", "id"),
        {"comment": "주문. 거부된 주문도 사유와 함께 남긴다 (감사 추적)"},
    )


class Trade(Base):
    """체결. 지금은 시장가 즉시 체결이라 주문 하나에 체결 하나"""
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="체결 ID")
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True,
                                          comment="주문 ID (orders.id). 주문 하나에 체결 하나")
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), comment=GAME_FK)
    stock_code: Mapped[str] = mapped_column(ForeignKey("stocks.code"), comment=STOCK_FK)
    side: Mapped[str] = mapped_column(Text, comment="BUY 매수 / SELL 매도")
    qty: Mapped[int] = mapped_column(BigInteger, comment="체결 수량 (주)")
    price: Mapped[int] = mapped_column(MONEY, comment="체결가 (원)")
    amount: Mapped[int] = mapped_column(MONEY, comment="체결금액 = 체결가 × 수량 (원)")
    fee: Mapped[int] = mapped_column(MONEY, comment="수수료 (원, 원 미만 버림)")
    tax: Mapped[int] = mapped_column(MONEY, comment="거래세 (원, 매도만, 원 미만 버림)")
    realized_pnl: Mapped[int | None] = mapped_column(
        MONEY, comment="실현손익 (원, 매도만) = 체결금액 − 수수료 − 거래세 − 평균 매수가 × 수량")
    cash_after: Mapped[int] = mapped_column(MONEY, comment="체결 직후 예수금 (원). 체결을 순서대로 더하면 이 값과 일치해야 한다")
    game_day: Mapped[int] = mapped_column(SmallInteger, comment=GAME_DAY)
    game_tick: Mapped[int] = mapped_column(SmallInteger, comment=GAME_TICK)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **NOW, comment="체결 시각 (실제 시각)")

    order: Mapped[Order] = relationship(back_populates="trade")

    __table_args__ = (
        CheckConstraint("qty > 0 AND price > 0 AND fee >= 0 AND tax >= 0", name="amounts_valid"),
        CheckConstraint("amount = price * qty", name="amount_matches"),
        CheckConstraint("(side = 'SELL') = (realized_pnl IS NOT NULL)", name="pnl_only_on_sell"),
        Index("trades_game", "game_id", "id"),
        {"comment": "체결 (원장). 시장가 즉시 체결이라 주문 하나에 체결 하나"},
    )


class StockPrice(Base):
    """게임별 일봉. day <= 0 은 게임 시작 전 차트"""
    __tablename__ = "stock_prices"
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), primary_key=True, comment=GAME_FK)
    stock_code: Mapped[str] = mapped_column(ForeignKey("stocks.code"), primary_key=True, comment=STOCK_FK)
    day: Mapped[int] = mapped_column(SmallInteger, primary_key=True,
                                     comment="거래일. 0 이하는 게임 시작 전 30일 차트 (0 = 시작 전날)")
    open: Mapped[int] = mapped_column(MONEY, comment="시가 (원) = 전날 종가")
    high: Mapped[int] = mapped_column(MONEY, comment="고가 (원)")
    low: Mapped[int] = mapped_column(MONEY, comment="저가 (원)")
    close: Mapped[int] = mapped_column(MONEY, comment="종가 (원)")
    __table_args__ = (CheckConstraint("low <= open AND low <= close AND high >= open AND high >= close",
                                      name="ohlc_consistent"),
                      {"comment": "게임별 일봉 (장 마감 때 기록). 장중 5분 가격은 Redis에만 있다"})


class Event(Base):
    """공개된 사건과 그 영향. 아직 일어나지 않은 사건은 여기에 없다"""
    __tablename__ = "events"
    __table_args__ = {"comment": "공개된 사건과 그 영향. 아직 일어나지 않은 사건은 기록하지 않는다 (공정성)"}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="사건 ID")
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), comment=GAME_FK)
    day: Mapped[int] = mapped_column(SmallInteger, comment=GAME_DAY)
    game_tick: Mapped[int] = mapped_column(SmallInteger, comment=GAME_TICK)
    type: Mapped[str] = mapped_column(Text, comment="macro 거시 / corp 기업 공시 / rate 금리 결정 / earnings 실적 / "
                                                    "trial 임상 / rumor 루머 / preview 예고")
    event_key: Mapped[str | None] = mapped_column(Text, comment="사건 종류 키 (simulator/catalog.py, 예: oil_up)")
    impacts: Mapped[dict] = mapped_column(JSONB, comment="종목별 예상 효과 {종목 코드: %}. 투자 성향 분석(뒤늦은 추격 매수)에 사용")
    prices_at: Mapped[dict] = mapped_column(JSONB, comment="사건이 공개된 순간의 종목 가격 {종목 코드: 원}")


class News(Base):
    __tablename__ = "news"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="뉴스 ID")
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), comment=GAME_FK)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"),
                                                 comment="이 뉴스를 만든 사건 (events.id). 안내·금리 예고처럼 가격 영향이 없으면 NULL")
    seq: Mapped[int] = mapped_column(Integer, comment="게임 안 뉴스 순번 (1부터). 새 뉴스만 받을 때 ?since= 기준")
    day: Mapped[int] = mapped_column(SmallInteger, comment=GAME_DAY)
    game_tick: Mapped[int] = mapped_column(SmallInteger, comment=GAME_TICK)
    time: Mapped[str] = mapped_column(Text, comment="화면에 보이는 시각 (예: 10:35, 아침 브리핑은 08:30)")
    kind: Mapped[str] = mapped_column(Text, comment="event 사건 / preview 일정 예고 / info 안내")
    tag: Mapped[str] = mapped_column(Text, comment="분류 표시 (금리·환율·유가·공시·실적·루머 등)")
    factor: Mapped[str | None] = mapped_column(Text, comment="관련 가격변동 요인 (/factors 페이지의 항목 id)")
    title: Mapped[str] = mapped_column(Text, comment="제목")
    body: Mapped[str] = mapped_column(Text, comment="본문")
    why: Mapped[str] = mapped_column(Text, server_default="", comment="해설: 왜 이런 종목이 오르고 내리는지")
    place: Mapped[str | None] = mapped_column(Text, comment="사건 장소 이름 (지도 핀)")
    lat: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), comment="위도")
    lon: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), comment="경도")
    domestic: Mapped[bool] = mapped_column(Boolean, server_default="false", comment="국내 사건이면 true (국내 지도에 핀)")

    stocks: Mapped[list["NewsStock"]] = relationship(cascade="all, delete-orphan", lazy="selectin")

    __table_args__ = (
        CheckConstraint("kind IN ('event', 'preview', 'info')", name="kind_valid"),
        UniqueConstraint("game_id", "seq", name="news_seq_per_game"),
        {"comment": "플레이어에게 공개된 뉴스"},
    )


class NewsStock(Base):
    __tablename__ = "news_stocks"
    __table_args__ = {"comment": "뉴스 ↔ 관련 종목 (다대다)"}
    news_id: Mapped[int] = mapped_column(ForeignKey("news.id", ondelete="CASCADE"), primary_key=True,
                                         comment="뉴스 ID (news.id)")
    stock_code: Mapped[str] = mapped_column(ForeignKey("stocks.code"), primary_key=True, comment=STOCK_FK)


class PortfolioHistory(Base):
    __tablename__ = "portfolio_history"
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), primary_key=True, comment=GAME_FK)
    day: Mapped[int] = mapped_column(SmallInteger, primary_key=True, comment=GAME_DAY)
    total: Mapped[int] = mapped_column(MONEY, comment="장 마감 총자산 (원) = 예수금 + 주식 평가액")
    cash: Mapped[int] = mapped_column(MONEY, comment="장 마감 예수금 (원)")
    stock_value: Mapped[int] = mapped_column(MONEY, comment="장 마감 주식 평가액 (원, 종가 기준)")
    allocation: Mapped[dict] = mapped_column(JSONB, comment="종목별 평가액 {종목 코드: 원}")
    __table_args__ = (CheckConstraint("total = cash + stock_value", name="total_adds_up"),
                      {"comment": "장 마감 기준 일별 자산 (자산 그래프·최대 낙폭 계산)"})


class MarketDaily(Base):
    __tablename__ = "market_daily"
    __table_args__ = {"comment": "장 마감 기준 시장 지표"}
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), primary_key=True, comment=GAME_FK)
    day: Mapped[int] = mapped_column(SmallInteger, primary_key=True, comment=GAME_DAY)
    index_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), comment="종합지수 (게임 시작 전날 = 1,000)")
    base_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), comment="기준금리 (%)")
    fx: Mapped[Decimal] = mapped_column(Numeric(8, 1), comment="원·달러 환율 (원)")
    oil: Mapped[Decimal] = mapped_column(Numeric(8, 2), comment="국제유가 (달러/배럴)")


class GameSnapshot(Base):
    """복구 지점: 장이 열린 직후의 엔진 상태. Redis가 비면 여기서 되살린다"""
    __tablename__ = "game_snapshots"
    __table_args__ = {"comment": "복구 지점: 장이 열린 직후의 엔진 상태. Redis 상태가 사라지면 여기서 되살린다"}
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), primary_key=True, comment=GAME_FK)
    day: Mapped[int] = mapped_column(SmallInteger, comment="저장한 거래일")
    state: Mapped[dict] = mapped_column(JSONB, comment="엔진 상태 전체 (simulator/engine.py 의 게임 상태 dict)")
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **NOW, onupdate=func.now(),
                                               comment="저장 시각")


class RealNews(Base):
    """실제 뉴스 (NAVER API HUB 뉴스 검색). 게임과 무관한 학습용 자료"""
    __tablename__ = "real_news"
    __table_args__ = (Index("real_news_recent", text("published_at DESC")),
                      {"comment": "실제 뉴스 제목·요약·링크와 자동 분류한 요인. 본문은 저장하지 않는다 (저작권). 30일 지나면 지움"})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="뉴스 ID")
    link: Mapped[str] = mapped_column(Text, unique=True, comment="원문 주소 (언론사 링크, 없으면 네이버 뉴스 링크). 중복 판단 기준")
    title: Mapped[str] = mapped_column(Text, comment="제목 (HTML 태그 제거)")
    summary: Mapped[str] = mapped_column(Text, comment="검색 결과의 짧은 요약 (2~3줄)")
    source: Mapped[str] = mapped_column(Text, comment="원문 도메인 (예: www.yna.co.kr)")
    query: Mapped[str] = mapped_column(Text, comment="이 뉴스를 찾은 검색어")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="기사 시각")
    factors: Mapped[dict] = mapped_column(JSONB, comment="자동 분류한 요인 {rate|fx|oil|econ: 1 오름 / -1 내림 / 0 그대로}")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **NOW, comment="가져온 시각")
