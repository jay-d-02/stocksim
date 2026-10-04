"""API 요청·응답 모델 (Pydantic). /docs 의 API 문서도 여기서 만들어진다"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from simulator import engine as E


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Error(BaseModel):
    code: str = Field(examples=["INSUFFICIENT_CASH"])
    message: str


class ErrorResponse(BaseModel):
    detail: Error


# ---------------------------------------------------------------- 인증
class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=30, examples=["trader01"])
    password: str = Field(min_length=4, max_length=128, examples=["secret123"])


PASSWORD_MIN = 8          # 새로 정하는 비밀번호 (예전 회원의 짧은 비밀번호로도 로그인은 됨)
Experience = Literal["beginner", "intermediate", "expert"]
NewPassword = Field(min_length=PASSWORD_MIN, max_length=128, examples=["secret123"])
Nickname = Field(None, min_length=2, max_length=20, examples=["개미왕"])
Email = Field(None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", examples=["me@example.com"])


def _blank_to_none(v):
    return (v.strip() or None) if isinstance(v, str) else v


class RegisterIn(Credentials):
    password: str = NewPassword
    nickname: str | None = Field(None, min_length=2, max_length=20, examples=["개미왕"],
                                 description="화면·명예의 전당에 보이는 이름. 비우면 아이디")
    experience: Experience = "beginner"
    email: str | None = Field(None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
                              examples=["me@example.com"], description="선택. 비밀번호 찾기용")
    agree_privacy: bool = Field(description="개인정보 수집·이용 동의 (true여야 가입됨, /privacy 참고)")

    _blank = field_validator("nickname", "email", mode="before")(_blank_to_none)

    @field_validator("agree_privacy")
    @classmethod
    def must_agree(cls, v):
        if not v:
            raise ValueError("개인정보 수집·이용에 동의해야 가입할 수 있습니다.")
        return v

    @model_validator(mode="after")
    def password_not_username(self):
        if self.password.lower() == self.username.strip().lower():
            raise ValueError("비밀번호를 아이디와 다르게 정하세요.")
        return self


class ProfileIn(BaseModel):
    """바꿀 항목만 보내면 됨. 이메일을 지우려면 빈 문자열이나 null"""
    nickname: str | None = Nickname
    experience: Experience | None = None
    email: str | None = Email

    _blank = field_validator("nickname", "email", mode="before")(_blank_to_none)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = NewPassword


class PasswordConfirmIn(BaseModel):
    password: str = Field(max_length=128, description="본인 확인용 현재 비밀번호")


class ResetRequestIn(BaseModel):
    email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ResetIn(BaseModel):
    token: str = Field(max_length=500)
    new_password: str = NewPassword


class UserOut(ORM):
    id: int
    username: str
    nickname: str
    experience: str
    email: str | None = None
    permit: Literal["USER", "ADMIN"] = "USER"


# ---------------------------------------------------------------- 관리자
class AdminUserOut(ORM):
    """관리자용 회원 정보. 탈퇴 회원(save_status = 'N')은 아이디·닉네임·이메일이 비어 있다"""
    id: int
    username: str | None
    nickname: str | None
    display_name: str
    email: str | None
    experience: str
    permit: Literal["USER", "ADMIN"]
    save_status: Literal["Y", "N"]
    created_at: datetime
    withdrawn_at: datetime | None
    privacy_agreed_at: datetime | None


class AdminUserRow(BaseModel):
    user: AdminUserOut
    games: int = Field(description="게임 수 (포기 포함)")
    finished: int = Field(description="끝까지 마친 게임 수")
    best_pct: float | None = Field(description="최고 수익률 (%)")
    last_played: datetime | None


class AdminUserPage(BaseModel):
    total: int
    page: int
    items: list[AdminUserRow]


class AdminGame(BaseModel):
    id: int
    status: str
    day: int
    max_day: int
    started_at: datetime
    finished_at: datetime | None
    final_total: int | None
    pct: float | None
    trades: int


class AdminUserDetail(BaseModel):
    user: AdminUserOut
    games: list[AdminGame]


class PermitIn(BaseModel):
    permit: Literal["USER", "ADMIN"]


# ---------------------------------------------------------------- 게임
class GameIn(BaseModel):
    days: Literal[15, 30] = Field(15, description="게임 길이 (거래일). 15 = 빠른 판, 30 = 정규 판")


class GameOut(ORM):
    id: int
    status: Literal["ACTIVE", "FINISHED", "ABANDONED"]
    current_day: int
    max_day: int
    start_cash: int
    cash: int
    started_at: datetime
    finished_at: datetime | None = None
    final_total: int | None = None
    final_return_pct: float | None = None


class GameDetail(GameOut):
    tick: int = Field(description="오늘 몇 번째 5분인지 (0 = 09:00)")
    clock: str = Field(examples=["10:35"])
    phase: Literal["open", "closed"]
    total: int = Field(description="총 평가자산 (예수금 + 보유 주식 평가액)")


# ---------------------------------------------------------------- 주문·체결
class OrderIn(BaseModel):
    stock_code: str = Field(examples=["HBS"])
    side: Literal["BUY", "SELL"]
    qty: int = Field(ge=1, le=10**9)
    client_order_id: str | None = Field(None, max_length=64,
                                        description="같은 값으로 다시 보내면 새로 체결하지 않고 처음 결과를 돌려줌 (재시도 안전)")


class TradeOut(ORM):
    id: int
    order_id: int
    stock_code: str
    side: str
    qty: int
    price: int
    amount: int
    fee: int
    tax: int
    realized_pnl: int | None
    cash_after: int
    game_day: int
    game_tick: int
    executed_at: datetime

    @computed_field(description="게임 속 체결 시각")
    @property
    def time(self) -> str:
        return E.clock(self.game_tick)


class OrderOut(ORM):
    id: int
    stock_code: str | None
    side: str
    qty: int
    status: Literal["FILLED", "REJECTED"]
    reject_code: str | None = None
    reject_message: str | None = None
    client_order_id: str | None = None
    game_day: int
    game_tick: int
    created_at: datetime
    trade: TradeOut | None = None


# ---------------------------------------------------------------- 포트폴리오·시장
class HoldingOut(BaseModel):
    stock_code: str
    name: str
    qty: int
    avg_price: float
    price: int
    value: int
    unrealized_pnl: int
    unrealized_pct: float


class PortfolioOut(BaseModel):
    game_id: int
    day: int
    cash: int
    stock_value: int
    total: int
    start_cash: int
    return_pct: float
    realized_pnl: int
    holdings: list[HoldingOut]


class StockQuote(BaseModel):
    code: str
    name: str
    sector: str
    beta: float
    price: int
    prev_close: int
    change: int
    change_pct: float
    held_qty: int


class Candle(ORM):
    day: int
    open: int
    high: int
    low: int
    close: int


class StockDetail(StockQuote):
    description: str
    hq: str
    sensitivity: dict[str, float] = Field(description="업종 민감도 (rate·fx·oil·econ이 +1 움직일 때 %)")
    candles: list[Candle]
    intraday: list[int] = Field(description="오늘 5분 가격 (09:00부터)")


class NewsOut(ORM):
    seq: int
    day: int
    time: str
    kind: str
    tag: str
    factor: str | None
    title: str
    body: str
    why: str
    place: str | None
    lat: float | None
    lon: float | None
    domestic: bool
    stock_codes: list[str]


class HistoryPoint(ORM):
    day: int
    total: int
    cash: int
    stock_value: int


class LeaderRow(BaseModel):
    game_id: int
    nickname: str
    experience: str
    total: int
    pct: float
    trades: int
    persona: list[str] | None
    finished_at: datetime
