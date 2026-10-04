"""게임 REST API

상태를 바꾸는 요청: POST /games, /tick, /next-day, /orders
읽기 요청: 나머지 GET. 기록(뉴스·체결·주문·일별 자산)은 PostgreSQL에서, 지금 가격·시각은 Redis에서 읽는다.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from simulator import analysis as A
from simulator import engine as E

from ..db import get_session
from ..models import User
from ..schemas import (Candle, ErrorResponse, GameDetail, GameIn, GameOut, HistoryPoint, LeaderRow, NewsOut, OrderIn,
                       OrderOut, PortfolioOut, StockDetail, StockQuote, TradeOut)
from ..security import current_user
from ..services.games import GameService, leaderboard, week_ago
from .deps import game_service

router = APIRouter(prefix="/api/v1", tags=["games"])
NOT_FOUND = {404: {"model": ErrorResponse, "description": "없는 게임 (남의 게임도 404)"}}
# 틱은 초당 여러 번 오는 요청이라 본문 없이 쿼리로 받는다. 본문이 있으면 헤더·본문이 따로 가는 클라이언트에서
# 지연 ACK(약 40ms)에 걸린다 (docs/performance.md)
SINCE = Query(0, ge=0, description="이 번호 이후의 뉴스만 받음")


def game_detail(g, st):
    return GameDetail(**GameOut.model_validate(g).model_dump(), tick=st["tick"], clock=E.clock(st["tick"]),
                      phase=st["phase"], total=E.total_value(st))


def quote(st, code):
    s, cur, prev = E.STOCK[code], E.price(st, code), E.prev_close(st, code)
    return dict(code=code, name=s["name"], sector=s["sector"], beta=s["beta"], price=cur, prev_close=prev,
                change=cur - prev, change_pct=round((cur - prev) / prev * 100, 2),
                held_qty=st["holdings"].get(code, {}).get("qty", 0))


def news_out(n):
    return NewsOut(seq=n.seq, day=n.day, time=n.time, kind=n.kind, tag=n.tag, factor=n.factor, title=n.title,
                   body=n.body, why=n.why, place=n.place, lat=float(n.lat) if n.lat is not None else None,
                   lon=float(n.lon) if n.lon is not None else None, domestic=n.domestic,
                   stock_codes=[s.stock_code for s in n.stocks])


# ---------------------------------------------------------------- 게임
@router.post("/games", status_code=201, response_model=GameDetail,
             summary="새 게임 시작", description="진행 중이던 게임은 포기(ABANDONED) 처리된다. 본문이 없으면 15거래일.")
def create_game(body: GameIn | None = None, user: User = Depends(current_user),
                svc: GameService = Depends(game_service)):
    return game_detail(*svc.create(user, (body or GameIn()).days))


@router.get("/games/current", response_model=GameDetail, responses=NOT_FOUND, summary="진행 중(또는 마지막) 게임")
def current_game(user: User = Depends(current_user), svc: GameService = Depends(game_service)):
    g = svc.active_game(user) or svc.latest_game(user)
    if not g:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "게임이 없습니다."})
    return game_detail(g, svc.state(g, locked=False))


@router.get("/games/{game_id}", response_model=GameDetail, responses=NOT_FOUND)
def get_game(game_id: int, user: User = Depends(current_user), svc: GameService = Depends(game_service)):
    return game_detail(*svc.view(game_id, user))


@router.get("/games/{game_id}/state", responses=NOT_FOUND, summary="화면용 전체 상태",
            description="시세·지표·보유·새 뉴스를 한 번에. 앞으로 일어날 사건 계획은 포함되지 않는다.")
def get_state(game_id: int, since: int = Query(0, ge=0), full: bool = False,
              user: User = Depends(current_user), svc: GameService = Depends(game_service)):
    g, st = svc.view(game_id, user)
    return {"game_id": g.id, **E.snapshot(st, since, full=full)}


@router.post("/games/{game_id}/tick", responses=NOT_FOUND, summary="시간 5분 진행",
             description="장중이면 5분이 지나고 그 시각의 사건이 공개된다. 15:30이 되면 장이 마감된다.")
def tick(game_id: int, since: int = SINCE, user: User = Depends(current_user),
         svc: GameService = Depends(game_service)):
    g, st = svc.tick(game_id, user)
    return {"game_id": g.id, **E.snapshot(st, since)}


@router.post("/games/{game_id}/next-day", responses={**NOT_FOUND, 409: {"model": ErrorResponse}},
             summary="다음 거래일 장 시작")
def next_day(game_id: int, since: int = SINCE, user: User = Depends(current_user),
             svc: GameService = Depends(game_service)):
    g, st = svc.next_day(game_id, user)
    return {"game_id": g.id, **E.snapshot(st, since, full=True)}


# ---------------------------------------------------------------- 시장
@router.get("/games/{game_id}/stocks", response_model=list[StockQuote], responses=NOT_FOUND)
def list_stocks(game_id: int, user: User = Depends(current_user), svc: GameService = Depends(game_service)):
    _, st = svc.view(game_id, user)
    return [quote(st, c) for c in E.STOCK]


@router.get("/games/{game_id}/stocks/{code}", response_model=StockDetail, responses=NOT_FOUND)
def get_stock(game_id: int, code: str, user: User = Depends(current_user), svc: GameService = Depends(game_service)):
    if code not in E.STOCK:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "없는 종목입니다."})
    _, st = svc.view(game_id, user)
    s = E.STOCK[code]
    return StockDetail(**quote(st, code), description=s["desc"], hq=E.HQ[code][0], sensitivity=E.SECTORS[s["sector"]],
                       candles=[Candle.model_validate(p) for p in svc.daily_prices(game_id, user, code)],
                       intraday=st["intra"][code])


@router.get("/games/{game_id}/news", response_model=list[NewsOut], responses=NOT_FOUND,
            summary="공개된 뉴스", description="`since` 이후 번호만. 아직 공개되지 않은 사건은 저장되어 있지 않다.")
def list_news(game_id: int, since: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500),
              user: User = Depends(current_user), svc: GameService = Depends(game_service)):
    return [news_out(n) for n in svc.list_news(game_id, user, since, limit)]


# ---------------------------------------------------------------- 주문·원장
@router.post("/games/{game_id}/orders", status_code=201, response_model=OrderOut,
             responses={**NOT_FOUND, 200: {"model": OrderOut, "description": "같은 client_order_id로 이미 처리된 주문"},
                        422: {"model": OrderOut, "description": "거부된 주문 (사유와 함께 기록됨)"}},
             summary="시장가 주문",
             description="지금 가격으로 즉시 체결된다. 예수금·보유 수량 부족, 장 마감 등으로 거부되면 422와 함께 "
                         "거부 사유(reject_code)를 돌려주고, 거부된 주문도 기록으로 남는다.")
def place_order(game_id: int, body: OrderIn, user: User = Depends(current_user),
                svc: GameService = Depends(game_service)):
    order, fresh = svc.place_order(game_id, user, body.stock_code, body.side, body.qty, body.client_order_id)
    out = OrderOut.model_validate(order)
    if not fresh:
        return JSONResponse(out.model_dump(mode="json"), status_code=200)
    if order.status == "REJECTED":
        return JSONResponse(out.model_dump(mode="json"), status_code=422)
    return out


@router.get("/games/{game_id}/orders", response_model=list[OrderOut], responses=NOT_FOUND)
def list_orders(game_id: int, user: User = Depends(current_user), svc: GameService = Depends(game_service)):
    return svc.list_orders(game_id, user)


@router.get("/games/{game_id}/trades", response_model=list[TradeOut], responses=NOT_FOUND)
def list_trades(game_id: int, user: User = Depends(current_user), svc: GameService = Depends(game_service)):
    return svc.list_trades(game_id, user)


@router.get("/games/{game_id}/portfolio", response_model=PortfolioOut, responses=NOT_FOUND)
def portfolio(game_id: int, user: User = Depends(current_user), svc: GameService = Depends(game_service)):
    return svc.portfolio(game_id, user)


@router.get("/games/{game_id}/history", response_model=list[HistoryPoint], responses=NOT_FOUND,
            summary="장 마감 기준 일별 자산")
def history(game_id: int, user: User = Depends(current_user), svc: GameService = Depends(game_service)):
    return svc.history(game_id, user)


@router.get("/games/{game_id}/analysis", responses=NOT_FOUND, summary="투자 성향 분석",
            description="코칭 문구의 눈높이와 개수는 회원의 투자 경험(experience)을 따른다. `?level=`로 바꿔 볼 수 있다")
def analysis(game_id: int, level: Literal["beginner", "intermediate", "expert"] | None = None,
             user: User = Depends(current_user), svc: GameService = Depends(game_service)):
    _, st = svc.view(game_id, user)
    return A.analyze(st, level or user.experience)


@router.get("/leaderboard", response_model=list[LeaderRow], tags=["leaderboard"], summary="명예의 전당")
def get_leaderboard(limit: int = Query(10, ge=1, le=100),
                    experience: Literal["beginner", "intermediate", "expert"] | None = Query(
                        None, description="이 투자 경험의 회원만"),
                    period: Literal["all", "week"] = Query("all", description="week = 최근 7일 안에 끝난 게임"),
                    days: int = Query(E.DEFAULT_DAYS, description="게임 길이 (거래일: 15 또는 30). 길이가 같은 게임끼리만 순위를 매긴다"),
                    db=Depends(get_session)):
    if days not in E.GAME_DAYS:
        raise HTTPException(422, detail={"code": "INVALID_DAYS", "message": "게임 길이는 15 또는 30입니다."})
    return leaderboard(db, limit, experience, week_ago() if period == "week" else None, days)
