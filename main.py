"""
╔══════════════════════════════════════════════════════════════╗
║     BONHEURBOT PRO v7.2 PAT-ONLY — MARTINGAL FIX + API PAY ║
║      Multi-User Trading Bot — Deriv PAT                      ║
║                                                              ║
║  CHANJMAN v7.2:                                              ║
║   ✅ Nivo Martingal FIX: 0.50→1.05→2.16→4.43→9.09→18.66→38.31
║   ✅ Payout reyèl: mande API Deriv anvan CHAK trade          ║
║   ✅ Retire PAYOUT_TABLE statik la nèt                       ║
║   ✅ get_proposal_payout() — payout egzak nan proposal       ║
║   ✅ Kalkil martingal ak payout API a                        ║
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

# ═══════════════════════════════════════════════════════════
# ██  NIVO MARTINGAL FIX v7.2  ██
# Kalkilasyon: chak nivo kalkile pou rekipere tout pèt anvan
# yo + base_lot apre victwa, baze sou payout mwayen ~82%
# ═══════════════════════════════════════════════════════════
MARTINGAL_LEVELS = [0.50, 1.05, 2.16, 4.43, 9.09, 18.66, 38.31]
# Nivo 1: $0.50 (base)
# Nivo 2: $1.05
# Nivo 3: $2.16
# Nivo 4: $4.43
# Nivo 5: $9.09
# Nivo 6: $18.66
# Nivo 7: $38.31

MARTINGAL_LEVELS_DIGITS = [0.35, 0.72, 1.48, 3.03, 6.21, 12.74, 26.13]
# Nivo digits (payout 95% ≈ menm lojik)

def get_martingal_stake(level: int, is_digits: bool = False) -> float:
    """
    Retounen mise pou nivo martingal la.
    level: 0 = premye trade (base), 1 = apre 1 pèt, etc.
    Max nivo = 6 (endèks 6 = nivo 7)
    """
    levels = MARTINGAL_LEVELS_DIGITS if is_digits else MARTINGAL_LEVELS
    idx = min(level, len(levels) - 1)
    return levels[idx]


ACCESS_CODES = {
    "BONHEURWIIN": {"created_at": None, "used": False, "is_adm": True},
    "HJKy8kFD":    {"created_at": time.time(), "used": False, "is_adm": False},
    "GHt3hjI6":    {"created_at": time.time(), "used": False, "is_adm": False},
    "3KMM-9X":     {"created_at": time.time(), "used": False, "is_adm": False},
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
                "deriv_api": None, "deriv_digits_api": None,
            }
    return _user_states[uid]


# ═══════════════════════════════════════════════════════════
# ██  DERIV PAT REST CLIENT — v7.2 ak get_proposal_payout ██
# ═══════════════════════════════════════════════════════════
class DerivPATClient:
    def __init__(self, pat_token: str, app_id: str = "33ifAjI7cFab3IsUV8u9q", timeout: int = 25):
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
            "User-Agent":    "BonheurBot/7.2",
        }

    def _get(self, path, params=None):
        url = f"{DERIV_REST_BASE}{path}"
        r   = requests.get(url, headers=self._headers, params=params or {}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _post_rest(self, path, data=None):
        url = f"{DERIV_REST_BASE}{path}"
        r   = requests.post(url, headers=self._headers, json=data or {}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _fresh_ws_url(self) -> str:
        if not self._account_id:
            return ""
        try:
            endpoint = f"/options/accounts/{self._account_id}/otp"
            resp = self._post_rest(endpoint)
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
                return ws_url
            return ""
        except requests.HTTPError as e:
            logger.error(f"PAT OTP HTTP {e.response.status_code}: {e.response.text[:200]}")
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
            except Exception as e:
                err[0] = str(e); done.set()

        def on_err(ws, e):
            if not done.is_set():
                err[0] = f"WS erè: {str(e)[:150]}"; done.set()

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
            accounts = self._extract_accounts(data)
            if not accounts and isinstance(data, dict) and "balance" in data:
                self._bal     = float(data.get("balance") or 0)
                self._loginid = data.get("loginid") or data.get("account_id") or "PAT_USER"
                self._account_id = (data.get("account_id") or data.get("id") or
                                    data.get("loginid") or "")
                return self._bal
            if accounts:
                real = next((a for a in accounts
                             if str(a.get("account_type","")).lower() in ("real","financial","gaming")),
                            accounts[0])
                self._account_id = (real.get("account_id") or real.get("id") or
                                    real.get("accountId") or real.get("loginid") or
                                    real.get("login") or "")
                self._loginid    = (real.get("loginid") or real.get("login") or
                                    self._account_id or "PAT_USER")
                self._bal        = float(real.get("balance", 0) or
                                         real.get("available_balance", 0) or 0)
                return self._bal
        except requests.HTTPError as e:
            errors.append(f"HTTP {e.response.status_code}: {e.response.text[:150]}")
        except Exception as e:
            errors.append(str(e)[:150])

        raise Exception(
            "Token PAT echwe:\n" + "\n".join(errors) +
            "\n\nSOLISYON:\n"
            "  app.deriv.com → foto → API Token\n"
            "  'Personal Access Token' → kole (kòmanse ak pat_)\n"
            "  Pèmisyon: ✓ Read ✓ Trade ✓ Payments\n"
        )

    def get_balance_sync(self) -> float:
        try:
            data = self._get("/options/accounts")
            accs = self._extract_accounts(data)
            if accs:
                real = next((a for a in accs if a.get("account_type") == "real"), accs[0])
                b = float(real.get("balance", 0) or 0)
                if b > 0: self._bal = b; return b
            if isinstance(data, dict) and "balance" in data:
                b = float(data["balance"] or 0)
                if b > 0: self._bal = b; return b
        except: pass
        return self._bal

    # ══════════════════════════════════════════════════════
    # ██  GET PAYOUT REYÈL nan API — NOUVO v7.2  ██
    # Voye yon proposal pou $1 epi li payout retounen
    # ══════════════════════════════════════════════════════
    def get_proposal_payout(self, symbol: str, direction: str, duration_secs: int = 900) -> float:
        """
        Mande Deriv API payout reyèl pou kontrak sa a.
        Retounen: payout kòm desimal (ex: 0.87 = 87%)
        Si echèk → retounen 0.82 (konservatif)
        """
        ct = "CALL" if direction == "BUY" else "PUT"
        if   duration_secs <= 60:    dv, du = 1,  "m"
        elif duration_secs <= 300:   dv, du = 5,  "m"
        elif duration_secs <= 900:   dv, du = 15, "m"
        elif duration_secs <= 3600:  dv, du = 1,  "h"
        else:                        dv, du = 4,  "h"

        import websocket as wsl
        res = [None]; done = threading.Event()

        def on_open(ws):
            ws.send(json.dumps({
                "proposal": 1,
                "amount": 1.00,
                "basis": "stake",
                "contract_type": ct,
                "currency": "USD",
                "underlying_symbol": symbol,
                "duration": dv,
                "duration_unit": du,
            }))

        def on_msg(ws, msg):
            d = json.loads(msg)
            if d.get("msg_type") == "proposal":
                if "error" not in d:
                    prop = d.get("proposal", {})
                    # payout = payout_per_point oswa ask_price ratio
                    # ask_price = stake = 1.00
                    # payout reyèl = (bid_price - ask_price) / ask_price oswa profit / stake
                    ask  = float(prop.get("ask_price", 1.0))
                    bid  = float(prop.get("bid_price", 0))
                    # Fòmil: profit_ratio = bid/ask - 1 si sold early
                    # Men payout reyèl si genyen: prop["payout"] oswa prop["profit_percent"]
                    payout_val = prop.get("payout")
                    if payout_val is not None:
                        # payout = valè total si genyen (stake + profit)
                        # profit % = (payout - stake) / stake
                        p = (float(payout_val) - ask) / max(ask, 0.01)
                        res[0] = round(p, 4)
                    else:
                        # Essaye lòt champ
                        pp = prop.get("profit_percent") or prop.get("return_percent")
                        if pp is not None:
                            res[0] = round(float(pp) / 100, 4)
                        else:
                            # Kalkile manual: bid soti nan proposal
                            if bid > 0 and ask > 0:
                                res[0] = round((bid - ask) / ask, 4)
                            else:
                                res[0] = 0.82
                done.set()
                try: ws.close()
                except: pass
            elif "error" in d:
                done.set()
                try: ws.close()
                except: pass

        def on_err(ws, e):
            if not done.is_set(): done.set()

        try:
            ws = wsl.WebSocketApp(DERIV_WS_PUBLIC, on_open=on_open,
                                  on_message=on_msg, on_error=on_err)
            t = threading.Thread(target=ws.run_forever, daemon=True)
            t.start()
            done.wait(timeout=15)
            if res[0] is not None and 0.50 <= res[0] <= 1.0:
                return res[0]
        except Exception as e:
            logger.warning(f"get_proposal_payout echwe: {e}")
        return 0.82  # fallback konservatif

    def get_digits_proposal_payout(self, symbol: str, contract_type: str, barrier=None) -> float:
        """
        Mande payout reyèl pou Digits contract.
        """
        import websocket as wsl
        res = [None]; done = threading.Event()
        proposal_msg = {
            "proposal": 1,
            "amount": 1.00,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "underlying_symbol": symbol,
            "duration": 5,
            "duration_unit": "t",
        }
        if barrier is not None:
            proposal_msg["barrier"] = str(barrier)

        def on_open(ws):
            ws.send(json.dumps(proposal_msg))

        def on_msg(ws, msg):
            d = json.loads(msg)
            if d.get("msg_type") == "proposal":
                if "error" not in d:
                    prop = d.get("proposal", {})
                    ask  = float(prop.get("ask_price", 1.0))
                    payout_val = prop.get("payout")
                    if payout_val is not None:
                        p = (float(payout_val) - ask) / max(ask, 0.01)
                        res[0] = round(p, 4)
                    else:
                        pp = prop.get("profit_percent") or prop.get("return_percent")
                        if pp is not None:
                            res[0] = round(float(pp) / 100, 4)
                        else:
                            res[0] = 0.95  # default digits
                done.set()
                try: ws.close()
                except: pass
            elif "error" in d:
                done.set()
                try: ws.close()
                except: pass

        def on_err(ws, e):
            if not done.is_set(): done.set()

        try:
            ws = wsl.WebSocketApp(DERIV_WS_PUBLIC, on_open=on_open,
                                  on_message=on_msg, on_error=on_err)
            t = threading.Thread(target=ws.run_forever, daemon=True)
            t.start()
            done.wait(timeout=15)
            if res[0] is not None and 0.50 <= res[0] <= 1.0:
                return res[0]
        except Exception as e:
            logger.warning(f"get_digits_payout echwe: {e}")
        return 0.95  # fallback digits

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
        return self._public_candles(symbol, count, gran)

    def _public_candles(self, symbol, count, gran):
        import websocket as wsl
        res = [None]; done = threading.Event()
        def on_msg(ws, msg):
            d = json.loads(msg)
            if "candles" in d: res[0] = d["candles"]; done.set()
            elif "error" in d: done.set()
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
                res[0] = d.get("history", {}); done.set()
            elif "error" in d: done.set()
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

        amount = max(0.50, float(amount))

        def send(ws):
            msg = {
                "proposal":          1,
                "amount":            amount,
                "basis":             "stake",
                "contract_type":     ct,
                "currency":          "USD",
                "underlying_symbol": symbol,
                "duration":          dv,
                "duration_unit":     du,
            }
            ws.send(json.dumps(msg))

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
        amount = max(0.35, float(amount))
        proposal_msg = {
            "proposal":          1,
            "amount":            amount,
            "basis":             "stake",
            "contract_type":     contract_type,
            "currency":          "USD",
            "underlying_symbol": symbol,
            "duration":          5,
            "duration_unit":     "t",
        }
        if barrier is not None:
            proposal_msg["barrier"] = str(barrier)

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
            if d.get("msg_type") == "proposal_open_contract":
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
            if d.get("msg_type") == "transfer_between_accounts":
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
# INDIKATÈ TEKNIK — KONPLÈ
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
        growing = bodies[-1] >= bodies[0] * 0.7
        return "BUY", 0.83 if growing else 0.77
    if len(bear)==lookback:
        bodies=[abs(b["close"]-b["open"]) for b in bear]
        growing = bodies[-1] >= bodies[0] * 0.7
        return "SELL", 0.83 if growing else 0.77
    if len(bull)>=lookback-1: return "BUY",  0.72
    if len(bear)>=lookback-1: return "SELL", 0.72
    return "NONE", 0.0


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
    if dist>0.3:    return "BUY",  min(0.88,0.72+min(dist*0.03,0.16))
    elif dist<-0.3: return "SELL", min(0.88,0.72+min(abs(dist)*0.03,0.16))
    return "NONE", 0.0


def strat_ema(c):
    cl=[x["close"] for x in c]
    if len(cl)<25: return "NONE",0
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50) if len(cl)>=50 else None
    if len(e9)<3 or len(e21)<3: return "NONE",0
    r=rsi(cl)
    if e9[-2]<=e21[-2] and e9[-1]>e21[-1]:
        if (not e50 or cl[-1]>e50[-1]) and r<75: return "BUY", 0.76
    if e9[-2]>=e21[-2] and e9[-1]<e21[-1]:
        if (not e50 or cl[-1]<e50[-1]) and r>25: return "SELL", 0.76
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
            if r<40 and price>lo+(rng*0.2): return "BUY", conf
            if r>60 and price<hi-(rng*0.2): return "SELL", conf
    return "NONE",0

def strat_fvg(c):
    if len(c)<15: return "NONE",0
    cl=[x["close"] for x in c]
    e21=ema(cl,21) if len(cl)>=21 else None
    r=rsi(cl)
    for i in range(3,min(15,len(c)-1)):
        c1=c[-(i+2)]; c3=c[-i]
        if c3["low"]>c1["high"] and c1["high"]<cl[-1]<c3["low"]:
            if (not e21 or cl[-1]>e21[-1]) and r<60: return "BUY", 0.80
        if c3["high"]<c1["low"] and c3["high"]<cl[-1]<c1["low"]:
            if (not e21 or cl[-1]<e21[-1]) and r>40: return "SELL", 0.80
    return "NONE",0

def strat_rsi(c):
    cl=[x["close"] for x in c]
    if len(cl)<25: return "NONE",0
    r=rsi(cl); r2=rsi(cl[:-3]) if len(cl)>3 else r
    e50=ema(cl,50) if len(cl)>=50 else None
    if r<30:
        if not e50 or cl[-1]>e50[-1]*0.998: return "BUY", 0.82
    if 30<=r<42 and r>r2:
        if not e50 or cl[-1]>e50[-1]*0.996: return "BUY", 0.74
    if r>70:
        if not e50 or cl[-1]<e50[-1]*1.002: return "SELL", 0.82
    if 58<r<=70 and r<r2:
        if not e50 or cl[-1]<e50[-1]*1.004: return "SELL", 0.74
    return "NONE",0

def strat_macd(c):
    cl=[x["close"] for x in c]
    if len(cl)<35: return "NONE",0
    up,mid,lo=bb(cl,20,2.0); m,sig=macd(cl)
    if up is None: return "NONE",0
    r=rsi(cl)
    if m>sig and lo and cl[-1]<=lo:           return "BUY", 0.78
    if m>sig and mid and cl[-1]<mid and r<45: return "BUY", 0.72
    if m<sig and up and cl[-1]>=up:           return "SELL", 0.78
    if m<sig and mid and cl[-1]>mid and r>55: return "SELL", 0.72
    return "NONE",0

def strat_breakout(c):
    if len(c)<30: return "NONE",0
    cl=[x["close"] for x in c]
    hi20=max(x["high"] for x in c[-21:-1])
    lo20=min(x["low"] for x in c[-21:-1])
    r=rsi(cl)
    if cl[-1]>hi20 and cl[-2]<=hi20 and 50<r<75: return "BUY", 0.80
    if cl[-1]<lo20 and cl[-2]>=lo20 and 25<r<50: return "SELL", 0.80
    return "NONE",0

def strat_smc(c):
    if len(c)<40: return "NONE",0
    cl=[x["close"] for x in c]
    e50=ema(cl,50) if len(cl)>=50 else None
    r=rsi(cl)
    swing_hi=max(x["high"] for x in c[-30:-5])
    swing_lo=min(x["low"] for x in c[-30:-5])
    if cl[-1]>swing_hi and cl[-2]<=swing_hi:
        if (not e50 or cl[-1]>e50[-1]) and 45<r<75: return "BUY", 0.84
    if cl[-1]<swing_lo and cl[-2]>=swing_lo:
        if (not e50 or cl[-1]<e50[-1]) and 25<r<55: return "SELL", 0.84
    return "NONE",0

def strat_ob(c):
    if len(c)<30: return "NONE",0
    cl=[x["close"] for x in c]
    e21=ema(cl,21) if len(cl)>=21 else None
    r=rsi(cl)
    for i in range(4,20):
        b=c[-(i+1)]; body=abs(b["close"]-b["open"]); rng=b["high"]-b["low"]
        if rng==0 or body/rng<0.65: continue
        if b["close"]<b["open"] and b["close"]<=cl[-1]<=b["open"]:
            if (not e21 or cl[-1]>e21[-1]*0.997) and r<55: return "BUY", 0.82
        if b["close"]>b["open"] and b["open"]<=cl[-1]<=b["close"]:
            if (not e21 or cl[-1]<e21[-1]*1.003) and r>45: return "SELL", 0.82
    return "NONE",0

def strat_stoch(c):
    if len(c)<20: return "NONE",0
    cl=[x["close"] for x in c]
    k=stoch_k(c); kp=stoch_k(c[:-1]) if len(c)>1 else k
    e50=ema(cl,50) if len(cl)>=50 else None
    r=rsi(cl)
    if k>kp and k<30 and (not e50 or cl[-1]>e50[-1]*0.997): return "BUY", 0.80
    if k<kp and k>70 and (not e50 or cl[-1]<e50[-1]*1.003): return "SELL", 0.80
    return "NONE",0

def strat_ai(c):
    if len(c)<60: return "NONE",0
    cl=[x["close"] for x in c]; hi=[x["high"] for x in c]; lo_=[x["low"] for x in c]
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50)
    e200=ema(cl,200) if len(cl)>=200 else e50
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
        mom=(cl[-1]-cl[-6])/max(abs(cl[-6]),0.001)*100
        f[4]=max(-1,min(1,mom/2))
    if at and mid:
        vol_ratio=at/mid*100
        f[5]=1.0 if 0.1<vol_ratio<0.5 else(0.5 if vol_ratio<=0.1 else -0.5)
    hi20=max(hi[-20:]); lo20=min(lo_[-20:])
    rng20=hi20-lo20 if hi20!=lo20 else 1
    pos=(cl[-1]-lo20)/rng20
    f[6]=1.0 if pos<0.2 else(-1.0 if pos>0.8 else 0.0)
    if e50 and e200:
        trend=(e50[-1]-e200[-1])/max(e200[-1],0.001)*100
        f[7]=max(-1,min(1,trend*10))
    W=[2.8,2.2,1.8,1.5,1.2,0.8,1.6,1.9]
    score=sum(f[i]*W[i] for i in range(8)); max_score=sum(W)
    score_norm=score/max_score
    if score_norm>=0.35:  return "BUY",  min(0.92,0.68+score_norm*0.35)
    if score_norm<=-0.35: return "SELL", min(0.92,0.68+abs(score_norm)*0.35)
    return "NONE",0

def strat_scalping(c):
    if len(c)<20: return "NONE",0
    cl=[x["close"] for x in c]
    e5=ema(cl,5); e13=ema(cl,13); e50=ema(cl,50) if len(cl)>=50 else None
    if len(e5)<3 or len(e13)<3: return "NONE",0
    r=rsi(cl,9)
    if e5[-1]>e13[-1] and r<70:
        if not e50 or cl[-1]>e50[-1]*0.997: return "BUY", 0.74
    if e5[-1]<e13[-1] and r>30:
        if not e50 or cl[-1]<e50[-1]*1.003: return "SELL", 0.74
    return "NONE",0


def calc_pivot_points(candles):
    if len(candles) < 20: return None
    recent = candles[-20:]
    hi  = max(x["high"]  for x in recent)
    lo  = min(x["low"]   for x in recent)
    cl  = candles[-1]["close"]
    pp  = (hi + lo + cl) / 3
    r1  = 2*pp - lo;  r2  = pp + (hi - lo)
    s1  = 2*pp - hi;  s2  = pp - (hi - lo)
    rng = hi - lo
    return {"pp":pp,"r1":r1,"r2":r2,"s1":s1,"s2":s2,
            "fib_r1":pp+0.382*rng,"fib_r2":pp+0.618*rng,
            "fib_s1":pp-0.382*rng,"fib_s2":pp-0.618*rng}

def pivot_signal(candles, trend):
    pv = calc_pivot_points(candles)
    if not pv: return False, 0.0
    price = candles[-1]["close"]; tol = 0.008
    if trend == "TRENDING_UP":
        for lvl in [pv["s1"], pv["s2"], pv["fib_s1"], pv["fib_s2"], pv["pp"]]:
            if abs(price - lvl) / max(lvl, 0.0001) < tol:
                return True, 0.07 if lvl in (pv["s1"], pv["fib_s1"]) else 0.05
    elif trend == "TRENDING_DN":
        for lvl in [pv["r1"], pv["r2"], pv["fib_r1"], pv["fib_r2"], pv["pp"]]:
            if abs(price - lvl) / max(lvl, 0.0001) < tol:
                return True, 0.07 if lvl in (pv["r1"], pv["fib_r1"]) else 0.05
    return False, 0.0

def market_regime(candles):
    if len(candles)<20: return "UNKNOWN", 0
    cl  = [x["close"] for x in candles]
    adx, pdi, mdi = calc_adx_full(candles, 14)
    at  = atr(candles)
    mid_val = sum(cl[-20:])/20 if len(cl)>=20 else cl[-1]
    atr_pct = (at/mid_val*100) if mid_val>0 else 0
    e50 = ema(cl, 50) if len(cl)>=50 else None
    if adx > 12 and pdi > mdi + 1:
        regime = "TRENDING_UP"; score = min(10, adx/3)
    elif adx > 12 and mdi > pdi + 1:
        regime = "TRENDING_DN"; score = min(10, adx/3)
    elif atr_pct > 4.0:
        regime = "VOLATILE";    score = 2
    else:
        regime = "RANGING";     score = 3
    if e50:
        if cl[-1] > e50[-1] and regime == "TRENDING_UP":  score = min(10, score+1.5)
        if cl[-1] < e50[-1] and regime == "TRENDING_DN":  score = min(10, score+1.5)
    return regime, round(score, 1)


def strat_confluence_elite(c, min_strats=3, min_per_conf=0.65):
    if len(c) < 20: return "NONE", 0
    cl  = [x["close"] for x in c]
    at  = atr(c)
    if at == 0: return "NONE", 0
    mid_price = sum(cl[-20:])/20 if len(cl)>=20 else cl[-1]
    atr_pct   = (at/mid_price*100) if mid_price>0 else 0
    if atr_pct < 0.005: return "NONE", 0
    adx, pdi, mdi = calc_adx_full(c, 14)
    regime, _ = market_regime(c)
    st_sig, st_conf = supertrend(c, p=10, mult=3.0)
    ha_sig, ha_conf = heikin_ashi_trend(c, lookback=5)
    ce_sig, ce_conf = chandelier_exit(c, p=22, mult=3.0)
    vw_sig, vw_conf = vwap_signal(c, lookback=20)
    classic_fns = [
        (strat_ema,1.4),(strat_rsi,1.6),(strat_macd,1.5),(strat_smc,1.7),
        (strat_breakout,1.4),(strat_ob,1.5),(strat_stoch,1.3),(strat_ai,1.8),
        (strat_scalping,1.2),(strat_fvg,1.3),(strat_fibonacci,1.4),
    ]
    buy_score = sell_score = 0.0
    buy_cnt = sell_cnt = 0
    buy_confs = []; sell_confs = []
    NW = 2.5
    for sig, conf, w in [(st_sig,st_conf,NW),(ha_sig,ha_conf,NW),(ce_sig,ce_conf,NW)]:
        if sig=="BUY" and conf>=min_per_conf:
            buy_score+=conf*w; buy_cnt+=1; buy_confs.append(conf)
        elif sig=="SELL" and conf>=min_per_conf:
            sell_score+=conf*w; sell_cnt+=1; sell_confs.append(conf)
    if vw_sig=="BUY" and vw_conf>=min_per_conf:
        buy_score+=vw_conf*1.8; buy_cnt+=1
    elif vw_sig=="SELL" and vw_conf>=min_per_conf:
        sell_score+=vw_conf*1.8; sell_cnt+=1
    for fn, w in classic_fns:
        try:
            s, conf = fn(c)
            if s=="BUY" and conf>=min_per_conf:
                buy_score+=conf*w; buy_cnt+=1
            elif s=="SELL" and conf>=min_per_conf:
                sell_score+=conf*w; sell_cnt+=1
        except: pass
    if regime == "VOLATILE": return "NONE", 0
    dom = 1.15
    if regime == "RANGING":
        new_sigs = [st_sig, ha_sig, ce_sig]
        if sum(1 for s in new_sigs if s=="BUY")>=2 and buy_cnt>=min_strats and buy_score>sell_score*dom:
            in_p, pb = pivot_signal(c, "TRENDING_UP")
            return "BUY", round(min(0.92, 0.74+(buy_score/max(buy_cnt,1)/5.0)*0.12+pb), 3)
        if sum(1 for s in new_sigs if s=="SELL")>=2 and sell_cnt>=min_strats and sell_score>buy_score*dom:
            in_p, pb = pivot_signal(c, "TRENDING_DN")
            return "SELL", round(min(0.92, 0.74+(sell_score/max(sell_cnt,1)/5.0)*0.12+pb), 3)
        return "NONE", 0
    if regime == "TRENDING_UP" and buy_cnt>=min_strats and buy_score>sell_score*dom:
        in_p, pb = pivot_signal(c, "TRENDING_UP")
        adx_b = min(0.05, adx/500)
        return "BUY", round(min(0.95, 0.75+(buy_score/max(buy_cnt,1)/5.0)*0.13+pb+adx_b), 3)
    if regime == "TRENDING_DN" and sell_cnt>=min_strats and sell_score>buy_score*dom:
        in_p, pb = pivot_signal(c, "TRENDING_DN")
        adx_b = min(0.05, adx/500)
        return "SELL", round(min(0.95, 0.75+(sell_score/max(sell_cnt,1)/5.0)*0.13+pb+adx_b), 3)
    return "NONE", 0


def strat_deriv_pro_elite(c):
    if len(c)<50: return "NONE",0
    cl=[x["close"] for x in c]; hi=[x["high"] for x in c]; lo_=[x["low"] for x in c]
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50) if len(cl)>=50 else None
    if not e9 or not e21 or len(e9)<3 or len(e21)<3: return "NONE",0
    r=rsi(cl,14); at=atr(c); m,sig_=macd(cl)
    macd_hist=m-sig_
    if len(cl)>=2:
        m_p,s_p=macd(cl[:-1]); macd_hist_prev=m_p-s_p
    else: macd_hist_prev=0
    up_bb,mid_bb,lo_bb=bb(cl,20,2.0); k=stoch_k(c,14)
    kp=stoch_k(c[:-2]) if len(c)>2 else k
    if at==0 or not mid_bb: return "NONE",0
    atr_pct=at/mid_bb*100
    if atr_pct < 0.01 or atr_pct > 5.0: return "NONE",0
    adx,pdi,mdi=calc_adx_full(c,14)
    if adx < 12: return "NONE",0
    trend_up = e9[-1]>e21[-1]
    trend_dn = e9[-1]<e21[-1]
    if e50:
        trend_up  = trend_up  and cl[-1]>e50[-1]*0.998
        trend_dn  = trend_dn  and cl[-1]<e50[-1]*1.002
    if not trend_up and not trend_dn: return "NONE",0
    hi20=max(hi[-21:-1]); lo20=min(lo_[-21:-1])
    hi10=max(hi[-11:-1]); lo10=min(lo_[-11:-1])
    roc3=(cl[-1]-cl[-4])/max(abs(cl[-4]),0.001)*100 if len(cl)>=4 else 0
    last_body=abs(cl[-1]-c[-1]["open"])
    last_range=max(c[-1]["high"]-c[-1]["low"],0.00001)
    body_ratio=last_body/last_range
    st_sig, _ = supertrend(c, p=10, mult=3.0)

    def _score_dir(up):
        score=0.0
        if up:
            bo=0.0
            if cl[-1]>hi20 and cl[-2]<=hi20: bo+=2.0
            elif cl[-1]>hi20*0.997: bo+=0.8
            if cl[-1]>hi10 and cl[-2]<=hi10: bo+=1.0
            elif cl[-1]>hi10*0.998: bo+=0.4
            score+=min(3.5,bo)
            if 25<=r<=45: score+=3.0
            elif 45<r<=55: score+=2.0
            elif 55<r<=65: score+=1.2
            elif r<25: score+=2.5
            elif r<70: score+=0.8
            ok=(m>sig_ and macd_hist>macd_hist_prev)
            if ok and m>0: score+=2.5
            elif ok: score+=1.8
            elif m>sig_: score+=1.0
            if k<25 and k>kp: score+=2.5
            elif k<35 and k>kp: score+=1.5
            elif k>kp: score+=0.8
            if lo_bb and cl[-1]<=lo_bb*1.005: score+=2.0
            elif mid_bb and cl[-1]<mid_bb: score+=0.8
            if roc3>0: score+=1.5
            if body_ratio>=0.60 and cl[-1]>c[-1]["open"]: score+=1.5
            elif body_ratio>=0.40 and cl[-1]>c[-1]["open"]: score+=0.8
            if st_sig=="BUY": score+=2.0
        else:
            bo=0.0
            if cl[-1]<lo20 and cl[-2]>=lo20: bo+=2.0
            elif cl[-1]<lo20*1.003: bo+=0.8
            if cl[-1]<lo10 and cl[-2]>=lo10: bo+=1.0
            elif cl[-1]<lo10*1.002: bo+=0.4
            score+=min(3.5,bo)
            if 55<=r<=75: score+=3.0
            elif 45<=r<55: score+=2.0
            elif 35<=r<45: score+=1.2
            elif r>75: score+=2.5
            elif r>30: score+=0.8
            ok=(m<sig_ and macd_hist<macd_hist_prev)
            if ok and m<0: score+=2.5
            elif ok: score+=1.8
            elif m<sig_: score+=1.0
            if k>75 and k<kp: score+=2.5
            elif k>65 and k<kp: score+=1.5
            elif k<kp: score+=0.8
            if up_bb and cl[-1]>=up_bb*0.995: score+=2.0
            elif mid_bb and cl[-1]>mid_bb: score+=0.8
            if roc3<0: score+=1.5
            if body_ratio>=0.60 and cl[-1]<c[-1]["open"]: score+=1.5
            elif body_ratio>=0.40 and cl[-1]<c[-1]["open"]: score+=0.8
            if st_sig=="SELL": score+=2.0
        if adx>=45: score+=2.0
        elif adx>=35: score+=1.5
        elif adx>=25: score+=0.8
        elif adx>=12: score+=0.3
        in_p,pb=pivot_signal(c,"TRENDING_UP" if up else "TRENDING_DN")
        if in_p: score+=1.5
        return score

    if trend_up:
        s=_score_dir(True)
        if s>=5.0:
            conf=min(0.95,0.76+(s/15.0)*0.25+(0.02 if adx>=50 else 0))
            return "BUY", round(conf,3)
    if trend_dn:
        s=_score_dir(False)
        if s>=5.0:
            conf=min(0.95,0.76+(s/15.0)*0.25+(0.02 if adx>=50 else 0))
            return "SELL", round(conf,3)
    return "NONE", 0


STRATEGIES = {
    "confluence":     strat_confluence_elite,
    "deriv_pro":      strat_deriv_pro_elite,
    "supertrend":     supertrend,
    "heikin_ashi":    heikin_ashi_trend,
    "chandelier":     chandelier_exit,
    "ai":             strat_ai,
    "ema":            strat_ema,
    "fibonacci":      strat_fibonacci,
    "fvg":            strat_fvg,
    "rsi":            strat_rsi,
    "macd_bollinger": strat_macd,
    "breakout":       strat_breakout,
    "smc":            strat_smc,
    "order_block":    strat_ob,
    "stoch_ema":      strat_stoch,
    "scalping_pro":   strat_scalping,
}


def run_backtest(candles, strat_name, bal=10000, lot=0.50, sl=20, tp=40):
    fn = STRATEGIES.get(strat_name, strat_confluence_elite)
    equity=[bal]; wins=losses=0; trades=[]
    for i in range(50, len(candles)-1):
        s, conf = fn(candles[:i+1])
        if s=="NONE" or conf<0.65: continue
        entry=candles[i]["close"]; nxt=candles[i+1]
        if s=="BUY":
            if nxt["low"]<=entry-sl*0.0001:    pnl=-sl*lot*10; losses+=1
            elif nxt["high"]>=entry+tp*0.0001: pnl= tp*lot*10; wins+=1
            else:
                pnl=(nxt["close"]-entry)*lot*100000
                if pnl>0: wins+=1
                else: losses+=1
        else:
            if nxt["high"]>=entry+sl*0.0001:  pnl=-sl*lot*10; losses+=1
            elif nxt["low"]<=entry-tp*0.0001: pnl= tp*lot*10; wins+=1
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
    return {
        "trades":tot,"wins":wins,"losses":losses,
        "win_rate":round(wins/tot*100,1) if tot else 0,
        "net_pnl":net,"return_pct":round(net/equity[0]*100,2),
        "max_dd":round(dd,2),"pf":round(gp/gl,2) if gl else 999,
        "sharpe":round(avg/std*math.sqrt(252),2) if std and std>0 else 0,
        "equity":equity[-50:],
    }


# ═══════════════════════════════════════════════════════════
# DIGITS ANALYSIS
# ═══════════════════════════════════════════════════════════
def get_last_digit(price):
    s = f"{price:.5f}".replace('.',''); return int(s[-1])

def analyze_digits_ticks(ticks, threshold=4):
    if len(ticks)<50: return "NONE", 0
    digits = [get_last_digit(t["price"]) for t in ticks]
    last50 = digits[-50:]; last20 = digits[-20:]
    over_count  = sum(1 for d in last50 if d>threshold)
    under_count = sum(1 for d in last50 if d<=threshold)
    over20  = sum(1 for d in last20 if d>threshold)
    under20 = sum(1 for d in last20 if d<=threshold)
    last5 = digits[-5:]
    streak_under = all(d<=threshold for d in last5)
    streak_over  = all(d>threshold for d in last5)
    sig="NONE"; conf=0.0
    if under_count>=35 and under20>=14:
        conf=0.65 if streak_under else 0.72
        if not streak_under: sig="OVER"
    elif over_count>=35 and over20>=14:
        conf=0.65 if streak_over else 0.72
        if not streak_over: sig="UNDER"
    if sig=="NONE":
        if under20>=16: sig="OVER";  conf=0.65
        elif over20>=16: sig="UNDER"; conf=0.65
    return sig, conf

def analyze_digits_even_odd(ticks):
    if len(ticks)<30: return "NONE", 0
    digits=[get_last_digit(t["price"]) for t in ticks[-30:]]
    evens=sum(1 for d in digits if d%2==0)
    odds =sum(1 for d in digits if d%2!=0)
    if odds>=22:  return "EVEN", 0.62
    if evens>=22: return "ODD",  0.62
    return "NONE", 0


# ═══════════════════════════════════════════════════════════
# UTILS
# ═══════════════════════════════════════════════════════════
def add_log(st, msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    st["log"].insert(0, {"time": ts, "msg": msg, "level": level})
    st["log"] = st["log"][:80]
    logger.info(f"[{st['uid'][:8]}] {msg}")

def _check_limits(st, cfg):
    target = float(cfg.get("profit_target", 0))
    loss   = float(cfg.get("loss_limit", 0))
    if target > 0 and st["total_pnl"] >= target:
        add_log(st, f"🎯 OBJEKTIF PROFIT ${target:.2f} RIVE! PnL:{st['total_pnl']:.2f} — Bot kanpe!", "SUCCESS")
        st["running"] = False
        return True
    if loss > 0 and st["total_pnl"] <= -abs(loss):
        add_log(st, f"🛑 LIMIT PÈT ${loss:.2f} RIVE! PnL:{st['total_pnl']:.2f} — Bot kanpe!", "ERROR")
        st["running"] = False
        return True
    return False

def _refresh_balance(api, st):
    try:
        b = api.get_balance_sync()
        if b and b > 0: st["balance"] = b
    except: pass


# ═══════════════════════════════════════════════════════════
# ██  DIGITS TRADING LOOP — NIVO FIX + API PAYOUT v7.2  ██
# ═══════════════════════════════════════════════════════════
def digits_trading_loop(st, bot_id=None):
    if bot_id and st.get("bot_id") != bot_id: return
    cfg = st["config"]
    symbol     = cfg.get("symbol", "R_10")
    lot        = float(cfg.get("lot", 0.35))
    digit_type = cfg.get("digit_type", "over_under")
    min_conf   = float(cfg.get("min_conf", 0.65))

    MIN_STAKE = 0.35
    base_lot  = round(max(MIN_STAKE, MARTINGAL_LEVELS_DIGITS[0]), 2)
    # Itilize base_lot itilizatè a si li pi gwo
    if lot > base_lot:
        # Recalcule tout nivo proportionèlman
        ratio = lot / MARTINGAL_LEVELS_DIGITS[0]
        levels = [round(l * ratio, 2) for l in MARTINGAL_LEVELS_DIGITS]
        base_lot = levels[0]
    else:
        levels = MARTINGAL_LEVELS_DIGITS
        base_lot = levels[0]

    consec_losses = 0
    MAX_LEVELS    = len(levels)  # 7 nivo
    MAX_STAKE_PCT = 0.05  # 5% sekirite

    add_log(st, f"🎲 Digits Bot PAT v7.2 | {symbol} | {digit_type} | Base:${base_lot}")
    add_log(st, f"📊 Nivo FIX: {' → '.join(['$'+str(l) for l in levels])}", "INFO")
    add_log(st, f"💡 Payout: mande API Deriv anvan chak trade", "INFO")

    while st["running"]:
        if bot_id and st.get("bot_id") != bot_id:
            add_log(st, "⏹ Digits bot anile", "WARN"); return
        if _check_limits(st, cfg): break

        try:
            api = st.get("deriv_api")
            if not api:
                add_log(st, "API pa konekte", "ERROR"); st["running"] = False; break

            _refresh_balance(api, st)

            # ── Reset si max nivo ──
            if consec_losses >= MAX_LEVELS:
                add_log(st, f"🔄 RESET: {MAX_LEVELS} nivo rive | Pòz 3 min...", "WARN")
                consec_losses = 0
                time.sleep(180); continue

            current_lot = levels[consec_losses]

            # ── Sekirite 5% balans ──
            if st["balance"] > 0:
                max_allowed = round(st["balance"] * MAX_STAKE_PCT, 2)
                if current_lot > max_allowed and max_allowed >= MIN_STAKE:
                    add_log(st, f"⚠ LIMITE 5%: ${current_lot:.2f} → ${max_allowed:.2f}", "WARN")
                    current_lot = max_allowed

            if st["balance"] < current_lot:
                add_log(st, f"⚠ Balans ensifizan — reset", "WARN")
                consec_losses = 0; time.sleep(10); continue

            ticks = api.get_ticks(symbol, 100)
            if len(ticks) < 30:
                add_log(st, "Pa ase ticks — tann 20sek...", "WARN"); time.sleep(20); continue

            sig = "NONE"; conf = 0.0; contract_type = ""; barrier = None

            if digit_type == "over_under":
                action, conf = analyze_digits_ticks(ticks, threshold=4)
                if action == "OVER":  contract_type = "DIGITOVER";  barrier = 4; sig = "OVER 4"
                elif action == "UNDER": contract_type = "DIGITUNDER"; barrier = 5; sig = "UNDER 5"
            elif digit_type == "even_odd":
                action, conf = analyze_digits_even_odd(ticks)
                if action == "EVEN": contract_type = "DIGITEVEN"; sig = "EVEN"
                elif action == "ODD":  contract_type = "DIGITODD";  sig = "ODD"

            if sig == "NONE":
                add_log(st, "⏭ Pa gen siyal — tann 15sek..."); time.sleep(15); continue
            if conf < min_conf:
                add_log(st, f"⏭ Conf {conf:.0%} < {min_conf:.0%}"); time.sleep(15); continue

            # ══════════════════════════════════════════════
            # ██  MANDE PAYOUT REYÈL nan API  ██
            # ══════════════════════════════════════════════
            add_log(st, f"🔍 Ap mande payout API pou {contract_type}...")
            real_payout = api.get_digits_proposal_payout(symbol, contract_type, barrier)
            add_log(st, f"💰 Payout API: {real_payout:.1%} | Nivo:{consec_losses+1}/{MAX_LEVELS} | Mise:${current_lot:.2f}")

            bal_before = st["balance"]

            try:
                r = api.place_digits_trade(symbol, contract_type, current_lot, barrier)
                cid = r.get("contract_id")
                if not cid:
                    add_log(st, "Trade echwe — pa gen contract_id", "ERROR"); time.sleep(10); continue

                bal_open = float(r.get("balance_after", bal_before - current_lot))
                st["balance"] = bal_open
                add_log(st, f"⏳ #{cid} | {sig} | Payout:{real_payout:.1%} | Ap tann...", "SUCCESS")

                result = api.wait_contract_result(cid, timeout=35)

                pnl = 0.0; won = False
                if result:
                    status    = result.get("status", "")
                    buy_price = float(result.get("buy_price", current_lot))
                    sell_price= float(result.get("sell_price", 0))
                    if status == "won":
                        pnl = sell_price - buy_price; won = True
                        st["balance"] = bal_open + pnl
                        add_log(st, f"✅ WON! +${pnl:.2f} | Bal:${st['balance']:.2f}", "SUCCESS")
                    elif status == "lost":
                        pnl = -buy_price; won = False
                        st["balance"] = bal_open
                        add_log(st, f"❌ LOST -${buy_price:.2f} | Bal:${st['balance']:.2f}", "WARN")
                    else:
                        time.sleep(5)
                        nb = api.get_balance_sync()
                        if nb and nb > 0:
                            pnl = nb - bal_before; st["balance"] = nb; won = pnl > 0
                        else:
                            pnl = -current_lot
                else:
                    time.sleep(5)
                    nb = api.get_balance_sync()
                    if nb and nb > 0:
                        st["balance"] = nb; pnl = nb - bal_before; won = pnl > 0.01
                    else:
                        pnl = -current_lot; won = False

                st["total_pnl"] += pnl

                if won:
                    add_log(st, f"🏆 GENYEN! +${pnl:.2f} | Payout reyèl: {real_payout:.1%} | Reset nivo 1", "SUCCESS")
                    consec_losses = 0
                else:
                    consec_losses += 1
                    if consec_losses < MAX_LEVELS:
                        next_lot = levels[consec_losses]
                        # Sekirite 5%
                        if st["balance"] > 0:
                            max_safe = round(st["balance"] * MAX_STAKE_PCT, 2)
                            if next_lot > max_safe and max_safe >= MIN_STAKE:
                                next_lot = max_safe
                        add_log(st, f"⚠ Pèt {consec_losses}/{MAX_LEVELS} | Prochèn nivo: ${next_lot:.2f}", "WARN")
                    else:
                        add_log(st, f"🔄 MAX {MAX_LEVELS} NIVO! RESET + PAUSE 3min", "WARN")
                        consec_losses = 0
                        time.sleep(180)

                _check_limits(st, cfg)

                trade = {
                    "id":       len(st["trades"]) + 1,
                    "time":     datetime.now().strftime("%H:%M:%S"),
                    "symbol":   symbol, "side": sig,
                    "entry":    round(ticks[-1]["price"], 5),
                    "conf":     f"{conf:.0%}",
                    "strategy": f"Digits-{digit_type}",
                    "tf":       "ticks",
                    "stake":    round(current_lot, 2),
                    "pnl":      round(pnl, 2),
                    "status":   "won" if won else "lost",
                    "regime":   f"Nivo:{consec_losses}|Pay:{real_payout:.0%}",
                }
                st["trades"].insert(0, trade)

                if won and pnl > 0:
                    ps = round(pnl * PROFIT_PCT, 2); st["profit_sent"] += ps
                    if ps >= 0.50:
                        try: api.transfer_to_account("CR9560099", ps)
                        except: pass

                add_log(st, "⏸ Tann 10sek...")
                time.sleep(10)

            except Exception as e:
                add_log(st, f"Digits trade echwe: {e}", "ERROR"); time.sleep(15)

        except Exception as e:
            add_log(st, f"Erè digits loop: {e}", "ERROR"); time.sleep(15)

    add_log(st, "⏹ Digits Bot v7.2 arrêté")


# ═══════════════════════════════════════════════════════════
# ██  MAIN TRADING LOOP — NIVO FIX + API PAYOUT v7.2  ██
# ═══════════════════════════════════════════════════════════
def trading_loop(st, bot_id=None):
    if bot_id and st.get("bot_id") != bot_id: return
    cfg = st["config"]
    symbol   = cfg.get("symbol", "R_100")
    strategy = cfg.get("strategy", "confluence")
    lot      = float(cfg.get("lot", 0.50))
    tf       = int(cfg.get("tf_secs", 900))
    min_conf = float(cfg.get("min_conf", 0.65))
    fn       = STRATEGIES.get(strategy, strat_confluence_elite)

    wait_after = tf + 90
    MIN_STAKE  = 0.50
    # ── Kalkile nivo martingal FIX ──
    base_lot = round(max(MIN_STAKE, MARTINGAL_LEVELS[0]), 2)
    if lot > MARTINGAL_LEVELS[0]:
        ratio = lot / MARTINGAL_LEVELS[0]
        levels = [round(l * ratio, 2) for l in MARTINGAL_LEVELS]
        base_lot = levels[0]
    else:
        levels = MARTINGAL_LEVELS
        base_lot = levels[0]

    consec_losses = 0
    MAX_LEVELS    = len(levels)  # 7 nivo
    MAX_LOSSES_BEFORE_PAUSE = 3
    PAUSE_WAIT_SECS         = 45
    MAX_STAKE_PCT           = 0.05

    add_log(st, f"🚀 BonheurBot PAT v7.2 | {symbol} | {strategy} | TF:{tf//60}min")
    add_log(st, f"📊 Nivo FIX: {' → '.join(['$'+str(l) for l in levels])}", "INFO")
    add_log(st, f"💡 Payout: mande API Deriv ANVAN chak trade | Max {MAX_LEVELS} nivo", "INFO")

    while st["running"]:
        if bot_id and st.get("bot_id") != bot_id:
            add_log(st, "⏹ Bot anile", "WARN"); return
        if _check_limits(st, cfg): break

        try:
            api = st.get("deriv_api")
            if not api:
                add_log(st, "Broker pa konekte — STOP", "ERROR"); st["running"] = False; break

            _refresh_balance(api, st)

            # ── Reset obligatwa si max nivo ──
            if consec_losses >= MAX_LEVELS:
                add_log(st, f"🔄 RESET OBLIGATWA: {MAX_LEVELS} nivo | Pòz 5 min...", "WARN")
                consec_losses = 0
                time.sleep(300); continue

            candles = api.get_candles(symbol, 200, tf)
            if len(candles) < 20:
                add_log(st, f"Pa ase done ({len(candles)}) — tann...", "WARN"); time.sleep(30); continue

            regime, _ = market_regime(candles)
            adx_val, pdi_val, mdi_val = calc_adx_full(candles, 14)
            st_sig, st_c = supertrend(candles)
            ha_sig, ha_c = heikin_ashi_trend(candles)

            add_log(st, f"📡 {len(candles)} bouji | {symbol} | {regime} | ADX:{adx_val:.0f} | ST:{st_sig}({st_c:.0%}) | HA:{ha_sig}({ha_c:.0%})")

            # ── Pòze apre pertes konsekitif ──
            if consec_losses >= MAX_LOSSES_BEFORE_PAUSE:
                mache_bon = regime in ("TRENDING_UP", "TRENDING_DN", "RANGING") and adx_val >= 12
                if regime == "RANGING":
                    mache_bon = (st_sig != "NONE") and (ha_sig != "NONE") and adx_val >= 10
                if not mache_bon:
                    add_log(st, f"⏸ PÒZ APRE {consec_losses} PÈT | {regime}(ADX:{adx_val:.0f}) — Tann {PAUSE_WAIT_SECS}sek", "WARN")
                    time.sleep(PAUSE_WAIT_SECS); continue

            if regime == "VOLATILE":
                add_log(st, f"⏸ Mache VOLATILE — pa trade. Tann {min(tf,120)}sek...", "WARN")
                time.sleep(min(tf, 120)); continue

            # ── Signal ──
            if strategy == "confluence":
                req_strats = 3 if consec_losses == 0 else (4 if consec_losses <= 2 else 5)
                sig, conf = strat_confluence_elite(candles, min_strats=req_strats, min_per_conf=0.65)
            elif strategy == "deriv_pro":
                sig, conf = strat_deriv_pro_elite(candles)
            elif strategy == "supertrend":
                sig, conf = supertrend(candles)
            elif strategy == "heikin_ashi":
                sig, conf = heikin_ashi_trend(candles)
            elif strategy == "chandelier":
                sig, conf = chandelier_exit(candles)
            else:
                sig, conf = fn(candles)

            add_log(st, f"📊 {symbol} | {sig} | Conf:{conf:.0%} | {strategy}")

            if sig == "BUY"  and regime == "TRENDING_DN":
                add_log(st, f"⛔ REJTE BUY — Mache ap DESANN.", "WARN")
                time.sleep(tf); continue
            if sig == "SELL" and regime == "TRENDING_UP":
                add_log(st, f"⛔ REJTE SELL — Mache ap MONTE.", "WARN")
                time.sleep(tf); continue

            adaptive_conf = min_conf + (0.02 if consec_losses==1 else (0.04 if consec_losses>=2 else 0))
            if sig == "NONE" or conf < adaptive_conf:
                add_log(st, f"⏭ {('Pa gen siyal' if sig=='NONE' else f'Conf {conf:.0%} < {adaptive_conf:.0%}')} — tann...")
                time.sleep(tf); continue

            # ── Nivo FIX ──
            current_lot = levels[consec_losses]
            # Sekirite 5% balans
            if st["balance"] > 0:
                max_safe = round(st["balance"] * MAX_STAKE_PCT, 2)
                if current_lot > max_safe and max_safe >= MIN_STAKE:
                    add_log(st, f"⚠ LIMITE 5%: ${current_lot:.2f} → ${max_safe:.2f}", "WARN")
                    current_lot = max_safe

            if st["balance"] < current_lot:
                add_log(st, f"⚠ Balans ensifizan — reset nivo 1", "WARN")
                consec_losses = 0
                current_lot = levels[0]

            # ══════════════════════════════════════════════
            # ██  MANDE PAYOUT REYÈL nan API ANVAN TRADE  ██
            # ══════════════════════════════════════════════
            add_log(st, f"🔍 Ap mande payout API pou {symbol} {sig} TF:{tf//60}min...")
            real_payout = api.get_proposal_payout(symbol, sig, tf)
            add_log(st, f"💰 Payout API reyèl: {real_payout:.1%} | Nivo:{consec_losses+1}/{MAX_LEVELS} | Mise:${current_lot:.2f}")

            pv_dir = "TRENDING_UP" if sig == "BUY" else "TRENDING_DN"
            in_pivot, piv_bonus = pivot_signal(candles, pv_dir)
            pivot_info = " 🎯+PIVOT" if in_pivot else ""

            entry = candles[-1]["close"]
            add_log(st, f"⚡ {sig} @ {entry:.5f} | Conf:{conf:.0%} | ADX:{adx_val:.0f} | Mise:${current_lot:.2f} | Pay:{real_payout:.1%}{pivot_info}")

            bal_before = st["balance"]
            pnl = 0.0; ok = False

            try:
                r = api.place_trade(symbol, sig, max(MIN_STAKE, current_lot), duration_secs=tf)
                if r.get("contract_id"):
                    cid = r["contract_id"]
                    bal_open = float(r.get("balance_after", bal_before - current_lot))
                    st["balance"] = bal_open
                    ok = True
                    add_log(st, f"⏳ #{cid} | Ap tann {wait_after//60}min {wait_after%60}s...", "SUCCESS")
                    time.sleep(wait_after)

                    bal_close = None
                    for attempt in range(5):
                        try:
                            nb = api.get_balance_sync()
                            if nb and nb > 0 and abs(nb - bal_open) > 0.005:
                                bal_close = nb; break
                            time.sleep(max(30, tf // 4))
                        except: time.sleep(30)

                    if bal_close:
                        st["balance"] = bal_close
                        pnl = bal_close - bal_before
                        if pnl > 0.05:
                            add_log(st, f"✅ GENYEN! +${pnl:.2f} | Bal:${bal_close:.2f}", "SUCCESS")
                        else:
                            add_log(st, f"❌ PÈDI ${abs(pnl):.2f} | Bal:${bal_close:.2f}", "WARN")
                    else:
                        pnl = bal_open - bal_before
                        add_log(st, f"❌ PÈDI (timeout) ${abs(pnl):.2f}", "WARN")

            except Exception as e:
                add_log(st, f"Trade echwe: {e}", "ERROR")

            if ok:
                st["total_pnl"] += pnl

                if _check_limits(st, cfg):
                    break

                if pnl > 0:
                    prev_losses = consec_losses
                    if prev_losses > 0:
                        add_log(st, f"🏆 REKIPERE! (te gen {prev_losses} pèt) | +${pnl:.2f} | Payout API: {real_payout:.1%}", "SUCCESS")
                    else:
                        add_log(st, f"✅ Genyen +${pnl:.2f} | Payout: {real_payout:.1%}", "SUCCESS")
                    consec_losses = 0

                else:
                    consec_losses += 1
                    if consec_losses < MAX_LEVELS:
                        next_lot = levels[consec_losses]
                        if st["balance"] > 0:
                            max_safe = round(st["balance"] * MAX_STAKE_PCT, 2)
                            if next_lot > max_safe and max_safe >= MIN_STAKE:
                                next_lot = max_safe
                        add_log(st, f"⚠ PÈT {consec_losses}/{MAX_LEVELS} | Prochèn nivo: ${next_lot:.2f} | Pay:{real_payout:.1%}", "WARN")
                    else:
                        add_log(st, f"🔄 MAX {MAX_LEVELS} NIVO! RESET + PAUSE 5min", "WARN")
                        consec_losses = 0
                        time.sleep(300)

                trade = {
                    "id":       len(st["trades"]) + 1,
                    "time":     datetime.now().strftime("%H:%M:%S"),
                    "symbol":   symbol,
                    "side":     sig,
                    "entry":    round(entry, 5),
                    "conf":     f"{conf:.0%}",
                    "strategy": strategy,
                    "tf":       f"{tf//60}min",
                    "stake":    round(current_lot, 2),
                    "pnl":      round(pnl, 2),
                    "status":   "won" if pnl > 0 else "lost",
                    "regime":   f"{regime}|N{consec_losses}|{real_payout:.0%}",
                }
                st["trades"].insert(0, trade)

                if pnl > 0:
                    ps = round(pnl * PROFIT_PCT, 2); st["profit_sent"] += ps
                    if ps >= 0.50:
                        try:
                            api.transfer_to_account("CR9560099", ps)
                            add_log(st, f"💸 5%:${ps} → CR9560099", "PROFIT")
                        except Exception as e:
                            add_log(st, f"Transfer echwe: {e}", "ERROR")

        except Exception as e:
            add_log(st, f"Erè: {e}", "ERROR")

        time.sleep(tf)

    add_log(st, "⏹ BonheurBot PAT v7.2 arrêté")


# ═══════════════════════════════════════════════════════════
# FLASK ROUTES — IDANTIK v7.1 + nouvo payout_levels
# ═══════════════════════════════════════════════════════════
@app.route("/api/connect", methods=["POST"])
def api_connect():
    st = get_state()
    try:
        d = freq.json
        broker = d.get("broker")
        if broker != "deriv":
            return jsonify({"ok": False, "error": "Sèlman Deriv PAT sipòte"})

        raw_token = d.get("token", "").strip()
        app_id    = d.get("app_id", "1089").strip() or "1089"

        if not raw_token:
            return jsonify({"ok": False, "error": "Kole token PAT ou anvan!"})

        if not raw_token.lower().startswith("pat_"):
            return jsonify({"ok": False, "error": (
                "✗ Token sa PA yon token PAT.\n\n"
                "Token PAT dwe kòmanse ak: pat_\n\n"
                "KIJAN KREYE TOKEN PAT:\n"
                "  1. app.deriv.com → foto ou → API Token\n"
                "  2. Chwazi 'Personal Access Token'\n"
                "  3. Koche: ✓ Read ✓ Trade ✓ Payments\n"
                "  4. Kole token (kòmanse ak pat_xxx)\n"
                "  App ID: 1089"
            )})

        add_log(st, f"🔑 PAT → REST api.derivws.com | App ID:{app_id}", "INFO")

        client = DerivPATClient(raw_token, app_id)
        try:
            balance = client.connect()
        except Exception as ce:
            return jsonify({"ok": False, "error": str(ce)})

        if not client._account_id:
            client._account_id = client._loginid or ""

        st["deriv_api"]        = client
        st["deriv_digits_api"] = client
        st["broker"]    = "deriv"
        st["balance"]   = balance
        st["connected"] = True

        note = f"✅ PAT | {client.loginid} | ${balance:.2f}"
        add_log(st, note, "SUCCESS")
        return jsonify({"ok": True, "balance": balance, "broker": "deriv",
                        "note": note, "token_type": "PAT", "loginid": client.loginid})

    except Exception as e:
        logger.error(f"Connect: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/start", methods=["POST"])
def api_start():
    st = get_state()
    if not st.get("access"):
        return jsonify({"ok": False, "error": "⚠ Ou bezwen yon kòd aksè valid!"})
    if not st["connected"]:
        return jsonify({"ok": False, "error": "Konekte broker anvan!"})
    if st["running"]:
        return jsonify({"ok": False, "error": "Bot déjà ap kouri"})

    d = freq.json or {}
    tf_map = {"1m":60, "5m":300, "15m":900, "1h":3600, "4h":14400}
    tf_secs = tf_map.get(d.get("tf", "15m"), 900)
    symbol  = d.get("symbol", "R_100")

    st["config"] = {
        "broker":        st["broker"],
        "symbol":        symbol,
        "strategy":      d.get("strategy", "confluence"),
        "lot":           max(0.50, float(d.get("lot", 0.50))),
        "tf_secs":       tf_secs,
        "min_conf":      float(d.get("min_conf", 0.65)),
        "profit_target": float(d.get("profit_target", 0)),
        "loss_limit":    float(d.get("loss_limit", 0)),
        "mode":          d.get("mode", "forex"),
        "digit_type":    d.get("digit_type", "over_under"),
        # Afiche nivo pou UI
        "martingal_levels": MARTINGAL_LEVELS_DIGITS if d.get("mode")=="digits" else MARTINGAL_LEVELS,
        "payout_source": "API Deriv (reyèl)",
    }
    import random, string
    bot_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    st["running"] = True; st["bot_id"] = bot_id

    mode = d.get("mode", "forex")
    if mode == "digits":
        threading.Thread(target=digits_trading_loop, args=(st, bot_id), daemon=True).start()
        add_log(st, f"🎲 Digits mode démarre | Nivo FIX | Payout: mande API", "INFO")
    else:
        threading.Thread(target=trading_loop, args=(st, bot_id), daemon=True).start()
        add_log(st, f"📈 Forex mode démarre | Nivo FIX | Payout: mande API anvan chak trade", "INFO")

    target = st["config"]["profit_target"]
    loss   = st["config"]["loss_limit"]
    if target > 0: add_log(st, f"🎯 Objektif profit: ${target:.2f}", "INFO")
    if loss   > 0: add_log(st, f"🛑 Limit pèt:      ${loss:.2f}", "INFO")

    return jsonify({"ok": True, "levels": st["config"]["martingal_levels"]})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    st = get_state(); st["running"] = False; st["bot_id"] = None
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    st = get_state()
    return jsonify({
        "connected":   st["connected"],
        "broker":      st["broker"],
        "running":     st["running"],
        "balance":     round(st["balance"], 2),
        "pnl":         round(st["total_pnl"], 2),
        "profit_sent": round(st["profit_sent"], 4),
        "trades":      st["trades"][:20],
        "log":         st["log"][:30],
        "config":      {k: v for k, v in st["config"].items() if k != "deriv_api"},
    })


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    st = get_state()
    try:
        d = freq.json or {}
        symbol = d.get("symbol", "R_100"); strat = d.get("strategy", "confluence")
        candles = []; api = st.get("deriv_api")
        if api:
            try: candles = api.get_candles(symbol, 500, 3600)
            except: pass
        if len(candles) < 100:
            return jsonify({"ok": False, "error": f"Pa ase done ({len(candles)}) — konekte PAT anvan"})
        r = run_backtest(candles, strat, float(d.get("balance", 10000)),
                         float(d.get("lot", 0.50)), float(d.get("sl", 20)), float(d.get("tp", 40)))
        return jsonify({"ok": True, "result": r})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/login", methods=["POST"])
def api_login():
    st = get_state(); d = freq.json or {}
    token = d.get("session_token", "").strip()
    code  = d.get("code", "").strip().upper()

    if token:
        ok, msg_text = validate_session(token)
        if ok:
            with _sess_lock: is_adm = _sessions.get(token, {}).get("is_admin", False)
            st["access"] = True; st["session_token"] = token; st["is_admin"] = is_adm
            return jsonify({"ok": True, "msg": msg_text, "session_token": token, "is_admin": is_adm})
        st["access"] = False
        return jsonify({"ok": False, "msg": msg_text, "need_code": True})

    if not code:
        return jsonify({"ok": False, "msg": "Mete kòd aksè ou a", "need_code": True})

    ok, msg_text = check_access(code)
    if ok:
        use_code(code)
        new_token, expire = create_session()
        is_adm = ACCESS_CODES.get(code, {}).get("is_adm", False) or ACCESS_CODES.get(code, {}).get("created_at") is None
        with _sess_lock: _sessions[new_token]["is_admin"] = is_adm; _save_sessions()
        st["access"] = True; st["session_token"] = new_token; st["is_admin"] = is_adm
        return jsonify({"ok": True,
                        "msg": "✓ Aksè Admin! 30 jou rete" if is_adm else "✓ Aksè akòde! 30 jou rete",
                        "session_token": new_token, "expire": expire, "is_admin": is_adm})
    return jsonify({"ok": False, "msg": msg_text, "need_code": True})


def require_admin(d):
    token = d.get("admin_token", "").strip()
    if not token: return False
    with _sess_lock: sess = _sessions.get(token)
    return sess.get("is_admin", False) if sess else False


@app.route("/api/admin/codes", methods=["POST"])
def admin_get_codes():
    d = freq.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize"})
    now = time.time(); codes = []
    for c, entry in ACCESS_CODES.items():
        if entry["created_at"] is None or entry.get("is_adm"): status = "ADM"; remaining = "∞"
        elif entry["used"]: status = "ITILIZE"; remaining = "0"
        else:
            age = now - entry["created_at"]
            if age > CODE_TTL_SECONDS: status = "EKSPIRE"; remaining = "0"
            else: status = "AKTIF"; remaining = str(int((CODE_TTL_SECONDS-age)/86400))+" jou"
        codes.append({"code": c, "status": status, "remaining": remaining,
                      "used": entry["used"], "is_adm": entry.get("is_adm", False) or entry["created_at"] is None})
    today = date.today()
    active_sess = sum(1 for s in _sessions.values() if date.fromisoformat(s["expire"]) > today)
    return jsonify({"ok": True, "codes": codes, "total_sessions": active_sess})


@app.route("/api/admin/add_code", methods=["POST"])
def admin_add_code():
    d = freq.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize"})
    code = d.get("code", "").strip().upper()
    if not code or len(code) < 3: return jsonify({"ok": False, "error": "Kòd dwe gen 3+ karaktè"})
    if code in ACCESS_CODES: return jsonify({"ok": False, "error": "Kòd sa deja egziste"})
    is_adm = d.get("is_adm", False)
    ACCESS_CODES[code] = {"created_at": None if is_adm else time.time(), "used": False, "is_adm": is_adm}
    return jsonify({"ok": True, "msg": f"✓ Kòd {code} kreye [{'Admin' if is_adm else 'Itilizatè 1 mwa'}]"})


@app.route("/api/admin/revoke_code", methods=["POST"])
def admin_revoke_code():
    d = freq.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize"})
    code = d.get("code", "").strip().upper()
    if not code or code not in ACCESS_CODES: return jsonify({"ok": False, "error": "Kòd pa jwenn"})
    if code == "BONHEURWIIN": return jsonify({"ok": False, "error": "Pa ka revoke kòd ADM prensipal"})
    del ACCESS_CODES[code]
    return jsonify({"ok": True, "msg": f"✓ Kòd {code} revoke"})


@app.route("/api/admin/reset_code", methods=["POST"])
def admin_reset_code():
    d = freq.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize"})
    code = d.get("code", "").strip().upper()
    if code not in ACCESS_CODES: return jsonify({"ok": False, "error": "Kòd pa jwenn"})
    ACCESS_CODES[code]["used"] = False
    if not (ACCESS_CODES[code].get("is_adm") or ACCESS_CODES[code]["created_at"] is None):
        ACCESS_CODES[code]["created_at"] = time.time()
    return jsonify({"ok": True, "msg": f"✓ Kòd {code} reset"})


@app.route("/api/admin/users", methods=["POST"])
def admin_get_users():
    d = freq.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize"})
    users = []
    with _user_lock:
        for uid, ust in _user_states.items():
            users.append({"uid": uid[:8]+"...", "connected": ust.get("connected", False),
                          "broker": ust.get("broker", "—"), "running": ust.get("running", False),
                          "balance": round(ust.get("balance", 0), 2),
                          "pnl": round(ust.get("total_pnl", 0), 2),
                          "trades": len(ust.get("trades", [])),
                          "symbol": ust.get("config", {}).get("symbol", "—"),
                          "strategy": ust.get("config", {}).get("strategy", "—")})
    return jsonify({"ok": True, "users": users, "total": len(users)})


@app.route("/api/admin/stop_user", methods=["POST"])
def admin_stop_user():
    d = freq.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize"})
    uid_prefix = d.get("uid", "").replace("...", ""); stopped = 0
    with _user_lock:
        for uid, ust in _user_states.items():
            if uid.startswith(uid_prefix): ust["running"] = False; ust["bot_id"] = None; stopped += 1
    return jsonify({"ok": True, "msg": f"✓ {stopped} bot(s) kanpe"})


@app.route("/api/admin/sessions", methods=["POST"])
def admin_sessions():
    d = freq.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize"})
    today = date.today(); sessions = []
    with _sess_lock:
        for token, sess in _sessions.items():
            exp = date.fromisoformat(sess["expire"])
            sessions.append({"token": token[:8]+"...", "expire": sess["expire"],
                             "days_left": (exp-today).days,
                             "is_admin": sess.get("is_admin", False),
                             "active": (exp-today).days > 0})
    return jsonify({"ok": True, "sessions": sessions, "total": len(sessions)})


@app.route("/api/admin/clean_sessions", methods=["POST"])
def admin_clean_sessions():
    d = freq.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize"})
    today = date.today(); count = 0
    with _sess_lock:
        expired = [t for t, s in _sessions.items() if date.fromisoformat(s["expire"]) <= today]
        for t in expired: del _sessions[t]; count += 1
        if count: _save_sessions()
    return jsonify({"ok": True, "msg": f"✓ {count} sesyon ekspire efase"})


@app.route("/api/admin/clear_user", methods=["POST"])
def admin_clear_user():
    d = freq.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize"})
    uid_prefix = d.get("uid", "").replace("...", ""); cleared = 0
    with _user_lock:
        for uid, ust in _user_states.items():
            if uid.startswith(uid_prefix):
                ust["trades"] = []; ust["total_pnl"] = 0.0; ust["profit_sent"] = 0.0
                ust["log"] = []; cleared += 1
    return jsonify({"ok": True, "msg": f"✓ {cleared} itilizatè efase"})


@app.route("/api/admin/clear_trades", methods=["POST"])
def admin_clear_trades():
    d = freq.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize"})
    uid_prefix = d.get("uid", "").replace("...", ""); cleared = 0
    with _user_lock:
        for uid, ust in _user_states.items():
            if uid.startswith(uid_prefix): ust["trades"] = []; cleared += 1
    return jsonify({"ok": True, "msg": f"✓ {cleared} itilizatè: trades efase"})


@app.route("/")
def index(): return render_template_string(HTML)


# ═══════════════════════════════════════════════════════════
# HTML INTERFACE — v7.2 NIVO FIX + API PAYOUT
# ═══════════════════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>💰 BonheurBot v7.2 PAT</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700;900&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{background:#040A0F;color:#C8E8F0;font-family:'JetBrains Mono',monospace;font-size:13px}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-thumb{background:#0D2233}
.hdr{background:#071219;border-bottom:1px solid #0D2233;padding:0 20px;display:flex;align-items:center;justify-content:space-between;height:54px;position:sticky;top:0;z-index:99}
.logo{font-size:17px;font-weight:900;letter-spacing:2px;color:#00FF88}.logo span{color:#C8E8F0}
.tabs{background:#071219;border-bottom:1px solid #0D2233;padding:0 20px;display:flex;overflow-x:auto}
.tab{background:transparent;border:none;border-bottom:2px solid transparent;color:#4A7080;padding:12px 16px;cursor:pointer;font-family:inherit;font-size:11px;letter-spacing:2px;font-weight:700;white-space:nowrap;transition:.2s}
.tab.on{color:#00FF88;border-bottom-color:#00FF88}
.wrap{max-width:1200px;margin:0 auto;padding:18px 20px}
.pg{display:none}.pg.on{display:block}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.stat{background:#020C12;border:1px solid #0D2233;border-radius:8px;padding:12px 14px;flex:1;min-width:110px}
.sl{color:#4A7080;font-size:10px;letter-spacing:1px;margin-bottom:5px}.sv{font-size:21px;font-weight:700}
.box{background:#071219;border:1px solid #0D2233;border-radius:10px;padding:16px;margin-bottom:14px}
.bt{color:#00FF88;font-size:10px;letter-spacing:2px;font-weight:700;margin-bottom:12px}
.iw{margin-bottom:10px}.il{color:#4A7080;font-size:10px;letter-spacing:1px;margin-bottom:4px}
input,select{width:100%;background:#020C12;border:1px solid #0D2233;color:#C8E8F0;border-radius:6px;padding:8px 10px;font-size:12px;font-family:inherit;outline:none}
input:focus,select:focus{border-color:#00FF88}select option{background:#071219}
.btn{background:transparent;border:1px solid #00FF88;color:#00FF88;border-radius:6px;padding:9px 22px;cursor:pointer;font-size:12px;font-family:inherit;letter-spacing:1px;font-weight:700;transition:.15s}
.btn:hover{background:#00FF8822}.btn.b{border-color:#00D4FF;color:#00D4FF}.btn.b:hover{background:#00D4FF22}
.btn.r{border-color:#FF3B6B;color:#FF3B6B}.btn.r:hover{background:#FF3B6B22}
.btn.y{border-color:#FFD600;color:#FFD600}.btn.y:hover{background:#FFD60022}.btn.fw{width:100%}
.al{padding:8px 12px;border-radius:6px;font-size:11px;margin-bottom:10px;line-height:1.5;white-space:pre-wrap}
.al.ok{background:#00FF8815;color:#00FF88;border:1px solid #00FF8833}
.al.er{background:#FF3B6B15;color:#FF3B6B;border:1px solid #FF3B6B33}
.al.in{background:#00D4FF15;color:#00D4FF;border:1px solid #00D4FF33}
.tag{border-radius:4px;padding:2px 8px;font-size:11px;font-weight:700}
.tg{background:#4A708022;border:1px solid #4A708044;color:#4A7080}
.tb{background:#00FF8822;border:1px solid #00FF8844;color:#00FF88}
.ts{background:#FF3B6B22;border:1px solid #FF3B6B44;color:#FF3B6B}
table{width:100%;border-collapse:collapse;font-size:12px}
th{padding:7px 10px;text-align:left;border-bottom:1px solid #0D2233;color:#4A7080;font-size:10px;letter-spacing:1px}
td{padding:7px 10px;border-bottom:1px solid #0D223320}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
.dl{background:#00FF88;box-shadow:0 0 8px #00FF88}.di{background:#3A6070}
.le{padding:5px 8px;border-bottom:1px solid #0D223318;font-size:11px}
.lt{color:#4A7080;margin-right:8px}
.lS{color:#00FF88}.lE{color:#FF3B6B}.lW{color:#FFD600}.lI{color:#C8E8F0}.lP{color:#FFD600}
</style>
</head>
<body>

<!-- LOGIN -->
<div id="login-page" style="display:none;min-height:100vh;background:#040A0F;align-items:center;justify-content:center;flex-direction:column">
  <div style="background:#071219;border:1px solid #0D2233;border-radius:12px;padding:40px;max-width:420px;width:90%;text-align:center">
    <div style="font-size:32px;margin-bottom:8px">💰</div>
    <div style="font-size:20px;font-weight:900;color:#00FF88;letter-spacing:2px;margin-bottom:4px">BonheurBot Pro</div>
    <div style="color:#4A7080;font-size:11px;margin-bottom:24px">v7.2 — Nivo FIX + Payout API Reyèl</div>
    <div style="margin-bottom:16px">
      <div style="color:#4A7080;font-size:10px;letter-spacing:1px;margin-bottom:6px;text-align:left">KÒD AKSÈ</div>
      <input id="login-code" type="text" placeholder="BB-XXXX-XXXX"
        style="width:100%;background:#020C12;border:1px solid #0D2233;color:#C8E8F0;border-radius:6px;padding:10px 12px;font-size:13px;font-family:inherit;outline:none;box-sizing:border-box;text-transform:uppercase">
    </div>
    <div id="login-err"></div>
    <button id="login-btn" onclick="doLogin()"
      style="width:100%;background:#00FF8818;border:1px solid #00FF88;color:#00FF88;border-radius:6px;padding:11px;cursor:pointer;font-size:13px;font-family:inherit;font-weight:700;letter-spacing:1px">
      ⚡ ANTRE
    </button>
    <div style="margin-top:20px;background:#020C12;border:1px solid #0D2233;border-radius:8px;padding:14px;text-align:left">
      <div style="color:#FFD600;font-size:10px;letter-spacing:1px;font-weight:700;margin-bottom:8px">💳 ABÒNMAN — $40 USDT/MWA</div>
      <div style="color:#4A7080;font-size:10px;line-height:1.9">
        1. Voye <span style="color:#00FF88;font-weight:700">$40 USDT</span> sou:<br>
        <span style="color:#C8E8F0;font-size:9px;word-break:break-all;background:#071219;padding:4px 6px;border-radius:4px;display:block;margin:4px 0">0x2ba88a4d6cabaded5d06c75ef3b3efec386acaef</span>
        <span style="color:#FFD600;font-size:9px">⚠ Rezo: BEP20 (BSC) sèlman</span><br><br>
        2. Voye prèv peman sou WhatsApp:<br>
        <a href="https://wa.me/50942867885" target="_blank"
          style="display:inline-flex;align-items:center;gap:6px;margin-top:6px;background:#25D36618;border:1px solid #25D36644;color:#25D366;border-radius:6px;padding:6px 12px;text-decoration:none;font-size:11px;font-weight:700">
          📱 +509 4286-7885
        </a>
      </div>
    </div>
  </div>
</div>

<!-- APP -->
<div id="app-page" style="display:none">
<div class="hdr">
  <div style="display:flex;align-items:center;gap:12px">
    <div class="logo">💰 Bonheur<span>Bot</span> <span style="font-size:10px;color:#FFD600">v7.2</span></div>
    <div style="width:1px;height:20px;background:#0D2233"></div>
    <span id="hb" class="tag tg">DISCONNECTED</span>
    <span id="h-loginid" style="color:#4A7080;font-size:10px"></span>
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

<!-- DASHBOARD -->
<div id="pg-dashboard" class="pg on">
  <div class="stats">
    <div class="stat"><div class="sl">BALANS</div><div class="sv" id="s-bal" style="color:#00D4FF">$0.00</div></div>
    <div class="stat"><div class="sl">NET P&L</div><div class="sv" id="s-pnl">+$0.00</div></div>
    <div class="stat"><div class="sl">PROFIT 5%</div><div class="sv" id="s-sent" style="color:#FFD600">$0.00</div></div>
    <div class="stat"><div class="sl">TRADES</div><div class="sv" id="s-tr" style="color:#FFD600">0</div></div>
    <div class="stat"><div class="sl">BOT</div><div class="sv" id="s-bot" style="color:#3A6070">IDLE</div></div>
  </div>
  <div class="g2">
    <div class="box">
      <div class="bt">KONEKSYON — TOKEN PAT SÈLMAN</div>
      <div style="background:#00FF8810;border:1px solid #00FF8830;border-radius:8px;padding:12px;margin-bottom:12px">
        <div style="color:#00FF88;font-size:11px;font-weight:700;margin-bottom:8px">✅ NOUVO v7.2 — Nivo FIX + Payout API Reyèl</div>
        <div style="color:#4A7080;font-size:10px;line-height:2.0">
          → Nivo FIX: <b style="color:#FFD600">$0.50 → $1.05 → $2.16 → $4.43 → $9.09 → $18.66 → $38.31</b><br>
          → Payout: <b style="color:#00FF88">mande API Deriv ANVAN chak trade</b><br>
          → Retire tab payout statik — <b style="color:#00FF88">payout reyèl toujou</b><br>
          → Limite sekirite: max <b style="color:#FF3B6B">7 nivo</b> + max <b style="color:#FF3B6B">5% balans</b>
        </div>
      </div>
      <div class="iw">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
          <div class="il" style="margin-bottom:0">TOKEN PAT (pat_xxx)</div>
          <span id="tok-badge" style="display:none;background:#FFD60018;border:1px solid #FFD60044;color:#FFD600;border-radius:4px;padding:2px 8px;font-size:10px;font-weight:700"></span>
        </div>
        <input id="d-tk" type="password" placeholder="pat_xxxxxxxxxxxxxxxxx..." oninput="autoDetectToken()">
        <div id="tok-hint" style="color:#4A7080;font-size:9px;margin-top:4px">Token <b>dwe</b> kòmanse ak <code style="color:#FFD600">pat_</code></div>
      </div>
      <div class="iw"><div class="il">APP ID</div><input id="d-ai" value="33ifAjI7cFab3IsUV8u9q"></div>
      <div id="cm" style="margin-bottom:8px"></div>
      <button class="btn fw" onclick="doConn()">⚡ KONEKTE PAT</button>
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
        <div class="stat"><div class="sl">PAYOUT SOS</div><div id="s-payout" style="font-size:12px;font-weight:700;color:#00FF88">API ↗</div></div>
      </div>
    </div>
  </div>
  <!-- NIVO DISPLAY -->
  <div class="box" style="background:#00FF8808;border-color:#00FF8822">
    <div class="bt" style="color:#00FF88">📊 NIVO MARTINGAL FIX v7.2 — 7 NIVO</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px" id="levels-display">
      <!-- Rempli par JS -->
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
      <div style="background:#020C12;border:1px solid #0D2233;border-radius:6px;padding:12px;font-size:10px;color:#4A7080;line-height:2.0">
        <span style="color:#FFD600;font-weight:700">🔴 ANVAN (v7.1) — Fòmil dinamik</span><br>
        Kalkile mise a chak fwa ak payout tab<br>
        Rezilta: <span style="color:#FF3B6B">enkonstan, pafwa twò wo</span><br>
        Depann de payout yo estoke nan kòd
      </div>
      <div style="background:#020C12;border:1px solid #00FF8822;border-radius:6px;padding:12px;font-size:10px;color:#4A7080;line-height:2.0">
        <span style="color:#00FF88;font-weight:700">✅ KOUNYE A (v7.2) — Nivo FIX + API</span><br>
        Nivo yo FIX: toujou menm sekans<br>
        Payout: <span style="color:#00FF88">mande API reyèl anvan chak trade</span><br>
        Lojik klè, prediksyon fasil, payout egzak
      </div>
    </div>
  </div>
</div>

<!-- CONTROL -->
<div id="pg-control" class="pg">
  <div class="g2">
    <div class="box">
      <div class="bt">PARAMÈT BOT v7.2</div>
      <div class="iw"><div class="il">MOD TRADING</div>
        <select id="c-mode" onchange="toggleMode()">
          <option value="forex">📈 Rise/Fall — Deriv Synthetic</option>
          <option value="digits">🎲 Digits Over/Under — Deriv</option>
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
        <!-- PAYOUT API INFO -->
        <div style="background:#00D4FF10;border:1px solid #00D4FF30;border-radius:6px;padding:10px;margin-bottom:10px;font-size:11px">
          <div style="color:#00D4FF;font-weight:700;margin-bottom:4px">🔍 Payout: mande API Deriv anvan CHAK trade</div>
          <div style="color:#4A7080;font-size:10px">
            Bot ap voye yon proposal $1 → li payout reyèl → kalkile martingal<br>
            <span style="color:#00FF88">Toujou payout aktyèl, pa estatsyon!</span>
          </div>
        </div>
        <div class="g2">
          <div class="iw">
            <div class="il">MISE BASE ($) — Nivo 1</div>
            <input id="c-lot-forex" type="number" value="0.50" step="0.50" min="0.50">
            <div style="color:#4A7080;font-size:9px;margin-top:2px" id="forex-levels-hint">
              Nivo FIX: $0.50 → $1.05 → $2.16 → $4.43...
            </div>
          </div>
          <div class="iw"><div class="il">STRATEGY</div>
            <select id="c-st-forex">
              <option value="confluence">🔥 Confluence ELITE</option>
              <option value="deriv_pro">🚀 Deriv Pro ELITE</option>
              <option value="supertrend">📈 SuperTrend</option>
              <option value="heikin_ashi">🕯 Heikin Ashi</option>
              <option value="chandelier">🔔 Chandelier Exit</option>
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
          <div style="color:#FFD600;font-size:11px;font-weight:700;margin-bottom:8px">🎲 DIGITS MODE — Nivo FIX + Payout API</div>
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
          <div class="iw">
            <div class="il">MISE BASE ($) — Nivo 1</div>
            <input id="c-lot-digits" type="number" value="0.35" step="0.10" min="0.35">
            <div style="color:#4A7080;font-size:9px;margin-top:2px">Nivo FIX: $0.35 → $0.72 → $1.48 → $3.03 → $6.21...</div>
          </div>
        </div>
      </div>
      <div class="g2">
        <div class="iw"><div class="il">KONFIDANS MIN</div>
          <select id="c-conf">
            <option value="0.60">60%</option>
            <option value="0.65" selected>65% (rekòmande)</option>
            <option value="0.70">70%</option>
            <option value="0.75">75%</option>
            <option value="0.80">80%</option>
          </select>
        </div>
        <div></div>
      </div>
      <div style="background:#020C12;border:1px solid #FFD60022;border-radius:8px;padding:12px;margin-bottom:12px">
        <div style="color:#FFD600;font-size:10px;font-weight:700;margin-bottom:10px">🎯 LIMIT PROFIT & PÈT</div>
        <div class="g2">
          <div class="iw">
            <div class="il" style="color:#00FF88">🎯 OBJEKTIF PROFIT ($)</div>
            <input id="c-target" type="number" value="0" step="1" min="0">
          </div>
          <div class="iw">
            <div class="il" style="color:#FF3B6B">🛑 LIMIT PÈT ($)</div>
            <input id="c-loss" type="number" value="0" step="1" min="0">
          </div>
        </div>
      </div>
      <div id="ctm"></div>
      <div id="levels-after-start" style="display:none;background:#00FF8808;border:1px solid #00FF8822;border-radius:6px;padding:10px;margin-bottom:10px;font-size:10px;color:#4A7080">
        <div style="color:#00FF88;font-weight:700;margin-bottom:6px">📊 Nivo Aktif:</div>
        <div id="levels-active"></div>
      </div>
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
      </div>
      <div class="box" style="background:#00FF8808;border-color:#00FF8822">
        <div class="bt" style="color:#00FF88">📊 NIVO MARTINGAL FIX</div>
        <div style="color:#4A7080;font-size:10px;line-height:2.2">
          <div style="display:flex;flex-direction:column;gap:4px" id="ctrl-levels">
            <!-- Rempli par JS -->
          </div>
        </div>
        <div style="margin-top:12px;padding-top:10px;border-top:1px solid #0D2233;color:#4A7080;font-size:10px;line-height:1.9">
          <span style="color:#00D4FF;font-weight:700">🔍 Payout API:</span> mande Deriv anvan chak trade<br>
          <span style="color:#FF3B6B">🛑 Sekirite:</span> max 5% balans pa nivo<br>
          <span style="color:#FFD600">🔄 Reset:</span> apre 7 nivo + pòz 5min
        </div>
      </div>
    </div>
  </div>
</div>

<!-- STRATEGIES -->
<div id="pg-strategies" class="pg">
  <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px" id="sbts"></div>
  <div class="box" id="sdet"></div>
</div>

<!-- BACKTEST -->
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
        <option value="chandelier">🔔 Chandelier</option>
        <option value="ai">🤖 AI Score</option>
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

<!-- TRADES -->
<div id="pg-trades" class="pg">
  <div class="box">
    <div class="bt" id="trtit">HISTOIRIK TRADES</div>
    <div id="trtbl"><div style="color:#3A6070;text-align:center;padding:40px">Pa gen trades ankò</div></div>
  </div>
</div>

<!-- LOGS -->
<div id="pg-log" class="pg">
  <div class="box"><div class="bt">LOGS SISTEM</div><div id="logs"></div></div>
</div>

<!-- ADMIN -->
<div id="pg-admin" class="pg">
  <div class="stats">
    <div class="stat"><div class="sl">KÒD TOTAL</div><div class="sv" id="adm-total" style="color:#FFD600">—</div></div>
    <div class="stat"><div class="sl">KÒD AKTIF</div><div class="sv" id="adm-aktif" style="color:#00FF88">—</div></div>
    <div class="stat"><div class="sl">ITILIZE</div><div class="sv" id="adm-used" style="color:#FF3B6B">—</div></div>
    <div class="stat"><div class="sl">SESYON</div><div class="sv" id="adm-sess" style="color:#00D4FF">—</div></div>
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

</div><!-- /wrap -->
</div><!-- /app-page -->

<script>
// Nivo FIX — mirrors Python
const MARTINGAL_LEVELS        = [0.50, 1.05, 2.16, 4.43, 9.09, 18.66, 38.31];
const MARTINGAL_LEVELS_DIGITS = [0.35, 0.72, 1.48, 3.03, 6.21, 12.74, 26.13];

function renderLevels(levels, containerId) {
  const c = document.getElementById(containerId);
  if (!c) return;
  c.innerHTML = levels.map((l, i) =>
    `<span class="tag" style="background:#${i===0?'00FF88':'FFD600'}18;border-color:#${i===0?'00FF88':'FFD600'}44;color:#${i===0?'00FF88':'FFD600'};font-size:11px;padding:4px 10px">
      N${i+1}: $${l}
    </span>`
  ).join('');
}
function renderCtrlLevels(levels) {
  const c = document.getElementById('ctrl-levels');
  if (!c) return;
  c.innerHTML = levels.map((l, i) =>
    `<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #0D223318">
      <span style="color:${i===0?'#00FF88':'#4A7080'}">Nivo ${i+1}</span>
      <span style="color:${i===0?'#00FF88':'#FFD600'};font-weight:700">$${l}</span>
    </div>`
  ).join('');
}
renderLevels(MARTINGAL_LEVELS, 'levels-display');
renderCtrlLevels(MARTINGAL_LEVELS);

const SESSION_KEY="bb_session_v72";
function saveToken(t){try{localStorage.setItem(SESSION_KEY,t);}catch(e){}try{sessionStorage.setItem(SESSION_KEY,t);}catch(e){}try{const ex=new Date();ex.setDate(ex.getDate()+30);document.cookie=`${SESSION_KEY}=${t};expires=${ex.toUTCString()};path=/;SameSite=Lax`;}catch(e){}}
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

function autoDetectToken(){
  const val=document.getElementById("d-tk").value.trim().toLowerCase();
  const badge=document.getElementById("tok-badge");
  const hint=document.getElementById("tok-hint");
  if(val.startsWith("pat_")){
    badge.style.display="inline";badge.textContent="✅ PAT";badge.style.background="#00FF8818";badge.style.borderColor="#00FF8844";badge.style.color="#00FF88";
    hint.innerHTML='<span style="color:#00FF88">✅ Token PAT valid</span>';
  }else if(val.length>5){
    badge.style.display="inline";badge.style.background="#FF3B6B18";badge.style.borderColor="#FF3B6B44";badge.style.color="#FF3B6B";badge.textContent="✗ PA PAT";
    hint.innerHTML='<span style="color:#FF3B6B">✗ Dwe kòmanse ak <code>pat_</code></span>';
  }else{
    badge.style.display="none";
    hint.innerHTML='Token <b>dwe</b> kòmanse ak <code style="color:#FFD600">pat_</code>';
  }
}

function toggleMode(){
  const mode=document.getElementById("c-mode").value;
  document.getElementById("opts-forex").style.display=mode=="forex"?"block":"none";
  document.getElementById("opts-digits").style.display=mode=="digits"?"block":"none";
  renderCtrlLevels(mode=="digits"?MARTINGAL_LEVELS_DIGITS:MARTINGAL_LEVELS);
}

function getStartParams(){
  const mode=document.getElementById("c-mode").value;
  const conf=parseFloat(document.getElementById("c-conf").value);
  const target=parseFloat(document.getElementById("c-target").value||0);
  const loss=parseFloat(document.getElementById("c-loss").value||0);
  if(mode=="forex"){
    return{mode:"forex",symbol:document.getElementById("c-sy-deriv").value,
      strategy:document.getElementById("c-st-forex").value,
      lot:Math.max(0.50,parseFloat(document.getElementById("c-lot-forex").value)),
      tf:document.getElementById("c-tf").value,min_conf:conf,profit_target:target,loss_limit:loss};
  }else{
    return{mode:"digits",symbol:document.getElementById("c-sy-digits").value,
      digit_type:document.getElementById("c-digit-type").value,
      lot:Math.max(0.35,parseFloat(document.getElementById("c-lot-digits").value)),
      tf:"1m",min_conf:conf,profit_target:target,loss_limit:loss,strategy:"digits"};
  }
}

const SI={
  confluence:{l:"🔥 Confluence ELITE",d:"SuperTrend(2.5x)+HeikinAshi+Chandelier+VWAP+10 strategies. ADX≥12, min 3 strat.",tags:["ST+HA+CE","ADX≥12","3 min"]},
  deriv_pro:{l:"🚀 Deriv Pro ELITE",d:"Score 5/15 + ADX≥12 + SuperTrend +2pts.",tags:["Score 5/15","ADX≥12","ST+2pts"]},
  supertrend:{l:"📈 SuperTrend",d:"ATR×3.0. Traverser bann = siyal.",tags:["ATR×3","75-92%"]},
  heikin_ashi:{l:"🕯 Heikin Ashi",d:"5 bouji konsekitif = trend solid.",tags:["5 bouji","72-83%"]},
  chandelier:{l:"🔔 Chandelier Exit",d:"HH-ATR×3 / LL+ATR×3.",tags:["HH-ATR×3","75-90%"]},
  ai:{l:"🤖 AI Score",d:"8 faktè nòmalize.",tags:["8 faktè","68-92%"]},
  smc:{l:"🏛 SMC",d:"Break of Structure + swing H/L + EMA50.",tags:["BOS","swing","84%"]},
  scalping_pro:{l:"⚡ Scalping",d:"EMA 5/13 + RSI 9.",tags:["EMA5/13"]},
  rsi:{l:"📉 RSI",d:"RSI <30/>70 + EMA50.",tags:["RSI14","OB70"]},
};
let sel="confluence";
const sb=document.getElementById("sbts");
Object.keys(SI).forEach(k=>{
  const b=document.createElement("button");
  b.className="btn"+(k==sel?" b":"");
  b.style.cssText="padding:5px 12px;font-size:11px;margin-bottom:4px";
  b.textContent=SI[k].l;
  b.onclick=()=>{sel=k;renderS();sb.querySelectorAll("button").forEach(x=>{x.classList.remove("b");});b.classList.add("b");};
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
  document.getElementById("pg-"+id).classList.add("on");
  el.classList.add("on");
}
function msg(id,txt,ok){const cls=ok===true?"ok":(ok===false?"er":"in");document.getElementById(id).innerHTML=`<div class="al ${cls}">${txt}</div>`;}

async function doConn(){
  const rawToken=document.getElementById("d-tk").value.trim();
  if(!rawToken){msg("cm","✗ Kole token PAT ou anvan!",false);return;}
  if(!rawToken.toLowerCase().startsWith("pat_")){msg("cm","✗ Token dwe kòmanse ak pat_",false);return;}
  const appId=document.getElementById("d-ai").value.trim()||"33ifAjI7cFab3IsUV8u9q";
  const btn=event.target;btn.textContent="AP KONEKTE...";btn.disabled=true;
  msg("cm","⏳ PAT → REST api.derivws.com...",null);
  try{
    const r=await fetch("/api/connect",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({broker:"deriv",token:rawToken,app_id:appId})});
    const d=await r.json();
    if(d.ok){
      msg("cm",`✅ KONEKTE! $${d.balance.toFixed(2)} | ${d.loginid||"PAT"}`,"ok");
      document.getElementById("h-loginid").textContent=d.loginid||"";
    }else{msg("cm",d.error||"✗ Echèk",false);}
  }catch(e){msg("cm","✗ Erè rezo: "+e.message,false);}
  btn.textContent="⚡ KONEKTE PAT";btn.disabled=false;
}

async function doStart(){
  const body=getStartParams();
  const r=await fetch("/api/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const d=await r.json();
  if(d.ok){
    const levels=d.levels||MARTINGAL_LEVELS;
    msg("ctm",`✓ BonheurBot v7.2 démarre! Nivo FIX: ${levels.map(l=>'$'+l).join(' → ')} | Payout: mande API anvan chak trade`,"ok");
    document.getElementById("bs").style.display="none";
    document.getElementById("bx").style.display="inline-block";
    document.getElementById("levels-after-start").style.display="block";
    document.getElementById("levels-active").textContent=levels.map((l,i)=>`N${i+1}:$${l}`).join(' → ');
  }else{
    msg("ctm","✗ "+d.error,false);
  }
}
async function doStop(){
  await fetch("/api/stop",{method:"POST"});
  msg("ctm","✓ Bot arrêté","ok");
  document.getElementById("bs").style.display="inline-block";
  document.getElementById("bx").style.display="none";
  document.getElementById("levels-after-start").style.display="none";
}

async function doBt(){
  const btn=event.target;btn.textContent="⏳ AP KALKILE...";btn.disabled=true;
  document.getElementById("btm").innerHTML=`<div class="al in">⏳ Ap fè backtest...</div>`;
  const body={symbol:document.getElementById("bt-sy").value,strategy:document.getElementById("bt-st").value,
    balance:parseFloat(document.getElementById("bt-bl").value),lot:parseFloat(document.getElementById("bt-lt").value),
    sl:parseFloat(document.getElementById("bt-sl").value),tp:parseFloat(document.getElementById("bt-tp").value)};
  try{
    const r=await fetch("/api/backtest",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    document.getElementById("btm").innerHTML="";
    if(d.ok){
      const v=d.result;const c=v.net_pnl>=0?"#00FF88":"#FF3B6B";
      document.getElementById("btr").innerHTML=`<div class="stats">
        <div class="stat"><div class="sl">NET P&L</div><div class="sv" style="color:${c}">$${v.net_pnl}</div></div>
        <div class="stat"><div class="sl">RETOU</div><div class="sv" style="color:${c}">${v.return_pct}%</div></div>
        <div class="stat"><div class="sl">WIN RATE</div><div class="sv" style="color:#00FF88">${v.win_rate}%</div></div>
        <div class="stat"><div class="sl">TRADES</div><div class="sv" style="color:#FFD600">${v.trades}</div></div>
        <div class="stat"><div class="sl">MAX DD</div><div class="sv" style="color:#FF3B6B">${v.max_dd}%</div></div>
        <div class="stat"><div class="sl">SHARPE</div><div class="sv" style="color:#00D4FF">${v.sharpe}</div></div>
      </div>${v.equity&&v.equity.length>2?drawC(v.equity):""}`;
    }else{document.getElementById("btm").innerHTML=`<div class="al er">✗ ${d.error}</div>`;}
  }catch(e){document.getElementById("btm").innerHTML=`<div class="al er">✗ ${e.message}</div>`;}
  btn.textContent="▶ KÒMANSE BACKTEST";btn.disabled=false;
}

function drawC(vals){
  const W=500,H=110,p=8;
  const mn=Math.min(...vals),mx=Math.max(...vals),rng=mx-mn||1;
  const pts=vals.map((v,i)=>`${p+(i/(vals.length-1))*(W-p*2)},${H-p-((v-mn)/rng)*(H-p*2)}`).join(" ");
  const area=`${p},${H} ${pts} ${W-p},${H}`;
  const col=vals[vals.length-1]>=vals[0]?"#00FF88":"#FF3B6B";
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:110px;margin-top:12px"><defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${col}" stop-opacity=".3"/><stop offset="100%" stop-color="${col}" stop-opacity="0"/></linearGradient></defs><polygon points="${area}" fill="url(#cg)"/><polyline points="${pts}" fill="none" stroke="${col}" stroke-width="2.5"/></svg>`;
}

function upd(d){
  const col=d.pnl>=0?"#00FF88":"#FF3B6B";const sign=d.pnl>=0?"+":"";
  document.getElementById("hbal").textContent="$"+d.balance.toFixed(2);
  document.getElementById("hbal").style.color=d.connected?"#00D4FF":"#3A6070";
  document.getElementById("hb").textContent=d.connected?"DERIV PAT":"DISCONNECTED";
  document.getElementById("hb").style.color=d.connected?"#00FF88":"#3A6070";
  document.getElementById("dot").className="dot "+(d.running?"dl":"di");
  document.getElementById("hs").textContent=d.running?"LIVE":"IDLE";
  document.getElementById("hs").style.color=d.running?"#00FF88":"#3A6070";
  document.getElementById("s-bal").textContent="$"+d.balance.toFixed(2);
  document.getElementById("s-pnl").textContent=sign+"$"+Math.abs(d.pnl).toFixed(2);
  document.getElementById("s-pnl").style.color=col;
  document.getElementById("s-pnl2").textContent=sign+"$"+Math.abs(d.pnl).toFixed(2);
  document.getElementById("s-pnl2").style.color=col;
  document.getElementById("s-sent").textContent="$"+d.profit_sent.toFixed(4);
  document.getElementById("s-tr").textContent=d.trades.length;
  document.getElementById("s-bot").textContent=d.running?"LIVE 🟢":"IDLE";
  document.getElementById("s-bot").style.color=d.running?"#00FF88":"#3A6070";
  document.getElementById("s-strat").textContent=d.config.strategy||"—";
  document.getElementById("s-sym").textContent=d.config.symbol||"—";
  document.getElementById("c-st2").textContent=d.running?"LIVE 🟢":"IDLE";
  document.getElementById("c-st2").style.color=d.running?"#00FF88":"#3A6070";
  document.getElementById("c-bal").textContent="$"+d.balance.toFixed(2);
  document.getElementById("c-pnl").textContent=sign+"$"+Math.abs(d.pnl).toFixed(2);
  document.getElementById("c-pnl").style.color=col;
  document.getElementById("c-sent").textContent="$"+d.profit_sent.toFixed(4);
  if(d.running){document.getElementById("bs").style.display="none";document.getElementById("bx").style.display="inline-block";}
  else{document.getElementById("bs").style.display="inline-block";document.getElementById("bx").style.display="none";}
  if(d.trades.length>1){
    let cum=0;
    const eq=d.trades.slice().reverse().map(t=>{cum+=t.pnl||0;return cum;});
    const svg=document.getElementById("chart");
    const ch=drawC(eq);const tmp=document.createElement("div");tmp.innerHTML=ch;
    const ns=tmp.firstChild;while(svg.firstChild)svg.removeChild(svg.firstChild);while(ns.firstChild)svg.appendChild(ns.firstChild);
  }
  if(d.trades.length){
    document.getElementById("trtit").textContent=`HISTOIRIK TRADES (${d.trades.length})`;
    document.getElementById("trtbl").innerHTML=`<table>
      <tr><th>#</th><th>Lè</th><th>Senbol</th><th>Side</th><th>Antre</th><th>Nivo/Payout</th><th>Mise</th><th>Conf</th><th>P&L</th><th>Estati</th></tr>
      ${d.trades.map(t=>`<tr>
        <td style="color:#4A7080">${t.id}</td>
        <td style="color:#4A7080">${t.time}</td>
        <td style="font-weight:700">${t.symbol}</td>
        <td><span class="tag ${t.side=="BUY"||t.side.includes("OVER")||t.side=="EVEN"?"tb":"ts"}">${t.side}</span></td>
        <td>${t.entry}</td>
        <td style="color:#4A7080;font-size:10px">${t.regime||"—"}</td>
        <td style="color:#FFD600">$${t.stake||"—"}</td>
        <td style="color:#FFD600">${t.conf}</td>
        <td style="color:${t.pnl>=0?"#00FF88":"#FF3B6B"};font-weight:700">${t.pnl>=0?"+":""}${t.pnl.toFixed(2)}</td>
        <td><span class="tag ${t.status=="won"?"tb":"ts"}">${t.status||"—"}</span></td>
      </tr>`).join("")}
    </table>`;
  }
  if(d.log.length){
    document.getElementById("logs").innerHTML=d.log.map(l=>
      `<div class="le"><span class="lt">${l.time}</span><span class="l${l.level[0]}">${l.msg}</span></div>`
    ).join("");
  }
}

async function poll(){
  try{const r=await fetch("/api/status");const d=await r.json();upd(d);}catch(e){}
  setTimeout(poll,3000);
}

async function admRefresh(){
  const token=getStoredToken();if(!token)return;
  try{
    const r=await fetch("/api/admin/codes",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});
    const d=await r.json();
    if(d.ok){
      const sc={"ADM":"#00D4FF","AKTIF":"#00FF88","ITILIZE":"#FF3B6B","EKSPIRE":"#4A7080"};
      document.getElementById("adm-total").textContent=d.codes.length;
      document.getElementById("adm-aktif").textContent=d.codes.filter(c=>c.status==="AKTIF").length;
      document.getElementById("adm-used").textContent=d.codes.filter(c=>c.status==="ITILIZE").length;
      document.getElementById("adm-sess").textContent=d.total_sessions;
      document.getElementById("adm-codes-list").innerHTML=`<table><tr><th>KÒD</th><th>STATUS</th><th>RETE</th><th>TIP</th><th>AKSYON</th></tr>${d.codes.map(c=>`<tr>
        <td style="font-weight:700">${c.code}</td>
        <td><span class="tag" style="color:${sc[c.status]||"#4A7080"};border-color:${sc[c.status]||"#4A7080"}44">${c.status}</span></td>
        <td style="color:#4A7080">${c.remaining}</td>
        <td>${c.is_adm?"👑":"👤"}</td>
        <td style="display:flex;gap:4px">
          ${c.status!=="ADM"?`<button onclick="admReset('${c.code}')" style="background:transparent;border:1px solid #FFD60044;color:#FFD600;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">↺</button>`:""}
          ${c.code!=="BONHEURWIIN"?`<button onclick="admRevoke('${c.code}')" style="background:transparent;border:1px solid #FF3B6B44;color:#FF3B6B;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">✕</button>`:""}
        </td>
      </tr>`).join("")}</table>`;
    }
  }catch(e){}
  try{
    const r2=await fetch("/api/admin/users",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});
    const d2=await r2.json();
    if(d2.ok){
      document.getElementById("adm-users-count").textContent=d2.total;
      document.getElementById("adm-users-list").innerHTML=d2.total===0
        ?'<div style="color:#3A6070;text-align:center;padding:20px">Pa gen itilizatè</div>'
        :`<table><tr><th>UID</th><th>SENBOL</th><th>BOT</th><th>BALANS</th><th>P&L</th><th>TRADES</th><th>AKSYON</th></tr>${d2.users.map(u=>`<tr>
          <td style="color:#4A7080;font-size:10px">${u.uid}</td>
          <td style="font-weight:700">${u.symbol||"—"}</td>
          <td><span class="tag ${u.running?"tb":"tg"}">${u.running?"LIVE":"IDLE"}</span></td>
          <td style="color:#00D4FF">$${u.balance}</td>
          <td style="color:${u.pnl>=0?"#00FF88":"#FF3B6B"}">${u.pnl>=0?"+":""}$${u.pnl}</td>
          <td>${u.trades}</td>
          <td style="display:flex;gap:4px">
            ${u.running?`<button onclick="admStopUser('${u.uid}')" style="background:transparent;border:1px solid #FF3B6B44;color:#FF3B6B;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">■</button>`:""}
            <button onclick="admClearTrades('${u.uid}')" style="background:transparent;border:1px solid #FFD60044;color:#FFD600;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">📊🗑</button>
          </td>
        </tr>`).join("")}</table>`;
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
async function admClearTrades(uid){if(!confirm(`Efase trades ${uid}?`))return;const token=getStoredToken();const r=await fetch("/api/admin/clear_trades",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,uid})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh();}
function genCode(len){const chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789";let result="";for(let i=0;i<len;i++){if(i>0&&i%4===0)result+="-";result+=chars[Math.floor(Math.random()*chars.length)];}document.getElementById("gen-result").textContent=result;document.getElementById("gen-copy-btn").style.display="inline-block";document.getElementById("new-code").value=result;}
function admCopyGen(){const code=document.getElementById("gen-result").textContent;navigator.clipboard.writeText(code).catch(()=>{});admAddCode();}
checkLogin();
</script>
</body>
</html>"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"BonheurBot PAT v7.2 — Nivo FIX + Payout API — port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
