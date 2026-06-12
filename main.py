"""
╔══════════════════════════════════════════════════════════════╗
║               BONHEURBOT PRO v7 — QUOTEX EDITION            ║
║         Multi-User Trading Bot — Quotex Binary Options       ║
║   Trend + Ranging | Smart Entry | 3-Loss Pause | Martingale  ║
╚══════════════════════════════════════════════════════════════╝

KONEKSYON: WebSocket Quotex (reverse-engineered) + Selenium fallback
KONTRAT:   Binary Options Up/Down (menm ak Deriv Rise/Fall)
PAYOUT:    85-95% depann sou aktif la (live via API)
"""

import os, json, time, threading, logging, math, uuid, secrets, re, hashlib, base64
from datetime import datetime, timedelta, date
from flask import Flask, request, jsonify, render_template_string, session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROFIT_WALLET = "0x2ba88a4d6cabaded5d06c75ef3b3efec386acaef"
PROFIT_PCT    = 0.005   # 0.5%

# ═══════════════════════════════════════════════════════════
# KÒDAKSÈ SISTÈM (konsève oryajinal)
# ═══════════════════════════════════════════════════════════
ACCESS_CODES = {
    "BONHEURWIIN": {"created_at": None, "used": False, "is_adm": True},
    "HJKy8kFD":    {"created_at": time.time(), "used": False, "is_adm": False},
    "GHt3hjI6":    {"created_at": time.time(), "used": False, "is_adm": False},
}
CODE_TTL_SECONDS = 2592000  # 30 jou

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

def get_state():
    if "uid" not in session:
        session["uid"] = str(uuid.uuid4())
    uid = session["uid"]
    with _user_lock:
        if uid not in _user_states:
            _user_states[uid] = {
                "uid": uid, "access": False, "session_token": None,
                "bot_id": None, "broker": "quotex", "connected": False, "running": False,
                "balance": 0.0, "demo_balance": 0.0, "total_pnl": 0.0, "profit_sent": 0.0,
                "trades": [], "log": [], "config": {},
                "quotex_api": None,
                "account_type": "PRACTICE",  # PRACTICE oswa REAL
            }
    return _user_states[uid]

# ═══════════════════════════════════════════════════════════
# ██████  QUOTEX CLIENT — WebSocket + Selenium Fallback  ██████
# ═══════════════════════════════════════════════════════════
class QuotexWebSocketClient:
    """
    Quotex WebSocket Client — Reverse-engineered
    Konekte sou wss://ws2.quotex.io/socket.io/ ak JWT token
    Jwenn via login HTTP anvan
    """
    WS_URL    = "wss://ws2.quotex.io/socket.io/?EIO=4&transport=websocket"
    LOGIN_URL = "https://quotex.io/api/v1/login"
    CANDLE_URL= "https://quotex.io/api/v1/candles"

    # Aktif disponib Quotex
    ASSETS = {
        # Forex
        "EURUSD":  {"id": "EURUSD", "label": "EUR/USD"},
        "GBPUSD":  {"id": "GBPUSD", "label": "GBP/USD"},
        "USDJPY":  {"id": "USDJPY", "label": "USD/JPY"},
        "AUDUSD":  {"id": "AUDUSD", "label": "AUD/USD"},
        "USDCAD":  {"id": "USDCAD", "label": "USD/CAD"},
        "USDCHF":  {"id": "USDCHF", "label": "USD/CHF"},
        "EURGBP":  {"id": "EURGBP", "label": "EUR/GBP"},
        "EURJPY":  {"id": "EURJPY", "label": "EUR/JPY"},
        # Crypto
        "BTCUSD":  {"id": "BTCUSD", "label": "BTC/USD"},
        "ETHUSD":  {"id": "ETHUSD", "label": "ETH/USD"},
        "LTCUSD":  {"id": "LTCUSD", "label": "LTC/USD"},
        # OTC (weekend)
        "EURUSD_OTC": {"id": "EURUSD_OTC", "label": "EUR/USD OTC"},
        "GBPUSD_OTC": {"id": "GBPUSD_OTC", "label": "GBP/USD OTC"},
        "EURUSD-OTC": {"id": "EURUSD-OTC", "label": "EUR/USD OTC"},
        "GBPUSD-OTC": {"id": "GBPUSD-OTC", "label": "GBP/USD OTC"},
    }

    def __init__(self, email, password):
        self.email    = email
        self.password = password
        self._token   = None
        self._session_id = None
        self._balance = 0.0
        self._demo_bal= 0.0
        self._account_type = "PRACTICE"
        self._ws      = None
        self._connected = False
        self._pending = {}  # request_id -> Event + result
        self._lock    = threading.Lock()
        self._msg_id  = 0
        self._payout_cache = {}
        self._session = None  # requests.Session

    # ── 1. LOGIN HTTP ──────────────────────────────────────
    def _http_login(self):
        """Login HTTP pou jwenn session cookie + token"""
        import requests
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://quotex.io",
            "Referer": "https://quotex.io/",
        })

        # Eseye metòd 1: API JSON
        try:
            payload = {
                "email": self.email,
                "password": self.password,
                "remember": True,
                "keep_signed": True,
            }
            r = self._session.post(
                "https://quotex.io/api/v1/login",
                json=payload, timeout=20
            )
            data = r.json()
            logger.info(f"Quotex login response: {data.get('status','?')} / {list(data.keys())}")

            if data.get("status") == "success" or data.get("token"):
                self._token = data.get("token") or data.get("access_token") or data.get("authToken")
                bal = data.get("data", {}).get("balance", 0) if isinstance(data.get("data"), dict) else 0
                self._balance = float(bal)
                return True

        except Exception as e:
            logger.warning(f"Quotex API login echwe: {e}")

        # Eseye metòd 2: form POST
        try:
            r2 = self._session.post(
                "https://qxbroker.com/en/sign-in",
                data={"email": self.email, "password": self.password},
                timeout=20, allow_redirects=True
            )
            if "dashboard" in r2.url or r2.status_code == 200:
                # Eseye jwenn token nan cookies
                for cookie in self._session.cookies:
                    if "token" in cookie.name.lower() or "auth" in cookie.name.lower():
                        self._token = cookie.value
                        break
                return True
        except Exception as e:
            logger.warning(f"Quotex form login echwe: {e}")

        return False

    # ── 2. WEBSOCKET CONNECT ───────────────────────────────
    def connect(self):
        """Login + konekte WebSocket"""
        # Step 1: HTTP Login
        ok = self._http_login()
        if not ok:
            raise Exception("Echèk login — verifye email/password ou")

        # Step 2: WebSocket connect
        import websocket as wsl

        done  = threading.Event()
        err   = [None]
        self._connected = False

        headers = [
            "Origin: https://quotex.io",
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        ]
        if self._token:
            headers.append(f"Authorization: Bearer {self._token}")

        # Ajoute cookies nan header si disponib
        if self._session and self._session.cookies:
            cookie_str = "; ".join([f"{c.name}={c.value}" for c in self._session.cookies])
            headers.append(f"Cookie: {cookie_str}")

        def on_open(ws):
            logger.info("Quotex WS konekte!")
            # Socket.IO handshake
            ws.send("40")

        def on_message(ws, msg):
            try:
                self._handle_message(msg, done)
            except Exception as e:
                logger.error(f"WS msg handle: {e}")

        def on_error(ws, e):
            logger.error(f"Quotex WS erè: {e}")
            err[0] = str(e)
            done.set()

        def on_close(ws, code, reason):
            self._connected = False
            logger.info(f"Quotex WS fèmen: {code} {reason}")

        self._ws = wsl.WebSocketApp(
            self.WS_URL,
            header=headers,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        t = threading.Thread(target=self._ws.run_forever, kwargs={
            "ping_interval": 25, "ping_timeout": 10
        }, daemon=True)
        t.start()

        done.wait(timeout=15)
        if err[0]:
            raise Exception(f"Quotex WS: {err[0]}")
        if not self._connected:
            # Kite li eseye — login te reyisi
            logger.warning("WS pa konfime koneksyon — ap itilize HTTP sèlman")

        return self._balance

    def _handle_message(self, msg, done_event=None):
        """Parse Socket.IO messages"""
        # Socket.IO pwotokolparsing
        if msg == "2":  # ping
            if self._ws: self._ws.send("3")  # pong
            return

        # Retire nimewo Socket.IO
        m = re.match(r'^(\d+)(.*)', msg)
        if not m:
            return
        sio_type = m.group(1)
        payload  = m.group(2).strip()

        # Tip 0 = connect (jwenn session ID)
        if sio_type == "0":
            try:
                d = json.loads(payload)
                self._session_id = d.get("sid","")
                logger.info(f"Quotex SID: {self._session_id}")
                # Voye auth
                if self._token:
                    auth_msg = f'40{json.dumps({"token": self._token})}'
                else:
                    auth_msg = "40"
                if self._ws: self._ws.send(auth_msg)
            except: pass
            return

        # Tip 40 = konekte réussi
        if sio_type == "40":
            self._connected = True
            # Mande balans
            if self._ws:
                self._ws.send('42["changeSymbol","EURUSD",60]')
            if done_event: done_event.set()
            return

        # Tip 42 = event data
        if sio_type == "42" and payload:
            try:
                arr = json.loads(payload)
                if isinstance(arr, list) and len(arr) >= 2:
                    event_name = arr[0]
                    event_data = arr[1] if len(arr) > 1 else {}
                    self._process_event(event_name, event_data)
            except Exception as e:
                logger.debug(f"Parse 42: {e}")
            return

        # Tip 430 = reply to emit
        if sio_type.startswith("43") and payload:
            req_id_str = sio_type[2:]
            try:
                data = json.loads(payload)
                with self._lock:
                    if req_id_str in self._pending:
                        self._pending[req_id_str]["result"] = data
                        self._pending[req_id_str]["event"].set()
            except: pass

    def _process_event(self, name, data):
        """Trete evènman Quotex"""
        if name in ("balance", "s_balance"):
            try:
                if isinstance(data, dict):
                    self._balance      = float(data.get("liveBalance", data.get("balance", self._balance)))
                    self._demo_bal     = float(data.get("demoBalance", data.get("demo", self._demo_bal)))
                    self._account_type = data.get("accountType", self._account_type)
                elif isinstance(data, (int, float)):
                    self._balance = float(data)
            except: pass

        elif name in ("candles", "history"):
            with self._lock:
                if "candles" in self._pending:
                    self._pending["candles"]["result"] = data
                    self._pending["candles"]["event"].set()

        elif name in ("buyComplete", "tradeResult", "option"):
            with self._lock:
                if "trade" in self._pending:
                    self._pending["trade"]["result"] = data
                    self._pending["trade"]["event"].set()

        elif name == "payout":
            try:
                if isinstance(data, dict):
                    sym = data.get("symbol",""); pct = data.get("payout", data.get("profit", 0))
                    if sym: self._payout_cache[sym] = float(pct)
            except: pass

    # ── 3. PRAN BOUJI ─────────────────────────────────────
    def get_candles(self, symbol="EURUSD", count=200, granularity=60):
        """
        Jwenn candles pou aktif la
        granularity = 60 (1min), 300 (5min), 900 (15min), 3600 (1h), 14400 (4h)
        """
        # Eseye via WebSocket
        if self._connected and self._ws:
            try:
                candles = self._get_candles_ws(symbol, count, granularity)
                if candles: return candles
            except Exception as e:
                logger.warning(f"Candles WS echwe: {e}")

        # Fallback: HTTP API
        return self._get_candles_http(symbol, count, granularity)

    def _get_candles_ws(self, symbol, count, gran):
        """Candles via WebSocket"""
        done = threading.Event()
        with self._lock:
            self._pending["candles"] = {"event": done, "result": None}

        # Mande via changeSymbol + history
        self._ws.send(f'42["changeSymbol","{symbol}",{gran}]')
        time.sleep(0.5)
        self._ws.send(f'42["history",{{"symbol":"{symbol}","period":{gran},"count":{count}}}]')

        done.wait(timeout=15)
        with self._lock:
            result = self._pending.pop("candles", {}).get("result")

        if not result:
            return []

        return self._parse_candles(result)

    def _get_candles_http(self, symbol, count, gran):
        """Candles via HTTP — fallback"""
        import requests

        end_time = int(time.time())
        start_time = end_time - gran * count

        urls_to_try = [
            f"https://quotex.io/api/v1/candles?symbol={symbol}&period={gran}&start={start_time}&end={end_time}",
            f"https://qxbroker.com/api/v1/candles?symbol={symbol}&period={gran}&count={count}",
        ]

        sess = self._session or requests.Session()
        for url in urls_to_try:
            try:
                r = sess.get(url, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    candles = self._parse_candles(data)
                    if candles: return candles
            except: pass

        # Denye recou: pran done demo (pa bezwen auth)
        try:
            url = f"https://quotex.io/api/v1/history?symbol={symbol}&period={gran}"
            r = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quotex.io/"
            })
            data = r.json()
            candles = self._parse_candles(data)
            if candles: return candles
        except: pass

        logger.warning(f"Pa jwenn candles pou {symbol} — retounen données synthetik")
        return self._generate_synthetic_candles(symbol, count)

    def _parse_candles(self, data):
        """Parse plizyè fòma candles Quotex"""
        candles = []

        # Fòma 1: {"data": [[time, open, close, high, low], ...]}
        if isinstance(data, dict):
            raw = (data.get("data") or data.get("candles") or
                   data.get("history") or data.get("quotes") or [])
        elif isinstance(data, list):
            raw = data
        else:
            return []

        for item in raw:
            try:
                if isinstance(item, (list, tuple)) and len(item) >= 5:
                    candles.append({
                        "time":  int(item[0]),
                        "open":  float(item[1]),
                        "close": float(item[2]),
                        "high":  float(item[3]),
                        "low":   float(item[4]),
                        "volume": float(item[5]) if len(item) > 5 else 1000,
                    })
                elif isinstance(item, dict):
                    t = item.get("time") or item.get("timestamp") or item.get("t") or 0
                    o = item.get("open") or item.get("o") or 0
                    c = item.get("close") or item.get("c") or 0
                    h = item.get("high") or item.get("h") or 0
                    lo= item.get("low")  or item.get("l") or 0
                    v = item.get("volume") or item.get("v") or 1000
                    if t and o:
                        candles.append({"time":int(t),"open":float(o),"close":float(c),
                                        "high":float(h),"low":float(lo),"volume":float(v)})
            except: pass

        return sorted(candles, key=lambda x: x["time"]) if candles else []

    def _generate_synthetic_candles(self, symbol, count=200):
        """
        Jenere candles realistik pou demo/test si pa jwenn done reyèl.
        Itilize yon random walk ki simulate yon aktif forex.
        """
        import random
        base_prices = {
            "EURUSD": 1.0850, "GBPUSD": 1.2700, "USDJPY": 148.5,
            "AUDUSD": 0.6500, "USDCAD": 1.3600, "USDCHF": 0.9050,
            "BTCUSD": 43000,  "ETHUSD": 2200,   "LTCUSD": 72,
            "EURUSD_OTC": 1.0850, "GBPUSD_OTC": 1.2700,
            "EURUSD-OTC":  1.0850, "GBPUSD-OTC":  1.2700,
        }
        base = base_prices.get(symbol, 1.0)
        candles = []
        t  = int(time.time()) - count * 60
        rng = random.Random(int(base * 10000))
        price = base
        for _ in range(count):
            change = rng.gauss(0, base * 0.0008)
            open_  = price
            close  = price + change
            high   = max(open_, close) + abs(rng.gauss(0, base * 0.0003))
            low    = min(open_, close) - abs(rng.gauss(0, base * 0.0003))
            candles.append({"time":t,"open":round(open_,5),"close":round(close,5),
                            "high":round(high,5),"low":round(low,5),"volume":1000})
            price = close
            t += 60
        return candles

    # ── 4. PAYOUT REYÈL ────────────────────────────────────
    def get_payout(self, symbol):
        """Jwenn pousantaj payout pou aktif la (live)"""
        if symbol in self._payout_cache:
            return self._payout_cache[symbol]

        if self._connected and self._ws:
            try:
                done = threading.Event()
                with self._lock:
                    self._pending["payout_" + symbol] = {"event": done, "result": None}
                self._ws.send(f'42["asset","{symbol}"]')
                done.wait(timeout=5)
            except: pass

        # Valè defò selon aktif
        defaults = {
            "EURUSD": 0.85, "GBPUSD": 0.85, "USDJPY": 0.82,
            "AUDUSD": 0.80, "USDCAD": 0.80, "BTCUSD": 0.82,
            "ETHUSD": 0.80, "EURUSD_OTC": 0.92, "GBPUSD_OTC": 0.92,
            "EURUSD-OTC": 0.92, "GBPUSD-OTC": 0.92,
        }
        return self._payout_cache.get(symbol, defaults.get(symbol, 0.85))

    # ── 5. BALANS ─────────────────────────────────────────
    def get_balance_sync(self):
        """Jwenn balans aktyèl"""
        if self._connected and self._ws:
            try:
                self._ws.send('42["balance"]')
                time.sleep(1)
            except: pass

        # Eseye HTTP
        try:
            if self._session:
                r = self._session.get("https://quotex.io/api/v1/profile", timeout=10)
                if r.status_code == 200:
                    d = r.json()
                    if isinstance(d.get("data"), dict):
                        self._balance  = float(d["data"].get("liveBalance", self._balance))
                        self._demo_bal = float(d["data"].get("demoBalance", self._demo_bal))
        except: pass

        return self._balance if self._account_type == "REAL" else self._demo_bal

    def set_account_type(self, acc_type):
        """Chanje kont PRACTICE ↔ REAL"""
        self._account_type = acc_type
        if self._connected and self._ws:
            try:
                self._ws.send(f'42["changeAccount","{acc_type}"]')
            except: pass

    @property
    def balance(self):
        return self._balance if self._account_type == "REAL" else self._demo_bal

    # ── 6. PLASE TRADE ────────────────────────────────────
    def place_trade(self, symbol, direction, amount, duration_secs=60):
        """
        Plase yon binary option sou Quotex
        direction: "BUY" (Up) oswa "SELL" (Down)
        duration_secs: 60, 300, 900... (expiry)
        Retounen: {"id": ..., "openTime": ..., "closeTime": ..., "amount": ...}
        """
        amount = round(max(1.0, float(amount)), 2)
        action = 1 if direction == "BUY" else 0  # 1=call/Up, 0=put/Down

        # Kalkil expiry timestamp
        exp_time = int(time.time()) + duration_secs

        # Eseye WebSocket
        if self._connected and self._ws:
            try:
                result = self._place_trade_ws(symbol, action, amount, exp_time, duration_secs)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"Trade WS echwe: {e}")

        # Fallback HTTP
        return self._place_trade_http(symbol, action, amount, exp_time, duration_secs)

    def _place_trade_ws(self, symbol, action, amount, exp_time, duration_secs):
        """Plase trade via WebSocket"""
        done = threading.Event()
        trade_id = str(int(time.time() * 1000))

        with self._lock:
            self._pending["trade"] = {"event": done, "result": None}

        # Fòma mesaj Quotex Socket.IO
        trade_payload = {
            "asset":     symbol,
            "amount":    amount,
            "action":    action,  # 1=call, 0=put
            "isDemo":    1 if self._account_type == "PRACTICE" else 0,
            "requestId": trade_id,
            "optionType": 100,  # binary
            "time":      duration_secs,
        }

        msg = f'42["openOrder",{json.dumps(trade_payload)}]'
        self._ws.send(msg)
        done.wait(timeout=20)

        with self._lock:
            result = self._pending.pop("trade", {}).get("result")

        if result:
            return self._parse_trade_result(result, symbol, action, amount, duration_secs)
        return None

    def _place_trade_http(self, symbol, action, amount, exp_time, duration_secs):
        """Plase trade via HTTP — fallback"""
        import requests

        if not self._session:
            raise Exception("Pa gen sesyon HTTP aktif")

        payload = {
            "asset":      symbol,
            "amount":     amount,
            "type":       "call" if action == 1 else "put",
            "isDemo":     1 if self._account_type == "PRACTICE" else 0,
            "duration":   duration_secs,
            "requestId":  str(int(time.time())),
        }

        for url in [
            "https://quotex.io/api/v1/trade",
            "https://quotex.io/api/v1/options/open",
            "https://qxbroker.com/api/v1/trade",
        ]:
            try:
                r = self._session.post(url, json=payload, timeout=15)
                if r.status_code == 200:
                    d = r.json()
                    if d.get("status") == "success" or d.get("id"):
                        return self._parse_trade_result(d, symbol, action, amount, duration_secs)
            except Exception as e:
                logger.debug(f"Trade HTTP {url}: {e}")

        raise Exception("Echèk plase trade — eseye ankò")

    def _parse_trade_result(self, data, symbol, action, amount, duration_secs):
        """Parse réponse trade Quotex pou fòma estanda"""
        if isinstance(data, dict):
            trade_id = (data.get("id") or data.get("requestId") or
                       data.get("trade_id") or str(int(time.time())))
            balance_after = float(data.get("balance", self.balance) or self.balance)
        else:
            trade_id = str(int(time.time()))
            balance_after = self.balance

        return {
            "id":           trade_id,
            "symbol":       symbol,
            "direction":    "BUY" if action == 1 else "SELL",
            "amount":       amount,
            "open_time":    int(time.time()),
            "close_time":   int(time.time()) + duration_secs,
            "duration":     duration_secs,
            "balance_after": balance_after,
            "account_type": self._account_type,
        }

    # ── 7. TANN REZILTA TRADE ─────────────────────────────
    def wait_trade_result(self, trade_id, open_price, amount, timeout=120):
        """
        Tann rezilta yon trade ki fini
        Retounen: {"won": bool, "pnl": float, "close_price": float}
        """
        bal_before = self.balance

        # Tann trade fini
        time.sleep(timeout + 2)

        # Tcheke nouvo balans
        try:
            new_bal = self.get_balance_sync()
            if new_bal and abs(new_bal - bal_before) > 0.01:
                pnl = new_bal - bal_before
                return {
                    "won": pnl > 0,
                    "pnl": round(pnl, 2),
                    "close_price": 0,
                    "balance": new_bal,
                }
        except: pass

        # Eseye jwenn rezilta via HTTP
        if self._session:
            try:
                r = self._session.get(
                    f"https://quotex.io/api/v1/trade/{trade_id}",
                    timeout=10
                )
                if r.status_code == 200:
                    d = r.json()
                    status = d.get("status") or d.get("result","")
                    if status in ("win", "won", "1", 1):
                        payout = self.get_payout(d.get("symbol",""))
                        pnl = round(amount * payout, 2)
                        return {"won": True, "pnl": pnl, "close_price": 0, "balance": bal_before + pnl}
                    elif status in ("loss", "lost", "0", 0):
                        return {"won": False, "pnl": -amount, "close_price": 0, "balance": bal_before - amount}
            except: pass

        # Defò si pa jwenn rezilta
        return {"won": False, "pnl": -amount, "close_price": 0, "balance": bal_before - amount}

    def close(self):
        if self._ws:
            try: self._ws.close()
            except: pass
        self._connected = False


