"""HTML 화면. 비즈니스 로직은 전부 GameService에 있고 여기선 화면에 맞게 모으기만 한다"""
import json

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import ValidationError

from simulator import analysis as A
from simulator import engine as E
from simulator.ledger import max_buy_qty

from .. import mail
from ..api.auth import base_url
from ..api.deps import game_service
from ..config import settings
from ..db import SessionLocal, get_session
from ..models import EXPERIENCE, PERMITS
from ..ratelimit import client_ip
from ..runtime import get_store
from ..schemas import PASSWORD_MIN, PasswordChangeIn, ProfileIn, RegisterIn, ResetIn, ResetRequestIn
from ..security import (admin_page_user, login_session, logout_session, optional_user, page_user,
                        user_from_reset_token)
from ..services import accounts as AC
from ..services import realnews as RN
from ..services.games import GameService, leaderboard, week_ago
from .templating import ROOT, flash, render, url_for

router = APIRouter(include_in_schema=False)
FEE, TAX = float(settings.fee_rate), float(settings.tax_rate)
ADMIN_PER_PAGE = 50


def go(name, **kw):
    return RedirectResponse(url_for(name, **kw), status_code=303)


def hall(db, days):
    return leaderboard(db, 10, days=days)


def game_days(value, default=E.DEFAULT_DAYS):
    """폼·주소의 게임 길이 → E.GAME_DAYS 중 하나 (이상한 값이면 기본값)"""
    try:
        days = int(value)
    except (TypeError, ValueError):
        return default
    return days if days in E.GAME_DAYS else default


# ---------------------------------------------------------------- 로그인
@router.get("/")
def index():
    return go("game_home")


# 로그인 화면 위쪽에 흐르는 시세 띠 (장식용: 기준가와 코드로 정한 고정 등락률)
TICKER = [{"name": s["name"], "price": s["base"], "pct": (sum(map(ord, c)) * 37 % 61 - 30) / 10}
          for c, s in E.STOCK.items()]


def auth_page(request, mode, form=None, status_code=200):
    return render(request, "auth.html", mode=mode, form=form or {}, experience=EXPERIENCE, ticker=TICKER,
                  start_cash=settings.start_cash, status_code=status_code)


@router.get("/login")
def login(request: Request, user=Depends(optional_user)):
    return go("game_home") if user else auth_page(request, "login")


@router.post("/login")
def login_post(request: Request, username: str = Form(""), password: str = Form(""), db=Depends(get_session)):
    try:
        user = AC.login(db, username, password, client_ip(request))
    except AC.AccountError as e:
        flash(request, e.message, "err")
        return auth_page(request, "login", {"username": username}, e.status)
    login_session(request, user)
    return go("game_home")


@router.get("/register")
def register(request: Request):
    return auth_page(request, "register")


# 입력 검증에 걸린 칸 → 화면 문장
FIELD_HINT = {"username": "아이디는 3~30자로 입력하세요.",
              "password": f"비밀번호는 {PASSWORD_MIN}자 이상으로 입력하세요.",
              "new_password": f"새 비밀번호는 {PASSWORD_MIN}자 이상으로 입력하세요.",
              "nickname": "닉네임은 2~20자로 입력하세요.", "email": "이메일 형식이 맞지 않습니다.",
              "experience": "투자 경험을 골라 주세요.",
              "agree_privacy": "개인정보 수집·이용에 동의해야 가입할 수 있습니다."}


def form_error(e: ValidationError):
    """Pydantic 검증 오류 → 화면에 보일 한 문장"""
    err = e.errors()[0]
    field = err["loc"][0] if err["loc"] else None
    return FIELD_HINT.get(field) or err["msg"].removeprefix("Value error, ")


@router.post("/register")
def register_post(request: Request, username: str = Form(""), password: str = Form(""), nickname: str = Form(""),
                  experience: str = Form("beginner"), email: str = Form(""), agree: str = Form(""),
                  db=Depends(get_session)):
    form = {"username": username, "nickname": nickname, "experience": experience, "email": email, "agree": agree}
    try:
        body = RegisterIn(username=username.strip(), password=password, nickname=nickname,
                          experience=experience, email=email, agree_privacy=agree == "on")
        user = AC.register(db, body, client_ip(request))
    except ValidationError as e:
        flash(request, form_error(e), "err")
        return auth_page(request, "register", form, 422)
    except AC.AccountError as e:
        flash(request, e.message, "err")
        return auth_page(request, "register", form, e.status)
    login_session(request, user)
    flash(request, f"{user.nickname}님, 환영합니다! 가상자금 {settings.start_cash:,}원으로 시작해 보세요.")
    return go("game_home")


