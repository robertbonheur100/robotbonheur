"""
╔══════════════════════════════════════════════════════════════╗
║        BONHEURBOT PRO v6 ELITE — VRÈ PAT FIX KONPLÈ        ║
║         Multi-User Trading Bot — Deriv + Binance            ║
║                                                             ║
║  FIX v6.1:                                                  ║
║   ✅ TOUTES STRATEGIES retabli (ansyen + nouvo)             ║
║   ✅ PAT token place_trade → REST API (pa WebSocket)        ║
║   ✅ PAT token get_candles → REST API                       ║
║   ✅ DerivClient (Klasik) pa chanje — WebSocket ok          ║
║                                                             ║
║  ACHITEKTI DUAL CLIENT:                                      ║
║   TOKEN PAT    → DerivRESTClient  (HTTP REST Bearer)        ║
║   TOKEN KLASIK → DerivClient      (WebSocket authorize)     ║
║                                                             ║
║  Smart Entry | 3-Loss Pause | Live Balance | Multi-User     ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, json, time, threading, logging, math, uuid, secrets, requests
from datetime import datetime, timedelta, date
from flask import Flask, request as freq, jsonify, render_template_string, session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROFIT_WALLET = "0x2ba88a4d6cabaded5d06c75ef3b3efec386acaef"
PROFIT_PCT    = 0.05
DERIV_REST_BASE = "https://api.deriv.com/v2"
DERIV_WS_APP_IDS = ["1089", "36544", "16929"]

ACCESS_CODES = {
    "BONHEURWIIN": {"created_at": None, "used": False, "is_adm": True},
    "HJKy8kFD":    {"created_at": time.time(), "used": False, "is_adm": False},
    "GHt3hjI6":    {"created_at": time.time(), "used": False, "is_adm": False},
}
CODE_TTL_SECONDS = 2592000  # 30 jou

# ═══════════════════════════════════════════════════════════
# ACCESS CODES + SESSIONS
# ═══════════════════════════════════════════════════════════
def check_access(code):
    code = code.strip().upper()
    if code not in ACCESS_CODES: return False, "Kòd aksè pa valid — kontakte admin"
    entry = ACCESS_CODES[code]
    if entry["created_at"] is None or entry.get("is_adm"): return True, "✓ Aksè admin akòde"
    age = time.time() - entry["created_at"]
    if age > CODE_TTL_SECONDS: return False, f"Kòd ekspire depi {int((age-CODE_TTL_SECONDS)/86400)} jou — kontakte admin"
    if entry["used"]: return False, "Kòd sa deja itilize — kontakte admin"
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
_user_lock = threading.Lock()

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


# ═══════════════════════════════════════════════════════════
# ██  VRÈ PAT FIX — DUAL CLIENT SYSTEM  ██
# ═══════════════════════════════════════════════════════════
def is_pat_token(token: str) -> bool:
    return token.strip().lower().startswith("pat_")


# ─────────────────────────────────────────────────────────────
# REST CLIENT — PAT TOKEN VIA HTTP Bearer
# ✅ FIX: place_trade + get_candles + get_ticks tout itilize REST
# ─────────────────────────────────────────────────────────────
class DerivRESTClient:
    """REST API client pou PAT token (Authorization: Bearer)"""

    def __init__(self, pat_token: str, timeout: int = 20):
        self.token    = pat_token
        self.timeout  = timeout
        self._bal     = 0.0
        self._loginid = "PAT_USER"
        self._headers = {
            "Authorization": f"Bearer {pat_token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "User-Agent":    "BonheurBot/6.1",
        }

    def _get(self, endpoint, params=None):
        r = requests.get(f"{DERIV_REST_BASE}{endpoint}", headers=self._headers,
                         params=params or {}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, endpoint, data=None):
        r = requests.post(f"{DERIV_REST_BASE}{endpoint}", headers=self._headers,
                          json=data or {}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def connect(self) -> float:
        errors = []
        # Metòd 1: GET /v2/account
        try:
            data = self._get("/account")
            logger.info(f"PAT REST /account: {str(data)[:200]}")
            if "error" not in data:
                for path in [["balance"], ["account","balance"], ["data","balance"]]:
                    v = data
                    try:
                        for k in path: v = v[k]
                        self._bal = float(v)
                        for lpath in [["loginid"],["account","loginid"],["login_id"]]:
                            try:
                                lv = data
                                for k in lpath: lv = lv[k]
                                self._loginid = str(lv); break
                            except: pass
                        return self._bal
                    except: pass
                self._loginid = "PAT_USER"; return 0.0
        except requests.HTTPError as e:
            errors.append(f"GET /account: {e.response.status_code} → {e.response.text[:80]}")
        except Exception as e:
            errors.append(f"GET /account: {str(e)[:80]}")

        # Metòd 2: GET /v2/account/balance
        try:
            data = self._get("/account/balance")
            if "error" not in data:
                for k in ["balance","amount"]:
                    if k in data: self._bal = float(data[k]); return self._bal
        except requests.HTTPError as e:
            errors.append(f"GET /balance: {e.response.status_code} → {e.response.text[:80]}")
        except Exception as e:
            errors.append(f"GET /balance: {str(e)[:80]}")

        # Metòd 3: POST /v2/account/authorize
        try:
            data = self._post("/account/authorize", {"token": self.token})
            if "error" not in data:
                bal = data.get("balance") or data.get("account", {}).get("balance") or 0
                self._bal = float(bal)
                self._loginid = data.get("loginid") or "PAT_USER"
                return self._bal
        except requests.HTTPError as e:
            errors.append(f"POST /authorize: {e.response.status_code} → {e.response.text[:80]}")
        except Exception as e:
            errors.append(f"POST /authorize: {str(e)[:80]}")

        # Metòd 4: ping
        try:
            self._get("/ping"); self._bal = 0.0; self._loginid = "PAT_USER"
            logger.warning("PAT: ping OK men balance unavailable — mete 0")
            return 0.0
        except requests.HTTPError as e:
            errors.append(f"GET /ping: {e.response.status_code} → {e.response.text[:80]}")
        except Exception as e:
            errors.append(f"GET /ping: {str(e)[:80]}")

        raise Exception(
            f"Token PAT echwe ak tout REST endpoints.\n\nDETAY ERÈ:\n" +
            "\n".join(errors) +
            "\n\nSOLISYON: Itilize Token Klasik (pa pat_xxx)\n"
            "1. app.deriv.com → foto ou → API Token\n"
            "2. Create new token → Read+Trade+Payments\n"
            "3. Kole token, App ID: 1089"
        )

    def get_balance_sync(self) -> float:
        try:
            data = self._get("/account/balance")
            for k in ["balance","amount"]:
                if k in data: self._bal = float(data[k]); return self._bal
        except: pass
        try:
            data = self._get("/account")
            bal = data.get("balance") or data.get("account",{}).get("balance")
            if bal: self._bal = float(bal); return self._bal
        except: pass
        return self._bal

    # ✅ FIX PRENSIPAL: get_candles pa itilize WebSocket ankò — REST sèlman
    def get_candles(self, symbol="R_100", count=200, gran=60):
        try:
            data = self._get("/ticks/history", {
                "symbol": symbol, "count": count,
                "granularity": gran, "style": "candles", "end": "latest"
            })
            candles = data.get("candles") or data.get("data",{}).get("candles") or []
            if candles:
                return [{"open":float(c.get("open",0)),"high":float(c.get("high",0)),
                         "low":float(c.get("low",0)),"close":float(c.get("close",0)),
                         "volume":1000,"time":c.get("epoch",0)} for c in candles]
        except Exception as e:
            logger.error(f"PAT get_candles REST: {e}")
        # Fallback: eseye WebSocket piblik (san authorize) pou jwenn done
        try:
            return self._get_candles_ws_public(symbol, count, gran)
        except Exception as e2:
            logger.error(f"PAT get_candles WS fallback: {e2}")
            return []

    def _get_candles_ws_public(self, symbol, count, gran):
        """WebSocket piblik (san token) jis pou done mache — PA pou trading"""
        import websocket as wsl
        res=[None]; done=threading.Event()
        def on_open(ws):
            ws.send(json.dumps({"ticks_history":symbol,"count":count,"end":"latest",
                                "granularity":gran,"style":"candles","adjust_start_time":1}))
        def on_msg(ws, msg):
            d=json.loads(msg)
            if "candles" in d: res[0]=d["candles"]; done.set()
            elif "error" in d: done.set()
        for aid in ["1089","36544"]:
            url=f"wss://ws.derivws.com/websockets/v3?app_id={aid}"
            try:
                w=wsl.WebSocketApp(url,on_open=on_open,on_message=on_msg)
                t=threading.Thread(target=w.run_forever,daemon=True); t.start()
                done.wait(timeout=20)
                if res[0]:
                    return [{"open":float(c["open"]),"high":float(c["high"]),"low":float(c["low"]),
                             "close":float(c["close"]),"volume":1000,"time":c["epoch"]} for c in res[0]]
                done.clear()
                try: w.close()
                except: pass
            except: pass
        return []

    def get_ticks(self, symbol="R_10", count=100):
        try:
            data = self._get("/ticks/history", {"symbol":symbol,"count":count,"style":"ticks","end":"latest"})
            hist = data.get("history") or data.get("data",{}).get("history") or {}
            prices = hist.get("prices",[]); times = hist.get("times",[])
            return [{"price":float(p),"time":t} for p,t in zip(prices,times)]
        except Exception as e:
            logger.error(f"PAT get_ticks: {e}"); return []

    # ✅ FIX PRENSIPAL: place_trade itilize REST API — PA WebSocket
    def place_trade(self, symbol, direction, amount=1.0, duration_secs=60):
        ct = "CALL" if direction == "BUY" else "PUT"
        if duration_secs<=60:   dv,du=1,"m"
        elif duration_secs<=300: dv,du=5,"m"
        elif duration_secs<=900: dv,du=15,"m"
        elif duration_secs<=3600: dv,du=1,"h"
        else: dv,du=4,"h"

        # Eseye REST API proposal/buy
        try:
            prop = self._post("/trade/proposal", {
                "contract_type":ct,"symbol":symbol,"amount":max(0.5,float(amount)),
                "basis":"stake","duration":dv,"duration_unit":du,"currency":"USD"})
            if "error" in prop:
                raise Exception(prop["error"].get("message","Proposal echwe"))
            pid = prop.get("proposal",{}).get("id") or prop.get("id")
            ask = prop.get("proposal",{}).get("ask_price") or prop.get("ask_price")
            if not pid: raise Exception(f"Pa jwenn proposal_id: {str(prop)[:100]}")
            result = self._post("/trade/buy", {"buy":pid,"price":ask})
            if "error" in result: raise Exception(result["error"].get("message","Buy echwe"))
            buy_data = result.get("buy") or result
            logger.info(f"PAT REST trade OK: {str(buy_data)[:100]}")
            return buy_data
        except Exception as e:
            logger.warning(f"PAT REST trade echwe ({e}), eseye WebSocket authorize...")
            # Fallback: WebSocket ak authorize (PAT pa toujou fonksyone men eseye)
            return self._place_trade_ws_fallback(symbol, direction, amount, dv, du, ct)

    def _place_trade_ws_fallback(self, symbol, direction, amount, dv, du, ct):
        """Fallback WebSocket trading ak PAT token — dènyè opsyon"""
        import websocket as wsl
        res=[None]; err=[None]; done=threading.Event()
        token=self.token
        def on_msg(ws, msg):
            d=json.loads(msg); mt=d.get("msg_type","")
            if mt=="authorize":
                if "error" in d:
                    err[0]=f"Auth echwe: {d['error'].get('message','')}"
                    done.set(); return
                ws.send(json.dumps({"proposal":1,"amount":max(0.5,float(amount)),"basis":"stake",
                                    "contract_type":ct,"currency":"USD","symbol":symbol,
                                    "duration":dv,"duration_unit":du}))
            elif mt=="proposal":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                ws.send(json.dumps({"buy":d["proposal"]["id"],"price":d["proposal"]["ask_price"]}))
            elif mt=="buy":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                res[0]=d.get("buy",{}); done.set()
        for aid in DERIV_WS_APP_IDS:
            done2=threading.Event(); res[0]=None; err[0]=None
            def on_open(ws): ws.send(json.dumps({"authorize": token}))
            w=wsl.WebSocketApp(f"wss://ws.derivws.com/websockets/v3?app_id={aid}",
                               on_message=on_msg,on_open=on_open)
            t=threading.Thread(target=w.run_forever,daemon=True); t.start()
            done.wait(timeout=30)
            try: w.close()
            except: pass
            if res[0]: return res[0]
            if err[0]: logger.warning(f"WS fallback app_id={aid}: {err[0]}")
            done.clear()
        raise Exception(err[0] or "Trade echwe — PA gen koneksyon valid. Itilize Token Klasik pou trading.")

    def place_digits_trade(self, symbol, contract_type, amount=0.35, barrier=None):
        payload = {"contract_type":contract_type,"symbol":symbol,
                   "amount":max(0.35,float(amount)),"basis":"stake",
                   "duration":5,"duration_unit":"t","currency":"USD"}
        if barrier is not None: payload["barrier"] = str(barrier)
        try:
            prop = self._post("/trade/proposal", payload)
            if "error" in prop: raise Exception(prop["error"].get("message","Proposal echwe"))
            pid = prop.get("proposal",{}).get("id") or prop.get("id")
            ask = prop.get("proposal",{}).get("ask_price") or prop.get("ask_price")
            result = self._post("/trade/buy", {"buy":pid,"price":ask})
            if "error" in result: raise Exception(result["error"].get("message","Buy echwe"))
            return result.get("buy") or result
        except Exception as e:
            logger.warning(f"PAT REST digits echwe ({e}), WS fallback...")
            import websocket as wsl
            res=[None]; err=[None]; done=threading.Event(); token=self.token
            proposal_ws={"proposal":1,"amount":max(0.35,float(amount)),"basis":"stake",
                         "contract_type":contract_type,"currency":"USD","symbol":symbol,
                         "duration":5,"duration_unit":"t"}
            if barrier is not None: proposal_ws["barrier"]=str(barrier)
            def on_msg(ws, msg):
                d=json.loads(msg); mt=d.get("msg_type","")
                if mt=="authorize" and "error" not in d: ws.send(json.dumps(proposal_ws))
                elif mt=="proposal":
                    if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                    ws.send(json.dumps({"buy":d["proposal"]["id"],"price":d["proposal"]["ask_price"]}))
                elif mt=="buy":
                    if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                    res[0]=d.get("buy",{}); done.set()
            for aid in DERIV_WS_APP_IDS:
                res[0]=None; err[0]=None; done.clear()
                def on_open(ws): ws.send(json.dumps({"authorize": token}))
                w=wsl.WebSocketApp(f"wss://ws.derivws.com/websockets/v3?app_id={aid}",
                                   on_message=on_msg,on_open=on_open)
                threading.Thread(target=w.run_forever,daemon=True).start()
                done.wait(timeout=30)
                try: w.close()
                except: pass
                if res[0]: return res[0]
            raise Exception(err[0] or "Digits trade echwe konplètman")

    def wait_contract_result(self, contract_id, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data = self._get(f"/trade/contracts/{contract_id}")
                poc = data.get("contract") or data.get("proposal_open_contract") or data
                if poc.get("status","") in ("won","lost","sold"): return poc
            except: pass
            time.sleep(2)
        return None

    def transfer_to_account(self, account_id, amount):
        try:
            result = self._post("/account/transfer", {
                "account_to":account_id,"amount":round(float(amount),2),"currency":"USD"})
            return result
        except Exception as e:
            logger.error(f"PAT transfer echwe: {e}"); return None

    @property
    def balance(self): return self._bal
    @property
    def loginid(self): return self._loginid


# ─────────────────────────────────────────────────────────────
# WEBSOCKET AUTHORIZE — TOKEN KLASIK SÈLMAN
# ─────────────────────────────────────────────────────────────
def connect_ws_authorize(token: str, app_id: str, timeout: int = 20):
    import websocket as wsl
    done = threading.Event()
    result = [None, None, None, None]

    def on_open(ws): ws.send(json.dumps({"authorize": token}))
    def on_msg(ws, msg):
        try: d = json.loads(msg)
        except: return
        if d.get("msg_type") == "authorize":
            if "error" in d:
                result[0]=False; result[3]=d["error"].get("message","Token invalib")
            else:
                result[0]=True; result[1]=float(d["authorize"].get("balance",0))
                result[2]=d["authorize"].get("loginid")
            done.set()
            try: ws.close()
            except: pass
        elif "error" in d and not done.is_set():
            result[0]=False; result[3]=d["error"].get("message","Erè enkoni")
            done.set()
            try: ws.close()
            except: pass
    def on_error(ws, e):
        if not done.is_set():
            result[0]=False; result[3]=f"WS erè: {str(e)[:150]}"; done.set()

    url = f"wss://ws.derivws.com/websockets/v3?app_id={app_id}"
    try:
        ws = wsl.WebSocketApp(url, on_open=on_open, on_message=on_msg, on_error=on_error)
        t  = threading.Thread(target=ws.run_forever, daemon=True); t.start()
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
    app_ids = [app_id] + [a for a in DERIV_WS_APP_IDS if a != app_id]
    errors  = []
    for aid in app_ids:
        ok,bal,lid,_,err = connect_ws_authorize(token, aid, timeout=15)
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
        logger.info("PAT token → REST API Bearer")
        try:
            c = DerivRESTClient(token)
            bal = c.connect()
            logger.info(f"PAT REST OK | {c.loginid} | ${bal:.2f}")
            return True, bal, c.loginid, None, f"PAT (REST Bearer) | {c.loginid}"
        except Exception as e:
            return False, 0.0, None, None, str(e)
    else:
        logger.info(f"Token Klasik → WebSocket app_id={app_id}")
        return connect_classic_token(token, app_id)


# ═══════════════════════════════════════════════════════════
# INDIKATÈ TEKNIK
# ═══════════════════════════════════════════════════════════
def ema(prices, p):
    if len(prices)<p: return []
    k=2/(p+1); e=[sum(prices[:p])/p]
    for x in prices[p:]: e.append(x*k+e[-1]*(1-k))
    return e

def rsi(prices, p=14):
    if len(prices)<p+1: return 50
    d=[prices[i+1]-prices[i] for i in range(len(prices)-1)]
    g=sum(x for x in d[-p:] if x>0)/p
    l=sum(-x for x in d[-p:] if x<0)/p
    return 100 if l==0 else 100-(100/(1+g/l))

def macd(prices):
    e12=ema(prices,12); e26=ema(prices,26)
    if not e12 or not e26: return 0,0
    m=e12[-1]-e26[-1]; return m,m*0.2

def bb(prices, p=20, s=2.0):
    if len(prices)<p: return None,None,None
    avg=sum(prices[-p:])/p
    std=math.sqrt(sum((x-avg)**2 for x in prices[-p:])/p)
    return avg+s*std,avg,avg-s*std

def atr(candles, p=14):
    if len(candles)<p+1: return 0
    trs=[]
    for i in range(1,len(candles)):
        h=candles[i]["high"]; l=candles[i]["low"]; pc=candles[i-1]["close"]
        trs.append(max(h-l,abs(h-pc),abs(l-pc)))
    return sum(trs[-p:])/p if trs else 0

def stoch_k(candles, p=14):
    if len(candles)<p: return 50
    hi=max(x["high"] for x in candles[-p:]); lo=min(x["low"] for x in candles[-p:])
    return ((candles[-1]["close"]-lo)/(hi-lo)*100) if hi!=lo else 50

def calc_adx_full(candles, p=14):
    if len(candles)<p+2: return 0,0,0
    trs=[]; pdms=[]; mdms=[]
    for i in range(1,len(candles)):
        h=candles[i]["high"]; l=candles[i]["low"]
        ph=candles[i-1]["high"]; pl=candles[i-1]["low"]; pc=candles[i-1]["close"]
        tr=max(h-l,abs(h-pc),abs(l-pc))
        up=h-ph; dn=pl-l
        pdms.append(up if up>dn and up>0 else 0)
        mdms.append(dn if dn>up and dn>0 else 0)
        trs.append(tr)
    av=sum(trs[-p:])/p if sum(trs[-p:])>0 else 1
    pdi=100*sum(pdms[-p:])/(p*av); mdi=100*sum(mdms[-p:])/(p*av)
    adx=100*abs(pdi-mdi)/(pdi+mdi+0.001)
    return round(adx,2),round(pdi,2),round(mdi,2)

def supertrend(candles, p=10, mult=3.0):
    if len(candles)<p+5: return "NONE",0.0
    highs=[c["high"] for c in candles]; lows=[c["low"] for c in candles]; closes=[c["close"] for c in candles]
    trs=[]
    for i in range(1,len(candles)):
        tr=max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1]))
        trs.append(tr)
    atr_vals=[]
    for i in range(p-1,len(trs)): atr_vals.append(sum(trs[i-p+1:i+1])/p)
    if not atr_vals: return "NONE",0.0
    n=len(atr_vals); hl2=[(highs[i+1]+lows[i+1])/2 for i in range(n)]
    ub=[hl2[i]+mult*atr_vals[i] for i in range(n)]; lb=[hl2[i]-mult*atr_vals[i] for i in range(n)]
    upper=list(ub); lower=list(lb)
    for i in range(1,n):
        upper[i]=min(ub[i],upper[i-1]) if closes[i+p-1]<=upper[i-1] else ub[i]
        lower[i]=max(lb[i],lower[i-1]) if closes[i+p-1]>=lower[i-1] else lb[i]
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
    ce_long=max(highs[-p:])-mult*at; ce_short=min(lows[-p:])+mult*at
    price=closes[-1]; prev=closes[-2] if len(closes)>=2 else price
    if price>ce_long and prev<=ce_long:
        return "BUY",min(0.90,0.78+min((price-ce_long)/max(at,0.0001)*0.04,0.12))
    elif price<ce_short and prev>=ce_short:
        return "SELL",min(0.90,0.78+min((ce_short-price)/max(at,0.0001)*0.04,0.12))
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
    bull=[b for b in recent if b["close"]>b["open"]]; bear=[b for b in recent if b["close"]<b["open"]]
    if len(bull)==lookback:
        bodies=[abs(b["close"]-b["open"]) for b in bull]; growing=bodies[-1]>=bodies[0]*0.7
        return "BUY",0.83 if growing else 0.77
    if len(bear)==lookback:
        bodies=[abs(b["close"]-b["open"]) for b in bear]; growing=bodies[-1]>=bodies[0]*0.7
        return "SELL",0.83 if growing else 0.77
    if len(bull)>=lookback-1: return "BUY",0.72
    if len(bear)>=lookback-1: return "SELL",0.72
    return "NONE",0.0

def vwap_signal(candles, lookback=20):
    if len(candles)<lookback: return "NONE",0.0
    recent=candles[-lookback:]; tpv=0.0; tv=0.0
    for c in recent:
        typ=(c["high"]+c["low"]+c["close"])/3; vol=c.get("volume",1000)
        tpv+=typ*vol; tv+=vol
    if tv==0: return "NONE",0.0
    vwap=tpv/tv; price=candles[-1]["close"]; at=atr(candles,14)
    if at==0: return "NONE",0.0
    dist=(price-vwap)/max(at,0.0001)
    if dist>0.3: return "BUY",min(0.88,0.72+min(dist*0.03,0.16))
    elif dist<-0.3: return "SELL",min(0.88,0.72+min(abs(dist)*0.03,0.16))
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
    r=rsi(cl); r2=rsi(cl[:-3]) if len(cl)>3 else r
    e50=ema(cl,50) if len(cl)>=50 else None
    if r<30 and (not e50 or cl[-1]>e50[-1]*0.998): return "BUY",0.82
    if 30<=r<42 and r>r2 and (not e50 or cl[-1]>e50[-1]*0.996): return "BUY",0.74
    if r>70 and (not e50 or cl[-1]<e50[-1]*1.002): return "SELL",0.82
    if 58<r<=70 and r<r2 and (not e50 or cl[-1]<e50[-1]*1.004): return "SELL",0.74
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
    if at and mid: vol_ratio=at/mid*100; f[5]=1.0 if 0.1<vol_ratio<0.5 else(0.5 if vol_ratio<=0.1 else -0.5)
    hi20=max(hi[-20:]); lo20=min(lo_[-20:]); rng20=hi20-lo20 if hi20!=lo20 else 1
    pos=(cl[-1]-lo20)/rng20; f[6]=1.0 if pos<0.2 else(-1.0 if pos>0.8 else 0.0)
    if e50 and e200: trend=(e50[-1]-e200[-1])/max(e200[-1],0.001)*100; f[7]=max(-1,min(1,trend*10))
    W=[2.8,2.2,1.8,1.5,1.2,0.8,1.6,1.9]; score=sum(f[i]*W[i] for i in range(8))/sum(W)
    if score>=0.35: return "BUY",min(0.92,0.68+score*0.35)
    if score<=-0.35: return "SELL",min(0.92,0.68+abs(score)*0.35)
    return "NONE",0

def strat_scalping(c):
    if len(c)<20: return "NONE",0
    cl=[x["close"] for x in c]; e5=ema(cl,5); e13=ema(cl,13); e50=ema(cl,50) if len(cl)>=50 else None
    if len(e5)<3 or len(e13)<3: return "NONE",0
    r=rsi(cl,9)
    if e5[-1]>e13[-1] and r<70 and (not e50 or cl[-1]>e50[-1]*0.997): return "BUY",0.74
    if e5[-1]<e13[-1] and r>30 and (not e50 or cl[-1]<e50[-1]*1.003): return "SELL",0.74
    return "NONE",0

def calc_pivot_points(candles):
    if len(candles)<20: return None
    recent=candles[-20:]; hi=max(x["high"] for x in recent); lo=min(x["low"] for x in recent); cl=candles[-1]["close"]
    pp=(hi+lo+cl)/3; rng=hi-lo
    return {"pp":pp,"r1":2*pp-lo,"r2":pp+rng,"s1":2*pp-hi,"s2":pp-rng,
            "fib_r1":pp+0.382*rng,"fib_r2":pp+0.618*rng,"fib_s1":pp-0.382*rng,"fib_s2":pp-0.618*rng}

def pivot_signal(candles, trend):
    pv=calc_pivot_points(candles)
    if not pv: return False,0.0
    price=candles[-1]["close"]; tol=0.008
    lvls={"TRENDING_UP":[pv["s1"],pv["s2"],pv["fib_s1"],pv["fib_s2"],pv["pp"]],
          "TRENDING_DN":[pv["r1"],pv["r2"],pv["fib_r1"],pv["fib_r2"],pv["pp"]]}.get(trend,[])
    for lvl in lvls:
        if abs(price-lvl)/max(lvl,0.0001)<tol:
            return True,(0.07 if lvl in(pv.get("s1"),pv.get("r1"),pv.get("fib_s1"),pv.get("fib_r1")) else 0.05)
    return False,0.0

def market_regime(candles):
    if len(candles)<20: return "UNKNOWN",0
    cl=[x["close"] for x in candles]; adx,pdi,mdi=calc_adx_full(candles,14); at=atr(candles)
    mid_val=sum(cl[-20:])/20; atr_pct=(at/mid_val*100) if mid_val>0 else 0
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
    if (at/mid_price*100) if mid_price>0 else 0 < 0.005: return "NONE",0
    adx,pdi,mdi=calc_adx_full(c,14); regime,_=market_regime(c)
    st_sig,st_conf=supertrend(c,p=10,mult=3.0); ha_sig,ha_conf=heikin_ashi_trend(c,lookback=5)
    ce_sig,ce_conf=chandelier_exit(c,p=22,mult=3.0); vw_sig,vw_conf=vwap_signal(c,lookback=20)
    classic_fns=[(strat_ema,1.4),(strat_rsi,1.6),(strat_macd,1.5),(strat_smc,1.7),
        (strat_breakout,1.4),(strat_ob,1.5),(strat_stoch,1.3),(strat_ai,1.8),
        (strat_scalping,1.2),(strat_fvg,1.3),(strat_fibonacci,1.4)]
    buy_score=sell_score=0.0; buy_cnt=sell_cnt=0; NW=2.5
    if st_sig=="BUY" and st_conf>=min_per_conf: buy_score+=st_conf*NW; buy_cnt+=1
    elif st_sig=="SELL" and st_conf>=min_per_conf: sell_score+=st_conf*NW; sell_cnt+=1
    if ha_sig=="BUY" and ha_conf>=min_per_conf: buy_score+=ha_conf*NW; buy_cnt+=1
    elif ha_sig=="SELL" and ha_conf>=min_per_conf: sell_score+=ha_conf*NW; sell_cnt+=1
    if ce_sig=="BUY" and ce_conf>=min_per_conf: buy_score+=ce_conf*NW; buy_cnt+=1
    elif ce_sig=="SELL" and ce_conf>=min_per_conf: sell_score+=ce_conf*NW; sell_cnt+=1
    if vw_sig=="BUY" and vw_conf>=min_per_conf: buy_score+=vw_conf*1.8; buy_cnt+=1
    elif vw_sig=="SELL" and vw_conf>=min_per_conf: sell_score+=vw_conf*1.8; sell_cnt+=1
    for fn,w in classic_fns:
        try:
            s,conf=fn(c)
            if s=="BUY" and conf>=min_per_conf: buy_score+=conf*w; buy_cnt+=1
            elif s=="SELL" and conf>=min_per_conf: sell_score+=conf*w; sell_cnt+=1
        except: pass
    if regime=="VOLATILE": return "NONE",0
    dom=1.15
    if regime=="RANGING":
        ns=[st_sig,ha_sig,ce_sig]; bn=sum(1 for s in ns if s=="BUY"); sn=sum(1 for s in ns if s=="SELL")
        if bn>=2 and buy_cnt>=min_strats and buy_score>sell_score*dom:
            _,pb=pivot_signal(c,"TRENDING_UP"); return "BUY",round(min(0.92,0.74+(buy_score/max(buy_cnt,1)/5.0)*0.12+pb),3)
        if sn>=2 and sell_cnt>=min_strats and sell_score>buy_score*dom:
            _,pb=pivot_signal(c,"TRENDING_DN"); return "SELL",round(min(0.92,0.74+(sell_score/max(sell_cnt,1)/5.0)*0.12+pb),3)
        return "NONE",0
    if regime=="TRENDING_UP" and buy_cnt>=min_strats and buy_score>sell_score*dom:
        _,pb=pivot_signal(c,"TRENDING_UP"); ab=min(0.05,adx/500)
        return "BUY",round(min(0.95,0.75+(buy_score/max(buy_cnt,1)/5.0)*0.13+pb+ab),3)
    if regime=="TRENDING_DN" and sell_cnt>=min_strats and sell_score>buy_score*dom:
        _,pb=pivot_signal(c,"TRENDING_DN"); ab=min(0.05,adx/500)
        return "SELL",round(min(0.95,0.75+(sell_score/max(sell_cnt,1)/5.0)*0.13+pb+ab),3)
    return "NONE",0

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
    if e50: trend_up=trend_up and cl[-1]>e50[-1]*0.998; trend_down=trend_down and cl[-1]<e50[-1]*1.002
    if not trend_up and not trend_down: return "NONE",0
    hi20=max(hi[-21:-1]); lo20=min(lo_[-21:-1])
    roc3=(cl[-1]-cl[-4])/max(abs(cl[-4]),0.001)*100 if len(cl)>=4 else 0
    body=abs(cl[-1]-c[-1]["open"])/max(c[-1]["high"]-c[-1]["low"],0.00001)
    st_sig,_=supertrend(c,p=10,mult=3.0)

    def score_direction(up):
        s=0.0
        if up:
            if cl[-1]>hi20 and cl[-2]<=hi20: s+=2.0
            elif cl[-1]>hi20*0.997: s+=0.8
            if 25<=r<=45: s+=3.0
            elif r<25: s+=2.5
            elif r<70: s+=0.8
            if m>sig_ and macd_h>macd_hp: s+=2.5 if m>0 else 1.8
            elif m>sig_: s+=1.0
            if k<25 and k>kp: s+=2.5
            elif k<35 and k>kp: s+=1.5
            if lo_bb and cl[-1]<=lo_bb*1.005: s+=2.0
            elif mid_bb and cl[-1]<mid_bb: s+=0.8
            if roc3>0: s+=0.7
            if body>=0.60 and cl[-1]>c[-1]["open"]: s+=1.5
            if st_sig=="BUY": s+=2.0
        else:
            if cl[-1]<lo20 and cl[-2]>=lo20: s+=2.0
            elif cl[-1]<lo20*1.003: s+=0.8
            if 55<=r<=75: s+=3.0
            elif r>75: s+=2.5
            elif r>30: s+=0.8
            if m<sig_ and macd_h<macd_hp: s+=2.5 if m<0 else 1.8
            elif m<sig_: s+=1.0
            if k>75 and k<kp: s+=2.5
            elif k>65 and k<kp: s+=1.5
            if up_bb and cl[-1]>=up_bb*0.995: s+=2.0
            elif mid_bb and cl[-1]>mid_bb: s+=0.8
            if roc3<0: s+=0.7
            if body>=0.60 and cl[-1]<c[-1]["open"]: s+=1.5
            if st_sig=="SELL": s+=2.0
        if adx>=45: s+=2.0
        elif adx>=35: s+=1.5
        elif adx>=25: s+=0.8
        elif adx>=12: s+=0.3
        _,pb=pivot_signal(c,"TRENDING_UP" if up else "TRENDING_DN")
        s+=pb*10
        return s

    if trend_up:
        s=score_direction(True)
        if s>=5.0: return "BUY",round(min(0.95,0.76+(s/15.0)*0.25+(0.02 if adx>=50 else 0)),3)
    if trend_down:
        s=score_direction(False)
        if s>=5.0: return "SELL",round(min(0.95,0.76+(s/15.0)*0.25+(0.02 if adx>=50 else 0)),3)
    return "NONE",0

def strat_binance_gold(c):
    if len(c)<60: return "NONE",0
    cl=[x["close"] for x in c]; vol=[x.get("volume",0) for x in c]
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
    if trend_up and 30<=r<=55: bp+=3
    elif trend_up and r<30: bp+=2
    if trend_dn and 45<=r<=70: sp+=3
    elif trend_dn and r>70: sp+=2
    if m>sig_ and m>0: bp+=2
    if m<sig_ and m<0: sp+=2
    if lo and cl[-1]<=lo*1.002: bp+=3
    if up and cl[-1]>=up*0.998: sp+=3
    if k<25: bp+=2
    if k>75: sp+=2
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
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50); e200=ema(cl,200) if len(cl)>=200 else ema(cl,100)
    if not e9 or not e21 or not e50: return "NONE",0
    r=rsi(cl,14); at=atr(c); m,sig_=macd(cl); up,mid,lo=bb(cl,20,2.0)
    k=stoch_k(c,14); adx_v,pdi_v,mdi_v=calc_adx_full(c,14)
    avg_vol=sum(vol[-20:])/20 if len(vol)>=20 else 1
    if adx_v<18 or (avg_vol>0 and vol[-1]<avg_vol*0.4): return "NONE",0
    long_bull=e200 and cl[-1]>e200[-1]; long_bear=e200 and cl[-1]<e200[-1]
    bp=0; sp=0
    if e9[-1]>e21[-1]>e50[-1]: bp+=3+(2 if long_bull else 0)
    if e9[-1]<e21[-1]<e50[-1]: sp+=3+(2 if long_bear else 0)
    if len(e9)>=2 and e9[-2]<e21[-2] and e9[-1]>e21[-1]: bp+=3
    if len(e9)>=2 and e9[-2]>e21[-2] and e9[-1]<e21[-1]: sp+=3
    if adx_v>=35: bp+=2 if pdi_v>mdi_v else 0; sp+=2 if mdi_v>pdi_v else 0
    if 30<=r<=50 and long_bull: bp+=3
    elif r<30: bp+=2
    if 50<=r<=70 and long_bear: sp+=3
    elif r>70: sp+=2
    if m>sig_ and m>0: bp+=2
    elif m>sig_: bp+=1
    if m<sig_ and m<0: sp+=2
    elif m<sig_: sp+=1
    if lo and cl[-1]<=lo: bp+=3
    if up and cl[-1]>=up: sp+=3
    if k<20: bp+=2
    if k>80: sp+=2
    if vol[-1]>avg_vol*2.0:
        if bp>sp: bp+=3
        elif sp>bp: sp+=3
    hi20=max(hi[-21:-1]); lo20=min(lo_[-21:-1])
    if cl[-1]>hi20 and cl[-2]<=hi20: bp+=3 if vol[-1]>avg_vol*1.5 else 1
    if cl[-1]<lo20 and cl[-2]>=lo20: sp+=3 if vol[-1]>avg_vol*1.5 else 1
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

# ✅ TOUT STRATEGIES — KONPLÈ
STRATEGIES={
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
            if nxt["low"]<=entry-sl*0.0001: pnl=-sl*lot*10; losses+=1
            elif nxt["high"]>=entry+tp*0.0001: pnl=tp*lot*10; wins+=1
            else:
                pnl=(nxt["close"]-entry)*lot*100000
                wins+=1 if pnl>0 else 0; losses+=0 if pnl>0 else 1
        else:
            if nxt["high"]>=entry+sl*0.0001: pnl=-sl*lot*10; losses+=1
            elif nxt["low"]<=entry-tp*0.0001: pnl=tp*lot*10; wins+=1
            else:
                pnl=(entry-nxt["close"])*lot*100000
                wins+=1 if pnl>0 else 0; losses+=0 if pnl>0 else 1
        bal+=pnl; equity.append(round(bal,2)); trades.append({"s":s,"e":round(entry,5),"pnl":round(pnl,2)})
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
    return {"trades":tot,"wins":wins,"losses":losses,"win_rate":round(wins/tot*100,1) if tot else 0,
            "net_pnl":net,"return_pct":round(net/equity[0]*100,2),"max_dd":round(dd,2),
            "pf":round(gp/gl,2) if gl else 999,"sharpe":round(avg/std*math.sqrt(252),2) if std>0 else 0,
            "equity":equity[-50:]}


# ═══════════════════════════════════════════════════════════
# DERIV CLIENT — WebSocket (TOKEN KLASIK SÈLMAN)
# ═══════════════════════════════════════════════════════════
class DerivClient:
    def __init__(self, token: str, app_id: str = "1089"):
        self.token=token; self.app_id=app_id; self._bal=0.0; self._loginid=None

    def connect(self) -> float:
        ok,balance,loginid,used_app_id,note=connect_classic_token(self.token,self.app_id)
        if not ok: raise Exception(note)
        self._bal=balance; self._loginid=loginid
        if used_app_id: self.app_id=used_app_id
        return self._bal

    def _auth(self, ws): ws.send(json.dumps({"authorize": self.token}))

    def get_candles(self, symbol="R_100", count=200, gran=60):
        import websocket as wsl
        res=[None]; done=threading.Event()
        def on_msg(ws, msg):
            d=json.loads(msg)
            if d.get("msg_type")=="authorize" and "error" not in d:
                ws.send(json.dumps({"ticks_history":symbol,"count":count,"end":"latest","granularity":gran,"style":"candles","adjust_start_time":1}))
            elif "candles" in d: res[0]=d["candles"]; done.set()
            elif "error" in d: done.set()
        w=wsl.WebSocketApp(f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws: self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=25)
        if not res[0]: return []
        return [{"open":float(c["open"]),"high":float(c["high"]),"low":float(c["low"]),
                 "close":float(c["close"]),"volume":1000,"time":c["epoch"]} for c in res[0]]

    def place_trade(self, symbol, direction, amount=1.0, duration_secs=60):
        import websocket as wsl
        res=[None]; err=[None]; done=threading.Event()
        ct="CALL" if direction=="BUY" else "PUT"
        if duration_secs<=60: dv,du=1,"m"
        elif duration_secs<=300: dv,du=5,"m"
        elif duration_secs<=900: dv,du=15,"m"
        elif duration_secs<=3600: dv,du=1,"h"
        else: dv,du=4,"h"
        def on_msg(ws, msg):
            d=json.loads(msg); mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d:
                ws.send(json.dumps({"proposal":1,"amount":max(0.5,float(amount)),"basis":"stake",
                                    "contract_type":ct,"currency":"USD","symbol":symbol,"duration":dv,"duration_unit":du}))
            elif mt=="proposal":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                ws.send(json.dumps({"buy":d["proposal"]["id"],"price":d["proposal"]["ask_price"]}))
            elif mt=="buy":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                res[0]=d.get("buy",{}); done.set()
        w=wsl.WebSocketApp(f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws: self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=30)
        if err[0]: raise Exception(err[0])
        return res[0] or {}

    def get_balance_sync(self) -> float:
        import websocket as wsl
        res=[None]; done=threading.Event()
        def on_msg(ws, msg):
            d=json.loads(msg)
            if d.get("msg_type")=="authorize" and "error" not in d:
                ws.send(json.dumps({"balance":1,"account":"current"}))
            elif d.get("msg_type")=="balance":
                b=d.get("balance",{}).get("balance")
                if b is not None: res[0]=float(b); done.set()
            elif "error" in d: done.set()
        w=wsl.WebSocketApp(f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws: self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=15)
        if res[0]: self._bal=res[0]
        return res[0] or self._bal

    def transfer_to_account(self, account_id, amount):
        import websocket as wsl
        res=[None]; err=[None]; done=threading.Event()
        def on_msg(ws, msg):
            d=json.loads(msg); mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d:
                ws.send(json.dumps({"transfer_between_accounts":1,"account_to":account_id,
                                    "amount":round(float(amount),2),"currency":"USD"}))
            elif mt=="transfer_between_accounts":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                res[0]=d; done.set()
        w=wsl.WebSocketApp(f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws: self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=20)
        if err[0]: raise Exception(err[0])
        return res[0]

    @property
    def balance(self): return self._bal


# ═══════════════════════════════════════════════════════════
# DERIV DIGITS CLIENT — DUAL (PAT=REST / KLASIK=WS)
# ═══════════════════════════════════════════════════════════
class DerivDigitsClient:
    def __init__(self, token: str, app_id: str = "1089"):
        self.token=token; self.app_id=app_id; self._bal=0.0
        self._is_pat=is_pat_token(token)
        self._rest=DerivRESTClient(token) if self._is_pat else None

    def connect(self) -> float:
        ok,bal,_,used,note=connect_deriv_token(self.token,self.app_id)
        if not ok: raise Exception(note)
        self._bal=bal
        if used: self.app_id=used
        if self._rest: self._rest._bal=bal
        return self._bal

    def _auth(self, ws): ws.send(json.dumps({"authorize": self.token}))

    def get_ticks(self, symbol="R_10", count=100):
        if self._is_pat and self._rest: return self._rest.get_ticks(symbol,count)
        import websocket as wsl
        res=[None]; done=threading.Event()
        def on_msg(ws, msg):
            d=json.loads(msg)
            if d.get("msg_type")=="authorize" and "error" not in d:
                ws.send(json.dumps({"ticks_history":symbol,"count":count,"end":"latest","style":"ticks"}))
            elif d.get("msg_type")=="history": res[0]=d.get("history",{}); done.set()
            elif "error" in d: done.set()
        w=wsl.WebSocketApp(f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws: self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=25)
        if not res[0]: return []
        return [{"price":float(p),"time":t} for p,t in zip(res[0].get("prices",[]),res[0].get("times",[]))]

    def place_digits_trade(self, symbol, contract_type, amount=0.35, barrier=None):
        if self._is_pat and self._rest: return self._rest.place_digits_trade(symbol,contract_type,amount,barrier)
        import websocket as wsl
        res=[None]; err=[None]; done=threading.Event()
        proposal={"proposal":1,"amount":max(0.35,float(amount)),"basis":"stake",
                  "contract_type":contract_type,"currency":"USD","symbol":symbol,"duration":5,"duration_unit":"t"}
        if barrier is not None: proposal["barrier"]=str(barrier)
        def on_msg(ws, msg):
            d=json.loads(msg); mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d: ws.send(json.dumps(proposal))
            elif mt=="proposal":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                ws.send(json.dumps({"buy":d["proposal"]["id"],"price":d["proposal"]["ask_price"]}))
            elif mt=="buy":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                res[0]=d.get("buy",{}); done.set()
        w=wsl.WebSocketApp(f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws: self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=30)
        if err[0]: raise Exception(err[0])
        return res[0] or {}

    def wait_contract_result(self, contract_id, timeout=30):
        if self._is_pat and self._rest: return self._rest.wait_contract_result(contract_id,timeout)
        import websocket as wsl
        res=[None]; done=threading.Event()
        def on_msg(ws, msg):
            d=json.loads(msg); mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d:
                ws.send(json.dumps({"proposal_open_contract":1,"contract_id":contract_id,"subscribe":1}))
            elif mt=="proposal_open_contract":
                poc=d.get("proposal_open_contract",{})
                if poc.get("status","") in ("won","lost","sold"): res[0]=poc; done.set()
        w=wsl.WebSocketApp(f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws: self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=timeout)
        return res[0]

    def get_balance_sync(self) -> float:
        if self._is_pat and self._rest: return self._rest.get_balance_sync()
        import websocket as wsl
        res=[None]; done=threading.Event()
        def on_msg(ws, msg):
            d=json.loads(msg)
            if d.get("msg_type")=="authorize" and "error" not in d:
                ws.send(json.dumps({"balance":1,"account":"current"}))
            elif d.get("msg_type")=="balance":
                b=d.get("balance",{}).get("balance")
                if b is not None: res[0]=float(b); done.set()
            elif "error" in d: done.set()
        w=wsl.WebSocketApp(f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws: self._auth(ws))
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=15)
        if res[0]: self._bal=res[0]
        return res[0] or self._bal

    def transfer_to_account(self, account_id, amount):
        if self._is_pat and self._rest: return self._rest.transfer_to_account(account_id,amount)
        import websocket as wsl
        res=[None]; err=[None]; done=threading.Event()
        def on_msg(ws, msg):
            d=json.loads(msg); mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d:
                ws.send(json.dumps({"transfer_between_accounts":1,"account_to":account_id,
                                    "amount":round(float(amount),2),"currency":"USD"}))
            elif mt=="transfer_between_accounts":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                res[0]=d; done.set()
        w=wsl.WebSocketApp(f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}",
                           on_message=on_msg,on_open=lambda ws: self._auth(ws))
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
    def __init__(self, key, secret):
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
    def get_candles(self, symbol="BTCUSDT", interval="15m", limit=200):
        k=self.c.get_klines(symbol=symbol,interval=interval,limit=limit)
        return [{"open":float(x[1]),"high":float(x[2]),"low":float(x[3]),"close":float(x[4]),"volume":float(x[5]),"time":x[0]} for x in k]
    def get_symbol_info_cached(self, symbol):
        try: return self.c.get_symbol_info(symbol)
        except: return None
    def _get_filter(self, symbol, ft):
        info=self.get_symbol_info_cached(symbol)
        if not info: return None
        for f in info.get("filters",[]):
            if f["filterType"]==ft: return f
        return None
    def get_min_notional(self, symbol):
        f=self._get_filter(symbol,"MIN_NOTIONAL") or self._get_filter(symbol,"NOTIONAL")
        return float(f.get("minNotional",10)) if f else 10.0
    def get_qty_precision(self, symbol):
        f=self._get_filter(symbol,"LOT_SIZE")
        if not f: return 3
        step=float(f["stepSize"])
        for p,v in [(0,1),(1,.1),(2,.01),(3,.001)]:
            if step>=v: return p
        return 4
    def get_min_qty(self, symbol):
        f=self._get_filter(symbol,"LOT_SIZE"); return float(f["minQty"]) if f else 0.001
    def get_price_precision(self, symbol):
        f=self._get_filter(symbol,"PRICE_FILTER")
        if not f: return 2
        tick=float(f["tickSize"])
        for p,v in [(0,1),(1,.1),(2,.01),(3,.001)]:
            if tick>=v: return p
        return 4
    def place_trade(self, symbol, direction, amount_usdt=10.0, sl_pct=0.018, tp_pct=0.035):
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
                elif s["status"] in ("CANCELED","EXPIRED","REJECTED"): break
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
        except Exception as e: logger.warning(f"OCO echwe ({e})")
        return order
    def send_profit(self, amount):
        try:
            r=self.c.withdraw(coin="USDT",address=PROFIT_WALLET,amount=amount,network="ERC20")
            logger.info(f"Profit sent: ${amount}"); return r
        except Exception as e: logger.error(f"Profit: {e}"); return None

class BinanceUSClient(BinanceClient):
    def __init__(self, key, secret):
        from binance.client import Client
        self.c=Client(key,secret,tld="us")
    def send_profit(self, amount):
        try:
            r=self.c.withdraw(coin="USDT",address=PROFIT_WALLET,amount=amount,network="ERC20")
            return r
        except Exception as e: logger.error(f"Profit BinanceUS: {e}"); return None


# ═══════════════════════════════════════════════════════════
# DIGITS LOGIC + UTILS
# ═══════════════════════════════════════════════════════════
def get_last_digit(price):
    s=f"{price:.5f}".replace('.',''); return int(s[-1])

def analyze_digits_ticks(ticks, threshold=4):
    if len(ticks)<50: return "NONE",0
    digits=[get_last_digit(t["price"]) for t in ticks]
    last50=digits[-50:]; last20=digits[-20:]
    over_count=sum(1 for d in last50 if d>threshold); under_count=sum(1 for d in last50 if d<=threshold)
    over20=sum(1 for d in last20 if d>threshold); under20=sum(1 for d in last20 if d<=threshold)
    last5=digits[-5:]; streak_u=all(d<=threshold for d in last5); streak_o=all(d>threshold for d in last5)
    sig="NONE"; conf=0.0
    if under_count>=35 and under20>=14: conf=0.65 if streak_u else 0.72; sig="OVER"
    elif over_count>=35 and over20>=14: conf=0.65 if streak_o else 0.72; sig="UNDER"
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

def _check_limits(st, cfg):
    target=float(cfg.get("profit_target",0)); loss=float(cfg.get("loss_limit",0))
    if target>0 and st["total_pnl"]>=target:
        add_log(st,f"🎯 OBJEKTIF ${target:.2f} RIVE! Bot kanpe!","SUCCESS"); st["running"]=False; return True
    if loss>0 and st["total_pnl"]<=-abs(loss):
        add_log(st,f"🛑 LIMIT PÈT ${loss:.2f} RIVE! Bot kanpe!","ERROR"); st["running"]=False; return True
    return False

def _refresh_balance(api, st):
    try:
        b=api.get_balance_sync()
        if b and b>0: st["balance"]=b
    except: pass


# ═══════════════════════════════════════════════════════════
# TRADING LOOPS
# ═══════════════════════════════════════════════════════════
def digits_trading_loop(st, bot_id=None):
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
                    if status=="won":
                        pnl=sell_price-buy_price; won=True; st["balance"]=bal_open+pnl
                        add_log(st,f"✅ WON! +${pnl:.2f} | ${st['balance']:.2f}","SUCCESS")
                    elif status=="lost":
                        pnl=-buy_price; won=False; st["balance"]=bal_open+pnl
                        add_log(st,f"❌ LOST -${buy_price:.2f} | ${st['balance']:.2f}","WARN")
                    else:
                        time.sleep(5); nb=api.get_balance_sync()
                        if nb and nb>0: pnl=nb-bal_before; st["balance"]=nb; won=pnl>0
                        else: pnl=-current_lot
                else:
                    time.sleep(5); nb=api.get_balance_sync()
                    if nb and nb>0: st["balance"]=nb; pnl=nb-bal_before; won=pnl>0.01
                    else: pnl=-current_lot; won=False
                if won:
                    current_lot=base_lot; consec_losses=0; total_lost=0.0
                else:
                    loss=abs(pnl) if abs(pnl)>0.01 else current_lot; total_lost+=loss; consec_losses+=1
                    if consec_losses<=4:
                        current_lot=max(base_lot,min(round((total_lost+base_lot)/PAYOUT,2),50.0))
                        add_log(st,f"⚠ Pèt #{consec_losses}/4 | Rekipere:${total_lost:.2f} | Prochèn:${current_lot:.2f}","WARN")
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
            except Exception as e:
                add_log(st,f"Digits trade echwe: {e}","ERROR"); time.sleep(15)
        except Exception as e:
            add_log(st,f"Erè digits loop: {e}","ERROR"); time.sleep(15)
    add_log(st,"⏹ Digits Bot arrêté")


def binance_trading_loop(st, bot_id=None):
    if bot_id and st.get("bot_id")!=bot_id: return
    cfg=st["config"]; symbol=cfg.get("symbol","BTCUSDT"); strategy=cfg.get("strategy","confluence")
    lot=float(cfg.get("lot",11.0)); tf=int(cfg.get("tf_secs",900)); min_conf=float(cfg.get("min_conf",0.75))
    is_gold="XAU" in symbol.upper() or "GOLD" in symbol.upper() or "XAG" in symbol.upper()
    SL_PCT=0.015 if is_gold else 0.020; TP_PCT=0.030 if is_gold else 0.040
    if strategy=="binance_gold" or is_gold: fn=strat_binance_gold
    elif strategy=="binance_crypto": fn=strat_binance_crypto
    elif strategy=="confluence": fn=lambda c: strat_confluence_binance(c,symbol)
    else: fn=STRATEGIES.get(strategy,strat_confluence_elite)
    iv={60:"1m",300:"5m",900:"15m",3600:"1h",14400:"4h"}.get(tf,"15m")
    base_lot=max(11.0,lot); current_lot=base_lot; consec_losses=0; total_lost=0.0
    add_log(st,f"🚀 Binance ELITE | {symbol} | Base:${base_lot} | Conf:{min_conf:.0%}")
    while st["running"]:
        if bot_id and st.get("bot_id")!=bot_id: add_log(st,"⏹ Bot anile","WARN"); return
        if _check_limits(st,cfg): break
        try:
            api=st.get("binance_api")
            if not api: add_log(st,"Binance pa konekte — STOP","ERROR"); st["running"]=False; break
            try:
                b=api.balance
                if b and b>0: st["balance"]=b
            except: pass
            try:
                min_notional=api.get_min_notional(symbol)
                if current_lot<min_notional*1.05: current_lot=round(min_notional*1.1,2)
            except: pass
            if st["balance"]<current_lot:
                add_log(st,f"⚠ Balans ${st['balance']:.2f} < Mise ${current_lot:.2f}","WARN")
                current_lot=base_lot; time.sleep(30); continue
            candles=api.get_candles(symbol,iv,200)
            if len(candles)<50: add_log(st,f"Pa ase done ({len(candles)}) — tann...","WARN"); time.sleep(60); continue
            sig,conf=fn(candles)
            add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%}")
            if sig=="NONE" or conf<min_conf: add_log(st,"⏭ Siyal fèb — tann..."); time.sleep(tf); continue
            entry=candles[-1]["close"]; bal_before=api.balance
            try:
                order=api.place_trade(symbol,sig,current_lot,SL_PCT,TP_PCT)
                add_log(st,f"✅ Limit+OCO plase | SL:{SL_PCT*100:.1f}% TP:{TP_PCT*100:.1f}%","SUCCESS")
                trade={"id":len(st["trades"])+1,"time":datetime.now().strftime("%H:%M:%S"),
                       "symbol":symbol,"side":sig,"entry":round(entry,4),"conf":f"{conf:.0%}",
                       "strategy":strategy,"tf":iv,"stake":round(current_lot,2),"pnl":0.0,"status":"open","regime":"—"}
                st["trades"].insert(0,trade)
            except Exception as e:
                add_log(st,f"Trade echwe: {e}","ERROR"); time.sleep(30); continue
            time.sleep(15)
            try:
                bal_after=api.balance; st["balance"]=bal_after; pnl_chk=bal_after-bal_before
                if abs(pnl_chk)>0.01:
                    if st["trades"]: st["trades"][0]["pnl"]=round(pnl_chk,4); st["trades"][0]["status"]="won" if pnl_chk>0 else "open"
                    st["total_pnl"]+=pnl_chk
                    if pnl_chk>0:
                        ps=round(pnl_chk*PROFIT_PCT,4); st["profit_sent"]+=ps
                        if ps>=0.10:
                            try: api.send_profit(ps)
                            except: pass
            except: pass
            time.sleep(tf)
        except Exception as e:
            add_log(st,f"Erè binance loop: {e}","ERROR"); time.sleep(30)
    add_log(st,"⏹ Binance Bot arrêté")


def trading_loop(st, bot_id=None):
    if bot_id and st.get("bot_id")!=bot_id: return
    cfg=st["config"]; symbol=cfg.get("symbol","R_100"); strategy=cfg.get("strategy","confluence")
    lot=float(cfg.get("lot",0.5)); tf=int(cfg.get("tf_secs",900)); min_conf=float(cfg.get("min_conf",0.65))
    fn=STRATEGIES.get(strategy,strat_confluence_elite)
    wait_after=tf+90; base_lot=round(max(0.5,lot),2); current_lot=base_lot
    consec_losses=0; total_lost=0.0; MAX_LOSSES=3; PAUSE=45
    add_log(st,f"🚀 BonheurBot ELITE v6.1 | {symbol} | {strategy} | TF:{tf//60}min | Conf:{min_conf:.0%}")
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
            add_log(st,f"📡 {len(candles)} bouji | {symbol} | {regime} | ADX:{adx_val:.0f} | ST:{st_sig}({st_c:.0%})")
            if consec_losses>=MAX_LOSSES:
                mache_bon=regime in("TRENDING_UP","TRENDING_DN","RANGING") and adx_val>=12
                if not mache_bon:
                    add_log(st,f"⏸ PÒZ APRE {consec_losses} PÈT | Ap tann {PAUSE}sek...","WARN")
                    time.sleep(PAUSE); continue
                else: add_log(st,f"✅ MACHE BON ANKÒ! {regime} ADX:{adx_val:.0f}","SUCCESS")
            if regime=="VOLATILE":
                add_log(st,f"⏸ VOLATILE — pa trade. Tann {min(tf,120)}sek...","WARN"); time.sleep(min(tf,120)); continue
            if strategy=="confluence":
                req=3 if consec_losses==0 else(4 if consec_losses<=2 else 5)
                sig,conf=strat_confluence_elite(candles,min_strats=req,min_per_conf=0.65)
            elif strategy=="deriv_pro": sig,conf=strat_deriv_pro_elite(candles)
            elif strategy=="supertrend": sig,conf=supertrend(candles)
            elif strategy=="heikin_ashi": sig,conf=heikin_ashi_trend(candles)
            elif strategy=="chandelier": sig,conf=chandelier_exit(candles)
            else: sig,conf=fn(candles)
            add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | {strategy}")
            if sig=="BUY" and regime=="TRENDING_DN": add_log(st,"⛔ REJTE BUY — TRENDING_DN","WARN"); time.sleep(tf); continue
            if sig=="SELL" and regime=="TRENDING_UP": add_log(st,"⛔ REJTE SELL — TRENDING_UP","WARN"); time.sleep(tf); continue
            adaptive=min_conf+(0.02 if consec_losses==1 else(0.04 if consec_losses>=2 else 0))
            if sig=="NONE" or conf<adaptive:
                reason="Pa gen siyal" if sig=="NONE" else f"Conf {conf:.0%} < {adaptive:.0%}"
                add_log(st,f"⏭ {reason} — tann..."); time.sleep(tf); continue
            if st["balance"]<current_lot:
                add_log(st,f"⚠ Balans ${st['balance']:.2f} < Mise ${current_lot:.2f} — reset","WARN")
                current_lot=base_lot; consec_losses=0; total_lost=0.0
            _,piv_b=pivot_signal(candles,"TRENDING_UP" if sig=="BUY" else "TRENDING_DN")
            entry=candles[-1]["close"]
            add_log(st,f"⚡ {sig} @ {entry:.5f} | Conf:{conf:.0%} | ${current_lot:.2f}{' 🎯+PIVOT' if piv_b>0 else ''}")
            bal_before=st["balance"]; pnl=0.0; ok=False
            try:
                r=api.place_trade(symbol,sig,max(0.5,current_lot),duration_secs=tf)
                if r.get("contract_id"):
                    cid=r["contract_id"]; bal_open=float(r.get("balance_after",bal_before-current_lot))
                    st["balance"]=bal_open; ok=True
                    add_log(st,f"⏳ #{cid} | ${bal_open:.2f} | Ap tann {wait_after//60}min...","SUCCESS")
                    time.sleep(wait_after)
                    bal_close=None
                    for _ in range(5):
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
            except Exception as e:
                add_log(st,f"Trade echwe: {e}","ERROR")
            if ok:
                if pnl>0:
                    prev_l=consec_losses; current_lot=base_lot; consec_losses=0; total_lost=0.0
                    if prev_l>0: add_log(st,f"🏆 REKIPERE! (te gen {prev_l} pèt)","SUCCESS")
                else:
                    loss=abs(pnl) if abs(pnl)>0.01 else current_lot; total_lost+=loss; consec_losses+=1
                    if consec_losses<MAX_LOSSES:
                        current_lot=max(0.5,min(round((total_lost+base_lot)/0.95,2),100.0))
                        add_log(st,f"⚠ PÈT #{consec_losses} | Total:${total_lost:.2f} | Prochèn:${current_lot:.2f}","WARN")
                    else:
                        current_lot=max(0.5,min(round((total_lost+base_lot)/0.95,2),100.0))
                        add_log(st,f"🚨 3 PÈT AFILE! PÒZE OTOMATIK | ${current_lot:.2f} | Ap tann mache...","WARN")
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
        except Exception as e:
            add_log(st,f"Erè: {e}","ERROR")
        time.sleep(tf)
    add_log(st,"⏹ BonheurBot ELITE v6.1 arrêté")


# ═══════════════════════════════════════════════════════════
# FLASK ROUTES
# ═══════════════════════════════════════════════════════════
@app.route("/api/connect", methods=["POST"])
def api_connect():
    st=get_state()
    try:
        d=freq.json; broker=d.get("broker")
        if broker=="deriv":
            raw_token=d.get("token","").strip(); app_id=d.get("app_id","1089").strip() or "1089"
            if not raw_token: return jsonify({"ok":False,"error":"Kole token ou anvan!"})
            tok_type="PAT" if is_pat_token(raw_token) else "Klasik"
            add_log(st,f"🔑 {tok_type} token → {'REST API Bearer' if tok_type=='PAT' else f'WebSocket app_id={app_id}'}","INFO")
            ok,balance,loginid,used_app_id,note=connect_deriv_token(raw_token,app_id)
            if not ok:
                if tok_type=="PAT":
                    err=(f"❌ Token PAT echwe via REST API\n\n{note}\n\n"
                         f"SOLISYON — ITILIZE TOKEN KLASIK:\n"
                         f"1. app.deriv.com → foto ou → API Token\n"
                         f"2. Create new token → ✓ Read ✓ Trade ✓ Payments\n"
                         f"3. Kole token (PA kòmanse ak pat_) — App ID: 1089")
                else:
                    err=(f"❌ Koneksyon echwe\n\n{note}\n\n"
                         f"VERIFYE:\n✓ Token valid epi pa ekspire\n✓ Pèmisyon: Read + Trade + Payments\n"
                         f"✓ Kreye nouvo sou app.deriv.com → API Token")
                return jsonify({"ok":False,"error":err})

            if is_pat_token(raw_token):
                api_main=DerivRESTClient(raw_token); api_main._bal=balance; api_main._loginid=loginid
                api_digits=DerivDigitsClient(raw_token,app_id); api_digits._bal=balance; api_digits._rest=api_main
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


@app.route("/api/start", methods=["POST"])
def api_start():
    st=get_state()
    if not st.get("access"): return jsonify({"ok":False,"error":"⚠ Ou bezwen yon kòd aksè valid!"})
    if not st["connected"]: return jsonify({"ok":False,"error":"Konekte broker anvan!"})
    if st["running"]: return jsonify({"ok":False,"error":"Bot déjà ap kouri"})
    d=freq.json or {}
    tf_map={"1m":60,"5m":300,"15m":900,"1h":3600,"4h":14400}
    st["config"]={
        "broker":st["broker"],"symbol":d.get("symbol","R_100"),"strategy":d.get("strategy","confluence"),
        "lot":d.get("lot",0.5),"tf_secs":tf_map.get(d.get("tf","15m"),900),
        "min_conf":d.get("min_conf",0.65),"profit_target":float(d.get("profit_target",0)),
        "loss_limit":float(d.get("loss_limit",0)),"mode":d.get("mode","forex"),
        "digit_type":d.get("digit_type","over_under"),
    }
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

@app.route("/api/stop", methods=["POST"])
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

@app.route("/api/backtest", methods=["POST"])
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

@app.route("/api/login", methods=["POST"])
def api_login():
    st=get_state(); d=freq.json or {}
    token=d.get("session_token","").strip(); code=d.get("code","").strip().upper()
    if token:
        ok,msg_text=validate_session(token)
        if ok:
            with _sess_lock: is_adm=_sessions.get(token,{}).get("is_admin",False)
            st["access"]=True; st["session_token"]=token; st["is_admin"]=is_adm
            return jsonify({"ok":True,"msg":msg_text,"session_token":token,"is_admin":is_adm})
        st["access"]=False
        return jsonify({"ok":False,"msg":msg_text,"need_code":True})
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

@app.route("/api/admin/codes", methods=["POST"])
def admin_get_codes():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
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

@app.route("/api/admin/add_code", methods=["POST"])
def admin_add_code():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    code=d.get("code","").strip().upper()
    if not code or len(code)<3: return jsonify({"ok":False,"error":"Kòd dwe gen 3+ karaktè"})
    if code in ACCESS_CODES: return jsonify({"ok":False,"error":"Kòd sa deja egziste"})
    is_adm=d.get("is_adm",False)
    ACCESS_CODES[code]={"created_at":None if is_adm else time.time(),"used":False,"is_adm":is_adm}
    return jsonify({"ok":True,"msg":f"✓ Kòd {code} kreye [{'Admin' if is_adm else 'Itilizatè'}]"})

@app.route("/api/admin/revoke_code", methods=["POST"])
def admin_revoke_code():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    code=d.get("code","").strip().upper()
    if not code or code not in ACCESS_CODES: return jsonify({"ok":False,"error":"Kòd pa jwenn"})
    if code=="BONHEURWIIN": return jsonify({"ok":False,"error":"Pa ka revoke kòd ADM prensipal"})
    del ACCESS_CODES[code]
    return jsonify({"ok":True,"msg":f"✓ Kòd {code} revoke"})

@app.route("/api/admin/reset_code", methods=["POST"])
def admin_reset_code():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    code=d.get("code","").strip().upper()
    if code not in ACCESS_CODES: return jsonify({"ok":False,"error":"Kòd pa jwenn"})
    ACCESS_CODES[code]["used"]=False
    if not(ACCESS_CODES[code].get("is_adm") or ACCESS_CODES[code]["created_at"] is None):
        ACCESS_CODES[code]["created_at"]=time.time()
    return jsonify({"ok":True,"msg":f"✓ Kòd {code} reset"})

@app.route("/api/admin/users", methods=["POST"])
def admin_get_users():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    users=[]
    with _user_lock:
        for uid,ust in _user_states.items():
            users.append({"uid":uid[:8]+"...","connected":ust.get("connected",False),
                          "broker":ust.get("broker","—"),"running":ust.get("running",False),
                          "balance":round(ust.get("balance",0),2),"pnl":round(ust.get("total_pnl",0),2),
                          "trades":len(ust.get("trades",[])),"symbol":ust.get("config",{}).get("symbol","—"),
                          "strategy":ust.get("config",{}).get("strategy","—")})
    return jsonify({"ok":True,"users":users,"total":len(users)})

@app.route("/api/admin/stop_user", methods=["POST"])
def admin_stop_user():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    uid_prefix=d.get("uid","").replace("...",""); stopped=0
    with _user_lock:
        for uid,ust in _user_states.items():
            if uid.startswith(uid_prefix): ust["running"]=False; ust["bot_id"]=None; stopped+=1
    return jsonify({"ok":True,"msg":f"✓ {stopped} bot(s) kanpe"})

@app.route("/api/admin/sessions", methods=["POST"])
def admin_sessions():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    today=date.today(); sessions=[]
    with _sess_lock:
        for token,sess in _sessions.items():
            exp=date.fromisoformat(sess["expire"])
            sessions.append({"token":token[:8]+"...","expire":sess["expire"],
                             "days_left":(exp-today).days,"is_admin":sess.get("is_admin",False),
                             "active":(exp-today).days>0})
    return jsonify({"ok":True,"sessions":sessions,"total":len(sessions)})

@app.route("/api/admin/clean_sessions", methods=["POST"])
def admin_clean_sessions():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    today=date.today(); count=0
    with _sess_lock:
        expired=[t for t,s in _sessions.items() if date.fromisoformat(s["expire"])<=today]
        for t in expired: del _sessions[t]; count+=1
        if count: _save_sessions()
    return jsonify({"ok":True,"msg":f"✓ {count} sesyon ekspire efase"})

@app.route("/api/admin/clear_user", methods=["POST"])
def admin_clear_user():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    uid_prefix=d.get("uid","").replace("...",""); cleared=0
    with _user_lock:
        for uid,ust in _user_states.items():
            if uid.startswith(uid_prefix):
                ust["trades"]=[]; ust["total_pnl"]=0.0; ust["profit_sent"]=0.0; ust["log"]=[]; cleared+=1
    return jsonify({"ok":True,"msg":f"✓ {cleared} itilizatè efase"})

@app.route("/api/admin/clear_trades", methods=["POST"])
def admin_clear_trades():
    d=freq.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize — admin sèlman"})
    uid_prefix=d.get("uid","").replace("...",""); cleared=0
    with _user_lock:
        for uid,ust in _user_states.items():
            if uid.startswith(uid_prefix): ust["trades"]=[]; cleared+=1
    return jsonify({"ok":True,"msg":f"✓ {cleared} itilizatè: trades efase"})

@app.route("/")
def index(): return render_template_string(HTML)


# ═══════════════════════════════════════════════════════════
# HTML INTERFACE — IDANTIK ANSYEN KOD LA (pa chanje)
# ═══════════════════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>💰 BonheurBot v6 ELITE</title>
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
.sl{color:#4A7080;font-size:10px;letter-spacing:1px;margin-bottom:5px}
.sv{font-size:21px;font-weight:700}
.box{background:#071219;border:1px solid #0D2233;border-radius:10px;padding:16px;margin-bottom:14px}
.bt{color:#00FF88;font-size:10px;letter-spacing:2px;font-weight:700;margin-bottom:12px}
.iw{margin-bottom:10px}.il{color:#4A7080;font-size:10px;letter-spacing:1px;margin-bottom:4px}
input,select{width:100%;background:#020C12;border:1px solid #0D2233;color:#C8E8F0;border-radius:6px;padding:8px 10px;font-size:12px;font-family:inherit;outline:none}
input:focus,select:focus{border-color:#00FF88}
select option{background:#071219}
.btn{background:transparent;border:1px solid #00FF88;color:#00FF88;border-radius:6px;padding:9px 22px;cursor:pointer;font-size:12px;font-family:inherit;letter-spacing:1px;font-weight:700;transition:.15s}
.btn:hover{background:#00FF8822}
.btn.b{border-color:#00D4FF;color:#00D4FF}.btn.b:hover{background:#00D4FF22}
.btn.r{border-color:#FF3B6B;color:#FF3B6B}.btn.r:hover{background:#FF3B6B22}
.btn.y{border-color:#FFD600;color:#FFD600}.btn.y:hover{background:#FFD60022}
.btn.fw{width:100%}
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
.badge-p{background:#FFD60018;border:1px solid #FFD60044;color:#FFD600;border-radius:4px;padding:2px 8px;font-size:10px;font-weight:700}
.badge-k{background:#00FF8818;border:1px solid #00FF8844;color:#00FF88;border-radius:4px;padding:2px 8px;font-size:10px;font-weight:700}
</style>
</head>
<body>
<div id="login-page" style="display:none;min-height:100vh;background:#040A0F;align-items:center;justify-content:center;flex-direction:column">
  <div style="background:#071219;border:1px solid #0D2233;border-radius:12px;padding:40px;max-width:420px;width:90%;text-align:center">
    <div style="font-size:32px;margin-bottom:8px">💰</div>
    <div style="font-size:20px;font-weight:900;color:#00FF88;letter-spacing:2px;margin-bottom:4px">BonheurBot Pro</div>
    <div style="color:#4A7080;font-size:11px;margin-bottom:24px">Trading Bot v6.1 ELITE — PAT Fix + All Strategies</div>
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
        <a href="https://wa.me/50942867885" target="_blank" style="display:inline-flex;align-items:center;gap:6px;margin-top:6px;background:#25D36618;border:1px solid #25D36644;color:#25D366;border-radius:6px;padding:6px 12px;text-decoration:none;font-size:11px;font-weight:700">📱 WhatsApp: +509 4286-7885</a>
      </div>
    </div>
  </div>
</div>

<div id="app-page" style="display:none">
<div class="hdr">
  <div style="display:flex;align-items:center;gap:12px">
    <div class="logo">💰 Bonheur<span>Bot</span> <span style="font-size:10px;color:#FFD600">ELITE v6.1</span></div>
    <div style="width:1px;height:20px;background:#0D2233"></div>
    <span id="hb" class="tag tg">DISCONNECTED</span>
    <span id="h-tok-type" style="display:none"></span>
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
        <select id="d-br" onchange="togBroker()">
          <option value="deriv">Deriv (Synthetic / Digits)</option>
          <option value="binance">Binance Global (Crypto / Gold)</option>
          <option value="binance_us">Binance US</option>
        </select>
      </div>
      <div id="fd">
        <div style="background:#020C12;border:1px solid #0D2233;border-radius:8px;padding:12px;margin-bottom:12px">
          <div style="color:#00FF88;font-size:10px;font-weight:700;margin-bottom:8px">✅ VRÈ PAT FIX v6.1 — DUAL SYSTEM</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div style="background:#00FF8810;border:1px solid #00FF8830;border-radius:6px;padding:8px">
              <div style="color:#00FF88;font-size:10px;font-weight:700;margin-bottom:4px">✅ TOKEN KLASIK</div>
              <div style="color:#4A7080;font-size:9px;line-height:1.8">PA kòmanse ak <code>pat_</code><br>→ WebSocket authorize<br>App ID: <b>1089</b><br>✅ Trade + Candles WS</div>
            </div>
            <div style="background:#FFD60010;border:1px solid #FFD60030;border-radius:6px;padding:8px">
              <div style="color:#FFD600;font-size:10px;font-weight:700;margin-bottom:4px">⚡ TOKEN PAT (pat_xxx)</div>
              <div style="color:#4A7080;font-size:9px;line-height:1.8">Kòmanse ak <code>pat_</code><br>→ REST API Bearer<br>✅ Trade REST + fallback WS<br>✅ Candles REST + fallback WS</div>
            </div>
          </div>
        </div>
        <div class="iw">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
            <div class="il" style="margin-bottom:0">TOKEN DERIV</div>
            <span id="tok-badge" style="display:none"></span>
          </div>
          <input id="d-tk" type="password" placeholder="Token Klasik oswa PAT (pat_xxx)..." oninput="autoDetectToken()">
          <div id="tok-hint" style="color:#4A7080;font-size:9px;margin-top:4px">Token Klasik PA kòmanse ak <code>pat_</code></div>
        </div>
        <div class="iw" id="appid-row">
          <div class="il">APP ID <span id="appid-note" style="color:#4A7080">(Token Klasik sèlman)</span></div>
          <input id="d-ai" value="1089" placeholder="1089">
        </div>
      </div>
      <div id="fb" style="display:none">
        <div class="iw"><div class="il">API KEY</div><input id="b-k" type="password" placeholder="Binance API Key"></div>
        <div class="iw"><div class="il">API SECRET</div><input id="b-s" type="password" placeholder="Binance API Secret"></div>
        <div id="fb-note" style="display:none;background:#FFD60010;border:1px solid #FFD60033;border-radius:6px;padding:8px;margin-bottom:8px;font-size:10px;color:#FFD600">🇺🇸 Binance US — api.binance.us</div>
      </div>
      <div id="cm" style="margin-bottom:8px"></div>
      <button class="btn b fw" onclick="doConn()">⚡ KONEKTE</button>
      <div id="cs" style="margin-top:10px"></div>
    </div>
    <div class="box">
      <div class="bt" style="display:flex;justify-content:space-between">
        <span>COURBE P&L</span><span id="s-pnl2" style="color:#00FF88;font-size:13px;font-weight:700">+$0.00</span>
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
</div>

<div id="pg-control" class="pg">
  <div class="g2">
    <div class="box">
      <div class="bt">PARAMÈT BOT ELITE v6.1</div>
      <div class="iw"><div class="il">MOD TRADING</div>
        <select id="c-mode" onchange="toggleMode()">
          <option value="forex">📈 Rise/Fall — Deriv Synthetic</option>
          <option value="digits">🎲 Digits Over/Under — Deriv</option>
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
              <option value="1m">1 minit</option><option value="5m">5 minit</option>
              <option value="15m" selected>15 minit ★★★</option><option value="1h">1 è</option><option value="4h">4 è</option>
            </select>
          </div>
        </div>
        <div class="g2">
          <div class="iw"><div class="il">MISE ($) — Min $0.50</div><input id="c-lot-forex" type="number" value="0.50" step="0.50" min="0.50"></div>
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
              <option value="macd_bollinger">📈 MACD+Bollinger</option>
              <option value="breakout">💥 Breakout</option>
              <option value="order_block">🧱 Order Block</option>
              <option value="stoch_ema">🔄 Stoch+EMA</option>
              <option value="fibonacci">🌀 Fibonacci</option>
              <option value="fvg">📐 Fair Value Gap</option>
            </select>
          </div>
        </div>
      </div>
      <div id="opts-digits" style="display:none">
        <div class="g2">
          <div class="iw"><div class="il">SENBOL</div>
            <select id="c-sy-digits"><option value="R_10" selected>R_10</option><option value="R_25">R_25</option><option value="R_50">R_50</option></select>
          </div>
          <div class="iw"><div class="il">TIP DIGITS</div>
            <select id="c-digit-type"><option value="over_under">Over 4 / Under 5</option><option value="even_odd">Even / Odd</option></select>
          </div>
        </div>
        <div class="iw"><div class="il">MISE ($) — Min $0.35</div><input id="c-lot-digits" type="number" value="0.35" step="0.10" min="0.35"></div>
      </div>
      <div id="opts-gold" style="display:none">
        <div class="g2">
          <div class="iw"><div class="il">SENBOL</div>
            <select id="c-sy-gold"><option value="XAUUSDT">XAUUSDT — Or</option><option value="XAGUSDT">XAGUSDT — Ajan</option></select>
          </div>
          <div class="iw"><div class="il">TIMEFRAME</div>
            <select id="c-tf-gold"><option value="5m">5 minit</option><option value="15m" selected>15 minit</option><option value="1h">1 è</option></select>
          </div>
        </div>
        <div class="iw"><div class="il">MISE USDT — Min $11</div><input id="c-lot-gold" type="number" value="11" step="1" min="11"></div>
      </div>
      <div id="opts-crypto" style="display:none">
        <div class="g2">
          <div class="iw"><div class="il">SENBOL</div>
            <select id="c-sy-crypto">
              <option value="BTCUSDT" selected>BTCUSDT</option><option value="ETHUSDT">ETHUSDT</option>
              <option value="BNBUSDT">BNBUSDT</option><option value="SOLUSDT">SOLUSDT</option><option value="XRPUSDT">XRPUSDT</option>
            </select>
          </div>
          <div class="iw"><div class="il">TIMEFRAME</div>
            <select id="c-tf-crypto"><option value="5m">5 minit</option><option value="15m" selected>15 minit</option><option value="1h">1 è</option><option value="4h">4 è</option></select>
          </div>
        </div>
        <div class="iw"><div class="il">MISE USDT — Min $11</div><input id="c-lot-crypto" type="number" value="11" step="1" min="11"></div>
      </div>
      <div class="g2">
        <div class="iw"><div class="il">KONFIDANS MIN</div>
          <select id="c-conf">
            <option value="0.60">60%</option><option value="0.65" selected>65% (rekòmande)</option>
            <option value="0.70">70%</option><option value="0.75">75%</option><option value="0.80">80%</option>
          </select>
        </div>
        <div class="iw"><div class="il">🎯 OBJEKTIF PROFIT ($)</div><input id="c-target" type="number" value="0" step="1" min="0"><div style="color:#00FF88;font-size:9px;margin-top:2px">0 = pa gen limit</div></div>
      </div>
      <div class="iw"><div class="il">🛑 LIMIT PÈT ($)</div><input id="c-loss" type="number" value="0" step="1" min="0"></div>
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
        <option value="confluence">🔥 Confluence ELITE</option><option value="deriv_pro">🚀 Deriv Pro ELITE</option>
        <option value="supertrend">📈 SuperTrend</option><option value="heikin_ashi">🕯 Heikin Ashi</option>
        <option value="ai">🤖 AI Score</option><option value="smc">🏛 SMC</option><option value="rsi">📉 RSI</option>
        <option value="macd_bollinger">📈 MACD+Bollinger</option><option value="breakout">💥 Breakout</option>
        <option value="order_block">🧱 Order Block</option><option value="fibonacci">🌀 Fibonacci</option>
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
  <div class="box"><div class="bt">LOGS SISTEM</div><div id="logs"></div></div>
</div>

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
      <div class="iw"><div class="il">TIP</div>
        <select id="new-code-type"><option value="user">👤 Itilizatè — 1 mwa</option><option value="adm">👑 Admin — pa janm ekspire</option></select>
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

</div></div>

<script>
const SK="bb_session_v61";
function saveToken(t){try{localStorage.setItem(SK,t)}catch(e){}try{sessionStorage.setItem(SK,t)}catch(e){}try{const x=new Date();x.setDate(x.getDate()+30);document.cookie=`${SK}=${t};expires=${x.toUTCString()};path=/;SameSite=Lax`}catch(e){}}
function getStoredToken(){try{const t=localStorage.getItem(SK);if(t)return t}catch(e){}try{const t=sessionStorage.getItem(SK);if(t)return t}catch(e){}try{const m=document.cookie.match(new RegExp("(^| )"+SK+"=([^;]+)"));if(m)return m[2]}catch(e){}return""}
function clearToken(){try{localStorage.removeItem(SK)}catch(e){}try{sessionStorage.removeItem(SK)}catch(e){}try{document.cookie=`${SK}=;expires=Thu,01 Jan 1970 00:00:00 UTC;path=/;`}catch(e){}}
function updateAdminTab(a){const t=document.getElementById("tab-admin");if(t)t.style.display=a?"block":"none"}

async function checkLogin(){
  const token=getStoredToken();if(!token){showLogin("");return}
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

function autoDetectToken(){
  const val=document.getElementById("d-tk").value.trim().toLowerCase();
  const badge=document.getElementById("tok-badge");
  const hint=document.getElementById("tok-hint");
  const appRow=document.getElementById("appid-row");
  if(val.startsWith("pat_")){
    badge.style.display="inline";badge.className="badge-p";badge.textContent="⚡ PAT → REST Bearer";
    hint.innerHTML='<span style="color:#FFD600">⚡ PAT token → REST API + WS fallback. Trade fonksyone!</span>';
    appRow.style.opacity="0.4";
  }else if(val.length>10){
    badge.style.display="inline";badge.className="badge-k";badge.textContent="✅ KLASIK → WebSocket";
    hint.innerHTML='<span style="color:#00FF88">✅ Token Klasik → WebSocket authorize. Trade + Candles WS!</span>';
    appRow.style.opacity="1";
  }else{
    badge.style.display="none";
    hint.innerHTML='Token Klasik PA kòmanse ak <code>pat_</code>';
    appRow.style.opacity="1";
  }
}
function togBroker(){
  const v=document.getElementById("d-br").value;
  document.getElementById("fd").style.display=v==="deriv"?"block":"none";
  document.getElementById("fb").style.display=(v==="binance"||v==="binance_us")?"block":"none";
  const note=document.getElementById("fb-note");
  if(note)note.style.display=v==="binance_us"?"block":"none";
}
function toggleMode(){
  const mode=document.getElementById("c-mode").value;
  document.getElementById("opts-forex").style.display=mode==="forex"?"block":"none";
  document.getElementById("opts-digits").style.display=mode==="digits"?"block":"none";
  document.getElementById("opts-gold").style.display=mode==="binance_gold"?"block":"none";
  document.getElementById("opts-crypto").style.display=mode==="binance_crypto"?"block":"none";
}
function getStartParams(){
  const mode=document.getElementById("c-mode").value;
  const conf=parseFloat(document.getElementById("c-conf").value);
  const target=parseFloat(document.getElementById("c-target").value||0);
  const loss=parseFloat(document.getElementById("c-loss").value||0);
  if(mode==="forex")return{mode:"forex",symbol:document.getElementById("c-sy-deriv").value,strategy:document.getElementById("c-st-forex").value,lot:parseFloat(document.getElementById("c-lot-forex").value),tf:document.getElementById("c-tf").value,min_conf:conf,profit_target:target,loss_limit:loss};
  if(mode==="digits")return{mode:"digits",symbol:document.getElementById("c-sy-digits").value,digit_type:document.getElementById("c-digit-type").value,lot:parseFloat(document.getElementById("c-lot-digits").value),tf:"1m",min_conf:conf,profit_target:target,loss_limit:loss,strategy:"digits"};
  if(mode==="binance_gold")return{mode:"forex",symbol:document.getElementById("c-sy-gold").value,strategy:"binance_gold",lot:parseFloat(document.getElementById("c-lot-gold").value),tf:document.getElementById("c-tf-gold").value,min_conf:conf,profit_target:target,loss_limit:loss};
  return{mode:"forex",symbol:document.getElementById("c-sy-crypto").value,strategy:"binance_crypto",lot:parseFloat(document.getElementById("c-lot-crypto").value),tf:document.getElementById("c-tf-crypto").value,min_conf:conf,profit_target:target,loss_limit:loss};
}
async function doConn(){
  const br=document.getElementById("d-br").value;
  const btn=event.target;btn.textContent="AP KONEKTE...";btn.disabled=true;
  const body={broker:br};
  if(br==="deriv"){
    const rawToken=document.getElementById("d-tk").value.trim();
    if(!rawToken){msg("cm","✗ Kole token ou anvan!",false);btn.textContent="⚡ KONEKTE";btn.disabled=false;return}
    const appId=document.getElementById("d-ai").value.trim()||"1089";
    const isPat=rawToken.toLowerCase().startsWith("pat_");
    body.token=rawToken;body.app_id=appId;
    msg("cm",`⏳ ${isPat?"PAT → REST API Bearer":"Klasik → WebSocket app_id="+appId} | Ap konekte...`,"ok");
  }
  if(br==="binance"||br==="binance_us"){
    body.api_key=document.getElementById("b-k").value.trim();
    body.api_secret=document.getElementById("b-s").value.trim();
    if(!body.api_key||!body.api_secret){msg("cm","✗ Mete API Key ak Secret!",false);btn.textContent="⚡ KONEKTE";btn.disabled=false;return}
    msg("cm","⏳ Ap konekte Binance...","ok");
  }
  try{
    const r=await fetch("/api/connect",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(d.ok){
      msg("cm",`✅ KONEKTE! $${d.balance.toFixed(2)} | ${d.note||""}`, "ok");
      document.getElementById("cs").innerHTML=`<div class="al ok">✓ <b>${d.broker||br}</b> | ${d.note||""} | $${d.balance.toFixed(2)}</div>`;
      if(d.token_type){
        const hb=document.getElementById("h-tok-type");hb.style.display="inline";
        hb.className=d.token_type==="PAT"?"badge-p":"badge-k";
        hb.textContent=d.token_type==="PAT"?"⚡ PAT-REST":"✅ Klasik-WS";
      }
    }else msg("cm",d.error||"✗ Echèk koneksyon",false);
  }catch(e){msg("cm","✗ Erè rezo: "+e.message,false)}
  btn.textContent="⚡ KONEKTE";btn.disabled=false;
}
async function doStart(){
  const body=getStartParams();
  const r=await fetch("/api/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const d=await r.json();
  if(d.ok){msg("ctm","✅ BonheurBot ELITE v6.1 démarre!","ok");document.getElementById("bs").style.display="none";document.getElementById("bx").style.display="inline-block"}
  else msg("ctm","✗ "+d.error,false);
}
async function doStop(){
  await fetch("/api/stop",{method:"POST"});
  msg("ctm","✅ Bot arrêté","ok");
  document.getElementById("bs").style.display="inline-block";
  document.getElementById("bx").style.display="none";
}
async function doBt(){
  const btn=event.target;btn.textContent="⏳ AP KALKILE...";btn.disabled=true;
  document.getElementById("btm").innerHTML=`<div class="al in">⏳ Ap fè backtest — tann...</div>`;
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
        <div class="stat"><div class="sl">PROFIT FACTOR</div><div class="sv" style="color:#FFD600">${v.pf}</div></div>
      </div>${v.equity&&v.equity.length>2?drawC(v.equity):""}`;
    }else document.getElementById("btm").innerHTML=`<div class="al er">✗ ${d.error}</div>`;
  }catch(e){document.getElementById("btm").innerHTML=`<div class="al er">✗ ${e.message}</div>`}
  btn.textContent="▶ KÒMANSE BACKTEST";btn.disabled=false;
}
function drawC(vals){
  const W=500,H=110,p=8;
  const mn=Math.min(...vals),mx=Math.max(...vals),rng=mx-mn||1;
  const pts=vals.map((v,i)=>`${p+(i/(vals.length-1))*(W-p*2)},${H-p-((v-mn)/rng)*(H-p*2)}`).join(" ");
  const col=vals[vals.length-1]>=vals[0]?"#00FF88":"#FF3B6B";
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:110px;margin-top:12px"><defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${col}" stop-opacity=".3"/><stop offset="100%" stop-color="${col}" stop-opacity="0"/></linearGradient></defs><polygon points="${p},${H} ${pts} ${W-p},${H}" fill="url(#cg)"/><polyline points="${pts}" fill="none" stroke="${col}" stroke-width="2.5"/></svg>`;
}
const SI={
  confluence:{l:"🔥 Confluence ELITE",d:"SuperTrend(2.5x)+HeikinAshi(2.5x)+Chandelier(2.5x)+11 strategies klasik. ADX≥12, 3 strat minimòm. VWAP bonus.",tags:["SuperTrend","HeikinAshi","Chandelier","VWAP","ADX≥12","3+ strat"]},
  deriv_pro:{l:"🚀 Deriv Pro ELITE",d:"Score 5.0/15 + ADX≥12 + SuperTrend bonus. Meye pou Deriv Synthetic.",tags:["score 5/15","ADX≥12","ST bonus"]},
  supertrend:{l:"📈 SuperTrend",d:"ATR × 3.0. Siyal klè BUY/SELL.",tags:["ATR×3","75-92%"]},
  heikin_ashi:{l:"🕯 Heikin Ashi",d:"5 bouji konsekitif menm direksyon.",tags:["5 bouji","72-83%"]},
  chandelier:{l:"🔔 Chandelier Exit",d:"Highest High - ATR×3 / Lowest Low + ATR×3.",tags:["HH-ATR×3","75-90%"]},
  ai:{l:"🤖 AI Score",d:"8 faktè: EMA+RSI+MACD+BB+momentum+vol+position+trend.",tags:["8 faktè","68-92%"]},
  smc:{l:"🏛 SMC",d:"Break of Structure + swing + EMA50.",tags:["BOS","swing","EMA50"]},
  scalping_pro:{l:"⚡ Scalping",d:"EMA 5/13 + RSI 9. Rapid pou 1m/5m.",tags:["EMA 5/13","RSI 9"]},
  ema:{l:"📊 EMA Classic",d:"Crossover EMA 9/21/50 + RSI filter.",tags:["EMA 9/21","RSI","76%"]},
  rsi:{l:"📉 RSI Classic",d:"RSI <30/>70 + EMA50.",tags:["RSI 14","OB 70","OS 30"]},
  macd_bollinger:{l:"📈 MACD+Bollinger",d:"MACD + Bollinger Bands confluence.",tags:["MACD","BB 20","72-78%"]},
  breakout:{l:"💥 Breakout",d:"20-period high/low breakout + RSI confirm.",tags:["HH/LL 20","RSI","80%"]},
  order_block:{l:"🧱 Order Block",d:"Institutional candle + retest + EMA21.",tags:["OB body 65%","EMA21","82%"]},
  stoch_ema:{l:"🔄 Stoch+EMA",d:"Stochastic K crossover + EMA50.",tags:["Stoch 14","EMA50","80%"]},
  fibonacci:{l:"🌀 Fibonacci",d:"618/500/382 retracement + RSI.",tags:["61.8%","50%","38.2%"]},
  fvg:{l:"📐 FVG",d:"Fair Value Gap — imbalance detection + EMA21.",tags:["FVG","EMA21","80%"]},
  binance_gold:{l:"🥇 Gold",d:"XAU/USD: EMA 20/50/200 + RSI+MACD+BB+Stoch+Volume. ADX≥20.",tags:["EMA 20/50/200","ADX≥20"]},
  binance_crypto:{l:"🪙 Crypto",d:"Binance: Trend+Volume+RSI+MACD+Breakout. ADX≥18.",tags:["EMA 9/21/50","Volume","ADX≥18"]},
};
let sel="confluence";
const sb=document.getElementById("sbts");
Object.keys(SI).forEach(k=>{
  const b=document.createElement("button");
  b.className="btn"+(k===sel?" b":"");b.style.cssText="padding:5px 12px;font-size:11px;margin-bottom:4px";
  b.textContent=SI[k].l;
  b.onclick=()=>{sel=k;renderS();sb.querySelectorAll("button").forEach(x=>x.style.borderColor="#0D2233");b.style.borderColor="#00FF88"};
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
function msg(id,txt,ok){
  const cls=ok===true?"ok":(ok===false?"er":"in");
  document.getElementById(id).innerHTML=`<div class="al ${cls}">${txt}</div>`;
}
function upd(d){
  const col=d.pnl>=0?"#00FF88":"#FF3B6B";const sign=d.pnl>=0?"+":"";
  const blab={"deriv":"DERIV","binance":"BINANCE","binance_us":"BINANCE US"}[d.broker]||(d.broker?d.broker.toUpperCase():"DISCONNECTED");
  document.getElementById("hbal").textContent="$"+d.balance.toFixed(2);
  document.getElementById("hbal").style.color=d.connected?"#00D4FF":"#3A6070";
  document.getElementById("hb").textContent=blab;
  document.getElementById("hb").style.color=d.connected?"#00FF88":"#3A6070";
  document.getElementById("dot").className="dot "+(d.running?"dl":"di");
  document.getElementById("hs").textContent=d.running?"LIVE":"IDLE";
  document.getElementById("hs").style.color=d.running?"#00FF88":"#3A6070";
  ["s-bal","c-bal"].forEach(id=>{const el=document.getElementById(id);if(el)el.textContent="$"+d.balance.toFixed(2)});
  ["s-pnl","s-pnl2","c-pnl"].forEach(id=>{const el=document.getElementById(id);if(el){el.textContent=sign+"$"+Math.abs(d.pnl).toFixed(2);el.style.color=col}});
  document.getElementById("s-sent").textContent="$"+d.profit_sent.toFixed(4);
  document.getElementById("c-sent").textContent="$"+d.profit_sent.toFixed(4);
  document.getElementById("s-tr").textContent=d.trades.length;
  ["s-bot","c-st2"].forEach(id=>{const el=document.getElementById(id);if(el){el.textContent=d.running?"LIVE 🟢":"IDLE";el.style.color=d.running?"#00FF88":"#3A6070"}});
  document.getElementById("s-strat").textContent=d.config.strategy||"—";
  document.getElementById("s-sym").textContent=d.config.symbol||"—";
  const br2=document.getElementById("s-br2");
  if(br2){br2.textContent=blab;br2.style.color=d.connected?"#00FF88":"#3A6070"}
  if(d.running){document.getElementById("bs").style.display="none";document.getElementById("bx").style.display="inline-block"}
  else{document.getElementById("bs").style.display="inline-block";document.getElementById("bx").style.display="none"}
  if(d.trades.length>1){
    let cum=0;const eq=d.trades.slice().reverse().map(t=>{cum+=t.pnl||0;return cum});
    const svg=document.getElementById("chart");
    const ch=drawC(eq);const tmp=document.createElement("div");tmp.innerHTML=ch;
    const ns=tmp.firstChild;while(svg.firstChild)svg.removeChild(svg.firstChild);while(ns&&ns.firstChild)svg.appendChild(ns.firstChild);
  }
  if(d.trades.length){
    document.getElementById("trtit").textContent=`HISTOIRIK TRADES (${d.trades.length})`;
    document.getElementById("trtbl").innerHTML=`<table><tr><th>#</th><th>Lè</th><th>Senbol</th><th>Side</th><th>Antre</th><th>Regime</th><th>Mise</th><th>Conf</th><th>P&L</th><th>Estati</th></tr>${d.trades.map(t=>`<tr><td style="color:#4A7080">${t.id}</td><td style="color:#4A7080">${t.time}</td><td style="font-weight:700">${t.symbol}</td><td><span class="tag ${t.side==="BUY"||t.side.includes("OVER")||t.side==="EVEN"?"tb":"ts"}">${t.side}</span></td><td>${t.entry}</td><td style="color:#4A7080;font-size:10px">${t.regime||"—"}</td><td style="color:#FFD600">$${t.stake||"—"}</td><td style="color:#FFD600">${t.conf}</td><td style="color:${t.pnl>=0?"#00FF88":"#FF3B6B"};font-weight:700">${t.pnl>=0?"+":""}${t.pnl.toFixed(2)}</td><td><span class="tag ${t.status==="won"?"tb":"ts"}">${t.status||"—"}</span></td></tr>`).join("")}</table>`;
  }
  if(d.log.length){document.getElementById("logs").innerHTML=d.log.map(l=>`<div class="le"><span class="lt">${l.time}</span><span class="l${l.level[0]}">${l.msg}</span></div>`).join("")}
}
async function poll(){try{const r=await fetch("/api/status");const d=await r.json();upd(d)}catch(e){}setTimeout(poll,3000)}

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
      document.getElementById("adm-codes-list").innerHTML=`<table><tr><th>KÒD</th><th>STATUS</th><th>RETE</th><th>TIP</th><th>AKSYON</th></tr>${d.codes.map(c=>`<tr><td style="font-weight:700">${c.code}</td><td><span class="tag" style="color:${sc[c.status]||"#4A7080"};border-color:${sc[c.status]||"#4A7080"}44">${c.status}</span></td><td style="color:#4A7080">${c.remaining}</td><td>${c.is_adm?"👑":"👤"}</td><td style="display:flex;gap:4px">${c.status!=="ADM"?`<button onclick="admReset('${c.code}')" style="background:transparent;border:1px solid #FFD60044;color:#FFD600;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">↺</button>`:""} ${c.code!=="BONHEURWIIN"?`<button onclick="admRevoke('${c.code}')" style="background:transparent;border:1px solid #FF3B6B44;color:#FF3B6B;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">✕</button>`:""}</td></tr>`).join("")}</table>`;
    }
  }catch(e){}
  try{
    const r2=await fetch("/api/admin/users",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});
    const d2=await r2.json();
    if(d2.ok){
      document.getElementById("adm-users-count").textContent=d2.total;
      document.getElementById("adm-users-list").innerHTML=d2.total===0?'<div style="color:#3A6070;text-align:center;padding:20px">Pa gen itilizatè</div>':`<table><tr><th>UID</th><th>BROKER</th><th>SENBOL</th><th>BOT</th><th>BALANS</th><th>P&L</th><th>TRADES</th><th>AKSYON</th></tr>${d2.users.map(u=>`<tr><td style="color:#4A7080;font-size:10px">${u.uid}</td><td>${u.broker||"—"}</td><td style="font-weight:700">${u.symbol||"—"}</td><td><span class="tag ${u.running?"tb":"tg"}">${u.running?"LIVE":"IDLE"}</span></td><td style="color:#00D4FF">$${u.balance}</td><td style="color:${u.pnl>=0?"#00FF88":"#FF3B6B"}">${u.pnl>=0?"+":""}$${u.pnl}</td><td>${u.trades}</td><td style="display:flex;gap:4px">${u.running?`<button onclick="admStopUser('${u.uid}')" style="background:transparent;border:1px solid #FF3B6B44;color:#FF3B6B;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">■</button>`:""}<button onclick="admClearUser('${u.uid}')" style="background:transparent;border:1px solid #4A708044;color:#4A7080;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">🗑</button></td></tr>`).join("")}</table>`;
    }
  }catch(e){}
  try{
    const r3=await fetch("/api/admin/sessions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});
    const d3=await r3.json();
    if(d3.ok){document.getElementById("adm-sessions-list").innerHTML=d3.sessions.length===0?'<div style="text-align:center;padding:10px">Pa gen sesyon</div>':d3.sessions.map(s=>`<div style="padding:5px 0;border-bottom:1px solid #0D2233;display:flex;justify-content:space-between"><span style="color:#4A7080">${s.token}</span><span style="color:${s.is_admin?"#00D4FF":"#4A7080"}">${s.is_admin?"👑":"👤"}</span><span style="color:${s.active?"#00FF88":"#FF3B6B"}">${s.days_left} jou</span></div>`).join("")}
  }catch(e){}
}
async function admAddCode(){
  const token=getStoredToken();const code=document.getElementById("new-code").value.trim().toUpperCase();
  if(!code){document.getElementById("add-code-msg").innerHTML='<div class="al er">Mete yon kòd</div>';return}
  const isAdm=document.getElementById("new-code-type").value==="adm";
  const r=await fetch("/api/admin/add_code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,code,is_adm:isAdm})});
  const d=await r.json();
  document.getElementById("add-code-msg").innerHTML=`<div class="al ${d.ok?"ok":"er"}">${d.ok?d.msg:d.error}</div>`;
  if(d.ok){document.getElementById("new-code").value="";admRefresh()}
}
async function admRevoke(code){if(!confirm(`Revoke ${code}?`))return;const token=getStoredToken();const r=await fetch("/api/admin/revoke_code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,code})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh()}
async function admReset(code){const token=getStoredToken();const r=await fetch("/api/admin/reset_code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,code})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh()}
async function admStopUser(uid){if(!confirm(`Kanpe bot ${uid}?`))return;const token=getStoredToken();const r=await fetch("/api/admin/stop_user",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,uid})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh()}
async function admCleanSessions(){const token=getStoredToken();const r=await fetch("/api/admin/clean_sessions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh()}
async function admClearUser(uid){if(!confirm(`Efase TOUT istorik ${uid}?`))return;const token=getStoredToken();const r=await fetch("/api/admin/clear_user",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,uid})});const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh()}
function genCode(len){const chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789";let r="";for(let i=0;i<len;i++){if(i>0&&i%4===0)r+="-";r+=chars[Math.floor(Math.random()*chars.length)]}document.getElementById("gen-result").textContent=r;document.getElementById("gen-copy-btn").style.display="inline-block";document.getElementById("new-code").value=r}
function admCopyGen(){admAddCode()}
checkLogin();
</script>
</body>
</html>"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"BonheurBot ELITE v6.1 — Strategies + PAT Fix — port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