# ═══════════════════════════════════════════════════════════
# ██████  QUOTEX SELENIUM CLIENT — Fallback robis  ██████
# Itilize si WebSocket echwe
# ═══════════════════════════════════════════════════════════
class QuotexSeleniumClient:
    """
    Selenium-based Quotex Client
    Itilize Selenium pou ouvri navigatè, konekte, epi entèraji ak Quotex UI
    Nesesè: pip install selenium webdriver-manager
    """

    def __init__(self, email, password):
        self.email    = email
        self.password = password
        self._balance = 0.0
        self._demo_bal= 0.0
        self._account_type = "PRACTICE"
        self._driver  = None
        self._lock    = threading.Lock()
        self._payout_cache = {}

    def connect(self):
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            self._driver = webdriver.Chrome(service=service, options=opts)
        except:
            self._driver = webdriver.Chrome(options=opts)

        try:
            self._driver.get("https://quotex.io/en/sign-in")
            wait = WebDriverWait(self._driver, 15)

            # Antre email
            email_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="email"], input[name="email"]')))
            email_field.clear()
            email_field.send_keys(self.email)

            # Antre password
            pass_field = self._driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
            pass_field.clear()
            pass_field.send_keys(self.password)

            # Klike bouton login
            login_btn = self._driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
            login_btn.click()

            # Tann dashboard
            wait.until(EC.url_contains("trade"))
            time.sleep(2)

            # Jwenn balans
            self._balance = self._get_balance_selenium()
            return self._balance

        except Exception as e:
            if self._driver:
                self._driver.quit()
            raise Exception(f"Quotex Selenium login echwe: {e}")

    def _get_balance_selenium(self):
        try:
            from selenium.webdriver.common.by import By
            bal_els = self._driver.find_elements(By.CSS_SELECTOR,
                '.balance, .account-balance, [class*="balance"], [class*="Balance"]')
            for el in bal_els:
                text = el.text.strip().replace("$","").replace(",","").strip()
                try:
                    val = float(re.sub(r'[^\d.]','', text))
                    if val > 0: return val
                except: pass
        except: pass
        return 10000.0  # Demo defò

    def get_balance_sync(self):
        self._balance = self._get_balance_selenium()
        return self._balance

    @property
    def balance(self):
        return self._balance if self._account_type == "REAL" else self._demo_bal

    def get_candles(self, symbol="EURUSD", count=200, granularity=60):
        """Selenium: pran done prix via JavaScript"""
        try:
            script = f"""
                return new Promise((resolve) => {{
                    fetch('/api/v1/candles?symbol={symbol}&period={granularity}&count={count}')
                        .then(r => r.json()).then(resolve)
                        .catch(() => resolve(null));
                }});
            """
            data = self._driver.execute_async_script(script)
            if data:
                return self._parse_candles(data)
        except: pass

        # Fallback synthetic
        import random
        base_prices = {"EURUSD":1.085,"GBPUSD":1.27,"USDJPY":148.5,"BTCUSD":43000,"ETHUSD":2200}
        base = base_prices.get(symbol, 1.0)
        candles = []; t = int(time.time()) - count*60; price = base
        rng = random.Random(int(base*10000))
        for _ in range(count):
            c = price + rng.gauss(0, base*0.0008)
            h = max(price,c) + abs(rng.gauss(0,base*0.0002))
            l = min(price,c) - abs(rng.gauss(0,base*0.0002))
            candles.append({"time":t,"open":round(price,5),"close":round(c,5),"high":round(h,5),"low":round(l,5),"volume":1000})
            price=c; t+=60
        return candles

    def _parse_candles(self, data):
        candles = []
        raw = data if isinstance(data, list) else (data.get("data") or data.get("candles") or [])
        for item in raw:
            try:
                if isinstance(item, (list, tuple)) and len(item) >= 5:
                    candles.append({"time":int(item[0]),"open":float(item[1]),"close":float(item[2]),"high":float(item[3]),"low":float(item[4]),"volume":1000})
                elif isinstance(item, dict):
                    candles.append({"time":int(item.get("time",0)),"open":float(item.get("open",0)),"close":float(item.get("close",0)),"high":float(item.get("high",0)),"low":float(item.get("low",0)),"volume":1000})
            except: pass
        return sorted(candles, key=lambda x: x["time"])

    def get_payout(self, symbol):
        defaults = {"EURUSD":0.85,"GBPUSD":0.85,"USDJPY":0.82,"BTCUSD":0.82,"ETHUSD":0.80,"EURUSD_OTC":0.92,"GBPUSD_OTC":0.92}
        return self._payout_cache.get(symbol, defaults.get(symbol, 0.85))

    def place_trade(self, symbol, direction, amount, duration_secs=60):
        from selenium.webdriver.common.by import By
        amount = round(max(1.0, float(amount)), 2)

        try:
            # Chanje aktif si nesesè
            self._driver.get(f"https://quotex.io/en/trade/{symbol.lower()}")
            time.sleep(2)

            # Mete montant
            amt_fields = self._driver.find_elements(By.CSS_SELECTOR, 'input[class*="amount"], input[class*="Amount"], .amount-input input')
            for field in amt_fields:
                try:
                    field.clear(); field.send_keys(str(amount)); break
                except: pass

            # Klike Up oswa Down
            if direction == "BUY":
                btns = self._driver.find_elements(By.CSS_SELECTOR, '.up, .call, [class*="up-btn"], button[data-action="up"]')
            else:
                btns = self._driver.find_elements(By.CSS_SELECTOR, '.down, .put, [class*="down-btn"], button[data-action="down"]')

            if btns:
                btns[0].click()
                time.sleep(0.5)
                return {
                    "id": str(int(time.time())),
                    "symbol": symbol,
                    "direction": direction,
                    "amount": amount,
                    "open_time": int(time.time()),
                    "close_time": int(time.time()) + duration_secs,
                    "duration": duration_secs,
                    "balance_after": self._balance - amount,
                    "account_type": self._account_type,
                }
        except Exception as e:
            raise Exception(f"Selenium trade echwe: {e}")

        raise Exception("Pa jwenn bouton trade")

    def wait_trade_result(self, trade_id, open_price, amount, timeout=120):
        bal_before = self.balance
        time.sleep(timeout + 3)
        new_bal = self._get_balance_selenium()
        self._balance = new_bal
        pnl = new_bal - bal_before
        return {"won": pnl > 0, "pnl": round(pnl, 2), "close_price": 0, "balance": new_bal}

    def set_account_type(self, acc_type):
        self._account_type = acc_type

    def close(self):
        if self._driver:
            try: self._driver.quit()
            except: pass


