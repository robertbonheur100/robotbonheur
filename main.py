"""
╔══════════════════════════════════════════════════════════════╗
║     BONHEURBOT PRO v6 ELITE — underlying_symbol FIX         ║
║      Multi-User Trading Bot — Deriv + Binance               ║
║                                                             ║
║  DERIV REST API PAT — KOREKSYON FINAL:                      ║
║   ✅ proposal PAT   → "underlying_symbol" (pa "symbol")     ║
║   ✅ digits PAT     → "underlying_symbol"                   ║
║   ✅ klasik WS      → "symbol" (pa chanje)                  ║
║                                                             ║
║  STRATEGIES ELITE v6 (konplè):                              ║
║   ✅ SuperTrend, Heikin Ashi, Chandelier Exit               ║
║   ✅ VWAP, Pivot Points, Market Regime                      ║
║   ✅ Confluence Elite, Deriv Pro Elite                      ║
║   ✅ Binance Gold, Binance Crypto, Confluence Binance       ║
║   ✅ EMA, RSI, MACD, SMC, Breakout, OB, Stoch, AI          ║
║   ✅ Scalping, Fibonacci, FVG                               ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, json, time, threading, logging, math, uuid, secrets, requests
from datetime import datetime, timedelta, date
from flask import Flask, request as freq, jsonify, render_template_string, session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROFIT_WALLET   = "0x2ba88a4d6cabaded5d06c75ef3b3efec386acaef"
PROFIT_PCT      = 0.05
DERIV_REST_BASE = "https://api.derivws.com/trading/v1"
DERIV_WS_PUBLIC = "wss://api.derivws.com/trading/v1/options/ws/public"
DERIV_WS_LEGACY = "wss://ws.derivws.com/websockets/v3"
DERIV_WS_APP_IDS = ["1089", "36544", "16929"]

ACCESS_CODES = {
    "BONHEURWIIN": {"created_at": None,        "used": False, "is_adm": True},
    "HJKy8kFD":    {"created_at": time.time(), "used": False, "is_adm": False},
    "GHt3hjI6":    {"created_at": time.time(), "used": False, "is_adm": False},
}
CODE_TTL_SECONDS = 2592000  # 30 jou

def check_access(code):
    code = code.strip().upper()
    if code not in ACCESS_CODES: return False, "Kòd aksè pa valid — kontakte admin"
    e = ACCESS_CODES[code]
    if e["created_at"] is None or e.get("is_adm"): return True, "✓ Aksè admin akòde"
    age = time.time() - e["created_at"]
    if age > CODE_TTL_SECONDS: return False, f"Kòd ekspire depi {int((age-CODE_TTL_SECONDS)/86400)} jou"
    if e["used"]: return False, "Kòd sa deja itilize — kontakte admin"
    return True, f"✓ Aksè akòde — {int((CODE_TTL_SECONDS-age)/86400)} jou rete"

def use_code(code):
    code = code.strip().upper()
    if code in ACCESS_CODES:
        e = ACCESS_CODES[code]
        if e["created_at"] is not None and not e.get("is_adm"):
            ACCESS_CODES[code]["used"] = True

SESSIONS_FILE = "sessions.json"
_sessions = {}
_sess_lock = threading.Lock()

def _load_sessions():
    global _sessions
    try:
        if os.path.exists(SESSIONS_FILE):
            with open(SESSIONS_FILE) as f: _sessions = json.load(f)
    except: _sessions = {}

def _save_sessions():
    try:
        with open(SESSIONS_FILE, "w") as f: json.dump(_sessions, f, indent=2)
    except Exception as e: logger.error(f"Sessions save: {e}")

_load_sessions()

def create_session():
    token = secrets.token_hex(32)
    expire = (date.today() + timedelta(days=30)).isoformat()
    with _sess_lock:
        _sessions[token] = {"expire": expire, "created": time.time()}
        _save_sessions()
    return token, expire

def validate_session(token):
    if not token: return False, "Pa gen sesyon"
    with _sess_lock: sess = _sessions.get(token)
    if not sess: return False, "Sesyon pa valid — antre kòd aksè ou"
    exp = date.fromisoformat(sess["expire"])
    if date.today() > exp:
        with _sess_lock: _sessions.pop(token, None); _save_sessions()
        return False, "Abònman ou ekspire (30 jou) — kontakte admin"
    return True, f"Sesyon aktif — {(exp - date.today()).days} jou rete"

SECRET_KEY_FILE = "secret.key"
if os.path.exists(SECRET_KEY_FILE):
    with open(SECRET_KEY_FILE) as f: _secret = f.read().strip()
else:
    _secret = secrets.token_hex(32)
    with open(SECRET_KEY_FILE, "w") as f: f.write(_secret)

app = Flask(__name__)
app.secret_key = _secret
_user_states = {}
_user_lock   = threading.Lock()

def get_state():
    if "uid" not in session: session["uid"] = str(uuid.uuid4())
    uid = session["uid"]
    with _user_lock:
        if uid not in _user_states:
            _user_states[uid] = {
                "uid": uid, "access": False, "session_token": None,
                "bot_id": None, "broker": None, "connected": False, "running": False,
                "balance": 0.0, "total_pnl": 0.0, "profit_sent": 0.0,
                "trades": [], "log": [], "config": {},
                "deriv_api": None, "binance_api": None, "deriv_digits_api": None,
            }
    return _user_states[uid]


def is_pat_token(token: str) -> bool:
    return token.strip().lower().startswith("pat_")


# ═══════════════════════════════════════════════════════════
# ██  DERIV PAT REST CLIENT — underlying_symbol FIX  ██
# ═══════════════════════════════════════════════════════════
class DerivRESTClient:
    def __init__(self, pat_token: str, app_id: str = "1089", timeout: int = 25):
        self.token       = pat_token
        self.app_id      = app_id
        self.timeout     = timeout
        self._bal        = 0.0
        self._loginid    = "PAT_USER"
        self._account_id = None
        self._headers    = {
            "Authorization": f"Bearer {pat_token}",
            "Deriv-App-ID":  app_id,
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "User-Agent":    "BonheurBot/6.0",
        }

    def _get(self, path, params=None):
        url = f"{DERIV_REST_BASE}{path}"
        r   = requests.get(url, headers=self._headers, params=params or {}, timeout=self.timeout)
        logger.debug(f"GET {url} → {r.status_code}: {r.text[:300]}")
        r.raise_for_status()
        return r.json()

    def _post_rest(self, path, data=None):
        url = f"{DERIV_REST_BASE}{path}"
        r   = requests.post(url, headers=self._headers, json=data or {}, timeout=self.timeout)
        logger.debug(f"POST {url} → {r.status_code}: {r.text[:300]}")
        r.raise_for_status()
        return r.json()

    def _fresh_ws_url(self) -> str:
        if not self._account_id:
            logger.warning("PAT OTP: _account_id vide — pa ka jwenn OTP")
            return ""
        try:
            endpoint = f"/options/accounts/{self._account_id}/otp"
            logger.info(f"PAT OTP POST: {DERIV_REST_BASE}{endpoint}")
            resp = self._post_rest(endpoint)
            logger.info(f"PAT OTP response: {str(resp)[:400]}")
            ws_url = ""
            if isinstance(resp, dict):
                data = resp.get("data") or {}
                if isinstance(data, dict):
                    ws_url = (data.get("url") or data.get("ws_url") or
                              data.get("websocket_url") or data.get("wss_url") or "")
                if not ws_url:
                    ws_url = (resp.get("url") or resp.get("ws_url") or
                              resp.get("websocket_url") or resp.get("wss_url") or "")
                if not ws_url and isinstance(resp.get("data"), str):
                    ws_url = resp["data"]
            if ws_url:
                logger.info(f"PAT OTP URL jwenn: {ws_url[:120]}")
                return ws_url
            else:
                logger.error(f"PAT OTP: pa jwenn URL. Response konplè: {resp}")
                return ""
        except requests.HTTPError as e:
            logger.error(f"PAT OTP HTTP {e.response.status_code}: {e.response.text[:300]}")
            if e.response.status_code in (404, 400):
                logger.error(f"⚠ account_id '{self._account_id}' ka pa bon")
            return ""
        except Exception as e:
            logger.error(f"PAT OTP exception: {e}")
            return ""

    def _ws_call(self, build_msg_fn, check_done_fn, timeout=30):
        ws_url = self._fresh_ws_url()
        if not ws_url:
            return None, "Pa ka kreye OTP WS URL — verifye account_id ak App ID"
        import websocket as wsl
        res  = [None]; err  = [None]; done = threading.Event()
        def on_open(ws):
            try: build_msg_fn(ws)
            except Exception as e: err[0] = str(e); done.set()
        def on_msg(ws, msg):
            try:
                d = json.loads(msg)
                check_done_fn(d, ws, res, err, done)
            except Exception as e: err[0] = str(e); done.set()
        def on_err(ws, e):
            if not done.is_set(): err[0] = f"WS erè: {str(e)[:150]}"; done.set()
        def on_close(ws, *a): pass
        try:
            ws = wsl.WebSocketApp(ws_url, on_open=on_open, on_message=on_msg,
                                  on_error=on_err, on_close=on_close)
            t = threading.Thread(target=ws.run_forever, daemon=True)
            t.start()
            done.wait(timeout=timeout)
            if not done.is_set():
                try: ws.close()
                except: pass
                return None, f"Timeout {timeout}s"
            return res[0], err[0]
        except Exception as e:
            return None, f"WS koneksyon echwe: {e}"

    def _extract_accounts(self, data) -> list:
        if isinstance(data, list): return data
        if not isinstance(data, dict): return []
        d2 = data.get("data") or {}
        if isinstance(d2, list) and d2: return d2
        if isinstance(d2, dict):
            accs = d2.get("accounts") or d2.get("account_list") or []
            if accs: return accs
        accs = data.get("accounts") or data.get("account_list") or []
        if accs: return accs
        if "account_id" in data or "id" in data or "loginid" in data: return [data]
        return []

    def connect(self) -> float:
        errors = []
        try:
            data = self._get("/options/accounts")
            logger.info(f"PAT /options/accounts RAW: {str(data)[:600]}")
            accounts = self._extract_accounts(data)
            logger.info(f"PAT accounts jwenn: {len(accounts)} | {str(accounts)[:300]}")
            if not accounts and isinstance(data, dict) and "balance" in data:
                self._bal     = float(data["balance"] or 0)
                self._loginid = data.get("loginid") or data.get("account_id") or "PAT_USER"
                self._account_id = (data.get("account_id") or data.get("id") or
                                    data.get("loginid") or "")
                logger.info(f"PAT root balance | id={self._account_id} | ${self._bal:.2f}")
                if self._account_id:
                    test_url = self._fresh_ws_url()
                    if test_url: logger.info("PAT OTP WS URL obtenu ✓")
                    return self._bal
            if accounts:
                real = next((a for a in accounts
                             if str(a.get("account_type","")).lower() in ("real","financial","gaming")),
                            accounts[0])
                logger.info(f"PAT kont chwazi: {real}")
                self._account_id = (real.get("account_id") or real.get("id") or
                                    real.get("accountId") or real.get("loginid") or
                                    real.get("login") or "")
                self._loginid    = (real.get("loginid") or real.get("login") or
                                    self._account_id or "PAT_USER")
                self._bal        = float(real.get("balance", 0) or
                                         real.get("available_balance", 0) or 0)
                logger.info(f"PAT account_id='{self._account_id}' | loginid='{self._loginid}' | ${self._bal:.2f}")
                if not self._account_id:
                    logger.error(f"PAT: pa ka jwenn account_id nan: {real}")
                    errors.append("account_id introuvable nan response accounts")
                else:
                    test_url = self._fresh_ws_url()
                    if test_url: logger.info("PAT OTP WS URL obtenu — trading ✓")
                    else: logger.warning("PAT OTP echwe — verifye account_id ak App ID")
                    return self._bal
        except requests.HTTPError as e:
            errors.append(f"GET /options/accounts: HTTP {e.response.status_code} → {e.response.text[:200]}")
            logger.error(f"PAT /options/accounts HTTP erè: {e.response.status_code} | {e.response.text[:300]}")
        except Exception as e:
            errors.append(f"GET /options/accounts: {str(e)[:200]}")
            logger.error(f"PAT /options/accounts exception: {e}")
        try:
            data = self._post_rest("/options/accounts", {"currency": "USD", "group": "row", "account_type": "demo"})
            logger.info(f"PAT create account: {str(data)[:200]}")
            inner = data.get("data") or data
            self._account_id = inner.get("account_id") or inner.get("id") or ""
            self._bal        = float(inner.get("balance", 0) or 0)
            if self._account_id: return self._bal
        except Exception as e:
            errors.append(f"POST /options/accounts: {str(e)[:100]}")
        try:
            self._get("/health")
            logger.warning("PAT health OK men pa jwenn accounts")
            return 0.0
        except Exception as e:
            errors.append(f"GET /health: {str(e)[:80]}")
        raise Exception(
            "Token PAT echwe ak tout endpoints.\n\nDETAY ERÈ:\n" +
            "\n".join(errors) +
            "\n\nSOLISYON REKÒMANDE — TOKEN KLASIK:\n"
            "  app.deriv.com → foto ou → API Token\n"
            "  Create new token → ✓ Read ✓ Trade ✓ Payments\n"
            "  Kole token (PA kòmanse ak pat_) — App ID: 1089\n"
        )

    def get_balance_sync(self) -> float:
        try:
            data = self._get("/options/accounts")
            accs = (data if isinstance(data, list) else
                    (data.get("data") or data).get("accounts", []) if isinstance(data, dict) else [])
            if accs:
                real = next((a for a in accs if a.get("account_type") == "real"), accs[0])
                b    = float(real.get("balance", 0) or 0)
                if b > 0: self._bal = b; return b
        except: pass
        return self._bal

    def get_candles(self, symbol="R_100", count=200, gran=60):
        if self._account_id:
            def send(ws):
                ws.send(json.dumps({"ticks_history": symbol, "count": count,
                                    "end": "latest", "granularity": gran,
                                    "style": "candles", "adjust_start_time": 1}))
            def recv(d, ws, res, err, done):
                if "candles" in d:
                    res[0] = d["candles"]; done.set()
                    try: ws.close()
                    except: pass
                elif "error" in d:
                    err[0] = d["error"].get("message", "err"); done.set()
            result, e = self._ws_call(send, recv, timeout=25)
            if result:
                return [{"open": float(c["open"]), "high": float(c["high"]),
                         "low":  float(c["low"]),  "close": float(c["close"]),
                         "volume": 1000, "time": c["epoch"]} for c in result]
            if e: logger.warning(f"PAT candles OTP WS: {e}")
        return self._public_candles(symbol, count, gran)

    def _public_candles(self, symbol, count, gran):
        import websocket as wsl
        res = [None]; done = threading.Event()
        def on_msg(ws, msg):
            d = json.loads(msg)
            if "candles" in d: res[0] = d["candles"]; done.set(); ws.close()
            elif "error" in d: done.set(); ws.close()
        def on_open(ws):
            ws.send(json.dumps({"ticks_history": symbol, "count": count,
                                "end": "latest", "granularity": gran,
                                "style": "candles", "adjust_start_time": 1}))
        ws = wsl.WebSocketApp(DERIV_WS_PUBLIC, on_open=on_open, on_message=on_msg)
        threading.Thread(target=ws.run_forever, daemon=True).start()
        done.wait(timeout=25)
        if not res[0]: return []
        return [{"open": float(c["open"]), "high": float(c["high"]),
                 "low":  float(c["low"]),  "close": float(c["close"]),
                 "volume": 1000, "time": c["epoch"]} for c in res[0]]

    def get_ticks(self, symbol="R_10", count=100):
        import websocket as wsl
        res = [None]; done = threading.Event()
        def on_msg(ws, msg):
            d = json.loads(msg)
            if d.get("msg_type") == "history":
                res[0] = d.get("history", {}); done.set(); ws.close()
            elif "error" in d: done.set(); ws.close()
        def on_open(ws):
            ws.send(json.dumps({"ticks_history": symbol, "count": count,
                                "end": "latest", "style": "ticks"}))
        ws = wsl.WebSocketApp(DERIV_WS_PUBLIC, on_open=on_open, on_message=on_msg)
        threading.Thread(target=ws.run_forever, daemon=True).start()
        done.wait(timeout=25)
        if not res[0]: return []
        return [{"price": float(p), "time": t}
                for p, t in zip(res[0].get("prices", []), res[0].get("times", []))]

    def place_trade(self, symbol, direction, amount=1.0, duration_secs=60):
        ct = "CALL" if direction == "BUY" else "PUT"
        if   duration_secs <= 60:    dv, du = 1,  "m"
        elif duration_secs <= 300:   dv, du = 5,  "m"
        elif duration_secs <= 900:   dv, du = 15, "m"
        elif duration_secs <= 3600:  dv, du = 1,  "h"
        else:                        dv, du = 4,  "h"
        def send(ws):
            proposal_msg = {
                "proposal":          1,
                "amount":            max(0.5, float(amount)),
                "basis":             "stake",
                "contract_type":     ct,
                "currency":          "USD",
                "underlying_symbol": symbol,   # ✅ FIX
                "duration":          dv,
                "duration_unit":     du,
            }
            logger.info(f"PAT proposal: {json.dumps(proposal_msg)}")
            ws.send(json.dumps(proposal_msg))
        def recv(d, ws, res, err, done):
            mt = d.get("msg_type", "")
            if mt == "proposal":
                if "error" in d: err[0] = d["error"]["message"]; done.set(); return
                ws.send(json.dumps({"buy": d["proposal"]["id"],
                                    "price": d["proposal"]["ask_price"]}))
            elif mt == "buy":
                if "error" in d: err[0] = d["error"]["message"]; done.set(); return
                res[0] = d.get("buy", {}); done.set()
                try: ws.close()
                except: pass
        result, e = self._ws_call(send, recv, timeout=35)
        if e: raise Exception(e)
        return result or {}

    def place_digits_trade(self, symbol, contract_type, amount=0.35, barrier=None):
        proposal_msg = {
            "proposal":          1,
            "amount":            max(0.35, float(amount)),
            "basis":             "stake",
            "contract_type":     contract_type,
            "currency":          "USD",
            "underlying_symbol": symbol,   # ✅ FIX
            "duration":          5,
            "duration_unit":     "t",
        }
        if barrier is not None: proposal_msg["barrier"] = str(barrier)
        logger.info(f"PAT digits proposal: {json.dumps(proposal_msg)}")
        def send(ws): ws.send(json.dumps(proposal_msg))
        def recv(d, ws, res, err, done):
            mt = d.get("msg_type", "")
            if mt == "proposal":
                if "error" in d: err[0] = d["error"]["message"]; done.set(); return
                ws.send(json.dumps({"buy": d["proposal"]["id"],
                                    "price": d["proposal"]["ask_price"]}))
            elif mt == "buy":
                if "error" in d: err[0] = d["error"]["message"]; done.set(); return
                res[0] = d.get("buy", {}); done.set()
                try: ws.close()
                except: pass
        result, e = self._ws_call(send, recv, timeout=35)
        if e: raise Exception(e)
        return result or {}

    def wait_contract_result(self, contract_id, timeout=35):
        def send(ws):
            ws.send(json.dumps({"proposal_open_contract": 1,
                                "contract_id": contract_id, "subscribe": 1}))
        def recv(d, ws, res, err, done):
            mt = d.get("msg_type", "")
            if mt == "proposal_open_contract":
                poc = d.get("proposal_open_contract", {})
                if poc.get("status", "") in ("won", "lost", "sold"):
                    res[0] = poc; done.set()
                    try: ws.close()
                    except: pass
        result, _ = self._ws_call(send, recv, timeout=timeout)
        return result

    def transfer_to_account(self, account_id, amount):
        def send(ws):
            ws.send(json.dumps({"transfer_between_accounts": 1,
                                "account_to":  account_id,
                                "amount":      round(float(amount), 2),
                                "currency":    "USD"}))
        def recv(d, ws, res, err, done):
            mt = d.get("msg_type", "")
            if mt == "transfer_between_accounts":
                if "error" in d: err[0] = d["error"]["message"]; done.set(); return
                res[0] = d; done.set()
                try: ws.close()
                except: pass
        result, e = self._ws_call(send, recv, timeout=20)
        if e: raise Exception(e)
        return result

    @property
    def balance(self): return self._bal
    @property
    def loginid(self): return self._loginid


# ═══════════════════════════════════════════════════════════
# WEBSOCKET AUTHORIZE — TOKEN KLASIK SÈLMAN
# ═══════════════════════════════════════════════════════════
def connect_ws_authorize(token: str, app_id: str, timeout: int = 20):
    import websocket as wsl
    done   = threading.Event()
    result = [None, None, None, None]
    def on_open(ws): ws.send(json.dumps({"authorize": token}))
    def on_msg(ws, msg):
        try: d = json.loads(msg)
        except: return
        if d.get("msg_type") == "authorize":
            if "error" in d:
                result[0] = False; result[3] = d["error"].get("message", "Token invalib")
            else:
                result[0] = True
                result[1]  = float(d["authorize"].get("balance", 0))
                result[2]  = d["authorize"].get("loginid")
            done.set()
            try: ws.close()
            except: pass
        elif "error" in d and not done.is_set():
            result[0] = False; result[3] = d["error"].get("message", "Erè enkoni")
            done.set()
            try: ws.close()
            except: pass
    def on_err(ws, e):
        if not done.is_set():
            result[0] = False; result[3] = f"WS erè: {str(e)[:150]}"; done.set()
    url = f"{DERIV_WS_LEGACY}?app_id={app_id}"
    try:
        ws = wsl.WebSocketApp(url, on_open=on_open, on_message=on_msg, on_error=on_err)
        threading.Thread(target=ws.run_forever, daemon=True).start()
        done.wait(timeout=timeout)
        if not done.is_set():
            try: ws.close()
            except: pass
            return False, 0.0, None, app_id, f"Timeout ({timeout}s)"
        if result[0]: return True, result[1], result[2], app_id, None
        return False, 0.0, None, app_id, result[3] or "Echèk enkoni"
    except Exception as e:
        return False, 0.0, None, app_id, f"Erè: {str(e)}"


def connect_classic_token(token: str, app_id: str = "1089"):
    ids    = [app_id] + [a for a in DERIV_WS_APP_IDS if a != app_id]
    errors = []
    for aid in ids:
        ok, bal, lid, _, err = connect_ws_authorize(token, aid, timeout=15)
        if ok:
            logger.info(f"Klasik OK | app_id={aid} | {lid} | ${bal:.2f}")
            return True, bal, lid, aid, f"Klasik (WS) | {lid} | app_id={aid}"
        errors.append(f"app_id={aid}: {err}")
    return False, 0.0, None, app_id, (
        "Token Klasik echwe.\n\nDETAY:\n" + "\n".join(errors[:3]) +
        "\n\nVERIFYE:\n1. Token valid epi pa ekspire\n"
        "2. Pèmisyon: Read + Trade + Payments\n"
        "3. Kreye nouvo sou app.deriv.com → API Token"
    )


def connect_deriv_token(token: str, app_id: str = "1089"):
    if is_pat_token(token):
        logger.info(f"PAT → REST api.derivws.com app_id={app_id}")
        try:
            c   = DerivRESTClient(token, app_id)
            bal = c.connect()
            logger.info(f"PAT OK | {c.loginid} | ${bal:.2f}")
            return True, bal, c.loginid, None, f"PAT (REST+OTP) | {c.loginid}"
        except Exception as e:
            return False, 0.0, None, None, str(e)
    else:
        logger.info(f"Klasik → WebSocket app_id={app_id}")
        return connect_classic_token(token, app_id)


# ═══════════════════════════════════════════════════════════
# INDIKATÈ TEKNIK — KONPLÈ v6 ELITE
# ═══════════════════════════════════════════════════════════
def ema(prices, p):
    if len(prices) < p: return []
    k = 2 / (p + 1); e = [sum(prices[:p]) / p]
    for x in prices[p:]: e.append(x * k + e[-1] * (1 - k))
    return e

def rsi(prices, p=14):
    if len(prices) < p + 1: return 50
    d = [prices[i+1] - prices[i] for i in range(len(prices) - 1)]
    g = sum(x for x in d[-p:] if x > 0) / p
    l = sum(-x for x in d[-p:] if x < 0) / p
    return 100 if l == 0 else 100 - (100 / (1 + g / l))

def macd(prices):
    e12 = ema(prices, 12); e26 = ema(prices, 26)
    if not e12 or not e26: return 0, 0
    m = e12[-1] - e26[-1]; return m, m * 0.2

def bb(prices, p=20, s=2.0):
    if len(prices) < p: return None, None, None
    avg = sum(prices[-p:]) / p
    std = math.sqrt(sum((x - avg) ** 2 for x in prices[-p:]) / p)
    return avg + s * std, avg, avg - s * std

def atr(candles, p=14):
    if len(candles) < p + 1: return 0
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]; l = candles[i]["low"]; pc = candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-p:]) / p if trs else 0

def stoch_k(candles, p=14):
    if len(candles) < p: return 50
    hi = max(x["high"] for x in candles[-p:]); lo = min(x["low"] for x in candles[-p:])
    return ((candles[-1]["close"] - lo) / (hi - lo) * 100) if hi != lo else 50

def calc_adx_full(candles, p=14):
    if len(candles) < p + 2: return 0, 0, 0
    trs = []; pdms = []; mdms = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]; l = candles[i]["low"]
        ph = candles[i-1]["high"]; pl = candles[i-1]["low"]; pc = candles[i-1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        up = h - ph; dn = pl - l
        pdms.append(up if up > dn and up > 0 else 0)
        mdms.append(dn if dn > up and dn > 0 else 0)
        trs.append(tr)
    av  = sum(trs[-p:]) / p if sum(trs[-p:]) > 0 else 1
    pdi = 100 * sum(pdms[-p:]) / (p * av)
    mdi = 100 * sum(mdms[-p:]) / (p * av)
    adx = 100 * abs(pdi - mdi) / (pdi + mdi + 0.001)
    return round(adx, 2), round(pdi, 2), round(mdi, 2)

# ═══════════════════════════════════════════════════════════
# ██  NOUVO: SUPERTREND INDIKATÈ  ██
# ═══════════════════════════════════════════════════════════
def supertrend(candles, p=10, mult=3.0):
    if len(candles) < p + 5: return "NONE", 0.0
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, len(candles))]
    atr_vals = [sum(trs[i-p+1:i+1])/p for i in range(p-1, len(trs))]
    if not atr_vals: return "NONE", 0.0
    n    = len(atr_vals)
    hl2  = [(highs[i+1]+lows[i+1])/2 for i in range(n)]
    ub   = [hl2[i]+mult*atr_vals[i] for i in range(n)]
    lb   = [hl2[i]-mult*atr_vals[i] for i in range(n)]
    upper = list(ub); lower = list(lb)
    for i in range(1, n):
        upper[i] = min(ub[i], upper[i-1]) if closes[i+p-1] <= upper[i-1] else ub[i]
        lower[i] = max(lb[i], lower[i-1]) if closes[i+p-1] >= lower[i-1] else lb[i]
    trend_up   = closes[-1] > lower[-1]
    trend_prev = closes[-2] > lower[-2] if len(closes) >= 2 else trend_up
    price = closes[-1]
    if trend_up:
        dist = (price - lower[-1]) / max(atr_vals[-1], 0.0001)
        conf = min(0.92, 0.75 + min(dist * 0.04, 0.17))
        if not trend_prev: return "BUY", min(0.92, conf + 0.05)
        return "BUY", conf
    else:
        dist = (upper[-1] - price) / max(atr_vals[-1], 0.0001)
        conf = min(0.92, 0.75 + min(dist * 0.04, 0.17))
        if trend_prev: return "SELL", min(0.92, conf + 0.05)
        return "SELL", conf

# ═══════════════════════════════════════════════════════════
# ██  NOUVO: CHANDELIER EXIT  ██
# ═══════════════════════════════════════════════════════════
def chandelier_exit(candles, p=22, mult=3.0):
    if len(candles) < p + 2: return "NONE", 0.0
    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    at = atr(candles, p)
    if at == 0: return "NONE", 0.0
    ce_long  = max(highs[-p:]) - mult * at
    ce_short = min(lows[-p:])  + mult * at
    price = closes[-1]; prev = closes[-2] if len(closes) >= 2 else price
    if price > ce_long  and prev <= ce_long:
        return "BUY",  min(0.90, 0.78 + min((price-ce_long)/max(at,0.0001)*0.04, 0.12))
    elif price < ce_short and prev >= ce_short:
        return "SELL", min(0.90, 0.78 + min((ce_short-price)/max(at,0.0001)*0.04, 0.12))
    elif price > ce_long:  return "BUY",  0.75
    elif price < ce_short: return "SELL", 0.75
    return "NONE", 0.0

# ═══════════════════════════════════════════════════════════
# ██  NOUVO: HEIKIN ASHI TREND  ██
# ═══════════════════════════════════════════════════════════
def heikin_ashi_trend(candles, lookback=5):
    if len(candles) < lookback + 3: return "NONE", 0.0
    ha = []; prev_o = (candles[0]["open"]+candles[0]["close"])/2
    prev_c = (candles[0]["open"]+candles[0]["high"]+candles[0]["low"]+candles[0]["close"])/4
    for c in candles:
        ha_c=(c["open"]+c["high"]+c["low"]+c["close"])/4; ha_o=(prev_o+prev_c)/2
        ha_h=max(c["high"],ha_o,ha_c); ha_l=min(c["low"],ha_o,ha_c)
        ha.append({"open":ha_o,"high":ha_h,"low":ha_l,"close":ha_c}); prev_o=ha_o; prev_c=ha_c
    recent = ha[-lookback:]
    bull=[b for b in recent if b["close"]>b["open"]]
    bear=[b for b in recent if b["close"]<b["open"]]
    if len(bull)==lookback:
        bodies=[abs(b["close"]-b["open"]) for b in bull]
        return "BUY", 0.83 if bodies[-1]>=bodies[0]*0.7 else 0.77
    if len(bear)==lookback:
        bodies=[abs(b["close"]-b["open"]) for b in bear]
        return "SELL", 0.83 if bodies[-1]>=bodies[0]*0.7 else 0.77
    if len(bull)>=lookback-1: return "BUY",  0.72
    if len(bear)>=lookback-1: return "SELL", 0.72
    return "NONE", 0.0

# ═══════════════════════════════════════════════════════════
# ██  NOUVO: VWAP SIGNAL  ██
# ═══════════════════════════════════════════════════════════
def vwap_signal(candles, lookback=20):
    if len(candles) < lookback: return "NONE", 0.0
    recent=candles[-lookback:]; tpv=0.0; tv=0.0
    for c in recent:
        typ=(c["high"]+c["low"]+c["close"])/3; vol=c.get("volume",1000)
        tpv+=typ*vol; tv+=vol
    if tv==0: return "NONE", 0.0
    vwap=tpv/tv; price=candles[-1]["close"]; at=atr(candles,14)
    if at==0: return "NONE", 0.0
    dist=(price-vwap)/max(at,0.0001)
    if dist>0.3:   return "BUY",  min(0.88,0.72+min(dist*0.03,0.16))
    elif dist<-0.3: return "SELL", min(0.88,0.72+min(abs(dist)*0.03,0.16))
    return "NONE", 0.0

# ═══════════════════════════════════════════════════════════
# STRATEGIES KLASIK
# ═══════════════════════════════════════════════════════════
def strat_ema(c):
    cl=[x["close"] for x in c]
    if len(cl)<25: return "NONE",0
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50) if len(cl)>=50 else None
    if len(e9)<3 or len(e21)<3: return "NONE",0
    r=rsi(cl)
    if e9[-2]<=e21[-2] and e9[-1]>e21[-1] and (not e50 or cl[-1]>e50[-1]) and r<75: return "BUY",0.76
    if e9[-2]>=e21[-2] and e9[-1]<e21[-1] and (not e50 or cl[-1]<e50[-1]) and r>25: return "SELL",0.76
    return "NONE",0

def strat_fibonacci(c):
    if len(c)<60: return "NONE",0
    cl=[x["close"] for x in c]
    hi=max(x["high"] for x in c[-60:]); lo=min(x["low"] for x in c[-60:])
    rng=hi-lo
    if rng==0: return "NONE",0
    r=rsi(cl); price=cl[-1]
    for lvl,conf in [(hi-0.618*rng,0.82),(hi-0.5*rng,0.78),(hi-0.382*rng,0.75)]:
        if abs(price-lvl)/max(lvl,0.0001)<0.001:
            if r<40 and price>lo+(rng*0.2): return "BUY",conf
            if r>60 and price<hi-(rng*0.2): return "SELL",conf
    return "NONE",0

def strat_fvg(c):
    if len(c)<15: return "NONE",0
    cl=[x["close"] for x in c]; e21=ema(cl,21) if len(cl)>=21 else None; r=rsi(cl)
    for i in range(3,min(15,len(c)-1)):
        c1=c[-(i+2)]; c3=c[-i]
        if c3["low"]>c1["high"] and c1["high"]<cl[-1]<c3["low"] and (not e21 or cl[-1]>e21[-1]) and r<60: return "BUY",0.80
        if c3["high"]<c1["low"] and c3["high"]<cl[-1]<c1["low"] and (not e21 or cl[-1]<e21[-1]) and r>40: return "SELL",0.80
    return "NONE",0

def strat_rsi(c):
    cl=[x["close"] for x in c]
    if len(cl)<25: return "NONE",0
    r=rsi(cl); r2=rsi(cl[:-3]) if len(cl)>3 else r
    e50=ema(cl,50) if len(cl)>=50 else None
    if r<30  and (not e50 or cl[-1]>e50[-1]*0.998): return "BUY",0.82
    if 30<=r<42 and r>r2 and (not e50 or cl[-1]>e50[-1]*0.996): return "BUY",0.74
    if r>70  and (not e50 or cl[-1]<e50[-1]*1.002): return "SELL",0.82
    if 58<r<=70 and r<r2 and (not e50 or cl[-1]<e50[-1]*1.004): return "SELL",0.74
    return "NONE",0

def strat_macd(c):
    cl=[x["close"] for x in c]
    if len(cl)<35: return "NONE",0
    up,mid,lo=bb(cl,20,2.0); m,sig=macd(cl)
    if up is None: return "NONE",0
    r=rsi(cl)
    if m>sig and lo  and cl[-1]<=lo:           return "BUY",0.78
    if m>sig and mid and cl[-1]<mid and r<45:  return "BUY",0.72
    if m<sig and up  and cl[-1]>=up:           return "SELL",0.78
    if m<sig and mid and cl[-1]>mid and r>55:  return "SELL",0.72
    return "NONE",0

def strat_breakout(c):
    if len(c)<30: return "NONE",0
    cl=[x["close"] for x in c]
    hi20=max(x["high"] for x in c[-21:-1]); lo20=min(x["low"] for x in c[-21:-1]); r=rsi(cl)
    if cl[-1]>hi20 and cl[-2]<=hi20 and 50<r<75: return "BUY",0.80
    if cl[-1]<lo20 and cl[-2]>=lo20 and 25<r<50: return "SELL",0.80
    if len(cl)>=3 and cl[-2]>hi20 and abs(cl[-1]-hi20)/max(hi20,0.0001)<0.001: return "BUY",0.76
    if len(cl)>=3 and cl[-2]<lo20 and abs(cl[-1]-lo20)/max(lo20,0.0001)<0.001: return "SELL",0.76
    return "NONE",0

def strat_smc(c):
    if len(c)<40: return "NONE",0
    cl=[x["close"] for x in c]; e50=ema(cl,50) if len(cl)>=50 else None; r=rsi(cl)
    shi=max(x["high"] for x in c[-30:-5]); slo=min(x["low"] for x in c[-30:-5])
    if cl[-1]>shi and cl[-2]<=shi and (not e50 or cl[-1]>e50[-1]) and 45<r<75: return "BUY",0.84
    if cl[-1]<slo and cl[-2]>=slo and (not e50 or cl[-1]<e50[-1]) and 25<r<55: return "SELL",0.84
    return "NONE",0

def strat_ob(c):
    if len(c)<30: return "NONE",0
    cl=[x["close"] for x in c]; e21=ema(cl,21) if len(cl)>=21 else None; r=rsi(cl)
    for i in range(4,20):
        b=c[-(i+1)]; body=abs(b["close"]-b["open"]); rng=b["high"]-b["low"]
        if rng==0 or body/rng<0.65: continue
        if b["close"]<b["open"] and b["close"]<=cl[-1]<=b["open"] and (not e21 or cl[-1]>e21[-1]*0.997) and r<55: return "BUY",0.82
        if b["close"]>b["open"] and b["open"]<=cl[-1]<=b["close"] and (not e21 or cl[-1]<e21[-1]*1.003) and r>45: return "SELL",0.82
    return "NONE",0

def strat_stoch(c):
    if len(c)<20: return "NONE",0
    cl=[x["close"] for x in c]; k=stoch_k(c); kp=stoch_k(c[:-1]) if len(c)>1 else k
    e50=ema(cl,50) if len(cl)>=50 else None; r=rsi(cl)
    if k>kp and k<30 and (not e50 or cl[-1]>e50[-1]*0.997): return "BUY",0.80
    if k<kp and k>70 and (not e50 or cl[-1]<e50[-1]*1.003): return "SELL",0.80
    return "NONE",0

def strat_ai(c):
    if len(c)<60: return "NONE",0
    cl=[x["close"] for x in c]; hi=[x["high"] for x in c]; lo_=[x["low"] for x in c]
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50)
    e200=ema(cl,200) if len(cl)>=200 else e50
    r=rsi(cl); m,sig_=macd(cl); up,mid,lo=bb(cl); at=atr(c)
    def norm(val,mn,mx): return 0 if mx==mn else 2*(val-mn)/(mx-mn)-1
    f=[0.0]*8
    if e9 and e21 and e50:
        if e9[-1]>e21[-1]>e50[-1]: f[0]=1.0
        elif e9[-1]<e21[-1]<e50[-1]: f[0]=-1.0
        else: f[0]=(e9[-1]-e21[-1])/(at if at else 1)*0.5
    f[1]=norm(r,0,100); f[1]=-f[1]
    if m and sig_: f[2]=1.0 if m>sig_ and m>0 else(-1.0 if m<sig_ and m<0 else 0.5 if m>sig_ else -0.5)
    if up and mid and lo: f[3]=norm(cl[-1],lo,up); f[3]=-f[3]
    if len(cl)>=6: mom=(cl[-1]-cl[-6])/max(abs(cl[-6]),0.001)*100; f[4]=max(-1,min(1,mom/2))
    if at and mid: vr=at/mid*100; f[5]=1.0 if 0.1<vr<0.5 else(0.5 if vr<=0.1 else -0.5)
    hi20=max(hi[-20:]); lo20=min(lo_[-20:]); rng20=hi20-lo20 if hi20!=lo20 else 1
    pos=(cl[-1]-lo20)/rng20; f[6]=1.0 if pos<0.2 else(-1.0 if pos>0.8 else 0.0)
    if e50 and e200: trend=(e50[-1]-e200[-1])/max(e200[-1],0.001)*100; f[7]=max(-1,min(1,trend*10))
    W=[2.8,2.2,1.8,1.5,1.2,0.8,1.6,1.9]
    score=sum(f[i]*W[i] for i in range(8))/sum(W)
    if score>=0.35:  return "BUY",  min(0.92,0.68+score*0.35)
    if score<=-0.35: return "SELL", min(0.92,0.68+abs(score)*0.35)
    return "NONE",0

def strat_scalping(c):
    if len(c)<20: return "NONE",0
    cl=[x["close"] for x in c]; e5=ema(cl,5); e13=ema(cl,13)
    e50=ema(cl,50) if len(cl)>=50 else None
    if len(e5)<3 or len(e13)<3: return "NONE",0
    r=rsi(cl,9)
    if e5[-1]>e13[-1] and r<70 and (not e50 or cl[-1]>e50[-1]*0.997): return "BUY",0.74
    if e5[-1]<e13[-1] and r>30 and (not e50 or cl[-1]<e50[-1]*1.003): return "SELL",0.74
    return "NONE",0

# ═══════════════════════════════════════════════════════════
# PIVOT POINTS + MARKET REGIME
# ═══════════════════════════════════════════════════════════
def calc_pivot_points(candles):
    if len(candles)<20: return None
    recent=candles[-20:]; hi=max(x["high"] for x in recent); lo=min(x["low"] for x in recent)
    cl=candles[-1]["close"]; pp=(hi+lo+cl)/3; rng=hi-lo
    return {"pp":pp,"r1":2*pp-lo,"r2":pp+rng,"r3":hi+2*(pp-lo),
            "s1":2*pp-hi,"s2":pp-rng,"s3":lo-2*(hi-pp),
            "fib_r1":pp+0.382*rng,"fib_r2":pp+0.618*rng,
            "fib_s1":pp-0.382*rng,"fib_s2":pp-0.618*rng}

def pivot_signal(candles, trend):
    pv=calc_pivot_points(candles)
    if not pv: return False,0.0
    price=candles[-1]["close"]; tol=0.008
    if trend=="TRENDING_UP":
        lvls=[pv["s1"],pv["s2"],pv["fib_s1"],pv["fib_s2"],pv["pp"]]
    elif trend=="TRENDING_DN":
        lvls=[pv["r1"],pv["r2"],pv["fib_r1"],pv["fib_r2"],pv["pp"]]
    else:
        lvls=[]
    for lvl in lvls:
        if abs(price-lvl)/max(lvl,0.0001)<tol:
            bonus=0.07 if lvl in(pv.get("s1"),pv.get("r1"),pv.get("fib_s1"),pv.get("fib_r1")) else 0.05
            return True, bonus
    return False,0.0

def market_regime(candles):
    if len(candles)<20: return "UNKNOWN",0
    cl=[x["close"] for x in candles]; adx,pdi,mdi=calc_adx_full(candles,14); at=atr(candles)
    mid_val=sum(cl[-20:])/20; atr_pct=(at/mid_val*100) if mid_val>0 else 0
    e50=ema(cl,50) if len(cl)>=50 else None
    if adx>12 and pdi>mdi+1:   regime="TRENDING_UP"; score=min(10,adx/3)
    elif adx>12 and mdi>pdi+1: regime="TRENDING_DN"; score=min(10,adx/3)
    elif atr_pct>4.0:          regime="VOLATILE";    score=2
    else:                      regime="RANGING";     score=3
    if e50:
        if cl[-1]>e50[-1] and regime=="TRENDING_UP": score=min(10,score+1.5)
        if cl[-1]<e50[-1] and regime=="TRENDING_DN": score=min(10,score+1.5)
    return regime,round(score,1)

# ═══════════════════════════════════════════════════════════
# ██████  CONFLUENCE ELITE v6  ██████
# ═══════════════════════════════════════════════════════════
def strat_confluence_elite(c, min_strats=3, min_per_conf=0.65):
    if len(c)<20: return "NONE",0
    cl=[x["close"] for x in c]; at=atr(c)
    if at==0: return "NONE",0
    mid_price=sum(cl[-20:])/20 if len(cl)>=20 else cl[-1]
    if (at/mid_price*100) if mid_price>0 else 0 < 0.005: return "NONE",0
    adx,pdi,mdi=calc_adx_full(c,14); regime,_=market_regime(c)
    st_sig,st_conf=supertrend(c,p=10,mult=3.0)
    ha_sig,ha_conf=heikin_ashi_trend(c,lookback=5)
    ce_sig,ce_conf=chandelier_exit(c,p=22,mult=3.0)
    vw_sig,vw_conf=vwap_signal(c,lookback=20)
    classic_fns=[
        (strat_ema,1.4),(strat_rsi,1.6),(strat_macd,1.5),(strat_smc,1.7),
        (strat_breakout,1.4),(strat_ob,1.5),(strat_stoch,1.3),(strat_ai,1.8),
        (strat_scalping,1.2),(strat_fvg,1.3),(strat_fibonacci,1.4)
    ]
    buy_score=sell_score=0.0; buy_cnt=sell_cnt=0; NW=2.5
    if st_sig=="BUY"  and st_conf>=min_per_conf: buy_score +=st_conf*NW; buy_cnt +=1
    elif st_sig=="SELL" and st_conf>=min_per_conf: sell_score+=st_conf*NW; sell_cnt+=1
    if ha_sig=="BUY"  and ha_conf>=min_per_conf: buy_score +=ha_conf*NW; buy_cnt +=1
    elif ha_sig=="SELL" and ha_conf>=min_per_conf: sell_score+=ha_conf*NW; sell_cnt+=1
    if ce_sig=="BUY"  and ce_conf>=min_per_conf: buy_score +=ce_conf*NW; buy_cnt +=1
    elif ce_sig=="SELL" and ce_conf>=min_per_conf: sell_score+=ce_conf*NW; sell_cnt+=1
    if vw_sig=="BUY"  and vw_conf>=min_per_conf: buy_score +=vw_conf*1.8; buy_cnt+=1
    elif vw_sig=="SELL" and vw_conf>=min_per_conf: sell_score+=vw_conf*1.8; sell_cnt+=1
    for fn,w in classic_fns:
        try:
            s,conf=fn(c)
            if s=="BUY"  and conf>=min_per_conf: buy_score +=conf*w; buy_cnt +=1
            elif s=="SELL" and conf>=min_per_conf: sell_score+=conf*w; sell_cnt+=1
        except: pass
    if regime=="VOLATILE": return "NONE",0
    dom=1.15
    if regime=="RANGING":
        ns=[st_sig,ha_sig,ce_sig]
        bn=sum(1 for s in ns if s=="BUY"); sn=sum(1 for s in ns if s=="SELL")
        if bn>=2 and buy_cnt>=min_strats and buy_score>sell_score*dom:
            _,pb=pivot_signal(c,"TRENDING_UP")
            return "BUY",round(min(0.92,0.74+(buy_score/max(buy_cnt,1)/5.0)*0.12+pb),3)
        if sn>=2 and sell_cnt>=min_strats and sell_score>buy_score*dom:
            _,pb=pivot_signal(c,"TRENDING_DN")
            return "SELL",round(min(0.92,0.74+(sell_score/max(sell_cnt,1)/5.0)*0.12+pb),3)
        return "NONE",0
    if regime=="TRENDING_UP" and buy_cnt>=min_strats and buy_score>sell_score*dom:
        _,pb=pivot_signal(c,"TRENDING_UP"); ab=min(0.05,adx/500)
        return "BUY",round(min(0.95,0.75+(buy_score/max(buy_cnt,1)/5.0)*0.13+pb+ab),3)
    if regime=="TRENDING_DN" and sell_cnt>=min_strats and sell_score>buy_score*dom:
        _,pb=pivot_signal(c,"TRENDING_DN"); ab=min(0.05,adx/500)
        return "SELL",round(min(0.95,0.75+(sell_score/max(sell_cnt,1)/5.0)*0.13+pb+ab),3)
    return "NONE",0

# ═══════════════════════════════════════════════════════════
# ██████  DERIV PRO ELITE v6  ██████
# ═══════════════════════════════════════════════════════════
def strat_deriv_pro_elite(c):
    if len(c)<50: return "NONE",0
    cl=[x["close"] for x in c]; hi=[x["high"] for x in c]; lo_=[x["low"] for x in c]
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50) if len(cl)>=50 else None
    if not e9 or not e21 or len(e9)<3 or len(e21)<3: return "NONE",0
    r=rsi(cl,14); at=atr(c); m,sig_=macd(cl); macd_h=m-sig_
    m2,sig2=(macd(cl[:-1]) if len(cl)>=2 else (m,sig_)); macd_hp=m2-sig2
    up_bb,mid_bb,lo_bb=bb(cl,20,2.0); k=stoch_k(c,14); kp=stoch_k(c[:-2]) if len(c)>2 else k
    if at==0 or not mid_bb: return "NONE",0
    atr_pct=at/mid_bb*100
    if atr_pct<0.01 or atr_pct>5.0: return "NONE",0
    adx,pdi,mdi=calc_adx_full(c,14)
    if adx<12: return "NONE",0
    trend_up=e9[-1]>e21[-1]; trend_down=e9[-1]<e21[-1]
    if e50:
        trend_up   = trend_up   and cl[-1]>e50[-1]*0.998
        trend_down = trend_down and cl[-1]<e50[-1]*1.002
    if not trend_up and not trend_down: return "NONE",0
    if trend_up  and not (e9[-1]>e9[-2] or e21[-1]>e21[-2]): return "NONE",0
    if trend_down and not (e9[-1]<e9[-2] or e21[-1]<e21[-2]): return "NONE",0
    hi20=max(hi[-21:-1]); lo20=min(lo_[-21:-1]); hi10=max(hi[-11:-1]); lo10=min(lo_[-11:-1])
    roc3=(cl[-1]-cl[-4])/max(abs(cl[-4]),0.001)*100 if len(cl)>=4 else 0
    roc5v=(cl[-1]-cl[-6])/max(abs(cl[-6]),0.001)*100 if len(cl)>=6 else 0
    last_body=abs(cl[-1]-c[-1]["open"])
    last_range=max(c[-1]["high"]-c[-1]["low"],0.00001)
    body_ratio=last_body/last_range
    st_sig,_=supertrend(c,p=10,mult=3.0)
    if trend_up:
        score=0.0
        bo_score=0.0
        if cl[-1]>hi20 and cl[-2]<=hi20:  bo_score+=2.0
        elif cl[-1]>hi20*0.997:            bo_score+=0.8
        if cl[-1]>hi10 and cl[-2]<=hi10:  bo_score+=1.0
        elif cl[-1]>hi10*0.998:            bo_score+=0.4
        score+=min(3.5,bo_score)
        if 25<=r<=45:       score+=3.0
        elif 45<r<=55:      score+=2.0
        elif 55<r<=65:      score+=1.2
        elif r<25:          score+=2.5
        elif r<70:          score+=0.8
        macd_ok=(m>sig_ and macd_h>macd_hp)
        if macd_ok and m>0: score+=2.5
        elif macd_ok:       score+=1.8
        elif m>sig_:        score+=1.0
        if k<25 and k>kp:   score+=2.5
        elif k<35 and k>kp: score+=1.5
        elif k>kp:          score+=0.8
        if lo_bb and cl[-1]<=lo_bb*1.005:  score+=2.0
        elif mid_bb and cl[-1]<mid_bb:     score+=0.8
        if roc3>0 and roc5v>0: score+=1.5
        elif roc3>0:           score+=0.7
        if body_ratio>=0.60 and cl[-1]>c[-1]["open"]: score+=1.5
        elif body_ratio>=0.40 and cl[-1]>c[-1]["open"]: score+=0.8
        if adx>=45:   score+=2.0
        elif adx>=35: score+=1.5
        elif adx>=25: score+=0.8
        elif adx>=12: score+=0.3
        if st_sig=="BUY": score+=2.0
        in_piv,piv_b=pivot_signal(c,"TRENDING_UP")
        if in_piv: score+=1.5
        if score>=5.0:
            pct=score/15.0; conf=min(0.95,0.76+pct*0.25)
            if adx>=50: conf=min(0.95,conf+0.02)
            return "BUY",round(conf,3)
    if trend_down:
        score=0.0
        bo_score=0.0
        if cl[-1]<lo20 and cl[-2]>=lo20:  bo_score+=2.0
        elif cl[-1]<lo20*1.003:            bo_score+=0.8
        if cl[-1]<lo10 and cl[-2]>=lo10:  bo_score+=1.0
        elif cl[-1]<lo10*1.002:            bo_score+=0.4
        score+=min(3.5,bo_score)
        if 55<=r<=75:       score+=3.0
        elif 45<=r<55:      score+=2.0
        elif 35<=r<45:      score+=1.2
        elif r>75:          score+=2.5
        elif r>30:          score+=0.8
        macd_ok=(m<sig_ and macd_h<macd_hp)
        if macd_ok and m<0: score+=2.5
        elif macd_ok:       score+=1.8
        elif m<sig_:        score+=1.0
        if k>75 and k<kp:   score+=2.5
        elif k>65 and k<kp: score+=1.5
        elif k<kp:          score+=0.8
        if up_bb and cl[-1]>=up_bb*0.995:  score+=2.0
        elif mid_bb and cl[-1]>mid_bb:     score+=0.8
        if roc3<0 and roc5v<0: score+=1.5
        elif roc3<0:           score+=0.7
        if body_ratio>=0.60 and cl[-1]<c[-1]["open"]: score+=1.5
        elif body_ratio>=0.40 and cl[-1]<c[-1]["open"]: score+=0.8
        if adx>=45:   score+=2.0
        elif adx>=35: score+=1.5
        elif adx>=25: score+=0.8
        elif adx>=12: score+=0.3
        if st_sig=="SELL": score+=2.0
        in_piv,piv_b=pivot_signal(c,"TRENDING_DN")
        if in_piv: score+=1.5
        if score>=5.0:
            pct=score/15.0; conf=min(0.95,0.76+pct*0.25)
            if adx>=50: conf=min(0.95,conf+0.02)
            return "SELL",round(conf,3)
    return "NONE",0

# ═══════════════════════════════════════════════════════════
# STRATEGIES BINANCE — GOLD + CRYPTO + CONFLUENCE
# ═══════════════════════════════════════════════════════════
def strat_binance_gold(c):
    if len(c)<60: return "NONE",0
    cl=[x["close"] for x in c]; hi=[x["high"] for x in c]; lo_=[x["low"] for x in c]
    vol=[x.get("volume",0) for x in c]
    e20=ema(cl,20); e50=ema(cl,50); e200=ema(cl,200) if len(cl)>=200 else ema(cl,100)
    if not e20 or not e50 or not e200: return "NONE",0
    r=rsi(cl,14); at=atr(c); m,sig_=macd(cl); up,mid,lo=bb(cl,20,2.0)
    k=stoch_k(c,14); adx_v,pdi_v,mdi_v=calc_adx_full(c,14)
    if not at or not mid or adx_v<20: return "NONE",0
    avg_vol=sum(vol[-20:])/20 if len(vol)>=20 else 1
    if avg_vol>0 and vol[-1]<avg_vol*0.5: return "NONE",0
    trend_up=e20[-1]>e50[-1] and e50[-1]>e200[-1] and cl[-1]>e200[-1]
    trend_dn=e20[-1]<e50[-1] and e50[-1]<e200[-1] and cl[-1]<e200[-1]
    if not trend_up and not trend_dn: return "NONE",0
    if at/mid*100<0.03: return "NONE",0
    bp=0; sp=0
    if trend_up: bp+=3
    if trend_dn: sp+=3
    if adx_v>=35: bp+=2 if trend_up else 0; sp+=2 if trend_dn else 0
    elif adx_v>=25: bp+=1 if trend_up else 0; sp+=1 if trend_dn else 0
    if trend_up and 30<=r<=55: bp+=3
    elif trend_up and r<30: bp+=2
    if trend_dn and 45<=r<=70: sp+=3
    elif trend_dn and r>70: sp+=2
    if m>sig_ and m>0: bp+=2
    if m<sig_ and m<0: sp+=2
    if lo and cl[-1]<=lo*1.002: bp+=3
    elif mid and cl[-1]<mid*1.005: bp+=1
    if up and cl[-1]>=up*0.998: sp+=3
    elif mid and cl[-1]>mid*0.995: sp+=1
    if k<25: bp+=2
    elif k<35: bp+=1
    if k>75: sp+=2
    elif k>65: sp+=1
    if vol[-1]>avg_vol*1.8:
        if bp>sp: bp+=2
        elif sp>bp: sp+=2
    st_sig,_=supertrend(c,p=10,mult=3.0)
    if st_sig=="BUY": bp+=2
    if st_sig=="SELL": sp+=2
    if bp>=7 and bp>sp+2 and trend_up: return "BUY",min(0.91,0.72+bp*0.018)
    if sp>=7 and sp>bp+2 and trend_dn: return "SELL",min(0.91,0.72+sp*0.018)
    return "NONE",0

def strat_binance_crypto(c):
    if len(c)<50: return "NONE",0
    cl=[x["close"] for x in c]; hi=[x["high"] for x in c]; lo_=[x["low"] for x in c]
    vol=[x.get("volume",0) for x in c]
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50)
    e200=ema(cl,200) if len(cl)>=200 else ema(cl,100)
    if not e9 or not e21 or not e50: return "NONE",0
    r=rsi(cl,14); at=atr(c); m,sig_=macd(cl); up,mid,lo=bb(cl,20,2.0)
    k=stoch_k(c,14); adx_v,pdi_v,mdi_v=calc_adx_full(c,14)
    avg_vol=sum(vol[-20:])/20 if len(vol)>=20 else 1
    curr_vol=vol[-1] if vol[-1]>0 else avg_vol
    if adx_v<18 or (avg_vol>0 and curr_vol<avg_vol*0.4): return "NONE",0
    long_bull=e200 and cl[-1]>e200[-1]; long_bear=e200 and cl[-1]<e200[-1]
    bp=0; sp=0
    if e9[-1]>e21[-1]>e50[-1]: bp+=3+(2 if long_bull else 0)
    if e9[-1]<e21[-1]<e50[-1]: sp+=3+(2 if long_bear else 0)
    if len(e9)>=2 and e9[-2]<e21[-2] and e9[-1]>e21[-1]: bp+=3
    if len(e9)>=2 and e9[-2]>e21[-2] and e9[-1]<e21[-1]: sp+=3
    if adx_v>=35: bp+=2 if pdi_v>mdi_v else 0; sp+=2 if mdi_v>pdi_v else 0
    elif adx_v>=25: bp+=1 if pdi_v>mdi_v else 0; sp+=1 if mdi_v>pdi_v else 0
    if 30<=r<=50 and long_bull: bp+=3
    elif r<30: bp+=2
    elif r<45: bp+=1
    if 50<=r<=70 and long_bear: sp+=3
    elif r>70: sp+=2
    elif r>55: sp+=1
    if m>sig_ and m>0: bp+=2
    elif m>sig_: bp+=1
    if m<sig_ and m<0: sp+=2
    elif m<sig_: sp+=1
    if lo and cl[-1]<=lo: bp+=3
    elif lo and cl[-1]<=lo*1.01: bp+=1
    if up and cl[-1]>=up: sp+=3
    elif up and cl[-1]>=up*0.99: sp+=1
    if k<20: bp+=2
    elif k<35: bp+=1
    if k>80: sp+=2
    elif k>65: sp+=1
    vol_surge=curr_vol>avg_vol*2.0
    if vol_surge:
        if bp>sp: bp+=3
        elif sp>bp: sp+=3
    hi20=max(hi[-21:-1]); lo20=min(lo_[-21:-1])
    if cl[-1]>hi20 and cl[-2]<=hi20: bp+=3 if curr_vol>avg_vol*1.5 else 1
    if cl[-1]<lo20 and cl[-2]>=lo20: sp+=3 if curr_vol>avg_vol*1.5 else 1
    st_sig,_=supertrend(c,p=10,mult=3.0)
    if st_sig=="BUY": bp+=2
    if st_sig=="SELL": sp+=2
    if bp>=8 and bp>sp+2: return "BUY",min(0.92,0.70+bp*0.016)
    if sp>=8 and sp>bp+2: return "SELL",min(0.92,0.70+sp*0.016)
    return "NONE",0

def strat_confluence_binance(c, symbol="BTCUSDT"):
    if "XAU" in symbol.upper() or "GOLD" in symbol.upper():
        primary,conf_p=strat_binance_gold(c)
    else:
        primary,conf_p=strat_binance_crypto(c)
    if primary=="NONE": return "NONE",0
    others=[(strat_rsi,1.3),(strat_macd,1.2),(strat_ema,1.1),(strat_smc,1.4),(strat_ob,1.2)]
    confirm=0; total_conf=conf_p
    for fn,w in others:
        try:
            s,conf=fn(c)
            if s==primary and conf>=0.65: confirm+=1; total_conf+=conf*w
        except: pass
    if confirm>=3: return primary,max(0.75,min(0.92,total_conf/(confirm+2)))
    return "NONE",0

# ═══════════════════════════════════════════════════════════
# STRATEGIES DICT — KONPLÈ
# ═══════════════════════════════════════════════════════════
STRATEGIES = {
    "confluence":    strat_confluence_elite,
    "deriv_pro":     strat_deriv_pro_elite,
    "supertrend":    supertrend,
    "heikin_ashi":   heikin_ashi_trend,
    "chandelier":    chandelier_exit,
    "ai":            strat_ai,
    "ema":           strat_ema,
    "fibonacci":     strat_fibonacci,
    "fvg":           strat_fvg,
    "rsi":           strat_rsi,
    "macd_bollinger":strat_macd,
    "breakout":      strat_breakout,
    "smc":           strat_smc,
    "order_block":   strat_ob,
    "stoch_ema":     strat_stoch,
    "scalping_pro":  strat_scalping,
    "binance_gold":  strat_binance_gold,
    "binance_crypto":strat_binance_crypto,
}

def run_backtest(candles, strat_name, bal=10000, lot=0.01, sl=20, tp=40):
    fn=STRATEGIES.get(strat_name,strat_confluence_elite)
    equity=[bal]; wins=losses=0; trades=[]
    for i in range(50,len(candles)-1):
        s,conf=fn(candles[:i+1])
        if s=="NONE" or conf<0.65: continue
        entry=candles[i]["close"]; nxt=candles[i+1]
        if s=="BUY":
            if nxt["low"]<=entry-sl*0.0001:    pnl=-sl*lot*10; losses+=1
            elif nxt["high"]>=entry+tp*0.0001: pnl= tp*lot*10; wins+=1
            else:
                pnl=(nxt["close"]-entry)*lot*100000
                wins+=1 if pnl>0 else 0; losses+=0 if pnl>0 else 1
        else:
            if nxt["high"]>=entry+sl*0.0001:  pnl=-sl*lot*10; losses+=1
            elif nxt["low"]<=entry-tp*0.0001: pnl= tp*lot*10; wins+=1
            else:
                pnl=(entry-nxt["close"])*lot*100000
                wins+=1 if pnl>0 else 0; losses+=0 if pnl>0 else 1
        bal+=pnl; equity.append(round(bal,2))
        trades.append({"s":s,"e":round(entry,5),"pnl":round(pnl,2)})
        if len(trades)>=200: break
    tot=wins+losses; net=round(equity[-1]-equity[0],2)
    dd=0; pk=equity[0]
    for e in equity:
        if e>pk: pk=e
        dd=max(dd,(pk-e)/pk*100 if pk else 0)
    gp=sum(t["pnl"] for t in trades if t["pnl"]>0)
    gl=abs(sum(t["pnl"] for t in trades if t["pnl"]<0))
    rets=[equity[i]/equity[i-1]-1 for i in range(1,len(equity))]
    avg=sum(rets)/len(rets) if rets else 0
    std=math.sqrt(sum((r-avg)**2 for r in rets)/len(rets)) if rets else 0
    return {"trades":tot,"wins":wins,"losses":losses,
            "win_rate":round(wins/tot*100,1) if tot else 0,
            "net_pnl":net,"return_pct":round(net/equity[0]*100,2),
            "max_dd":round(dd,2),"pf":round(gp/gl,2) if gl else 999,
            "sharpe":round(avg/std*math.sqrt(252),2) if std>0 else 0,
            "equity":equity[-50:]}


# ═══════════════════════════════════════════════════════════
# DERIV CLIENT — TOKEN KLASIK WebSocket
# ═══════════════════════════════════════════════════════════
class DerivClient:
    def __init__(self, token, app_id="1089"):
        self.token=token; self.app_id=app_id; self._bal=0.0; self._loginid=None

    def connect(self):
        ok,balance,loginid,used,note=connect_classic_token(self.token,self.app_id)
        if not ok: raise Exception(note)
        self._bal=balance; self._loginid=loginid
        if used: self.app_id=used
        return self._bal

    def _auth(self,ws): ws.send(json.dumps({"authorize":self.token}))

    def get_candles(self,symbol="R_100",count=200,gran=60):
        import websocket as wsl
        res=[None]; done=threading.Event()
        def on_msg(ws,msg):
            d=json.loads(msg)
            if d.get("msg_type")=="authorize" and "error" not in d:
                ws.send(json.dumps({"ticks_history":symbol,"count":count,"end":"latest",
                                    "granularity":gran,"style":"candles","adjust_start_time":1}))
            elif "candles" in d: res[0]=d["candles"]; done.set()
            elif "error" in d: done.set()
        w=wsl.WebSocketApp(f"{DERIV_WS_LEGACY}?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws:self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=25)
        if not res[0]: return []
        return [{"open":float(c["open"]),"high":float(c["high"]),"low":float(c["low"]),
                 "close":float(c["close"]),"volume":1000,"time":c["epoch"]} for c in res[0]]

    def place_trade(self,symbol,direction,amount=1.0,duration_secs=60):
        import websocket as wsl
        res=[None]; err=[None]; done=threading.Event()
        ct="CALL" if direction=="BUY" else "PUT"
        if duration_secs<=60:   dv,du=1,"m"
        elif duration_secs<=300: dv,du=5,"m"
        elif duration_secs<=900: dv,du=15,"m"
        elif duration_secs<=3600:dv,du=1,"h"
        else: dv,du=4,"h"
        def on_msg(ws,msg):
            d=json.loads(msg); mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d:
                ws.send(json.dumps({"proposal":1,"amount":max(0.5,float(amount)),"basis":"stake",
                                    "contract_type":ct,"currency":"USD","symbol":symbol,
                                    "duration":dv,"duration_unit":du}))
            elif mt=="proposal":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                ws.send(json.dumps({"buy":d["proposal"]["id"],"price":d["proposal"]["ask_price"]}))
            elif mt=="buy":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                res[0]=d.get("buy",{}); done.set()
        w=wsl.WebSocketApp(f"{DERIV_WS_LEGACY}?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws:self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=30)
        if err[0]: raise Exception(err[0])
        return res[0] or {}

    def get_balance_sync(self):
        import websocket as wsl
        res=[None]; done=threading.Event()
        def on_msg(ws,msg):
            d=json.loads(msg)
            if d.get("msg_type")=="authorize" and "error" not in d:
                ws.send(json.dumps({"balance":1,"account":"current"}))
            elif d.get("msg_type")=="balance":
                b=d.get("balance",{}).get("balance")
                if b is not None: res[0]=float(b); done.set()
            elif "error" in d: done.set()
        w=wsl.WebSocketApp(f"{DERIV_WS_LEGACY}?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws:self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=15)
        if res[0]: self._bal=res[0]
        return res[0] or self._bal

    def transfer_to_account(self,account_id,amount):
        import websocket as wsl
        res=[None]; err=[None]; done=threading.Event()
        def on_msg(ws,msg):
            d=json.loads(msg); mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d:
                ws.send(json.dumps({"transfer_between_accounts":1,"account_to":account_id,
                                    "amount":round(float(amount),2),"currency":"USD"}))
            elif mt=="transfer_between_accounts":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                res[0]=d; done.set()
        w=wsl.WebSocketApp(f"{DERIV_WS_LEGACY}?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws:self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=20)
        if err[0]: raise Exception(err[0])
        return res[0]

    @property
    def balance(self): return self._bal


# ═══════════════════════════════════════════════════════════
# DERIV DIGITS CLIENT
# ═══════════════════════════════════════════════════════════
class DerivDigitsClient:
    def __init__(self,token,app_id="1089"):
        self.token=token; self.app_id=app_id; self._bal=0.0
        self._is_pat=is_pat_token(token)
        self._rest=DerivRESTClient(token,app_id) if self._is_pat else None

    def connect(self):
        ok,bal,_,used,note=connect_deriv_token(self.token,self.app_id)
        if not ok: raise Exception(note)
        self._bal=bal
        if used: self.app_id=used
        if self._rest: self._rest._bal=bal
        return self._bal

    def _auth(self,ws): ws.send(json.dumps({"authorize":self.token}))

    def get_ticks(self,symbol="R_10",count=100):
        if self._is_pat and self._rest: return self._rest.get_ticks(symbol,count)
        import websocket as wsl
        res=[None]; done=threading.Event()
        def on_msg(ws,msg):
            d=json.loads(msg)
            if d.get("msg_type")=="authorize" and "error" not in d:
                ws.send(json.dumps({"ticks_history":symbol,"count":count,"end":"latest","style":"ticks"}))
            elif d.get("msg_type")=="history": res[0]=d.get("history",{}); done.set()
            elif "error" in d: done.set()
        w=wsl.WebSocketApp(f"{DERIV_WS_LEGACY}?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws:self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=25)
        if not res[0]: return []
        return [{"price":float(p),"time":t}
                for p,t in zip(res[0].get("prices",[]),res[0].get("times",[]))]

    def place_digits_trade(self,symbol,contract_type,amount=0.35,barrier=None):
        if self._is_pat and self._rest:
            return self._rest.place_digits_trade(symbol,contract_type,amount,barrier)
        import websocket as wsl
        res=[None]; err=[None]; done=threading.Event()
        proposal={"proposal":1,"amount":max(0.35,float(amount)),"basis":"stake",
                  "contract_type":contract_type,"currency":"USD",
                  "symbol":symbol,
                  "duration":5,"duration_unit":"t"}
        if barrier is not None: proposal["barrier"]=str(barrier)
        def on_msg(ws,msg):
            d=json.loads(msg); mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d: ws.send(json.dumps(proposal))
            elif mt=="proposal":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                ws.send(json.dumps({"buy":d["proposal"]["id"],"price":d["proposal"]["ask_price"]}))
            elif mt=="buy":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                res[0]=d.get("buy",{}); done.set()
        w=wsl.WebSocketApp(f"{DERIV_WS_LEGACY}?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws:self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=30)
        if err[0]: raise Exception(err[0])
        return res[0] or {}

    def wait_contract_result(self,contract_id,timeout=35):
        if self._is_pat and self._rest: return self._rest.wait_contract_result(contract_id,timeout)
        import websocket as wsl
        res=[None]; done=threading.Event()
        def on_msg(ws,msg):
            d=json.loads(msg); mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d:
                ws.send(json.dumps({"proposal_open_contract":1,"contract_id":contract_id,"subscribe":1}))
            elif mt=="proposal_open_contract":
                poc=d.get("proposal_open_contract",{})
                if poc.get("status","") in("won","lost","sold"): res[0]=poc; done.set()
        w=wsl.WebSocketApp(f"{DERIV_WS_LEGACY}?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws:self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=timeout)
        return res[0]

    def get_balance_sync(self):
        if self._is_pat and self._rest: return self._rest.get_balance_sync()
        import websocket as wsl
        res=[None]; done=threading.Event()
        def on_msg(ws,msg):
            d=json.loads(msg)
            if d.get("msg_type")=="authorize" and "error" not in d:
                ws.send(json.dumps({"balance":1,"account":"current"}))
            elif d.get("msg_type")=="balance":
                b=d.get("balance",{}).get("balance")
                if b is not None: res[0]=float(b); done.set()
            elif "error" in d: done.set()
        w=wsl.WebSocketApp(f"{DERIV_WS_LEGACY}?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws:self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=15)
        if res[0]: self._bal=res[0]
        return res[0] or self._bal

    def transfer_to_account(self,account_id,amount):
        if self._is_pat and self._rest: return self._rest.transfer_to_account(account_id,amount)
        import websocket as wsl
        res=[None]; err=[None]; done=threading.Event()
        def on_msg(ws,msg):
            d=json.loads(msg); mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d:
                ws.send(json.dumps({"transfer_between_accounts":1,"account_to":account_id,
                                    "amount":round(float(amount),2),"currency":"USD"}))
            elif mt=="transfer_between_accounts":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                res[0]=d; done.set()
        w=wsl.WebSocketApp(f"{DERIV_WS_LEGACY}?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws:self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=20)
        if err[0]: raise Exception(err[0])
        return res[0]

    @property
    def balance(self): return self._bal


# ═══════════════════════════════════════════════════════════
# BINANCE CLIENTS
# ═══════════════════════════════════════════════════════════
class BinanceClient:
    def __init__(self,key,secret):
        from binance.client import Client
        self.c=Client(key,secret)
    def connect(self):
        for b in self.c.get_account()["balances"]:
            if b["asset"]=="USDT": return float(b["free"])
        return 0.0
    @property
    def balance(self):
        try:
            for b in self.c.get_account()["balances"]:
                if b["asset"]=="USDT": return float(b["free"])
        except: pass
        return 0.0
    def get_candles(self,symbol="BTCUSDT",interval="15m",limit=200):
        k=self.c.get_klines(symbol=symbol,interval=interval,limit=limit)
        return [{"open":float(x[1]),"high":float(x[2]),"low":float(x[3]),
                 "close":float(x[4]),"volume":float(x[5]),"time":x[0]} for x in k]
    def _get_filter(self,symbol,ft):
        info=self.c.get_symbol_info(symbol)
        if not info: return None
        for f in info.get("filters",[]):
            if f["filterType"]==ft: return f
        return None
    def get_min_notional(self,symbol):
        f=self._get_filter(symbol,"MIN_NOTIONAL") or self._get_filter(symbol,"NOTIONAL")
        return float(f.get("minNotional",10)) if f else 10.0
    def get_qty_precision(self,symbol):
        f=self._get_filter(symbol,"LOT_SIZE")
        if not f: return 3
        step=float(f["stepSize"])
        for p,v in [(0,1),(1,.1),(2,.01),(3,.001)]:
            if step>=v: return p
        return 4
    def get_min_qty(self,symbol):
        f=self._get_filter(symbol,"LOT_SIZE"); return float(f["minQty"]) if f else 0.001
    def get_price_precision(self,symbol):
        f=self._get_filter(symbol,"PRICE_FILTER")
        if not f: return 2
        tick=float(f["tickSize"])
        for p,v in [(0,1),(1,.1),(2,.01),(3,.001)]:
            if tick>=v: return p
        return 4
    def place_trade(self,symbol,direction,amount_usdt=10.0,sl_pct=0.018,tp_pct=0.035):
        from binance.enums import SIDE_BUY,SIDE_SELL,TIME_IN_FORCE_GTC
        ticker=self.c.get_symbol_ticker(symbol=symbol); price=float(ticker["price"])
        pp=self.get_price_precision(symbol); qp=self.get_qty_precision(symbol)
        min_qty=self.get_min_qty(symbol); min_not=self.get_min_notional(symbol)
        qty=round(amount_usdt/price,qp); qty=max(qty,min_qty)
        if qty*price<min_not: qty=round(min_not/price*1.01,qp); qty=max(qty,min_qty)
        side=SIDE_BUY if direction=="BUY" else SIDE_SELL
        if direction=="BUY": lp=round(price*1.0005,pp); sl=round(price*(1-sl_pct),pp); tp=round(price*(1+tp_pct),pp)
        else: lp=round(price*0.9995,pp); sl=round(price*(1+sl_pct),pp); tp=round(price*(1-tp_pct),pp)
        order=self.c.order_limit(symbol=symbol,side=side,quantity=qty,price=str(lp),timeInForce=TIME_IN_FORCE_GTC)
        oid=order.get("orderId"); filled=False
        for _ in range(18):
            time.sleep(5)
            try:
                s=self.c.get_order(symbol=symbol,orderId=oid)
                if s["status"]=="FILLED": filled=True; break
                elif s["status"] in("CANCELED","EXPIRED","REJECTED"): break
            except: pass
        if not filled:
            try: self.c.cancel_order(symbol=symbol,orderId=oid)
            except: pass
            return self.c.order_market(symbol=symbol,side=side,quantity=qty)
        try:
            if direction=="BUY":
                self.c.order_oco_sell(symbol=symbol,quantity=qty,price=str(tp),stopPrice=str(sl),
                                      stopLimitPrice=str(round(sl*0.998,pp)),stopLimitTimeInForce=TIME_IN_FORCE_GTC)
            else:
                self.c.order_oco_buy(symbol=symbol,quantity=qty,price=str(tp),stopPrice=str(sl),
                                     stopLimitPrice=str(round(sl*1.002,pp)),stopLimitTimeInForce=TIME_IN_FORCE_GTC)
        except Exception as e: logger.warning(f"OCO: {e}")
        return order
    def send_profit(self,amount):
        try: return self.c.withdraw(coin="USDT",address=PROFIT_WALLET,amount=amount,network="ERC20")
        except Exception as e: logger.error(f"Profit: {e}"); return None

class BinanceUSClient(BinanceClient):
    def __init__(self,key,secret):
        from binance.client import Client
        self.c=Client(key,secret,tld="us")


# ═══════════════════════════════════════════════════════════
# DIGITS LOGIC + UTILS
# ═══════════════════════════════════════════════════════════
def get_last_digit(price):
    s=f"{price:.5f}".replace('.',''); return int(s[-1])

def analyze_digits_ticks(ticks,threshold=4):
    if len(ticks)<50: return "NONE",0
    digits=[get_last_digit(t["price"]) for t in ticks]
    last50=digits[-50:]; last20=digits[-20:]
    oc=sum(1 for d in last50 if d>threshold); uc=sum(1 for d in last50 if d<=threshold)
    o20=sum(1 for d in last20 if d>threshold); u20=sum(1 for d in last20 if d<=threshold)
    last5=digits[-5:]; su=all(d<=threshold for d in last5); so=all(d>threshold for d in last5)
    sig="NONE"; conf=0.0
    if uc>=35 and u20>=14: conf=0.65 if su else 0.72; sig="OVER"
    elif oc>=35 and o20>=14: conf=0.65 if so else 0.72; sig="UNDER"
    if sig=="NONE":
        if u20>=16: sig="OVER"; conf=0.65
        elif o20>=16: sig="UNDER"; conf=0.65
    return sig,conf

def analyze_digits_even_odd(ticks):
    if len(ticks)<30: return "NONE",0
    digits=[get_last_digit(t["price"]) for t in ticks[-30:]]
    evens=sum(1 for d in digits if d%2==0); odds=sum(1 for d in digits if d%2!=0)
    if odds>=22: return "EVEN",0.62
    if evens>=22: return "ODD",0.62
    return "NONE",0

def add_log(st,msg,level="INFO"):
    ts=datetime.now().strftime("%H:%M:%S")
    st["log"].insert(0,{"time":ts,"msg":msg,"level":level})
    st["log"]=st["log"][:80]
    logger.info(f"[{st['uid'][:8]}] {msg}")

def _check_limits(st,cfg):
    target=float(cfg.get("profit_target",0)); loss=float(cfg.get("loss_limit",0))
    if target>0 and st["total_pnl"]>=target:
        add_log(st,f"🎯 OBJEKTIF ${target:.2f} RIVE! Bot kanpe!","SUCCESS"); st["running"]=False; return True
    if loss>0 and st["total_pnl"]<=-abs(loss):
        add_log(st,f"🛑 LIMIT PÈT ${loss:.2f} RIVE! Bot kanpe!","ERROR"); st["running"]=False; return True
    return False

def _refresh_balance(api,st):
    try:
        b=api.get_balance_sync()
        if b and b>0: st["balance"]=b
    except: pass


# ═══════════════════════════════════════════════════════════
# TRADING LOOPS
# ═══════════════════════════════════════════════════════════
def digits_trading_loop(st,bot_id=None):
    if bot_id and st.get("bot_id")!=bot_id: return
    cfg=st["config"]; symbol=cfg.get("symbol","R_10"); lot=float(cfg.get("lot",0.35))
    digit_type=cfg.get("digit_type","over_under"); min_conf=float(cfg.get("min_conf",0.65))
    PAYOUT=0.95; base_lot=round(max(0.35,lot),2); current_lot=base_lot
    consec_losses=0; total_lost=0.0
    add_log(st,f"🎲 Digits Bot | {symbol} | {digit_type} | Base:${base_lot}")
    while st["running"]:
        if bot_id and st.get("bot_id")!=bot_id: add_log(st,"⏹ Digits bot anile","WARN"); return
        if _check_limits(st,cfg): break
        try:
            api=st.get("deriv_digits_api")
            if not api: add_log(st,"Digits API pa konekte","ERROR"); st["running"]=False; break
            _refresh_balance(api,st)
            if st["balance"]<current_lot:
                add_log(st,f"⚠ Balans ${st['balance']:.2f} ensifizan — reset","WARN")
                current_lot=base_lot; consec_losses=0; total_lost=0.0; time.sleep(10); continue
            ticks=api.get_ticks(symbol,100)
            if len(ticks)<30: add_log(st,"Pa ase ticks — tann 20sek...","WARN"); time.sleep(20); continue
            sig="NONE"; conf=0.0; contract_type=""; barrier=None
            if digit_type=="over_under":
                action,conf=analyze_digits_ticks(ticks,threshold=4)
                if action=="OVER": contract_type="DIGITOVER"; barrier=4; sig="OVER 4"
                elif action=="UNDER": contract_type="DIGITUNDER"; barrier=5; sig="UNDER 5"
            elif digit_type=="even_odd":
                action,conf=analyze_digits_even_odd(ticks)
                if action=="EVEN": contract_type="DIGITEVEN"; sig="EVEN"
                elif action=="ODD": contract_type="DIGITODD"; sig="ODD"
            if sig=="NONE": add_log(st,"⏭ Pa gen siyal klè — tann 15sek..."); time.sleep(15); continue
            if conf<min_conf: add_log(st,f"⏭ Conf {conf:.0%} < {min_conf:.0%}"); time.sleep(15); continue
            add_log(st,f"✅ Siyal | {sig} | Conf:{conf:.0%} | Mise:${current_lot:.2f}")
            bal_before=st["balance"]
            try:
                r=api.place_digits_trade(symbol,contract_type,current_lot,barrier)
                cid=r.get("contract_id")
                if not cid: add_log(st,"Trade echwe — pa gen contract_id","ERROR"); time.sleep(10); continue
                bal_open=float(r.get("balance_after",bal_before-current_lot)); st["balance"]=bal_open
                add_log(st,f"⏳ #{cid} | {sig} | ${bal_open:.2f} | Ap tann...","SUCCESS")
                result=api.wait_contract_result(cid,timeout=35)
                pnl=0.0; won=False
                if result:
                    status=result.get("status","")
                    buy_price=float(result.get("buy_price",current_lot)); sell_price=float(result.get("sell_price",0))
                    if status=="won": pnl=sell_price-buy_price; won=True; st["balance"]=bal_open+pnl; add_log(st,f"✅ WON! +${pnl:.2f} | ${st['balance']:.2f}","SUCCESS")
                    elif status=="lost": pnl=-buy_price; st["balance"]=bal_open+pnl; add_log(st,f"❌ LOST -${buy_price:.2f} | ${st['balance']:.2f}","WARN")
                    else:
                        time.sleep(5); nb=api.get_balance_sync()
                        if nb and nb>0: pnl=nb-bal_before; st["balance"]=nb; won=pnl>0
                        else: pnl=-current_lot
                else:
                    time.sleep(5); nb=api.get_balance_sync()
                    if nb and nb>0: st["balance"]=nb; pnl=nb-bal_before; won=pnl>0.01
                    else: pnl=-current_lot; won=False
                if won: current_lot=base_lot; consec_losses=0; total_lost=0.0
                else:
                    loss=abs(pnl) if abs(pnl)>0.01 else current_lot; total_lost+=loss; consec_losses+=1
                    if consec_losses<=4:
                        current_lot=max(base_lot,min(round((total_lost+base_lot)/PAYOUT,2),50.0))
                        add_log(st,f"⚠ Pèt #{consec_losses}/4 | Total:${total_lost:.2f} | Prochèn:${current_lot:.2f}","WARN")
                    else:
                        add_log(st,"🔄 Reset apre 4 pèt | Tann 90sek...","WARN")
                        current_lot=base_lot; consec_losses=0; total_lost=0.0; time.sleep(90)
                trade={"id":len(st["trades"])+1,"time":datetime.now().strftime("%H:%M:%S"),
                       "symbol":symbol,"side":sig,"entry":round(ticks[-1]["price"],5),
                       "conf":f"{conf:.0%}","strategy":f"Digits-{digit_type}","tf":"ticks",
                       "stake":round(current_lot,2),"pnl":round(pnl,2),"status":"won" if won else "lost","regime":"—"}
                st["trades"].insert(0,trade); st["total_pnl"]+=pnl
                if won and pnl>0:
                    ps=round(pnl*PROFIT_PCT,2); st["profit_sent"]+=ps
                    if ps>=0.50:
                        try: api.transfer_to_account("CR9560099",ps); add_log(st,f"💸 5%:${ps}","PROFIT")
                        except: pass
                add_log(st,"⏸ Tann 10sek..."); time.sleep(10)
            except Exception as e: add_log(st,f"Digits trade echwe: {e}","ERROR"); time.sleep(15)
        except Exception as e: add_log(st,f"Erè digits loop: {e}","ERROR"); time.sleep(15)
    add_log(st,"⏹ Digits Bot arrêté")


def binance_trading_loop(st,bot_id=None):
    if bot_id and st.get("bot_id")!=bot_id: return
    cfg=st["config"]; symbol=cfg.get("symbol","BTCUSDT"); strategy=cfg.get("strategy","confluence")
    lot=float(cfg.get("lot",11.0)); tf=int(cfg.get("tf_secs",900)); min_conf=float(cfg.get("min_conf",0.75))
    is_gold="XAU" in symbol.upper() or "GOLD" in symbol.upper() or "XAG" in symbol.upper()
    SL_PCT=0.015 if is_gold else 0.020; TP_PCT=0.030 if is_gold else 0.040
    if strategy=="binance_gold" or is_gold: fn=strat_binance_gold
    elif strategy=="binance_crypto": fn=strat_binance_crypto
    elif strategy=="confluence": fn=lambda c:strat_confluence_binance(c,symbol)
    else: fn=STRATEGIES.get(strategy,strat_confluence_elite)
    iv={60:"1m",300:"5m",900:"15m",3600:"1h",14400:"4h"}.get(tf,"15m")
    base_lot=max(11.0,lot); current_lot=base_lot
    add_log(st,f"🚀 Binance ELITE | {symbol} | SL:{SL_PCT*100:.1f}% TP:{TP_PCT*100:.1f}% | Base:${base_lot} | Conf:{min_conf:.0%}")
    while st["running"]:
        if bot_id and st.get("bot_id")!=bot_id: return
        if _check_limits(st,cfg): break
        try:
            api=st.get("binance_api")
            if not api: add_log(st,"Binance pa konekte — STOP","ERROR"); st["running"]=False; break
            try:
                b=api.balance
                if b and b>0: st["balance"]=b
            except: pass
            try:
                mn=api.get_min_notional(symbol)
                if current_lot<mn*1.05: current_lot=round(mn*1.1,2); add_log(st,f"ℹ Mise ajiste: ${current_lot:.2f}","WARN")
            except: pass
            if st["balance"]<current_lot:
                add_log(st,f"⚠ Balans ${st['balance']:.2f} < Mise ${current_lot:.2f}","WARN"); time.sleep(30); continue
            candles=api.get_candles(symbol,iv,200)
            if len(candles)<50: time.sleep(60); continue
            cl_vals=[x["close"] for x in candles]
            e200_v=ema(cl_vals,200) if len(cl_vals)>=200 else ema(cl_vals,100)
            adx_v,pdi_v,mdi_v=calc_adx_full(candles,14)
            add_log(st,f"📡 {len(candles)} bouji | {symbol} {iv} | ADX:{adx_v:.0f}")
            sig,conf=fn(candles)
            add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | ADX:{adx_v:.0f}")
            if sig=="NONE" or conf<min_conf: time.sleep(tf); continue
            if e200_v:
                if sig=="BUY" and cl_vals[-1]<e200_v[-1]*0.995:
                    add_log(st,f"⛔ REJTE BUY — Prix ANBA EMA200","WARN"); time.sleep(tf); continue
                if sig=="SELL" and cl_vals[-1]>e200_v[-1]*1.005:
                    add_log(st,f"⛔ REJTE SELL — Prix ANLÈ EMA200","WARN"); time.sleep(tf); continue
            entry=candles[-1]["close"]; bal_before=api.balance
            try:
                order=api.place_trade(symbol,sig,current_lot,SL_PCT,TP_PCT)
                add_log(st,f"✅ Limit+OCO | {sig} | ${current_lot:.2f} | SL:{SL_PCT*100:.1f}% TP:{TP_PCT*100:.1f}%","SUCCESS")
                st["trades"].insert(0,{"id":len(st["trades"])+1,"time":datetime.now().strftime("%H:%M:%S"),
                    "symbol":symbol,"side":sig,"entry":round(entry,4),"conf":f"{conf:.0%}",
                    "strategy":strategy,"tf":iv,"stake":round(current_lot,2),
                    "sl":f"{SL_PCT*100:.1f}%","tp":f"{TP_PCT*100:.1f}%",
                    "pnl":0.0,"status":"open","regime":"—"})
            except Exception as e: add_log(st,f"Trade echwe: {e}","ERROR"); time.sleep(30); continue
            time.sleep(15)
            try:
                bal_after=api.balance; st["balance"]=bal_after; pnl_chk=bal_after-bal_before
                if abs(pnl_chk)>0.01 and st["trades"]:
                    st["trades"][0]["pnl"]=round(pnl_chk,4); st["trades"][0]["status"]="won" if pnl_chk>0 else "open"
                    st["total_pnl"]+=pnl_chk
                    if pnl_chk>0:
                        ps=round(pnl_chk*PROFIT_PCT,4); st["profit_sent"]+=ps
                        if ps>=0.10:
                            try: api.send_profit(ps)
                            except: pass
            except: pass
            time.sleep(tf)
        except Exception as e: add_log(st,f"Erè binance: {e}","ERROR"); time.sleep(30)
    add_log(st,"⏹ Binance Bot arrêté")


def trading_loop(st,bot_id=None):
    if bot_id and st.get("bot_id")!=bot_id: return
    cfg=st["config"]; symbol=cfg.get("symbol","R_100"); strategy=cfg.get("strategy","confluence")
    lot=float(cfg.get("lot",0.5)); tf=int(cfg.get("tf_secs",900)); min_conf=float(cfg.get("min_conf",0.65))
    fn=STRATEGIES.get(strategy,strat_confluence_elite)
    wait_after=tf+90; base_lot=round(max(0.5,lot),2); current_lot=base_lot
    consec_losses=0; total_lost=0.0; MAX_LOSSES=3; PAUSE=45
    add_log(st,f"🚀 BonheurBot ELITE v6 | {symbol} | {strategy} | TF:{tf//60}min | Conf:{min_conf:.0%}")
    add_log(st,f"📌 SuperTrend+HA+Chandelier | ADX>12 | 3 strategies minimum")
    while st["running"]:
        if bot_id and st.get("bot_id")!=bot_id: add_log(st,"⏹ Bot anile","WARN"); return
        if _check_limits(st,cfg): break
        try:
            api=st.get("deriv_api")
            if not api: add_log(st,"Broker pa konekte — STOP","ERROR"); st["running"]=False; break
            _refresh_balance(api,st)
            candles=api.get_candles(symbol,200,tf)
            if len(candles)<20: add_log(st,f"Pa ase done ({len(candles)}) — tann...","WARN"); time.sleep(30); continue
            regime,_=market_regime(candles); adx_val,_,_=calc_adx_full(candles,14)
            st_sig,st_c=supertrend(candles); ha_sig,ha_c=heikin_ashi_trend(candles)
            add_log(st,f"📡 {len(candles)} bouji | {symbol} | {regime} | ADX:{adx_val:.0f} | ST:{st_sig}({st_c:.0%}) | HA:{ha_sig}({ha_c:.0%})")
            if consec_losses>=MAX_LOSSES:
                mache_bon=regime in("TRENDING_UP","TRENDING_DN","RANGING") and adx_val>=12
                if regime=="RANGING": mache_bon=(st_sig!="NONE") and (ha_sig!="NONE") and adx_val>=10
                if not mache_bon:
                    add_log(st,f"⏸ PÒZ APRE {consec_losses} PÈT | Mache:{regime}(ADX:{adx_val:.0f}) | Tann {PAUSE}sek...","WARN")
                    time.sleep(PAUSE); continue
                else:
                    add_log(st,f"✅ MACHE BON ANKÒ! {regime} ADX:{adx_val:.0f} | Reprann avèk ${current_lot:.2f}","SUCCESS")
            if regime=="VOLATILE":
                add_log(st,f"⏸ Mache VOLATILE — pa trade. Tann {min(tf,120)}sek...","WARN"); time.sleep(min(tf,120)); continue
            if strategy=="confluence":
                req=3 if consec_losses==0 else(4 if consec_losses<=2 else 5)
                sig,conf=strat_confluence_elite(candles,min_strats=req,min_per_conf=0.65)
                add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | Elite({req}strat)")
            elif strategy=="deriv_pro":
                sig,conf=strat_deriv_pro_elite(candles)
                add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | DerivPro Elite")
            elif strategy=="supertrend":
                sig,conf=supertrend(candles)
                add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | SuperTrend")
            elif strategy=="heikin_ashi":
                sig,conf=heikin_ashi_trend(candles)
                add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | Heikin Ashi")
            elif strategy=="chandelier":
                sig,conf=chandelier_exit(candles)
                add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | Chandelier")
            else:
                sig,conf=fn(candles)
                add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | {strategy}")
            if sig=="BUY"  and regime=="TRENDING_DN": add_log(st,f"⛔ REJTE BUY — Mache ap DESANN. {st_sig}/{ha_sig}","WARN"); time.sleep(tf); continue
            if sig=="SELL" and regime=="TRENDING_UP": add_log(st,f"⛔ REJTE SELL — Mache ap MONTE. {st_sig}/{ha_sig}","WARN"); time.sleep(tf); continue
            adaptive=min_conf+(0.02 if consec_losses==1 else(0.04 if consec_losses>=2 else 0))
            if sig=="NONE" or conf<adaptive:
                reason="Pa gen siyal" if sig=="NONE" else f"Conf {conf:.0%} < {adaptive:.0%}"
                add_log(st,f"⏭ {reason} — tann pwochen bouji..."); time.sleep(tf); continue
            pv_sig_dir="TRENDING_UP" if sig=="BUY" else "TRENDING_DN"
            in_pivot,piv_bonus=pivot_signal(candles,pv_sig_dir)
            pivot_info=" 🎯+PIVOT" if in_pivot else ""
            if st["balance"]<current_lot:
                add_log(st,f"⚠ Balans ${st['balance']:.2f} < Mise ${current_lot:.2f} — reset","WARN")
                current_lot=base_lot; consec_losses=0; total_lost=0.0
            entry=candles[-1]["close"]
            add_log(st,f"⚡ {sig} @ {entry:.5f} | Conf:{conf:.0%} | ADX:{adx_val:.0f} | ST:{st_sig} | HA:{ha_sig} | Mise:${current_lot:.2f}{pivot_info}")
            bal_before=st["balance"]; pnl=0.0; ok=False
            try:
                r=api.place_trade(symbol,sig,max(0.5,current_lot),duration_secs=tf)
                if r.get("contract_id"):
                    cid=r["contract_id"]; bal_open=float(r.get("balance_after",bal_before-current_lot))
                    st["balance"]=bal_open; ok=True
                    add_log(st,f"⏳ #{cid} | ${bal_open:.2f} | Ap tann {wait_after//60}min {wait_after%60}s...","SUCCESS")
                    time.sleep(wait_after)
                    bal_close=None
                    for attempt in range(5):
                        try:
                            nb=api.get_balance_sync()
                            if nb and nb>0 and abs(nb-bal_open)>0.01: bal_close=nb; break
                            time.sleep(max(30,tf//4))
                        except: time.sleep(30)
                    if bal_close:
                        st["balance"]=bal_close; pnl=bal_close-bal_before
                        if pnl>0.10: add_log(st,f"✅ GENYEN! +${pnl:.2f} | ${bal_close:.2f}","SUCCESS")
                        else: add_log(st,f"❌ PÈDI ${abs(pnl):.2f} | ${bal_close:.2f}","WARN")
                    else:
                        pnl=-(bal_before-bal_open); add_log(st,f"❌ PÈDI (timeout) ${abs(pnl):.2f}","WARN")
            except Exception as e: add_log(st,f"Trade echwe: {e}","ERROR")
            if ok:
                if pnl>0:
                    prev_l=consec_losses; current_lot=base_lot; consec_losses=0; total_lost=0.0
                    if prev_l>0: add_log(st,f"🏆 REKIPERE! (te gen {prev_l} pèt) ← Reset ${base_lot:.2f}","SUCCESS")
                    else: add_log(st,f"✅ Genyen +${pnl:.2f}","SUCCESS")
                else:
                    loss=abs(pnl) if abs(pnl)>0.01 else current_lot; total_lost+=loss; consec_losses+=1
                    if consec_losses<MAX_LOSSES:
                        next_lot=round((total_lost+base_lot)/0.95,2); current_lot=max(0.5,min(next_lot,100.0))
                        add_log(st,f"⚠ PÈT #{consec_losses}/{MAX_LOSSES-1} | Total:${total_lost:.2f} | Prochèn:${current_lot:.2f}","WARN")
                    else:
                        next_lot=round((total_lost+base_lot)/0.95,2); current_lot=max(0.5,min(next_lot,100.0))
                        add_log(st,f"🚨 3 PÈT AFILE! PÒZE OTOMATIK | Total:${total_lost:.2f} | Mise rekipere:${current_lot:.2f} | Ap tann mache...","WARN")
                trade={"id":len(st["trades"])+1,"time":datetime.now().strftime("%H:%M:%S"),
                       "symbol":symbol,"side":sig,"entry":round(entry,5),"conf":f"{conf:.0%}",
                       "strategy":strategy,"tf":f"{tf//60}min","stake":round(current_lot,2),
                       "pnl":round(pnl,2),"status":"won" if pnl>0 else "lost","regime":regime}
                st["trades"].insert(0,trade); st["total_pnl"]+=pnl
                if pnl>0:
                    ps=round(pnl*PROFIT_PCT,2); st["profit_sent"]+=ps
                    if ps>=0.5:
                        try: api.transfer_to_account("CR9560099",ps); add_log(st,f"💸 5%:${ps} → CR9560099","PROFIT")
                        except Exception as e: add_log(st,f"Transfer echwe: {e}","ERROR")
        except Exception as e: add_log(st,f"Erè: {e}","ERROR")
        time.sleep(tf)
    add_log(st,"⏹ BonheurBot ELITE v6 arrêté")


# ═══════════════════════════════════════════════════════════
# FLASK ROUTES
# ═══════════════════════════════════════════════════════════
@app.route("/api/connect",methods=["POST"])
def api_connect():
    st=get_state()
    try:
        d=freq.json; broker=d.get("broker")
        if broker=="deriv":
            raw_token=d.get("token","").strip(); app_id=d.get("app_id","1089").strip() or "1089"
            if not raw_token: return jsonify({"ok":False,"error":"Kole token ou anvan!"})
            tok_type="PAT" if is_pat_token(raw_token) else "Klasik"
            add_log(st,f"🔑 {tok_type} token → {'REST+OTP WS' if tok_type=='PAT' else f'WebSocket app_id={app_id}'}","INFO")
            ok,balance,loginid,used_app_id,note=connect_deriv_token(raw_token,app_id)
            if not ok:
                err=(f"❌ Token PAT echwe\n\n{note}\n\nSOLISYON:\n1. app.deriv.com → foto ou → API Token\n2. Create token → Read+Trade+Payments\n3. Kole (PA pat_xxx) — App ID: 1089") if tok_type=="PAT" else \
                    (f"❌ Koneksyon echwe\n\n{note}\n\n✓ Token valid?\n✓ Pèmisyon: Read+Trade+Payments?")
                return jsonify({"ok":False,"error":err})
            if is_pat_token(raw_token):
                api_main=DerivRESTClient(raw_token,app_id)
                try:
                    bal2=api_main.connect()
                    if bal2>0: balance=bal2
                except Exception as ce:
                    logger.warning(f"api_main.connect() re-fetch: {ce}")
                    api_main._bal=balance; api_main._loginid=loginid
                    if loginid and loginid!="PAT_USER": api_main._account_id=loginid; logger.info(f"Fallback account_id={loginid}")
                if not api_main._account_id:
                    api_main._account_id=loginid or ""
                    logger.warning(f"account_id toujou vide — sèvi ak loginid: {loginid}")
                add_log(st,f"PAT account_id: '{api_main._account_id}' | loginid: '{api_main._loginid}'","INFO")
                api_digits=DerivDigitsClient(raw_token,app_id)
                api_digits._bal=api_main._bal; api_digits._rest=api_main
            else:
                api_main=DerivClient(raw_token,used_app_id or app_id); api_main._bal=balance; api_main._loginid=loginid
                api_digits=DerivDigitsClient(raw_token,used_app_id or app_id); api_digits._bal=balance
            st["deriv_api"]=api_main; st["deriv_digits_api"]=api_digits
            st["broker"]="deriv"; st["balance"]=balance; st["connected"]=True
            note_msg=f"✓ {tok_type} | {loginid or 'OK'} | ${balance:.2f}"
            add_log(st,note_msg,"SUCCESS")
            return jsonify({"ok":True,"balance":balance,"broker":"deriv","note":note_msg,"token_type":tok_type})
        elif broker=="binance":
            api=BinanceClient(d["api_key"],d["api_secret"]); bal=api.connect()
            st["binance_api"]=api; st["broker"]="binance"; st["balance"]=bal; st["connected"]=True
            return jsonify({"ok":True,"balance":bal,"broker":"binance"})
        elif broker=="binance_us":
            api=BinanceUSClient(d["api_key"],d["api_secret"]); bal=api.connect()
            st["binance_api"]=api; st["broker"]="binance_us"; st["balance"]=bal; st["connected"]=True
            return jsonify({"ok":True,"balance":bal,"broker":"binance_us"})
        return jsonify({"ok":False,"error":"Broker enkoni"})
    except Exception as e:
        logger.error(f"Connect: {e}",exc_info=True)
        return jsonify({"ok":False,"error":str(e)})


@app.route("/api/start",methods=["POST"])
def api_start():
    st=get_state()
    if not st.get("access"): return jsonify({"ok":False,"error":"⚠ Ou bezwen yon kòd aksè valid!"})
    if not st["connected"]: return jsonify({"ok":False,"error":"Konekte broker anvan!"})
    if st["running"]: return jsonify({"ok":False,"error":"Bot déjà ap kouri"})
    d=freq.json or {}
    tf_map={"1m":60,"5m":300,"15m":900,"1h":3600,"4h":14400}
    st["config"]={"broker":st["broker"],"symbol":d.get("symbol","R_100"),"strategy":d.get("strategy","confluence"),
        "lot":d.get("lot",0.5),"tf_secs":tf_map.get(d.get("tf","15m"),900),
        "min_conf":d.get("min_conf",0.65),"profit_target":float(d.get("profit_target",0)),
        "loss_limit":float(d.get("loss_limit",0)),"mode":d.get("mode","forex"),
        "digit_type":d.get("digit_type","over_under")}
    import random,string
    bot_id=''.join(random.choices(string.ascii_uppercase+string.digits,k=8))
    st["running"]=True; st["bot_id"]=bot_id
    mode=d.get("mode","forex"); broker=st["broker"]
    if mode=="digits":
        threading.Thread(target=digits_trading_loop,args=(st,bot_id),daemon=True).start()
        add_log(st,"🎲 Digits mode démarre","INFO")
    elif broker in("binance","binance_us"):
        threading.Thread(target=binance_trading_loop,args=(st,bot_id),daemon=True).start()
        add_log(st,f"🪙 {'Binance US' if broker=='binance_us' else 'Binance'} mode démarre","INFO")
    else:
        threading.Thread(target=trading_loop,args=(st,bot_id),daemon=True).start()
    return jsonify({"ok":True})

@app.route("/api/stop",methods=["POST"])
def api_stop():
    st=get_state(); st["running"]=False; st["bot_id"]=None
    return jsonify({"ok":True})

@app.route("/api/status")
def api_status():
    st=get_state()
    return jsonify({"connected":st["connected"],"broker":st["broker"],"running":st["running"],
                    "balance":round(st["balance"],2),"pnl":round(st["total_pnl"],2),
                    "profit_sent":round(st["profit_sent"],4),"trades":st["trades"][:20],
                    "log":st["log"][:30],"config":st["config"]})

@app.route("/api/backtest",methods=["POST"])
def api_backtest():
    st=get_state()
    try:
        d=freq.json or {}; symbol=d.get("symbol","R_100"); strat=d.get("strategy","confluence")
        candles=[]; api=st.get("deriv_api") or st.get("binance_api")
        if api:
            try: candles=api.get_candles(symbol,500,3600)
            except: pass
        if len(candles)<100: return jsonify({"ok":False,"error":f"Pa ase done ({len(candles)}) — konekte broker anvan"})
        r=run_backtest(candles,strat,float(d.get("balance",10000)),float(d.get("lot",0.01)),
                       float(d.get("sl",20)),float(d.get("tp",40)))
        return jsonify({"ok":True,"result":r})
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

@app.route("/api/login",methods=["POST"])
def api_login():
    st=get_state(); d=freq.json or {}
    token=d.get("session_token","").strip(); code=d.get("code","").strip().upper()
    if token:
        ok,msg_text=validate_session(token)
        if ok:
            with _sess_lock: is_adm=_sessions.get(token,{}).get("is_admin",False)
            st["access"]=True; st["session_token"]=token; st["is_admin"]=is_adm
            return jsonify({"ok":True,"msg":msg_text,"session_token":token,"is_admin":is_adm})
        st["access"]=False; return jsonify({"ok":False,"msg":msg_text,"need_code":True})
    if not code: return jsonify({"ok":False,"msg":"Mete kòd aksè ou a","need_code":True})
    ok,msg_text=check_access(code)
    if ok:
        use_code(code); new_token,expire=create_session()
        is_adm=ACCESS_CODES.get(code,{}).get("is_adm",False) or ACCESS_CODES.get(code,{}).get("created_at") is None
        with _sess_lock: _sessions[new_token]["is_admin"]=is_adm; _save_sessions()
        st["access"]=True; st["session_token"]=new_token; st["is_admin"]=is_adm
        return jsonify({"ok":True,"msg":"✓ Aksè Admin! 30 jou rete" if is_adm else "✓ Aksè akòde! 30 jou rete",
                        "session_token":new_token,"expire":expire,"is_admin":is_adm})
    return jsonify({"ok":False,"msg":msg_text,"need_code":True})

def require_admin(d):
    token=d.get("admin_token","").strip()
    if not token: return False
    with _sess_lock: sess=_sessions.get(token)
    return sess.get("is_admin",False) if sess else False

@app.route("/api/admin/codes",methods=["POST"])
def admin_get_codes():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    now=time.time(); codes=[]
    for c,entry in ACCESS_CODES.items():
        if entry["created_at"] is None or entry.get("is_adm"): status="ADM"; remaining="∞"
        elif entry["used"]: status="ITILIZE"; remaining="0"
        else:
            age=now-entry["created_at"]
            if age>CODE_TTL_SECONDS: status="EKSPIRE"; remaining="0"
            else: status="AKTIF"; remaining=str(int((CODE_TTL_SECONDS-age)/86400))+" jou"
        codes.append({"code":c,"status":status,"remaining":remaining,"used":entry["used"],
                      "is_adm":entry.get("is_adm",False) or entry["created_at"] is None})
    today=date.today()
    active_sess=sum(1 for s in _sessions.values() if date.fromisoformat(s["expire"])>today)
    return jsonify({"ok":True,"codes":codes,"total_sessions":active_sess})

@app.route("/api/admin/add_code",methods=["POST"])
def admin_add_code():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    code=d.get("code","").strip().upper()
    if not code or len(code)<3: return jsonify({"ok":False,"error":"Kòd dwe gen 3+ karaktè"})
    if code in ACCESS_CODES: return jsonify({"ok":False,"error":"Kòd sa deja egziste"})
    is_adm=d.get("is_adm",False)
    ACCESS_CODES[code]={"created_at":None if is_adm else time.time(),"used":False,"is_adm":is_adm}
    typ="Admin" if is_adm else "Itilizatè (1 mwa)"
    return jsonify({"ok":True,"msg":f"✓ Kòd {code} kreye [{typ}]"})

@app.route("/api/admin/revoke_code",methods=["POST"])
def admin_revoke_code():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    code=d.get("code","").strip().upper()
    if not code or code not in ACCESS_CODES: return jsonify({"ok":False,"error":"Kòd pa jwenn"})
    if code=="BONHEURWIIN": return jsonify({"ok":False,"error":"Pa ka revoke kòd ADM prensipal"})
    del ACCESS_CODES[code]; return jsonify({"ok":True,"msg":f"✓ Kòd {code} revoke"})

@app.route("/api/admin/reset_code",methods=["POST"])
def admin_reset_code():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    code=d.get("code","").strip().upper()
    if code not in ACCESS_CODES: return jsonify({"ok":False,"error":"Kòd pa jwenn"})
    ACCESS_CODES[code]["used"]=False
    if not(ACCESS_CODES[code].get("is_adm") or ACCESS_CODES[code]["created_at"] is None):
        ACCESS_CODES[code]["created_at"]=time.time()
    return jsonify({"ok":True,"msg":f"✓ Kòd {code} reset"})

@app.route("/api/admin/users",methods=["POST"])
def admin_get_users():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    users=[]
    with _user_lock:
        for uid,ust in _user_states.items():
            users.append({"uid":uid[:8]+"...","connected":ust.get("connected",False),
                          "broker":ust.get("broker","—"),"running":ust.get("running",False),
                          "balance":round(ust.get("balance",0),2),"pnl":round(ust.get("total_pnl",0),2),
                          "trades":len(ust.get("trades",[])),"symbol":ust.get("config",{}).get("symbol","—"),
                          "strategy":ust.get("config",{}).get("strategy","—")})
    return jsonify({"ok":True,"users":users,"total":len(users)})

@app.route("/api/admin/stop_user",methods=["POST"])
def admin_stop_user():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    uid_prefix=d.get("uid","").replace("...",""); stopped=0
    with _user_lock:
        for uid,ust in _user_states.items():
            if uid.startswith(uid_prefix): ust["running"]=False; ust["bot_id"]=None; stopped+=1
    return jsonify({"ok":True,"msg":f"✓ {stopped} bot(s) kanpe"})

@app.route("/api/admin/sessions",methods=["POST"])
def admin_sessions():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    today=date.today(); sessions=[]
    with _sess_lock:
        for token,sess in _sessions.items():
            exp=date.fromisoformat(sess["expire"])
            sessions.append({"token":token[:8]+"...","expire":sess["expire"],
                             "days_left":(exp-today).days,"is_admin":sess.get("is_admin",False),
                             "active":(exp-today).days>0})
    return jsonify({"ok":True,"sessions":sessions,"total":len(sessions)})

@app.route("/api/admin/clean_sessions",methods=["POST"])
def admin_clean_sessions():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    today=date.today(); count=0
    with _sess_lock:
        expired=[t for t,s in _sessions.items() if date.fromisoformat(s["expire"])<=today]
        for t in expired: del _sessions[t]; count+=1
        if count: _save_sessions()
    return jsonify({"ok":True,"msg":f"✓ {count} sesyon efase"})

@app.route("/api/admin/clear_user",methods=["POST"])
def admin_clear_user():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    uid_prefix=d.get("uid","").replace("...",""); cleared=0
    with _user_lock:
        for uid,ust in _user_states.items():
            if uid.startswith(uid_prefix):
                ust["trades"]=[]; ust["total_pnl"]=0.0; ust["profit_sent"]=0.0; ust["log"]=[]; cleared+=1
    return jsonify({"ok":True,"msg":f"✓ {cleared} itilizatè efase"})

@app.route("/api/admin/clear_trades",methods=["POST"])
def admin_clear_trades():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    uid_prefix=d.get("uid","").replace("...",""); cleared=0
    with _user_lock:
        for uid,ust in _user_states.items():
            if uid.startswith(uid_prefix): ust["trades"]=[]; cleared+=1
    return jsonify({"ok":True,"msg":f"✓ {cleared} itilizatè: trades efase (log + pnl konsève)"})

@app.route("/")
def index(): return render_template_string(HTML)
