"""주식 모의투자 연습 사이트 (Flask + PostgreSQL)"""
import os
import json
import time
import random
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, g, jsonify)
from werkzeug.security import generate_password_hash, check_password_hash

# ---------------------------------------------------------------- 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
START_CASH = int(os.environ.get("START_CASH", 10_000_000))
FEE_RATE = float(os.environ.get("FEE_RATE", 0.00015))
TAX_RATE = float(os.environ.get("TAX_RATE", 0.002))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# 자주 찾는 종목 (검색 편의용, 종목코드는 직접 입력해도 됨)
POPULAR = {
    "005930": "삼성전자", "000660": "SK하이닉스", "373220": "LG에너지솔루션",
    "207940": "삼성바이오로직스", "005380": "현대차", "000270": "기아",
    "035420": "NAVER", "035720": "카카오", "068270": "셀트리온",
    "005490": "POSCO홀딩스", "051910": "LG화학", "006400": "삼성SDI",
    "105560": "KB금융", "055550": "신한지주", "012330": "현대모비스",
    "028260": "삼성물산", "066570": "LG전자", "003550": "LG",
    "017670": "SK텔레콤", "034730": "SK", "259960": "크래프톤",
    "352820": "하이브", "247540": "에코프로비엠", "086520": "에코프로",
}
_name_cache = dict(POPULAR)
_listing_loaded = False


# ---------------------------------------------------------------- DB
import db as dbmod


def get_db():
    """요청 하나 동안 풀에서 연결 하나를 빌려 씀"""
    if "db" not in g:
        g.db = dbmod.pool.getconn()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    con = g.pop("db", None)
    if con is not None:
        try:
            con.rollback()   # 커밋 안 된 작업은 버림
        finally:
            dbmod.pool.putconn(con)


dbmod.init_db()


# ---------------------------------------------------------------- 시세
from providers import PriceService

prices = PriceService()   # PRICE_PROVIDERS 환경변수 순서대로 증권사 API 사용

import stockinfo


def get_history(code, days=120):
    """[(날짜, 종가), ...] 반환. 실패하면 None."""
    return prices.daily(code, days)


def get_quote(code):
    q = prices.quote(code)
    if q and q.get("name"):
        _name_cache.setdefault(code, q["name"])
    return q


def get_price(code):
    q = get_quote(code)
    return q["price"] if q else None


def get_change(code):
    """(현재가, 전일대비, 등락률%)"""
    q = get_quote(code)
    if not q:
        return None, None, None
    return q["price"], q["diff"], q["pct"]


def stock_name(code):
    global _listing_loaded
    if code in _name_cache:
        return _name_cache[code]
    if not _listing_loaded and any(p.key == "fdr" for p in prices.providers):
        _listing_loaded = True  # 전체 종목 목록은 한 번만 시도
        try:
            import FinanceDataReader as fdr
            for _, r in fdr.StockListing("KRX").iterrows():
                _name_cache.setdefault(str(r["Code"]), r["Name"])
        except Exception:
            pass
    return _name_cache.get(code, code)


# ---------------------------------------------------------------- 로그인
def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


def current_user():
    return get_db().execute("SELECT * FROM users WHERE id=%s",
                            (session["user_id"],)).fetchone()


@app.template_filter("won")
def won(v):
    if v is None:
        return "-"
    return f"{int(round(v)):,}"


@app.template_filter("signed")
def signed(v):
    if v is None:
        return "-"
    return f"{'+' if v > 0 else ''}{int(round(v)):,}"