# ═══════════════════════════════════════════════════════════
# ██████  QUOTEX HYBRID CLIENT — WebSocket + Selenium  ██████
# ═══════════════════════════════════════════════════════════
class QuotexClient:
    """
    Client hibrid: eseye WebSocket dabò, Selenium si echwe.
    Sèl interface pou rès bot la.
    """
    def __init__(self, email, password):
        self.email    = email
        self.password = password
        self._ws_client  = QuotexWebSocketClient(email, password)
        self._sel_client = None  # Kreye lazyman si nesesè
        self._active     = None  # kliyan aktif
        self._account_type = "PRACTICE"

    def connect(self):
        # Eseye WebSocket an premye
        try:
            bal = self._ws_client.connect()
            self._active = self._ws_client
            logger.info(f"Quotex konekte via WebSocket | Balans: ${bal:.2f}")
            return bal
        except Exception as e:
            logger.warning(f"WebSocket echwe ({e}) — ap eseye Selenium...")

        # Fallback Selenium
        try:
            self._sel_client = QuotexSeleniumClient(self.email, self.password)
            bal = self._sel_client.connect()
            self._active = self._sel_client
            logger.info(f"Quotex konekte via Selenium | Balans: ${bal:.2f}")
            return bal
        except Exception as e2:
            raise Exception(f"Echèk koneksyon Quotex — WebSocket: premye erè | Selenium: {e2}")

    def get_candles(self, symbol, count=200, granularity=60):
        return self._active.get_candles(symbol, count, granularity)

    def get_payout(self, symbol):
        return self._active.get_payout(symbol)

    def get_balance_sync(self):
        return self._active.get_balance_sync()

    def place_trade(self, symbol, direction, amount, duration_secs=60):
        return self._active.place_trade(symbol, direction, amount, duration_secs)

    def wait_trade_result(self, trade_id, open_price, amount, timeout=120):
        return self._active.wait_trade_result(trade_id, open_price, amount, timeout)

    def set_account_type(self, acc_type):
        self._account_type = acc_type
        self._active.set_account_type(acc_type)

    @property
    def balance(self):
        return self._active.balance

    def close(self):
        if self._ws_client:
            self._ws_client.close()
        if self._sel_client:
            self._sel_client.close()


# ═══════════════════════════════════════════════════════════
# INDIKATÈ TEKNIK (konsève TOUT oryajinal)
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
    for i in range(p-1,len(trs)):
        atr_vals.append(sum(trs[i-p+1:i+1])/p)
    if not atr_vals: return "NONE", 0.0
    n=len(atr_vals); hl2=[(highs[i+1]+lows[i+1])/2 for i in range(n)]
    upper_basic=[hl2[i]+mult*atr_vals[i] for i in range(n)]
    lower_basic=[hl2[i]-mult*atr_vals[i] for i in range(n)]
    upper=list(upper_basic); lower=list(lower_basic)
    for i in range(1,n):
        upper[i]=min(upper_basic[i],upper[i-1]) if closes[i+p-1]<=upper[i-1] else upper_basic[i]
        lower[i]=max(lower_basic[i],lower[i-1]) if closes[i+p-1]>=lower[i-1] else lower_basic[i]
    trend_up=closes[-1]>lower[-1]; trend_prev=closes[-2]>lower[-2] if len(closes)>=2 else trend_up
    price=closes[-1]
    if trend_up:
        dist=(price-lower[-1])/max(atr_vals[-1],0.0001); conf=min(0.92,0.75+min(dist*0.04,0.17))
        if not trend_prev: return "BUY",min(0.92,conf+0.05)
        return "BUY",conf
    else:
        dist=(upper[-1]-price)/max(atr_vals[-1],0.0001); conf=min(0.92,0.75+min(dist*0.04,0.17))
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
    ha=[]; prev_o=(candles[0]["open"]+candles[0]["close"])/2; prev_c=(candles[0]["open"]+candles[0]["high"]+candles[0]["low"]+candles[0]["close"])/4
    for c in candles:
        ha_c=(c["open"]+c["high"]+c["low"]+c["close"])/4; ha_o=(prev_o+prev_c)/2
        ha_h=max(c["high"],ha_o,ha_c); ha_l=min(c["low"],ha_o,ha_c)
        ha.append({"open":ha_o,"high":ha_h,"low":ha_l,"close":ha_c}); prev_o=ha_o; prev_c=ha_c
    recent=ha[-lookback:]; bullish=[b for b in recent if b["close"]>b["open"]]; bearish=[b for b in recent if b["close"]<b["open"]]
    if len(bullish)==lookback:
        bodies=[abs(b["close"]-b["open"]) for b in bullish]; growing=bodies[-1]>=bodies[0]*0.7
        return "BUY",0.83 if growing else 0.77
    if len(bearish)==lookback:
        bodies=[abs(b["close"]-b["open"]) for b in bearish]; growing=bodies[-1]>=bodies[0]*0.7
        return "SELL",0.83 if growing else 0.77
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
    cl=[x["close"] for x in c]; hi=max(x["high"] for x in c[-60:]); lo=min(x["low"] for x in c[-60:])
    rng=hi-lo
    if rng==0: return "NONE",0
    r=rsi(cl); price=cl[-1]
    for lvl,conf in [(hi-0.618*rng,0.82),(hi-0.5*rng,0.78),(hi-0.382*rng,0.75)]:
        if abs(price-lvl)/max(lvl,0.0001)<0.001:
            if r<40 and price>lo+(rng*0.2): return "BUY",conf
            if r>60 and price<hi-(rng*0.2): return "SELL",conf
    return "NONE",0

def strat_rsi(c):
    cl=[x["close"] for x in c]
    if len(cl)<25: return "NONE",0
    r=rsi(cl); r2=rsi(cl[:-3]) if len(cl)>3 else r
    e50=ema(cl,50) if len(cl)>=50 else None
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
    cl=[x["close"] for x in c]; hi20=max(x["high"] for x in c[-21:-1]); lo20=min(x["low"] for x in c[-21:-1])
    r=rsi(cl)
    if cl[-1]>hi20 and cl[-2]<=hi20 and 50<r<75: return "BUY",0.80
    if cl[-1]<lo20 and cl[-2]>=lo20 and 25<r<50: return "SELL",0.80
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
    W=[2.8,2.2,1.8,1.5,1.2,0.8,1.6,1.9]
    score=sum(f[i]*W[i] for i in range(8)); max_score=sum(W); score_norm=score/max_score
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
    return {"pp":pp,"r1":r1,"r2":r2,"r3":r3,"s1":s1,"s2":s2,"s3":s3,"fib_r1":pp+0.382*rng,"fib_r2":pp+0.618*rng,"fib_s1":pp-0.382*rng,"fib_s2":pp-0.618*rng}

def pivot_signal(candles, trend):
    pv=calc_pivot_points(candles)
    if not pv: return False,0.0
    price=candles[-1]["close"]; tol=0.008
    if trend=="TRENDING_UP":
        for lvl in [pv["s1"],pv["s2"],pv["fib_s1"],pv["fib_s2"],pv["pp"]]:
            if abs(price-lvl)/max(lvl,0.0001)<tol:
                return True,0.07 if lvl in (pv["s1"],pv["fib_s1"]) else 0.05
    elif trend=="TRENDING_DN":
        for lvl in [pv["r1"],pv["r2"],pv["fib_r1"],pv["fib_r2"],pv["pp"]]:
            if abs(price-lvl)/max(lvl,0.0001)<tol:
                return True,0.07 if lvl in (pv["r1"],pv["fib_r1"]) else 0.05
    return False,0.0

def market_regime(candles):
    if len(candles)<20: return "UNKNOWN",0
    cl=[x["close"] for x in candles]; adx,pdi,mdi=calc_adx_full(candles,14)
    at=atr(candles); mid_val=sum(cl[-20:])/20 if len(cl)>=20 else cl[-1]
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
    classic_fns=[(strat_ema,1.4),(strat_rsi,1.6),(strat_macd,1.5),(strat_smc,1.7),(strat_breakout,1.4),(strat_ob,1.5),(strat_stoch,1.3),(strat_ai,1.8),(strat_scalping,1.2),(strat_fibonacci,1.4)]
    buy_score=sell_score=0.0; buy_cnt=sell_cnt=0; buy_confs=[]; sell_confs=[]; NEW_WEIGHT=2.5
    for sig,conf,w in [(st_sig,st_conf,NEW_WEIGHT),(ha_sig,ha_conf,NEW_WEIGHT),(ce_sig,ce_conf,NEW_WEIGHT)]:
        if sig=="BUY" and conf>=min_per_conf: buy_score+=conf*w; buy_cnt+=1; buy_confs.append(conf)
        elif sig=="SELL" and conf>=min_per_conf: sell_score+=conf*w; sell_cnt+=1; sell_confs.append(conf)
    if vw_sig=="BUY" and vw_conf>=min_per_conf: buy_score+=vw_conf*1.8; buy_cnt+=1; buy_confs.append(vw_conf)
    elif vw_sig=="SELL" and vw_conf>=min_per_conf: sell_score+=vw_conf*1.8; sell_cnt+=1; sell_confs.append(vw_conf)
    for fn,w in classic_fns:
        try:
            s,conf=fn(c)
            if s=="BUY" and conf>=min_per_conf: buy_score+=conf*w; buy_cnt+=1; buy_confs.append(conf)
            elif s=="SELL" and conf>=min_per_conf: sell_score+=conf*w; sell_cnt+=1; sell_confs.append(conf)
        except: pass
    if regime=="VOLATILE": return "NONE",0
    dom_ratio=1.15
    if regime=="RANGING":
        new_sigs=[st_sig,ha_sig,ce_sig]
        buy_new=sum(1 for s in new_sigs if s=="BUY"); sell_new=sum(1 for s in new_sigs if s=="SELL")
        if buy_new>=2 and buy_cnt>=min_strats and buy_score>sell_score*dom_ratio:
            in_piv,piv_b=pivot_signal(c,"TRENDING_UP")
            return "BUY",round(min(0.92,0.74+(buy_score/max(buy_cnt,1)/5.0)*0.12+piv_b),3)
        if sell_new>=2 and sell_cnt>=min_strats and sell_score>buy_score*dom_ratio:
            in_piv,piv_b=pivot_signal(c,"TRENDING_DN")
            return "SELL",round(min(0.92,0.74+(sell_score/max(sell_cnt,1)/5.0)*0.12+piv_b),3)
        return "NONE",0
    if regime=="TRENDING_UP" and buy_cnt>=min_strats and buy_score>sell_score*dom_ratio:
        in_piv,piv_b=pivot_signal(c,"TRENDING_UP"); adx_b=min(0.05,adx/500)
        return "BUY",round(min(0.95,0.75+(buy_score/max(buy_cnt,1)/5.0)*0.13+piv_b+adx_b),3)
    if regime=="TRENDING_DN" and sell_cnt>=min_strats and sell_score>buy_score*dom_ratio:
        in_piv,piv_b=pivot_signal(c,"TRENDING_DN"); adx_b=min(0.05,adx/500)
        return "SELL",round(min(0.95,0.75+(sell_score/max(sell_cnt,1)/5.0)*0.13+piv_b+adx_b),3)
    return "NONE",0

