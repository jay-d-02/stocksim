"""Jinja2 설정: 템플릿이 쓰는 url_for·flash·금액 필터

템플릿은 Flask 시절 문법(url_for('이름', filename=…, 경로 인자, 나머지는 쿼리))을 그대로 쓴다.
라우트 이름으로 경로를 만들어 주는 작은 호환 계층을 둬서 화면 코드를 다시 쓰지 않았다.
"""
from datetime import timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(ROOT / "templates"))
_app = None
_path_params = {}        # 라우트 이름 → 경로 인자 이름들


def bind(app, *routers):
    """앱과 라우터 등록. 최신 FastAPI는 포함된 라우터를 내부 타입으로 감싸 app.routes에서
    이름을 바로 볼 수 없으므로, 원래 라우터에서 이름과 경로 인자를 모아 둔다"""
    global _app
    _app = app
    for router in routers:
        for r in router.routes:
            _path_params[r.name] = set(getattr(r, "param_convertors", {}))


def url_for(name, **kw):
    if name == "static":
        return f"/static/{kw['filename']}"
    if name not in _path_params:
        raise LookupError(f"없는 라우트 이름: {name}")
    params = {k: kw.pop(k) for k in list(kw) if k in _path_params[name]}
    path = _app.url_path_for(name, **params)
    return f"{path}?{urlencode(kw)}" if kw else str(path)


def flash(request: Request, message, category="ok"):
    request.session.setdefault("_flashes", []).append([category, message])


def get_flashed_messages(request: Request):
    return [tuple(x) for x in request.session.pop("_flashes", [])]


def won(v):
    return "-" if v is None else f"{int(round(v)):,}"


def signed(v):
    return "-" if v is None else f"{'+' if v > 0 else ''}{int(round(v)):,}"


def pct(v, digits=1, sign=True):
    """퍼센트 숫자. 반올림하면 0이 되는 값은 '-0.0'이 아니라 '0.0'으로"""
    if v is None:
        return "-"
    s = f"{v:{'+' if sign else ''}.{digits}f}"
    return s.lstrip("+-") if float(s) == 0 else s


KST = timezone(timedelta(hours=9))


def kst(dt, fmt="%m.%d %H:%M"):
    """시각 → 한국 시간 문자열 (DB 값은 UTC)"""
    return "-" if dt is None else dt.astimezone(KST).strftime(fmt)


env = templates.env
env.globals["url_for"] = url_for
env.filters["won"] = won
env.filters["signed"] = signed
env.filters["pct"] = pct
env.filters["kst"] = kst


def render(request: Request, name, user=None, status_code=200, **ctx):
    endpoint = getattr(request.scope.get("endpoint"), "__name__", "")
    return templates.TemplateResponse(request, name, {
        "user": user, "endpoint": endpoint,
        "get_flashed_messages": lambda with_categories=True: get_flashed_messages(request), **ctx},
        status_code=status_code)
