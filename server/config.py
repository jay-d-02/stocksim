"""환경변수 설정. docker compose가 채워 주고, 없으면 로컬 개발용 기본값"""
import logging
import os
from dataclasses import dataclass
from decimal import Decimal

log = logging.getLogger("config")
DEV_SECRET = "dev-only-change-me"


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    secret_key: str
    start_cash: int
    fee_rate: Decimal
    tax_rate: Decimal
    secure_cookies: bool
    # 비밀번호 찾기 메일. SMTP_HOST가 비어 있으면 메일 대신 서버 로그에 링크를 남긴다 (개발용)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    public_url: str = ""          # 메일 속 링크의 주소 (예: https://market-sim.xxxx.ts.net). 비우면 요청 주소
    privacy_contact: str = ""     # 개인정보 문의처 (개인정보 처리방침 화면에 표시)
    # 실제 뉴스 (NAVER API HUB 뉴스 검색). 비워 두면 '오늘의 뉴스' 화면은 설정 안내만 보인다
    naver_client_id: str = ""
    naver_client_secret: str = ""
    news_daily_cap: int = 500     # 하루 API 호출 상한 (무료 한도: 월 775,000건)


def load():
    s = Settings(
        database_url=os.environ.get("DATABASE_URL", "postgresql+psycopg://stocksim:stocksim@localhost:5432/market_sim"),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        secret_key=os.environ.get("SECRET_KEY") or DEV_SECRET,
        start_cash=int(os.environ.get("START_CASH", 10_000_000)),
        fee_rate=Decimal(os.environ.get("FEE_RATE", "0.00015")),
        tax_rate=Decimal(os.environ.get("TAX_RATE", "0.002")),
        secure_cookies=os.environ.get("SECURE_COOKIES", "false").lower() == "true",
        smtp_host=os.environ.get("SMTP_HOST", ""),
        smtp_port=int(os.environ.get("SMTP_PORT") or 587),
        smtp_user=os.environ.get("SMTP_USER", ""),
        smtp_password=os.environ.get("SMTP_PASSWORD", ""),
        smtp_from=os.environ.get("SMTP_FROM", "") or os.environ.get("SMTP_USER", ""),
        public_url=os.environ.get("PUBLIC_URL", "").rstrip("/"),
        privacy_contact=os.environ.get("PRIVACY_CONTACT", ""),
        naver_client_id=os.environ.get("NAVER_CLIENT_ID", "").strip(),
        naver_client_secret=os.environ.get("NAVER_CLIENT_SECRET", "").strip(),
        news_daily_cap=int(os.environ.get("NEWS_DAILY_CAP") or 500),
    )
    if s.secret_key == DEV_SECRET:
        log.warning("SECRET_KEY가 설정되지 않아 개발용 키를 씁니다. 외부에 공개하기 전에 .env에 설정하세요.")
    elif len(s.secret_key) < 32:
        log.warning("SECRET_KEY가 %d자로 짧습니다. 32자 이상 랜덤 문자열을 쓰세요 "
                    "(python -c \"import secrets; print(secrets.token_urlsafe(48))\").", len(s.secret_key))
    if not s.secure_cookies:
        log.warning("SECURE_COOKIES=false: HTTPS로 공개한다면 .env에 SECURE_COOKIES=true를 설정하세요.")
    return s


settings = load()