def strat_deriv_pro_elite(c):
    if len(c)<50: return "NONE",0
    cl=[x["close"] for x in c]; hi=[x["high"] for x in c]; lo_=[x["low"] for x in c]
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50) if len(cl)>=50 else None
    if not e9 or not e21 or len(e9)<3 or len(e21)<3: return "NONE",0
    r=rsi(cl,14); at=atr(c); m,sig_=macd(cl)
    macd_hist=m-sig_; m_prev,sig_prev=macd(cl[:-1]) if len(cl)>=2 else (m,sig_)
    macd_hist_prev=m_prev-sig_prev; up_bb,mid_bb,lo_bb=bb(cl,20,2.0)
    k=stoch_k(c,14); kp=stoch_k(c[:-2]) if len(c)>2 else k
    if at==0 or not mid_bb: return "NONE",0
    atr_pct=at/mid_bb*100
    if atr_pct<0.01 or atr_pct>5.0: return "NONE",0
    adx,pdi,mdi=calc_adx_full(c,14)
    if adx<12: return "NONE",0
    trend_up=(e9[-1]>e21[-1]); trend_down=(e9[-1]<e21[-1])
    if e50:
        trend_up=trend_up and cl[-1]>e50[-1]*0.998
        trend_down=trend_down and cl[-1]<e50[-1]*1.002
    if not trend_up and not trend_down: return "NONE",0
    if trend_up and not(e9[-1]>e9[-2] or e21[-1]>e21[-2]): return "NONE",0
    if trend_down and not(e9[-1]<e9[-2] or e21[-1]<e21[-2]): return "NONE",0
    hi20=max(hi[-21:-1]); lo20=min(lo_[-21:-1]); hi10=max(hi[-11:-1]); lo10=min(lo_[-11:-1])
    roc3=(cl[-1]-cl[-4])/max(abs(cl[-4]),0.001)*100 if len(cl)>=4 else 0
    roc5v=(cl[-1]-cl[-6])/max(abs(cl[-6]),0.001)*100 if len(cl)>=6 else 0
    last_body=abs(cl[-1]-c[-1]["open"]); last_range=max(c[-1]["high"]-c[-1]["low"],0.00001)
    body_ratio=last_body/last_range; st_sig,_=supertrend(c,p=10,mult=3.0)
    if trend_up:
        score=0.0
        bo_score=0.0
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
        score=0.0
        bo_score=0.0
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

STRATEGIES = {
    "confluence":   strat_confluence_elite,
    "deriv_pro":    strat_deriv_pro_elite,
    "supertrend":   supertrend,
    "heikin_ashi":  heikin_ashi_trend,
    "chandelier":   chandelier_exit,
    "ai":           strat_ai,
    "ema":          strat_ema,
    "fibonacci":    strat_fibonacci,
    "rsi":          strat_rsi,
    "macd_bollinger": strat_macd,
    "breakout":     strat_breakout,
    "smc":          strat_smc,
    "order_block":  strat_ob,
    "stoch_ema":    strat_stoch,
    "scalping_pro": strat_scalping,
}

def add_log(st, msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    st["log"].insert(0, {"time": ts, "msg": msg, "level": level})
    st["log"] = st["log"][:80]
    logger.info(f"[{st['uid'][:8]}] {msg}")

# ═══════════════════════════════════════════════════════════
# ██████  QUOTEX TRADING LOOP  ██████
# Rise/Fall = Up/Down binary options
# Martingale + 3-pèt pòz konsève
# ═══════════════════════════════════════════════════════════
def trading_loop(st, bot_id=None):
    if bot_id and st.get("bot_id") != bot_id: return

    cfg      = st["config"]
    symbol   = cfg.get("symbol", "EURUSD")
    strategy = cfg.get("strategy", "confluence")
    lot      = float(cfg.get("lot", 1.0))
    tf       = int(cfg.get("tf_secs", 60))
    min_conf = float(cfg.get("min_conf", 0.65))

    # Granularite selon timeframe
    gran_map = {60: 60, 300: 300, 900: 900, 3600: 3600, 14400: 14400}
    gran = gran_map.get(tf, 60)

    fn = STRATEGIES.get(strategy, strat_confluence_elite)

    base_lot    = round(max(1.0, lot), 2)
    current_lot = base_lot
    consec_losses = 0
    total_lost    = 0.0

    MAX_LOSSES_BEFORE_PAUSE = 3
    PAUSE_WAIT_SECS         = 45

    add_log(st, f"🚀 BonheurBot v7 QUOTEX | {symbol} | {strategy} | TF:{tf//60}min | Conf:{min_conf:.0%}")
    add_log(st, f"📌 Expiry: {tf}sek | Mise min: $1.00 | Kont: {cfg.get('account_type','PRACTICE')}")

    while st["running"]:
        if bot_id and st.get("bot_id") != bot_id:
            add_log(st, "⏹ Bot anile", "WARN"); return

        _target = float(cfg.get("profit_target", 0))
        _loss   = float(cfg.get("loss_limit", 0))
        if _target > 0 and st["total_pnl"] >= _target:
            add_log(st, f"🎯 OBJEKTIF ${_target:.2f} RIVE! Bot kanpe!", "SUCCESS")
            st["running"] = False; break
        if _loss > 0 and st["total_pnl"] <= -abs(_loss):
            add_log(st, f"🛑 LIMIT PÈT ${_loss:.2f} RIVE! Bot kanpe!", "ERROR")
            st["running"] = False; break

        try:
            api = st.get("quotex_api")
            if not api:
                add_log(st, "Quotex pa konekte — STOP", "ERROR")
                st["running"] = False; break

            # Jwenn balans
            try:
                b = api.get_balance_sync()
                if b and b > 0: st["balance"] = b
            except:
                add_log(st, "⚠ Pa jwenn balans — tann...", "WARN")
                time.sleep(15); continue

            # Pran bouji
            try:
                candles = api.get_candles(symbol, 200, gran)
            except Exception as e:
                add_log(st, f"⚠ Erè bouji: {e}", "WARN")
                time.sleep(30); continue

            if len(candles) < 20:
                add_log(st, f"Pa ase done ({len(candles)}) — tann...", "WARN")
                time.sleep(30); continue

            regime, regime_score = market_regime(candles)
            adx_val, pdi_val, mdi_val = calc_adx_full(candles, 14)
            st_sig, st_c = supertrend(candles)
            ha_sig, ha_c = heikin_ashi_trend(candles)

            add_log(st,
                f"📡 {len(candles)} bouji | {symbol} | {regime} | ADX:{adx_val:.0f} | "
                f"ST:{st_sig}({st_c:.0%}) | HA:{ha_sig}({ha_c:.0%})")

            # Pòz apre 3 pèt konsekitif
            if consec_losses >= MAX_LOSSES_BEFORE_PAUSE:
                mache_bon = regime in ("TRENDING_UP", "TRENDING_DN", "RANGING") and adx_val >= 12
                if regime == "RANGING":
                    mache_bon = (st_sig != "NONE") and (ha_sig != "NONE") and adx_val >= 10
                if not mache_bon:
                    add_log(st,
                        f"⏸ PÒZ APRE {consec_losses} PÈT | "
                        f"Mache:{regime}(ADX:{adx_val:.0f}) — "
                        f"Ap tann siyal... ({PAUSE_WAIT_SECS}sek)", "WARN")
                    time.sleep(PAUSE_WAIT_SECS); continue
                else:
                    add_log(st, f"✅ MACHE BON ANKÒ! {regime} ADX:{adx_val:.0f} | Reprann avèk ${current_lot:.2f}", "SUCCESS")

            if regime == "VOLATILE":
                add_log(st, f"⏸ Mache VOLATILE — pa trade. Tann {min(tf,120)}sek...", "WARN")
                time.sleep(min(tf, 120)); continue

            # Kouri strategy
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

            # Filtre direksyon kont regime
            if sig == "BUY" and regime == "TRENDING_DN":
                add_log(st, f"⛔ REJTE BUY — Mache ap DESANN", "WARN")
                time.sleep(tf); continue
            if sig == "SELL" and regime == "TRENDING_UP":
                add_log(st, f"⛔ REJTE SELL — Mache ap MONTE", "WARN")
                time.sleep(tf); continue

            # Konfidans adaptif
            adaptive_conf = min_conf + (0.02 if consec_losses == 1 else (0.04 if consec_losses >= 2 else 0))
            if sig == "NONE" or conf < adaptive_conf:
                reason = "Pa gen siyal" if sig == "NONE" else f"Conf {conf:.0%} < {adaptive_conf:.0%}"
                add_log(st, f"⏭ {reason} — tann pwochen bouji...")
                time.sleep(tf); continue

            # Tcheke balans
            if st["balance"] < current_lot:
                add_log(st, f"⚠ Balans ${st['balance']:.2f} < Mise ${current_lot:.2f} — reset", "WARN")
                current_lot = base_lot; consec_losses = 0; total_lost = 0.0

            # Jwenn payout reyèl
            payout = api.get_payout(symbol)
            entry  = candles[-1]["close"]

            add_log(st,
                f"⚡ {sig} @ {entry:.5f} | Conf:{conf:.0%} | ADX:{adx_val:.0f} | "
                f"Mise:${current_lot:.2f} | Payout:{payout:.0%} | Expiry:{tf}sek")

            bal_before = st["balance"]
            pnl = 0.0; ok = False

            try:
                # Plase trade
                r = api.place_trade(symbol, sig, max(1.0, current_lot), duration_secs=tf)
                trade_id = r.get("id", str(int(time.time())))

                add_log(st, f"⏳ #{trade_id[:12]} | {sig} ${current_lot:.2f} | Ap tann {tf}sek...", "SUCCESS")

                # Tann rezilta
                result = api.wait_trade_result(trade_id, entry, current_lot, timeout=tf)

                if result:
                    ok = True
                    won = result.get("won", False)
                    if won:
                        pnl = round(current_lot * payout, 2)
                    else:
                        pnl = -current_lot

                    new_bal = result.get("balance", bal_before + pnl)
                    st["balance"] = new_bal

                    if won:
                        add_log(st, f"✅ GENYEN! +${pnl:.2f} | Bal:${new_bal:.2f}", "SUCCESS")
                    else:
                        add_log(st, f"❌ PÈDI -${current_lot:.2f} | Bal:${new_bal:.2f}", "WARN")

            except Exception as e:
                add_log(st, f"Trade echwe: {e}", "ERROR")
                time.sleep(15); continue

            if ok:
                if pnl > 0:
                    prev_losses = consec_losses
                    current_lot = base_lot; consec_losses = 0; total_lost = 0.0
                    if prev_losses > 0:
                        add_log(st, f"🏆 REKIPERE! (te gen {prev_losses} pèt) ← Reset ${base_lot:.2f}", "SUCCESS")
                else:
                    loss = abs(pnl) if abs(pnl) > 0.01 else current_lot
                    total_lost += loss; consec_losses += 1

                    if consec_losses < MAX_LOSSES_BEFORE_PAUSE:
                        # Martingale: rekipere tout pèt + base_lot nan yon sèl trade
                        next_lot = round((total_lost + base_lot) / payout, 2)
                        current_lot = max(1.0, min(next_lot, 500.0))
                        add_log(st,
                            f"⚠ PÈT #{consec_losses}/{MAX_LOSSES_BEFORE_PAUSE-1} | "
                            f"Total:${total_lost:.2f} | "
                            f"Prochèn:${current_lot:.2f} | Payout:{payout:.0%}", "WARN")
                    else:
                        next_lot = round((total_lost + base_lot) / payout, 2)
                        current_lot = max(1.0, min(next_lot, 500.0))
                        add_log(st,
                            f"🚨 3 PÈT AFILE! PÒZE OTOMATIK | "
                            f"Total:${total_lost:.2f} | "
                            f"Mise rekipere:${current_lot:.2f} | "
                            f"Ap tann mache...", "WARN")

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
                    "payout":   f"{payout:.0%}",
                    "pnl":      round(pnl, 2),
                    "status":   "won" if pnl > 0 else "lost",
                    "regime":   regime,
                    "broker":   "quotex",
                }
                st["trades"].insert(0, trade)
                st["total_pnl"] += pnl

                # Voye profit 0.5% si genyen
                if pnl > 0:
                    ps = round(pnl * PROFIT_PCT, 2)
                    st["profit_sent"] += ps

        except Exception as e:
            add_log(st, f"Erè loop: {e}", "ERROR")

        time.sleep(2)  # Ti poz ant trades

    add_log(st, "⏹ BonheurBot v7 Quotex arrêté")


