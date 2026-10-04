"""관리자 API: 회원 목록·상세, 권한 변경, 탈퇴 처리. permit = 'ADMIN'만 (아니면 403)"""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import User
from ..runtime import get_store
from ..schemas import AdminUserDetail, AdminUserOut, AdminUserPage, ErrorResponse, PermitIn
from ..security import admin_user
from ..services import accounts as AC

router = APIRouter(prefix="/api/v1/admin", tags=["admin"],
                   responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse, "description": "관리자 아님"}})
PER_PAGE = 50


@router.get("/users", response_model=AdminUserPage, summary="회원 목록")
def users(q: str = Query("", max_length=100, description="아이디·닉네임·이메일 일부"),
          status: Literal["Y", "N", "all"] = Query("Y", description="Y 사용 중 / N 탈퇴 / all"),
          permit: Literal["USER", "ADMIN"] | None = None, page: int = Query(1, ge=1),
          _: User = Depends(admin_user), db: Session = Depends(get_session)):
    total, rows = AC.list_users(db, q, status, permit, page, PER_PAGE)
    return {"total": total, "page": page, "items": rows}


@router.get("/users/{user_id}", response_model=AdminUserDetail, summary="회원 상세 (게임 기록 포함)",
            responses={404: {"model": ErrorResponse}})
def user_detail(user_id: int, _: User = Depends(admin_user), db: Session = Depends(get_session)):
    return {"user": AC.get_user(db, user_id), "games": AC.user_games(db, user_id)}


@router.patch("/users/{user_id}/permit", response_model=AdminUserOut, summary="권한 변경 (USER ↔ ADMIN)",
              responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def change_permit(user_id: int, body: PermitIn, admin: User = Depends(admin_user), db: Session = Depends(get_session)):
    return AC.set_permit(db, admin, AC.get_user(db, user_id), body.permit)


@router.post("/users/{user_id}/withdraw", response_model=AdminUserOut, summary="탈퇴 처리 (개인정보 삭제, 되돌릴 수 없음)",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def withdraw(user_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_session)):
    return AC.admin_withdraw(db, get_store(), admin, AC.get_user(db, user_id))