@router.get("/logout")
def logout(request: Request):
    logout_session(request)
    return go("login")


@router.get("/privacy")
def privacy(request: Request, user=Depends(optional_user)):
    return render(request, "privacy.html", user=user, contact=settings.privacy_contact)


# ---------------------------------------------------------------- 비밀번호 찾기
@router.get("/forgot")
def forgot(request: Request):
    return render(request, "forgot.html", mail_on=mail.enabled())


@router.post("/forgot")
def forgot_post(request: Request, tasks: BackgroundTasks, email: str = Form(""), db=Depends(get_session)):
    try:
        body = ResetRequestIn(email=email.strip())
        tasks.add_task(AC.send_reset_mail, AC.request_reset(db, body.email, client_ip(request), base_url(request)))
    except ValidationError:
        flash(request, "이메일 형식이 맞지 않습니다.", "err")
        return render(request, "forgot.html", mail_on=mail.enabled(), email=email, status_code=422)
    except AC.AccountError as e:
        flash(request, e.message, "err")
        return render(request, "forgot.html", mail_on=mail.enabled(), email=email, status_code=e.status)
    return render(request, "forgot.html", mail_on=mail.enabled(), sent=True)


@router.get("/reset")
def reset(request: Request, token: str = "", db=Depends(get_session)):
    return render(request, "reset.html", token=token, valid=user_from_reset_token(db, token) is not None)


@router.post("/reset")
def reset_post(request: Request, token: str = Form(""), password: str = Form(""), password2: str = Form(""),
               db=Depends(get_session)):
    page = lambda status: render(request, "reset.html", token=token, valid=True, status_code=status)
    if password != password2:
        flash(request, "새 비밀번호 두 칸이 서로 다릅니다.", "err")
        return page(422)
    try:
        body = ResetIn(token=token, new_password=password)
        user = AC.reset_password(db, body.token, body.new_password)
    except ValidationError as e:
        flash(request, form_error(e), "err")
        return page(422)
    except AC.AccountError as e:
        flash(request, e.message, "err")
        return render(request, "reset.html", token=token, valid=e.code != "INVALID_TOKEN", status_code=e.status)
    login_session(request, user)
    flash(request, "비밀번호를 바꿨어요. 다른 기기의 로그인은 모두 끝났습니다.")
    return go("game_home")


# ---------------------------------------------------------------- 내 정보
def account_page(request, user, status_code=200):
    return render(request, "account.html", user=user, experience=EXPERIENCE, mail_on=mail.enabled(),
                  status_code=status_code)


@router.get("/account")
def account(request: Request, user=Depends(page_user)):
    return account_page(request, user)


@router.post("/account/profile")
def account_profile(request: Request, nickname: str = Form(""), experience: str = Form(""), email: str = Form(""),
                    user=Depends(page_user), db=Depends(get_session)):
    try:
        body = ProfileIn(nickname=nickname, experience=experience or None, email=email)
        AC.update_profile(db, user, body)
    except ValidationError as e:
        flash(request, form_error(e), "err")
        return account_page(request, user, 422)
    except AC.AccountError as e:
        db.refresh(user)
        flash(request, e.message, "err")
        return account_page(request, user, e.status)
    flash(request, "내 정보를 저장했어요.")
    return go("account")


@router.post("/account/password")
def account_password(request: Request, current: str = Form(""), password: str = Form(""), password2: str = Form(""),
                     user=Depends(page_user), db=Depends(get_session)):
    if password != password2:
        flash(request, "새 비밀번호 두 칸이 서로 다릅니다.", "err")
        return account_page(request, user, 422)
    try:
        body = PasswordChangeIn(current_password=current, new_password=password)
        AC.change_password(db, user, body.current_password, body.new_password)
    except ValidationError as e:
        flash(request, form_error(e), "err")
        return account_page(request, user, 422)
    except AC.AccountError as e:
        flash(request, e.message, "err")
        return account_page(request, user, e.status)
    login_session(request, user)
    flash(request, "비밀번호를 바꿨어요. 다른 기기의 로그인은 모두 끝났습니다.")
    return go("account")