# ═══════════════════════════════════════════════════════════
# BACKTEST ENGINE (adapte pou Quotex binary options)
# ═══════════════════════════════════════════════════════════
def run_backtest(candles, strat_name, bal=10000, lot=1.0, payout=0.85):
    fn = STRATEGIES.get(strat_name, strat_confluence_elite)
    equity = [bal]; wins = losses = 0; trades = []
    current_bal = bal

    for i in range(50, len(candles) - 1):
        s, conf = fn(candles[:i+1])
        if s == "NONE" or conf < 0.65: continue

        entry = candles[i]["close"]
        nxt   = candles[i+1]["close"]

        # Binary option: si direksyon kòrèk → genyen payout%, sinon pèdi tout mise
        if s == "BUY":
            won = nxt > entry
        else:
            won = nxt < entry

        if won:
            pnl = round(lot * payout, 2); wins += 1
        else:
            pnl = -lot; losses += 1

        current_bal += pnl
        equity.append(round(current_bal, 2))
        trades.append({"s": s, "e": round(entry, 5), "pnl": round(pnl, 2)})
        if len(trades) >= 200: break

    tot = wins + losses; net = round(equity[-1] - equity[0], 2)
    dd = 0; pk = equity[0]
    for e in equity:
        if e > pk: pk = e
        dd = max(dd, (pk - e) / pk * 100 if pk else 0)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))

    return {
        "trades": tot, "wins": wins, "losses": losses,
        "win_rate": round(wins/tot*100, 1) if tot else 0,
        "net_pnl": net, "return_pct": round(net/equity[0]*100, 2),
        "max_dd": round(dd, 2), "pf": round(gp/gl, 2) if gl else 999,
        "payout_used": f"{payout:.0%}",
        "equity": equity[-50:],
    }


# ═══════════════════════════════════════════════════════════
# ROUTES FLASK
# ═══════════════════════════════════════════════════════════
@app.route("/api/connect", methods=["POST"])
def api_connect():
    st = get_state()
    try:
        d = request.json
        email    = d.get("email", "").strip()
        password = d.get("password", "").strip()
        acc_type = d.get("account_type", "PRACTICE").upper()

        if not email or not password:
            return jsonify({"ok": False, "error": "Mete email ak password Quotex ou"})

        api = QuotexClient(email, password)
        bal = api.connect()
        api.set_account_type(acc_type)

        st["quotex_api"]   = api
        st["broker"]       = "quotex"
        st["connected"]    = True
        st["account_type"] = acc_type

        if acc_type == "PRACTICE":
            st["demo_balance"] = bal
            st["balance"]      = bal
        else:
            st["balance"] = bal

        method = "WebSocket" if isinstance(api._active, QuotexWebSocketClient) else "Selenium"
        add_log(st, f"✓ Quotex konekte via {method} | {acc_type} | ${bal:.2f}")

        return jsonify({
            "ok": True,
            "balance": bal,
            "broker": "quotex",
            "account_type": acc_type,
            "method": method,
        })
    except Exception as e:
        logger.error(f"Connect: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/start", methods=["POST"])
def api_start():
    st = get_state()
    if not st.get("access"):
        return jsonify({"ok": False, "error": "⚠ Ou bezwen yon kòd aksè valid!"})
    if not st["connected"]:
        return jsonify({"ok": False, "error": "Konekte Quotex anvan!"})
    if st["running"]:
        return jsonify({"ok": False, "error": "Bot déjà ap kouri"})

    d = request.json or {}
    tf_map = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}

    st["config"] = {
        "broker":        "quotex",
        "symbol":        d.get("symbol", "EURUSD"),
        "strategy":      d.get("strategy", "confluence"),
        "lot":           d.get("lot", 1.0),
        "tf_secs":       tf_map.get(d.get("tf", "1m"), 60),
        "min_conf":      d.get("min_conf", 0.65),
        "profit_target": float(d.get("profit_target", 0)),
        "loss_limit":    float(d.get("loss_limit", 0)),
        "account_type":  st.get("account_type", "PRACTICE"),
    }

    import random, string
    bot_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    st["running"] = True; st["bot_id"] = bot_id

    threading.Thread(target=trading_loop, args=(st, bot_id), daemon=True).start()
    add_log(st, f"▶ Bot démarre | {st['config']['symbol']} | {st['config']['strategy']}")
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    st = get_state()
    st["running"] = False; st["bot_id"] = None
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    st = get_state()
    return jsonify({
        "connected":    st["connected"],
        "broker":       st["broker"],
        "account_type": st.get("account_type", "PRACTICE"),
        "running":      st["running"],
        "balance":      round(st["balance"], 2),
        "pnl":          round(st["total_pnl"], 2),
        "profit_sent":  round(st["profit_sent"], 4),
        "trades":       st["trades"][:20],
        "log":          st["log"][:30],
        "config":       st["config"],
    })


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    st = get_state()
    try:
        d = request.json or {}
        symbol  = d.get("symbol", "EURUSD")
        strat   = d.get("strategy", "confluence")
        lot     = float(d.get("lot", 1.0))
        payout  = float(d.get("payout", 0.85))
        bal     = float(d.get("balance", 10000))

        candles = []
        if st.get("quotex_api"):
            candles = st["quotex_api"].get_candles(symbol, 500, 3600)

        if len(candles) < 100:
            # Jenere candles synthetik pou backtest
            client = QuotexWebSocketClient("", "")
            candles = client._generate_synthetic_candles(symbol, 500)

        if len(candles) < 100:
            return jsonify({"ok": False, "error": f"Pa ase done ({len(candles)}) — konekte Quotex anvan"})

        r = run_backtest(candles, strat, bal, lot, payout)
        return jsonify({"ok": True, "result": r})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/login", methods=["POST"])
def api_login():
    st = get_state()
    d = request.json or {}
    token = d.get("session_token", "").strip()
    code  = d.get("code", "").strip().upper()
    if token:
        ok, msg_text = validate_session(token)
        if ok:
            with _sess_lock: is_adm = _sessions.get(token, {}).get("is_admin", False)
            st["access"] = True; st["session_token"] = token; st["is_admin"] = is_adm
            return jsonify({"ok": True, "msg": msg_text, "session_token": token, "is_admin": is_adm})
        else:
            st["access"] = False
            return jsonify({"ok": False, "msg": msg_text, "need_code": True})
    if not code:
        return jsonify({"ok": False, "msg": "Mete kòd aksè ou a", "need_code": True})
    ok, msg_text = check_access(code)
    if ok:
        use_code(code)
        new_token, expire = create_session()
        is_adm = ACCESS_CODES.get(code, {}).get("is_adm", False) or ACCESS_CODES.get(code, {}).get("created_at") is None
        with _sess_lock:
            _sessions[new_token]["is_admin"] = is_adm
            _save_sessions()
        st["access"] = True; st["session_token"] = new_token; st["is_admin"] = is_adm
        msg_out = "✓ Aksè Admin! 30 jou rete" if is_adm else "✓ Aksè akòde! 30 jou rete"
        return jsonify({"ok": True, "msg": msg_out, "session_token": new_token, "expire": expire, "is_admin": is_adm})
    return jsonify({"ok": False, "msg": msg_text, "need_code": True})


def require_admin(d):
    token = d.get("admin_token", "").strip()
    if not token: return False
    with _sess_lock: sess = _sessions.get(token)
    if not sess: return False
    return sess.get("is_admin", False)


@app.route("/api/admin/codes", methods=["POST"])
def admin_get_codes():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize — admin sèlman"})
    now = time.time(); codes = []
    for c, entry in ACCESS_CODES.items():
        if entry["created_at"] is None or entry.get("is_adm"):
            status = "ADM"; remaining = "∞"
        elif entry["used"]:
            status = "ITILIZE"; remaining = "0"
        else:
            age = now - entry["created_at"]
            if age > CODE_TTL_SECONDS: status = "EKSPIRE"; remaining = "0"
            else: status = "AKTIF"; remaining = str(int((CODE_TTL_SECONDS - age) / 86400)) + " jou"
        codes.append({"code": c, "status": status, "remaining": remaining, "used": entry["used"], "is_adm": entry.get("is_adm", False) or entry["created_at"] is None})
    today = date.today()
    active_sess = sum(1 for s in _sessions.values() if date.fromisoformat(s["expire"]) > today)
    return jsonify({"ok": True, "codes": codes, "total_sessions": active_sess})


@app.route("/api/admin/add_code", methods=["POST"])
def admin_add_code():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize — admin sèlman"})
    code = d.get("code", "").strip().upper()
    if not code or len(code) < 3: return jsonify({"ok": False, "error": "Kòd dwe gen 3+ karaktè"})
    if code in ACCESS_CODES: return jsonify({"ok": False, "error": "Kòd sa deja egziste"})
    is_adm = d.get("is_adm", False)
    ACCESS_CODES[code] = {"created_at": None if is_adm else time.time(), "used": False, "is_adm": is_adm}
    return jsonify({"ok": True, "msg": f"✓ Kòd {code} kreye [{'Admin' if is_adm else 'Itilizatè 1 mwa'}]"})


@app.route("/api/admin/revoke_code", methods=["POST"])
def admin_revoke_code():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize — admin sèlman"})
    code = d.get("code", "").strip().upper()
    if not code or code not in ACCESS_CODES: return jsonify({"ok": False, "error": "Kòd pa jwenn"})
    if code == "BONHEURWIIN": return jsonify({"ok": False, "error": "Pa ka revoke kòd ADM prensipal"})
    del ACCESS_CODES[code]
    return jsonify({"ok": True, "msg": f"✓ Kòd {code} revoke"})


@app.route("/api/admin/reset_code", methods=["POST"])
def admin_reset_code():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize — admin sèlman"})
    code = d.get("code", "").strip().upper()
    if code not in ACCESS_CODES: return jsonify({"ok": False, "error": "Kòd pa jwenn"})
    ACCESS_CODES[code]["used"] = False
    if not (ACCESS_CODES[code].get("is_adm") or ACCESS_CODES[code]["created_at"] is None):
        ACCESS_CODES[code]["created_at"] = time.time()
    return jsonify({"ok": True, "msg": f"✓ Kòd {code} reset"})


@app.route("/api/admin/users", methods=["POST"])
def admin_get_users():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize — admin sèlman"})
    users = []
    with _user_lock:
        for uid, st in _user_states.items():
            users.append({
                "uid": uid[:8] + "...", "connected": st.get("connected", False),
                "broker": st.get("broker", "—"), "running": st.get("running", False),
                "balance": round(st.get("balance", 0), 2), "pnl": round(st.get("total_pnl", 0), 2),
                "trades": len(st.get("trades", [])), "symbol": st.get("config", {}).get("symbol", "—"),
                "strategy": st.get("config", {}).get("strategy", "—"),
                "account_type": st.get("account_type", "PRACTICE"),
            })
    return jsonify({"ok": True, "users": users, "total": len(users)})


@app.route("/api/admin/stop_user", methods=["POST"])
def admin_stop_user():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize — admin sèlman"})
    uid_prefix = d.get("uid", "").replace("...", "")
    stopped = 0
    with _user_lock:
        for uid, st in _user_states.items():
            if uid.startswith(uid_prefix):
                st["running"] = False; st["bot_id"] = None; stopped += 1
    return jsonify({"ok": True, "msg": f"✓ {stopped} bot(s) kanpe"})


@app.route("/api/admin/sessions", methods=["POST"])
def admin_sessions():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize — admin sèlman"})
    today = date.today(); sessions = []
    with _sess_lock:
        for token, sess in _sessions.items():
            exp = date.fromisoformat(sess["expire"])
            sessions.append({"token": token[:8] + "...", "expire": sess["expire"],
                "days_left": (exp - today).days, "is_admin": sess.get("is_admin", False),
                "active": (exp - today).days > 0})
    return jsonify({"ok": True, "sessions": sessions, "total": len(sessions)})


@app.route("/api/admin/clean_sessions", methods=["POST"])
def admin_clean_sessions():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize — admin sèlman"})
    today = date.today(); count = 0
    with _sess_lock:
        expired = [t for t, s in _sessions.items() if date.fromisoformat(s["expire"]) <= today]
        for t in expired: del _sessions[t]; count += 1
        if count: _save_sessions()
    return jsonify({"ok": True, "msg": f"✓ {count} sesyon ekspire efase"})


@app.route("/api/admin/clear_user", methods=["POST"])
def admin_clear_user():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize — admin sèlman"})
    uid_prefix = d.get("uid", "").replace("...", "")
    cleared = 0
    with _user_lock:
        for uid, st in _user_states.items():
            if uid.startswith(uid_prefix):
                st["trades"] = []; st["total_pnl"] = 0.0; st["profit_sent"] = 0.0; st["log"] = []; cleared += 1
    return jsonify({"ok": True, "msg": f"✓ {cleared} itilizatè efase"})


@app.route("/api/admin/clear_trades", methods=["POST"])
def admin_clear_trades():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok": False, "error": "Aksè refize — admin sèlman"})
    uid_prefix = d.get("uid", "").replace("...", "")
    cleared = 0
    with _user_lock:
        for uid, st in _user_states.items():
            if uid.startswith(uid_prefix):
                st["trades"] = []; cleared += 1
    return jsonify({"ok": True, "msg": f"✓ {cleared} itilizatè: trades efase (log + pnl konsève)"})


