"""
여러 증권사 API에서 시세를 가져오는 모듈.

PRICE_PROVIDERS=kis,kiwoom,ls,fdr 처럼 순서를 정하면
앞에서부터 시도하고, 실패하면 다음 증권사로 자동으로 넘어갑니다.

지원: kis(한국투자증권), kiwoom(키움증권 REST), ls(LS증권), fdr(FinanceDataReader), demo(가짜 시세)
"""
import os
import json
import time
import fcntl
import random
import hashlib
import logging
from datetime import datetime, timedelta

import requests

log = logging.getLogger("providers")
TIMEOUT = (3, 6)  # (연결, 응답) 초
TOKEN_DIR = os.environ.get("TOKEN_DIR", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "tokens"))


def _int(v):
    """'+70,000' '-1500' 같은 문자열을 정수로"""
    if v is None or v == "":
        return 0
    return int(float(str(v).replace(",", "").strip()))


def _float(v):
    if v is None or v == "":
        return 0.0
    return float(str(v).replace(",", "").replace("%", "").strip())


def _truthy(v):
    return str(v).lower() in ("1", "true", "yes", "y")


class ProviderError(Exception):
    pass


def _json(r):
    try:
        return r.json()
    except ValueError:
        raise ProviderError(f"HTTP {r.status_code} 응답을 해석할 수 없음")


