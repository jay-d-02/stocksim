"""회원 유스케이스: 가입·로그인(잠금)·내 정보·비밀번호·탈퇴·비밀번호 찾기

API(server/api/auth.py)와 화면(server/web/pages.py)이 같은 함수를 쓴다.
실패는 AccountError(코드, 문장, HTTP 상태)로 올린다.
"""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .. import mail
from .. import ratelimit as RL
from ..models import PERMITS, Game, Trade, User
from ..security import hash_password, reset_token, user_from_reset_token, verify_password


class AccountError(Exception):
    def __init__(self, code, message, status=400, retry_after=0):
        super().__init__(message)
        self.code, self.message, self.status, self.retry_after = code, message, status, retry_after


# 어긴 유니크 제약조건 → (오류 코드, 화면 문장)
TAKEN = {
    "users_username_key": ("USERNAME_TAKEN", "이미 쓰고 있는 아이디입니다."),
    "users_nickname_key": ("NICKNAME_TAKEN", "이미 쓰고 있는 닉네임입니다."),
    "users_email_key": ("EMAIL_TAKEN", "이미 가입된 이메일입니다."),
}


def _commit_unique(db):
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        code, message = TAKEN.get(getattr(e.orig.diag, "constraint_name", None), TAKEN["users_username_key"])
        raise AccountError(code, message, 409) from None


def _locked(seconds):
    return AccountError("TOO_MANY_ATTEMPTS", f"시도가 너무 많아 잠시 막혔어요. {RL.wait_text(seconds)} 뒤 다시 시도하세요.",
                        429, seconds)


# ------------------------------------------------------------ 가입·로그인
def register(db, body, ip):
    if wait := RL.retry_after(RL.REGISTER_IP, ip):
        raise _locked(wait)
    username = body.username.strip()
    user = User(username=username, password_hash=hash_password(body.password),
                nickname=body.nickname or username, experience=body.experience,
                email=body.email.lower() if body.email else None,
                privacy_agreed_at=datetime.now(timezone.utc))
    db.add(user)
    _commit_unique(db)
    RL.hit(RL.REGISTER_IP, ip)                  # 성공한 가입만 셈 (계정 대량 생성 방지)
    return user


def login(db, username, password, ip):
    """아이디 5번 / IP 30번 틀리면 15분 잠금"""
    name = username.strip()
    if wait := max(RL.retry_after(RL.LOGIN_USER, name), RL.retry_after(RL.LOGIN_IP, ip)):
        raise _locked(wait)
    user = db.scalar(select(User).where(User.username == name, User.save_status == "Y"))    # 탈퇴 회원은 로그인 불가
    if not user or not verify_password(user.password_hash, password):
        RL.hit(RL.LOGIN_USER, name)
        RL.hit(RL.LOGIN_IP, ip)
        if wait := RL.retry_after(RL.LOGIN_USER, name):
            raise AccountError("TOO_MANY_ATTEMPTS", f"비밀번호를 {RL.LOGIN_USER.limit}번 잘못 입력해 "
                               f"{RL.wait_text(wait)} 동안 로그인이 막혔어요.", 429, wait)
        raise AccountError("INVALID_CREDENTIALS", "아이디 또는 비밀번호가 맞지 않습니다.", 401)
    RL.clear(RL.LOGIN_USER, name)
    return user


# ------------------------------------------------------------ 내 정보
def update_profile(db, user, body):
    """보낸 항목만 바꿈. 닉네임은 지울 수 없고, 이메일은 null/빈 값이면 지움"""
    sent = body.model_fields_set
    if "nickname" in sent and body.nickname:
        user.nickname = body.nickname
    if "experience" in sent and body.experience:
        user.experience = body.experience
    if "email" in sent:
        user.email = body.email.lower() if body.email else None
    _commit_unique(db)
    return user


def change_password(db, user, current, new):
    if not verify_password(user.password_hash, current):
        raise AccountError("WRONG_PASSWORD", "현재 비밀번호가 맞지 않습니다.", 400)
    if new.lower() == user.username.lower():
        raise AccountError("WEAK_PASSWORD", "비밀번호를 아이디와 다르게 정하세요.", 422)
    user.password_hash = hash_password(new)
    db.commit()
    return user


def delete_account(db, store, user, password):
    """본인 탈퇴 (비밀번호 확인 후 withdraw)"""
    if not verify_password(user.password_hash, password):
        raise AccountError("WRONG_PASSWORD", "비밀번호가 맞지 않습니다.", 400)
    withdraw(db, store, user)


WITHDRAWN_HASH = "!withdrawn"      # 해시 형식이 아니라 어떤 비밀번호와도 맞지 않음


def _last_admin(db, user):
    return user.is_admin and db.scalar(
        select(func.count()).select_from(User).where(User.permit == "ADMIN", User.save_status == "Y")) <= 1


def withdraw(db, store, user):
    """탈퇴: 행은 남기고 save_status = 'N'. 누구인지 알 수 있는 정보(아이디·닉네임·이메일·비밀번호)는 지운다.
    끝난 게임 기록은 통계용으로 남고(명예의 전당에서는 빠짐), 진행 중인 게임은 포기 처리."""
    if _last_admin(db, user):
        raise AccountError("LAST_ADMIN", "마지막 관리자는 탈퇴할 수 없어요. 다른 회원을 먼저 관리자로 지정하세요.", 409)
    now = datetime.now(timezone.utc)
    for g in db.scalars(select(Game).where(Game.user_id == user.id).with_for_update()):
        if g.status == "ACTIVE":
            g.status, g.finished_at = "ABANDONED", now
        store.delete(g.id)
    user.username = user.nickname = user.email = None
    user.password_hash = WITHDRAWN_HASH
    user.save_status, user.withdrawn_at, user.permit = "N", now, "USER"
    db.commit()


