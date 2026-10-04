"""비밀번호 해시와 로그인 세션

해시는 werkzeug 형식(scrypt)을 그대로 쓴다. 예전 Flask 버전의 회원 비밀번호 해시를
옮겨 와도 그대로 로그인되게 하기 위해서다.
세션은 서명된 쿠키(Starlette SessionMiddleware)에 user_id와 비밀번호 지문만 담는다. SameSite=Lax라서
다른 사이트에서 보낸 POST 요청에는 쿠키가 붙지 않는다 (CSRF 완화).
비밀번호를 바꾸면 지문이 달라져 다른 기기의 세션은 자동으로 끝난다.

비밀번호 찾기 링크는 DB에 저장하지 않는 서명 토큰이다. 토큰 안에 비밀번호 지문이 들어 있어,
한 번 비밀번호를 바꾸면 같은 링크는 더 이상 쓸 수 없다 (한 번만 쓰임). 30분 뒤 만료.
"""
import hashlib

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from .config import settings
from .db import get_session
from .models import User

RESET_MAX_AGE = 30 * 60
_reset = URLSafeTimedSerializer(settings.secret_key, salt="password-reset")


def hash_password(password):
    return generate_password_hash(password)


def verify_password(password_hash, password):
    return check_password_hash(password_hash, password)


def _fingerprint(user: User):
    """비밀번호 해시에서 뽑은 짧은 지문 (해시 자체는 쿠키에 넣지 않음)"""
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]


def login_session(request: Request, user: User):
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["pv"] = _fingerprint(user)


def logout_session(request: Request):
    request.session.clear()


def optional_user(request: Request, db: Session = Depends(get_session)):
    uid = request.session.get("user_id")
    user = db.get(User, uid) if uid else None
    # 탈퇴한 회원(save_status = 'N')이거나 비밀번호가 바뀐 뒤의 옛 세션이면 로그아웃
    if user and (not user.active or request.session.get("pv") != _fingerprint(user)):
        request.session.clear()
        return None
    return user


def reset_token(user: User):
    return _reset.dumps({"u": user.id, "pv": _fingerprint(user)})


def user_from_reset_token(db, token):
    try:
        data = _reset.loads(token, max_age=RESET_MAX_AGE)
    except BadSignature:                      # 만료(SignatureExpired)도 여기에 포함
        return None
    user = db.get(User, data.get("u"))
    return user if user and user.active and data.get("pv") == _fingerprint(user) else None


def current_user(user: User | None = Depends(optional_user)):
    """API용: 로그인 안 했으면 401"""
    if not user:
        raise HTTPException(401, detail={"code": "UNAUTHORIZED", "message": "로그인이 필요합니다."})
    return user


class LoginRequired(Exception):
    """화면용: 로그인 페이지로 보냄 (main.py의 예외 처리기)"""


class AdminRequired(Exception):
    """화면용: 관리자가 아니면 게임 화면으로 (main.py의 예외 처리기)"""


def page_user(user: User | None = Depends(optional_user)):
    if not user:
        raise LoginRequired()
    return user


def admin_user(user: User = Depends(current_user)):
    """API용: permit = 'ADMIN'이 아니면 403"""
    if not user.is_admin:
        raise HTTPException(403, detail={"code": "FORBIDDEN", "message": "관리자만 볼 수 있습니다."})
    return user


def admin_page_user(user: User = Depends(page_user)):
    if not user.is_admin:
        raise AdminRequired()
    return user