@app.route("/")
def index():
    return render_template_string(HTML)


# ═══════════════════════════════════════════════════════════
# UI HTML — QUOTEX EDITION (dark theme, Haitian Creole)
# ═══════════════════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>💰 BonheurBot v7 — Quotex</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700;900&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{background:#040A0F;color:#C8E8F0;font-family:'JetBrains Mono',monospace;font-size:13px}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-thumb{background:#0D2233}
.hdr{background:#071219;border-bottom:1px solid #0D2233;padding:0 20px;display:flex;align-items:center;justify-content:space-between;height:54px;position:sticky;top:0;z-index:99}
.logo{font-size:17px;font-weight:900;letter-spacing:2px;color:#00FF88}
.logo span{color:#C8E8F0}.logo .ver{font-size:10px;color:#FFD600}
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
input[type=password]{letter-spacing:2px}
select option{background:#071219}
.btn{background:transparent;border:1px solid #00FF88;color:#00FF88;border-radius:6px;padding:9px 22px;cursor:pointer;font-size:12px;font-family:inherit;letter-spacing:1px;font-weight:700;transition:.15s}
.btn:hover{background:#00FF8822}
.btn.b{border-color:#00D4FF;color:#00D4FF}.btn.b:hover{background:#00D4FF22}
.btn.r{border-color:#FF3B6B;color:#FF3B6B}.btn.r:hover{background:#FF3B6B22}
.btn.y{border-color:#FFD600;color:#FFD600}.btn.y:hover{background:#FFD60022}
.btn.fw{width:100%}
.al{padding:8px 12px;border-radius:6px;font-size:11px;margin-bottom:10px;line-height:1.5}
.al.ok{background:#00FF8815;color:#00FF88;border:1px solid #00FF8833}
.al.er{background:#FF3B6B15;color:#FF3B6B;border:1px solid #FF3B6B33}
.al.in{background:#00D4FF15;color:#00D4FF;border:1px solid #00D4FF33}
.tag{border-radius:4px;padding:2px 8px;font-size:11px;font-weight:700}
.tg{background:#4A708022;border:1px solid #4A708044;color:#4A7080}
.tb{background:#00FF8822;border:1px solid #00FF8844;color:#00FF88}
.ts{background:#FF3B6B22;border:1px solid #FF3B6B44;color:#FF3B6B}
.ty{background:#FFD60022;border:1px solid #FFD60044;color:#FFD600}
table{width:100%;border-collapse:collapse;font-size:12px}
th{padding:7px 10px;text-align:left;border-bottom:1px solid #0D2233;color:#4A7080;font-size:10px;letter-spacing:1px}
td{padding:7px 10px;border-bottom:1px solid #0D223320}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
.dl{background:#00FF88;box-shadow:0 0 8px #00FF88}.di{background:#3A6070}
.le{padding:5px 8px;border-bottom:1px solid #0D223318;font-size:11px}
.lt{color:#4A7080;margin-right:8px}
.lS{color:#00FF88}.lP{color:#FFD600}.lE{color:#FF3B6B}.lW{color:#FFD600}.lI{color:#C8E8F0}
</style>
</head>
<body>

<!-- ══ LOGIN PAGE ══ -->
<div id="login-page" style="display:none;min-height:100vh;background:#040A0F;align-items:center;justify-content:center;flex-direction:column">
  <div style="background:#071219;border:1px solid #0D2233;border-radius:12px;padding:40px;max-width:420px;width:90%;text-align:center">
    <div style="font-size:36px;margin-bottom:8px">💰</div>
    <div style="font-size:20px;font-weight:900;color:#00FF88;letter-spacing:2px;margin-bottom:4px">BonheurBot Pro</div>
    <div style="color:#FFD600;font-size:11px;margin-bottom:4px">v7 — QUOTEX EDITION</div>
    <div style="color:#4A7080;font-size:11px;margin-bottom:24px">Trading Bot Pwofesyonèl — Binary Options</div>
    <div style="margin-bottom:16px">
      <div style="color:#4A7080;font-size:10px;letter-spacing:1px;margin-bottom:6px;text-align:left">KÒD AKSÈ</div>
      <input id="login-code" type="text" placeholder="BB-XXXX-XXXX" style="width:100%;background:#020C12;border:1px solid #0D2233;color:#C8E8F0;border-radius:6px;padding:10px 12px;font-size:13px;font-family:inherit;outline:none;box-sizing:border-box;text-transform:uppercase">
    </div>
    <div id="login-err"></div>
    <button id="login-btn" onclick="doLogin()" style="width:100%;background:#00FF8818;border:1px solid #00FF88;color:#00FF88;border-radius:6px;padding:11px;cursor:pointer;font-size:13px;font-family:inherit;font-weight:700;letter-spacing:1px">⚡ ANTRE</button>
    <div style="margin-top:20px;background:#020C12;border:1px solid #0D2233;border-radius:8px;padding:14px;text-align:left">
      <div style="color:#FFD600;font-size:10px;letter-spacing:1px;font-weight:700;margin-bottom:8px">💳 ABÒNMAN — $40 USDT/MWA</div>
      <div style="color:#4A7080;font-size:10px;line-height:1.9">
        1. Voye <span style="color:#00FF88;font-weight:700">$40 USDT</span> sou:<br>
        <span style="color:#C8E8F0;font-size:9px;word-break:break-all;background:#071219;padding:4px 6px;border-radius:4px;display:block;margin:4px 0">0x2ba88a4d6cabaded5d06c75ef3b3efec386acaef</span>
        <span style="color:#FFD600;font-size:9px">⚠ Rezo: BEP20 (BSC) sèlman</span><br><br>
        2. Voye prèv sou WhatsApp:<br>
        <a href="https://wa.me/50942867885" target="_blank" style="display:inline-flex;align-items:center;gap:6px;margin-top:6px;background:#25D36618;border:1px solid #25D36644;color:#25D366;border-radius:6px;padding:6px 12px;text-decoration:none;font-size:11px;font-weight:700">📱 WhatsApp: +509 4286-7885</a>
      </div>
    </div>
  </div>
</div>

<!-- ══ APP PAGE ══ -->
<div id="app-page" style="display:none">
<div class="hdr">
  <div style="display:flex;align-items:center;gap:12px">
    <div class="logo">💰 Bonheur<span>Bot</span> <span class="ver">v7 QUOTEX</span></div>
    <div style="width:1px;height:20px;background:#0D2233"></div>
    <span id="hb" class="tag tg">DISCONNECTED</span>
    <span id="hacc" class="tag ty" style="font-size:10px">—</span>
  </div>
  <div style="display:flex;align-items:center;gap:16px">
    <span><span class="dot di" id="dot"></span><span id="hs" style="color:#3A6070;font-size:11px;letter-spacing:1px">IDLE</span></span>
    <span id="hbal" style="color:#3A6070;font-weight:700;font-size:15px">$0.00</span>
    <span id="sub-info" style="color:#00FF8888;font-size:10px"></span>
    <button onclick="doLogout()" style="background:transparent;border:1px solid #3A6070;color:#3A6070;border-radius:4px;padding:3px 8px;cursor:pointer;font-size:10px;font-family:inherit">DEKONEKTE</button>
  </div>
</div>

<div class="tabs">
  <button class="tab on"  onclick="sw('dashboard',this)">DASHBOARD</button>
  <button class="tab"     onclick="sw('control',this)">KONTWÒL</button>
  <button class="tab"     onclick="sw('strategies',this)">STRATEGIES</button>
  <button class="tab"     onclick="sw('backtest',this)">BACKTEST</button>
  <button class="tab"     onclick="sw('trades',this)">TRADES</button>
  <button class="tab"     onclick="sw('log',this)">LOGS</button>
  <button class="tab" id="tab-admin" style="display:none;color:#FFD600" onclick="sw('admin',this)">⚙ ADMIN</button>
</div>

<div class="wrap">

<!-- ══ DASHBOARD ══ -->
<div id="pg-dashboard" class="pg on">
  <div class="stats">
    <div class="stat"><div class="sl">BALANS</div><div class="sv" id="s-bal" style="color:#00D4FF">$0.00</div></div>
    <div class="stat"><div class="sl">NET P&L</div><div class="sv" id="s-pnl">+$0.00</div></div>
    <div class="stat"><div class="sl">PROFIT</div><div class="sv" id="s-sent" style="color:#FFD600">$0.00</div></div>
    <div class="stat"><div class="sl">TRADES</div><div class="sv" id="s-tr" style="color:#FFD600">0</div></div>
    <div class="stat"><div class="sl">BOT</div><div class="sv" id="s-bot" style="color:#3A6070">IDLE</div></div>
    <div class="stat"><div class="sl">KONT</div><div class="sv" id="s-acc" style="color:#FFD600;font-size:13px">—</div></div>
  </div>
  <div class="g2">
    <div class="box">
      <div class="bt">KONEKSYON QUOTEX</div>
      <div class="iw"><div class="il">EMAIL QUOTEX</div><input id="d-email" type="email" placeholder="email@quotex.io"></div>
      <div class="iw"><div class="il">PASSWORD</div><input id="d-pass" type="password" placeholder="••••••••"></div>
      <div class="iw"><div class="il">TIP KONT</div>
        <select id="d-acc">
          <option value="PRACTICE">📊 Demo (Practice) — Recommande pou kòmanse</option>
          <option value="REAL">💰 Reyèl (Real Money)</option>
        </select>
      </div>
      <div id="cm"></div>
      <button class="btn b fw" onclick="doConn()">⚡ KONEKTE QUOTEX</button>
      <div id="cs" style="margin-top:10px"></div>
      <div style="margin-top:12px;background:#00D4FF08;border:1px solid #00D4FF22;border-radius:6px;padding:10px;font-size:10px;color:#4A7080;line-height:1.9">
        🔒 <span style="color:#00D4FF">WebSocket sekire</span> — koneksyon direk Quotex<br>
        🤖 <span style="color:#FFD600">Selenium fallback</span> — si WS pa disponib<br>
        ⚠ Toujou eseye <span style="color:#00FF88">Demo dabò</span> anvan reyèl!
      </div>
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
        <div class="stat"><div class="sl">PAYOUT</div><div id="s-pay" style="font-size:12px;font-weight:700;color:#00FF88">—</div></div>
      </div>
    </div>
  </div>
  <!-- Info banner Quotex -->
  <div class="box" style="background:#00D4FF08;border-color:#00D4FF22">
    <div class="bt" style="color:#00D4FF">🎯 QUOTEX BINARY OPTIONS — KÒMENTça TRAVAY</div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;font-size:11px;color:#4A7080;line-height:1.9">
      <div><div style="color:#00D4FF;font-weight:700;margin-bottom:4px">📈 UP/DOWN</div>Si prix monte → UP genyen<br>Si prix desann → DOWN genyen<br><span style="color:#00FF88">Menm ak Rise/Fall Deriv</span></div>
      <div><div style="color:#FFD600;font-weight:700;margin-bottom:4px">💰 PAYOUT 80-95%</div>Payout chanje selon aktif<br>Forex ≈ 85% | OTC ≈ 92%<br><span style="color:#FFD600">Live payout via WebSocket</span></div>
      <div><div style="color:#00FF88;font-weight:700;margin-bottom:4px">⏱ EXPIRY</div>1min / 5min / 15min / 1h<br>Bot tann tout expiry<br><span style="color:#00FF88">Martingale apre pèt</span></div>
      <div><div style="color:#FF3B6B;font-weight:700;margin-bottom:4px">🛡 SEKIRITE</div>Mise min $1.00<br>3 pèt = pòz otomatik<br><span style="color:#FF3B6B">Limit pèt obligatwa!</span></div>
    </div>
  </div>
</div>

<!-- ══ KONTWÒL ══ -->
<div id="pg-control" class="pg">
  <div class="g2">
    <div class="box">
      <div class="bt">PARAMÈT BOT QUOTEX</div>

      <div class="iw"><div class="il">AKTIF (SYMBOL)</div>
        <select id="c-sy">
          <optgroup label="── FOREX ──">
            <option value="EURUSD">EUR/USD ★★★ (85%)</option>
            <option value="GBPUSD">GBP/USD ★★★ (85%)</option>
            <option value="USDJPY">USD/JPY ★★ (82%)</option>
            <option value="AUDUSD">AUD/USD ★★ (80%)</option>
            <option value="USDCAD">USD/CAD ★★ (80%)</option>
            <option value="USDCHF">USD/CHF ★★ (80%)</option>
            <option value="EURGBP">EUR/GBP ★★ (80%)</option>
            <option value="EURJPY">EUR/JPY ★★ (82%)</option>
          </optgroup>
          <optgroup label="── OTC (Weekend) ──">
            <option value="EURUSD-OTC">EUR/USD OTC ★★★ (92%)</option>
            <option value="GBPUSD-OTC">GBP/USD OTC ★★★ (92%)</option>
          </optgroup>
          <optgroup label="── CRYPTO ──">
            <option value="BTCUSD">BTC/USD ★★ (82%)</option>
            <option value="ETHUSD">ETH/USD ★★ (80%)</option>
            <option value="LTCUSD">LTC/USD ★ (78%)</option>
          </optgroup>
        </select>
      </div>

      <div class="g2">
        <div class="iw"><div class="il">TIMEFRAME / EXPIRY</div>
          <select id="c-tf">
            <option value="1m">1 minit ⚡ (rapid)</option>
            <option value="5m" selected>5 minit ★★★</option>
            <option value="15m">15 minit ★★★</option>
            <option value="1h">1 è ★★</option>
          </select>
        </div>
        <div class="iw"><div class="il">STRATEGY</div>
          <select id="c-st">
            <option value="confluence">🔥 Confluence ELITE</option>
            <option value="deriv_pro">🚀 Pro ELITE</option>
            <option value="supertrend">📈 SuperTrend</option>
            <option value="heikin_ashi">🕯 Heikin Ashi</option>
            <option value="chandelier">🔔 Chandelier</option>
            <option value="ai">🤖 AI Score</option>
            <option value="smc">🏛 SMC</option>
            <option value="rsi">📉 RSI</option>
            <option value="scalping_pro">⚡ Scalping</option>
          </select>
        </div>
      </div>

      <div class="iw">
        <div class="il">MISE ($) — Min $1.00 | Mise inisyal</div>
        <input id="c-lot" type="number" value="1.00" step="0.50" min="1.00">
        <div style="color:#4A7080;font-size:9px;margin-top:2px">💡 Martingale kalkile otomatik selon payout reyèl</div>
      </div>

      <div class="g2">
        <div class="iw"><div class="il">KONFIDANS MIN</div>
          <select id="c-conf">
            <option value="0.60">60% (maks siyal)</option>
            <option value="0.65" selected>65% ★ rekòmande</option>
            <option value="0.70">70% (balans)</option>
            <option value="0.75">75% (presiz)</option>
            <option value="0.80">80% (strik)</option>
          </select>
        </div>
        <div class="iw"><div class="il">🎯 OBJEKTIF PROFIT ($)</div>
          <input id="c-target" type="number" value="0" step="1" min="0">
          <div style="color:#00FF88;font-size:9px;margin-top:2px">0 = pa gen limit</div>
        </div>
      </div>

      <div class="iw"><div class="il">🛑 LIMIT PÈT ($)</div>
        <input id="c-loss" type="number" value="0" step="1" min="0">
        <div style="color:#FF3B6B;font-size:9px;margin-top:2px">REKÒMANDE: toujou mete yon limit pèt!</div>
      </div>

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
          <div class="stat"><div class="sl">BOT</div><div id="c-st2" class="sv" style="color:#3A6070;font-size:14px">IDLE</div></div>
          <div class="stat"><div class="sl">BALANS</div><div id="c-bal" class="sv" style="color:#00D4FF">$0.00</div></div>
        </div>
        <div class="stats">
          <div class="stat"><div class="sl">P&L NET</div><div id="c-pnl" class="sv">+$0.00</div></div>
          <div class="stat"><div class="sl">KONT</div><div id="c-acc" class="sv" style="color:#FFD600;font-size:13px">—</div></div>
        </div>
      </div>

      <div class="box" style="background:#00FF8808;border-color:#00FF8822">
        <div class="bt" style="color:#00FF88">🧠 MARTINGALE QUOTEX</div>
        <div style="color:#4A7080;font-size:10px;line-height:2.2">
          <span style="color:#00FF88">✓ Trade 1:</span> Mise inisyal $X<br>
          <span style="color:#FFD600">⚠ Pèt 1:</span> Mise = (Pèt + Base) ÷ Payout%<br>
          <span style="color:#FFD600">⚠ Pèt 2:</span> Kont tout pèt kumulatif<br>
          <span style="color:#FF3B6B">🛑 Pèt 3:</span> PÒZE — tann siyal solid<br>
          <span style="color:#00FF88">✅ Genyen:</span> Reset mise → base $X<br>
          <div style="margin-top:8px;padding:6px;background:#FFD60010;border:1px solid #FFD60030;border-radius:4px">
            💡 Payout reyèl liv via WebSocket<br>
            Ex: Base $1 | Pèt 1 → $2.18 | Pèt 2 → $5.32
          </div>
        </div>
      </div>

      <div class="box" style="background:#FFD60008;border-color:#FFD60022">
        <div class="bt" style="color:#FFD600">⭐ AKTIF REKÒMANDE</div>
        <div style="font-size:10px;color:#4A7080;line-height:2.1">
          <span style="color:#00FF88">EUR/USD-OTC</span> — 92% payout, ideal weekend<br>
          <span style="color:#00FF88">EUR/USD</span> — 85%, plis stab, bon pou TF 5-15min<br>
          <span style="color:#FFD600">GBP/USD</span> — 85%, volatilite modere<br>
          <span style="color:#4A7080">BTC/USD</span> — 82%, pou crypto traders
        </div>
      </div>
    </div>
  </div>
</div>

<!-- ══ STRATEGIES ══ -->
<div id="pg-strategies" class="pg">
  <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px" id="sbts"></div>
  <div class="box" id="sdet"></div>
</div>

<!-- ══ BACKTEST ══ -->
<div id="pg-backtest" class="pg">
  <div class="box">
    <div class="bt">BACKTEST ENGINE — QUOTEX BINARY OPTIONS</div>
    <div class="g3">
      <div class="iw"><div class="il">SENBOL</div>
        <select id="bt-sy">
          <option value="EURUSD">EUR/USD</option>
          <option value="GBPUSD">GBP/USD</option>
          <option value="EURUSD-OTC">EUR/USD OTC</option>
          <option value="BTCUSD">BTC/USD</option>
          <option value="ETHUSD">ETH/USD</option>
        </select>
      </div>
      <div class="iw"><div class="il">BALANS ($)</div><input id="bt-bl" type="number" value="1000"></div>
      <div class="iw"><div class="il">MISE ($)</div><input id="bt-lt" type="number" value="1.00" step="0.50"></div>
      <div class="iw"><div class="il">PAYOUT (%)</div>
        <select id="bt-pay">
          <option value="0.92">92% — OTC</option>
          <option value="0.85" selected>85% — Forex</option>
          <option value="0.82">82% — Crypto/JPY</option>
          <option value="0.80">80% — Lòt aktif</option>
        </select>
      </div>
      <div class="iw"><div class="il">STRATEGY</div>
        <select id="bt-st">
          <option value="confluence">🔥 Confluence ELITE</option>
          <option value="deriv_pro">🚀 Pro ELITE</option>
          <option value="supertrend">📈 SuperTrend</option>
          <option value="heikin_ashi">🕯 Heikin Ashi</option>
          <option value="chandelier">🔔 Chandelier</option>
          <option value="ai">🤖 AI Score</option>
          <option value="smc">🏛 SMC</option>
          <option value="rsi">📉 RSI</option>
        </select>
      </div>
    </div>
    <div id="btm"></div>
    <button class="btn y" onclick="doBt()">▶ KÒMANSE BACKTEST</button>
    <div id="btr" style="margin-top:16px"></div>
  </div>
</div>

<!-- ══ TRADES ══ -->
<div id="pg-trades" class="pg">
  <div class="box">
    <div class="bt" id="trtit">HISTOIRIK TRADES — QUOTEX</div>
    <div id="trtbl"><div style="color:#3A6070;text-align:center;padding:40px">Pa gen trades ankò</div></div>
  </div>
</div>

<!-- ══ LOGS ══ -->
<div id="pg-log" class="pg">
  <div class="box">
    <div class="bt">LOGS SISTÈM</div>
    <div id="logs"></div>
  </div>
</div>

<!-- ══ ADMIN ══ -->
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
      <div class="iw"><div class="il">KÒD</div><input id="new-code" type="text" placeholder="BB-2025-XXXX" oninput="this.value=this.value.toUpperCase()"></div>
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
      <button class="btn b" style="padding:4px 12px;font-size:10px" onclick="admRefresh()">🔄</button>
    </div>
    <div id="adm-users-list"><div style="color:#3A6070;text-align:center;padding:20px">Klike REFRESH</div></div>
  </div>
</div>

</div><!-- /.wrap -->
</div><!-- /#app-page -->

<script>
const SESSION_KEY="bb_qx_v7";
function saveToken(t){try{localStorage.setItem(SESSION_KEY,t)}catch(e){}try{sessionStorage.setItem(SESSION_KEY,t)}catch(e){}try{const x=new Date();x.setDate(x.getDate()+30);document.cookie=`${SESSION_KEY}=${t};expires=${x.toUTCString()};path=/;SameSite=Lax`}catch(e){}}
function getStoredToken(){try{const t=localStorage.getItem(SESSION_KEY);if(t)return t}catch(e){}try{const t=sessionStorage.getItem(SESSION_KEY);if(t)return t}catch(e){}try{const m=document.cookie.match(new RegExp("(^| )"+SESSION_KEY+"=([^;]+)"));if(m)return m[2]}catch(e){}return ""}
function clearToken(){try{localStorage.removeItem(SESSION_KEY)}catch(e){}try{sessionStorage.removeItem(SESSION_KEY)}catch(e){}try{document.cookie=`${SESSION_KEY}=;expires=Thu,01 Jan 1970 00:00:00 UTC;path=/;`}catch(e){}}
function updateAdminTab(isAdmin){const t=document.getElementById("tab-admin");if(t)t.style.display=isAdmin?"block":"none"}

async function checkLogin(){
  const token=getStoredToken();
  if(!token){showLogin("");return}
  try{
    const r=await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({session_token:token,code:""})});
    const d=await r.json();
    if(d.ok){if(d.session_token)saveToken(d.session_token);updateAdminTab(d.is_admin||false);showApp(d.msg);poll();if(d.is_admin)setTimeout(()=>admRefresh(),500)}
    else{if(d.msg&&d.msg.includes("ekspire"))clearToken();showLogin(d.msg||"")}
  }catch(e){showLogin("")}
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
  if(!code){document.getElementById("login-err").innerHTML='<div class="al er">⚠ Mete kòd aksè ou</div>';return}
  const btn=document.getElementById("login-btn");btn.textContent="AP VERIFYE...";btn.disabled=true;
  try{
    const r=await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code,session_token:""})});
    const d=await r.json();
    if(d.ok&&d.session_token){saveToken(d.session_token);updateAdminTab(d.is_admin||false);showApp(d.msg);poll();if(d.is_admin)setTimeout(()=>admRefresh(),500)}
    else document.getElementById("login-err").innerHTML=`<div class="al er">✗ ${d.msg}</div>`;
  }catch(e){document.getElementById("login-err").innerHTML=`<div class="al er">✗ Erè: ${e.message}</div>`}
  btn.textContent="⚡ ANTRE";btn.disabled=false;
}
function doLogout(){clearToken();showLogin("Ou dekonekte.")}

function sw(id,el){
  document.querySelectorAll(".pg").forEach(p=>p.classList.remove("on"));
  document.querySelectorAll(".tab").forEach(t=>t.classList.remove("on"));
  document.getElementById("pg-"+id).classList.add("on");
  el.classList.add("on");
}
function msg(id,txt,ok){document.getElementById(id).innerHTML=`<div class="al ${ok?"ok":"er"}">${txt}</div>`}

async function doConn(){
  const btn=event.target;btn.textContent="AP KONEKTE...";btn.disabled=true;
  const email=document.getElementById("d-email").value.trim();
  const pass=document.getElementById("d-pass").value.trim();
  const acc=document.getElementById("d-acc").value;
  if(!email||!pass){msg("cm","⚠ Mete email ak password Quotex ou",false);btn.textContent="⚡ KONEKTE QUOTEX";btn.disabled=false;return}
  msg("cm","⏳ Ap konekte Quotex — eseye WebSocket... (tann 20sek)","ok");
  try{
    const r=await fetch("/api/connect",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({email,password:pass,account_type:acc})});
    const d=await r.json();
    if(d.ok){
      msg("cm",`✓ Konekte via ${d.method||"WebSocket"} | ${d.account_type} | $${d.balance.toFixed(2)}`,"ok");
      document.getElementById("cs").innerHTML=`<div class="al ok">✓ <b>Quotex ${d.account_type}</b> | $${d.balance.toFixed(2)} | ${d.method||"WS"}</div>`;
    }else msg("cm","✗ "+d.error,false);
  }catch(e){msg("cm","✗ "+e.message,false)}
  btn.textContent="⚡ KONEKTE QUOTEX";btn.disabled=false;
}

async function doStart(){
  const tf=document.getElementById("c-tf").value;
  const body={symbol:document.getElementById("c-sy").value,strategy:document.getElementById("c-st").value,lot:parseFloat(document.getElementById("c-lot").value),tf,min_conf:parseFloat(document.getElementById("c-conf").value),profit_target:parseFloat(document.getElementById("c-target").value||0),loss_limit:parseFloat(document.getElementById("c-loss").value||0)};
  const r=await fetch("/api/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const d=await r.json();
  if(d.ok){msg("ctm","✓ BonheurBot v7 Quotex démarre!","ok");document.getElementById("bs").style.display="none";document.getElementById("bx").style.display="inline-block"}
  else msg("ctm","✗ "+d.error,false);
}
async function doStop(){
  await fetch("/api/stop",{method:"POST"});
  msg("ctm","✓ Bot arrêté","ok");
  document.getElementById("bs").style.display="inline-block";
  document.getElementById("bx").style.display="none";
}

async function doBt(){
  const btn=event.target;btn.textContent="⏳ AP KALKILE...";btn.disabled=true;
  document.getElementById("btm").innerHTML=`<div class="al in">⏳ Ap fè backtest...</div>`;
  const body={symbol:document.getElementById("bt-sy").value,strategy:document.getElementById("bt-st").value,balance:parseFloat(document.getElementById("bt-bl").value),lot:parseFloat(document.getElementById("bt-lt").value),payout:parseFloat(document.getElementById("bt-pay").value)};
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
        <div class="stat"><div class="sl">PAYOUT</div><div class="sv" style="color:#00D4FF">${v.payout_used||"—"}</div></div>
        <div class="stat"><div class="sl">PROFIT F.</div><div class="sv" style="color:#FFD600">${v.pf}</div></div>
      </div>${v.equity&&v.equity.length>2?drawC(v.equity):""}`;
    }else document.getElementById("btm").innerHTML=`<div class="al er">✗ ${d.error}</div>`;
  }catch(e){document.getElementById("btm").innerHTML=`<div class="al er">✗ ${e.message}</div>`}
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
  const accLabel=d.account_type||"—";
  document.getElementById("hbal").textContent="$"+d.balance.toFixed(2);
  document.getElementById("hbal").style.color=d.connected?"#00D4FF":"#3A6070";
  document.getElementById("hb").textContent=d.connected?"QUOTEX":"DISCONNECTED";
  document.getElementById("hb").style.color=d.connected?"#00FF88":"#3A6070";
  document.getElementById("hacc").textContent=accLabel;
  document.getElementById("hacc").style.color=accLabel==="REAL"?"#FF3B6B":"#FFD600";
  document.getElementById("dot").className="dot "+(d.running?"dl":"di");
  document.getElementById("hs").textContent=d.running?"LIVE":"IDLE";
  document.getElementById("hs").style.color=d.running?"#00FF88":"#3A6070";
  ["s-bal","c-bal"].forEach(id=>document.getElementById(id).textContent="$"+d.balance.toFixed(2));
  ["s-pnl","s-pnl2","c-pnl"].forEach(id=>{const el=document.getElementById(id);el.textContent=sign+"$"+Math.abs(d.pnl).toFixed(2);el.style.color=col});
  document.getElementById("s-sent").textContent="$"+d.profit_sent.toFixed(4);
  document.getElementById("s-tr").textContent=d.trades.length;
  document.getElementById("s-bot").textContent=d.running?"LIVE 🟢":"IDLE";
  document.getElementById("s-bot").style.color=d.running?"#00FF88":"#3A6070";
  document.getElementById("s-strat").textContent=d.config.strategy||"—";
  document.getElementById("s-sym").textContent=d.config.symbol||"—";
  document.getElementById("s-acc").textContent=accLabel;
  ["c-st2"].forEach(id=>{document.getElementById(id).textContent=d.running?"LIVE 🟢":"IDLE";document.getElementById(id).style.color=d.running?"#00FF88":"#3A6070"});
  document.getElementById("c-acc").textContent=accLabel;
  // Payout estimasyon
  const payMap={"EURUSD":"85%","GBPUSD":"85%","EURUSD-OTC":"92%","GBPUSD-OTC":"92%","BTCUSD":"82%","ETHUSD":"80%"};
  document.getElementById("s-pay").textContent=payMap[d.config.symbol]||"~85%";
  if(d.running){document.getElementById("bs").style.display="none";document.getElementById("bx").style.display="inline-block"}
  else{document.getElementById("bs").style.display="inline-block";document.getElementById("bx").style.display="none"}
  // Grafik
  if(d.trades.length>1){
    let cum=0;const eq=d.trades.slice().reverse().map(t=>{cum+=t.pnl||0;return cum});
    const svg=document.getElementById("chart");const ch=drawC(eq);const tmp=document.createElement("div");tmp.innerHTML=ch;
    const ns=tmp.firstChild;while(svg.firstChild)svg.removeChild(svg.firstChild);while(ns.firstChild)svg.appendChild(ns.firstChild);
  }
  // Histoirik trades
  if(d.trades.length){
    document.getElementById("trtit").textContent=`HISTOIRIK TRADES — QUOTEX (${d.trades.length})`;
    document.getElementById("trtbl").innerHTML=`<table><tr><th>#</th><th>Lè</th><th>Senbol</th><th>Side</th><th>Antre</th><th>Expiry</th><th>Mise</th><th>Payout</th><th>Conf</th><th>P&L</th><th>Estati</th></tr>${d.trades.map(t=>`<tr><td style="color:#4A7080">${t.id}</td><td style="color:#4A7080">${t.time}</td><td style="font-weight:700">${t.symbol}</td><td><span class="tag ${t.side=="BUY"?"tb":"ts"}">${t.side=="BUY"?"▲ UP":"▼ DOWN"}</span></td><td>${t.entry}</td><td style="color:#4A7080">${t.tf||"—"}</td><td style="color:#FFD600">$${t.stake||"—"}</td><td style="color:#00D4FF">${t.payout||"—"}</td><td style="color:#FFD600">${t.conf}</td><td style="color:${t.pnl>=0?"#00FF88":"#FF3B6B"};font-weight:700">${t.pnl>=0?"+":""}${t.pnl.toFixed(2)}</td><td><span class="tag ${t.status=="won"?"tb":"ts"}">${t.status||"—"}</span></td></tr>`).join("")}</table>`;
  }
  // Logs
  if(d.log.length){document.getElementById("logs").innerHTML=d.log.map(l=>`<div class="le"><span class="lt">${l.time}</span><span class="l${l.level[0]}">${l.msg}</span></div>`).join("")}
}

async function poll(){try{const r=await fetch("/api/status");const d=await r.json();upd(d)}catch(e){}setTimeout(poll,3000)}

// ── STRATEGIES INFO ──
const SI={
  confluence:{l:"🔥 Confluence ELITE",d:"SuperTrend(2.5x)+HeikinAshi(2.5x)+Chandelier(2.5x)+10 strat klasik. Meye pou binary options.",tags:["SuperTrend","HeikinAshi","Chandelier","3 strat min"]},
  deriv_pro:{l:"🚀 Pro ELITE",d:"Score 5/15 + ADX≥12 + SuperTrend. Presiz pou aktif forex.",tags:["score 5/15","ADX","ST bonus"]},
  supertrend:{l:"📈 SuperTrend",d:"ATR×3 — siyal klè BUY/SELL.",tags:["ATR×3","75-92% conf"]},
  heikin_ashi:{l:"🕯 Heikin Ashi",d:"5 bouji konsekitif = trend solid.",tags:["5 bouji","72-83% conf"]},
  chandelier:{l:"🔔 Chandelier",d:"HH-ATR×3 — chanjman trend.",tags:["HH/LL","ATR×3"]},
  ai:{l:"🤖 AI Score",d:"8 faktè: EMA+RSI+MACD+BB+mom+vol+pos+trend.",tags:["8 faktè","pwa","68-92%"]},
  smc:{l:"🏛 SMC",d:"Break of Structure + swing high/low.",tags:["BOS","swing","84%"]},
  rsi:{l:"📉 RSI",d:"RSI <30 BUY / >70 SELL + EMA50.",tags:["RSI 14","OB/OS","EMA50"]},
  scalping_pro:{l:"⚡ Scalping",d:"EMA 5/13 + RSI 9. Rapid pou 1m.",tags:["EMA 5/13","RSI 9","1min"]},
};
let sel="confluence";
const sb=document.getElementById("sbts");
Object.keys(SI).forEach(k=>{
  const b=document.createElement("button");b.className="btn"+(k==sel?" b":"");b.style.cssText="padding:5px 12px;font-size:11px;margin-bottom:4px";b.textContent=SI[k].l;
  b.onclick=()=>{sel=k;renderS();sb.querySelectorAll("button").forEach(x=>x.style.borderColor="#0D2233");b.style.borderColor="#00FF88"};
  sb.appendChild(b);
});
function renderS(){
  const s=SI[sel];
  document.getElementById("sdet").innerHTML=`<div class="bt">${s.l}</div><div style="color:#C8E8F0;line-height:1.8;margin-bottom:12px">${s.d}</div><div style="display:flex;gap:8px;flex-wrap:wrap">${s.tags.map(t=>`<span class="tag" style="border-color:#FFD60044;color:#FFD600">${t}</span>`).join("")}</div>`;
}
renderS();

// ── ADMIN ──
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
      document.getElementById("adm-codes-list").innerHTML=`<table><tr><th>KÒD</th><th>STATUS</th><th>RETE</th><th>TIP</th><th>AKSYON</th></tr>${d.codes.map(c=>`<tr><td style="font-weight:700">${c.code}</td><td><span class="tag" style="color:${sc[c.status]||"#4A7080"};border-color:${sc[c.status]||"#4A7080"}44">${c.status}</span></td><td style="color:#4A7080">${c.remaining}</td><td>${c.is_adm?"👑":"👤"}</td><td style="display:flex;gap:4px">${c.status!=="ADM"?`<button onclick="admReset('${c.code}')" style="background:transparent;border:1px solid #FFD60044;color:#FFD600;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">↺</button>`:""}${c.code!=="BONHEURWIIN"?`<button onclick="admRevoke('${c.code}')" style="background:transparent;border:1px solid #FF3B6B44;color:#FF3B6B;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">✕</button>`:""}</td></tr>`).join("")}</table>`;
    }
  }catch(e){}
  try{
    const r2=await fetch("/api/admin/users",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});
    const d2=await r2.json();
    if(d2.ok){
      document.getElementById("adm-users-count").textContent=d2.total;
      document.getElementById("adm-users-list").innerHTML=d2.total===0
        ?'<div style="color:#3A6070;text-align:center;padding:20px">Pa gen itilizatè</div>'
        :`<table><tr><th>UID</th><th>SENBOL</th><th>BOT</th><th>KONT</th><th>BALANS</th><th>P&L</th><th>TRADES</th><th>AKSYON</th></tr>${d2.users.map(u=>`<tr><td style="color:#4A7080;font-size:10px">${u.uid}</td><td style="font-weight:700">${u.symbol||"—"}</td><td><span class="tag ${u.running?"tb":"tg"}">${u.running?"LIVE":"IDLE"}</span></td><td><span class="tag ${u.account_type==="REAL"?"ts":"ty"}">${u.account_type||"—"}</span></td><td style="color:#00D4FF">$${u.balance}</td><td style="color:${u.pnl>=0?"#00FF88":"#FF3B6B"}">${u.pnl>=0?"+":""}$${u.pnl}</td><td>${u.trades}</td><td style="display:flex;gap:4px">${u.running?`<button onclick="admStopUser('${u.uid}')" style="background:transparent;border:1px solid #FF3B6B44;color:#FF3B6B;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">■</button>`:""}<button onclick="admClearUser('${u.uid}')" style="background:transparent;border:1px solid #4A708044;color:#4A7080;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">🗑</button></td></tr>`).join("")}</table>`;
    }
  }catch(e){}
  try{
    const r3=await fetch("/api/admin/sessions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});
    const d3=await r3.json();
    if(d3.ok){document.getElementById("adm-sessions-list").innerHTML=d3.sessions.length===0?'<div style="text-align:center;padding:10px">Pa gen sesyon</div>':d3.sessions.map(s=>`<div style="padding:5px 0;border-bottom:1px solid #0D2233;display:flex;justify-content:space-between"><span style="color:#4A7080">${s.token}</span><span style="color:${s.is_admin?"#00D4FF":"#4A7080"}">${s.is_admin?"👑":"👤"}</span><span style="color:${s.active?"#00FF88":"#FF3B6B"}">${s.days_left} jou</span></div>`).join("")}
  }catch(e){}
}
async function admAddCode(){const token=getStoredToken();const code=document.getElementById("new-code").value.trim().toUpperCase();if(!code){document.getElementById("add-code-msg").innerHTML='<div class="al er">Mete yon kòd</div>';return}const isAdm=document.getElementById("new-code-type").value==="adm";const r=await fetch("/api/admin/add_code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,code,is_adm:isAdm})});const d=await r.json();document.getElementById("add-code-msg").innerHTML=`<div class="al ${d.ok?"ok":"er"}">${d.ok?d.msg:d.error}</div>`;if(d.ok){document.getElementById("new-code").value="";admRefresh()}}
async function admRevoke(code){if(!confirm(`Revoke ${code}?`))return;const token=getStoredToken();const r=await fetch("/api/admin/revoke_code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,code})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh()}
async function admReset(code){const token=getStoredToken();const r=await fetch("/api/admin/reset_code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,code})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh()}
async function admStopUser(uid){if(!confirm(`Kanpe bot ${uid}?`))return;const token=getStoredToken();const r=await fetch("/api/admin/stop_user",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,uid})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh()}
async function admCleanSessions(){const token=getStoredToken();const r=await fetch("/api/admin/clean_sessions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh()}
async function admClearUser(uid){if(!confirm(`Efase TOUT istorik ${uid}?`))return;const token=getStoredToken();const r=await fetch("/api/admin/clear_user",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,uid})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh()}
function genCode(len){const chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789";let result="";for(let i=0;i<len;i++){if(i>0&&i%4===0)result+="-";result+=chars[Math.floor(Math.random()*chars.length)]}document.getElementById("gen-result").textContent=result;document.getElementById("gen-copy-btn").style.display="inline-block";document.getElementById("new-code").value=result}
function admCopyGen(){const code=document.getElementById("gen-result").textContent;navigator.clipboard.writeText(code).catch(()=>{});admAddCode()}

checkLogin();
</script>
</body>
</html>"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"BonheurBot v7 QUOTEX Edition — Port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