# ------------------------------------------------------------ 관리자 (permit = 'ADMIN')
def list_users(db, q="", status="Y", permit=None, page=1, per_page=50):
    """회원 목록 + 게임 요약. q: 아이디·닉네임·이메일 일부"""
    games = (select(Game.user_id, func.count().label("games"),
                    func.count().filter(Game.status == "FINISHED").label("finished"),
                    func.max(Game.final_return_pct).label("best_pct"),
                    func.max(Game.started_at).label("last_played"))
             .group_by(Game.user_id).subquery())
    stmt = select(User, games.c.games, games.c.finished, games.c.best_pct, games.c.last_played) \
        .outerjoin(games, games.c.user_id == User.id)
    if status in ("Y", "N"):
        stmt = stmt.where(User.save_status == status)
    if permit in PERMITS:
        stmt = stmt.where(User.permit == permit)
    if q := q.strip():
        like = f"%{q}%"
        stmt = stmt.where(User.username.ilike(like) | User.nickname.ilike(like) | User.email.ilike(like))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(User.id.desc()).offset((page - 1) * per_page).limit(per_page))
    return total, [{"user": u, "games": n or 0, "finished": f or 0,
                    "best_pct": float(b) if b is not None else None, "last_played": lp}
                   for u, n, f, b, lp in rows]


def get_user(db, user_id):
    user = db.get(User, user_id)
    if not user:
        raise AccountError("NOT_FOUND", "없는 회원입니다.", 404)
    return user


def user_games(db, user_id, limit=30):
    n = select(func.count()).select_from(Trade).where(Trade.game_id == Game.id).scalar_subquery()
    return [{"id": g.id, "status": g.status, "day": g.current_day, "max_day": g.max_day, "started_at": g.started_at,
             "finished_at": g.finished_at, "final_total": g.final_total,
             "pct": float(g.final_return_pct) if g.final_return_pct is not None else None, "trades": cnt}
            for g, cnt in db.execute(select(Game, n).where(Game.user_id == user_id)
                                     .order_by(Game.id.desc()).limit(limit))]


def set_permit(db, admin, target, permit):
    if permit not in PERMITS:
        raise AccountError("INVALID_PERMIT", "권한은 USER 또는 ADMIN입니다.", 422)
    if not target.active:
        raise AccountError("WITHDRAWN", "탈퇴한 회원의 권한은 바꿀 수 없어요.", 409)
    if target.id == admin.id and permit != "ADMIN":
        raise AccountError("SELF_DEMOTE", "자기 자신의 관리자 권한은 뺄 수 없어요. 다른 관리자에게 부탁하세요.", 409)
    target.permit = permit
    db.commit()
    return target


def admin_withdraw(db, store, admin, target):
    if target.id == admin.id:
        raise AccountError("SELF_WITHDRAW", "본인 탈퇴는 내 정보 화면에서 해 주세요.", 409)
    if not target.active:
        raise AccountError("WITHDRAWN", "이미 탈퇴한 회원입니다.", 409)
    withdraw(db, store, target)
    return target


# ------------------------------------------------------------ 비밀번호 찾기
def request_reset(db, email, ip, base_url):
    """가입된 이메일이면 재설정 링크 메일 (보낼 내용을 돌려주고 발송은 호출한 쪽이 백그라운드로).
    가입 여부를 알려 주지 않도록, 없는 이메일이어도 같은 결과처럼 보이게 한다"""
    email = email.strip().lower()
    if wait := max(RL.retry_after(RL.RESET_IP, ip), RL.retry_after(RL.RESET_EMAIL, email)):
        raise _locked(wait)
    RL.hit(RL.RESET_IP, ip)
    RL.hit(RL.RESET_EMAIL, email)
    user = db.scalar(select(User).where(User.email == email, User.save_status == "Y"))
    if not user:
        return None
    link = f"{base_url}/reset?token={reset_token(user)}"
    body = (f"{user.nickname}님, 비밀번호 재설정을 요청하셨습니다.\n\n"
            f"아래 링크에서 새 비밀번호를 정하세요 (30분 동안, 한 번만 쓸 수 있어요).\n{link}\n\n"
            f"아이디: {user.username}\n\n요청하지 않았다면 이 메일을 무시하세요. 비밀번호는 바뀌지 않습니다.")
    return email, "[주식 시뮬레이터] 비밀번호 재설정 안내", body


def send_reset_mail(message):
    if message:
        mail.send(*message)


def reset_password(db, token, new):
    user = user_from_reset_token(db, token)
    if not user:
        raise AccountError("INVALID_TOKEN", "링크가 만료됐거나 이미 사용됐어요. 비밀번호 찾기를 다시 해 주세요.", 400)
    if new.lower() == user.username.lower():
        raise AccountError("WEAK_PASSWORD", "비밀번호를 아이디와 다르게 정하세요.", 422)
    user.password_hash = hash_password(new)
    db.commit()
    RL.clear(RL.LOGIN_USER, user.username)       # 잠겨 있었다면 풀어 줌
    return user
