"""인증·회원 API: 가입·로그인·로그아웃, 내 정보·비밀번호·탈퇴, 비밀번호 찾기 (세션 쿠키)"""
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_session
from ..models import User
from ..ratelimit import client_ip
from ..runtime import get_store
from ..schemas import (Credentials, ErrorResponse, PasswordChangeIn, PasswordConfirmIn, ProfileIn, RegisterIn,
                       ResetIn, ResetRequestIn, UserOut)
from ..security import current_user, login_session, logout_session
from ..services import accounts as AC

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
ERR = {400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
       429: {"model": ErrorResponse, "description": "시도가 너무 많음 (Retry-After 헤더)"}}


def error_response(e: AC.AccountError):
    headers = {"Retry-After": str(e.retry_after)} if e.retry_after else None
    return JSONResponse({"detail": {"code": e.code, "message": e.message}}, status_code=e.status, headers=headers)


def base_url(request: Request):
    """메일 속 링크의 앞부분. PUBLIC_URL이 있으면 그것 (프록시 뒤에서 요청 주소가 내부 주소일 수 있음)"""
    return settings.public_url or str(request.base_url).rstrip("/")


@router.post("/register", status_code=201, name="api_register", response_model=UserOut, responses=ERR,
             summary="회원가입 (가입과 동시에 로그인)")
def register(body: RegisterIn, request: Request, db: Session = Depends(get_session)):
    user = AC.register(db, body, client_ip(request))
    login_session(request, user)
    return user


@router.post("/login", name="api_login", response_model=UserOut, responses=ERR,
             summary="로그인 (세션 쿠키 발급). 같은 아이디로 5번 틀리면 15분 잠금")
def login(body: Credentials, request: Request, db: Session = Depends(get_session)):
    user = AC.login(db, body.username, body.password, client_ip(request))
    login_session(request, user)
    return user


@router.post("/logout", status_code=204, name="api_logout", summary="로그아웃")
def logout(request: Request):
    logout_session(request)
    return Response(status_code=204)


@router.get("/me", name="api_me", response_model=UserOut, summary="내 정보", responses={401: {"model": ErrorResponse}})
def me(user: User = Depends(current_user)):
    return user


@router.patch("/me", name="api_update_me", response_model=UserOut, responses=ERR,
              summary="내 정보 바꾸기 (닉네임·투자 경험·이메일 중 보낸 것만)")
def update_me(body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_session)):
    return AC.update_profile(db, user, body)


@router.post("/password", name="api_change_password", response_model=UserOut, responses=ERR,
             summary="비밀번호 바꾸기 (다른 기기의 로그인은 끝남)")
def change_password(body: PasswordChangeIn, request: Request, user: User = Depends(current_user),
                    db: Session = Depends(get_session)):
    AC.change_password(db, user, body.current_password, body.new_password)
    login_session(request, user)                # 지금 세션만 새 지문으로 유지
    return user


@router.delete("/me", status_code=204, name="api_delete_me", responses=ERR,
               summary="회원 탈퇴 (모든 게임 기록 삭제, 되돌릴 수 없음)")
def delete_me(body: PasswordConfirmIn, request: Request, user: User = Depends(current_user),
              db: Session = Depends(get_session)):
    AC.delete_account(db, get_store(), user, body.password)
    logout_session(request)
    return Response(status_code=204)


@router.post("/password-reset", status_code=202, name="api_request_reset", responses=ERR,
             summary="비밀번호 찾기 메일 요청 (가입 여부와 관계없이 202)")
def request_reset(body: ResetRequestIn, request: Request, tasks: BackgroundTasks, db: Session = Depends(get_session)):
    tasks.add_task(AC.send_reset_mail, AC.request_reset(db, body.email, client_ip(request), base_url(request)))
    return {"message": "가입된 이메일이면 재설정 링크를 보냈습니다."}


@router.post("/password-reset/confirm", name="api_reset_password", response_model=UserOut, responses=ERR,
             summary="메일 속 토큰으로 새 비밀번호 정하기")
def reset_password(body: ResetIn, request: Request, db: Session = Depends(get_session)):
    user = AC.reset_password(db, body.token, body.new_password)
    login_session(request, user)
    return user