# ------------------------------------------------------------------ 토큰 저장소
class TokenStore:
    """
    접근토큰을 파일에 저장해서 gunicorn 워커끼리 공유합니다.
    (한국투자증권은 토큰 발급 횟수 제한이 있어서 매번 새로 받으면 막힙니다)
    """

    def __init__(self, name):
        os.makedirs(TOKEN_DIR, exist_ok=True)
        self.path = os.path.join(TOKEN_DIR, f"{name}.json")
        self.lock_path = self.path + ".lock"

    def get(self, issue_fn):
        tok = self._read()
        if tok:
            return tok
        with open(self.lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                tok = self._read()  # 기다리는 동안 다른 워커가 발급했을 수 있음
                if tok:
                    return tok
                token, expires_at = issue_fn()
                with open(self.path, "w") as f:
                    json.dump({"token": token, "expires_at": expires_at}, f)
                return token
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def clear(self):
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass

    def _read(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            if d["expires_at"] - time.time() > 300:  # 만료 5분 전이면 새로 발급
                return d["token"]
        except (FileNotFoundError, ValueError, KeyError):
            pass
        return None


# ------------------------------------------------------------------ 공통 인터페이스
class Provider:
    key = ""
    label = ""

    def configured(self):
        return True

    def quote(self, code):
        """{'price': int, 'diff': int, 'pct': float, 'name': str|None}"""
        raise NotImplementedError

    def daily(self, code, days=120):
        """[('YYYY-MM-DD', 종가), ...] 오래된 순"""
        raise NotImplementedError


# ------------------------------------------------------------------ 한국투자증권
class KISProvider(Provider):
    key, label = "kis", "한국투자증권"

    def __init__(self):
        self.appkey = os.environ.get("KIS_APPKEY", "")
        self.appsecret = os.environ.get("KIS_APPSECRET", "")
        self.mock = _truthy(os.environ.get("KIS_MOCK", "false"))
        self.base = ("https://openapivts.koreainvestment.com:29443" if self.mock
                     else "https://openapi.koreainvestment.com:9443")
        self.tokens = TokenStore("kis_mock" if self.mock else "kis")

    def configured(self):
        return bool(self.appkey and self.appsecret)

    def _issue(self):
        r = requests.post(f"{self.base}/oauth2/tokenP", timeout=TIMEOUT, json={
            "grant_type": "client_credentials",
            "appkey": self.appkey, "appsecret": self.appsecret})
        d = _json(r)
        if "access_token" not in d:
            raise ProviderError(f"토큰 발급 실패: {d}")
        return d["access_token"], time.time() + int(d.get("expires_in", 86400))

    def _get(self, path, tr_id, params):
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.tokens.get(self._issue)}",
            "appkey": self.appkey, "appsecret": self.appsecret,
            "tr_id": tr_id, "custtype": "P",
        }
        r = requests.get(f"{self.base}{path}", headers=headers, params=params, timeout=TIMEOUT)
        d = _json(r)
        if d.get("rt_cd") != "0":
            if "token" in str(d.get("msg1", "")).lower() or r.status_code in (401, 403):
                self.tokens.clear()
            raise ProviderError(f"{d.get('msg_cd')} {d.get('msg1')}")
        return d

    def quote(self, code):
        d = self._get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                      {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
        o = d["output"]
        return {"price": _int(o["stck_prpr"]), "diff": _int(o["prdy_vrss"]),
                "pct": _float(o["prdy_ctrt"]), "name": None}

    def daily(self, code, days=120):
        end = datetime.now()
        start = end - timedelta(days=days)
        d = self._get("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                      "FHKST03010100", {
                          "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                          "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                          "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                          "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
        rows = [r for r in d.get("output2", []) if r.get("stck_bsop_date")]
        out = [(f"{r['stck_bsop_date'][:4]}-{r['stck_bsop_date'][4:6]}-{r['stck_bsop_date'][6:]}",
                _int(r["stck_clpr"])) for r in rows]
        return sorted(out)


# ------------------------------------------------------------------ 키움증권 REST
class KiwoomProvider(Provider):
    key, label = "kiwoom", "키움증권"

    def __init__(self):
        self.appkey = os.environ.get("KIWOOM_APPKEY", "")
        self.secretkey = os.environ.get("KIWOOM_SECRETKEY", "")
        self.mock = _truthy(os.environ.get("KIWOOM_MOCK", "false"))
        self.base = "https://mockapi.kiwoom.com" if self.mock else "https://api.kiwoom.com"
        self.tokens = TokenStore("kiwoom_mock" if self.mock else "kiwoom")

    def configured(self):
        return bool(self.appkey and self.secretkey)

    def _issue(self):
        r = requests.post(f"{self.base}/oauth2/token", timeout=TIMEOUT,
                          headers={"content-type": "application/json;charset=UTF-8"},
                          json={"grant_type": "client_credentials",
                                "appkey": self.appkey, "secretkey": self.secretkey})
        d = _json(r)
        if not d.get("token"):
            raise ProviderError(f"토큰 발급 실패: {d}")
        try:
            exp = datetime.strptime(d["expires_dt"], "%Y%m%d%H%M%S").timestamp()
        except (KeyError, ValueError):
            exp = time.time() + 3600 * 12
        return d["token"], exp

    def _post(self, path, api_id, body):
        headers = {
            "content-type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.tokens.get(self._issue)}",
            "api-id": api_id, "cont-yn": "N", "next-key": "",
        }
        r = requests.post(f"{self.base}{path}", headers=headers, json=body, timeout=TIMEOUT)
        d = _json(r)
        if d.get("return_code") not in (0, "0", None):
            if r.status_code in (401, 403) or "토큰" in str(d.get("return_msg", "")):
                self.tokens.clear()
            raise ProviderError(f"{d.get('return_code')} {d.get('return_msg')}")
        return d

    def quote(self, code):
        d = self._post("/api/dostk/stkinfo", "ka10001", {"stk_cd": code})
        price = abs(_int(d.get("cur_prc")))
        if not price:
            raise ProviderError("현재가 없음")
        return {"price": price, "diff": _int(d.get("pred_pre")),
                "pct": _float(d.get("flu_rt")), "name": d.get("stk_nm") or None}

    def daily(self, code, days=120):
        d = self._post("/api/dostk/chart", "ka10081", {
            "stk_cd": code, "base_dt": datetime.now().strftime("%Y%m%d"), "upd_stkpc_tp": "1"})
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        out = []
        for r in d.get("stk_dt_pole_chart_qry", []):
            dt = r.get("dt", "")
            if dt and dt >= cutoff:
                out.append((f"{dt[:4]}-{dt[4:6]}-{dt[6:]}", abs(_int(r.get("cur_prc")))))
        return sorted(out)


# ------------------------------------------------------------------ LS증권
class LSProvider(Provider):
    key, label = "ls", "LS증권"
    BASE = "https://openapi.ls-sec.co.kr:8080"

    def __init__(self):
        self.appkey = os.environ.get("LS_APPKEY", "")
        self.appsecret = os.environ.get("LS_APPSECRET", "")
        self.tokens = TokenStore("ls")  # 모의/실전은 발급받은 키에 따라 자동 구분됨

    def configured(self):
        return bool(self.appkey and self.appsecret)

    def _issue(self):
        r = requests.post(f"{self.BASE}/oauth2/token", timeout=TIMEOUT,
                          headers={"content-type": "application/x-www-form-urlencoded"},
                          data={"grant_type": "client_credentials", "appkey": self.appkey,
                                "appsecretkey": self.appsecret, "scope": "oob"})
        d = _json(r)
        if "access_token" not in d:
            raise ProviderError(f"토큰 발급 실패: {d}")
        return d["access_token"], time.time() + int(d.get("expires_in", 86400))

    def _post(self, path, tr_cd, body):
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.tokens.get(self._issue)}",
            "tr_cd": tr_cd, "tr_cont": "N", "tr_cont_key": "",
        }
        r = requests.post(f"{self.BASE}{path}", headers=headers, json=body, timeout=TIMEOUT)
        d = _json(r)
        rsp = str(d.get("rsp_cd", ""))
        if r.status_code != 200 or (rsp and rsp not in ("00000", "00136", "00310")):
            if r.status_code in (401, 403):
                self.tokens.clear()
            raise ProviderError(f"{rsp} {d.get('rsp_msg')}")
        return d

    def quote(self, code):
        d = self._post("/stock/market-data", "t1102", {"t1102InBlock": {"shcode": code}})
        o = d.get("t1102OutBlock") or {}
        price = _int(o.get("price"))
        if not price:
            raise ProviderError("현재가 없음")
        change = abs(_int(o.get("change")))
        sign = str(o.get("sign", "3"))          # 1상한 2상승 3보합 4하한 5하락
        diff = -change if sign in ("4", "5") else (change if sign in ("1", "2") else 0)
        return {"price": price, "diff": diff, "pct": _float(o.get("diff")),
                "name": o.get("hname") or None}

    def daily(self, code, days=120):
        cnt = min(500, max(20, int(days * 0.7)))
        d = self._post("/stock/market-data", "t1305", {"t1305InBlock": {
            "shcode": code, "dwmcode": 1, "date": "", "idx": 0, "cnt": cnt}})
        out = []
        for r in d.get("t1305OutBlock1", []):
            dt = r.get("date", "")
            if dt:
                out.append((f"{dt[:4]}-{dt[4:6]}-{dt[6:]}", _int(r.get("close"))))
        return sorted(out)