@router.post("/account/delete")
def account_delete(request: Request, password: str = Form(""), confirm: str = Form(""),
                   user=Depends(page_user), db=Depends(get_session)):
    if confirm != "on":
        flash(request, "탈퇴하려면 안내를 확인하고 체크해 주세요.", "err")
        return account_page(request, user, 422)
    try:
        AC.delete_account(db, get_store(), user, password)
    except AC.AccountError as e:
        flash(request, e.message, "err")
        return account_page(request, user, e.status)
    logout_session(request)
    flash(request, "탈퇴했습니다. 아이디·닉네임·이메일을 지웠어요. 그동안 고마웠어요!")
    return go("login")


# ---------------------------------------------------------------- 관리자 (permit = 'ADMIN')
@router.get("/admin/users")
def admin_users(request: Request, q: str = "", status: str = "Y", permit: str = "", page: int = 1,
                admin=Depends(admin_page_user), db=Depends(get_session)):
    status = status if status in ("Y", "N", "all") else "Y"
    page = max(1, page)
    total, rows = AC.list_users(db, q[:100], status, permit or None, page, ADMIN_PER_PAGE)
    return render(request, "admin_users.html", user=admin, rows=rows, total=total, q=q, status=status, permit=permit,
                  page=page, pages=max(1, -(-total // ADMIN_PER_PAGE)), experience=EXPERIENCE, permits=PERMITS)


@router.get("/admin/users/{user_id}")
def admin_user_detail(user_id: int, request: Request, admin=Depends(admin_page_user), db=Depends(get_session)):
    try:
        target = AC.get_user(db, user_id)
    except AC.AccountError as e:
        flash(request, e.message, "err")
        return go("admin_users")
    return render(request, "admin_user.html", user=admin, target=target, games=AC.user_games(db, user_id),
                  experience=EXPERIENCE, permits=PERMITS)


@router.post("/admin/users/{user_id}/permit")
def admin_set_permit(user_id: int, request: Request, permit: str = Form(""),
                     admin=Depends(admin_page_user), db=Depends(get_session)):
    try:
        target = AC.set_permit(db, admin, AC.get_user(db, user_id), permit)
        flash(request, f"{target.display_name}님의 권한을 '{PERMITS[target.permit]}'(으)로 바꿨어요.")
    except AC.AccountError as e:
        flash(request, e.message, "err")
    return go("admin_user_detail", user_id=user_id)


@router.post("/admin/users/{user_id}/withdraw")
def admin_withdraw(user_id: int, request: Request, confirm: str = Form(""),
                   admin=Depends(admin_page_user), db=Depends(get_session)):
    if confirm != "on":
        flash(request, "탈퇴 처리하려면 안내를 확인하고 체크해 주세요.", "err")
        return go("admin_user_detail", user_id=user_id)
    try:
        AC.admin_withdraw(db, get_store(), admin, AC.get_user(db, user_id))
        flash(request, f"회원 #{user_id}을(를) 탈퇴 처리했어요. 개인정보를 지웠습니다.")
    except AC.AccountError as e:
        flash(request, e.message, "err")
    return go("admin_user_detail", user_id=user_id)


# ---------------------------------------------------------------- 게임
def my_game(svc: GameService, user):
    """진행 중인 게임, 없으면 마지막 게임(끝난 결과 화면), 그것도 없으면 새로 만듦"""
    g = svc.active_game(user) or svc.latest_game(user)
    if g is None or g.status == "ABANDONED":
        g, _ = svc.create(user)
    return g


@router.get("/game")
def game_home(request: Request, user=Depends(page_user), svc: GameService = Depends(game_service)):
    g = my_game(svc, user)
    st = svc.state(g, locked=False)
    return render(request, "game.html", user=user, game_id=g.id, snap=E.snapshot(st, full=True),
                  hall=hall(svc.db, g.max_day), game_days=E.GAME_DAYS, fee_rate=FEE, tax_rate=TAX,
                  tour_auto=user.experience == "beginner")      # 초보는 첫 방문 때 사용법 안내를 자동으로


@router.post("/game/new")
def game_new(request: Request, days: str = Form(""), user=Depends(page_user),
             svc: GameService = Depends(game_service)):
    days = game_days(days)
    svc.create(user, days)
    flash(request, f"{days}거래일 새 게임을 시작했습니다. 가상자금 {settings.start_cash:,}원!")
    return go("game_home")


@router.get("/game/stocks/{code}")
def game_stock(code: str, request: Request, user=Depends(page_user), svc: GameService = Depends(game_service)):
    if code not in E.STOCK:
        return go("game_home")
    g = my_game(svc, user)
    st = svc.state(g, locked=False)
    s = E.STOCK[code]
    cur, prev = E.price(st, code), E.prev_close(st, code)
    hist = st["prices"][code] + ([cur] if st["phase"] == "open" else [])
    news = [n for n in st["news"] if code in n["codes"]
            or (not n["codes"] and n["kind"] in ("event", "preview"))][::-1][:15]
    fees = svc.fees(g)
    return render(request, "game_stock.html", user=user, st=st, s=s, code=code, cur=cur, diff=cur - prev,
                  pct=(cur - prev) / prev * 100, holding=st["holdings"].get(code),
                  max_buy=max_buy_qty(st["cash"], cur, fees),
                  labels=[E.day_label(i) for i in range(len(hist))], prices=hist,
                  exposure=E.SECTORS[s["sector"]], factors=E.FACTORS, news=news, stocks=E.STOCK,
                  fee_rate=FEE, tax_rate=TAX, my_trades=[t for t in st["trades"] if t["code"] == code][::-1])


@router.post("/game/stocks/{code}/order")
def game_trade(code: str, request: Request, side: str = Form(""), qty: str = Form("0"),
               user=Depends(page_user), svc: GameService = Depends(game_service)):
    try:
        n = int(qty)
    except ValueError:
        n = 0
    if side not in ("BUY", "SELL") or n <= 0:
        flash(request, "수량을 1주 이상으로 입력하세요.", "err")
        return go("game_stock", code=code)
    g = my_game(svc, user)
    order, _ = svc.place_order(g.id, user, code, side, n)
    if order.status == "REJECTED":
        flash(request, order.reject_message, "err")
    else:
        t = order.trade
        name = E.STOCK[code]["name"]
        flash(request, f"{name} {n:,}주를 {t.price:,}원에 {'매수' if side == 'BUY' else '매도'}했습니다."
              + (f" 실현손익 {t.realized_pnl:+,}원" if t.realized_pnl is not None else ""))
    return go("game_stock", code=code)


@router.get("/game/news")
def game_news(request: Request, user=Depends(page_user), svc: GameService = Depends(game_service)):
    g = my_game(svc, user)
    st = svc.state(g, locked=False)
    return render(request, "game_news.html", user=user, st=st, news=st["news"][::-1], stocks=E.STOCK)


@router.get("/game/style")
def game_style(request: Request, user=Depends(page_user), svc: GameService = Depends(game_service)):
    g = my_game(svc, user)
    st = svc.state(g, locked=False)
    return render(request, "style.html", user=user, a=A.analyze(st, user.experience), st=st, past=svc.past_games(user),
                  fmt_hold=A.fmt_hold, min_trades=A.MIN_TRADES, experience=EXPERIENCE)


@router.post("/game/style/level")
def game_style_level(request: Request, experience: str = Form(""), user=Depends(page_user), db=Depends(get_session)):
    """투자 성향 화면에서 코칭 눈높이(투자 경험) 바꾸기"""
    if experience in EXPERIENCE:
        user.experience = experience
        db.commit()
        flash(request, f"코칭을 '{EXPERIENCE[experience]}' 눈높이로 바꿨어요.")
    return go("game_style")


@router.get("/ranking")
def ranking(request: Request, period: str = "all", level: str = "all", days: str = "",
            user=Depends(page_user), db=Depends(get_session)):
    period = period if period in ("all", "week") else "all"
    level = level if level in EXPERIENCE else "all"
    days = game_days(days)
    board = leaderboard(db, 50, None if level == "all" else level, week_ago() if period == "week" else None, days)
    return render(request, "ranking.html", user=user, board=board, days=days, game_days=E.GAME_DAYS,
                  period=period, level=level, experience=EXPERIENCE)


@router.get("/game/replay/{game_id}")
def game_replay(game_id: int, request: Request, user=Depends(page_user), svc: GameService = Depends(game_service)):
    r = svc.replay(game_id, user)
    g = r["game"]
    mkt = r["curve"][-1]["mkt"] if len(r["curve"]) > 1 else 0.0
    return render(request, "replay.html", user=user, g=g, pct=float(g.final_return_pct), mkt=mkt,
                  data={k: r[k] for k in ("curve", "codes", "candles", "news", "trades")},
                  names={c: E.STOCK[c]["name"] for c in r["codes"]}, persona=(g.profile or {}).get("persona"))


# ---------------------------------------------------------------- 투자 공부
@router.get("/learn")
def learn(request: Request, user=Depends(page_user)):
    scenarios = [{"key": e["key"], "title": e["title"], "why": e["why"], "factor": e["factor"],
                  "tag": E.TAGS.get(e["factor"], "시장"), "impacts": E.event_impacts(e)} for e in E.MACRO_EVENTS]
    corp = [{"key": e["key"], "title": e["title"].replace("{name}", "A사"), "move": e["move"],
             "why": e["why"], "factor": e["factor"]} for e in E.CORP_EVENTS]
    stocks = {c: {"name": s["name"], "sector": s["sector"], "beta": s["beta"]} for c, s in E.STOCK.items()}
    ev = {e["key"]: e for e in E.MACRO_EVENTS}
    # 실험실 슬라이더 단위 → 엔진의 요인 충격 (게임 사건과 같은 크기가 되도록 사건 정의에서 역산)
    units = {"fx": ev["fx_up"]["ind"]["fx"] / ev["fx_up"]["shock"]["fx"],
             "oil": ev["oil_up"]["ind"]["oil"] / ev["oil_up"]["shock"]["oil"],
             "econ": ev["econ_up"]["shock"]["econ"], "rate": E.RATE_SHOCK, "rate_mkt": E.RATE_MARKET}
    return render(request, "learn.html", user=user, scenarios=scenarios, corp=corp, stocks=stocks,
                  sectors=E.SECTORS, factors=E.FACTORS, units=units, fee_rate=FEE, tax_rate=TAX)


@router.get("/factors")
def factors(request: Request, user=Depends(page_user)):
    return render(request, "factors.html", user=user, sectors=E.SECTORS, factors=E.FACTORS, stocks=E.STOCK)


@router.get("/learn/news")
def learn_news(request: Request, tasks: BackgroundTasks, factor: str = "", user=Depends(page_user),
               db=Depends(get_session)):
    """실제 뉴스를 게임의 요인·업종 민감도로 읽어 보기. 오래됐으면 응답 뒤에 새로 가져옴"""
    store = get_store()
    factor = factor if factor in E.FACTORS else ""
    if settings.naver_client_id and RN.needs_refresh(store):
        tasks.add_task(RN.refresh_in_background, SessionLocal, store, settings)
    items = [{"n": n, "factors": RN.describe(n.factors), "impacts": sorted(RN.sector_impacts(n.factors).items(),
                                                                          key=lambda x: -x[1])}
             for n in RN.recent(db, factor or None)]
    return render(request, "learn_news.html", user=user, items=items, factor=factor, factors=E.FACTORS,
                  status=RN.status(store, settings), refresh_min=RN.REFRESH_SEC // 60)


# ---------------------------------------------------------------- 앱(PWA)
@router.get("/manifest.webmanifest")
def manifest():
    icon = lambda f, size, purpose: {"src": f"/static/icons/{f}", "sizes": size, "type": "image/png", "purpose": purpose}
    data = {
        "name": "Event-driven Stock Market Simulator", "short_name": "주식 시뮬레이터",
        "description": "가상 경제 이벤트로 움직이는 시장에서 투자 판단을 연습하는 시뮬레이터",
        "lang": "ko", "start_url": "/game", "scope": "/", "id": "/",
        "display": "standalone", "orientation": "portrait",
        "background_color": "#0B111C", "theme_color": "#18212F",
        "icons": [icon("icon-192.png", "192x192", "any"), icon("icon-512.png", "512x512", "any"),
                  icon("maskable-512.png", "512x512", "maskable")],
        "shortcuts": [{"name": "투자 게임", "url": "/game"}, {"name": "요인 실험실", "url": "/learn#lab"},
                      {"name": "내 투자 성향", "url": "/game/style"}],
    }
    return Response(json.dumps(data, ensure_ascii=False), media_type="application/manifest+json")


@router.get("/sw.js")
def service_worker():
    """서비스 워커는 사이트 전체를 다루려면 루트 경로에서 내려줘야 함"""
    return FileResponse(ROOT / "static" / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@router.get("/offline")
def offline(request: Request):
    return render(request, "offline.html")