@app.template_filter("eok")
def eok(v):
    """큰 금액을 '1조 2,345억' 형태로"""
    if v is None:
        return "-"
    e = int(v // 100_000_000)
    jo, rest = divmod(e, 10_000)
    if jo:
        return f"{jo:,}조 {rest:,}억" if rest else f"{jo:,}조"
    return f"{e:,}억"


@app.context_processor
def inject():
    return {"stock_name": stock_name, "popular": POPULAR}


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        if len(u) < 3 or len(p) < 4:
            flash("아이디는 3자, 비밀번호는 4자 이상으로 입력하세요.", "err")
        else:
            try:
                db = get_db()
                db.execute("INSERT INTO users (username, pw_hash, cash) VALUES (%s,%s,%s)",
                           (u, generate_password_hash(p), START_CASH))
                db.commit()
                flash("가입했습니다. 로그인하세요.", "ok")
                return redirect(url_for("login"))
            except dbmod.UniqueViolation:
                db.rollback()
                flash("이미 쓰고 있는 아이디입니다.", "err")
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        row = get_db().execute("SELECT * FROM users WHERE username=%s", (u,)).fetchone()
        if row and check_password_hash(row["pw_hash"], p):
            session.clear()
            session["user_id"] = row["id"]
            return redirect(url_for("index"))
        flash("아이디 또는 비밀번호가 맞지 않습니다.", "err")
    return render_template("auth.html", mode="login")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- 화면
@app.route("/")
@login_required
def index():
    user = current_user()
    rows = get_db().execute("SELECT * FROM holdings WHERE user_id=%s ORDER BY code",
                            (user["id"],)).fetchall()
    holdings, stock_value, cost = [], 0, 0
    for h in rows:
        cur = get_price(h["code"]) or h["avg_price"]
        value = cur * h["qty"]
        buy = h["avg_price"] * h["qty"]
        holdings.append({
            "code": h["code"], "name": stock_name(h["code"]), "qty": h["qty"],
            "avg": h["avg_price"], "cur": cur, "value": value,
            "pl": value - buy, "pl_pct": (value - buy) / buy * 100 if buy else 0,
        })
        stock_value += value
        cost += buy
    total = user["cash"] + stock_value
    return render_template("index.html", user=user, holdings=holdings,
                           stock_value=stock_value, total=total,
                           total_pl=total - START_CASH,
                           total_pl_pct=(total - START_CASH) / START_CASH * 100)


@app.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return redirect(url_for("index"))
    if q.isdigit() and len(q) == 6:
        return redirect(url_for("stock", code=q))
    for code, name in _name_cache.items():
        if q.lower() in name.lower():
            return redirect(url_for("stock", code=code))
    flash(f"'{q}' 종목을 찾지 못했습니다. 6자리 종목코드로 검색해 보세요.", "err")
    return redirect(url_for("index"))


@app.route("/stock/<code>")
@login_required
def stock(code):
    hist = get_history(code)
    if not hist:
        flash(f"{code} 시세를 불러오지 못했습니다. 종목코드를 확인하세요.", "err")
        return redirect(url_for("index"))
    user = current_user()
    q = get_quote(code)
    if not q:
        flash(f"{code} 현재가를 불러오지 못했습니다.", "err")
        return redirect(url_for("index"))
    cur, diff, pct = q["price"], q["diff"], q["pct"]
    h = get_db().execute("SELECT * FROM holdings WHERE user_id=%s AND code=%s",
                         (user["id"], code)).fetchone()
    max_buy = int(user["cash"] // (cur * (1 + FEE_RATE))) if cur else 0
    return render_template("stock.html", user=user, code=code, name=stock_name(code),
                           cur=cur, diff=diff, pct=pct, holding=h, max_buy=max_buy,
                           labels=[d for d, _ in hist], prices=[p for _, p in hist],
                           fee_rate=FEE_RATE, tax_rate=TAX_RATE,
                           source=q.get("source"), quoted_at=q.get("at"))


@app.route("/info")
@app.route("/info/<code>")
@login_required
def info(code=None):
    """종목 정보. 코드가 없으면 인기 종목 중 하나를 랜덤으로 보여줌"""
    if code is None:
        prev = request.args.get("prev")
        choices = [c for c in POPULAR if c != prev] or list(POPULAR)
        return redirect(url_for("info", code=random.choice(choices)))
    q = get_quote(code)
    price = q["price"] if q else stockinfo.fallback_price(code)
    name = stock_name(code)
    others = random.sample([c for c in POPULAR if c != code], 6)
    return render_template("info.html", user=current_user(), code=code, name=name,
                           price=price, q=q, d=stockinfo.generate(code, name, price),
                           others=others)


@app.route("/trade/<code>", methods=["POST"])
@login_required
def trade(code):
    side = request.form.get("side")
    try:
        qty = int(request.form.get("qty", 0))
    except ValueError:
        qty = 0
    if qty <= 0 or side not in ("BUY", "SELL"):
        flash("수량을 1주 이상으로 입력하세요.", "err")
        return redirect(url_for("stock", code=code))

    price = get_price(code)
    if not price:
        flash("시세를 불러오지 못해 주문하지 않았습니다.", "err")
        return redirect(url_for("stock", code=code))

    db = get_db()
    # 사용자 행을 잠가서 같은 사람의 주문이 동시에 처리되지 않게 함 (중복 매수 방지)
    user = db.execute("SELECT * FROM users WHERE id=%s FOR UPDATE",
                      (session["user_id"],)).fetchone()
    h = db.execute("SELECT * FROM holdings WHERE user_id=%s AND code=%s",
                   (user["id"], code)).fetchone()
    amount = price * qty
    fee = int(amount * FEE_RATE)

    if side == "BUY":
        need = amount + fee
        if need > user["cash"]:
            flash(f"예수금이 부족합니다. 필요 {need:,}원 / 보유 {user['cash']:,}원", "err")
            return redirect(url_for("stock", code=code))
        if h:
            new_qty = h["qty"] + qty
            new_avg = (h["avg_price"] * h["qty"] + amount) / new_qty
            db.execute("UPDATE holdings SET qty=%s, avg_price=%s WHERE user_id=%s AND code=%s",
                       (new_qty, new_avg, user["id"], code))
        else:
            db.execute("INSERT INTO holdings (user_id, code, qty, avg_price) "
                       "VALUES (%s,%s,%s,%s)",
                       (user["id"], code, qty, price))
        db.execute("UPDATE users SET cash=cash-%s WHERE id=%s", (need, user["id"]))
        db.execute("INSERT INTO trades (user_id,code,side,qty,price,fee,tax,profit) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s,NULL)",
                   (user["id"], code, "BUY", qty, price, fee, 0))
        flash(f"{stock_name(code)} {qty:,}주를 {price:,}원에 매수했습니다.", "ok")
    else:
        if not h or h["qty"] < qty:
            flash(f"보유 수량이 부족합니다. 보유 {h['qty'] if h else 0:,}주", "err")
            return redirect(url_for("stock", code=code))
        tax = int(amount * TAX_RATE)
        receive = amount - fee - tax
        profit = int(receive - h["avg_price"] * qty)
        if h["qty"] == qty:
            db.execute("DELETE FROM holdings WHERE user_id=%s AND code=%s", (user["id"], code))
        else:
            db.execute("UPDATE holdings SET qty=qty-%s WHERE user_id=%s AND code=%s",
                       (qty, user["id"], code))
        db.execute("UPDATE users SET cash=cash+%s WHERE id=%s", (receive, user["id"]))
        db.execute("INSERT INTO trades (user_id,code,side,qty,price,fee,tax,profit) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                   (user["id"], code, "SELL", qty, price, fee, tax, profit))
        flash(f"{stock_name(code)} {qty:,}주를 {price:,}원에 매도했습니다. "
              f"실현손익 {profit:+,}원", "ok")
    db.commit()
    return redirect(url_for("stock", code=code))


@app.route("/history")
@login_required
def history():
    user = current_user()
    trades = get_db().execute("SELECT * FROM trades WHERE user_id=%s ORDER BY id DESC",
                              (user["id"],)).fetchall()
    realized = sum(t["profit"] or 0 for t in trades)
    return render_template("history.html", user=user, trades=trades, realized=realized)


@app.route("/ranking")
@login_required
def ranking():
    db = get_db()
    users = db.execute("SELECT id, username, cash FROM users").fetchall()
    board = []
    for u in users:
        total = u["cash"]
        for h in db.execute("SELECT * FROM holdings WHERE user_id=%s", (u["id"],)):
            total += (get_price(h["code"]) or h["avg_price"]) * h["qty"]
        board.append({"name": u["username"], "total": total,
                      "pct": (total - START_CASH) / START_CASH * 100,
                      "me": u["id"] == session["user_id"]})
    board.sort(key=lambda x: x["total"], reverse=True)
    return render_template("ranking.html", user=current_user(), board=board)


@app.route("/reset", methods=["POST"])
@login_required
def reset():
    db = get_db()
    uid = session["user_id"]
    db.execute("DELETE FROM holdings WHERE user_id=%s", (uid,))
    db.execute("DELETE FROM trades WHERE user_id=%s", (uid,))
    db.execute("UPDATE users SET cash=%s WHERE id=%s", (START_CASH, uid))
    db.commit()
    flash(f"계좌를 초기화했습니다. 예수금 {START_CASH:,}원으로 다시 시작합니다.", "ok")
    return redirect(url_for("index"))


# ---------------------------------------------------------------- 투자 게임
from simulator import engine as gm
from simulator import analysis as an


def load_game(lock=False):
    """내 게임 상태. 없거나 예전(하루 단위) 형식이면 새로 만듦. lock=True면 행을 잠가 동시 요청을 막음"""
    db = get_db()
    row = db.execute("SELECT state FROM games WHERE user_id=%s" + (" FOR UPDATE" if lock else ""),
                     (session["user_id"],)).fetchone()
    if row and row["state"].get("v") == 2:
        return row["state"]
    state = gm.new_game(START_CASH)
    db.execute("INSERT INTO games (user_id, state) VALUES (%s,%s) "
               "ON CONFLICT (user_id) DO UPDATE SET state=EXCLUDED.state, started_at=now()",
               (session["user_id"], dbmod.Jsonb(state)))
    db.commit()
    return load_game(lock)


def save_game(state):
    """저장하고, 막 끝난 게임이면 명예의 전당에 기록 (커밋은 호출한 쪽에서)"""
    db = get_db()
    if state["finished"] and not state["recorded"]:
        state["recorded"] = True
        total = gm.total_value(state)
        db.execute("INSERT INTO game_results (user_id, total, pct, trades, profile) VALUES (%s,%s,%s,%s,%s)",
                   (session["user_id"], total,
                    (total - state["start_cash"]) / state["start_cash"] * 100, len(state["trades"]),
                    dbmod.Jsonb(an.summary(an.analyze(state)))))
    db.execute("UPDATE games SET state=%s, updated_at=now() WHERE user_id=%s",
               (dbmod.Jsonb(state), session["user_id"]))


def game_hall(limit=10):
    return get_db().execute(
        "SELECT u.username, r.total, r.pct, r.trades, r.finished_at, r.user_id "
        "FROM game_results r JOIN users u ON u.id=r.user_id "
        "ORDER BY r.total DESC LIMIT %s", (limit,)).fetchall()


@app.route("/game")
@login_required
def game_home():
    st = load_game()
    return render_template("game.html", user=current_user(), snap=gm.snapshot(st, full=True),
                           hall=game_hall(), fee_rate=FEE_RATE, tax_rate=TAX_RATE)


def _since():
    try:
        return int((request.get_json(silent=True) or {}).get("since", 0))
    except (TypeError, ValueError):
        return 0


@app.route("/api/game/tick", methods=["POST"])
@login_required
def api_game_tick():
    st = load_game(lock=True)
    gm.step_tick(st)
    save_game(st)
    get_db().commit()
    return jsonify(gm.snapshot(st, _since()))


@app.route("/api/game/next_day", methods=["POST"])
@login_required
def api_game_next_day():
    st = load_game(lock=True)
    gm.begin_day(st)
    save_game(st)
    get_db().commit()
    return jsonify(gm.snapshot(st, _since(), full=True))


@app.route("/api/game/trade", methods=["POST"])
@login_required
def api_game_trade():
    d = request.get_json(silent=True) or {}
    code, side = d.get("code"), d.get("side")
    try:
        qty = int(d.get("qty", 0))
    except (TypeError, ValueError):
        qty = 0
    if code not in gm.STOCK or side not in ("BUY", "SELL") or qty <= 0:
        return jsonify(ok=False, msg="수량을 1주 이상으로 입력하세요.")
    st = load_game(lock=True)
    ok, msg = gm.trade(st, code, side, qty, FEE_RATE, TAX_RATE)
    if ok:
        save_game(st)
        get_db().commit()
    return jsonify(ok=ok, msg=msg, snap=gm.snapshot(st, _since()))


@app.route("/game/new", methods=["POST"])
@login_required
def game_new():
    load_game(lock=True)
    st = gm.new_game(START_CASH)
    save_game(st)
    get_db().commit()
    flash(f"새 게임을 시작했습니다. 가상자금 {START_CASH:,}원!", "ok")
    return redirect(url_for("game_home"))


@app.route("/game/stock/<code>")
@login_required
def game_stock(code):
    if code not in gm.STOCK:
        return redirect(url_for("game_home"))
    st = load_game()
    s = gm.STOCK[code]
    cur, prev = gm.price(st, code), gm.prev_close(st, code)
    hist = st["prices"][code] + ([cur] if st["phase"] == "open" else [])
    h = st["holdings"].get(code)
    # 이 종목 뉴스 + 시장 전체에 영향을 준 사건
    news = [n for n in st["news"] if code in n["codes"]
            or (not n["codes"] and n["kind"] in ("event", "preview"))][::-1][:15]
    return render_template("game_stock.html", user=current_user(), st=st, s=s, code=code,
                           cur=cur, diff=cur - prev, pct=(cur - prev) / prev * 100, holding=h,
                           max_buy=int(st["cash"] // (cur * (1 + FEE_RATE))),
                           labels=[gm.day_label(i) for i in range(len(hist))], prices=hist,
                           exposure=gm.SECTORS[s["sector"]], factors=gm.FACTORS,
                           news=news, stocks=gm.STOCK, fee_rate=FEE_RATE, tax_rate=TAX_RATE,
                           my_trades=[t for t in st["trades"] if t["code"] == code][::-1])


@app.route("/game/trade/<code>", methods=["POST"])
@login_required
def game_trade(code):
    side = request.form.get("side")
    try:
        qty = int(request.form.get("qty", 0))
    except ValueError:
        qty = 0
    if code not in gm.STOCK or qty <= 0 or side not in ("BUY", "SELL"):
        flash("수량을 1주 이상으로 입력하세요.", "err")
        return redirect(url_for("game_stock", code=code))
    st = load_game(lock=True)
    ok, msg = gm.trade(st, code, side, qty, FEE_RATE, TAX_RATE)
    if ok:
        save_game(st)
        get_db().commit()
    flash(msg, "ok" if ok else "err")
    return redirect(url_for("game_stock", code=code))


@app.route("/game/style")
@login_required
def game_style():
    """내 투자 성향: 진행 중인(또는 방금 끝난) 게임 분석 + 지난 게임 기록"""
    st = load_game()
    past = get_db().execute(
        "SELECT total, pct, trades, profile, finished_at FROM game_results "
        "WHERE user_id=%s ORDER BY id DESC LIMIT 10", (session["user_id"],)).fetchall()
    return render_template("style.html", user=current_user(), a=an.analyze(st), st=st,
                           past=past, fmt_hold=an.fmt_hold, min_trades=an.MIN_TRADES)


@app.route("/game/news")
@login_required
def game_news():
    st = load_game()
    return render_template("game_news.html", user=current_user(), st=st,
                           news=st["news"][::-1], stocks=gm.STOCK)


@app.route("/learn")
@login_required
def learn():
    """투자 공부: 요인 실험실 · 투자 팁 · 퀴즈. 숫자는 전부 게임 엔진 값에서 가져옴"""
    scenarios = [{"key": e["key"], "title": e["title"], "why": e["why"], "factor": e["factor"],
                  "tag": gm.TAGS.get(e["factor"], "시장"), "impacts": gm.event_impacts(e)}
                 for e in gm.MACRO_EVENTS]
    corp = [{"key": e["key"], "title": e["title"].replace("{name}", "A사"), "move": e["move"],
             "why": e["why"], "factor": e["factor"]} for e in gm.CORP_EVENTS]
    stocks = {c: {"name": s["name"], "sector": s["sector"], "beta": s["beta"]}
              for c, s in gm.STOCK.items()}
    ev = {e["key"]: e for e in gm.MACRO_EVENTS}
    # 실험실 슬라이더 단위 → 엔진의 요인 충격 (게임 사건과 같은 크기가 되도록 사건 정의에서 역산)
    units = {"fx": ev["fx_up"]["ind"]["fx"] / ev["fx_up"]["shock"]["fx"],       # 원 / 충격 1
             "oil": ev["oil_up"]["ind"]["oil"] / ev["oil_up"]["shock"]["oil"],  # 달러 / 충격 1
             "econ": ev["econ_up"]["shock"]["econ"],                            # '호조' 한 단계
             "rate": gm.RATE_SHOCK, "rate_mkt": gm.RATE_MARKET}
    return render_template("learn.html", user=current_user(), scenarios=scenarios, corp=corp,
                           stocks=stocks, sectors=gm.SECTORS, factors=gm.FACTORS, units=units,
                           fee_rate=FEE_RATE, tax_rate=TAX_RATE)


@app.route("/factors")
@login_required
def factors():
    return render_template("factors.html", user=current_user(), sectors=gm.SECTORS,
                           factors=gm.FACTORS, stocks=gm.STOCK)


@app.route("/api/price/<code>")
@login_required
def api_price(code):
    q = get_quote(code)
    if not q:
        return jsonify(error="not found"), 404
    return jsonify(code=code, price=q["price"], diff=q["diff"], pct=round(q["pct"], 2),
                   source=q.get("source"), at=q.get("at"))


@app.route("/providers")
@login_required
def providers_page():
    code = request.args.get("code", "005930").strip()
    rows = prices.compare(code) if request.args.get("run") else None
    return render_template("providers.html", user=current_user(), code=code,
                           name=stock_name(code), rows=rows,
                           status=prices.status, order=[p.label for p in prices.providers],
                           now=time.time())


# ---------------------------------------------------------------- 앱(PWA)
@app.route("/manifest.webmanifest")
def manifest():
    icon = lambda f, size, purpose: {"src": url_for("static", filename=f"icons/{f}"),
                                     "sizes": size, "type": "image/png", "purpose": purpose}
    data = {
        "name": "연습계좌 · 투자 게임", "short_name": "연습계좌",
        "description": "가상 자금으로 뉴스를 읽고 투자하며 주식을 배우는 게임",
        "lang": "ko", "start_url": url_for("game_home"), "scope": "/", "id": "/",
        "display": "standalone", "orientation": "portrait",
        "background_color": "#0B111C", "theme_color": "#18212F",
        "icons": [icon("icon-192.png", "192x192", "any"), icon("icon-512.png", "512x512", "any"),
                  icon("maskable-512.png", "512x512", "maskable")],
        "shortcuts": [
            {"name": "투자 게임", "url": url_for("game_home")},
            {"name": "요인 실험실", "url": url_for("learn") + "#lab"},
            {"name": "내 투자 성향", "url": url_for("game_style")},
        ],
    }
    return app.response_class(json.dumps(data, ensure_ascii=False), mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    """서비스 워커는 사이트 전체를 다루려면 루트 경로에서 내려줘야 함"""
    res = app.send_static_file("sw.js")
    res.headers["Cache-Control"] = "no-cache"
    res.mimetype = "application/javascript"
    return res


@app.route("/offline")
def offline():
    return render_template("offline.html")


@app.route("/health")
def health():
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
