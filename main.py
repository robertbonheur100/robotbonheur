"""
╔══════════════════════════════════════════════════════════════╗
║                  BONHEURBOT PRO v6 ELITE                     ║
║         Multi-User Trading Bot — Deriv + Binance            ║
║   Trend + Ranging | Smart Entry | 3-Loss Pause              ║
║   PAT Token REST Auth | OAuth2+PKCE+OTP | Token Klasik      ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, json, time, threading, logging, math, uuid, secrets, hashlib, base64, re
import urllib.request, urllib.parse
from datetime import datetime, timedelta, date
from flask import Flask, request, jsonify, render_template_string, session, redirect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROFIT_WALLET = "0x2ba88a4d6cabaded5d06c75ef3b3efec386acaef"
PROFIT_PCT    = 0.05

# ═══════════════════════════════════════════════════════════
# DERIV CONFIG
# ═══════════════════════════════════════════════════════════
DERIV_CLIENT_ID    = "33h9RL9bbCjURr4MO3PQ0"
DERIV_REDIRECT_URI = "https://robotbonheur.onrender.com/callback"
DERIV_AUTH_URL     = "https://oauth.deriv.com/oauth2/authorize"
DERIV_TOKEN_URL    = "https://oauth.deriv.com/oauth2/token"

# ── NEW Deriv REST API v2 endpoints (PAT compatible) ──
DERIV_REST_BASE      = "https://api.deriv.com/v1"
DERIV_REST_ACCOUNTS  = "https://api.deriv.com/v1/accounts"
DERIV_REST_BALANCE   = "https://api.deriv.com/v1/accounts/{account_id}/balance"
DERIV_REST_ORDERS    = "https://api.deriv.com/v1/orders"

# Legacy WS app IDs to try
DERIV_WS_APP_IDS = ["36544", "1089", "16929", DERIV_CLIENT_ID]

ACCESS_CODES = {
    "BONHEURWIIN": {"created_at": None, "used": False, "is_adm": True},
    "HJKy8kFD":    {"created_at": time.time(), "used": False, "is_adm": False},
    "GHt3hjI6":    {"created_at": time.time(), "used": False, "is_adm": False},
}
CODE_TTL_SECONDS = 2592000

def check_access(code):
    code = code.strip().upper()
    if code not in ACCESS_CODES:
        return False, "Kòd aksè pa valid — kontakte admin"
    entry = ACCESS_CODES[code]
    if entry["created_at"] is None or entry.get("is_adm"):
        return True, "✓ Aksè admin akòde"
    age = time.time() - entry["created_at"]
    if age > CODE_TTL_SECONDS:
        days_ago = int((age - CODE_TTL_SECONDS) / 86400)
        return False, f"Kòd ekspire depi {days_ago} jou — kontakte admin"
    if entry["used"]:
        return False, "Kòd sa deja itilize — kontakte admin"
    days_left = int((CODE_TTL_SECONDS - age) / 86400)
    return True, f"✓ Aksè akòde — {days_left} jou rete"

def use_code(code):
    code = code.strip().upper()
    if code in ACCESS_CODES:
        entry = ACCESS_CODES[code]
        if entry["created_at"] is not None and not entry.get("is_adm"):
            ACCESS_CODES[code]["used"] = True

SESSIONS_FILE = "sessions.json"
_sessions = {}
_sess_lock = threading.Lock()

def _load_sessions():
    global _sessions
    try:
        if os.path.exists(SESSIONS_FILE):
            with open(SESSIONS_FILE, "r") as f:
                _sessions = json.load(f)
    except:
        _sessions = {}

def _save_sessions():
    try:
        with open(SESSIONS_FILE, "w") as f:
            json.dump(_sessions, f, indent=2)
    except Exception as e:
        logger.error(f"Sessions save: {e}")

_load_sessions()

def create_session():
    token = secrets.token_hex(32)
    expire = (date.today() + timedelta(days=30)).isoformat()
    with _sess_lock:
        _sessions[token] = {"expire": expire, "created": time.time()}
        _save_sessions()
    return token, expire

def validate_session(token):
    if not token:
        return False, "Pa gen sesyon"
    with _sess_lock:
        sess = _sessions.get(token)
    if not sess:
        return False, "Sesyon pa valid — antre kòd aksè ou"
    exp = date.fromisoformat(sess["expire"])
    if date.today() > exp:
        with _sess_lock:
            _sessions.pop(token, None)
            _save_sessions()
        return False, "Abònman ou ekspire (30 jou) — kontakte admin"
    days_left = (exp - date.today()).days
    return True, f"Sesyon aktif — {days_left} jou rete"

SECRET_KEY_FILE = "secret.key"
if os.path.exists(SECRET_KEY_FILE):
    with open(SECRET_KEY_FILE, "r") as f:
        _secret = f.read().strip()
else:
    _secret = secrets.token_hex(32)
    with open(SECRET_KEY_FILE, "w") as f:
        f.write(_secret)

app = Flask(__name__)
app.secret_key = _secret

_user_states = {}
_user_lock = threading.Lock()
_pkce_store = {}
_pkce_lock  = threading.Lock()

def get_state():
    if "uid" not in session:
        session["uid"] = str(uuid.uuid4())
    uid = session["uid"]
    with _user_lock:
        if uid not in _user_states:
            _user_states[uid] = {
                "uid": uid, "access": False, "session_token": None,
                "bot_id": None, "broker": None, "connected": False, "running": False,
                "balance": 0.0, "total_pnl": 0.0, "profit_sent": 0.0,
                "trades": [], "log": [], "config": {},
                "deriv_api": None, "binance_api": None, "deriv_digits_api": None,
                "_pat_token": None, "_pat_account_id": None, "_pat_currency": "USD",
            }
    return _user_states[uid]

# ═══════════════════════════════════════════════════════════
# ── NEW: DERIV PAT REST AUTH HELPERS ──
# ═══════════════════════════════════════════════════════════

def _deriv_rest_request(url, method="GET", data=None, pat_token=None, timeout=20):
    """
    Generic Deriv REST API request using PAT Bearer auth.
    Returns (ok, response_dict_or_error_str)
    """
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if pat_token:
        headers["Authorization"] = f"Bearer {pat_token}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read())
        except:
            err_body = {"raw": str(e)}
        return False, err_body
    except Exception as e:
        return False, str(e)

def pat_verify_and_get_account(pat_token):
    """
    Verify a PAT token via Deriv REST API v1.
    Returns (ok, account_info_dict_or_error)
    account_info = {"account_id", "balance", "currency", "status", "email"}
    """
    # Try new REST v1 accounts endpoint
    ok, resp = _deriv_rest_request(DERIV_REST_ACCOUNTS, "GET", pat_token=pat_token)
    if ok:
        # Response may be list or dict with accounts key
        accounts = []
        if isinstance(resp, list):
            accounts = resp
        elif isinstance(resp, dict):
            accounts = resp.get("accounts", resp.get("data", []))
            if not accounts and resp.get("account_id"):
                accounts = [resp]
        if accounts:
            acct = accounts[0]
            return True, {
                "account_id": acct.get("account_id") or acct.get("id", ""),
                "balance":    float(acct.get("balance", 0)),
                "currency":   acct.get("currency", "USD"),
                "status":     acct.get("status", "active"),
                "email":      acct.get("email", ""),
                "all_accounts": accounts,
            }
        # Empty accounts list — token valid but no accounts?
        return True, {"account_id": "", "balance": 0.0, "currency": "USD", "status": "active", "all_accounts": []}

    # REST failed — fall back to WebSocket authorize
    logger.info(f"REST accounts failed ({resp}), trying WS authorize fallback...")
    return _pat_verify_via_websocket(pat_token)

def _pat_verify_via_websocket(pat_token):
    """
    Fallback: verify PAT via WebSocket authorize message.
    Tries multiple app_ids.
    """
    try:
        import websocket as ws_lib
    except ImportError:
        return False, "websocket-client library not installed"

    for app_id in DERIV_WS_APP_IDS:
        done  = threading.Event()
        result = [None]

        def on_open(ws):
            ws.send(json.dumps({"authorize": pat_token}))

        def on_msg(ws, msg):
            d = json.loads(msg)
            if d.get("msg_type") == "authorize":
                if "error" in d:
                    result[0] = {"ok": False, "error": d["error"].get("message", "Token invalib")}
                else:
                    auth = d["authorize"]
                    result[0] = {
                        "ok": True,
                        "account_id": auth.get("loginid", auth.get("account_id", "")),
                        "balance":    float(auth.get("balance", 0)),
                        "currency":   auth.get("currency", "USD"),
                        "status":     "active",
                        "email":      auth.get("email", ""),
                        "all_accounts": auth.get("account_list", []),
                    }
                done.set()

        def on_err(ws, e):
            result[0] = {"ok": False, "error": str(e)}
            done.set()

        url = f"wss://ws.derivws.com/websockets/v3?app_id={app_id}"
        try:
            ws = ws_lib.WebSocketApp(url, on_open=on_open, on_message=on_msg, on_error=on_err)
            t  = threading.Thread(target=ws.run_forever, daemon=True)
            t.start()
            done.wait(timeout=15)
            try: ws.close()
            except: pass
            r = result[0]
            if r and r.get("ok"):
                logger.info(f"PAT WS verify ok via app_id={app_id}")
                return True, r
            elif r:
                logger.info(f"PAT WS app_id={app_id} error: {r.get('error')}")
        except Exception as e:
            logger.info(f"PAT WS app_id={app_id} exception: {e}")

    return False, "PAT token invalib oswa rejte pa tout app_id Deriv"

def pat_get_balance(pat_token, account_id):
    """Fetch balance for a specific account via REST."""
    if account_id:
        url = DERIV_REST_BALANCE.format(account_id=account_id)
        ok, resp = _deriv_rest_request(url, "GET", pat_token=pat_token)
        if ok:
            bal = resp.get("balance", resp.get("amount", 0))
            return True, float(bal)
    # Fallback: re-verify which also returns balance
    ok, info = pat_verify_and_get_account(pat_token)
    if ok:
        return True, info.get("balance", 0.0)
    return False, 0.0

# ═══════════════════════════════════════════════════════════
# OAUTH2 + PKCE HELPERS
# ═══════════════════════════════════════════════════════════
def _pkce_pair():
    verifier  = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode()
    digest    = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
    return verifier, challenge

def build_oauth_url(uid):
    verifier, challenge = _pkce_pair()
    state = secrets.token_hex(16)
    with _pkce_lock:
        _pkce_store[state] = {"verifier": verifier, "uid": uid, "ts": time.time()}
    params = (
        f"?response_type=code"
        f"&client_id={DERIV_CLIENT_ID}"
        f"&redirect_uri={DERIV_REDIRECT_URI}"
        f"&scope=read:trading write:trading read:account"
        f"&state={state}"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
    )
    return DERIV_AUTH_URL + params, state

def exchange_oauth_code(code, state):
    with _pkce_lock:
        entry = _pkce_store.pop(state, None)
    if not entry:
        return False, "State OAuth invalib ou ekspire — resesye koneksyon"
    verifier = entry["verifier"]
    uid      = entry.get("uid", "")
    payload  = urllib.parse.urlencode({
        "grant_type":    "authorization_code",
        "client_id":     DERIV_CLIENT_ID,
        "code":          code,
        "code_verifier": verifier,
        "redirect_uri":  DERIV_REDIRECT_URI,
    }).encode()
    req = urllib.request.Request(
        DERIV_TOKEN_URL, data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        access_token = data.get("access_token")
        if not access_token:
            return False, f"Deriv pa retounen access_token: {data}"
        return True, access_token, uid
    except Exception as e:
        body = getattr(e, 'read', lambda: b'')()
        return False, f"Echange kòd echwe: {e} | {body}"

def get_deriv_accounts_oauth(access_token):
    ok, resp = _deriv_rest_request(
        "https://api.deriv.com/trading/v1/options/accounts", "GET",
        pat_token=access_token
    )
    if not ok:
        return False, str(resp)
    accounts = resp.get("accounts", resp.get("data", []))
    if not accounts and resp.get("account_id"):
        accounts = [resp]
    return True, accounts

def get_deriv_otp(access_token, account_id):
    url = f"https://api.deriv.com/trading/v1/options/accounts/{account_id}/otp"
    ok, resp = _deriv_rest_request(url, "POST", data={}, pat_token=access_token)
    if not ok:
        return False, f"OTP echwe: {resp}", 0.0
    ws_url  = resp.get("ws_url") or resp.get("websocket_url") or resp.get("url")
    otp     = resp.get("otp") or resp.get("token")
    balance = float(resp.get("balance", 0))
    if ws_url:
        return True, ws_url, balance
    if otp:
        ws_url = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_CLIENT_ID}&otp={otp}"
        return True, ws_url, balance
    return False, f"Pa jwenn ws_url: {resp}", 0.0

# ═══════════════════════════════════════════════════════════
# DERIV WebSocket CLIENT — PAT + OAuth2 + Classic
# ═══════════════════════════════════════════════════════════
class DerivClient:
    """
    Unified Deriv client.
    - pat_token: PAT token (pat_...) — uses REST for auth, WS for candles/trading
    - token: classic WS authorize token
    - ws_url: OAuth2 OTP WebSocket URL
    """
    def __init__(self, token, app_id="1089", ws_url=None, pat_token=None):
        self.token      = token      # WS authorize token (classic or PAT as fallback)
        self.app_id     = app_id
        self.ws_url     = ws_url
        self.pat_token  = pat_token  # original PAT for REST calls
        self._bal       = 0.0
        self._account_id = ""
        self._currency   = "USD"

    def _get_ws_url(self):
        if self.ws_url:
            return self.ws_url
        return f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"

    def connect(self):
        """
        Connect: PAT tries REST first, then WS fallback.
        Classic token goes straight to WS.
        """
        if self.pat_token:
            ok, info = pat_verify_and_get_account(self.pat_token)
            if ok:
                self._bal        = info.get("balance", 0.0)
                self._account_id = info.get("account_id", "")
                self._currency   = info.get("currency", "USD")
                # Also set token for WS trading (PAT works as authorize token in WS)
                if not self.token or self.token == self.pat_token:
                    self.token = self.pat_token
                logger.info(f"PAT REST auth ok | balance={self._bal} {self._currency}")
                return self._bal
            else:
                raise Exception(f"PAT invalib: {info}")

        # Classic or OAuth2 token — use WS authorize
        import websocket
        done = threading.Event(); err = [None]
        def on_open(ws): ws.send(json.dumps({"authorize": self.token}))
        def on_msg(ws, msg):
            d = json.loads(msg)
            if d.get("msg_type") == "authorize":
                if "error" in d: err[0] = d["error"]["message"]
                else:
                    a = d["authorize"]
                    self._bal        = float(a.get("balance", 0))
                    self._account_id = a.get("loginid", "")
                    self._currency   = a.get("currency", "USD")
                done.set()
        def on_err(ws, e): err[0] = str(e); done.set()

        app_ids_to_try = DERIV_WS_APP_IDS if not self.ws_url else [self.app_id]
        for aid in app_ids_to_try:
            done.clear(); err[0] = None
            url = self.ws_url or f"wss://ws.derivws.com/websockets/v3?app_id={aid}"
            ws  = websocket.WebSocketApp(url, on_open=on_open, on_message=on_msg, on_error=on_err)
            threading.Thread(target=ws.run_forever, daemon=True).start()
            done.wait(timeout=15)
            try: ws.close()
            except: pass
            if not err[0]:
                self.app_id = aid
                return self._bal
        if err[0]: raise Exception(f"Deriv: {err[0]}")
        return self._bal

    def get_balance_sync(self):
        """Refresh balance — PAT uses REST, others use WS."""
        if self.pat_token and self._account_id:
            ok, bal = pat_get_balance(self.pat_token, self._account_id)
            if ok:
                self._bal = bal
                return bal
        # WS balance
        import websocket as wsl
        res = [None]; done = threading.Event()
        def on_msg(ws, msg):
            d = json.loads(msg)
            if d.get("msg_type") == "authorize" and "error" not in d:
                ws.send(json.dumps({"balance": 1, "account": "current"}))
            elif d.get("msg_type") == "balance":
                b = d.get("balance", {}).get("balance")
                if b is not None: res[0] = float(b); done.set()
            elif "error" in d: done.set()
        def on_open(ws): ws.send(json.dumps({"authorize": self.token}))
        url = self._get_ws_url()
        w = wsl.WebSocketApp(url, on_message=on_msg, on_open=on_open)
        threading.Thread(target=w.run_forever, daemon=True).start()
        done.wait(timeout=15)
        if res[0]: self._bal = res[0]
        return res[0] or self._bal

    def get_candles(self, symbol="R_100", count=200, gran=60):
        import websocket as wsl
        res = [None]; done = threading.Event()
        def on_msg(ws, msg):
            d = json.loads(msg)
            if d.get("msg_type") == "authorize":
                ws.send(json.dumps({"ticks_history": symbol, "count": count,
                    "end": "latest", "granularity": gran, "style": "candles", "adjust_start_time": 1}))
            elif "candles" in d: res[0] = d["candles"]; done.set()
            elif "error" in d: done.set()
        def on_open(ws): ws.send(json.dumps({"authorize": self.token}))
        url = self._get_ws_url()
        w = wsl.WebSocketApp(url, on_message=on_msg, on_open=on_open)
        threading.Thread(target=w.run_forever, daemon=True).start()
        done.wait(timeout=25)
        if not res[0]: return []
        return [{"open": float(c["open"]), "high": float(c["high"]),
                 "low": float(c["low"]), "close": float(c["close"]),
                 "volume": 1000, "time": c["epoch"]} for c in res[0]]

    def place_trade(self, symbol, direction, amount=1.0, duration_secs=60):
        import websocket as wsl
        res = [None]; err = [None]; done = threading.Event()
        ct = "CALL" if direction == "BUY" else "PUT"
        if duration_secs <= 60: dur_val, dur_unit = 1, "m"
        elif duration_secs <= 300: dur_val, dur_unit = 5, "m"
        elif duration_secs <= 900: dur_val, dur_unit = 15, "m"
        elif duration_secs <= 3600: dur_val, dur_unit = 1, "h"
        else: dur_val, dur_unit = 4, "h"
        def on_msg(ws, msg):
            d = json.loads(msg); mt = d.get("msg_type", "")
            if mt == "authorize" and "error" not in d:
                ws.send(json.dumps({"proposal": 1, "amount": max(0.5, float(amount)),
                    "basis": "stake", "contract_type": ct, "currency": "USD",
                    "symbol": symbol, "duration": dur_val, "duration_unit": dur_unit}))
            elif mt == "proposal":
                if "error" in d: err[0] = d["error"]["message"]; done.set(); return
                ws.send(json.dumps({"buy": d["proposal"]["id"], "price": d["proposal"]["ask_price"]}))
            elif mt == "buy":
                if "error" in d: err[0] = d["error"]["message"]; done.set(); return
                res[0] = d.get("buy", {}); done.set()
        def on_open(ws): ws.send(json.dumps({"authorize": self.token}))
        url = self._get_ws_url()
        w = wsl.WebSocketApp(url, on_message=on_msg, on_open=on_open)
        threading.Thread(target=w.run_forever, daemon=True).start()
        done.wait(timeout=30)
        if err[0]: raise Exception(err[0])
        return res[0] or {}

    def transfer_to_account(self, account_id, amount):
        import websocket as wsl
        res = [None]; err = [None]; done = threading.Event()
        def on_msg(ws, msg):
            d = json.loads(msg); mt = d.get("msg_type", "")
            if mt == "authorize" and "error" not in d:
                ws.send(json.dumps({"transfer_between_accounts": 1, "account_to": account_id,
                    "amount": round(float(amount), 2), "currency": "USD"}))
            elif mt == "transfer_between_accounts":
                if "error" in d: err[0] = d["error"]["message"]; done.set(); return
                res[0] = d; done.set()
        def on_open(ws): ws.send(json.dumps({"authorize": self.token}))
        url = self._get_ws_url()
        w = wsl.WebSocketApp(url, on_message=on_msg, on_open=on_open)
        threading.Thread(target=w.run_forever, daemon=True).start()
        done.wait(timeout=20)
        if err[0]: raise Exception(err[0])
        return res[0]

    @property
    def balance(self): return self._bal
    @property
    def account_id(self): return self._account_id
    @property
    def currency(self): return self._currency

# ═══════════════════════════════════════════════════════════
# DERIV DIGITS CLIENT — PAT compatible
# ═══════════════════════════════════════════════════════════
class DerivDigitsClient:
    def __init__(self, token, app_id="1089", ws_url=None, pat_token=None):
        self.token     = token
        self.app_id    = app_id
        self.ws_url    = ws_url
        self.pat_token = pat_token
        self._bal      = 0.0

    def _get_ws_url(self):
        if self.ws_url: return self.ws_url
        return f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"

    def connect(self):
        if self.pat_token:
            ok, info = pat_verify_and_get_account(self.pat_token)
            if ok:
                self._bal = info.get("balance", 0.0)
                if not self.token or self.token == self.pat_token:
                    self.token = self.pat_token
                return self._bal
            raise Exception(f"Digits PAT invalib: {info}")
        import websocket
        done = threading.Event(); err = [None]
        def on_open(ws): ws.send(json.dumps({"authorize": self.token}))
        def on_msg(ws, msg):
            d = json.loads(msg)
            if d.get("msg_type") == "authorize":
                if "error" in d: err[0] = d["error"]["message"]
                else: self._bal = float(d["authorize"].get("balance", 0))
                done.set()
        def on_err(ws, e): err[0] = str(e); done.set()
        app_ids_to_try = DERIV_WS_APP_IDS if not self.ws_url else [self.app_id]
        for aid in app_ids_to_try:
            done.clear(); err[0] = None
            url = self.ws_url or f"wss://ws.derivws.com/websockets/v3?app_id={aid}"
            ws  = websocket.WebSocketApp(url, on_open=on_open, on_message=on_msg, on_error=on_err)
            threading.Thread(target=ws.run_forever, daemon=True).start()
            done.wait(timeout=15)
            try: ws.close()
            except: pass
            if not err[0]: self.app_id = aid; return self._bal
        if err[0]: raise Exception(f"Deriv Digits: {err[0]}")
        return self._bal

    def get_ticks(self, symbol="R_10", count=100):
        import websocket as wsl
        res = [None]; done = threading.Event()
        def on_msg(ws, msg):
            d = json.loads(msg)
            if d.get("msg_type") == "authorize":
                ws.send(json.dumps({"ticks_history": symbol, "count": count, "end": "latest", "style": "ticks"}))
            elif d.get("msg_type") == "history": res[0] = d.get("history", {}); done.set()
            elif "error" in d: done.set()
        def on_open(ws): ws.send(json.dumps({"authorize": self.token}))
        url = self._get_ws_url()
        w = wsl.WebSocketApp(url, on_message=on_msg, on_open=on_open)
        threading.Thread(target=w.run_forever, daemon=True).start()
        done.wait(timeout=25)
        if not res[0]: return []
        prices = res[0].get("prices", []); times = res[0].get("times", [])
        return [{"price": float(p), "time": t} for p, t in zip(prices, times)]

    def place_digits_trade(self, symbol, contract_type, amount=0.35, barrier=None):
        import websocket as wsl
        res = [None]; err = [None]; done = threading.Event()
        proposal = {"proposal": 1, "amount": max(0.35, float(amount)), "basis": "stake",
                    "contract_type": contract_type, "currency": "USD",
                    "symbol": symbol, "duration": 5, "duration_unit": "t"}
        if barrier is not None: proposal["barrier"] = str(barrier)
        def on_msg(ws, msg):
            d = json.loads(msg); mt = d.get("msg_type", "")
            if mt == "authorize" and "error" not in d: ws.send(json.dumps(proposal))
            elif mt == "proposal":
                if "error" in d: err[0] = d["error"]["message"]; done.set(); return
                ws.send(json.dumps({"buy": d["proposal"]["id"], "price": d["proposal"]["ask_price"]}))
            elif mt == "buy":
                if "error" in d: err[0] = d["error"]["message"]; done.set(); return
                res[0] = d.get("buy", {}); done.set()
        def on_open(ws): ws.send(json.dumps({"authorize": self.token}))
        url = self._get_ws_url()
        w = wsl.WebSocketApp(url, on_message=on_msg, on_open=on_open)
        threading.Thread(target=w.run_forever, daemon=True).start()
        done.wait(timeout=30)
        if err[0]: raise Exception(err[0])
        return res[0] or {}

    def wait_contract_result(self, contract_id, timeout=30):
        import websocket as wsl
        res = [None]; done = threading.Event()
        def on_msg(ws, msg):
            d = json.loads(msg); mt = d.get("msg_type", "")
            if mt == "authorize" and "error" not in d:
                ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}))
            elif mt == "proposal_open_contract":
                poc = d.get("proposal_open_contract", {}); status = poc.get("status", "")
                if status in ("won", "lost", "sold"): res[0] = poc; done.set()
        def on_open(ws): ws.send(json.dumps({"authorize": self.token}))
        url = self._get_ws_url()
        w = wsl.WebSocketApp(url, on_message=on_msg, on_open=on_open)
        threading.Thread(target=w.run_forever, daemon=True).start()
        done.wait(timeout=timeout)
        return res[0]

    def get_balance_sync(self):
        if self.pat_token:
            ok, bal = pat_get_balance(self.pat_token, "")
            if ok: self._bal = bal; return bal
        import websocket as wsl
        res = [None]; done = threading.Event()
        def on_msg(ws, msg):
            d = json.loads(msg)
            if d.get("msg_type") == "authorize" and "error" not in d:
                ws.send(json.dumps({"balance": 1, "account": "current"}))
            elif d.get("msg_type") == "balance":
                b = d.get("balance", {}).get("balance")
                if b is not None: res[0] = float(b); done.set()
            elif "error" in d: done.set()
        def on_open(ws): ws.send(json.dumps({"authorize": self.token}))
        url = self._get_ws_url()
        w = wsl.WebSocketApp(url, on_message=on_msg, on_open=on_open)
        threading.Thread(target=w.run_forever, daemon=True).start()
        done.wait(timeout=15)
        if res[0]: self._bal = res[0]
        return res[0] or self._bal

    def transfer_to_account(self, account_id, amount):
        import websocket as wsl
        res = [None]; err = [None]; done = threading.Event()
        def on_msg(ws, msg):
            d = json.loads(msg); mt = d.get("msg_type", "")
            if mt == "authorize" and "error" not in d:
                ws.send(json.dumps({"transfer_between_accounts": 1, "account_to": account_id,
                    "amount": round(float(amount), 2), "currency": "USD"}))
            elif mt == "transfer_between_accounts":
                if "error" in d: err[0] = d["error"]["message"]; done.set(); return
                res[0] = d; done.set()
        def on_open(ws): ws.send(json.dumps({"authorize": self.token}))
        url = self._get_ws_url()
        w = wsl.WebSocketApp(url, on_message=on_msg, on_open=on_open)
        threading.Thread(target=w.run_forever, daemon=True).start()
        done.wait(timeout=20)
        if err[0]: raise Exception(err[0])
        return res[0]

    @property
    def balance(self): return self._bal

# ═══════════════════════════════════════════════════════════
# BINANCE CLIENTS (unchanged)
# ═══════════════════════════════════════════════════════════
class BinanceClient:
    def __init__(self, key, secret):
        from binance.client import Client; self.c = Client(key, secret)
    def connect(self):
        for b in self.c.get_account()["balances"]:
            if b["asset"] == "USDT": return float(b["free"])
        return 0.0
    @property
    def balance(self):
        try:
            for b in self.c.get_account()["balances"]:
                if b["asset"] == "USDT": return float(b["free"])
        except: pass
        return 0.0
    def get_candles(self, symbol="BTCUSDT", interval="15m", limit=200):
        k = self.c.get_klines(symbol=symbol, interval=interval, limit=limit)
        return [{"open": float(x[1]), "high": float(x[2]), "low": float(x[3]),
                 "close": float(x[4]), "volume": float(x[5]), "time": x[0]} for x in k]
    def get_symbol_info_cached(self, symbol):
        try: return self.c.get_symbol_info(symbol)
        except: return None
    def get_min_notional(self, symbol):
        info = self.get_symbol_info_cached(symbol)
        if not info: return 10.0
        for f in info.get("filters", []):
            if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"): return float(f.get("minNotional", "10"))
        return 10.0
    def get_qty_precision(self, symbol):
        info = self.get_symbol_info_cached(symbol)
        if not info: return 3
        for f in info.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                step = float(f["stepSize"])
                if step >= 1: return 0
                elif step >= 0.1: return 1
                elif step >= 0.01: return 2
                elif step >= 0.001: return 3
                else: return 4
        return 3
    def get_min_qty(self, symbol):
        info = self.get_symbol_info_cached(symbol)
        if not info: return 0.001
        for f in info.get("filters", []):
            if f["filterType"] == "LOT_SIZE": return float(f["minQty"])
        return 0.001
    def get_price_precision(self, symbol):
        info = self.get_symbol_info_cached(symbol)
        if not info: return 2
        for f in info.get("filters", []):
            if f["filterType"] == "PRICE_FILTER":
                tick = float(f["tickSize"])
                if tick >= 1: return 0
                elif tick >= 0.1: return 1
                elif tick >= 0.01: return 2
                elif tick >= 0.001: return 3
                else: return 4
        return 2
    def place_trade(self, symbol, direction, amount_usdt=10.0, sl_pct=0.018, tp_pct=0.035):
        from binance.enums import SIDE_BUY, SIDE_SELL, TIME_IN_FORCE_GTC
        ticker = self.c.get_symbol_ticker(symbol=symbol); price = float(ticker["price"])
        pp = self.get_price_precision(symbol); qp = self.get_qty_precision(symbol)
        min_qty = self.get_min_qty(symbol); min_not = self.get_min_notional(symbol)
        qty = round(amount_usdt / price, qp); qty = max(qty, min_qty)
        if qty * price < min_not: qty = round(min_not / price * 1.01, qp); qty = max(qty, min_qty)
        side = SIDE_BUY if direction == "BUY" else SIDE_SELL
        if direction == "BUY":
            limit_price = round(price * 1.0005, pp); sl_price = round(price * (1 - sl_pct), pp); tp_price = round(price * (1 + tp_pct), pp)
        else:
            limit_price = round(price * 0.9995, pp); sl_price = round(price * (1 + sl_pct), pp); tp_price = round(price * (1 - tp_pct), pp)
        entry_order = self.c.order_limit(symbol=symbol, side=side, quantity=qty, price=str(limit_price), timeInForce=TIME_IN_FORCE_GTC)
        oid = entry_order.get("orderId"); filled = False
        for _ in range(18):
            time.sleep(5)
            try:
                status = self.c.get_order(symbol=symbol, orderId=oid)
                if status["status"] == "FILLED": filled = True; break
                elif status["status"] in ("CANCELED", "EXPIRED", "REJECTED"): break
            except: pass
        if not filled:
            try: self.c.cancel_order(symbol=symbol, orderId=oid)
            except: pass
            return self.c.order_market(symbol=symbol, side=side, quantity=qty)
        try:
            if direction == "BUY":
                self.c.order_oco_sell(symbol=symbol, quantity=qty, price=str(tp_price), stopPrice=str(sl_price),
                    stopLimitPrice=str(round(sl_price * 0.998, pp)), stopLimitTimeInForce=TIME_IN_FORCE_GTC)
            else:
                self.c.order_oco_buy(symbol=symbol, quantity=qty, price=str(tp_price), stopPrice=str(sl_price),
                    stopLimitPrice=str(round(sl_price * 1.002, pp)), stopLimitTimeInForce=TIME_IN_FORCE_GTC)
        except Exception as e: logger.warning(f"OCO echwe ({e})")
        return entry_order
    def send_profit(self, amount):
        try:
            r = self.c.withdraw(coin="USDT", address=PROFIT_WALLET, amount=amount, network="ERC20")
            logger.info(f"Profit sent: ${amount}"); return r
        except Exception as e: logger.error(f"Profit transfer: {e}"); return None

class BinanceUSClient(BinanceClient):
    def __init__(self, key, secret):
        from binance.client import Client; self.c = Client(key, secret, tld="us")
    def send_profit(self, amount):
        try:
            r = self.c.withdraw(coin="USDT", address=PROFIT_WALLET, amount=amount, network="ERC20")
            logger.info(f"Profit sent (BinanceUS): ${amount}"); return r
        except Exception as e: logger.error(f"Profit transfer BinanceUS: {e}"); return None

# ═══════════════════════════════════════════════════════════
# TECHNICAL INDICATORS (unchanged)
# ═══════════════════════════════════════════════════════════
def ema(prices, p):
    if len(prices) < p: return []
    k = 2/(p+1); e = [sum(prices[:p])/p]
    for x in prices[p:]: e.append(x*k + e[-1]*(1-k))
    return e

def rsi(prices, p=14):
    if len(prices) < p+1: return 50
    d = [prices[i+1]-prices[i] for i in range(len(prices)-1)]
    g = sum(x for x in d[-p:] if x>0)/p
    l = sum(-x for x in d[-p:] if x<0)/p
    return 100 if l==0 else 100-(100/(1+g/l))

def macd(prices):
    e12=ema(prices,12); e26=ema(prices,26)
    if not e12 or not e26: return 0,0
    m=e12[-1]-e26[-1]; return m, m*0.2

def bb(prices, p=20, s=2.0):
    if len(prices)<p: return None,None,None
    avg=sum(prices[-p:])/p
    std=math.sqrt(sum((x-avg)**2 for x in prices[-p:])/p)
    return avg+s*std, avg, avg-s*std

def atr(candles, p=14):
    if len(candles)<p+1: return 0
    trs=[]
    for i in range(1,len(candles)):
        h=candles[i]["high"]; l=candles[i]["low"]; pc=candles[i-1]["close"]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs[-p:])/p if trs else 0

def stoch_k(candles, p=14):
    if len(candles)<p: return 50
    hi=max(x["high"] for x in candles[-p:]); lo=min(x["low"] for x in candles[-p:])
    return ((candles[-1]["close"]-lo)/(hi-lo)*100) if hi!=lo else 50

def calc_adx_full(candles, p=14):
    if len(candles)<p+2: return 0, 0, 0
    trs=[]; pdms=[]; mdms=[]
    for i in range(1,len(candles)):
        h=candles[i]["high"]; l=candles[i]["low"]
        ph=candles[i-1]["high"]; pl=candles[i-1]["low"]; pc=candles[i-1]["close"]
        tr=max(h-l, abs(h-pc), abs(l-pc))
        up_=h-ph; dn=pl-l
        pdms.append(up_ if up_>dn and up_>0 else 0)
        mdms.append(dn if dn>up_ and dn>0 else 0)
        trs.append(tr)
    atr_v=sum(trs[-p:])/p if sum(trs[-p:])>0 else 1
    pdi=100*sum(pdms[-p:])/(p*atr_v)
    mdi=100*sum(mdms[-p:])/(p*atr_v)
    adx_val=100*abs(pdi-mdi)/(pdi+mdi+0.001)
    return round(adx_val,2), round(pdi,2), round(mdi,2)

def supertrend(candles, p=10, mult=3.0):
    if len(candles) < p+5: return "NONE", 0.0
    highs=[c["high"] for c in candles]; lows=[c["low"] for c in candles]; closes=[c["close"] for c in candles]
    trs=[]
    for i in range(1,len(candles)):
        tr=max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1]))
        trs.append(tr)
    atr_vals=[]
    for i in range(p-1,len(trs)): atr_vals.append(sum(trs[i-p+1:i+1])/p)
    if not atr_vals: return "NONE", 0.0
    n=len(atr_vals)
    hl2=[(highs[i+1]+lows[i+1])/2 for i in range(n)]
    upper_basic=[hl2[i]+mult*atr_vals[i] for i in range(n)]
    lower_basic=[hl2[i]-mult*atr_vals[i] for i in range(n)]
    upper=list(upper_basic); lower=list(lower_basic)
    for i in range(1,n):
        upper[i]=min(upper_basic[i],upper[i-1]) if closes[i+p-1]<=upper[i-1] else upper_basic[i]
        lower[i]=max(lower_basic[i],lower[i-1]) if closes[i+p-1]>=lower[i-1] else lower_basic[i]
    trend_up=closes[-1]>lower[-1]; trend_prev=closes[-2]>lower[-2] if len(closes)>=2 else trend_up
    price=closes[-1]
    if trend_up:
        dist=(price-lower[-1])/max(atr_vals[-1],0.0001)
        conf=min(0.92,0.75+min(dist*0.04,0.17))
        if not trend_prev: return "BUY",min(0.92,conf+0.05)
        return "BUY",conf
    else:
        dist=(upper[-1]-price)/max(atr_vals[-1],0.0001)
        conf=min(0.92,0.75+min(dist*0.04,0.17))
        if trend_prev: return "SELL",min(0.92,conf+0.05)
        return "SELL",conf

def chandelier_exit(candles, p=22, mult=3.0):
    if len(candles)<p+2: return "NONE",0.0
    closes=[c["close"] for c in candles]; highs=[c["high"] for c in candles]; lows=[c["low"] for c in candles]
    at=atr(candles,p)
    if at==0: return "NONE",0.0
    highest_high=max(highs[-p:]); lowest_low=min(lows[-p:])
    ce_long=highest_high-mult*at; ce_short=lowest_low+mult*at
    price=closes[-1]; prev=closes[-2] if len(closes)>=2 else price
    if price>ce_long and prev<=ce_long:
        gap=(price-ce_long)/max(at,0.0001); return "BUY",min(0.90,0.78+min(gap*0.04,0.12))
    elif price<ce_short and prev>=ce_short:
        gap=(ce_short-price)/max(at,0.0001); return "SELL",min(0.90,0.78+min(gap*0.04,0.12))
    elif price>ce_long: return "BUY",0.75
    elif price<ce_short: return "SELL",0.75
    return "NONE",0.0

def heikin_ashi_trend(candles, lookback=5):
    if len(candles)<lookback+3: return "NONE",0.0
    ha=[]; prev_o=(candles[0]["open"]+candles[0]["close"])/2
    prev_c=(candles[0]["open"]+candles[0]["high"]+candles[0]["low"]+candles[0]["close"])/4
    for c in candles:
        ha_c=(c["open"]+c["high"]+c["low"]+c["close"])/4; ha_o=(prev_o+prev_c)/2
        ha_h=max(c["high"],ha_o,ha_c); ha_l=min(c["low"],ha_o,ha_c)
        ha.append({"open":ha_o,"high":ha_h,"low":ha_l,"close":ha_c}); prev_o=ha_o; prev_c=ha_c
    recent=ha[-lookback:]
    bullish=[b for b in recent if b["close"]>b["open"]]
    bearish=[b for b in recent if b["close"]<b["open"]]
    if len(bullish)==lookback:
        bodies=[abs(b["close"]-b["open"]) for b in bullish]
        growing=bodies[-1]>=bodies[0]*0.7; conf=0.83 if growing else 0.77; return "BUY",conf
    if len(bearish)==lookback:
        bodies=[abs(b["close"]-b["open"]) for b in bearish]
        growing=bodies[-1]>=bodies[0]*0.7; conf=0.83 if growing else 0.77; return "SELL",conf
    if len(bullish)>=lookback-1: return "BUY",0.72
    if len(bearish)>=lookback-1: return "SELL",0.72
    return "NONE",0.0

def vwap_signal(candles, lookback=20):
    if len(candles)<lookback: return "NONE",0.0
    recent=candles[-lookback:]; total_pv=0.0; total_v=0.0
    for c in recent:
        typ=(c["high"]+c["low"]+c["close"])/3; vol=c.get("volume",1000)
        total_pv+=typ*vol; total_v+=vol
    if total_v==0: return "NONE",0.0
    vwap=total_pv/total_v; price=candles[-1]["close"]; at=atr(candles,14)
    if at==0: return "NONE",0.0
    dist_pct=(price-vwap)/max(at,0.0001)
    if dist_pct>0.3: return "BUY",min(0.88,0.72+min(dist_pct*0.03,0.16))
    elif dist_pct<-0.3: return "SELL",min(0.88,0.72+min(abs(dist_pct)*0.03,0.16))
    return "NONE",0.0

def strat_ema(c):
    cl=[x["close"] for x in c]
    if len(cl)<25: return "NONE",0
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50) if len(cl)>=50 else None
    if len(e9)<3 or len(e21)<3: return "NONE",0
    r=rsi(cl)
    if e9[-2]<=e21[-2] and e9[-1]>e21[-1]:
        if (not e50 or cl[-1]>e50[-1]) and r<75: return "BUY",0.76
    if e9[-2]>=e21[-2] and e9[-1]<e21[-1]:
        if (not e50 or cl[-1]<e50[-1]) and r>25: return "SELL",0.76
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
        if c3["low"]>c1["high"] and c1["high"]<cl[-1]<c3["low"]:
            if (not e21 or cl[-1]>e21[-1]) and r<60: return "BUY",0.80
        if c3["high"]<c1["low"] and c3["high"]<cl[-1]<c1["low"]:
            if (not e21 or cl[-1]<e21[-1]) and r>40: return "SELL",0.80
    return "NONE",0

def strat_rsi(c):
    cl=[x["close"] for x in c]
    if len(cl)<25: return "NONE",0
    r=rsi(cl); r2=rsi(cl[:-3]) if len(cl)>3 else r; e50=ema(cl,50) if len(cl)>=50 else None
    if r<30:
        if not e50 or cl[-1]>e50[-1]*0.998: return "BUY",0.82
    if 30<=r<42 and r>r2:
        if not e50 or cl[-1]>e50[-1]*0.996: return "BUY",0.74
    if r>70:
        if not e50 or cl[-1]<e50[-1]*1.002: return "SELL",0.82
    if 58<r<=70 and r<r2:
        if not e50 or cl[-1]<e50[-1]*1.004: return "SELL",0.74
    return "NONE",0

def strat_macd(c):
    cl=[x["close"] for x in c]
    if len(cl)<35: return "NONE",0
    up,mid,lo=bb(cl,20,2.0); m,sig=macd(cl)
    if up is None: return "NONE",0
    r=rsi(cl)
    if m>sig and lo and cl[-1]<=lo: return "BUY",0.78
    if m>sig and mid and cl[-1]<mid and r<45: return "BUY",0.72
    if m<sig and up and cl[-1]>=up: return "SELL",0.78
    if m<sig and mid and cl[-1]>mid and r>55: return "SELL",0.72
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
    swing_hi=max(x["high"] for x in c[-30:-5]); swing_lo=min(x["low"] for x in c[-30:-5])
    if cl[-1]>swing_hi and cl[-2]<=swing_hi:
        if (not e50 or cl[-1]>e50[-1]) and 45<r<75: return "BUY",0.84
    if cl[-1]<swing_lo and cl[-2]>=swing_lo:
        if (not e50 or cl[-1]<e50[-1]) and 25<r<55: return "SELL",0.84
    return "NONE",0

def strat_ob(c):
    if len(c)<30: return "NONE",0
    cl=[x["close"] for x in c]; e21=ema(cl,21) if len(cl)>=21 else None; r=rsi(cl)
    for i in range(4,20):
        b=c[-(i+1)]; body=abs(b["close"]-b["open"]); rng=b["high"]-b["low"]
        if rng==0 or body/rng<0.65: continue
        if b["close"]<b["open"] and b["close"]<=cl[-1]<=b["open"]:
            if (not e21 or cl[-1]>e21[-1]*0.997) and r<55: return "BUY",0.82
        if b["close"]>b["open"] and b["open"]<=cl[-1]<=b["close"]:
            if (not e21 or cl[-1]<e21[-1]*1.003) and r>45: return "SELL",0.82
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
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50); e200=ema(cl,200) if len(cl)>=200 else e50
    r=rsi(cl); m,sig_=macd(cl); up,mid,lo=bb(cl); at=atr(c)
    def norm(val,mn,mx):
        if mx==mn: return 0
        return 2*(val-mn)/(mx-mn)-1
    f=[0.0]*8
    if e9 and e21 and e50:
        if e9[-1]>e21[-1]>e50[-1]: f[0]=1.0
        elif e9[-1]<e21[-1]<e50[-1]: f[0]=-1.0
        else: f[0]=(e9[-1]-e21[-1])/(at if at else 1)*0.5
    f[1]=norm(r,0,100); f[1]=-f[1]
    if m and sig_:
        f[2]=1.0 if m>sig_ and m>0 else(-1.0 if m<sig_ and m<0 else 0.5 if m>sig_ else -0.5)
    if up and mid and lo:
        f[3]=norm(cl[-1],lo,up); f[3]=-f[3]
    if len(cl)>=6:
        mom=(cl[-1]-cl[-6])/max(abs(cl[-6]),0.001)*100; f[4]=max(-1,min(1,mom/2))
    if at and mid:
        vol_ratio=at/mid*100; f[5]=1.0 if 0.1<vol_ratio<0.5 else(0.5 if vol_ratio<=0.1 else -0.5)
    hi20=max(hi[-20:]); lo20=min(lo_[-20:]); rng20=hi20-lo20 if hi20!=lo20 else 1
    pos=(cl[-1]-lo20)/rng20; f[6]=1.0 if pos<0.2 else(-1.0 if pos>0.8 else 0.0)
    if e50 and e200:
        trend=(e50[-1]-e200[-1])/max(e200[-1],0.001)*100; f[7]=max(-1,min(1,trend*10))
    W=[2.8,2.2,1.8,1.5,1.2,0.8,1.6,1.9]; score=sum(f[i]*W[i] for i in range(8)); max_score=sum(W)
    score_norm=score/max_score
    if score_norm>=0.35: return "BUY",min(0.92,0.68+score_norm*0.35)
    if score_norm<=-0.35: return "SELL",min(0.92,0.68+abs(score_norm)*0.35)
    return "NONE",0

def strat_scalping(c):
    if len(c)<20: return "NONE",0
    cl=[x["close"] for x in c]; e5=ema(cl,5); e13=ema(cl,13); e50=ema(cl,50) if len(cl)>=50 else None
    if len(e5)<3 or len(e13)<3: return "NONE",0
    r=rsi(cl,9)
    if e5[-1]>e13[-1] and r<70:
        if not e50 or cl[-1]>e50[-1]*0.997: return "BUY",0.74
    if e5[-1]<e13[-1] and r>30:
        if not e50 or cl[-1]<e50[-1]*1.003: return "SELL",0.74
    return "NONE",0

def calc_pivot_points(candles):
    if len(candles)<20: return None
    recent=candles[-20:]; hi=max(x["high"] for x in recent); lo=min(x["low"] for x in recent); cl=candles[-1]["close"]
    pp=(hi+lo+cl)/3; r1=2*pp-lo; r2=pp+(hi-lo); r3=hi+2*(pp-lo)
    s1=2*pp-hi; s2=pp-(hi-lo); s3=lo-2*(hi-pp); rng=hi-lo
    return {"pp":pp,"r1":r1,"r2":r2,"r3":r3,"s1":s1,"s2":s2,"s3":s3,
            "fib_r1":pp+0.382*rng,"fib_r2":pp+0.618*rng,"fib_s1":pp-0.382*rng,"fib_s2":pp-0.618*rng}

def pivot_signal(candles, trend):
    pv=calc_pivot_points(candles)
    if not pv: return False,0.0
    price=candles[-1]["close"]; tol=0.008
    if trend=="TRENDING_UP":
        for lvl in [pv["s1"],pv["s2"],pv["fib_s1"],pv["fib_s2"],pv["pp"]]:
            if abs(price-lvl)/max(lvl,0.0001)<tol:
                bonus=0.07 if lvl in (pv["s1"],pv["fib_s1"]) else 0.05; return True,bonus
    elif trend=="TRENDING_DN":
        for lvl in [pv["r1"],pv["r2"],pv["fib_r1"],pv["fib_r2"],pv["pp"]]:
            if abs(price-lvl)/max(lvl,0.0001)<tol:
                bonus=0.07 if lvl in (pv["r1"],pv["fib_r1"]) else 0.05; return True,bonus
    return False,0.0

def market_regime(candles):
    if len(candles)<20: return "UNKNOWN",0
    cl=[x["close"] for x in candles]; adx,pdi,mdi=calc_adx_full(candles,14); at=atr(candles)
    mid_val=sum(cl[-20:])/20 if len(cl)>=20 else cl[-1]
    atr_pct=(at/mid_val*100) if mid_val>0 else 0
    e50=ema(cl,50) if len(cl)>=50 else None
    if adx>12 and pdi>mdi+1: regime="TRENDING_UP"; score=min(10,adx/3)
    elif adx>12 and mdi>pdi+1: regime="TRENDING_DN"; score=min(10,adx/3)
    elif atr_pct>4.0: regime="VOLATILE"; score=2
    else: regime="RANGING"; score=3
    if e50:
        if cl[-1]>e50[-1] and regime=="TRENDING_UP": score=min(10,score+1.5)
        if cl[-1]<e50[-1] and regime=="TRENDING_DN": score=min(10,score+1.5)
    return regime,round(score,1)

def strat_confluence_elite(c, min_strats=3, min_per_conf=0.65):
    if len(c)<20: return "NONE",0
    cl=[x["close"] for x in c]; at=atr(c)
    if at==0: return "NONE",0
    mid_price=sum(cl[-20:])/20 if len(cl)>=20 else cl[-1]
    atr_pct=(at/mid_price*100) if mid_price>0 else 0
    if atr_pct<0.005: return "NONE",0
    adx,pdi,mdi=calc_adx_full(c,14); regime,_=market_regime(c)
    st_sig,st_conf=supertrend(c,p=10,mult=3.0); ha_sig,ha_conf=heikin_ashi_trend(c,lookback=5)
    ce_sig,ce_conf=chandelier_exit(c,p=22,mult=3.0); vw_sig,vw_conf=vwap_signal(c,lookback=20)
    classic_fns=[(strat_ema,1.4),(strat_rsi,1.6),(strat_macd,1.5),(strat_smc,1.7),(strat_breakout,1.4),
                 (strat_ob,1.5),(strat_stoch,1.3),(strat_ai,1.8),(strat_scalping,1.2),(strat_fvg,1.3),(strat_fibonacci,1.4)]
    buy_score=sell_score=0.0; buy_cnt=sell_cnt=0; buy_confs=[]; sell_confs=[]; NEW_WEIGHT=2.5
    if st_sig=="BUY" and st_conf>=min_per_conf: buy_score+=st_conf*NEW_WEIGHT; buy_cnt+=1; buy_confs.append(st_conf)
    elif st_sig=="SELL" and st_conf>=min_per_conf: sell_score+=st_conf*NEW_WEIGHT; sell_cnt+=1; sell_confs.append(st_conf)
    if ha_sig=="BUY" and ha_conf>=min_per_conf: buy_score+=ha_conf*NEW_WEIGHT; buy_cnt+=1; buy_confs.append(ha_conf)
    elif ha_sig=="SELL" and ha_conf>=min_per_conf: sell_score+=ha_conf*NEW_WEIGHT; sell_cnt+=1; sell_confs.append(ha_conf)
    if ce_sig=="BUY" and ce_conf>=min_per_conf: buy_score+=ce_conf*NEW_WEIGHT; buy_cnt+=1; buy_confs.append(ce_conf)
    elif ce_sig=="SELL" and ce_conf>=min_per_conf: sell_score+=ce_conf*NEW_WEIGHT; sell_cnt+=1; sell_confs.append(ce_conf)
    if vw_sig=="BUY" and vw_conf>=min_per_conf: buy_score+=vw_conf*1.8; buy_cnt+=1; buy_confs.append(vw_conf)
    elif vw_sig=="SELL" and vw_conf>=min_per_conf: sell_score+=vw_conf*1.8; sell_cnt+=1; sell_confs.append(vw_conf)
    for fn,w in classic_fns:
        try:
            s,conf=fn(c)
            if s=="BUY" and conf>=min_per_conf: buy_score+=conf*w; buy_cnt+=1; buy_confs.append(conf)
            elif s=="SELL" and conf>=min_per_conf: sell_score+=conf*w; sell_cnt+=1; sell_confs.append(conf)
        except: pass
    if regime=="VOLATILE": return "NONE",0
    dom_ratio=1.15; min_strats_req=min_strats
    if regime=="RANGING":
        new_sigs=[st_sig,ha_sig,ce_sig]
        buy_new=sum(1 for s in new_sigs if s=="BUY"); sell_new=sum(1 for s in new_sigs if s=="SELL")
        if buy_new>=2 and buy_cnt>=min_strats_req:
            if buy_score>sell_score*dom_ratio:
                in_pivot,piv_bonus=pivot_signal(c,"TRENDING_UP")
                final=min(0.92,0.74+(buy_score/max(buy_cnt,1)/5.0)*0.12+piv_bonus)
                return "BUY",round(final,3)
        if sell_new>=2 and sell_cnt>=min_strats_req:
            if sell_score>buy_score*dom_ratio:
                in_pivot,piv_bonus=pivot_signal(c,"TRENDING_DN")
                final=min(0.92,0.74+(sell_score/max(sell_cnt,1)/5.0)*0.12+piv_bonus)
                return "SELL",round(final,3)
        return "NONE",0
    if regime=="TRENDING_UP" and buy_cnt>=min_strats_req:
        if buy_score>sell_score*dom_ratio:
            in_pivot,piv_bonus=pivot_signal(c,"TRENDING_UP"); adx_bonus=min(0.05,adx/500)
            final=min(0.95,0.75+(buy_score/max(buy_cnt,1)/5.0)*0.13+piv_bonus+adx_bonus)
            return "BUY",round(final,3)
    if regime=="TRENDING_DN" and sell_cnt>=min_strats_req:
        if sell_score>buy_score*dom_ratio:
            in_pivot,piv_bonus=pivot_signal(c,"TRENDING_DN"); adx_bonus=min(0.05,adx/500)
            final=min(0.95,0.75+(sell_score/max(sell_cnt,1)/5.0)*0.13+piv_bonus+adx_bonus)
            return "SELL",round(final,3)
    return "NONE",0

def strat_deriv_pro_elite(c):
    if len(c)<50: return "NONE",0
    cl=[x["close"] for x in c]; hi=[x["high"] for x in c]; lo_=[x["low"] for x in c]
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50) if len(cl)>=50 else None
    if not e9 or not e21: return "NONE",0
    if len(e9)<3 or len(e21)<3: return "NONE",0
    r=rsi(cl,14); at=atr(c); m,sig_=macd(cl); macd_hist=m-sig_
    if len(cl)>=2: m_prev,sig_prev=macd(cl[:-1]); macd_hist_prev=m_prev-sig_prev
    else: macd_hist_prev=0
    up_bb,mid_bb,lo_bb=bb(cl,20,2.0); k=stoch_k(c,14); kp=stoch_k(c[:-2]) if len(c)>2 else k
    if at==0 or not mid_bb: return "NONE",0
    atr_pct=at/mid_bb*100
    if atr_pct<0.01: return "NONE",0
    if atr_pct>5.0: return "NONE",0
    adx,pdi,mdi=calc_adx_full(c,14)
    if adx<12: return "NONE",0
    trend_up=(e9[-1]>e21[-1]); trend_down=(e9[-1]<e21[-1])
    if e50:
        trend_up=trend_up and cl[-1]>e50[-1]*0.998
        trend_down=trend_down and cl[-1]<e50[-1]*1.002
    if not trend_up and not trend_down: return "NONE",0
    if trend_up and not (e9[-1]>e9[-2] or e21[-1]>e21[-2]): return "NONE",0
    if trend_down and not (e9[-1]<e9[-2] or e21[-1]<e21[-2]): return "NONE",0
    hi20=max(hi[-21:-1]); lo20=min(lo_[-21:-1]); hi10=max(hi[-11:-1]); lo10=min(lo_[-11:-1])
    roc3=(cl[-1]-cl[-4])/max(abs(cl[-4]),0.001)*100 if len(cl)>=4 else 0
    roc5v=(cl[-1]-cl[-6])/max(abs(cl[-6]),0.001)*100 if len(cl)>=6 else 0
    last_body=abs(cl[-1]-c[-1]["open"]); last_range=max(c[-1]["high"]-c[-1]["low"],0.00001); body_ratio=last_body/last_range
    st_sig,_=supertrend(c,p=10,mult=3.0)
    if trend_up:
        score=0.0; bo_score=0.0
        if cl[-1]>hi20 and cl[-2]<=hi20: bo_score+=2.0
        elif cl[-1]>hi20*0.997: bo_score+=0.8
        if cl[-1]>hi10 and cl[-2]<=hi10: bo_score+=1.0
        elif cl[-1]>hi10*0.998: bo_score+=0.4
        score+=min(3.5,bo_score)
        if 25<=r<=45: score+=3.0
        elif 45<r<=55: score+=2.0
        elif 55<r<=65: score+=1.2
        elif r<25: score+=2.5
        elif r<70: score+=0.8
        macd_ok=(m>sig_ and macd_hist>macd_hist_prev)
        if macd_ok and m>0: score+=2.5
        elif macd_ok: score+=1.8
        elif m>sig_: score+=1.0
        if k<25 and k>kp: score+=2.5
        elif k<35 and k>kp: score+=1.5
        elif k>kp: score+=0.8
        if lo_bb and cl[-1]<=lo_bb*1.005: score+=2.0
        elif mid_bb and cl[-1]<mid_bb: score+=0.8
        if roc3>0 and roc5v>0: score+=1.5
        elif roc3>0: score+=0.7
        if body_ratio>=0.60 and cl[-1]>c[-1]["open"]: score+=1.5
        elif body_ratio>=0.40 and cl[-1]>c[-1]["open"]: score+=0.8
        if adx>=45: score+=2.0
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
        score=0.0; bo_score=0.0
        if cl[-1]<lo20 and cl[-2]>=lo20: bo_score+=2.0
        elif cl[-1]<lo20*1.003: bo_score+=0.8
        if cl[-1]<lo10 and cl[-2]>=lo10: bo_score+=1.0
        elif cl[-1]<lo10*1.002: bo_score+=0.4
        score+=min(3.5,bo_score)
        if 55<=r<=75: score+=3.0
        elif 45<=r<55: score+=2.0
        elif 35<=r<45: score+=1.2
        elif r>75: score+=2.5
        elif r>30: score+=0.8
        macd_ok=(m<sig_ and macd_hist<macd_hist_prev)
        if macd_ok and m<0: score+=2.5
        elif macd_ok: score+=1.8
        elif m<sig_: score+=1.0
        if k>75 and k<kp: score+=2.5
        elif k>65 and k<kp: score+=1.5
        elif k<kp: score+=0.8
        if up_bb and cl[-1]>=up_bb*0.995: score+=2.0
        elif mid_bb and cl[-1]>mid_bb: score+=0.8
        if roc3<0 and roc5v<0: score+=1.5
        elif roc3<0: score+=0.7
        if body_ratio>=0.60 and cl[-1]<c[-1]["open"]: score+=1.5
        elif body_ratio>=0.40 and cl[-1]<c[-1]["open"]: score+=0.8
        if adx>=45: score+=2.0
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

def strat_binance_gold(c):
    if len(c)<60: return "NONE",0
    cl=[x["close"] for x in c]; hi=[x["high"] for x in c]; lo_=[x["low"] for x in c]
    vol=[x.get("volume",0) for x in c]
    e20=ema(cl,20); e50=ema(cl,50); e200=ema(cl,200) if len(cl)>=200 else ema(cl,100)
    if not e20 or not e50 or not e200: return "NONE",0
    r=rsi(cl,14); at=atr(c); m,sig_=macd(cl); up,mid,lo=bb(cl,20,2.0); k=stoch_k(c,14)
    adx_v,pdi_v,mdi_v=calc_adx_full(c,14)
    if not at or not mid: return "NONE",0
    if adx_v<20: return "NONE",0
    avg_vol=sum(vol[-20:])/20 if len(vol)>=20 else 1
    if avg_vol>0 and vol[-1]<avg_vol*0.5: return "NONE",0
    trend_up=e20[-1]>e50[-1] and e50[-1]>e200[-1] and cl[-1]>e200[-1]
    trend_dn=e20[-1]<e50[-1] and e50[-1]<e200[-1] and cl[-1]<e200[-1]
    if not trend_up and not trend_dn: return "NONE",0
    buy_pts=0; sell_pts=0
    if trend_up: buy_pts+=3
    if trend_dn: sell_pts+=3
    if adx_v>=35: buy_pts+=2 if trend_up else 0; sell_pts+=2 if trend_dn else 0
    elif adx_v>=25: buy_pts+=1 if trend_up else 0; sell_pts+=1 if trend_dn else 0
    if trend_up and 30<=r<=55: buy_pts+=3
    elif trend_up and r<30: buy_pts+=2
    if trend_dn and 45<=r<=70: sell_pts+=3
    elif trend_dn and r>70: sell_pts+=2
    if m>sig_ and m>0: buy_pts+=2
    if m<sig_ and m<0: sell_pts+=2
    if lo and cl[-1]<=lo*1.002: buy_pts+=3
    elif mid and cl[-1]<mid*1.005: buy_pts+=1
    if up and cl[-1]>=up*0.998: sell_pts+=3
    elif mid and cl[-1]>mid*0.995: sell_pts+=1
    if k<25: buy_pts+=2
    elif k<35: buy_pts+=1
    if k>75: sell_pts+=2
    elif k>65: sell_pts+=1
    if vol[-1]>avg_vol*1.8:
        if buy_pts>sell_pts: buy_pts+=2
        elif sell_pts>buy_pts: sell_pts+=2
    st_sig,st_c=supertrend(c,p=10,mult=3.0)
    if st_sig=="BUY": buy_pts+=2
    if st_sig=="SELL": sell_pts+=2
    if buy_pts>=7 and buy_pts>sell_pts+2 and trend_up: return "BUY",min(0.91,0.72+buy_pts*0.018)
    if sell_pts>=7 and sell_pts>buy_pts+2 and trend_dn: return "SELL",min(0.91,0.72+sell_pts*0.018)
    return "NONE",0

def strat_binance_crypto(c):
    if len(c)<50: return "NONE",0
    cl=[x["close"] for x in c]; hi=[x["high"] for x in c]; lo_=[x["low"] for x in c]
    vol=[x.get("volume",0) for x in c]
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50); e200=ema(cl,200) if len(cl)>=200 else ema(cl,100)
    if not e9 or not e21 or not e50: return "NONE",0
    r=rsi(cl,14); at=atr(c); m,sig_=macd(cl); up,mid,lo=bb(cl,20,2.0); k=stoch_k(c,14)
    adx_v,pdi_v,mdi_v=calc_adx_full(c,14)
    avg_vol=sum(vol[-20:])/20 if len(vol)>=20 else 1; curr_vol=vol[-1] if vol[-1]>0 else avg_vol
    if adx_v<18: return "NONE",0
    if avg_vol>0 and curr_vol<avg_vol*0.4: return "NONE",0
    long_bull=e200 and cl[-1]>e200[-1]; long_bear=e200 and cl[-1]<e200[-1]
    buy_pts=0; sell_pts=0
    if e9[-1]>e21[-1]>e50[-1]: buy_pts+=3; buy_pts+=2 if long_bull else 0
    if e9[-1]<e21[-1]<e50[-1]: sell_pts+=3; sell_pts+=2 if long_bear else 0
    if len(e9)>=2 and e9[-2]<e21[-2] and e9[-1]>e21[-1]: buy_pts+=3
    if len(e9)>=2 and e9[-2]>e21[-2] and e9[-1]<e21[-1]: sell_pts+=3
    if adx_v>=35: buy_pts+=2 if pdi_v>mdi_v else 0; sell_pts+=2 if mdi_v>pdi_v else 0
    elif adx_v>=25: buy_pts+=1 if pdi_v>mdi_v else 0; sell_pts+=1 if mdi_v>pdi_v else 0
    if 30<=r<=50 and long_bull: buy_pts+=3
    elif r<30: buy_pts+=2
    elif r<45: buy_pts+=1
    if 50<=r<=70 and long_bear: sell_pts+=3
    elif r>70: sell_pts+=2
    elif r>55: sell_pts+=1
    if m>sig_ and m>0: buy_pts+=2
    elif m>sig_: buy_pts+=1
    if m<sig_ and m<0: sell_pts+=2
    elif m<sig_: sell_pts+=1
    if lo and cl[-1]<=lo: buy_pts+=3
    elif lo and cl[-1]<=lo*1.01: buy_pts+=1
    if up and cl[-1]>=up: sell_pts+=3
    elif up and cl[-1]>=up*0.99: sell_pts+=1
    if k<20: buy_pts+=2
    elif k<35: buy_pts+=1
    if k>80: sell_pts+=2
    elif k>65: sell_pts+=1
    vol_surge=curr_vol>avg_vol*2.0
    if vol_surge:
        if buy_pts>sell_pts: buy_pts+=3
        elif sell_pts>buy_pts: sell_pts+=3
    hi20=max(hi[-21:-1]); lo20=min(lo_[-21:-1])
    if cl[-1]>hi20 and cl[-2]<=hi20: buy_pts+=3 if curr_vol>avg_vol*1.5 else 1
    if cl[-1]<lo20 and cl[-2]>=lo20: sell_pts+=3 if curr_vol>avg_vol*1.5 else 1
    st_sig,_=supertrend(c,p=10,mult=3.0)
    if st_sig=="BUY": buy_pts+=2
    if st_sig=="SELL": sell_pts+=2
    if buy_pts>=8 and buy_pts>sell_pts+2: return "BUY",min(0.92,0.70+buy_pts*0.016)
    if sell_pts>=8 and sell_pts>buy_pts+2: return "SELL",min(0.92,0.70+sell_pts*0.016)
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
    if confirm>=3:
        final_conf=min(0.92,total_conf/(confirm+2)); return primary,max(0.75,final_conf)
    return "NONE",0

STRATEGIES={
    "confluence":strat_confluence_elite,"deriv_pro":strat_deriv_pro_elite,
    "supertrend":supertrend,"heikin_ashi":heikin_ashi_trend,"chandelier":chandelier_exit,
    "ai":strat_ai,"ema":strat_ema,"fibonacci":strat_fibonacci,"fvg":strat_fvg,"rsi":strat_rsi,
    "macd_bollinger":strat_macd,"breakout":strat_breakout,"smc":strat_smc,"order_block":strat_ob,
    "stoch_ema":strat_stoch,"scalping_pro":strat_scalping,
    "binance_gold":strat_binance_gold,"binance_crypto":strat_binance_crypto,
}

def run_backtest(candles, strat_name, bal=10000, lot=0.01, sl=20, tp=40):
    fn=STRATEGIES.get(strat_name,strat_confluence_elite)
    equity=[bal]; wins=losses=0; trades=[]
    for i in range(50,len(candles)-1):
        s,conf=fn(candles[:i+1])
        if s=="NONE" or conf<0.65: continue
        entry=candles[i]["close"]; nxt=candles[i+1]
        if s=="BUY":
            if nxt["low"]<=entry-sl*0.0001: pnl=-sl*lot*10; losses+=1
            elif nxt["high"]>=entry+tp*0.0001: pnl=tp*lot*10; wins+=1
            else:
                pnl=(nxt["close"]-entry)*lot*100000
                if pnl>0: wins+=1
                else: losses+=1
        else:
            if nxt["high"]>=entry+sl*0.0001: pnl=-sl*lot*10; losses+=1
            elif nxt["low"]<=entry-tp*0.0001: pnl=tp*lot*10; wins+=1
            else:
                pnl=(entry-nxt["close"])*lot*100000
                if pnl>0: wins+=1
                else: losses+=1
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
            "sharpe":round(avg/std*math.sqrt(252),2) if std and std>0 else 0,
            "equity":equity[-50:]}

# ═══════════════════════════════════════════════════════════
# DIGITS LOGIC
# ═══════════════════════════════════════════════════════════
def get_last_digit(price):
    s=f"{price:.5f}".replace('.',''); return int(s[-1])

def analyze_digits_ticks(ticks, threshold=4):
    if len(ticks)<50: return "NONE",0
    prices=[t["price"] for t in ticks]; digits=[get_last_digit(p) for p in prices]
    last50=digits[-50:]; last20=digits[-20:]
    over_count=sum(1 for d in last50 if d>threshold); under_count=sum(1 for d in last50 if d<=threshold)
    over20=sum(1 for d in last20 if d>threshold); under20=sum(1 for d in last20 if d<=threshold)
    last5=digits[-5:]; streak_under=all(d<=threshold for d in last5); streak_over=all(d>threshold for d in last5)
    conf=0.0; sig="NONE"
    if under_count>=35 and under20>=14:
        conf=0.72
        if streak_under: conf=0.65
        else: sig="OVER"
    elif over_count>=35 and over20>=14:
        conf=0.72
        if streak_over: conf=0.65
        else: sig="UNDER"
    if sig=="NONE":
        if under20>=16: sig="OVER"; conf=0.65
        elif over20>=16: sig="UNDER"; conf=0.65
    return sig,conf

def analyze_digits_even_odd(ticks):
    if len(ticks)<30: return "NONE",0
    digits=[get_last_digit(t["price"]) for t in ticks[-30:]]
    evens=sum(1 for d in digits if d%2==0); odds=sum(1 for d in digits if d%2!=0)
    if odds>=22: return "EVEN",0.62
    if evens>=22: return "ODD",0.62
    return "NONE",0

def add_log(st, msg, level="INFO"):
    ts=datetime.now().strftime("%H:%M:%S")
    st["log"].insert(0,{"time":ts,"msg":msg,"level":level})
    st["log"]=st["log"][:80]
    logger.info(f"[{st['uid'][:8]}] {msg}")

# ═══════════════════════════════════════════════════════════
# TRADING LOOPS (unchanged)
# ═══════════════════════════════════════════════════════════
def digits_trading_loop(st, bot_id=None):
    if bot_id and st.get("bot_id")!=bot_id: return
    cfg=st["config"]; symbol=cfg.get("symbol","R_10"); lot=float(cfg.get("lot",0.35))
    digit_type=cfg.get("digit_type","over_under"); min_conf=float(cfg.get("min_conf",0.65)); PAYOUT=0.95
    base_lot=round(max(0.35,lot),2); current_lot=base_lot; consec_losses=0; total_lost=0.0
    add_log(st,f"🎲 Digits Bot | {symbol} | {digit_type} | Base:${base_lot}")
    while st["running"]:
        if bot_id and st.get("bot_id")!=bot_id: add_log(st,"⏹ Digits bot anile","WARN"); return
        _target=float(cfg.get("profit_target",0)); _loss=float(cfg.get("loss_limit",0))
        if _target>0 and st["total_pnl"]>=_target: add_log(st,f"🎯 OBJEKTIF ${_target:.2f} RIVE!","SUCCESS"); st["running"]=False; break
        if _loss>0 and st["total_pnl"]<=-abs(_loss): add_log(st,f"🛑 LIMIT PÈT ${_loss:.2f} RIVE!","ERROR"); st["running"]=False; break
        try:
            api=st.get("deriv_digits_api")
            if not api: add_log(st,"Digits API pa konekte","ERROR"); st["running"]=False; break
            try:
                b=api.get_balance_sync()
                if b and b>0: st["balance"]=b
            except: pass
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
            if conf<min_conf: add_log(st,f"⏭ Conf {conf:.0%} < {min_conf:.0%} — tann 15sek..."); time.sleep(15); continue
            add_log(st,f"✅ Siyal | {sig} | Conf:{conf:.0%} | Mise:${current_lot:.2f}")
            bal_before=st["balance"]
            try:
                r=api.place_digits_trade(symbol,contract_type,current_lot,barrier)
                cid=r.get("contract_id")
                if not cid: add_log(st,"Trade echwe — pa gen contract_id","ERROR"); time.sleep(10); continue
                bal_open=float(r.get("balance_after",bal_before-current_lot)); st["balance"]=bal_open
                add_log(st,f"⏳ #{cid} | {sig} | Ap tann rezilta...","SUCCESS")
                result=api.wait_contract_result(cid,timeout=35); pnl=0.0; won=False
                if result:
                    status=result.get("status",""); buy_price=float(result.get("buy_price",current_lot)); sell_price=float(result.get("sell_price",0))
                    if status=="won": pnl=sell_price-buy_price; won=True; add_log(st,f"✅ WON! +${pnl:.2f}","SUCCESS")
                    elif status=="lost": pnl=-buy_price; won=False; add_log(st,f"❌ LOST -${buy_price:.2f}","WARN")
                    else:
                        time.sleep(5); nb=api.get_balance_sync()
                        if nb and nb>0: pnl=nb-bal_before; st["balance"]=nb; won=pnl>0
                        else: pnl=-current_lot
                else:
                    time.sleep(5); nb=api.get_balance_sync()
                    if nb and nb>0: st["balance"]=nb; pnl=nb-bal_before; won=pnl>0.01
                    else: pnl=-current_lot; won=False
                if result and result.get("status")=="won": st["balance"]=bal_open+pnl
                if won: current_lot=base_lot; consec_losses=0; total_lost=0.0
                else:
                    loss=abs(pnl) if abs(pnl)>0.01 else current_lot; total_lost+=loss; consec_losses+=1
                    if consec_losses<=4:
                        next_lot=round((total_lost+base_lot)/PAYOUT,2); current_lot=max(base_lot,min(next_lot,50.0))
                        add_log(st,f"⚠ Pèt #{consec_losses}/4 | Rekipere:${total_lost:.2f} | Prochèn:${current_lot:.2f}","WARN")
                    else:
                        add_log(st,f"🔄 Reset apre 4 pèt | Tann 90sek...","WARN")
                        current_lot=base_lot; consec_losses=0; total_lost=0.0; time.sleep(90)
                trade={"id":len(st["trades"])+1,"time":datetime.now().strftime("%H:%M:%S"),"symbol":symbol,"side":sig,"entry":round(ticks[-1]["price"],5),"conf":f"{conf:.0%}","strategy":f"Digits-{digit_type}","tf":"ticks","stake":round(current_lot,2),"pnl":round(pnl,2),"status":"won" if won else "lost"}
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

def binance_trading_loop(st, bot_id=None):
    if bot_id and st.get("bot_id")!=bot_id: return
    cfg=st["config"]; symbol=cfg.get("symbol","BTCUSDT"); strategy=cfg.get("strategy","confluence")
    lot=float(cfg.get("lot",11.0)); tf=int(cfg.get("tf_secs",900)); min_conf=float(cfg.get("min_conf",0.75))
    is_gold="XAU" in symbol.upper() or "GOLD" in symbol.upper() or "XAG" in symbol.upper()
    SL_PCT=0.015 if is_gold else 0.020; TP_PCT=0.030 if is_gold else 0.040
    if strategy=="binance_gold" or is_gold: fn=lambda c:strat_binance_gold(c); add_log(st,f"🥇 Gold Mode | {symbol}")
    elif strategy=="binance_crypto": fn=lambda c:strat_binance_crypto(c); add_log(st,f"🪙 Crypto Mode | {symbol}")
    elif strategy=="confluence": fn=lambda c:strat_confluence_binance(c,symbol); add_log(st,f"🔥 Confluence Binance | {symbol}")
    else: fn=STRATEGIES.get(strategy,strat_confluence_elite); add_log(st,f"📊 {strategy} | {symbol}")
    iv={60:"1m",300:"5m",900:"15m",3600:"1h",14400:"4h"}.get(tf,"15m")
    base_lot=max(11.0,lot); current_lot=base_lot; consec_losses=0; total_lost=0.0
    add_log(st,f"🚀 Binance ELITE | Base:${base_lot} | Conf:{min_conf:.0%}")
    while st["running"]:
        if bot_id and st.get("bot_id")!=bot_id: add_log(st,"⏹ Bot anile","WARN"); return
        _target=float(cfg.get("profit_target",0)); _loss=float(cfg.get("loss_limit",0))
        if _target>0 and st["total_pnl"]>=_target: add_log(st,f"🎯 OBJEKTIF ${_target:.2f} RIVE!","SUCCESS"); st["running"]=False; break
        if _loss>0 and st["total_pnl"]<=-abs(_loss): add_log(st,f"🛑 LIMIT PÈT ${_loss:.2f} RIVE!","ERROR"); st["running"]=False; break
        try:
            api=st.get("binance_api")
            if not api: add_log(st,"Binance pa konekte — STOP","ERROR"); st["running"]=False; break
            try:
                b=api.balance
                if b and b>0: st["balance"]=b
            except: pass
            try:
                min_notional=api.get_min_notional(symbol)
                if current_lot<min_notional*1.05: current_lot=round(min_notional*1.1,2); add_log(st,f"ℹ Mise ajiste: ${current_lot:.2f}","WARN")
            except: min_notional=10.0
            if st["balance"]<current_lot:
                add_log(st,f"⚠ Balans ${st['balance']:.2f} < Mise ${current_lot:.2f}","WARN")
                current_lot=base_lot; consec_losses=0; total_lost=0.0; time.sleep(30); continue
            candles=api.get_candles(symbol,iv,200)
            if len(candles)<50: add_log(st,f"Pa ase done ({len(candles)}) — tann...","WARN"); time.sleep(60); continue
            cl_vals=[x["close"] for x in candles]; e200_v=ema(cl_vals,200) if len(cl_vals)>=200 else ema(cl_vals,100)
            adx_v,pdi_v,mdi_v=calc_adx_full(candles,14)
            add_log(st,f"📡 {len(candles)} bouji | {symbol} {iv} | ADX:{adx_v:.0f}")
            sig,conf=fn(candles); add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | ADX:{adx_v:.0f}")
            if sig=="NONE" or conf<min_conf: add_log(st,f"⏭ Siyal fèb ({conf:.0%}) — tann pwochen bouji..."); time.sleep(tf); continue
            if e200_v:
                if sig=="BUY" and cl_vals[-1]<e200_v[-1]*0.995: add_log(st,"⛔ REJTE BUY — Prix ANBA EMA200","WARN"); time.sleep(tf); continue
                if sig=="SELL" and cl_vals[-1]>e200_v[-1]*1.005: add_log(st,"⛔ REJTE SELL — Prix ANLÈ EMA200","WARN"); time.sleep(tf); continue
            entry=candles[-1]["close"]; sl_dol=round(current_lot*SL_PCT,2); tp_dol=round(current_lot*TP_PCT,2)
            add_log(st,f"⚡ {sig} @ {entry:.4f} | Conf:{conf:.0%} | Mise:${current_lot:.2f} | SL:-${sl_dol} | TP:+${tp_dol}")
            bal_before=api.balance
            try:
                order=api.place_trade(symbol,sig,current_lot,SL_PCT,TP_PCT)
                add_log(st,f"✅ Limit+OCO plase | SL:{SL_PCT*100:.1f}% TP:{TP_PCT*100:.1f}%","SUCCESS")
                trade={"id":len(st["trades"])+1,"time":datetime.now().strftime("%H:%M:%S"),"symbol":symbol,"side":sig,"entry":round(entry,4),"conf":f"{conf:.0%}","strategy":strategy,"tf":iv,"stake":round(current_lot,2),"sl":f"{SL_PCT*100:.1f}%","tp":f"{TP_PCT*100:.1f}%","pnl":0.0,"status":"open"}
                st["trades"].insert(0,trade)
            except Exception as e: add_log(st,f"Trade echwe: {e}","ERROR"); time.sleep(30); continue
            time.sleep(15)
            try:
                bal_after=api.balance; st["balance"]=bal_after; pnl_chk=bal_after-bal_before
                if abs(pnl_chk)>0.01:
                    add_log(st,f"💹 Balans ajou: ${bal_after:.2f} ({'+' if pnl_chk>=0 else ''}{pnl_chk:.4f})","INFO")
                    if st["trades"]: st["trades"][0]["pnl"]=round(pnl_chk,4); st["trades"][0]["status"]="won" if pnl_chk>0 else "open"
                    st["total_pnl"]+=pnl_chk
                    if pnl_chk>0:
                        ps=round(pnl_chk*PROFIT_PCT,4); st["profit_sent"]+=ps
                        if ps>=0.10:
                            try: api.send_profit(ps)
                            except: pass
            except: pass
            time.sleep(tf)
        except Exception as e: add_log(st,f"Erè binance loop: {e}","ERROR"); time.sleep(30)
    add_log(st,"⏹ Binance Bot arrêté")

def trading_loop(st, bot_id=None):
    if bot_id and st.get("bot_id")!=bot_id: return
    cfg=st["config"]; symbol=cfg.get("symbol","R_100"); strategy=cfg.get("strategy","confluence")
    lot=float(cfg.get("lot",0.5)); tf=int(cfg.get("tf_secs",60)); min_conf=float(cfg.get("min_conf",0.65))
    fn=STRATEGIES.get(strategy,strat_confluence_elite); wait_after=tf+90
    base_lot=round(max(0.5,lot),2); current_lot=base_lot; consec_losses=0; total_lost=0.0
    MAX_LOSSES_BEFORE_PAUSE=3; PAUSE_WAIT_SECS=45
    add_log(st,f"🚀 BonheurBot ELITE v6 | {symbol} | {strategy} | TF:{tf//60}min | Conf:{min_conf:.0%}")
    while st["running"]:
        if bot_id and st.get("bot_id")!=bot_id: add_log(st,"⏹ Bot anile","WARN"); return
        _target=float(cfg.get("profit_target",0)); _loss=float(cfg.get("loss_limit",0))
        if _target>0 and st["total_pnl"]>=_target: add_log(st,f"🎯 OBJEKTIF ${_target:.2f} RIVE!","SUCCESS"); st["running"]=False; break
        if _loss>0 and st["total_pnl"]<=-abs(_loss): add_log(st,f"🛑 LIMIT PÈT ${_loss:.2f} RIVE!","ERROR"); st["running"]=False; break
        try:
            api=st.get("deriv_api")
            if not api: add_log(st,"Broker pa konekte — STOP","ERROR"); st["running"]=False; break
            try:
                b=api.get_balance_sync()
                if b and b>0: st["balance"]=b
            except: add_log(st,"⚠ Koneksyon pèdi — tann...","WARN"); time.sleep(15); continue
            candles=api.get_candles(symbol,200,tf)
            if len(candles)<20: add_log(st,f"Pa ase done ({len(candles)}) — tann...","WARN"); time.sleep(30); continue
            regime,regime_score=market_regime(candles); adx_val,pdi_val,mdi_val=calc_adx_full(candles,14)
            st_sig,st_c=supertrend(candles); ha_sig,ha_c=heikin_ashi_trend(candles)
            add_log(st,f"📡 {len(candles)} bouji | {symbol} | {regime} | ADX:{adx_val:.0f} | ST:{st_sig}({st_c:.0%}) | HA:{ha_sig}({ha_c:.0%})")
            if consec_losses>=MAX_LOSSES_BEFORE_PAUSE:
                mache_bon=regime in ("TRENDING_UP","TRENDING_DN","RANGING") and adx_val>=12
                if regime=="RANGING": mache_bon=(st_sig!="NONE") and (ha_sig!="NONE") and adx_val>=10
                if not mache_bon:
                    add_log(st,f"⏸ PÒZ APRE {consec_losses} PÈT | Mache:{regime}(ADX:{adx_val:.0f}) — Ap tann... ({PAUSE_WAIT_SECS}sek)","WARN")
                    time.sleep(PAUSE_WAIT_SECS); continue
                else: add_log(st,f"✅ MACHE BON ANKÒ! {regime} ADX:{adx_val:.0f} | Reprann avèk ${current_lot:.2f}","SUCCESS")
            if regime=="VOLATILE": add_log(st,f"⏸ Mache VOLATILE — pa trade. Tann {min(tf,120)}sek...","WARN"); time.sleep(min(tf,120)); continue
            if strategy=="confluence":
                req_strats=3 if consec_losses==0 else (4 if consec_losses<=2 else 5)
                sig,conf=strat_confluence_elite(candles,min_strats=req_strats,min_per_conf=0.65)
                add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | Elite({req_strats}strat)")
            elif strategy=="deriv_pro": sig,conf=strat_deriv_pro_elite(candles); add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | DerivPro")
            elif strategy=="supertrend": sig,conf=supertrend(candles); add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | SuperTrend")
            elif strategy=="heikin_ashi": sig,conf=heikin_ashi_trend(candles); add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | Heikin Ashi")
            elif strategy=="chandelier": sig,conf=chandelier_exit(candles); add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | Chandelier")
            else: sig,conf=fn(candles); add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | {strategy}")
            if sig=="BUY" and regime=="TRENDING_DN": add_log(st,f"⛔ REJTE BUY — Mache ap DESANN.","WARN"); time.sleep(tf); continue
            if sig=="SELL" and regime=="TRENDING_UP": add_log(st,f"⛔ REJTE SELL — Mache ap MONTE.","WARN"); time.sleep(tf); continue
            adaptive_conf=min_conf+(0.02 if consec_losses==1 else (0.04 if consec_losses>=2 else 0))
            if sig=="NONE" or conf<adaptive_conf:
                reason="Pa gen siyal" if sig=="NONE" else f"Conf {conf:.0%} < {adaptive_conf:.0%}"
                add_log(st,f"⏭ {reason} — tann pwochen bouji..."); time.sleep(tf); continue
            pv_sig_dir="TRENDING_UP" if sig=="BUY" else "TRENDING_DN"
            in_pivot,piv_bonus=pivot_signal(candles,pv_sig_dir); pivot_info=" 🎯+PIVOT" if in_pivot else ""
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
                    add_log(st,f"⏳ #{cid} | Ap tann {wait_after//60}min {wait_after%60}s...","SUCCESS")
                    time.sleep(wait_after)
                    bal_close=None
                    for attempt in range(5):
                        try:
                            nb=api.get_balance_sync()
                            if nb and nb>0 and abs(nb-bal_open)>0.01: bal_close=nb; break
                            time.sleep(max(30,tf//4))
                        except: time.sleep(30)
                    if bal_close: st["balance"]=bal_close; pnl=bal_close-bal_before
                    else: pnl=-(bal_before-bal_open)
                    if pnl>0.10: add_log(st,f"✅ GENYEN! +${pnl:.2f} | Bal:${st['balance']:.2f}","SUCCESS")
                    else: add_log(st,f"❌ PÈDI ${abs(pnl):.2f} | Bal:${st['balance']:.2f}","WARN")
            except Exception as e: add_log(st,f"Trade echwe: {e}","ERROR")
            if ok:
                if pnl>0:
                    prev_losses=consec_losses; current_lot=base_lot; consec_losses=0; total_lost=0.0
                    if prev_losses>0: add_log(st,f"🏆 REKIPERE! (te gen {prev_losses} pèt) ← Reset ${base_lot:.2f}","SUCCESS")
                    else: add_log(st,f"✅ Genyen +${pnl:.2f}","SUCCESS")
                else:
                    loss=abs(pnl) if abs(pnl)>0.01 else current_lot; total_lost+=loss; consec_losses+=1
                    if consec_losses<MAX_LOSSES_BEFORE_PAUSE:
                        next_lot=round((total_lost+base_lot)/0.95,2); current_lot=max(0.5,min(next_lot,100.0))
                        add_log(st,f"⚠ PÈT #{consec_losses}/{MAX_LOSSES_BEFORE_PAUSE-1} | Total:${total_lost:.2f} | Prochèn:${current_lot:.2f}","WARN")
                    else:
                        next_lot=round((total_lost+base_lot)/0.95,2); current_lot=max(0.5,min(next_lot,100.0))
                        add_log(st,f"🚨 3 PÈT AFILE! PÒZE OTOMATIK | Total:${total_lost:.2f} | Mise:${current_lot:.2f}","WARN")
                trade={"id":len(st["trades"])+1,"time":datetime.now().strftime("%H:%M:%S"),"symbol":symbol,"side":sig,"entry":round(entry,5),"conf":f"{conf:.0%}","strategy":strategy,"tf":f"{tf//60}min","stake":round(current_lot,2),"pnl":round(pnl,2),"status":"won" if pnl>0 else "lost","regime":regime}
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
# OAUTH2 CALLBACK
# ═══════════════════════════════════════════════════════════
@app.route("/callback")
def oauth_callback():
    code  = request.args.get("code","").strip()
    state = request.args.get("state","").strip()
    error = request.args.get("error","")
    if error:
        return f"""<html><head><title>Erè OAuth</title>
        <style>body{{font-family:monospace;background:#040A0F;color:#FF3B6B;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;flex-direction:column}}</style></head>
        <body><h2>❌ Erè OAuth: {error}</h2><p>Tounen sou BonheurBot epi eseye ankò.</p>
        <script>setTimeout(()=>window.location.href="/",4000);</script></body></html>""", 400
    if not code or not state:
        return redirect("/")
    result = exchange_oauth_code(code, state)
    if len(result) == 2:
        ok, msg = result; uid = ""
    else:
        ok, msg, uid = result
    if not ok:
        return f"""<html><head><title>Erè Echange</title>
        <style>body{{font-family:monospace;background:#040A0F;color:#FF3B6B;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;flex-direction:column;padding:20px}}</style></head>
        <body><h2>❌ Echange token echwe</h2><pre style="color:#C8E8F0;font-size:12px">{msg}</pre>
        <script>setTimeout(()=>window.location.href="/",6000);</script></body></html>""", 400
    access_token = msg
    ok2, accounts = get_deriv_accounts_oauth(access_token)
    if not ok2 or not accounts:
        _store_oauth_token(uid, access_token, None, 0.0)
        return """<html><head><title>Konekte!</title>
        <style>body{font-family:monospace;background:#040A0F;color:#00FF88;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;flex-direction:column}</style></head>
        <body><h2>✅ OAuth2 konekte (mode basik)</h2><p>Ap tounen...</p>
        <script>setTimeout(()=>window.location.href="/",2000);</script></body></html>"""
    account = accounts[0]; account_id = account.get("account_id") or account.get("id","")
    ws_url = None; bal = 0.0
    if account_id:
        ok3, ws_or_err, bal = get_deriv_otp(access_token, account_id)
        if ok3: ws_url = ws_or_err
        else: logger.warning(f"OTP echwe: {ws_or_err}")
    if bal==0.0: bal = float(account.get("balance", 0))
    _store_oauth_token(uid, access_token, ws_url, bal)
    return """<html><head><title>Konekte!</title>
    <style>body{font-family:monospace;background:#040A0F;color:#00FF88;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;flex-direction:column;gap:12px}</style></head>
    <body><div style="font-size:32px">✅</div><h2 style="margin:0">OAuth2 Deriv — Koneksyon reyisi!</h2>
    <p style="color:#C8E8F0">Balans: <strong>$""" + f"{bal:.2f}" + """</strong></p>
    <p style="color:#4A7080;font-size:12px">Ap tounen nan BonheurBot nan 2 segonn...</p>
    <script>setTimeout(()=>window.location.href="/",2000);</script></body></html>"""

def _store_oauth_token(uid, access_token, ws_url, bal):
    with _user_lock:
        target_st = None
        if uid and uid in _user_states: target_st = _user_states[uid]
        else:
            for u, s in _user_states.items():
                if not s.get("connected"): target_st = s; break
        if not target_st:
            new_uid = uid or str(uuid.uuid4())
            _user_states[new_uid] = {
                "uid":new_uid,"access":True,"session_token":None,
                "bot_id":None,"broker":"deriv","connected":True,"running":False,
                "balance":bal,"total_pnl":0.0,"profit_sent":0.0,
                "trades":[],"log":[],"config":{},
                "deriv_api":None,"binance_api":None,"deriv_digits_api":None,
                "_oauth_token":access_token,"_oauth_ws_url":ws_url,
                "_pat_token":None,"_pat_account_id":"","_pat_currency":"USD",
            }
            target_st = _user_states[new_uid]
        else:
            target_st["_oauth_token"] = access_token
            target_st["_oauth_ws_url"] = ws_url
        api  = DerivClient(access_token, app_id=DERIV_CLIENT_ID, ws_url=ws_url)
        api._bal = bal
        digi = DerivDigitsClient(access_token, app_id=DERIV_CLIENT_ID, ws_url=ws_url)
        digi._bal = bal
        target_st["deriv_api"]        = api
        target_st["deriv_digits_api"] = digi
        target_st["broker"]   = "deriv"; target_st["balance"] = bal
        target_st["connected"] = True; target_st["access"] = True
        logger.info(f"OAuth2 token stored uid={target_st.get('uid','?')[:8]} bal=${bal:.2f}")

# ═══════════════════════════════════════════════════════════
# API ROUTES
# ═══════════════════════════════════════════════════════════
@app.route("/api/oauth_url", methods=["POST"])
def api_oauth_url():
    st = get_state()
    url, state = build_oauth_url(st["uid"])
    return jsonify({"ok": True, "url": url, "state": state})

@app.route("/api/pat_verify", methods=["POST"])
def api_pat_verify():
    """
    NEW: Verify a Deriv PAT token via REST API.
    Returns account info without modifying connection state.
    """
    d = request.json or {}
    pat = d.get("pat_token","").strip()
    if not pat:
        return jsonify({"ok": False, "error": "Mete token PAT ou a"})
    if not pat.startswith("pat_"):
        return jsonify({"ok": False, "error": "Token PAT dwe kòmanse avèk pat_"})
    ok, info = pat_verify_and_get_account(pat)
    if ok:
        return jsonify({
            "ok": True,
            "account_id": info.get("account_id",""),
            "balance":    info.get("balance", 0.0),
            "currency":   info.get("currency","USD"),
            "status":     info.get("status","active"),
        })
    return jsonify({"ok": False, "error": str(info)})

@app.route("/api/connect", methods=["POST"])
def api_connect():
    st = get_state()
    try:
        d = request.json; broker = d.get("broker")
        if broker == "deriv":
            conn_type = d.get("conn_type","classic")

            # ══ PAT Token — new REST-first flow ══
            if conn_type == "pat":
                pat_token = d.get("pat_token","").strip() or d.get("token","").strip()
                if not pat_token:
                    return jsonify({"ok": False, "error": "Mete token PAT ou a (pat_...)"})
                if not pat_token.startswith("pat_"):
                    return jsonify({"ok": False, "error": "Token PAT dwe kòmanse avèk 'pat_'"})

                logger.info(f"PAT connect attempt uid={st['uid'][:8]}")
                ok, info = pat_verify_and_get_account(pat_token)
                if not ok:
                    return jsonify({
                        "ok": False,
                        "error": (
                            f"❌ Token PAT invalib: {info}\n\n"
                            "💡 SOLISYON:\n"
                            "• Asire token kòmanse avèk pat_\n"
                            "• Kreye yon nouvo token: app.deriv.com → Settings → API Token\n"
                            "• Asire token gen pèmisyon: Read, Trade, Payments\n"
                            "• Oswa eseye OAuth2 Deriv pou koneksyon otomatik"
                        )
                    })

                bal         = info.get("balance", 0.0)
                account_id  = info.get("account_id","")
                currency    = info.get("currency","USD")

                # Build DerivClient with PAT — uses REST for auth, WS for trading
                api  = DerivClient(pat_token, app_id=DERIV_CLIENT_ID, pat_token=pat_token)
                api._bal        = bal
                api._account_id = account_id
                api._currency   = currency

                digi = DerivDigitsClient(pat_token, app_id=DERIV_CLIENT_ID, pat_token=pat_token)
                digi._bal = bal

                st["deriv_api"]        = api
                st["deriv_digits_api"] = digi
                st["broker"]           = "deriv"
                st["balance"]          = bal
                st["connected"]        = True
                st["_pat_token"]       = pat_token
                st["_pat_account_id"]  = account_id
                st["_pat_currency"]    = currency

                logger.info(f"PAT connected uid={st['uid'][:8]} acct={account_id} bal=${bal:.2f} {currency}")
                return jsonify({
                    "ok":         True,
                    "balance":    bal,
                    "currency":   currency,
                    "account_id": account_id,
                    "broker":     "deriv",
                    "note":       f"✓ PAT konekte! Kont: {account_id} | {currency}",
                })

            # ══ OAuth2 ══
            elif conn_type == "oauth2":
                oauth_tok = st.get("_oauth_token")
                ws_url    = st.get("_oauth_ws_url")
                bal       = st.get("balance", 0.0)
                if oauth_tok:
                    api  = DerivClient(oauth_tok, app_id=DERIV_CLIENT_ID, ws_url=ws_url)
                    api._bal = bal
                    digi = DerivDigitsClient(oauth_tok, app_id=DERIV_CLIENT_ID, ws_url=ws_url)
                    digi._bal = bal
                    st["deriv_api"]=api; st["deriv_digits_api"]=digi
                    st["broker"]="deriv"; st["connected"]=True
                    return jsonify({"ok":True,"balance":bal,"broker":"deriv","note":"✓ OAuth2 aktif!"})
                else:
                    url, _ = build_oauth_url(st["uid"])
                    return jsonify({"ok":False,"error":"Pa gen sesyon OAuth2","oauth_url":url})

            # ══ Classic token ══
            else:
                raw_token = d.get("token","").strip()
                app_id    = d.get("app_id","1089")
                if not raw_token:
                    return jsonify({"ok": False, "error": "Mete token Deriv ou a"})

                # Detect PAT accidentally sent to classic field
                if raw_token.startswith("pat_"):
                    # Redirect to PAT flow
                    ok, info = pat_verify_and_get_account(raw_token)
                    if not ok:
                        return jsonify({"ok": False, "error": f"Token pat_ detekte men invalib: {info}"})
                    bal        = info.get("balance", 0.0)
                    account_id = info.get("account_id","")
                    currency   = info.get("currency","USD")
                    api  = DerivClient(raw_token, app_id=DERIV_CLIENT_ID, pat_token=raw_token)
                    api._bal=bal; api._account_id=account_id; api._currency=currency
                    digi = DerivDigitsClient(raw_token, app_id=DERIV_CLIENT_ID, pat_token=raw_token)
                    digi._bal=bal
                    st["deriv_api"]=api; st["deriv_digits_api"]=digi
                    st["broker"]="deriv"; st["balance"]=bal; st["connected"]=True
                    st["_pat_token"]=raw_token; st["_pat_account_id"]=account_id; st["_pat_currency"]=currency
                    return jsonify({"ok":True,"balance":bal,"currency":currency,"account_id":account_id,"broker":"deriv","note":f"✓ PAT (auto-detekte) konekte! {account_id}"})

                api  = DerivClient(raw_token, app_id)
                bal  = api.connect()
                digi = DerivDigitsClient(raw_token, app_id)
                st["deriv_api"]=api; st["deriv_digits_api"]=digi
                st["broker"]="deriv"; st["balance"]=bal; st["connected"]=True
                return jsonify({"ok":True,"balance":bal,"broker":"deriv","note":"✓ Token klasik konekte!"})

        elif broker == "binance":
            api=BinanceClient(d["api_key"],d["api_secret"]); bal=api.connect()
            st["binance_api"]=api; st["broker"]="binance"; st["balance"]=bal; st["connected"]=True
            return jsonify({"ok":True,"balance":bal,"broker":"binance"})

        elif broker == "binance_us":
            api=BinanceUSClient(d["api_key"],d["api_secret"]); bal=api.connect()
            st["binance_api"]=api; st["broker"]="binance_us"; st["balance"]=bal; st["connected"]=True
            return jsonify({"ok":True,"balance":bal,"broker":"binance_us"})

        return jsonify({"ok":False,"error":"Broker enkoni"})
    except Exception as e:
        logger.error(f"Connect error: {e}", exc_info=True)
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/start", methods=["POST"])
def api_start():
    st=get_state()
    if not st.get("access"): return jsonify({"ok":False,"error":"⚠ Ou bezwen yon kòd aksè valid!"})
    if not st["connected"]: return jsonify({"ok":False,"error":"Konekte broker anvan!"})
    if st["running"]: return jsonify({"ok":False,"error":"Bot déjà ap kouri"})
    d=request.json or {}
    tf_map={"1m":60,"5m":300,"15m":900,"1h":3600,"4h":14400}
    st["config"]={"broker":st["broker"],"symbol":d.get("symbol","R_100"),"strategy":d.get("strategy","confluence"),
        "lot":d.get("lot",0.5),"tf_secs":tf_map.get(d.get("tf","15m"),900),"min_conf":d.get("min_conf",0.65),
        "profit_target":float(d.get("profit_target",0)),"loss_limit":float(d.get("loss_limit",0)),
        "mode":d.get("mode","forex"),"digit_type":d.get("digit_type","over_under")}
    import random,string
    bot_id=''.join(random.choices(string.ascii_uppercase+string.digits,k=8))
    st["running"]=True; st["bot_id"]=bot_id
    mode=d.get("mode","forex"); broker=st["broker"]
    if mode=="digits": threading.Thread(target=digits_trading_loop,args=(st,bot_id),daemon=True).start(); add_log(st,"🎲 Digits mode démarre","INFO")
    elif broker in ("binance","binance_us"): threading.Thread(target=binance_trading_loop,args=(st,bot_id),daemon=True).start(); add_log(st,f"🪙 Binance mode démarre","INFO")
    else: threading.Thread(target=trading_loop,args=(st,bot_id),daemon=True).start()
    return jsonify({"ok":True})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    st=get_state(); st["running"]=False; st["bot_id"]=None; return jsonify({"ok":True})

@app.route("/api/status")
def api_status():
    st=get_state()
    return jsonify({
        "connected":   st["connected"], "broker": st["broker"], "running": st["running"],
        "balance":     round(st["balance"],2), "pnl": round(st["total_pnl"],2),
        "profit_sent": round(st["profit_sent"],4),
        "trades":      st["trades"][:20], "log": st["log"][:30], "config": st["config"],
        "oauth_ready": bool(st.get("_oauth_token")),
        "pat_ready":   bool(st.get("_pat_token")),
        "account_id":  st.get("_pat_account_id",""),
        "currency":    st.get("_pat_currency","USD"),
    })

@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    st=get_state()
    try:
        d=request.json or {}; symbol=d.get("symbol","R_100"); strat=d.get("strategy","confluence"); candles=[]
        if st.get("deriv_api"): candles=st["deriv_api"].get_candles(symbol,500,3600)
        elif st.get("binance_api"): candles=st["binance_api"].get_candles(symbol,"1h",500)
        if len(candles)<100: return jsonify({"ok":False,"error":f"Pa ase done ({len(candles)}) — konekte broker anvan"})
        r=run_backtest(candles,strat,float(d.get("balance",10000)),float(d.get("lot",0.01)),float(d.get("sl",20)),float(d.get("tp",40)))
        return jsonify({"ok":True,"result":r})
    except Exception as e: return jsonify({"ok":False,"error":str(e)})

@app.route("/api/login", methods=["POST"])
def api_login():
    st=get_state(); d=request.json or {}
    token=d.get("session_token","").strip(); code=d.get("code","").strip().upper()
    if token:
        ok,msg_text=validate_session(token)
        if ok:
            with _sess_lock: is_adm=_sessions.get(token,{}).get("is_admin",False)
            st["access"]=True; st["session_token"]=token; st["is_admin"]=is_adm
            return jsonify({"ok":True,"msg":msg_text,"session_token":token,"is_admin":is_adm})
        else: st["access"]=False; return jsonify({"ok":False,"msg":msg_text,"need_code":True})
    if not code: return jsonify({"ok":False,"msg":"Mete kòd aksè ou a","need_code":True})
    ok,msg_text=check_access(code)
    if ok:
        use_code(code); new_token,expire=create_session()
        is_adm=ACCESS_CODES.get(code,{}).get("is_adm",False) or ACCESS_CODES.get(code,{}).get("created_at") is None
        with _sess_lock: _sessions[new_token]["is_admin"]=is_adm; _save_sessions()
        st["access"]=True; st["session_token"]=new_token; st["is_admin"]=is_adm
        msg_out="✓ Aksè Admin! 30 jou rete" if is_adm else "✓ Aksè akòde! 30 jou rete"
        return jsonify({"ok":True,"msg":msg_out,"session_token":new_token,"expire":expire,"is_admin":is_adm})
    return jsonify({"ok":False,"msg":msg_text,"need_code":True})

def require_admin(d):
    token=d.get("admin_token","").strip()
    if not token: return False
    with _sess_lock: sess=_sessions.get(token)
    if not sess: return False
    return sess.get("is_admin",False)

@app.route("/api/admin/codes", methods=["POST"])
def admin_get_codes():
    d=request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    now=time.time(); codes=[]
    for c,entry in ACCESS_CODES.items():
        if entry["created_at"] is None or entry.get("is_adm"): status="ADM"; remaining="∞"
        elif entry["used"]: status="ITILIZE"; remaining="0"
        else:
            age=now-entry["created_at"]
            if age>CODE_TTL_SECONDS: status="EKSPIRE"; remaining="0"
            else: status="AKTIF"; remaining=str(int((CODE_TTL_SECONDS-age)/86400))+" jou"
        codes.append({"code":c,"status":status,"remaining":remaining,"used":entry["used"],"is_adm":entry.get("is_adm",False) or entry["created_at"] is None})
    today=date.today(); active_sess=sum(1 for s in _sessions.values() if date.fromisoformat(s["expire"])>today)
    return jsonify({"ok":True,"codes":codes,"total_sessions":active_sess})

@app.route("/api/admin/add_code", methods=["POST"])
def admin_add_code():
    d=request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    code=d.get("code","").strip().upper()
    if not code or len(code)<3: return jsonify({"ok":False,"error":"Kòd dwe gen 3+ karaktè"})
    if code in ACCESS_CODES: return jsonify({"ok":False,"error":"Kòd sa deja egziste"})
    is_adm=d.get("is_adm",False)
    ACCESS_CODES[code]={"created_at":None if is_adm else time.time(),"used":False,"is_adm":is_adm}
    return jsonify({"ok":True,"msg":f"✓ Kòd {code} kreye [{'Admin' if is_adm else 'Itilizatè 1 mwa'}]"})

@app.route("/api/admin/revoke_code", methods=["POST"])
def admin_revoke_code():
    d=request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    code=d.get("code","").strip().upper()
    if not code or code not in ACCESS_CODES: return jsonify({"ok":False,"error":"Kòd pa jwenn"})
    if code=="BONHEURWIIN": return jsonify({"ok":False,"error":"Pa ka revoke kòd ADM prensipal"})
    del ACCESS_CODES[code]; return jsonify({"ok":True,"msg":f"✓ Kòd {code} revoke"})

@app.route("/api/admin/reset_code", methods=["POST"])
def admin_reset_code():
    d=request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    code=d.get("code","").strip().upper()
    if code not in ACCESS_CODES: return jsonify({"ok":False,"error":"Kòd pa jwenn"})
    ACCESS_CODES[code]["used"]=False
    if not (ACCESS_CODES[code].get("is_adm") or ACCESS_CODES[code]["created_at"] is None):
        ACCESS_CODES[code]["created_at"]=time.time()
    return jsonify({"ok":True,"msg":f"✓ Kòd {code} reset"})

@app.route("/api/admin/users", methods=["POST"])
def admin_get_users():
    d=request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    users=[]
    with _user_lock:
        for uid,st in _user_states.items():
            users.append({"uid":uid[:8]+"...","connected":st.get("connected",False),"broker":st.get("broker","—"),
                "running":st.get("running",False),"balance":round(st.get("balance",0),2),
                "pnl":round(st.get("total_pnl",0),2),"trades":len(st.get("trades",[])),
                "symbol":st.get("config",{}).get("symbol","—"),"strategy":st.get("config",{}).get("strategy","—"),
                "auth_type": "PAT" if st.get("_pat_token") else ("OAuth2" if st.get("_oauth_token") else "Classic")})
    return jsonify({"ok":True,"users":users,"total":len(users)})

@app.route("/api/admin/stop_user", methods=["POST"])
def admin_stop_user():
    d=request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    uid_prefix=d.get("uid","").replace("...",""); stopped=0
    with _user_lock:
        for uid,st in _user_states.items():
            if uid.startswith(uid_prefix): st["running"]=False; st["bot_id"]=None; stopped+=1
    return jsonify({"ok":True,"msg":f"✓ {stopped} bot(s) kanpe"})

@app.route("/api/admin/sessions", methods=["POST"])
def admin_sessions():
    d=request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    today=date.today(); sessions=[]
    with _sess_lock:
        for token,sess in _sessions.items():
            exp=date.fromisoformat(sess["expire"])
            sessions.append({"token":token[:8]+"...","expire":sess["expire"],"days_left":(exp-today).days,"is_admin":sess.get("is_admin",False),"active":(exp-today).days>0})
    return jsonify({"ok":True,"sessions":sessions,"total":len(sessions)})

@app.route("/api/admin/clean_sessions", methods=["POST"])
def admin_clean_sessions():
    d=request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    today=date.today(); count=0
    with _sess_lock:
        expired=[t for t,s in _sessions.items() if date.fromisoformat(s["expire"])<=today]
        for t in expired: del _sessions[t]; count+=1
        if count: _save_sessions()
    return jsonify({"ok":True,"msg":f"✓ {count} sesyon ekspire efase"})

@app.route("/api/admin/clear_user", methods=["POST"])
def admin_clear_user():
    d=request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    uid_prefix=d.get("uid","").replace("...",""); cleared=0
    with _user_lock:
        for uid,st in _user_states.items():
            if uid.startswith(uid_prefix):
                st["trades"]=[]; st["total_pnl"]=0.0; st["profit_sent"]=0.0; st["log"]=[]; cleared+=1
    return jsonify({"ok":True,"msg":f"✓ {cleared} itilizatè efase"})

@app.route("/api/admin/clear_trades", methods=["POST"])
def admin_clear_trades():
    d=request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    uid_prefix=d.get("uid","").replace("...",""); cleared=0
    with _user_lock:
        for uid,st in _user_states.items():
            if uid.startswith(uid_prefix): st["trades"]=[]; cleared+=1
    return jsonify({"ok":True,"msg":f"✓ {cleared} itilizatè: trades efase"})

@app.route("/")
def index(): return render_template_string(HTML)

# ═══════════════════════════════════════════════════════════
# HTML INTERFACE
# ═══════════════════════════════════════════════════════════
HTML=r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>💰 BonheurBot v6 ELITE</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700;900&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{background:#040A0F;color:#C8E8F0;font-family:'JetBrains Mono',monospace;font-size:13px}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-thumb{background:#0D2233}
.hdr{background:#071219;border-bottom:1px solid #0D2233;padding:0 20px;display:flex;align-items:center;justify-content:space-between;height:54px;position:sticky;top:0;z-index:99}
.logo{font-size:17px;font-weight:900;letter-spacing:2px;color:#00FF88}
.logo span{color:#C8E8F0}
.tabs{background:#071219;border-bottom:1px solid #0D2233;padding:0 20px;display:flex;overflow-x:auto}
.tab{background:transparent;border:none;border-bottom:2px solid transparent;color:#4A7080;padding:12px 16px;cursor:pointer;font-family:inherit;font-size:11px;letter-spacing:2px;font-weight:700;white-space:nowrap;transition:.2s}
.tab.on{color:#00FF88;border-bottom-color:#00FF88}
.wrap{max-width:1200px;margin:0 auto;padding:18px 20px}
.pg{display:none}.pg.on{display:block}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.stat{background:#020C12;border:1px solid #0D2233;border-radius:8px;padding:12px 14px;flex:1;min-width:110px}
.sl{color:#4A7080;font-size:10px;letter-spacing:1px;margin-bottom:5px}
.sv{font-size:21px;font-weight:700}
.box{background:#071219;border:1px solid #0D2233;border-radius:10px;padding:16px;margin-bottom:14px}
.bt{color:#00FF88;font-size:10px;letter-spacing:2px;font-weight:700;margin-bottom:12px}
.iw{margin-bottom:10px}
.il{color:#4A7080;font-size:10px;letter-spacing:1px;margin-bottom:4px}
input,select{width:100%;background:#020C12;border:1px solid #0D2233;color:#C8E8F0;border-radius:6px;padding:8px 10px;font-size:12px;font-family:inherit;outline:none}
input:focus,select:focus{border-color:#00FF88}
select option{background:#071219}
.btn{background:transparent;border:1px solid #00FF88;color:#00FF88;border-radius:6px;padding:9px 22px;cursor:pointer;font-size:12px;font-family:inherit;letter-spacing:1px;font-weight:700;transition:.15s}
.btn:hover{background:#00FF8822}
.btn.b{border-color:#00D4FF;color:#00D4FF}.btn.b:hover{background:#00D4FF22}
.btn.r{border-color:#FF3B6B;color:#FF3B6B}.btn.r:hover{background:#FF3B6B22}
.btn.y{border-color:#FFD600;color:#FFD600}.btn.y:hover{background:#FFD60022}
.btn.p{border-color:#A855F7;color:#A855F7}.btn.p:hover{background:#A855F722}
.btn.fw{width:100%}
.al{padding:8px 12px;border-radius:6px;font-size:11px;margin-bottom:10px;line-height:1.5;white-space:pre-wrap}
.al.ok{background:#00FF8815;color:#00FF88;border:1px solid #00FF8833}
.al.er{background:#FF3B6B15;color:#FF3B6B;border:1px solid #FF3B6B33}
.al.in{background:#00D4FF15;color:#00D4FF;border:1px solid #00D4FF33}
.al.wa{background:#FFD60015;color:#FFD600;border:1px solid #FFD60033}
.tag{border-radius:4px;padding:2px 8px;font-size:11px;font-weight:700}
.tg{background:#4A708022;border:1px solid #4A708044;color:#4A7080}
.tb{background:#00FF8822;border:1px solid #00FF8844;color:#00FF88}
.ts{background:#FF3B6B22;border:1px solid #FF3B6B44;color:#FF3B6B}
table{width:100%;border-collapse:collapse;font-size:12px}
th{padding:7px 10px;text-align:left;border-bottom:1px solid #0D2233;color:#4A7080;font-size:10px;letter-spacing:1px}
td{padding:7px 10px;border-bottom:1px solid #0D223320}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
.dl{background:#00FF88;box-shadow:0 0 8px #00FF88}
.di{background:#3A6070}
.le{padding:5px 8px;border-bottom:1px solid #0D223318;font-size:11px}
.lt{color:#4A7080;margin-right:8px}
.lS{color:#00FF88}.lP{color:#FFD600}.lE{color:#FF3B6B}.lW{color:#FFD600}.lI{color:#C8E8F0}
</style>
</head>
<body>
<div id="login-page" style="display:none;min-height:100vh;background:#040A0F;align-items:center;justify-content:center;flex-direction:column">
  <div style="background:#071219;border:1px solid #0D2233;border-radius:12px;padding:40px;max-width:420px;width:90%;text-align:center">
    <div style="font-size:32px;margin-bottom:8px">💰</div>
    <div style="font-size:20px;font-weight:900;color:#00FF88;letter-spacing:2px;margin-bottom:4px">BonheurBot Pro</div>
    <div style="color:#4A7080;font-size:11px;margin-bottom:24px">Trading Bot Pwofesyonèl v6 ELITE</div>
    <div style="margin-bottom:16px">
      <div style="color:#4A7080;font-size:10px;letter-spacing:1px;margin-bottom:6px;text-align:left">KÒD AKSÈ</div>
      <input id="login-code" type="text" placeholder="BB-XXXX-XXXX" style="width:100%;background:#020C12;border:1px solid #0D2233;color:#C8E8F0;border-radius:6px;padding:10px 12px;font-size:13px;font-family:inherit;outline:none;box-sizing:border-box;text-transform:uppercase">
    </div>
    <div id="login-err"></div>
    <button id="login-btn" onclick="doLogin()" style="width:100%;background:#00FF8818;border:1px solid #00FF88;color:#00FF88;border-radius:6px;padding:11px;cursor:pointer;font-size:13px;font-family:inherit;font-weight:700;letter-spacing:1px">⚡ ANTRE</button>
    <div style="margin-top:20px;background:#020C12;border:1px solid #0D2233;border-radius:8px;padding:14px;text-align:left">
      <div style="color:#FFD600;font-size:10px;letter-spacing:1px;font-weight:700;margin-bottom:8px">💳 ABÒNMAN — $40 USDT/MWA</div>
      <div style="color:#4A7080;font-size:10px;line-height:1.9">
        1. Voye <span style="color:#00FF88;font-weight:700">$40 USDT</span> sou adrès sa:<br>
        <span style="color:#C8E8F0;font-size:9px;word-break:break-all;background:#071219;padding:4px 6px;border-radius:4px;display:block;margin:4px 0">0x2ba88a4d6cabaded5d06c75ef3b3efec386acaef</span>
        <span style="color:#FFD600;font-size:9px">⚠ Rezo: BEP20 (BSC) sèlman</span><br><br>
        2. Voye prèv peman sou WhatsApp:<br>
        <a href="https://wa.me/50942867885" target="_blank" style="display:inline-flex;align-items:center;gap:6px;margin-top:6px;background:#25D36618;border:1px solid #25D36644;color:#25D366;border-radius:6px;padding:6px 12px;text-decoration:none;font-size:11px;font-weight:700">
          📱 WhatsApp: +509 4286-7885
        </a>
      </div>
    </div>
  </div>
</div>

<div id="app-page" style="display:none">
<div class="hdr">
  <div style="display:flex;align-items:center;gap:12px">
    <div class="logo">💰 Bonheur<span>Bot</span> <span style="font-size:10px;color:#FFD600">ELITE v6</span></div>
    <div style="width:1px;height:20px;background:#0D2233"></div>
    <span id="hb" class="tag tg">DISCONNECTED</span>
    <span id="h-acct" style="color:#4A7080;font-size:10px;display:none"></span>
  </div>
  <div style="display:flex;align-items:center;gap:16px">
    <span><span class="dot di" id="dot"></span><span id="hs" style="color:#3A6070;font-size:11px;letter-spacing:1px">IDLE</span></span>
    <span id="hbal" style="color:#3A6070;font-weight:700;font-size:15px">$0.00</span>
    <span id="sub-info" style="color:#00FF8888;font-size:10px"></span>
    <button onclick="doLogout()" style="background:transparent;border:1px solid #3A6070;color:#3A6070;border-radius:4px;padding:3px 8px;cursor:pointer;font-size:10px;font-family:inherit">DEKONEKTE</button>
  </div>
</div>

<div class="tabs">
  <button class="tab on" onclick="sw('dashboard',this)">DASHBOARD</button>
  <button class="tab" onclick="sw('control',this)">KONTWÒL</button>
  <button class="tab" onclick="sw('strategies',this)">STRATEGIES</button>
  <button class="tab" onclick="sw('backtest',this)">BACKTEST</button>
  <button class="tab" onclick="sw('trades',this)">TRADES</button>
  <button class="tab" onclick="sw('log',this)">LOGS</button>
  <button class="tab" id="tab-admin" style="display:none;color:#FFD600" onclick="sw('admin',this)">⚙ ADMIN</button>
</div>

<div class="wrap">

<div id="pg-dashboard" class="pg on">
  <div class="stats">
    <div class="stat"><div class="sl">BALANS</div><div class="sv" id="s-bal" style="color:#00D4FF">$0.00</div></div>
    <div class="stat"><div class="sl">NET P&L</div><div class="sv" id="s-pnl">+$0.00</div></div>
    <div class="stat"><div class="sl">PROFIT VOYE</div><div class="sv" id="s-sent" style="color:#FFD600">$0.00</div></div>
    <div class="stat"><div class="sl">TRADES</div><div class="sv" id="s-tr" style="color:#FFD600">0</div></div>
    <div class="stat"><div class="sl">BOT</div><div class="sv" id="s-bot" style="color:#3A6070">IDLE</div></div>
  </div>
  <div class="g2">
    <div class="box">
      <div class="bt">KONEKSYON BROKER</div>
      <div class="iw"><div class="il">BROKER</div>
        <select id="d-br" onchange="tog()">
          <option value="deriv">Deriv (Synthetic/Digits)</option>
          <option value="binance">Binance Global (Crypto/Gold)</option>
          <option value="binance_us">Binance US (Crypto/Gold)</option>
        </select>
      </div>

      <!-- ══ DERIV FIELDS ══ -->
      <div id="fd">
        <div class="iw">
          <div class="il">MOD KONEKSYON DERIV</div>
          <select id="d-conn-mode" onchange="togConnMode()">
            <option value="pat">🔑 PAT Token (pat_...) — NOUVO REKÒMANDE</option>
            <option value="oauth2">🔐 OAuth2 Deriv (PKCE + OTP)</option>
            <option value="classic">🗝 Token Klasik (ansyen)</option>
          </select>
        </div>

        <!-- ══ PAT TOKEN PANEL (DEFAULT) ══ -->
        <div id="d-pat-panel">
          <div style="background:#00D4FF12;border:1px solid #00D4FF33;border-radius:8px;padding:12px;margin-bottom:10px">
            <div style="color:#00D4FF;font-weight:700;font-size:11px;margin-bottom:6px">🔑 PAT — Personal Access Token</div>
            <div style="color:#4A7080;font-size:10px;line-height:1.8">
              Kreye token sou: <span style="color:#C8E8F0">app.deriv.com → Settings → API Token</span><br>
              Pèmisyon rekèri: <span style="color:#00FF88">Read • Trade • Payments</span><br>
              Fòma: <span style="color:#FFD600">pat_XXXXXXXXXXXXXXXXXXXXXXXXXX</span>
            </div>
          </div>
          <div class="iw">
            <div class="il">TOKEN PAT DERIV</div>
            <div style="display:flex;gap:8px">
              <input id="d-pat-input" type="password" placeholder="pat_XXXXXXXXXXXXXXXXXXXXXXXXXX" style="flex:1">
              <button class="btn b" onclick="doPATVerify()" style="padding:8px 14px;font-size:11px;white-space:nowrap">✓ TEST</button>
            </div>
          </div>
          <div id="d-pat-verify-result" style="display:none;margin-bottom:10px"></div>
          <!-- PAT account info card -->
          <div id="d-pat-info" style="display:none;background:#00FF8810;border:1px solid #00FF8830;border-radius:6px;padding:10px;margin-bottom:10px;font-size:11px">
            <div style="color:#00FF88;font-weight:700;margin-bottom:4px">✅ Kont Deriv verifye</div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;color:#C8E8F0">
              <span>Kont ID: <strong id="pi-id">—</strong></span>
              <span>Balans: <strong id="pi-bal" style="color:#00FF88">—</strong></span>
              <span>Devise: <strong id="pi-cur">—</strong></span>
              <span>Status: <strong id="pi-stat" style="color:#00FF88">—</strong></span>
            </div>
          </div>
        </div>

        <!-- ══ OAuth2 PANEL ══ -->
        <div id="d-oauth2-panel" style="display:none">
          <div id="d-oauth-status" style="background:#A855F715;border:1px solid #A855F730;border-radius:6px;padding:10px;margin-bottom:10px;font-size:11px;color:#A855F7;line-height:1.8">
            🔐 <strong>OAuth2 + PKCE + OTP WebSocket</strong><br>
            <span style="color:#4A7080">Nouvo sistèm Deriv ofisyèl.<br>
            Klike bouton la anba, login nan Deriv, tounen otomatik.</span>
          </div>
          <button class="btn p fw" onclick="doOAuth2()">🔐 KONEKTE VIA DERIV OAUTH2</button>
          <div id="d-oauth-ready" style="display:none;margin-top:8px" class="al ok">✅ OAuth2 aktif! Klike ⚡ KONEKTE pou fini.</div>
        </div>

        <!-- ══ CLASSIC PANEL ══ -->
        <div id="d-classic-panel" style="display:none">
          <div style="background:#FFD60010;border:1px solid #FFD60030;border-radius:6px;padding:8px;margin-bottom:8px;font-size:10px;color:#4A7080;line-height:1.7">
            🗝 Token klasik — Kreye nan: <span style="color:#C8E8F0">app.deriv.com → foto → API Token</span><br>
            Pa kòmanse ak <span style="color:#FF3B6B">pat_</span> — fòma alphanumerik ~15 karaktè
          </div>
          <div class="iw"><div class="il">API TOKEN DERIV (Klasik)</div>
            <input id="d-tk" type="password" placeholder="Kole token klasik ou a isit">
          </div>
          <div class="iw"><div class="il">APP ID</div><input id="d-ai" value="1089"></div>
        </div>
      </div>

      <!-- ══ BINANCE FIELDS ══ -->
      <div id="fb" style="display:none">
        <div class="iw"><div class="il">API KEY</div><input id="b-k" type="password"></div>
        <div class="iw"><div class="il">API SECRET</div><input id="b-s" type="password"></div>
        <div id="fb-note" style="display:none;background:#FFD60010;border:1px solid #FFD60033;border-radius:6px;padding:8px;margin-bottom:8px;font-size:10px;color:#FFD600">
          🇺🇸 Binance US — api.binance.us | Kreye kle sou: binance.us
        </div>
      </div>

      <div id="cm" style="margin-top:8px"></div>
      <button class="btn b fw" id="conn-btn" onclick="doConn()" style="margin-top:6px">⚡ KONEKTE</button>
      <div id="cs" style="margin-top:10px"></div>
    </div>
    <div class="box">
      <div class="bt" style="display:flex;justify-content:space-between">
        <span>COURBE P&L</span>
        <span id="s-pnl2" style="color:#00FF88;font-size:13px;font-weight:700">+$0.00</span>
      </div>
      <svg id="chart" viewBox="0 0 500 120" style="width:100%;height:120px">
        <text x="250" y="65" text-anchor="middle" fill="#3A6070" font-size="12" font-family="monospace">Pa gen trades ankò</text>
      </svg>
      <div style="display:flex;gap:10px;margin-top:12px">
        <div class="stat"><div class="sl">STRATEGY</div><div id="s-strat" style="color:#FFD600;font-size:12px;font-weight:700">—</div></div>
        <div class="stat"><div class="sl">SENBOL</div><div id="s-sym" style="font-size:12px;font-weight:700">—</div></div>
        <div class="stat"><div class="sl">BROKER</div><div id="s-br2" style="font-size:12px;font-weight:700;color:#3A6070">—</div></div>
      </div>
    </div>
  </div>
  <div class="box" style="background:#00D4FF08;border-color:#00D4FF22">
    <div class="bt" style="color:#00D4FF">🔑 PAT TOKEN — NOUVO SISTÈM DERIV OFISYÈL</div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;font-size:11px;color:#4A7080;line-height:1.9">
      <div>
        <div style="color:#00D4FF;font-weight:700;margin-bottom:4px">🔑 PAT Auth</div>
        REST API v1 Bearer<br>pat_... token format<br>
        <span style="color:#00D4FF">Otomatik REST → WS</span><br>
        Pa bezwen app_id<br><span style="color:#00D4FF">→ Pi senp, pi solid</span>
      </div>
      <div>
        <div style="color:#00FF88;font-weight:700;margin-bottom:4px">📈 SuperTrend</div>
        ATR x3.0 multiplier<br>Siyal klè BUY/SELL<br>
        <span style="color:#00FF88">Pwa: 2.5x (pi wo)</span><br>
        Travèse = siyal solid<br><span style="color:#00FF88">→ Pi reliable</span>
      </div>
      <div>
        <div style="color:#FFD600;font-weight:700;margin-bottom:4px">🕯 Heikin Ashi</div>
        5 bouji konsekitif<br>Filtre bwi mache<br>
        <span style="color:#FFD600">Pwa: 2.5x</span><br>
        Grandi = plis konfidans<br><span style="color:#FFD600">→ Trend pi klè</span>
      </div>
      <div>
        <div style="color:#FF3B6B;font-weight:700;margin-bottom:4px">🛡 ADX 12 + 3 strat</div>
        ADX sèyil: 12<br>3 strategies minimòm<br>
        RANGING oke si ST+HA<br>Pause 3 pèt<br>
        <span style="color:#FF3B6B">→ Plis siyal</span>
      </div>
    </div>
  </div>
</div>

<div id="pg-control" class="pg">
  <div class="g2">
    <div class="box">
      <div class="bt">PARAMÈT BOT ELITE v6</div>
      <div class="iw"><div class="il">MOD TRADING</div>
        <select id="c-mode" onchange="toggleMode()">
          <option value="forex">📈 Rise/Fall — Deriv Synthetic</option>
          <option value="digits">Digits Over/Under — Deriv</option>
          <option value="binance_gold">🥇 XAU/USD + Metals — Binance</option>
          <option value="binance_crypto">🪙 Crypto USDT — Binance</option>
        </select>
      </div>
      <div id="opts-forex">
        <div class="g2">
          <div class="iw"><div class="il">SENBOL DERIV</div>
            <select id="c-sy-deriv">
              <option value="R_10">R_10 — Volatility 10</option>
              <option value="R_25">R_25 — Volatility 25</option>
              <option value="R_50">R_50 — Volatility 50</option>
              <option value="R_75">R_75 — Volatility 75</option>
              <option value="R_100" selected>R_100 — Volatility 100</option>
            </select>
          </div>
          <div class="iw"><div class="il">TIMEFRAME</div>
            <select id="c-tf">
              <option value="1m">1 minit</option>
              <option value="5m">5 minit ★</option>
              <option value="15m" selected>15 minit ★★★</option>
              <option value="1h">1 è ★★★</option>
              <option value="4h">4 è</option>
            </select>
          </div>
        </div>
        <div class="g2">
          <div class="iw"><div class="il">MISE ($) — Min $0.50</div><input id="c-lot-forex" type="number" value="0.50" step="0.50" min="0.50"></div>
          <div class="iw"><div class="il">STRATEGY</div>
            <select id="c-st-forex">
              <option value="confluence">🔥 Confluence ELITE (ST+HA+CE)</option>
              <option value="deriv_pro">🚀 Deriv Pro ELITE (score+ST)</option>
              <option value="supertrend">📈 SuperTrend Sèl</option>
              <option value="heikin_ashi">🕯 Heikin Ashi Sèl</option>
              <option value="chandelier">🔔 Chandelier Exit Sèl</option>
              <option value="ai">🤖 AI Score</option>
              <option value="smc">🏛 Smart Money</option>
              <option value="scalping_pro">⚡ Scalping Pro</option>
              <option value="ema">📊 EMA Classic</option>
              <option value="rsi">📉 RSI Classic</option>
            </select>
          </div>
        </div>
      </div>
      <div id="opts-digits" style="display:none">
        <div style="background:#FFD60010;border:1px solid #FFD60033;border-radius:6px;padding:12px;margin-bottom:10px">
          <div style="color:#FFD600;font-size:11px;font-weight:700;margin-bottom:8px">DIGITS MODE</div>
          <div class="g2">
            <div class="iw"><div class="il">SENBOL</div>
              <select id="c-sy-digits">
                <option value="R_10" selected>R_10 — Volatility 10</option>
                <option value="R_25">R_25 — Volatility 25</option>
                <option value="R_50">R_50 — Volatility 50</option>
              </select>
            </div>
            <div class="iw"><div class="il">TIP DIGITS</div>
              <select id="c-digit-type">
                <option value="over_under">Over 4 / Under 5</option>
                <option value="even_odd">Even / Odd</option>
              </select>
            </div>
          </div>
          <div class="iw"><div class="il">MISE ($) — Min $0.35</div><input id="c-lot-digits" type="number" value="0.35" step="0.10" min="0.35"></div>
        </div>
      </div>
      <div id="opts-gold" style="display:none">
        <div style="background:#FFD60010;border:1px solid #FFD60033;border-radius:6px;padding:12px;margin-bottom:10px">
          <div style="color:#FFD600;font-size:11px;font-weight:700;margin-bottom:8px">🥇 GOLD / METALS — BINANCE</div>
          <div class="g2">
            <div class="iw"><div class="il">SENBOL METAL</div>
              <select id="c-sy-gold">
                <option value="XAUUSDT">XAUUSDT — Or (Gold)</option>
                <option value="XAGUSDT">XAGUSDT — Ajan (Silver)</option>
              </select>
            </div>
            <div class="iw"><div class="il">TIMEFRAME</div>
              <select id="c-tf-gold">
                <option value="5m">5 minit</option>
                <option value="15m" selected>15 minit</option>
                <option value="1h">1 è</option>
              </select>
            </div>
          </div>
          <div class="iw"><div class="il">MISE USDT — Min $11</div><input id="c-lot-gold" type="number" value="11" step="1" min="11"></div>
        </div>
      </div>
      <div id="opts-crypto" style="display:none">
        <div style="background:#00D4FF10;border:1px solid #00D4FF33;border-radius:6px;padding:12px;margin-bottom:10px">
          <div style="color:#00D4FF;font-size:11px;font-weight:700;margin-bottom:8px">🪙 CRYPTO — BINANCE</div>
          <div class="g2">
            <div class="iw"><div class="il">SENBOL KRIPTO</div>
              <select id="c-sy-crypto">
                <option value="BTCUSDT" selected>BTCUSDT — Bitcoin</option>
                <option value="ETHUSDT">ETHUSDT — Ethereum</option>
                <option value="BNBUSDT">BNBUSDT — BNB</option>
                <option value="SOLUSDT">SOLUSDT — Solana</option>
                <option value="XRPUSDT">XRPUSDT — XRP</option>
                <option value="ADAUSDT">ADAUSDT — Cardano</option>
                <option value="AVAXUSDT">AVAXUSDT — Avalanche</option>
                <option value="DOGEUSDT">DOGEUSDT — Dogecoin</option>
              </select>
            </div>
            <div class="iw"><div class="il">TIMEFRAME</div>
              <select id="c-tf-crypto">
                <option value="5m">5 minit</option>
                <option value="15m" selected>15 minit</option>
                <option value="1h">1 è</option>
                <option value="4h">4 è</option>
              </select>
            </div>
          </div>
          <div class="iw"><div class="il">MISE USDT — Min $11</div><input id="c-lot-crypto" type="number" value="11" step="1" min="11"></div>
        </div>
      </div>
      <div class="g2">
        <div class="iw"><div class="il">KONFIDANS MIN</div>
          <select id="c-conf">
            <option value="0.60">60% (maksimòm siyal)</option>
            <option value="0.65" selected>65% (rekòmande ★)</option>
            <option value="0.70">70% (balans)</option>
            <option value="0.75">75% (konsèvatif)</option>
            <option value="0.80">80% (presiz)</option>
          </select>
        </div>
        <div class="iw"><div class="il">🎯 OBJEKTIF PROFIT ($)</div><input id="c-target" type="number" value="0" step="1" min="0"><div style="color:#00FF88;font-size:9px;margin-top:2px">0 = pa gen limit</div></div>
      </div>
      <div class="iw"><div class="il">🛑 LIMIT PÈT ($)</div><input id="c-loss" type="number" value="0" step="1" min="0"><div style="color:#FF3B6B;font-size:9px;margin-top:2px">REKÒMANDE: toujou mete yon limit pèt!</div></div>
      <div id="ctm"></div>
      <div style="display:flex;gap:10px">
        <button class="btn" id="bs" onclick="doStart()">▶ START BOT</button>
        <button class="btn r" id="bx" onclick="doStop()" style="display:none">■ STOP BOT</button>
      </div>
    </div>
    <div>
      <div class="box">
        <div class="bt">ESTATI LIVE</div>
        <div class="stats">
          <div class="stat"><div class="sl">BOT</div><div id="c-st2" class="sv" style="color:#3A6070">IDLE</div></div>
          <div class="stat"><div class="sl">BALANS</div><div id="c-bal" class="sv" style="color:#00D4FF">$0.00</div></div>
        </div>
        <div class="stats">
          <div class="stat"><div class="sl">P&L NET</div><div id="c-pnl" class="sv">+$0.00</div></div>
          <div class="stat"><div class="sl">PROFIT 5%</div><div id="c-sent" class="sv" style="color:#FFD600">$0.00</div></div>
        </div>
        <div id="c-acct-info" style="display:none;background:#00D4FF10;border:1px solid #00D4FF30;border-radius:6px;padding:8px;font-size:11px;color:#00D4FF">
          Kont: <strong id="c-acct-id">—</strong> | Devise: <strong id="c-currency">USD</strong>
        </div>
      </div>
      <div class="box" style="background:#00D4FF08;border-color:#00D4FF22">
        <div class="bt" style="color:#00D4FF">🔑 PAT — LOJIK KONEKSYON</div>
        <div style="color:#4A7080;font-size:10px;line-height:2.1">
          <span style="color:#00D4FF">✓ REST API:</span> Bearer pat_... auth<br>
          <span style="color:#00D4FF">✓ Verifikasyon:</span> Balance + Account ID<br>
          <span style="color:#00FF88">✓ WS Trading:</span> pat_ kòm authorize token<br>
          <span style="color:#FFD600">⚠ 1-2 pèt:</span> Conf+2-4%, mise monte<br>
          <span style="color:#FF3B6B">🛑 3 pèt:</span> PÒZE — tann siyal bon<br>
          <span style="color:#00D4FF">✓ RANGING:</span> Trade si ST+HA dakò
        </div>
      </div>
    </div>
  </div>
</div>

<div id="pg-strategies" class="pg">
  <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px" id="sbts"></div>
  <div class="box" id="sdet"></div>
</div>

<div id="pg-backtest" class="pg">
  <div class="box">
    <div class="bt">BACKTEST ENGINE</div>
    <div class="g3">
      <div class="iw"><div class="il">SENBOL</div><input id="bt-sy" value="R_100"></div>
      <div class="iw"><div class="il">BALANS ($)</div><input id="bt-bl" type="number" value="10000"></div>
      <div class="iw"><div class="il">LOT SIZE</div><input id="bt-lt" type="number" value="0.50" step="0.10"></div>
      <div class="iw"><div class="il">STOP LOSS</div><input id="bt-sl" type="number" value="20"></div>
      <div class="iw"><div class="il">TAKE PROFIT</div><input id="bt-tp" type="number" value="40"></div>
    </div>
    <div class="iw"><div class="il">STRATEGY</div>
      <select id="bt-st">
        <option value="confluence">🔥 Confluence ELITE</option>
        <option value="deriv_pro">🚀 Deriv Pro ELITE</option>
        <option value="supertrend">📈 SuperTrend</option>
        <option value="heikin_ashi">🕯 Heikin Ashi</option>
        <option value="chandelier">🔔 Chandelier Exit</option>
        <option value="ai">🤖 AI Score</option>
        <option value="binance_gold">🥇 Gold Strategy</option>
        <option value="binance_crypto">🪙 Crypto Strategy</option>
        <option value="smc">🏛 SMC</option>
        <option value="macd_bollinger">📊 MACD+BB</option>
        <option value="rsi">📉 RSI</option>
      </select>
    </div>
    <div id="btm"></div>
    <button class="btn y" onclick="doBt()">▶ KÒMANSE BACKTEST</button>
    <div id="btr" style="margin-top:16px"></div>
  </div>
</div>

<div id="pg-trades" class="pg">
  <div class="box">
    <div class="bt" id="trtit">HISTOIRIK TRADES</div>
    <div id="trtbl"><div style="color:#3A6070;text-align:center;padding:40px">Pa gen trades ankò</div></div>
  </div>
</div>

<div id="pg-log" class="pg">
  <div class="box">
    <div class="bt">LOGS SISTEM</div>
    <div id="logs"></div>
  </div>
</div>

<div id="pg-admin" class="pg">
  <div class="stats">
    <div class="stat"><div class="sl">KÒD TOTAL</div><div class="sv" id="adm-total" style="color:#FFD600">—</div></div>
    <div class="stat"><div class="sl">KÒD AKTIF</div><div class="sv" id="adm-aktif" style="color:#00FF88">—</div></div>
    <div class="stat"><div class="sl">ITILIZE</div><div class="sv" id="adm-used" style="color:#FF3B6B">—</div></div>
    <div class="stat"><div class="sl">SESYON AKTIF</div><div class="sv" id="adm-sess" style="color:#00D4FF">—</div></div>
    <div class="stat"><div class="sl">ITILIZATÈ</div><div class="sv" id="adm-users-count" style="color:#FFD600">—</div></div>
  </div>
  <div class="g2">
    <div class="box">
      <div class="bt">➕ KREYE KÒD AKSÈ</div>
      <div class="iw"><div class="il">KÒD</div>
        <input id="new-code" type="text" placeholder="BB-2025-XXXX" oninput="this.value=this.value.toUpperCase()">
      </div>
      <div class="iw"><div class="il">TIP KÒD</div>
        <select id="new-code-type">
          <option value="user">👤 Itilizatè — 1 mwa</option>
          <option value="adm">👑 Admin — pa janm ekspire</option>
        </select>
      </div>
      <button class="btn fw" onclick="admAddCode()">➕ KREYE KÒD</button>
      <div id="add-code-msg" style="margin-top:8px"></div>
      <div style="margin-top:14px;padding-top:12px;border-top:1px solid #0D2233">
        <div class="bt" style="margin-bottom:8px">⚡ GENERATÈ RAPID</div>
        <div style="display:flex;gap:8px">
          <button class="btn b" style="padding:5px 14px;font-size:11px" onclick="genCode(6)">6 kar</button>
          <button class="btn b" style="padding:5px 14px;font-size:11px" onclick="genCode(8)">8 kar</button>
          <button class="btn b" style="padding:5px 14px;font-size:11px" onclick="genCode(10)">10 kar</button>
        </div>
        <div id="gen-result" style="margin-top:10px;font-size:14px;font-weight:700;color:#00FF88;letter-spacing:2px"></div>
        <button id="gen-copy-btn" style="display:none;margin-top:6px;background:transparent;border:1px solid #00FF8844;color:#00FF88;border-radius:4px;padding:4px 10px;cursor:pointer;font-size:10px;font-family:inherit" onclick="admCopyGen()">📋 KOPYE + AJOUTE</button>
      </div>
    </div>
    <div class="box">
      <div class="bt" style="display:flex;justify-content:space-between">
        <span>🔐 SESYON AKTIF</span>
        <div style="display:flex;gap:6px">
          <button class="btn b" style="padding:3px 10px;font-size:10px" onclick="admRefresh()">🔄</button>
          <button class="btn r" style="padding:3px 10px;font-size:10px" onclick="admCleanSessions()">🗑</button>
        </div>
      </div>
      <div id="adm-sessions-list" style="color:#4A7080;font-size:11px;max-height:200px;overflow-y:auto">Klike 🔄</div>
    </div>
  </div>
  <div class="box">
    <div class="bt" style="display:flex;justify-content:space-between;align-items:center">
      <span>📋 TOUT KÒD AKSÈ</span>
      <button class="btn b" style="padding:4px 12px;font-size:10px" onclick="admRefresh()">🔄 REFRESH</button>
    </div>
    <div id="adm-codes-list"><div style="color:#3A6070;text-align:center;padding:20px">Klike REFRESH</div></div>
  </div>
  <div class="box">
    <div class="bt" style="display:flex;justify-content:space-between;align-items:center">
      <span>👥 ITILIZATÈ AKTIF</span>
      <button class="btn b" style="padding:4px 12px;font-size:10px" onclick="admRefresh()">🔄 REFRESH</button>
    </div>
    <div id="adm-users-list"><div style="color:#3A6070;text-align:center;padding:20px">Klike REFRESH</div></div>
  </div>
</div>

</div>
</div>

<script>
const SESSION_KEY="bb_session_v6";
function saveToken(t){try{localStorage.setItem(SESSION_KEY,t);}catch(e){}try{sessionStorage.setItem(SESSION_KEY,t);}catch(e){}try{const exp=new Date();exp.setDate(exp.getDate()+30);document.cookie=`${SESSION_KEY}=${t};expires=${exp.toUTCString()};path=/;SameSite=Lax`;}catch(e){}}
function getStoredToken(){try{const t=localStorage.getItem(SESSION_KEY);if(t)return t;}catch(e){}try{const t=sessionStorage.getItem(SESSION_KEY);if(t)return t;}catch(e){}try{const m=document.cookie.match(new RegExp("(^| )"+SESSION_KEY+"=([^;]+)"));if(m)return m[2];}catch(e){}return "";}
function clearToken(){try{localStorage.removeItem(SESSION_KEY);}catch(e){}try{sessionStorage.removeItem(SESSION_KEY);}catch(e){}try{document.cookie=`${SESSION_KEY}=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/;`;}catch(e){}}
function updateAdminTab(isAdmin){const tab=document.getElementById("tab-admin");if(tab)tab.style.display=isAdmin?"block":"none";}

async function checkLogin(){
  const token=getStoredToken();
  if(!token){showLogin("");return;}
  try{
    const r=await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({session_token:token,code:""})});
    const d=await r.json();
    if(d.ok){if(d.session_token)saveToken(d.session_token);updateAdminTab(d.is_admin||false);showApp(d.msg);poll();if(d.is_admin)setTimeout(()=>admRefresh(),500);}
    else{if(d.msg&&d.msg.includes("ekspire"))clearToken();showLogin(d.msg||"");}
  }catch(e){showLogin("");}
}

function showLogin(err=""){
  document.getElementById("login-page").style.display="flex";
  document.getElementById("app-page").style.display="none";
  if(err&&err!=="Pa gen sesyon"&&err!=="Mete kòd aksè ou a")
    document.getElementById("login-err").innerHTML=`<div class="al er">⚠ ${err}</div>`;
}
function showApp(msg){
  document.getElementById("login-page").style.display="none";
  document.getElementById("app-page").style.display="block";
  document.getElementById("sub-info").textContent=msg||"";
}

async function doLogin(){
  const code=document.getElementById("login-code").value.trim().toUpperCase();
  if(!code){document.getElementById("login-err").innerHTML='<div class="al er">⚠ Mete kòd aksè ou</div>';return;}
  const btn=document.getElementById("login-btn");btn.textContent="AP VERIFYE...";btn.disabled=true;
  try{
    const r=await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code,session_token:""})});
    const d=await r.json();
    if(d.ok&&d.session_token){saveToken(d.session_token);updateAdminTab(d.is_admin||false);showApp(d.msg);poll();if(d.is_admin)setTimeout(()=>admRefresh(),500);}
    else{document.getElementById("login-err").innerHTML=`<div class="al er">✗ ${d.msg}</div>`;}
  }catch(e){document.getElementById("login-err").innerHTML=`<div class="al er">✗ Erè: ${e.message}</div>`;}
  btn.textContent="⚡ ANTRE";btn.disabled=false;
}
function doLogout(){clearToken();showLogin("Ou dekonekte.");}

// ══ PAT Token Verify ══
async function doPATVerify(){
  const pat=document.getElementById("d-pat-input").value.trim();
  const resEl=document.getElementById("d-pat-verify-result");
  const infoEl=document.getElementById("d-pat-info");
  if(!pat){resEl.style.display="block";resEl.innerHTML='<div class="al er">⚠ Mete token PAT ou a anvan</div>';return;}
  if(!pat.startsWith("pat_")){resEl.style.display="block";resEl.innerHTML='<div class="al er">❌ Token dwe kòmanse avèk pat_</div>';infoEl.style.display="none";return;}
  resEl.style.display="block";resEl.innerHTML='<div class="al in">⏳ Ap verifye token PAT via Deriv REST API...</div>';
  try{
    const r=await fetch("/api/pat_verify",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({pat_token:pat})});
    const d=await r.json();
    if(d.ok){
      resEl.innerHTML=`<div class="al ok">✅ Token PAT valid! Kont: ${d.account_id} | ${d.balance.toFixed(2)} ${d.currency}</div>`;
      document.getElementById("pi-id").textContent=d.account_id||"—";
      document.getElementById("pi-bal").textContent=`${d.balance.toFixed(2)} ${d.currency}`;
      document.getElementById("pi-cur").textContent=d.currency||"USD";
      document.getElementById("pi-stat").textContent=d.status||"active";
      infoEl.style.display="block";
    } else {
      resEl.innerHTML=`<div class="al er">❌ Token invalib\n${d.error||"Erè enkoni"}</div>`;
      infoEl.style.display="none";
    }
  }catch(e){resEl.innerHTML=`<div class="al er">✗ Erè: ${e.message}</div>`;infoEl.style.display="none";}
}

// ══ OAuth2 Flow ══
async function doOAuth2(){
  const btn=event.target;btn.textContent="⏳ Ap jenere URL...";btn.disabled=true;
  try{
    const r=await fetch("/api/oauth_url",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({})});
    const d=await r.json();
    if(d.ok&&d.url){msg("cm","⏳ Ap ouvri Deriv login — w ap retounen otomatik apre...","ok");window.location.href=d.url;}
    else{msg("cm","✗ Pa ka jenere URL OAuth2","");}
  }catch(e){msg("cm","✗ "+e.message,"");}
  btn.textContent="🔐 KONEKTE VIA DERIV OAUTH2";btn.disabled=false;
}

// ══ Toggle broker fields ══
function tog(){
  const v=document.getElementById("d-br").value;
  document.getElementById("fd").style.display=v=="deriv"?"block":"none";
  document.getElementById("fb").style.display=(v=="binance"||v=="binance_us")?"block":"none";
  const note=document.getElementById("fb-note");
  if(note) note.style.display=v=="binance_us"?"block":"none";
}

function togConnMode(){
  const v=document.getElementById("d-conn-mode").value;
  document.getElementById("d-pat-panel").style.display=v=="pat"?"block":"none";
  document.getElementById("d-oauth2-panel").style.display=v=="oauth2"?"block":"none";
  document.getElementById("d-classic-panel").style.display=v=="classic"?"block":"none";
  const cBtn=document.getElementById("conn-btn");
  if(v=="oauth2") cBtn.textContent="⚡ KONEKTE (OAuth2)";
  else if(v=="pat") cBtn.textContent="⚡ KONEKTE (PAT)";
  else cBtn.textContent="⚡ KONEKTE";
}

function toggleMode(){
  const mode=document.getElementById("c-mode").value;
  document.getElementById("opts-forex").style.display=mode=="forex"?"block":"none";
  document.getElementById("opts-digits").style.display=mode=="digits"?"block":"none";
  document.getElementById("opts-gold").style.display=mode=="binance_gold"?"block":"none";
  document.getElementById("opts-crypto").style.display=mode=="binance_crypto"?"block":"none";
}

function getStartParams(){
  const mode=document.getElementById("c-mode").value;
  const conf=parseFloat(document.getElementById("c-conf").value);
  const target=parseFloat(document.getElementById("c-target").value||0);
  const loss=parseFloat(document.getElementById("c-loss").value||0);
  if(mode=="forex") return{mode:"forex",symbol:document.getElementById("c-sy-deriv").value,strategy:document.getElementById("c-st-forex").value,lot:parseFloat(document.getElementById("c-lot-forex").value),tf:document.getElementById("c-tf").value,min_conf:conf,profit_target:target,loss_limit:loss};
  else if(mode=="digits") return{mode:"digits",symbol:document.getElementById("c-sy-digits").value,digit_type:document.getElementById("c-digit-type").value,lot:parseFloat(document.getElementById("c-lot-digits").value),tf:"1m",min_conf:conf,profit_target:target,loss_limit:loss,strategy:"digits"};
  else if(mode=="binance_gold") return{mode:"forex",symbol:document.getElementById("c-sy-gold").value,strategy:"binance_gold",lot:parseFloat(document.getElementById("c-lot-gold").value),tf:document.getElementById("c-tf-gold").value,min_conf:conf,profit_target:target,loss_limit:loss};
  else return{mode:"forex",symbol:document.getElementById("c-sy-crypto").value,strategy:"binance_crypto",lot:parseFloat(document.getElementById("c-lot-crypto").value),tf:document.getElementById("c-tf-crypto").value,min_conf:conf,profit_target:target,loss_limit:loss};
}

const SI={
  confluence:{l:"🔥 Confluence ELITE",d:"v6: SuperTrend(pwa 2.5x)+HeikinAshi(pwa 2.5x)+Chandelier(pwa 2.5x)+10 strategies klasik. ADX≥12, 3 strat min.",tags:["SuperTrend","HeikinAshi","Chandelier","ADX≥12"]},
  deriv_pro:{l:"🚀 Deriv Pro ELITE",d:"Score 5.0/15 + ADX≥12 + SuperTrend bonus 2.0pts.",tags:["score 5/15","ADX≥12","ST bonus"]},
  supertrend:{l:"📈 SuperTrend",d:"ATR × 3.0. Siyal klè BUY/SELL. Travèse bann = siyal solid.",tags:["ATR×3","travèse=BUY","conf 75-92%"]},
  heikin_ashi:{l:"🕯 Heikin Ashi",d:"5 bouji konsekitif. Filtre bwi mache.",tags:["5 bouji","filtre bwi","conf 72-83%"]},
  chandelier:{l:"🔔 Chandelier Exit",d:"HH - ATR×3 (long). LL + ATR×3 (short). Chanjman trend.",tags:["HH-ATR×3","LL+ATR×3","conf 75-90%"]},
  ai:{l:"🤖 AI Score",d:"8 faktè ak pwa: EMA+RSI+MACD+BB+momentum+volatilite.",tags:["8 faktè","pwa","conf 68-92%"]},
  smc:{l:"🏛 SMC",d:"Break of Structure + swing high/low + EMA50.",tags:["BOS","swing","EMA50","84%"]},
  scalping_pro:{l:"⚡ Scalping",d:"EMA 5/13 + RSI 9. Rapid pou 1m/5m.",tags:["EMA 5/13","RSI 9","1m/5m"]},
  rsi:{l:"📉 RSI",d:"RSI <30/>70 + EMA50.",tags:["RSI 14","OB 70","OS 30"]},
  binance_gold:{l:"🥇 Gold Strategy",d:"Espesyal XAU/USD. EMA+RSI+MACD+BB+Stoch. 6+ pts.",tags:["EMA 20/50/200","RSI+Stoch"]},
  binance_crypto:{l:"🪙 Crypto Strategy",d:"Espesyal Binance. Trend+Volume+RSI+MACD+Breakout. 7+ pts.",tags:["EMA 9/21/50","Volume"]},
};
let sel="confluence";
const sb=document.getElementById("sbts");
Object.keys(SI).forEach(k=>{
  const b=document.createElement("button");
  b.className="btn"+(k==sel?" b":"");b.style.cssText="padding:5px 12px;font-size:11px;margin-bottom:4px";
  b.textContent=SI[k].l;b.onclick=()=>{sel=k;renderS();sb.querySelectorAll("button").forEach(x=>x.style.borderColor="#0D2233");b.style.borderColor="#00FF88";};
  sb.appendChild(b);
});
function renderS(){
  const s=SI[sel];
  document.getElementById("sdet").innerHTML=`<div class="bt">${s.l}</div><div style="color:#C8E8F0;line-height:1.8;margin-bottom:12px">${s.d}</div><div style="display:flex;gap:8px;flex-wrap:wrap">${s.tags.map(t=>`<span class="tag" style="border-color:#FFD60044;color:#FFD600">${t}</span>`).join("")}</div>`;
}
renderS();

function sw(id,el){
  document.querySelectorAll(".pg").forEach(p=>p.classList.remove("on"));
  document.querySelectorAll(".tab").forEach(t=>t.classList.remove("on"));
  document.getElementById("pg-"+id).classList.add("on");el.classList.add("on");
}
function msg(id,txt,ok){document.getElementById(id).innerHTML=`<div class="al ${ok?"ok":"er"}">${txt}</div>`;}

async function doConn(){
  const br=document.getElementById("d-br").value;
  const btn=document.getElementById("conn-btn");btn.textContent="AP KONEKTE...";btn.disabled=true;
  const brokerLabel={"deriv":"Deriv","binance":"Binance Global","binance_us":"Binance US"}[br]||br;
  msg("cm",`⏳ Ap konekte ${brokerLabel}...`,"ok");

  const body={broker:br};
  if(br=="deriv"){
    const connMode=document.getElementById("d-conn-mode").value;
    body.conn_type=connMode;
    if(connMode=="pat"){
      body.pat_token=document.getElementById("d-pat-input").value.trim();
    } else if(connMode=="classic"){
      body.token=document.getElementById("d-tk").value.trim();
      body.app_id=document.getElementById("d-ai").value||"1089";
    }
    // oauth2: backend gen token via /callback
  }
  if(br=="binance"||br=="binance_us"){
    body.api_key=document.getElementById("b-k").value;
    body.api_secret=document.getElementById("b-s").value;
  }

  try{
    const r=await fetch("/api/connect",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(d.ok){
      const noteMsg=d.note?` | ${d.note}`:"";
      const acctMsg=d.account_id?` | Kont: ${d.account_id}`:"";
      const curMsg=d.currency?` ${d.currency}`:"";
      msg("cm",`✓ Konekte ${brokerLabel} | $${d.balance.toFixed(2)}${curMsg}${acctMsg}${noteMsg}`,"ok");
      document.getElementById("cs").innerHTML=`<div class="al ok">✓ <b>${brokerLabel}</b> | $${d.balance.toFixed(2)}${curMsg}${acctMsg}${noteMsg}</div>`;
      // Montre info kont PAT
      if(d.account_id){
        const acctEl=document.getElementById("c-acct-info");
        const acctIdEl=document.getElementById("c-acct-id");
        const curEl=document.getElementById("c-currency");
        const hAcct=document.getElementById("h-acct");
        if(acctEl){acctEl.style.display="block";}
        if(acctIdEl) acctIdEl.textContent=d.account_id;
        if(curEl) curEl.textContent=d.currency||"USD";
        if(hAcct){hAcct.style.display="inline";hAcct.textContent=`[${d.account_id} | ${d.currency||"USD"}]`;}
      }
    } else {
      let errMsg=d.error||"Echwe";
      if(d.oauth_url) errMsg+=`\n\n➡️ <a href="${d.oauth_url}" style="color:#A855F7">Klike isit pou OAuth2</a>`;
      msg("cm",`✗ Echwe\n${errMsg}`,false);
    }
  }catch(e){msg("cm","✗ "+e.message,false);}
  btn.textContent="⚡ KONEKTE";btn.disabled=false;
}

async function doStart(){
  const body=getStartParams();
  const r=await fetch("/api/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const d=await r.json();
  if(d.ok){msg("ctm","✓ BonheurBot ELITE v6 démarre!","ok");document.getElementById("bs").style.display="none";document.getElementById("bx").style.display="inline-block";}
  else msg("ctm","✗ "+d.error,false);
}
async function doStop(){
  await fetch("/api/stop",{method:"POST"});
  msg("ctm","✓ Bot arrêté","ok");
  document.getElementById("bs").style.display="inline-block";document.getElementById("bx").style.display="none";
}

async function doBt(){
  const btn=event.target;btn.textContent="⏳ AP KALKILE...";btn.disabled=true;
  document.getElementById("btm").innerHTML=`<div class="al in">⏳ Ap fè backtest — tann 30 segonn...</div>`;
  const body={symbol:document.getElementById("bt-sy").value,strategy:document.getElementById("bt-st").value,balance:parseFloat(document.getElementById("bt-bl").value),lot:parseFloat(document.getElementById("bt-lt").value),sl:parseFloat(document.getElementById("bt-sl").value),tp:parseFloat(document.getElementById("bt-tp").value)};
  try{
    const r=await fetch("/api/backtest",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();document.getElementById("btm").innerHTML="";
    if(d.ok){
      const v=d.result;const c=v.net_pnl>=0?"#00FF88":"#FF3B6B";
      document.getElementById("btr").innerHTML=`<div class="stats">
        <div class="stat"><div class="sl">NET P&L</div><div class="sv" style="color:${c}">$${v.net_pnl}</div></div>
        <div class="stat"><div class="sl">RETOU</div><div class="sv" style="color:${c}">${v.return_pct}%</div></div>
        <div class="stat"><div class="sl">WIN RATE</div><div class="sv" style="color:#00FF88">${v.win_rate}%</div></div>
        <div class="stat"><div class="sl">TRADES</div><div class="sv" style="color:#FFD600">${v.trades}</div></div>
        <div class="stat"><div class="sl">MAX DD</div><div class="sv" style="color:#FF3B6B">${v.max_dd}%</div></div>
        <div class="stat"><div class="sl">SHARPE</div><div class="sv" style="color:#00D4FF">${v.sharpe}</div></div>
        <div class="stat"><div class="sl">PROFIT FACTOR</div><div class="sv" style="color:#FFD600">${v.pf}</div></div>
      </div>${v.equity&&v.equity.length>2?drawC(v.equity):""}`;
    }else document.getElementById("btm").innerHTML=`<div class="al er">✗ ${d.error}</div>`;
  }catch(e){document.getElementById("btm").innerHTML=`<div class="al er">✗ ${e.message}</div>`;}
  btn.textContent="▶ KÒMANSE BACKTEST";btn.disabled=false;
}

function drawC(vals){
  const W=500,H=110,p=8;const mn=Math.min(...vals),mx=Math.max(...vals),rng=mx-mn||1;
  const pts=vals.map((v,i)=>`${p+(i/(vals.length-1))*(W-p*2)},${H-p-((v-mn)/rng)*(H-p*2)}`).join(" ");
  const area=`${p},${H} ${pts} ${W-p},${H}`;const col=vals[vals.length-1]>=vals[0]?"#00FF88":"#FF3B6B";
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:110px;margin-top:12px"><defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${col}" stop-opacity=".3"/><stop offset="100%" stop-color="${col}" stop-opacity="0"/></linearGradient></defs><polygon points="${area}" fill="url(#cg)"/><polyline points="${pts}" fill="none" stroke="${col}" stroke-width="2.5"/></svg>`;
}

function upd(d){
  const col=d.pnl>=0?"#00FF88":"#FF3B6B";const sign=d.pnl>=0?"+":"";
  const brokerLabel={"deriv":"DERIV","binance":"BINANCE","binance_us":"BINANCE US"}[d.broker]||(d.broker?d.broker.toUpperCase():"DISCONNECTED");
  const authLabel=d.pat_ready?"[PAT]":(d.oauth_ready?"[OAuth2]":"");
  document.getElementById("hbal").textContent="$"+d.balance.toFixed(2);document.getElementById("hbal").style.color=d.connected?"#00D4FF":"#3A6070";
  document.getElementById("hb").textContent=brokerLabel+(authLabel?" "+authLabel:"");document.getElementById("hb").style.color=d.connected?"#00FF88":"#3A6070";
  document.getElementById("dot").className="dot "+(d.running?"dl":"di");
  document.getElementById("hs").textContent=d.running?"LIVE":"IDLE";document.getElementById("hs").style.color=d.running?"#00FF88":"#3A6070";
  document.getElementById("s-bal").textContent="$"+d.balance.toFixed(2);
  document.getElementById("s-pnl").textContent=sign+"$"+Math.abs(d.pnl).toFixed(2);document.getElementById("s-pnl").style.color=col;
  document.getElementById("s-pnl2").textContent=sign+"$"+Math.abs(d.pnl).toFixed(2);document.getElementById("s-pnl2").style.color=col;
  document.getElementById("s-sent").textContent="$"+d.profit_sent.toFixed(4);
  document.getElementById("s-tr").textContent=d.trades.length;
  document.getElementById("s-bot").textContent=d.running?"LIVE 🟢":"IDLE";document.getElementById("s-bot").style.color=d.running?"#00FF88":"#3A6070";
  document.getElementById("s-strat").textContent=d.config.strategy||"—";document.getElementById("s-sym").textContent=d.config.symbol||"—";
  document.getElementById("s-br2").textContent=brokerLabel;document.getElementById("s-br2").style.color=d.connected?"#00FF88":"#3A6070";
  document.getElementById("c-st2").textContent=d.running?"LIVE 🟢":"IDLE";document.getElementById("c-st2").style.color=d.running?"#00FF88":"#3A6070";
  document.getElementById("c-bal").textContent="$"+d.balance.toFixed(2);
  document.getElementById("c-pnl").textContent=sign+"$"+Math.abs(d.pnl).toFixed(2);document.getElementById("c-pnl").style.color=col;
  document.getElementById("c-sent").textContent="$"+d.profit_sent.toFixed(4);
  // Show account info if PAT connected
  if(d.pat_ready && d.account_id){
    const acctEl=document.getElementById("c-acct-info");
    if(acctEl){acctEl.style.display="block";document.getElementById("c-acct-id").textContent=d.account_id;document.getElementById("c-currency").textContent=d.currency||"USD";}
    const hAcct=document.getElementById("h-acct");if(hAcct){hAcct.style.display="inline";hAcct.textContent=`[${d.account_id}|${d.currency||"USD"}]`;}
  }
  if(d.running){document.getElementById("bs").style.display="none";document.getElementById("bx").style.display="inline-block";}
  else{document.getElementById("bs").style.display="inline-block";document.getElementById("bx").style.display="none";}
  if(d.trades.length>1){
    let cum=0;const eq=d.trades.slice().reverse().map(t=>{cum+=t.pnl||0;return cum;});
    const svg=document.getElementById("chart");const ch=drawC(eq);const tmp=document.createElement("div");tmp.innerHTML=ch;
    const ns=tmp.firstChild;while(svg.firstChild)svg.removeChild(svg.firstChild);while(ns.firstChild)svg.appendChild(ns.firstChild);
  }
  if(d.trades.length){
    document.getElementById("trtit").textContent=`HISTOIRIK TRADES (${d.trades.length})`;
    document.getElementById("trtbl").innerHTML=`<table><tr><th>#</th><th>Lè</th><th>Senbol</th><th>Side</th><th>Antre</th><th>Regime</th><th>Mise</th><th>Conf</th><th>P&L</th><th>Estati</th></tr>${d.trades.map(t=>`<tr><td style="color:#4A7080">${t.id}</td><td style="color:#4A7080">${t.time}</td><td style="font-weight:700">${t.symbol}</td><td><span class="tag ${t.side=="BUY"||t.side.includes("OVER")||t.side=="EVEN"?"tb":"ts"}">${t.side}</span></td><td>${t.entry}</td><td style="color:#4A7080;font-size:10px">${t.regime||"—"}</td><td style="color:#FFD600">$${t.stake||"—"}</td><td style="color:#FFD600">${t.conf}</td><td style="color:${t.pnl>=0?"#00FF88":"#FF3B6B"};font-weight:700">${t.pnl>=0?"+":""}${t.pnl.toFixed(2)}</td><td><span class="tag ${t.status=="won"?"tb":"ts"}">${t.status||"—"}</span></td></tr>`).join("")}</table>`;
  }
  if(d.log.length){document.getElementById("logs").innerHTML=d.log.map(l=>`<div class="le"><span class="lt">${l.time}</span><span class="l${l.level[0]}">${l.msg}</span></div>`).join("");}
}

async function poll(){try{const r=await fetch("/api/status");const d=await r.json();upd(d);}catch(e){}setTimeout(poll,3000);}

async function admRefresh(){
  const token=getStoredToken();if(!token){alert("Pa konekte!");return;}
  try{
    const r=await fetch("/api/admin/codes",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});
    const d=await r.json();
    if(d.ok){
      const sc={"ADM":"#00D4FF","AKTIF":"#00FF88","ITILIZE":"#FF3B6B","EKSPIRE":"#4A7080"};
      document.getElementById("adm-total").textContent=d.codes.length;
      document.getElementById("adm-aktif").textContent=d.codes.filter(c=>c.status==="AKTIF").length;
      document.getElementById("adm-used").textContent=d.codes.filter(c=>c.status==="ITILIZE").length;
      document.getElementById("adm-sess").textContent=d.total_sessions;
      document.getElementById("adm-codes-list").innerHTML=`<table><tr><th>KÒD</th><th>STATUS</th><th>RETE</th><th>TIP</th><th>AKSYON</th></tr>${d.codes.map(c=>`<tr><td style="font-weight:700">${c.code}</td><td><span class="tag" style="color:${sc[c.status]||"#4A7080"};border-color:${sc[c.status]||"#4A7080"}44">${c.status}</span></td><td style="color:#4A7080">${c.remaining}</td><td>${c.is_adm?"👑":"👤"}</td><td style="display:flex;gap:4px">${c.status!=="ADM"?`<button onclick="admReset('${c.code}')" style="background:transparent;border:1px solid #FFD60044;color:#FFD600;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">↺</button>`:""} ${c.code!=="BONHEURWIIN"?`<button onclick="admRevoke('${c.code}')" style="background:transparent;border:1px solid #FF3B6B44;color:#FF3B6B;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">✕</button>`:""}</td></tr>`).join("")}</table>`;
    }
  }catch(e){console.error(e);}
  try{
    const r2=await fetch("/api/admin/users",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});
    const d2=await r2.json();
    if(d2.ok){
      document.getElementById("adm-users-count").textContent=d2.total;
      document.getElementById("adm-users-list").innerHTML=d2.total===0?'<div style="color:#3A6070;text-align:center;padding:20px">Pa gen itilizatè</div>':`<table><tr><th>UID</th><th>AUTH</th><th>BROKER</th><th>SENBOL</th><th>BOT</th><th>BALANS</th><th>P&L</th><th>TRADES</th><th>AKSYON</th></tr>${d2.users.map(u=>`<tr><td style="color:#4A7080;font-size:10px">${u.uid}</td><td style="color:#00D4FF;font-size:10px">${u.auth_type||"—"}</td><td>${u.broker||"—"}</td><td style="font-weight:700">${u.symbol||"—"}</td><td><span class="tag ${u.running?"tb":"tg"}">${u.running?"LIVE":"IDLE"}</span></td><td style="color:#00D4FF">$${u.balance}</td><td style="color:${u.pnl>=0?"#00FF88":"#FF3B6B"}">${u.pnl>=0?"+":""}$${u.pnl}</td><td>${u.trades}</td><td style="display:flex;gap:4px;align-items:center">${u.running?`<button onclick="admStopUser('${u.uid}')" style="background:transparent;border:1px solid #FF3B6B44;color:#FF3B6B;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">■</button>`:""}<button onclick="admClearTrades('${u.uid}')" style="background:transparent;border:1px solid #FFD60044;color:#FFD600;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">📊🗑</button><button onclick="admClearUser('${u.uid}')" style="background:transparent;border:1px solid #4A708044;color:#4A7080;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">🗑</button></td></tr>`).join("")}</table>`;
    }
  }catch(e){}
  try{
    const r3=await fetch("/api/admin/sessions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});
    const d3=await r3.json();
    if(d3.ok){document.getElementById("adm-sessions-list").innerHTML=d3.sessions.length===0?'<div style="text-align:center;padding:10px">Pa gen sesyon</div>':d3.sessions.map(s=>`<div style="padding:5px 0;border-bottom:1px solid #0D2233;display:flex;justify-content:space-between"><span style="color:#4A7080">${s.token}</span><span style="color:${s.is_admin?"#00D4FF":"#4A7080"}">${s.is_admin?"👑":"👤"}</span><span style="color:${s.active?"#00FF88":"#FF3B6B"}">${s.days_left} jou</span></div>`).join("");}
  }catch(e){}
}

async function admAddCode(){
  const token=getStoredToken();const code=document.getElementById("new-code").value.trim().toUpperCase();
  if(!code){document.getElementById("add-code-msg").innerHTML='<div class="al er">Mete yon kòd</div>';return;}
  const isAdm=document.getElementById("new-code-type").value==="adm";
  const r=await fetch("/api/admin/add_code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,code,is_adm:isAdm})});
  const d=await r.json();
  document.getElementById("add-code-msg").innerHTML=`<div class="al ${d.ok?"ok":"er"}">${d.ok?d.msg:d.error}</div>`;
  if(d.ok){document.getElementById("new-code").value="";admRefresh();}
}
async function admRevoke(code){if(!confirm(`Revoke ${code}?`))return;const token=getStoredToken();const r=await fetch("/api/admin/revoke_code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,code})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh();}
async function admReset(code){const token=getStoredToken();const r=await fetch("/api/admin/reset_code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,code})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh();}
async function admStopUser(uid){if(!confirm(`Kanpe bot ${uid}?`))return;const token=getStoredToken();const r=await fetch("/api/admin/stop_user",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,uid})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh();}
async function admCleanSessions(){const token=getStoredToken();const r=await fetch("/api/admin/clean_sessions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh();}
async function admClearUser(uid){if(!confirm(`Efase TOUT istorik ${uid}?\n(trades + log + pnl reset)`))return;const token=getStoredToken();const r=await fetch("/api/admin/clear_user",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,uid})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh();}
async function admClearTrades(uid){if(!confirm(`Efase trades sèlman pou ${uid}?`))return;const token=getStoredToken();const r=await fetch("/api/admin/clear_trades",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,uid})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh();}

function genCode(len){const chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789";let result="";for(let i=0;i<len;i++){if(i>0&&i%4===0)result+="-";result+=chars[Math.floor(Math.random()*chars.length)];}document.getElementById("gen-result").textContent=result;document.getElementById("gen-copy-btn").style.display="inline-block";document.getElementById("new-code").value=result;}
function admCopyGen(){const code=document.getElementById("gen-result").textContent;navigator.clipboard.writeText(code).catch(()=>{});admAddCode();}

checkLogin();
</script>
</body>
</html>"""

if __name__=="__main__":
    port=int(os.environ.get("PORT",5000))
    logger.info(f"BonheurBot ELITE v6 starting on port {port}")
    logger.info(f"App ID: {DERIV_CLIENT_ID} | Redirect: {DERIV_REDIRECT_URI}")
    logger.info(f"PAT REST Base: {DERIV_REST_BASE}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