# ------------------------------------------------------------------ FinanceDataReader (키 불필요)
class FDRProvider(Provider):
    key, label = "fdr", "FinanceDataReader"

    def configured(self):
        try:
            import FinanceDataReader  # noqa: F401
            return True
        except ImportError:
            return False

    def daily(self, code, days=120):
        import FinanceDataReader as fdr
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        df = fdr.DataReader(code, start)
        if df is None or df.empty:
            raise ProviderError("데이터 없음")
        return [(i.strftime("%Y-%m-%d"), int(r["Close"])) for i, r in df.iterrows()]

    def quote(self, code):
        hist = self.daily(code, 10)
        cur = hist[-1][1]
        prev = hist[-2][1] if len(hist) > 1 else cur
        return {"price": cur, "diff": cur - prev,
                "pct": (cur - prev) / prev * 100 if prev else 0, "name": None}


# ------------------------------------------------------------------ 데모 (오프라인 테스트)
class DemoProvider(Provider):
    key, label = "demo", "데모 시세"

    def daily(self, code, days=120):
        seed = int(hashlib.md5(code.encode()).hexdigest()[:8], 16)
        rnd = random.Random(seed)
        price = rnd.randint(10, 300) * 1000
        today = datetime.now().date()
        out = []
        for i in range(days, -1, -1):
            d = today - timedelta(days=i)
            if d.weekday() >= 5:
                continue
            price = max(1000, price * (1 + rnd.gauss(0.0005, 0.02)))
            out.append((d.isoformat(), int(round(price, -1))))
        m = random.Random(seed + int(time.time() // 60))
        out[-1] = (out[-1][0], int(round(out[-1][1] * (1 + m.gauss(0, 0.004)), -1)))
        return out

    def quote(self, code):
        h = self.daily(code, 10)
        cur, prev = h[-1][1], h[-2][1]
        return {"price": cur, "diff": cur - prev, "pct": (cur - prev) / prev * 100, "name": None}


REGISTRY = {p.key: p for p in (KISProvider, KiwoomProvider, LSProvider, FDRProvider, DemoProvider)}


# ------------------------------------------------------------------ 여러 증권사를 묶는 서비스
class PriceService:
    """
    - 설정된 순서대로 증권사를 시도하고, 실패하면 다음 증권사로 넘어감
    - 연속으로 실패한 증권사는 잠시 쉬게 함 (서킷 브레이커)
    - 현재가/일봉을 캐시해서 API 호출 제한에 걸리지 않게 함
    """

    def __init__(self, order=None, quote_ttl=10, daily_ttl=600, cooldown=60):
        order = order or os.environ.get("PRICE_PROVIDERS", "kis,kiwoom,ls,fdr")
        self.providers = []
        for k in [x.strip().lower() for x in order.split(",") if x.strip()]:
            if k not in REGISTRY:
                log.warning("알 수 없는 시세 제공자: %s", k)
                continue
            p = REGISTRY[k]()
            if p.configured():
                self.providers.append(p)
            else:
                log.info("%s: 키가 없어 건너뜀", p.label)
        if not self.providers:
            log.warning("사용 가능한 시세 제공자가 없어 데모 시세를 씁니다.")
            self.providers.append(DemoProvider())
        self.quote_ttl, self.daily_ttl, self.cooldown = quote_ttl, daily_ttl, cooldown
        self._q, self._d = {}, {}
        self.status = {p.key: {"label": p.label, "ok": 0, "fail": 0, "last_error": None,
                               "last_ms": None, "down_until": 0} for p in self.providers}

    # 내부: 순서대로 시도
    def _try(self, method, *args):
        errors = []
        for p in self.providers:
            st = self.status[p.key]
            if st["down_until"] > time.time():
                continue
            t0 = time.time()
            try:
                result = getattr(p, method)(*args)
                st["ok"] += 1
                st["last_ms"] = int((time.time() - t0) * 1000)
                st["fail_streak"] = 0
                return result, p
            except Exception as e:
                st["fail"] += 1
                st["last_error"] = f"{datetime.now():%H:%M:%S} {type(e).__name__}: {e}"[:200]
                st["fail_streak"] = st.get("fail_streak", 0) + 1
                if st["fail_streak"] >= 3:
                    st["down_until"] = time.time() + self.cooldown
                errors.append(f"{p.label}: {e}")
                log.warning("%s %s 실패: %s", p.label, method, e)
        raise ProviderError(" / ".join(errors) or "모든 증권사가 일시 중지 상태")

    def quote(self, code):
        hit = self._q.get(code)
        if hit and time.time() - hit[0] < self.quote_ttl:
            return hit[1]
        try:
            q, p = self._try("quote", code)
        except ProviderError:
            return hit[1] if hit else None  # 모두 실패하면 마지막 값이라도
        q = dict(q, source=p.label, at=datetime.now().strftime("%H:%M:%S"))
        self._q[code] = (time.time(), q)
        return q

    def daily(self, code, days=120):
        hit = self._d.get(code)
        if hit and time.time() - hit[0] < self.daily_ttl:
            data = hit[1]
        else:
            try:
                data, _ = self._try("daily", code, days)
            except ProviderError:
                data = hit[1] if hit else None
            if data:
                self._d[code] = (time.time(), data)
        if not data:
            return None
        # 차트 마지막 값을 실시간 현재가로 맞춤
        q = self.quote(code)
        if q:
            today = datetime.now().strftime("%Y-%m-%d")
            data = list(data)
            if data[-1][0] == today:
                data[-1] = (today, q["price"])
            elif datetime.now().weekday() < 5:
                data.append((today, q["price"]))
        return data

    def compare(self, code):
        """모든 증권사에 동시에 물어서 결과를 비교 (진단용)"""
        rows = []
        for p in self.providers:
            t0 = time.time()
            try:
                q = p.quote(code)
                rows.append({"label": p.label, "ok": True, "price": q["price"],
                             "diff": q["diff"], "pct": q["pct"],
                             "ms": int((time.time() - t0) * 1000)})
            except Exception as e:
                rows.append({"label": p.label, "ok": False, "error": str(e)[:150],
                             "ms": int((time.time() - t0) * 1000)})
        return rows
