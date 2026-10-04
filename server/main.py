"""FastAPI 앱 조립: 미들웨어·라우터·예외 처리·정적 파일

실행: uvicorn server.main:app
API 문서: /docs (Swagger UI), /redoc
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from .api import admin as admin_api
from .api import auth as auth_api
from .api import games as games_api
from .config import settings
from .db import SessionLocal
from .runtime import get_store
from .security import AdminRequired, LoginRequired
from .services.accounts import AccountError
from .services.games import Conflict, NotFound, sync_stocks
from .web import pages, templating

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app):
    with SessionLocal() as db:
        sync_stocks(db)              # 종목 마스터를 catalog.py 기준으로 맞춤
    yield


app = FastAPI(
    title="Event-driven Stock Market Simulator",
    summary="가상 경제 이벤트 기반 주식시장 시뮬레이터",
    description="금리·환율·유가·실적·루머 같은 사건이 5분 단위로 공개되며 12개 가상 종목의 가격을 움직입니다. "
                "주문은 PostgreSQL 트랜잭션으로 처리되고, 진행 중인 시장 상태는 Redis에 있습니다.\n\n"
                "인증: `/api/v1/auth/login` 이 세션 쿠키를 발급합니다 (Swagger UI에서도 로그인 후 그대로 호출 가능).",
    version="2.0.0",
    lifespan=lifespan,
    openapi_tags=[{"name": "auth", "description": "가입·로그인"},
                  {"name": "games", "description": "게임 진행·시세·주문·원장"},
                  {"name": "leaderboard", "description": "명예의 전당"},
                  {"name": "admin", "description": "관리자 전용 (permit = ADMIN): 회원 정보 열람·권한 변경"}],
)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax",
                   https_only=settings.secure_cookies, max_age=30 * 24 * 3600, session_cookie="sim_session")
app.mount("/static", StaticFiles(directory=str(templating.ROOT / "static")), name="static")
app.include_router(auth_api.router)
app.include_router(games_api.router)
app.include_router(admin_api.router)
app.include_router(pages.router)
templating.bind(app, auth_api.router, games_api.router, admin_api.router, pages.router)


def _is_api(request):
    return request.url.path.startswith("/api/")


@app.exception_handler(NotFound)
def not_found(request: Request, exc):
    if _is_api(request):
        return JSONResponse({"detail": {"code": "NOT_FOUND", "message": "없는 게임입니다."}}, status_code=404)
    return RedirectResponse("/game", status_code=303)


@app.exception_handler(Conflict)
def conflict(request: Request, exc: Conflict):
    if _is_api(request):
        return JSONResponse({"detail": {"code": exc.code, "message": exc.message}}, status_code=409)
    templating.flash(request, exc.message, "err")
    return RedirectResponse("/game", status_code=303)


@app.exception_handler(AccountError)
def account_error(request: Request, exc: AccountError):
    """API만 여기로 온다 (화면은 pages.py에서 직접 잡아 알림으로 보여 줌)"""
    return auth_api.error_response(exc)


@app.exception_handler(LoginRequired)
def login_required(request: Request, exc):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(AdminRequired)
def admin_required(request: Request, exc):
    templating.flash(request, "관리자만 볼 수 있는 화면입니다.", "err")
    return RedirectResponse("/game", status_code=303)


@app.get("/health", tags=["ops"], summary="DB·Redis 연결 확인")
def health():
    status = {}
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        status["postgres"] = "ok"
    except Exception as e:                  # noqa: BLE001 — 헬스체크는 원인을 그대로 보여 준다
        status["postgres"] = f"error: {e.__class__.__name__}"
    try:
        get_store().ping()
        status["redis"] = "ok"
    except Exception as e:                  # noqa: BLE001
        status["redis"] = f"error: {e.__class__.__name__}"
    ok = all(v == "ok" for v in status.values())
    return JSONResponse({"status": "ok" if ok else "degraded", **status}, status_code=200 if ok else 503)
