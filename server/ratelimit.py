"""실패 횟수 제한 (Redis 카운터)

로그인: 같은 아이디로 5번 틀리면 15분 잠금 (비밀번호 대입 방지).
같은 IP에서 30번 틀려도 15분 잠금 (여러 아이디를 돌려 가며 대입하는 것 방지).
IP는 프록시(Tailscale·Cloudflare) 뒤라면 X-Forwarded-For의 첫 주소를 쓴다. 이 값은 꾸밀 수 있으므로
IP 제한은 보조 수단이고, 아이디 잠금이 주된 방어다.
"""
from dataclasses import dataclass

from fastapi import Request

from .runtime import get_store


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int         # 이 횟수에 닿으면 잠금
    window: int        # 초. 첫 실패부터 이 시간이 지나면 카운터가 사라진다


LOGIN_USER = Rule("login:u", 5, 15 * 60)
LOGIN_IP = Rule("login:ip", 30, 15 * 60)
REGISTER_IP = Rule("register:ip", 10, 60 * 60)
RESET_IP = Rule("reset:ip", 5, 60 * 60)
RESET_EMAIL = Rule("reset:email", 3, 60 * 60)


def client_ip(request: Request):
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def _key(rule, who):
    return f"rl:{rule.name}:{str(who).lower()}"


def retry_after(rule, who):
    """잠겨 있으면 남은 초, 아니면 0"""
    r, k = get_store().r, _key(rule, who)
    n = r.get(k)
    if n is None or int(n) < rule.limit:
        return 0
    return max(1, r.ttl(k))


def hit(rule, who):
    """실패(또는 요청) 1회 기록"""
    r, k = get_store().r, _key(rule, who)
    with r.pipeline() as p:
        p.incr(k)
        p.expire(k, rule.window, nx=True)       # 첫 실패 때만 만료 시간을 건다
        p.execute()


def clear(rule, who):
    get_store().r.delete(_key(rule, who))


def wait_text(seconds):
    m = -(-seconds // 60)
    return f"{m}분" if m > 1 else "1분"
