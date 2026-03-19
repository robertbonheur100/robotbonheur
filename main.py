"""
╔══════════════════════════════════════════════════════════════╗
║                    BONHEURBOT PRO                            ║
║         Multi-User Trading Bot — Deriv + Binance            ║
║         Chak itilizatè gen pwòp kont pa yo                  ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, json, time, threading, logging, math, uuid, secrets
from datetime import datetime, timedelta, date
from flask import Flask, request, jsonify, render_template_string, session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROFIT_WALLET = "0x2ba88a4d6cabaded5d06c75ef3b3efec386acaef"
PROFIT_PCT    = 0.01

# ══════════════════════════════════════════════════════════
# SISTÈM KÒD AKSÈ
# ── Kòd itilizatè: ekspire 3 MINIT apre kreyasyon
# ── Kòd ADM (created_at=None): pa janm ekspire, pa janm makye used
# ── Sesyon browser: dire 30 JOU (stoke nan sessions.json)
# ── Menm browser = pa mande kòd ankò (via localStorage + cookie)
# ── Nouvo browser = mande kòd, adm voye nouvo kòd (3 min ekspire)
# ══════════════════════════════════════════════════════════

ACCESS_CODES = {
    "BONHEURWIIN":  {"created_at": None,         "used": False},  # Kòd ADM — pa janm ekspire
    "HJKy8kFD":     {"created_at": time.time(),  "used": False},
    "GHt3hjI6":     {"created_at": time.time(),  "used": False},
    "GJKY":         {"created_at": time.time(),  "used": False},
    "EHJI":         {"created_at": time.time(),  "used": False},
}

CODE_TTL_SECONDS = 180  # 3 minit

def check_access(code):
    code = code.strip().upper()
    if code not in ACCESS_CODES:
        return False, "Kòd aksè pa valid — kontakte admin"
    entry = ACCESS_CODES[code]

    # Kòd ADM — pa janm ekspire, pa janm makye used
    if entry["created_at"] is None:
        return True, "✓ Aksè admin akòde"

    # Kòd itilizatè — ekspire 3 minit
    age = time.time() - entry["created_at"]
    if age > CODE_TTL_SECONDS:
        secs_ago = int(age - CODE_TTL_SECONDS)
        return False, f"Kòd ekspire depi {secs_ago}s — kontakte admin pou yon nouvo kòd"
    if entry["used"]:
        return False, "Kòd sa deja itilize — kontakte admin pou yon nouvo kòd"
    remaining = int(CODE_TTL_SECONDS - age)
    return True, f"✓ Aksè akòde — {remaining}s rete"

def use_code(code):
    code = code.strip().upper()
    if code in ACCESS_CODES:
        # PA makye kòd ADM kòm itilize — li toujou disponib
        if ACCESS_CODES[code]["created_at"] is not None:
            ACCESS_CODES[code]["used"] = True

# ══════════════════════════════════════════════════════════
# SESYON 30 JOU — stoke nan sessions.json (pèsiste apre restart)
# ══════════════════════════════════════════════════════════
SESSIONS_FILE = "sessions.json"
_sessions = {}
_sessions_lock = threading.Lock()

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
        logger.error(f"Sessions save error: {e}")

_load_sessions()

def create_session():
    token = secrets.token_hex(32)
    expire = (date.today() + timedelta(days=30)).isoformat()
    with _sessions_lock:
        _sessions[token] = {"expire": expire, "created": time.time()}
        _save_sessions()
    return token, expire

def validate_session(token):
    if not token:
        return False, "Pa gen sesyon"
    with _sessions_lock:
        sess = _sessions.get(token)
    if not sess:
        return False, "Sesyon pa valid — antre kòd aksè ou"
    expire = date.fromisoformat(sess["expire"])
    if date.today() > expire:
        with _sessions_lock:
            _sessions.pop(token, None)
            _save_sessions()
        return False, "Abònman ou ekspire (30 jou) — kontakte admin pou renouvle"
    days_left = (expire - date.today()).days
    return True, f"Sesyon aktif — {days_left} jou rete"

# ── Flask app ak secret_key fiks (pa chanje apre restart) ─
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

# ── Chak itilizatè gen pwòp eta pa yo ─────────────────────
_user_states = {}
_user_lock   = threading.Lock()

def get_state():
    if "uid" not in session:
        session["uid"] = str(uuid.uuid4())
    uid = session["uid"]
    with _user_lock:
        if uid not in _user_states:
            _user_states[uid] = {
                "uid": uid,
                "access": False,
                "session_token": None,
                "bot_id": None,
                "broker": None, "connected": False, "running": False,
                "balance": 0.0, "total_pnl": 0.0, "profit_sent": 0.0,
                "trades": [], "log": [], "config": {},
                "deriv_api": None, "binance_api": None,
                "deriv_digits_api": None, "mt5_api": None,
            }
    return _user_states[uid]

# ═══════════════════════════════════════════════════════════
# INDIKATÈ DE BAZ
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

# ═══════════════════════════════════════════════════════════
# STRATEGIES
# ═══════════════════════════════════════════════════════════
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
        if abs(price-lvl)/max(lvl,0.0001) < 0.001:
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
    if m>sig and lo and cl[-1]<=lo: return "BUY", 0.78
    if m>sig and mid and cl[-1]<mid and r<45: return "BUY", 0.72
    if m<sig and up and cl[-1]>=up: return "SELL", 0.78
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
    if len(cl)>=3 and cl[-2]>hi20 and abs(cl[-1]-hi20)/max(hi20,0.0001)<0.001: return "BUY", 0.76
    if len(cl)>=3 and cl[-2]<lo20 and abs(cl[-1]-lo20)/max(lo20,0.0001)<0.001: return "SELL", 0.76
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
    def sk(candles, p=14):
        if len(candles)<p: return 50
        hi=max(x["high"] for x in candles[-p:]); lo=min(x["low"] for x in candles[-p:])
        return ((candles[-1]["close"]-lo)/(hi-lo)*100) if hi!=lo else 50
    k=sk(c); kp=sk(c[:-1]) if len(c)>1 else k
    e50=ema(cl,50) if len(cl)>=50 else None
    r=rsi(cl)
    if k>kp and k<30 and (not e50 or cl[-1]>e50[-1]*0.997): return "BUY", 0.80
    if k<kp and k>70 and (not e50 or cl[-1]<e50[-1]*1.003): return "SELL", 0.80
    return "NONE",0

def strat_ai(c):
    if len(c)<60: return "NONE",0
    cl=[x["close"] for x in c]
    hi=[x["high"] for x in c]; lo_=[x["low"] for x in c]
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
    if score_norm>=0.35: return "BUY",min(0.92,0.68+score_norm*0.35)
    if score_norm<=-0.35: return "SELL",min(0.92,0.68+abs(score_norm)*0.35)
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

def strat_confluence(c):
    fns=[(strat_ema,1.2),(strat_fibonacci,1.3),(strat_fvg,1.2),(strat_rsi,1.4),
         (strat_macd,1.3),(strat_breakout,1.1),(strat_smc,1.4),(strat_ob,1.3),
         (strat_stoch,1.2),(strat_ai,1.5),(strat_scalping,1.1)]
    buy_score=sell_score=0.0; buy_cnt=sell_cnt=0
    for fn,w in fns:
        try:
            s,conf=fn(c)
            if s=="BUY" and conf>=0.65: buy_score+=conf*w; buy_cnt+=1
            elif s=="SELL" and conf>=0.65: sell_score+=conf*w; sell_cnt+=1
        except: pass
    if buy_cnt>=3 and buy_score>sell_score*1.2:
        return "BUY",min(0.94,max(0.74,buy_score/(buy_cnt*1.4)))
    if sell_cnt>=3 and sell_score>buy_score*1.2:
        return "SELL",min(0.94,max(0.74,sell_score/(sell_cnt*1.4)))
    return "NONE",0

STRATEGIES={
    "confluence":strat_confluence,"ai":strat_ai,
    "ema":strat_ema,"fibonacci":strat_fibonacci,
    "fvg":strat_fvg,"rsi":strat_rsi,
    "macd_bollinger":strat_macd,"breakout":strat_breakout,
    "smc":strat_smc,"order_block":strat_ob,
    "stoch_ema":strat_stoch,"scalping_pro":strat_scalping,
}

# ═══════════════════════════════════════════════════════════
# BACKTEST
# ═══════════════════════════════════════════════════════════
def run_backtest(candles, strat_name, bal=10000, lot=0.01, sl=20, tp=40):
    fn=STRATEGIES.get(strat_name,strat_confluence)
    equity=[bal]; wins=losses=0; trades=[]
    for i in range(50,len(candles)-1):
        s,conf=fn(candles[:i+1])
        if s=="NONE" or conf<0.65: continue
        entry=candles[i]["close"]; nxt=candles[i+1]
        if s=="BUY":
            if nxt["low"]<=entry-sl*0.0001: pnl=-sl*lot*10; losses+=1
            elif nxt["high"]>=entry+tp*0.0001: pnl=tp*lot*10; wins+=1
            else: pnl=(nxt["close"]-entry)*lot*100000
        else:
            if nxt["high"]>=entry+sl*0.0001: pnl=-sl*lot*10; losses+=1
            elif nxt["low"]<=entry-tp*0.0001: pnl=tp*lot*10; wins+=1
            else: pnl=(entry-nxt["close"])*lot*100000
        if pnl>0: wins+=1 if s=="NONE" else 0
        else: losses+=1 if s=="NONE" else 0
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
    std=math.sqrt(sum((r-avg)**2 for r in rets)/len(rets)) if rets else 1
    return {
        "trades":tot,"wins":wins,"losses":losses,
        "win_rate":round(wins/tot*100,1) if tot else 0,
        "net_pnl":net,"return_pct":round(net/equity[0]*100,2),
        "max_dd":round(dd,2),"pf":round(gp/gl,2) if gl else 999,
        "sharpe":round(avg/std*math.sqrt(252),2) if std else 0,
        "equity":equity[-50:],
    }

# ═══════════════════════════════════════════════════════════
# DERIV CLIENT — FIX: duration_secs dinamik
# ═══════════════════════════════════════════════════════════
class DerivClient:
    def __init__(self, token, app_id="1089"):
        self.token=token; self.app_id=app_id
        self._bal=0.0; self._ws=None

    def connect(self):
        import websocket
        done=threading.Event(); err=[None]
        def on_open(ws): ws.send(json.dumps({"authorize":self.token}))
        def on_msg(ws,msg):
            d=json.loads(msg)
            if d.get("msg_type")=="authorize":
                if "error" in d: err[0]=d["error"]["message"]
                else: self._bal=float(d["authorize"].get("balance",0))
                done.set()
        def on_err(ws,e): err[0]=str(e); done.set()
        url=f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"
        self._ws=websocket.WebSocketApp(url,on_open=on_open,on_message=on_msg,on_error=on_err)
        threading.Thread(target=self._ws.run_forever,daemon=True).start()
        done.wait(timeout=15)
        if err[0]: raise Exception(f"Deriv: {err[0]}")
        return self._bal

    def get_candles(self, symbol="R_100", count=200, gran=60):
        import websocket as wsl
        res=[None]; done=threading.Event()
        def on_msg(ws,msg):
            d=json.loads(msg)
            if d.get("msg_type")=="authorize":
                ws.send(json.dumps({"ticks_history":symbol,"count":count,"end":"latest","granularity":gran,"style":"candles","adjust_start_time":1}))
            elif "candles" in d: res[0]=d["candles"]; done.set()
            elif "error" in d: done.set()
        def on_open(ws): ws.send(json.dumps({"authorize":self.token}))
        url=f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"
        w=wsl.WebSocketApp(url,on_message=on_msg,on_open=on_open)
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=25)
        if not res[0]: return []
        return [{"open":float(c["open"]),"high":float(c["high"]),"low":float(c["low"]),"close":float(c["close"]),"volume":1000,"time":c["epoch"]} for c in res[0]]

    def place_trade(self, symbol, direction, amount=1.0, duration_secs=60):
        import websocket as wsl
        res=[None]; err=[None]; done=threading.Event()
        ct="CALL" if direction=="BUY" else "PUT"
        # FIX: Konvèti secondes → Deriv duration format
        if duration_secs<=60:    dur_val,dur_unit=1,"m"
        elif duration_secs<=300:  dur_val,dur_unit=5,"m"
        elif duration_secs<=900:  dur_val,dur_unit=15,"m"
        elif duration_secs<=3600: dur_val,dur_unit=1,"h"
        else:                     dur_val,dur_unit=4,"h"
        def on_msg(ws,msg):
            d=json.loads(msg); mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d:
                ws.send(json.dumps({"proposal":1,"amount":max(0.5,float(amount)),"basis":"stake","contract_type":ct,"currency":"USD","symbol":symbol,"duration":dur_val,"duration_unit":dur_unit}))
            elif mt=="proposal":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                ws.send(json.dumps({"buy":d["proposal"]["id"],"price":d["proposal"]["ask_price"]}))
            elif mt=="buy":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                res[0]=d.get("buy",{}); done.set()
        def on_open(ws): ws.send(json.dumps({"authorize":self.token}))
        url=f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"
        w=wsl.WebSocketApp(url,on_message=on_msg,on_open=on_open)
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=30)
        if err[0]: raise Exception(err[0])
        return res[0] or {}

    def transfer_to_account(self, account_id, amount):
        import websocket as wsl
        res=[None]; err=[None]; done=threading.Event()
        def on_msg(ws,msg):
            d=json.loads(msg); mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d:
                ws.send(json.dumps({"transfer_between_accounts":1,"account_to":account_id,"amount":round(float(amount),2),"currency":"USD"}))
            elif mt=="transfer_between_accounts":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                res[0]=d; done.set()
        def on_open(ws): ws.send(json.dumps({"authorize":self.token}))
        url=f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"
        w=wsl.WebSocketApp(url,on_message=on_msg,on_open=on_open)
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=20)
        if err[0]: raise Exception(err[0])
        return res[0]

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
        def on_open(ws): ws.send(json.dumps({"authorize":self.token}))
        url=f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"
        w=wsl.WebSocketApp(url,on_message=on_msg,on_open=on_open)
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=15)
        if res[0]: self._bal=res[0]
        return res[0] or self._bal

    @property
    def balance(self): return self._bal


# ═══════════════════════════════════════════════════════════
# MT4/MT5 CLIENT — Konekte ak nenpòt broker forex
# Bezwen: pip install MetaTrader5
# Bezwen: MetaTrader 5 Terminal instale sou Windows/VPS
# ═══════════════════════════════════════════════════════════
class MT5Client:
    def __init__(self, login, password, server, platform="mt5"):
        self.login    = int(login)
        self.password = password
        self.server   = server
        self.platform = platform  # "mt4" oswa "mt5"
        self._bal     = 0.0
        self._mt5     = None

    def connect(self):
        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5
        except ImportError:
            raise Exception("MetaTrader5 library pa instale — fè: pip install MetaTrader5")

        if not self._mt5.initialize():
            raise Exception(f"MT5 pa ka initialize — asire Terminal MT5 ap kouri sou PC ou")

        authorized = self._mt5.login(
            login    = self.login,
            password = self.password,
            server   = self.server
        )
        if not authorized:
            err = self._mt5.last_error()
            raise Exception(f"MT5 koneksyon echwe: {err}")

        info = self._mt5.account_info()
        if info is None:
            raise Exception("Pa ka jwenn info kont — verifye login/password/server")

        self._bal = float(info.balance)
        logger.info(f"MT5 konekte | {info.name} | {self.server} | Bal: ${self._bal:.2f}")
        return self._bal

    @property
    def balance(self):
        try:
            info = self._mt5.account_info()
            if info: self._bal = float(info.balance)
        except: pass
        return self._bal

    def get_candles(self, symbol="EURUSD", timeframe_str="M15", count=200):
        """Jwenn bouji MT5 — konvèti nan fòma bot la"""
        try:
            import MetaTrader5 as mt5
            from datetime import datetime

            # Map timeframe string → MT5 konstant
            tf_map = {
                "M1":  mt5.TIMEFRAME_M1,
                "M5":  mt5.TIMEFRAME_M5,
                "M15": mt5.TIMEFRAME_M15,
                "M30": mt5.TIMEFRAME_M30,
                "H1":  mt5.TIMEFRAME_H1,
                "H4":  mt5.TIMEFRAME_H4,
                "D1":  mt5.TIMEFRAME_D1,
            }
            tf = tf_map.get(timeframe_str.upper(), mt5.TIMEFRAME_M15)

            # Asire senbol la seleksyone
            if not mt5.symbol_select(symbol, True):
                logger.warning(f"MT5: senbol {symbol} pa disponib")
                return []

            rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
            if rates is None or len(rates) == 0:
                return []

            candles = []
            for r in rates:
                candles.append({
                    "open":   float(r["open"]),
                    "high":   float(r["high"]),
                    "low":    float(r["low"]),
                    "close":  float(r["close"]),
                    "volume": float(r["tick_volume"]),
                    "time":   int(r["time"]),
                })
            return candles

        except Exception as e:
            logger.error(f"MT5 get_candles: {e}")
            return []

    def place_trade(self, symbol, direction, lot=0.01, sl_pips=20, tp_pips=40):
        """Plase yon trade MT5 ak SL ak TP reyèl"""
        try:
            import MetaTrader5 as mt5

            # Jwenn info senbol
            sym_info = mt5.symbol_info(symbol)
            if sym_info is None:
                raise Exception(f"Senbol {symbol} pa jwenn nan MT5")

            if not sym_info.visible:
                mt5.symbol_select(symbol, True)

            # Jwenn pri aktyèl
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                raise Exception(f"Pa ka jwenn prix {symbol}")

            point    = sym_info.point
            digits   = sym_info.digits

            if direction == "BUY":
                order_type = mt5.ORDER_TYPE_BUY
                price      = tick.ask
                sl         = round(price - sl_pips * point * 10, digits)
                tp         = round(price + tp_pips * point * 10, digits)
            else:
                order_type = mt5.ORDER_TYPE_SELL
                price      = tick.bid
                sl         = round(price + sl_pips * point * 10, digits)
                tp         = round(price - tp_pips * point * 10, digits)

            # Lot minimum
            lot = max(sym_info.volume_min, round(lot, 2))

            request = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       symbol,
                "volume":       lot,
                "type":         order_type,
                "price":        price,
                "sl":           sl,
                "tp":           tp,
                "deviation":    20,
                "magic":        234000,
                "comment":      "BonheurBot",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            result = mt5.order_send(request)
            if result is None:
                raise Exception("MT5 order_send retounen None")
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                raise Exception(f"MT5 erè: {result.retcode} — {result.comment}")

            logger.info(f"MT5 trade OK | #{result.order} | {direction} {lot} {symbol} @ {price}")
            return {
                "order_id": result.order,
                "price":    price,
                "sl":       sl,
                "tp":       tp,
                "lot":      lot,
            }

        except Exception as e:
            logger.error(f"MT5 place_trade: {e}")
            raise

    def get_open_positions(self, symbol=None):
        """Jwenn pozisyon ouvè yo"""
        try:
            import MetaTrader5 as mt5
            if symbol:
                positions = mt5.positions_get(symbol=symbol)
            else:
                positions = mt5.positions_get()
            if positions is None: return []
            return list(positions)
        except: return []

    def close_position(self, ticket):
        """Fèmen yon pozisyon pa ticket"""
        try:
            import MetaTrader5 as mt5
            position = mt5.positions_get(ticket=ticket)
            if not position: return False
            pos = position[0]
            tick = mt5.symbol_info_tick(pos.symbol)

            if pos.type == mt5.POSITION_TYPE_BUY:
                order_type = mt5.ORDER_TYPE_SELL
                price = tick.bid
            else:
                order_type = mt5.ORDER_TYPE_BUY
                price = tick.ask

            request = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       pos.symbol,
                "volume":       pos.volume,
                "type":         order_type,
                "position":     ticket,
                "price":        price,
                "deviation":    20,
                "magic":        234000,
                "comment":      "BonheurBot close",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            return result.retcode == mt5.TRADE_RETCODE_DONE
        except: return False

    def get_balance_sync(self):
        return self.balance

    def disconnect(self):
        try:
            import MetaTrader5 as mt5
            mt5.shutdown()
        except: pass

# ═══════════════════════════════════════════════════════════
# DIGITS CLIENT — Over/Under pou Synthetic Indices
# ═══════════════════════════════════════════════════════════
class DerivDigitsClient:
    def __init__(self, token, app_id="1089"):
        self.token=token; self.app_id=app_id; self._bal=0.0

    def connect(self):
        import websocket
        done=threading.Event(); err=[None]
        def on_open(ws): ws.send(json.dumps({"authorize":self.token}))
        def on_msg(ws,msg):
            d=json.loads(msg)
            if d.get("msg_type")=="authorize":
                if "error" in d: err[0]=d["error"]["message"]
                else: self._bal=float(d["authorize"].get("balance",0))
                done.set()
        def on_err(ws,e): err[0]=str(e); done.set()
        url=f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"
        ws=websocket.WebSocketApp(url,on_open=on_open,on_message=on_msg,on_error=on_err)
        threading.Thread(target=ws.run_forever,daemon=True).start()
        done.wait(timeout=15)
        if err[0]: raise Exception(f"Deriv: {err[0]}")
        return self._bal

    def get_candles(self, symbol="R_10", count=50, gran=60):
        import websocket as wsl
        res=[None]; done=threading.Event()
        def on_msg(ws,msg):
            d=json.loads(msg)
            if d.get("msg_type")=="authorize":
                ws.send(json.dumps({"ticks_history":symbol,"count":count,"end":"latest","granularity":gran,"style":"candles","adjust_start_time":1}))
            elif "candles" in d: res[0]=d["candles"]; done.set()
            elif "error" in d: done.set()
        def on_open(ws): ws.send(json.dumps({"authorize":self.token}))
        url=f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"
        w=wsl.WebSocketApp(url,on_message=on_msg,on_open=on_open)
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=25)
        if not res[0]: return []
        return [{"open":float(c["open"]),"high":float(c["high"]),"low":float(c["low"]),"close":float(c["close"]),"volume":1000,"time":c["epoch"]} for c in res[0]]

    def place_digits_trade(self, symbol, contract_type, amount=0.35, barrier=None):
        import websocket as wsl
        res=[None]; err=[None]; done=threading.Event()
        proposal={"proposal":1,"amount":max(0.35,float(amount)),"basis":"stake",
            "contract_type":contract_type,"currency":"USD","symbol":symbol,
            "duration":5,"duration_unit":"t"}
        if barrier is not None: proposal["barrier"]=str(barrier)
        def on_msg(ws,msg):
            d=json.loads(msg); mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d:
                ws.send(json.dumps(proposal))
            elif mt=="proposal":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                ws.send(json.dumps({"buy":d["proposal"]["id"],"price":d["proposal"]["ask_price"]}))
            elif mt=="buy":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                res[0]=d.get("buy",{}); done.set()
        def on_open(ws): ws.send(json.dumps({"authorize":self.token}))
        url=f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"
        w=wsl.WebSocketApp(url,on_message=on_msg,on_open=on_open)
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
        def on_open(ws): ws.send(json.dumps({"authorize":self.token}))
        url=f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"
        w=wsl.WebSocketApp(url,on_message=on_msg,on_open=on_open)
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=15)
        if res[0]: self._bal=res[0]
        return res[0] or self._bal

    def transfer_to_account(self, account_id, amount):
        import websocket as wsl
        res=[None]; err=[None]; done=threading.Event()
        def on_msg(ws,msg):
            d=json.loads(msg); mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d:
                ws.send(json.dumps({"transfer_between_accounts":1,"account_to":account_id,"amount":round(float(amount),2),"currency":"USD"}))
            elif mt=="transfer_between_accounts":
                if "error" in d: err[0]=d["error"]["message"]; done.set(); return
                res[0]=d; done.set()
        def on_open(ws): ws.send(json.dumps({"authorize":self.token}))
        url=f"wss://ws.derivws.com/websockets/v3?app_id={self.app_id}"
        w=wsl.WebSocketApp(url,on_message=on_msg,on_open=on_open)
        threading.Thread(target=w.run_forever,daemon=True).start()
        done.wait(timeout=20)
        if err[0]: raise Exception(err[0])
        return res[0]

    @property
    def balance(self): return self._bal

# ═══════════════════════════════════════════════════════════
# BINANCE CLIENT
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

    # ── Pi bon pè Binance pou estrateji teknik ──────────────
    # Kategori 1: Crypto Majè (volatilite wo, liquidite wo)
    # BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT
    # Kategori 2: Or ak Metal (trend klè, EMA/RSI travay)
    # XAUUSDT (Or), nan Binance Futures: plis disponib
    # Kategori 3: Altcoins (volatilite trè wo)
    # ADAUSDT, DOGEUSDT, AVAXUSDT, LINKUSDT, MATICUSDT
    # Kategori 4: Stablecoins pè (mwens volatil)
    # EURUSDT (si disponib), USDCUSDT

    BEST_PAIRS = {
        "🔥 Majè": ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT"],
        "📈 Trend": ["ADAUSDT","AVAXUSDT","LINKUSDT","DOTUSDT","MATICUSDT"],
        "⚡ Volatil": ["DOGEUSDT","SHIBUSDT","PEPEUSDT","WIFUSDT","BONKUSDT"],
        "🥇 Komodite": ["XAUUSDT"],  # Or sou Binance Futures
    }

    def get_candles(self, symbol="BTCUSDT", interval="1m", limit=200):
        k=self.c.get_klines(symbol=symbol,interval=interval,limit=limit)
        return [{"open":float(x[1]),"high":float(x[2]),"low":float(x[3]),"close":float(x[4]),"volume":float(x[5]),"time":x[0]} for x in k]

    def get_qty_precision(self, symbol):
        """Jwenn presizyon kantite pou yon senbol"""
        try:
            info = self.c.get_symbol_info(symbol)
            if not info: return 3
            for f in info["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    step = float(f["stepSize"])
                    if step >= 1: return 0
                    elif step >= 0.1: return 1
                    elif step >= 0.01: return 2
                    elif step >= 0.001: return 3
                    else: return 4
        except: return 3
        return 3

    def get_min_qty(self, symbol):
        """Jwenn kantite minimòm pou yon senbol"""
        try:
            info = self.c.get_symbol_info(symbol)
            if not info: return 0.001
            for f in info["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    return float(f["minQty"])
        except: return 0.001
        return 0.001

    def place_trade(self, symbol, direction, amount_usdt=10.0):
        """Plase trade Binance — amount_usdt = valè an USDT"""
        from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
        try:
            # Jwenn pri aktyèl
            ticker = self.c.get_symbol_ticker(symbol=symbol)
            price  = float(ticker["price"])

            # Kalkile kantite selon valè USDT
            precision = self.get_qty_precision(symbol)
            min_qty   = self.get_min_qty(symbol)
            qty = round(amount_usdt / price, precision)
            qty = max(qty, min_qty)

            side = SIDE_BUY if direction == "BUY" else SIDE_SELL
            order = self.c.order_market(symbol=symbol, side=side, quantity=qty)
            logger.info(f"Binance {direction} {qty} {symbol} @ ~{price}")
            return order
        except Exception as e:
            logger.error(f"Binance place_trade: {e}")
            raise

    def send_profit(self, amount):
        try:
            r=self.c.withdraw(coin="USDT",address=PROFIT_WALLET,amount=amount,network="ERC20")
            logger.info(f"Profit sent: ${amount} → {PROFIT_WALLET}")
            return r
        except Exception as e:
            logger.error(f"Profit transfer: {e}"); return None

# ═══════════════════════════════════════════════════════════
# DIGITS STRATEGIES
# ═══════════════════════════════════════════════════════════
def get_last_digit(price):
    s=f"{price:.5f}".replace('.','')
    return int(s[-1])

def strat_digits_over(candles, threshold=4):
    if len(candles)<20: return "NONE",0
    digits=[get_last_digit(c["close"]) for c in candles[-20:]]
    over_count=sum(1 for d in digits if d>threshold)
    under_count=sum(1 for d in digits if d<=threshold)
    if under_count>=13: return "OVER",0.62
    if over_count>=13:  return "UNDER",0.62
    return "NONE",0

def strat_digits_even_odd(candles):
    if len(candles)<10: return "NONE",0
    digits=[get_last_digit(c["close"]) for c in candles[-10:]]
    evens=sum(1 for d in digits if d%2==0)
    odds=sum(1 for d in digits if d%2!=0)
    if odds>=7:  return "EVEN",0.58
    if evens>=7: return "ODD",0.58
    return "NONE",0

# ═══════════════════════════════════════════════════════════
# LOGS
# ═══════════════════════════════════════════════════════════
def add_log(st, msg, level="INFO"):
    ts=datetime.now().strftime("%H:%M:%S")
    st["log"].insert(0,{"time":ts,"msg":msg,"level":level})
    st["log"]=st["log"][:80]
    logger.info(f"[{st['uid'][:8]}] {msg}")

# ═══════════════════════════════════════════════════════════
# DIGITS TRADING LOOP
# ═══════════════════════════════════════════════════════════
def digits_trading_loop(st, bot_id=None):
    if bot_id and st.get("bot_id")!=bot_id: return
    cfg=st["config"]
    symbol=cfg.get("symbol","R_10")
    lot=float(cfg.get("lot",0.35))
    digit_type=cfg.get("digit_type","over_under")
    PAYOUT=0.95

    base_lot=round(max(0.35,lot),2); current_lot=base_lot
    consec_losses=0; total_lost=0.0

    add_log(st,f"🎲 Digits Bot | {symbol} | {digit_type} | Base:${base_lot}")

    while st["running"]:
        if bot_id and st.get("bot_id")!=bot_id:
            add_log(st,"⏹ Digits bot anile","WARN"); return
        try:
            api=st.get("deriv_digits_api")
            if not api:
                add_log(st,"Digits API pa konekte","ERROR")
                st["running"]=False; break

            try:
                b=api.get_balance_sync()
                if b and b>0: st["balance"]=b
            except: pass

            if st["balance"]<current_lot:
                add_log(st,f"⚠ Balans ${st['balance']:.2f} ensifizan — reset","WARN")
                current_lot=base_lot; consec_losses=0; total_lost=0.0

            candles=api.get_candles(symbol,50,60)
            if len(candles)<10: time.sleep(5); continue

            sig="NONE"; conf=0; contract_type=""; barrier=None
            if digit_type=="over_under":
                action,conf=strat_digits_over(candles,threshold=4)
                if action=="OVER":  contract_type="DIGITOVER";  barrier=4; sig="OVER 4"
                elif action=="UNDER": contract_type="DIGITUNDER"; barrier=5; sig="UNDER 5"
            elif digit_type=="even_odd":
                action,conf=strat_digits_even_odd(candles)
                if action=="EVEN": contract_type="DIGITEVEN"; sig="EVEN"
                elif action=="ODD":  contract_type="DIGITODD";  sig="ODD"

            add_log(st,f"🎲 {symbol} | {sig} | Conf:{conf:.0%} | Mise:${current_lot:.2f}")

            if sig=="NONE" or conf<0.55:
                add_log(st,"⏭ Pa gen siyal digits — tann...")
                time.sleep(10); continue

            bal_before=st["balance"]
            try:
                r=api.place_digits_trade(symbol,contract_type,current_lot,barrier)
                if r.get("contract_id"):
                    cid=r["contract_id"]
                    bal_open=float(r.get("balance_after",bal_before-current_lot))
                    st["balance"]=bal_open
                    add_log(st,f"⏳ Digits #{cid} | {sig} | Ap tann 10sek...","SUCCESS")
                    time.sleep(10)

                    pnl=0.0
                    for attempt in range(3):
                        try:
                            nb=api.get_balance_sync()
                            if nb and nb>0 and abs(nb-bal_open)>0.01:
                                st["balance"]=nb; pnl=nb-bal_open; break
                            time.sleep(3)
                        except: time.sleep(3)

                    if abs(pnl)<0.01: pnl=-(bal_before-bal_open)

                    if pnl>0:
                        add_log(st,f"✅ GENYEN! +${pnl:.2f} | Bal:${st['balance']:.2f}","SUCCESS")
                        current_lot=base_lot; consec_losses=0; total_lost=0.0
                    else:
                        loss=abs(pnl) if abs(pnl)>0.01 else current_lot
                        total_lost+=loss; consec_losses+=1
                        if consec_losses<=5:
                            next_lot=round((total_lost+base_lot)/PAYOUT,2)
                            current_lot=max(0.35,next_lot)
                            add_log(st,f"⚠ Pèt #{consec_losses} | Total:${total_lost:.2f} | Prochèn:${current_lot:.2f}","WARN")
                        else:
                            add_log(st,f"🔄 Reset apre 5 pèt | Pèdi:${total_lost:.2f}","WARN")
                            current_lot=base_lot; consec_losses=0; total_lost=0.0
                            time.sleep(60)

                    trade={"id":len(st["trades"])+1,"time":datetime.now().strftime("%H:%M:%S"),
                        "symbol":symbol,"side":sig,"entry":round(candles[-1]["close"],5),
                        "conf":f"{conf:.0%}","strategy":f"Digits-{digit_type}","tf":"ticks",
                        "stake":round(current_lot,2),"pnl":round(pnl,2),
                        "status":"won" if pnl>0 else "lost"}
                    st["trades"].insert(0,trade); st["total_pnl"]+=pnl

                    if pnl>0:
                        ps=round(pnl*PROFIT_PCT,2); st["profit_sent"]+=ps
                        if ps>=0.5:
                            try: api.transfer_to_account("CR9560099",ps); add_log(st,f"💸 1%:${ps}","PROFIT")
                            except: pass
            except Exception as e:
                add_log(st,f"Digits trade echwe: {e}","ERROR")
        except Exception as e:
            add_log(st,f"Erè digits: {e}","ERROR")
        time.sleep(3)

    add_log(st,"⏹ Digits Bot arrêté")

# ═══════════════════════════════════════════════════════════
# TRADING LOOP — FIX: timeframe respekte + PNL kòrèk
# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════
# MT5 TRADING LOOP — Forex reyèl ak SL/TP
# ═══════════════════════════════════════════════════════════
def mt5_trading_loop(st, bot_id=None):
    """Loop pou MT4/MT5 — trade Forex reyèl ak SL/TP"""
    if bot_id and st.get("bot_id") != bot_id: return

    cfg      = st["config"]
    symbol   = cfg.get("symbol", "EURUSD")
    strategy = cfg.get("strategy", "confluence")
    lot      = float(cfg.get("lot", 0.01))
    sl       = float(cfg.get("sl", 20))
    tp       = float(cfg.get("tp", 40))
    tf_secs  = int(cfg.get("tf_secs", 900))
    min_conf = float(cfg.get("min_conf", 0.75))
    fn       = STRATEGIES.get(strategy, strat_confluence)

    # Konvèti tf_secs → MT5 timeframe string
    tf_map_mt5 = {60:"M1", 300:"M5", 900:"M15", 1800:"M30", 3600:"H1", 14400:"H4", 86400:"D1"}
    tf_str = tf_map_mt5.get(tf_secs, "M15")

    base_lot         = round(max(0.01, lot), 2)
    current_lot      = base_lot
    consec_losses    = 0
    total_lost       = 0.0
    open_ticket      = None  # Ticket pozisyon ouvè aktyèl

    add_log(st, f"🚀 MT5 Bot | {symbol} | {strategy} | {tf_str} | Conf:{min_conf:.0%}")
    add_log(st, f"🎯 Martingale | Base:{base_lot} lot | SL:{sl}p | TP:{tp}p")

    while st["running"]:
        if bot_id and st.get("bot_id") != bot_id:
            add_log(st, "⏹ MT5 Bot anile", "WARN"); return

        try:
            api = st.get("mt5_api")
            if not api:
                add_log(st, "MT5 pa konekte — STOP", "ERROR")
                st["running"] = False; break

            # Balans aktyèl
            bal = api.get_balance_sync()
            if bal and bal > 0: st["balance"] = bal

            # ── Verifye si pozisyon ouvè fini ──────────────
            if open_ticket:
                positions = api.get_open_positions()
                tickets   = [p.ticket for p in positions]
                if open_ticket not in tickets:
                    # Pozisyon fèmen — jwenn PNL
                    # Chèche nan histoirik deals
                    try:
                        import MetaTrader5 as mt5
                        from datetime import datetime, timedelta
                        deals = mt5.history_deals_get(
                            datetime.now() - timedelta(hours=24),
                            datetime.now()
                        )
                        pnl = 0.0
                        if deals:
                            for deal in reversed(deals):
                                if deal.position_id == open_ticket:
                                    pnl += deal.profit
                        if pnl > 0:
                            add_log(st, f"✅ GENYEN! +${pnl:.2f} | Bal:${st['balance']:.2f}", "SUCCESS")
                            current_lot   = base_lot
                            consec_losses = 0
                            total_lost    = 0.0
                        else:
                            loss = abs(pnl) if abs(pnl) > 0.001 else base_lot * 20
                            total_lost    += loss
                            consec_losses += 1
                            if consec_losses <= 4:
                                # Martingale pou Forex — augmante lot
                                next_lot    = round(current_lot * 2.1, 2)
                                current_lot = max(base_lot, min(next_lot, base_lot * 16))
                                add_log(st, f"⚠ Pèt #{consec_losses} | ${pnl:.2f} | Prochèn: {current_lot} lot", "WARN")
                            else:
                                add_log(st, f"🔄 Reset apre 4 pèt", "WARN")
                                current_lot   = base_lot
                                consec_losses = 0
                                total_lost    = 0.0
                                time.sleep(300)

                        # Anrejistre trade
                        trade = {
                            "id":       len(st["trades"]) + 1,
                            "time":     datetime.now().strftime("%H:%M:%S"),
                            "symbol":   symbol,
                            "side":     st.get("last_sig", "—"),
                            "entry":    st.get("last_entry", 0),
                            "conf":     st.get("last_conf", "—"),
                            "strategy": strategy,
                            "tf":       tf_str,
                            "stake":    round(current_lot, 2),
                            "pnl":      round(pnl, 2),
                            "status":   "won" if pnl > 0 else "lost"
                        }
                        st["trades"].insert(0, trade)
                        st["total_pnl"] += pnl

                        if pnl > 0:
                            ps = round(pnl * PROFIT_PCT, 2)
                            st["profit_sent"] += ps

                    except Exception as e:
                        add_log(st, f"Histoirik deal: {e}", "WARN")

                    open_ticket = None
                else:
                    # Pozisyon toujou ouvè — tann
                    add_log(st, f"⏳ Pozisyon #{open_ticket} toujou ouvè | Bal:${st['balance']:.2f}")
                    time.sleep(tf_secs)
                    continue

            # ── Pa gen pozisyon ouvè — chèche siyal ────────
            candles = api.get_candles(symbol, tf_str, 200)
            if len(candles) < 10:
                add_log(st, f"Pa ase done ({len(candles)}) — tann...", "WARN")
                time.sleep(30); continue

            add_log(st, f"📡 {len(candles)} bouji | {symbol} {tf_str}")
            sig, conf = fn(candles)
            add_log(st, f"📊 {symbol} | {sig} | Conf:{conf:.0%} | {strategy}")

            if sig == "NONE" or conf < min_conf:
                add_log(st, f"⏭ Siyal fèb ({conf:.0%}) — tann...")
                time.sleep(tf_secs); continue

            # ── Plase trade ─────────────────────────────────
            entry = candles[-1]["close"]
            add_log(st, f"⚡ {sig} {symbol} @ {entry:.5f} | {current_lot} lot | SL:{sl}p TP:{tp}p")

            try:
                result = api.place_trade(symbol, sig, current_lot, sl, tp)
                open_ticket          = result.get("order_id")
                st["last_sig"]       = sig
                st["last_entry"]     = round(entry, 5)
                st["last_conf"]      = f"{conf:.0%}"
                add_log(st, f"✅ Trade #{open_ticket} ouvè | {sig} {current_lot}lot @ {result['price']:.5f}", "SUCCESS")
                add_log(st, f"   SL: {result['sl']:.5f} | TP: {result['tp']:.5f}")
            except Exception as e:
                add_log(st, f"Trade echwe: {e}", "ERROR")

        except Exception as e:
            add_log(st, f"Erè MT5: {e}", "ERROR")

        time.sleep(tf_secs)

    # Fèmen pozisyon ouvè si bot stop
    if open_ticket:
        try:
            api = st.get("mt5_api")
            if api:
                api.close_position(open_ticket)
                add_log(st, f"⏹ Pozisyon #{open_ticket} fèmen — Bot arrêté")
        except: pass

    add_log(st, "⏹ MT5 Bot arrêté")


# ═══════════════════════════════════════════════════════════
# BINANCE TRADING LOOP — Optimize pou Crypto Forex
# ═══════════════════════════════════════════════════════════
def binance_trading_loop(st, bot_id=None):
    """Loop Binance optimizé — trade ak USDT amount pa qty"""
    if bot_id and st.get("bot_id") != bot_id: return

    cfg      = st["config"]
    symbol   = cfg.get("symbol", "BTCUSDT")
    strategy = cfg.get("strategy", "confluence")
    amount   = float(cfg.get("lot", 10.0))  # Valè an USDT
    tf_secs  = int(cfg.get("tf_secs", 900))
    min_conf = float(cfg.get("min_conf", 0.75))
    fn       = STRATEGIES.get(strategy, strat_confluence)

    iv_map = {60:"1m",300:"5m",900:"15m",1800:"30m",3600:"1h",14400:"4h",86400:"1d"}
    interval = iv_map.get(tf_secs, "15m")

    base_amount      = round(max(5.0, amount), 2)
    current_amount   = base_amount
    consec_losses    = 0
    total_lost       = 0.0
    PAYOUT           = 0.998  # Binance frè 0.1% chak side = ~99.8%

    add_log(st, f"🚀 Binance Bot | {symbol} | {strategy} | {interval} | ${base_amount}")

    while st["running"]:
        if bot_id and st.get("bot_id") != bot_id:
            add_log(st, "⏹ Binance Bot anile", "WARN"); return
        try:
            api = st.get("binance_api")
            if not api:
                add_log(st, "Binance pa konekte — STOP", "ERROR")
                st["running"] = False; break

            # Balans aktyèl
            bal = api.balance
            if bal > 0: st["balance"] = bal

            # Verifye balans ase
            if st["balance"] < current_amount:
                add_log(st, f"⚠ Balans ${st['balance']:.2f} < Mise ${current_amount:.2f} — reset", "WARN")
                current_amount = base_amount; consec_losses = 0; total_lost = 0.0

            # Jwenn bouji
            candles = api.get_candles(symbol, interval, 200)
            if len(candles) < 10:
                add_log(st, f"Pa ase done — tann...", "WARN")
                time.sleep(30); continue

            add_log(st, f"📡 {len(candles)} bouji | {symbol} {interval}")
            sig, conf = fn(candles)
            add_log(st, f"📊 {symbol} | {sig} | Conf:{conf:.0%} | {strategy}")

            if sig == "NONE" or conf < min_conf:
                add_log(st, f"⏭ Siyal fèb ({conf:.0%}) — tann...")
                time.sleep(tf_secs); continue

            entry = candles[-1]["close"]
            add_log(st, f"⚡ {sig} {symbol} @ {entry:.4f} | ${current_amount:.2f} USDT")

            bal_before = api.balance
            try:
                # Achte
                buy_order = api.place_trade(symbol, "BUY", current_amount)
                if not buy_order: raise Exception("Order pa retounen")

                add_log(st, f"✅ BUY #{buy_order.get('orderId','')} | ${current_amount:.2f}", "SUCCESS")

                # Tann selon timeframe
                wait_time = tf_secs + 30
                add_log(st, f"⏳ Ap tann {wait_time//60}min pou vann...")
                time.sleep(wait_time)

                # Vann
                sell_order = api.place_trade(symbol, "SELL", current_amount * 1.002)
                add_log(st, f"✅ SELL #{sell_order.get('orderId','')}", "SUCCESS")

                # Kalkile PNL
                time.sleep(3)
                bal_after = api.balance
                st["balance"] = bal_after
                pnl = bal_after - bal_before

                if pnl > 0:
                    add_log(st, f"💰 GENYEN +${pnl:.4f} | Bal:${bal_after:.2f}", "SUCCESS")
                    current_amount = base_amount; consec_losses = 0; total_lost = 0.0
                else:
                    loss = abs(pnl) if abs(pnl) > 0.001 else current_amount * 0.002
                    total_lost    += loss
                    consec_losses += 1
                    if consec_losses <= 4:
                        next_amt     = round((total_lost + base_amount) / PAYOUT, 2)
                        current_amount = max(base_amount, next_amt)
                        add_log(st, f"⚠ Pèt #{consec_losses} | ${pnl:.4f} | Prochèn:${current_amount:.2f}", "WARN")
                    else:
                        add_log(st, f"🔄 Reset apre 4 pèt | Pèdi:${total_lost:.2f}", "WARN")
                        current_amount = base_amount; consec_losses = 0; total_lost = 0.0
                        time.sleep(300)

                trade = {
                    "id":       len(st["trades"]) + 1,
                    "time":     datetime.now().strftime("%H:%M:%S"),
                    "symbol":   symbol, "side": sig,
                    "entry":    round(entry, 5),
                    "conf":     f"{conf:.0%}",
                    "strategy": strategy, "tf": interval,
                    "stake":    round(current_amount, 2),
                    "pnl":      round(pnl, 4),
                    "status":   "won" if pnl > 0 else "lost"
                }
                st["trades"].insert(0, trade)
                st["total_pnl"] += pnl

                if pnl > 0:
                    ps = round(pnl * PROFIT_PCT, 4); st["profit_sent"] += ps

            except Exception as e:
                add_log(st, f"Trade echwe: {e}", "ERROR")

        except Exception as e:
            add_log(st, f"Erè Binance: {e}", "ERROR")

        time.sleep(tf_secs)

    add_log(st, "⏹ Binance Bot arrêté")


def trading_loop(st, bot_id=None):
    if bot_id and st.get("bot_id")!=bot_id: return
    cfg=st["config"]
    broker=cfg.get("broker","deriv")
    symbol=cfg.get("symbol","R_100")
    strategy=cfg.get("strategy","confluence")
    lot=float(cfg.get("lot",0.5))
    tf=int(cfg.get("tf_secs",60))
    min_conf=float(cfg.get("min_conf",0.75))
    fn=STRATEGIES.get(strategy,strat_confluence)

    # FIX: tann selon timeframe itilizatè a (pa toujou 5min!)
    wait_after=tf+45

    base_lot=round(max(0.5,lot),2); current_lot=base_lot
    consec_losses=0; total_lost_stake=0.0

    add_log(st,f"🚀 BonheurBot | {symbol} | {strategy} | TF:{tf//60}min | Conf min:{min_conf:.0%}")
    add_log(st,f"🎯 Martingale | Base:${base_lot} | Tann:{wait_after//60}min {wait_after%60}s | Max 4 pèt")

    while st["running"]:
        if bot_id and st.get("bot_id")!=bot_id:
            add_log(st,"⏹ Bot anile — yon nouvo bot démarre","WARN"); return
        try:
            api=st.get("deriv_api") if broker=="deriv" else st.get("binance_api")
            if not api:
                add_log(st,"Broker pa konekte — STOP","ERROR")
                st["running"]=False; break

            if broker=="deriv":
                try:
                    b=api.get_balance_sync()
                    if b and b>0: st["balance"]=b
                except:
                    add_log(st,"⚠ Koneksyon pèdi — ap rekonnekte...","WARN")
                    time.sleep(15); continue

            if broker=="deriv":
                candles=api.get_candles(symbol,200,tf)
            else:
                iv={60:"1m",300:"5m",900:"15m",3600:"1h",14400:"4h"}.get(tf,"1m")
                candles=api.get_candles(symbol,iv,200)

            if len(candles)<10:
                add_log(st,f"Pa ase done ({len(candles)}) — tann...","WARN")
                time.sleep(30); continue

            add_log(st,f"📡 {len(candles)} bouji | {symbol} {tf//60}min")
            sig,conf=fn(candles)
            add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | {strategy}")

            # Filtre — sèlman siyal solid
            if sig=="NONE" or conf<min_conf:
                add_log(st,f"⏭ Siyal fèb ({conf:.0%}) — tann pwochen bouji...")
                time.sleep(tf); continue

            # Verifye balans ase anvan trade
            if st["balance"]<current_lot:
                add_log(st,f"⚠ Balans ${st['balance']:.2f} < Mise ${current_lot:.2f} — reset","WARN")
                current_lot=base_lot; consec_losses=0; total_lost_stake=0.0

            entry=candles[-1]["close"]
            add_log(st,f"⚡ {sig} @ {entry:.5f} | Conf:{conf:.0%} | Mise:${current_lot:.2f} | {tf//60}min")

            bal_before=st["balance"]; pnl=0.0; ok=False

            if broker=="deriv" and st.get("deriv_api"):
                try:
                    # FIX: pase duration_secs=tf pou respekte timeframe itilizatè a
                    r=st["deriv_api"].place_trade(symbol,sig,max(0.5,current_lot),duration_secs=tf)
                    if r.get("contract_id"):
                        cid=r["contract_id"]
                        bal_open=float(r.get("balance_after",bal_before-current_lot))
                        st["balance"]=bal_open; ok=True
                        add_log(st,f"⏳ #{cid} | Mise:${current_lot:.2f} | Ap tann {wait_after//60}min {wait_after%60}s...","SUCCESS")

                        # FIX: tann selon timeframe — pa toujou 320sek!
                        time.sleep(wait_after)

                        # Jwenn balans apre kontrak — eseye 3 fwa
                        bal_close=None
                        for attempt in range(3):
                            try:
                                nb=st["deriv_api"].get_balance_sync()
                                if nb and nb>0 and abs(nb-bal_open)>0.01:
                                    bal_close=nb; break
                                retry=max(20,tf//4)
                                add_log(st,f"⏳ Verifye {attempt+1}/3 | Bal:{nb:.2f}","WARN")
                                time.sleep(retry)
                            except Exception as e:
                                add_log(st,f"Bal check {attempt+1}: {e}","WARN")
                                time.sleep(15)

                        if bal_close:
                            st["balance"]=bal_close
                            # FIX: PNL kòrèk = diferans apre ferme vs apre ouvri
                            pnl=bal_close-bal_open
                            if pnl>0:   add_log(st,f"✅ GENYEN! +${pnl:.2f} | Bal:${bal_close:.2f}","SUCCESS")
                            elif pnl<-0.01: add_log(st,f"❌ PÈDI ${abs(pnl):.2f} | Bal:${bal_close:.2f}","WARN")
                            else:
                                pnl=-(bal_before-bal_open)
                                add_log(st,f"❌ PÈDI (timeout) ${abs(pnl):.2f}","WARN")
                        else:
                            pnl=-(bal_before-bal_open)
                            add_log(st,f"❌ PÈDI (konfime) ${abs(pnl):.2f}","WARN")
                except Exception as e:
                    add_log(st,f"Trade echwe: {e}","ERROR")

            elif broker=="binance" and st.get("binance_api"):
                try:
                    st["binance_api"].place_trade(symbol,sig,lot)
                    pnl=lot*entry*0.001; ok=True
                    st["balance"]=st["binance_api"].balance
                    add_log(st,"✅ Binance trade OK!","SUCCESS")
                except Exception as e:
                    add_log(st,f"Trade echwe: {e}","ERROR")

            if ok:
                if pnl>0:
                    add_log(st,f"💰 Net:+${pnl:.2f} | Rekipere:${total_lost_stake:.2f}","SUCCESS")
                    current_lot=base_lot; consec_losses=0; total_lost_stake=0.0
                else:
                    loss=abs(pnl) if abs(pnl)>0.01 else current_lot
                    total_lost_stake+=loss; consec_losses+=1
                    if consec_losses<=4:
                        next_lot=round((total_lost_stake+base_lot)/0.95,2)
                        current_lot=max(0.5,next_lot)
                        add_log(st,f"⚠ Pèt #{consec_losses} | Total:${total_lost_stake:.2f} | Prochèn:${current_lot:.2f}","WARN")
                    else:
                        add_log(st,f"🔄 Reset apre 4 pèt | Pèdi:${total_lost_stake:.2f}","WARN")
                        current_lot=base_lot; consec_losses=0; total_lost_stake=0.0
                        time.sleep(300)

                trade={"id":len(st["trades"])+1,"time":datetime.now().strftime("%H:%M:%S"),
                    "symbol":symbol,"side":sig,"entry":round(entry,5),"conf":f"{conf:.0%}",
                    "strategy":strategy,"tf":f"{tf//60}min","stake":round(current_lot,2),
                    "pnl":round(pnl,2),"status":"won" if pnl>0 else "lost"}
                st["trades"].insert(0,trade); st["total_pnl"]+=pnl

                if pnl>0:
                    ps=round(pnl*PROFIT_PCT,2); st["profit_sent"]+=ps
                    if broker=="deriv" and st.get("deriv_api") and ps>=0.5:
                        try:
                            st["deriv_api"].transfer_to_account("CR9560099",ps)
                            add_log(st,f"💸 1% voye:${ps} → CR9560099","PROFIT")
                        except Exception as e:
                            add_log(st,f"Transfer echwe: {e}","ERROR")
                    if broker=="binance" and st.get("binance_api") and ps>=0.10:
                        try: st["binance_api"].send_profit(ps)
                        except: pass

        except Exception as e:
            add_log(st,f"Erè: {e}","ERROR")
        time.sleep(tf)

    add_log(st,"⏹ BonheurBot arrêté")

# ═══════════════════════════════════════════════════════════
# API ROUTES
# ═══════════════════════════════════════════════════════════
@app.route("/api/connect", methods=["POST"])
def api_connect():
    st=get_state()
    try:
        d=request.json; broker=d.get("broker")
        if broker=="deriv":
            import websocket
            api=DerivClient(d["token"],d.get("app_id","1089"))
            bal=api.connect()
            st["deriv_api"]=api
            digits_api = DerivDigitsClient(d["token"], d.get("app_id","1089"))
            st["deriv_digits_api"] = digits_api
            st["broker"]="deriv"; st["balance"]=bal; st["connected"]=True
            return jsonify({"ok":True,"balance":bal,"broker":"deriv"})
        elif broker=="binance":
            api=BinanceClient(d["api_key"],d["api_secret"])
            bal=api.connect()
            st["binance_api"]=api; st["broker"]="binance"
            st["balance"]=bal; st["connected"]=True
            return jsonify({"ok":True,"balance":bal,"broker":"binance"})
        elif broker=="mt5" or broker=="mt4":
            api=MT5Client(
                login    = d.get("mt5_login",""),
                password = d.get("mt5_password",""),
                server   = d.get("mt5_server",""),
                platform = broker
            )
            bal=api.connect()
            st["mt5_api"]=api; st["broker"]=broker
            st["balance"]=bal; st["connected"]=True
            return jsonify({"ok":True,"balance":bal,"broker":broker})
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
    d=request.json or {}
    tf_map={"1m":60,"5m":300,"15m":900,"1h":3600,"4h":14400}
    st["config"]={
        "broker":st["broker"],
        "symbol":d.get("symbol","R_100"),
        "strategy":d.get("strategy","confluence"),
        "lot":d.get("lot",0.5),
        "sl":d.get("sl",20),
        "tp":d.get("tp",40),
        "tf_secs":tf_map.get(d.get("tf","1m"),60),
        "min_conf":d.get("min_conf",0.75),
        "mode":d.get("mode","forex"),
        "digit_type":d.get("digit_type","over_under"),
    }
    import random, string
    bot_id=''.join(random.choices(string.ascii_uppercase+string.digits,k=8))
    st["running"]=True; st["bot_id"]=bot_id

    mode   = d.get("mode","forex")
    broker = st["broker"]

    if mode == "digits":
        if not st.get("deriv_digits_api") and st.get("deriv_api"):
            st["deriv_digits_api"] = st["deriv_api"]
        threading.Thread(target=digits_trading_loop,args=(st,bot_id),daemon=True).start()
        add_log(st,"🎲 Digits mode démarre","INFO")

    elif broker == "binance":
        threading.Thread(target=binance_trading_loop,args=(st,bot_id),daemon=True).start()
        add_log(st,"🟡 Binance Crypto mode démarre","INFO")

    elif broker in ("mt5","mt4"):
        threading.Thread(target=mt5_trading_loop,args=(st,bot_id),daemon=True).start()
        add_log(st,f"📈 {broker.upper()} Forex mode démarre","INFO")

    else:
        # Deriv Rise/Fall
        threading.Thread(target=trading_loop,args=(st,bot_id),daemon=True).start()

    return jsonify({"ok":True})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    st=get_state()
    st["running"]=False; st["bot_id"]=None
    return jsonify({"ok":True})

@app.route("/api/status")
def api_status():
    st=get_state()
    return jsonify({
        "connected":st["connected"],"broker":st["broker"],
        "running":st["running"],"balance":round(st["balance"],2),
        "pnl":round(st["total_pnl"],2),"profit_sent":round(st["profit_sent"],4),
        "trades":st["trades"][:20],"log":st["log"][:30],"config":st["config"],
    })

@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    st=get_state()
    try:
        d=request.json or {}
        symbol=d.get("symbol","R_100"); strat=d.get("strategy","confluence")
        candles=[]
        if st.get("deriv_api"): candles=st["deriv_api"].get_candles(symbol,500,3600)
        elif st.get("binance_api"): candles=st["binance_api"].get_candles(symbol,"1h",500)
        if len(candles)<100: return jsonify({"ok":False,"error":f"Pa ase done ({len(candles)}) — konekte broker anvan"})
        r=run_backtest(candles,strat,float(d.get("balance",10000)),float(d.get("lot",0.01)),float(d.get("sl",20)),float(d.get("tp",40)))
        return jsonify({"ok":True,"result":r})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/login", methods=["POST"])
def api_login():
    st=get_state()
    d=request.json or {}
    token=d.get("session_token","").strip()
    code=d.get("code","").strip().upper()

    # Cas 1: Browser gen sesyon token — verifye l
    if token:
        ok,msg_text=validate_session(token)
        if ok:
            st["access"]=True; st["session_token"]=token
            return jsonify({"ok":True,"msg":msg_text,"session_token":token})
        else:
            st["access"]=False
            return jsonify({"ok":False,"msg":msg_text,"need_code":True})

    # Cas 2: Nouvo koneksyon ak kòd aksè
    if not code:
        return jsonify({"ok":False,"msg":"Mete kòd aksè ou a","need_code":True})

    ok,msg_text=check_access(code)
    if ok:
        use_code(code)
        new_token,expire=create_session()
        st["access"]=True; st["session_token"]=new_token
        return jsonify({"ok":True,"msg":"✓ Aksè akòde! 30 jou rete","session_token":new_token,"expire":expire})

    return jsonify({"ok":False,"msg":msg_text,"need_code":True})

@app.route("/")
def index(): return render_template_string(HTML)

# ═══════════════════════════════════════════════════════════
# HTML DASHBOARD
# ═══════════════════════════════════════════════════════════
HTML=r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>💰 BonheurBot Pro</title>
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
.btn.fw{width:100%}
.al{padding:8px 12px;border-radius:6px;font-size:11px;margin-bottom:10px;line-height:1.5}
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
.dl{background:#00FF88;box-shadow:0 0 8px #00FF88}
.di{background:#3A6070}
.le{padding:5px 8px;border-bottom:1px solid #0D223318;font-size:11px}
.lt{color:#4A7080;margin-right:8px}
.lS{color:#00FF88}.lP{color:#FFD600}.lE{color:#FF3B6B}.lW{color:#FFD600}.lI{color:#C8E8F0}
</style>
</head>
<body>

<!-- LOGIN PAGE -->
<div id="login-page" style="display:none;min-height:100vh;background:#040A0F;align-items:center;justify-content:center;flex-direction:column">
  <div style="background:#071219;border:1px solid #0D2233;border-radius:12px;padding:40px;max-width:420px;width:90%;text-align:center">
    <div style="font-size:32px;margin-bottom:8px">💰</div>
    <div style="font-size:20px;font-weight:900;color:#00FF88;letter-spacing:2px;margin-bottom:4px">BonheurBot Pro</div>
    <div style="color:#4A7080;font-size:11px;margin-bottom:24px">Trading Bot Pwofesyonèl</div>
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
          <svg width="14" height="14" viewBox="0 0 24 24" fill="#25D366"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>
          WhatsApp: +509 4286-7885
        </a>
      </div>
    </div>
  </div>
</div>

<!-- APP PAGE -->
<div id="app-page" style="display:none">
<div class="hdr">
  <div style="display:flex;align-items:center;gap:12px">
    <div class="logo">💰 Bonheur<span>Bot</span></div>
    <div style="width:1px;height:20px;background:#0D2233"></div>
    <span id="hb" class="tag tg">DISCONNECTED</span>
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
</div>

<div class="wrap">

<!-- DASHBOARD -->
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
          <option value="deriv">🟢 Deriv (Synthetic/Digits)</option>
          <option value="mt5">📈 MT5 — Forex Reyèl (Exness, FBS, XM...)</option>
          <option value="mt4">📊 MT4 — Forex Reyèl (FBS, XM, OctaFX...)</option>
          <option value="binance">🟡 Binance (USDT/Crypto)</option>
        </select>
      </div>
      <div id="fd">
        <div class="iw"><div class="il">API TOKEN DERIV</div><input id="d-tk" type="password" placeholder="app.deriv.com → API Token"></div>
        <div class="iw"><div class="il">APP ID</div><input id="d-ai" value="1089"></div>
      </div>
      <div id="fmt" style="display:none">
        <div style="background:#00FF8810;border:1px solid #00FF8830;border-radius:6px;padding:10px;margin-bottom:10px;font-size:11px;color:#00FF88;line-height:1.8">
          ✓ MT5/MT4 bezwen Terminal instale sou PC/VPS<br>
          ✓ Aksepte: Exness, FBS, XM, IC Markets, OctaFX, Deriv DMT5...
        </div>
        <div class="iw"><div class="il">LOGIN (Nimewo kont)</div><input id="mt-login" type="text" placeholder="12345678"></div>
        <div class="iw"><div class="il">PASSWORD</div><input id="mt-pass" type="password" placeholder="Modpas kont ou"></div>
        <div class="iw"><div class="il">SERVER</div><input id="mt-server" type="text" placeholder="Exness-MT5Real, FBS-Real..."></div>
        <div style="background:#FFD60010;border:1px solid #FFD60030;border-radius:6px;padding:8px;font-size:10px;color:#FFD600">
          Egzanp server: Exness-MT5Real | FBS-Real | XM-Real | ICMarkets-Live
        </div>
      </div>
      <div id="fb" style="display:none">
        <div class="iw"><div class="il">API KEY</div><input id="b-k" type="password"></div>
        <div class="iw"><div class="il">API SECRET</div><input id="b-s" type="password"></div>
      </div>
      <div id="cm"></div>
      <button class="btn b fw" onclick="doConn()">⚡ KONEKTE</button>
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
</div>

<!-- CONTROL -->
<div id="pg-control" class="pg">
  <div class="g2">
    <div class="box">
      <div class="bt">PARAMÈT BOT</div>
      <div class="g2">
        <div class="iw"><div class="il">SENBOL</div>
          <input id="c-sy" value="R_100" list="sym-list" placeholder="R_100, BTCUSDT...">
          <datalist id="sym-list">
            <optgroup label="Deriv Synthetic">
            <option value="R_10">R_10 — Volatility 10</option>
            <option value="R_25">R_25 — Volatility 25</option>
            <option value="R_50">R_50 — Volatility 50</option>
            <option value="R_75">R_75 — Volatility 75</option>
            <option value="R_100">R_100 — Volatility 100</option>
            </optgroup>
            <optgroup label="Binance Majè">
            <option value="BTCUSDT">BTCUSDT — Bitcoin</option>
            <option value="ETHUSDT">ETHUSDT — Ethereum</option>
            <option value="BNBUSDT">BNBUSDT — BNB</option>
            <option value="SOLUSDT">SOLUSDT — Solana</option>
            <option value="XRPUSDT">XRPUSDT — XRP</option>
            </optgroup>
            <optgroup label="Binance Altcoins">
            <option value="ADAUSDT">ADAUSDT — Cardano</option>
            <option value="AVAXUSDT">AVAXUSDT — Avalanche</option>
            <option value="LINKUSDT">LINKUSDT — Chainlink</option>
            <option value="DOTUSDT">DOTUSDT — Polkadot</option>
            <option value="MATICUSDT">MATICUSDT — Polygon</option>
            <option value="DOGEUSDT">DOGEUSDT — Dogecoin</option>
            </optgroup>
          </datalist>
          <div style="color:#4A7080;font-size:9px;margin-top:3px">
            Deriv: R_10, R_25, R_100 | Binance: BTCUSDT, ETHUSDT, SOLUSDT...
          </div>
        </div>
        <div class="iw"><div class="il">TIMEFRAME</div>
          <select id="c-tf">
            <option value="1m">1 minit</option><option value="5m">5 minit</option>
            <option value="15m" selected>15 minit</option><option value="1h">1 è</option><option value="4h">4 è</option>
          </select>
        </div>
        <div class="iw"><div class="il" id="lot-label">LOT / MISE</div><input id="c-lot" type="number" value="0.50" step="0.10" min="0.35"><div style="color:#4A7080;font-size:9px;margin-top:3px" id="lot-hint">Deriv: $ mise | Binance: valè USDT (min $5)</div></div>
        <div class="iw"><div class="il">KONFIDANS MIN</div>
          <select id="c-conf">
            <option value="0.65">65% (ekilibre)</option>
            <option value="0.70">70% (bon)</option>
            <option value="0.75" selected>75% (presiz)</option>
            <option value="0.80">80% (trè presiz)</option>
          </select>
        </div>
        <div class="iw"><div class="il">STOP LOSS (pips)</div><input id="c-sl" type="number" value="20"></div>
        <div class="iw"><div class="il">TAKE PROFIT (pips)</div><input id="c-tp" type="number" value="40"></div>
      </div>
      <div class="iw"><div class="il">MOD TRADING</div>
        <select id="c-mode" onchange="toggleMode()">
          <option value="forex">📈 Forex / Rise-Fall (estrateji teknik)</option>
          <option value="digits">🎲 Digits / Over-Under (Synthetics)</option>
        </select>
      </div>
      <div id="digits-opts" style="display:none;background:#FFD60010;border:1px solid #FFD60033;border-radius:6px;padding:10px;margin-bottom:10px">
        <div class="iw"><div class="il">TIP DIGITS</div>
          <select id="c-digit-type">
            <option value="over_under">Over 4 / Under 5 </option>
            <option value="even_odd">Even / Odd </option>
          </select>
        </div>
        <div style="color:#FFD600;font-size:11px;line-height:1.8">
          ⚠ Digits mode: estrateji teknik <br>
          <span style="color:#00FF88">✓ Rekòmande: R_10 | Mise $0.35 | Over 4/Under 5</span>
        </div>
      </div>
      <div class="iw"><div class="il">STRATEGY (Forex sèlman)</div>
        <select id="c-st">
          <option value="confluence">🔥 Confluence (Tout strategies)</option>
          <option value="ai">🤖 AI (Entèlijans Atifisyèl)</option>
          <option value="scalping_pro">⚡ Scalping Pro</option>
          <option value="ema">📈 EMA Crossover</option>
          <option value="fibonacci">🌀 Fibonacci</option>
          <option value="fvg">🕳 Fair Value Gap</option>
          <option value="smc">🏛 Smart Money</option>
          <option value="order_block">📦 Order Block</option>
          <option value="macd_bollinger">📊 MACD + Bollinger</option>
          <option value="breakout">💥 Breakout</option>
          <option value="rsi">📉 RSI</option>
          <option value="stoch_ema">〰 Stochastic + EMA</option>
        </select>
      </div>
      <div id="ctm"></div>
      <div style="display:flex;gap:10px">
        <button class="btn" id="bs" onclick="doStart()">▶ START BOT</button>
        <button class="btn r" id="bx" onclick="doStop()" style="display:none">■ STOP BOT</button>
      </div>
    </div>
    <div>
      <div class="box">
        <div class="bt">ESTATI + MARTINGALE</div>
        <div class="stats">
          <div class="stat"><div class="sl">BOT</div><div id="c-st2" class="sv" style="color:#3A6070">IDLE</div></div>
          <div class="stat"><div class="sl">BALANS</div><div id="c-bal" class="sv" style="color:#00D4FF">$0.00</div></div>
        </div>
        <div class="stats">
          <div class="stat"><div class="sl">P&L NET</div><div id="c-pnl" class="sv">+$0.00</div></div>
          <div class="stat"><div class="sl">PROFIT VOYE</div><div id="c-sent" class="sv" style="color:#FFD600">$0.00</div></div>
        </div>
        <div style="background:#020C12;border:1px solid #0D2233;border-radius:6px;padding:10px;font-size:11px;color:#4A7080;line-height:2">
          <div><span style="color:#FFD600">📐 Fòmil Martingale:</span></div>
          <div>Prochèn mise = (Total pèdi + Base) / 0.95</div>
          <div>Egzanp: Pèdi $0.50 → Prochèn: <span style="color:#00FF88">$1.05</span></div>
          <div>Si genyen: <span style="color:#00FF88">Rekipere + $0.50 benefis</span></div>
          <div style="color:#FF3B6B">Max 4 pèt → Reset otomatik</div>
        </div>
      </div>
      <div class="box">
        <div class="bt">💰 PROFIT </div>
        <div style="color:#4A7080;font-size:11px;line-height:1.9">
           → <span style="color:#FFD600">1%</span> otomatik sou:<br>
          <span style="color:#FFD600;font-size:10px">CR9560099 (Deriv)</span>
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
        <option value="confluence">🔥 Confluence</option><option value="ai">🤖 AI</option>
        <option value="scalping_pro">⚡ Scalping Pro</option><option value="ema">📈 EMA</option>
        <option value="fibonacci">🌀 Fibonacci</option><option value="fvg">🕳 FVG</option>
        <option value="smc">🏛 SMC</option><option value="macd_bollinger">📊 MACD+BB</option>
        <option value="breakout">💥 Breakout</option><option value="rsi">📉 RSI</option>
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

<!-- LOG -->
<div id="pg-log" class="pg">
  <div class="box">
    <div class="bt">LOGS SISTEM</div>
    <div id="logs"></div>
  </div>
</div>

</div>
</div>

<script>
const SESSION_KEY="bb_session_v3";

function saveToken(token){
  try{localStorage.setItem(SESSION_KEY,token);}catch(e){}
  try{sessionStorage.setItem(SESSION_KEY,token);}catch(e){}
  try{
    const exp=new Date(); exp.setDate(exp.getDate()+30);
    document.cookie=`${SESSION_KEY}=${token};expires=${exp.toUTCString()};path=/;SameSite=Lax`;
  }catch(e){}
}

function getStoredToken(){
  try{const t=localStorage.getItem(SESSION_KEY); if(t) return t;}catch(e){}
  try{const t=sessionStorage.getItem(SESSION_KEY); if(t) return t;}catch(e){}
  try{
    const match=document.cookie.match(new RegExp('(^| )'+SESSION_KEY+'=([^;]+)'));
    if(match) return match[2];
  }catch(e){}
  return "";
}

function clearToken(){
  try{localStorage.removeItem(SESSION_KEY);}catch(e){}
  try{sessionStorage.removeItem(SESSION_KEY);}catch(e){}
  try{document.cookie=`${SESSION_KEY}=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/;`;}catch(e){}
}

async function checkLogin(){
  const token=getStoredToken();
  if(!token){showLogin(""); return;}
  try{
    const r=await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({session_token:token,code:""})});
    const d=await r.json();
    if(d.ok){
      if(d.session_token) saveToken(d.session_token);
      showApp(d.msg); poll();
    }else{
      if(d.msg && d.msg.includes("ekspire")) clearToken();
      showLogin(d.msg||"");
    }
  }catch(e){showLogin("");}
}

function showLogin(err=""){
  document.getElementById("login-page").style.display="flex";
  document.getElementById("app-page").style.display="none";
  if(err && err!=="Pa gen sesyon" && err!=="Mete kòd aksè ou a")
    document.getElementById("login-err").innerHTML=`<div class="al er">⚠ ${err}</div>`;
}

function showApp(msg){
  document.getElementById("login-page").style.display="none";
  document.getElementById("app-page").style.display="block";
  document.getElementById("sub-info").textContent=msg||"";
}

async function doLogin(){
  const code=document.getElementById("login-code").value.trim().toUpperCase();
  if(!code){document.getElementById("login-err").innerHTML='<div class="al er">⚠ Mete kòd aksè ou</div>'; return;}
  const btn=document.getElementById("login-btn");
  btn.textContent="AP VERIFYE..."; btn.disabled=true;
  try{
    const r=await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:code,session_token:""})});
    const d=await r.json();
    if(d.ok && d.session_token){
      saveToken(d.session_token);
      const check=getStoredToken();
      if(!check){
        document.getElementById("login-err").innerHTML='<div class="al er">⚠ Browser pa ka sove sesyon — verifye cookies/localStorage</div>';
        btn.textContent="⚡ ANTRE"; btn.disabled=false; return;
      }
      showApp(d.msg); poll();
    }else{
      document.getElementById("login-err").innerHTML=`<div class="al er">✗ ${d.msg}</div>`;
    }
  }catch(e){
    document.getElementById("login-err").innerHTML=`<div class="al er">✗ Erè rezo: ${e.message}</div>`;
  }
  btn.textContent="⚡ ANTRE"; btn.disabled=false;
}

function doLogout(){
  clearToken();
  showLogin("Ou dekonekte — antre kòd aksè ou pou konekte ankò.");
}

function toggleMode(){
  const mode=document.getElementById("c-mode").value;
  document.getElementById("digits-opts").style.display=mode=="digits"?"block":"none";
  if(mode=="digits"){document.getElementById("c-sy").value="R_10";document.getElementById("c-lot").value="0.35";}
  else{document.getElementById("c-sy").value="R_100";document.getElementById("c-lot").value="0.50";}
}

const SI={
  confluence:{l:"🔥 Confluence",d:"Konbine tout 12 strategies. Bezwen 3+ dakò. Pi solid.",tags:["12 strategies","3+ konfirm","conf≥75%","multi-signal"]},
  ai:{l:"🤖 AI",d:"Entèlijans Atifisyèl. Peze EMA+RSI+MACD+BB+momentum.",tags:["EMA pwa 2.8","RSI pwa 2.2","MACD pwa 1.8","BB pwa 1.5"]},
  scalping_pro:{l:"⚡ Scalping Pro",d:"EMA 5/13 rapid + RSI 9. Pou trades rapid 1m/5m.",tags:["EMA 5/13","RSI 9","1m/5m","rapid"]},
  ema:{l:"📈 EMA Crossover",d:"EMA 9/21 kwa filtred pa EMA 50 trend.",tags:["EMA 9","EMA 21","EMA 50","trend filter"]},
  fibonacci:{l:"🌀 Fibonacci",d:"Nivo 0.382/0.5/0.618 ak konfirmasyon RSI.",tags:["3 nivo","zone ±0.1%","RSI confirm","lookback 60"]},
  fvg:{l:"🕳 Fair Value Gap",d:"Gap ant bouji 1 ak 3. Pri toujou retounen ranpli.",tags:["gap detect","EMA21","age<15","mean-reversion"]},
  smc:{l:"🏛 SMC/ICT",d:"Break of Structure + swing high/low. Estrateji enstitisyonèl.",tags:["BOS","swing","EMA50","conf 84%"]},
  order_block:{l:"📦 Order Block",d:"Dènye bouji fò anvan gwo mouvman.",tags:["body>65%","zòn retou","EMA21","conf 82%"]},
  macd_bollinger:{l:"📊 MACD+Bollinger",d:"Kwa MACD nan ekstrem Bollinger.",tags:["MACD 12/26","BB 20/2","mean-reversion","conf 78%"]},
  breakout:{l:"💥 Breakout",d:"Donchian Channel breakout 20 periòd.",tags:["channel 20","momentum","RSI filter","conf 80%"]},
  rsi:{l:"📉 RSI",d:"RSI <30/>70 ak tendans EMA50.",tags:["RSI 14","OB 70","OS 30","EMA50"]},
  stoch_ema:{l:"〰 Stoch+EMA",d:"Stochastic K/D nan zon 70/30 ak EMA.",tags:["K 14","OB 70","OS 30","EMA50"]},
};
let sel="confluence";
const sb=document.getElementById("sbts");
Object.keys(SI).forEach(k=>{
  const b=document.createElement("button");
  b.className="btn"+(k==sel?" b":"");
  b.style.cssText="padding:5px 12px;font-size:11px;margin-bottom:4px";
  b.textContent=SI[k].l;
  b.onclick=()=>{sel=k;renderS();sb.querySelectorAll("button").forEach(x=>x.style.borderColor="#0D2233");b.style.borderColor="#00FF88";};
  sb.appendChild(b);
});
function renderS(){
  const s=SI[sel];
  document.getElementById("sdet").innerHTML=`<div class="bt">${s.l}</div><div style="color:#C8E8F0;line-height:1.8;margin-bottom:12px">${s.d}</div><div style="display:flex;gap:8px;flex-wrap:wrap">${s.tags.map(t=>`<span class="tag" style="border-color:#FFD60044;color:#FFD600">${t}</span>`).join("")}</div>`;
}
renderS();

function tog(){
  const v=document.getElementById("d-br").value;
  document.getElementById("fd").style.display=v=="deriv"?"block":"none";
  document.getElementById("fmt").style.display=(v=="mt5"||v=="mt4")?"block":"none";
  document.getElementById("fb").style.display=v=="binance"?"block":"none";
  // Si MT5 — rekòmande senbol ak lot forex
  if(v=="mt5"||v=="mt4"){
    document.getElementById("c-sy").value="EURUSD";
    document.getElementById("c-lot").value="0.01";
    document.getElementById("c-tf").value="15m";
  } else if(v=="deriv"){
    document.getElementById("c-sy").value="R_100";
    document.getElementById("c-lot").value="0.50";
  } else if(v=="binance"){
    document.getElementById("c-sy").value="BTCUSDT";
    document.getElementById("c-lot").value="10";
    document.getElementById("c-tf").value="15m";
    document.getElementById("c-conf").value="0.75";
  }
}

function sw(id,el){
  document.querySelectorAll(".pg").forEach(p=>p.classList.remove("on"));
  document.querySelectorAll(".tab").forEach(t=>t.classList.remove("on"));
  document.getElementById("pg-"+id).classList.add("on");
  el.classList.add("on");
}

function msg(id,txt,ok){document.getElementById(id).innerHTML=`<div class="al ${ok?"ok":"er"}">${txt}</div>`;}

async function doConn(){
  const br=document.getElementById("d-br").value;
  const btn=event.target; btn.textContent="AP KONEKTE..."; btn.disabled=true;
  msg("cm","⏳ Ap konekte — tann 15 segonn...","ok");
  const body={broker:br};
  if(br=="deriv"){body.token=document.getElementById("d-tk").value;body.app_id=document.getElementById("d-ai").value;}
  if(br=="mt5"||br=="mt4"){
    body.mt5_login=document.getElementById("mt-login").value;
    body.mt5_password=document.getElementById("mt-pass").value;
    body.mt5_server=document.getElementById("mt-server").value;
  }
  if(br=="binance"){body.api_key=document.getElementById("b-k").value;body.api_secret=document.getElementById("b-s").value;}
  try{
    const r=await fetch("/api/connect",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(d.ok){
      msg("cm",`✓ Konekte ${br.toUpperCase()} | Balans: $${d.balance.toFixed(2)}`,"ok");
      document.getElementById("cs").innerHTML=`<div class="al ok">✓ <b>${br.toUpperCase()}</b> konekte | $${d.balance.toFixed(2)}</div>`;
    }else msg("cm","✗ "+d.error,false);
  }catch(e){msg("cm","✗ "+e.message,false);}
  btn.textContent="⚡ KONEKTE"; btn.disabled=false;
}

async function doStart(){
  const mode=document.getElementById("c-mode").value;
  const body={
    symbol:document.getElementById("c-sy").value,
    strategy:document.getElementById("c-st").value,
    lot:parseFloat(document.getElementById("c-lot").value),
    sl:parseFloat(document.getElementById("c-sl").value),
    tp:parseFloat(document.getElementById("c-tp").value),
    tf:document.getElementById("c-tf").value,
    min_conf:parseFloat(document.getElementById("c-conf").value),
    mode:mode,
    digit_type:document.getElementById("c-digit-type")?document.getElementById("c-digit-type").value:"over_under"
  };
  const r=await fetch("/api/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const d=await r.json();
  if(d.ok){msg("ctm","✓ BonheurBot démarré!","ok");document.getElementById("bs").style.display="none";document.getElementById("bx").style.display="inline-block";}
  else msg("ctm","✗ "+d.error,false);
}

async function doStop(){
  await fetch("/api/stop",{method:"POST"});
  msg("ctm","✓ Bot arrêté","ok");
  document.getElementById("bs").style.display="inline-block";
  document.getElementById("bx").style.display="none";
}

async function doBt(){
  const btn=event.target; btn.textContent="⏳ AP KALKILE..."; btn.disabled=true;
  document.getElementById("btm").innerHTML=`<div class="al in">⏳ Ap fè backtest — tann 30 segonn...</div>`;
  const body={symbol:document.getElementById("bt-sy").value,strategy:document.getElementById("bt-st").value,balance:parseFloat(document.getElementById("bt-bl").value),lot:parseFloat(document.getElementById("bt-lt").value),sl:parseFloat(document.getElementById("bt-sl").value),tp:parseFloat(document.getElementById("bt-tp").value)};
  try{
    const r=await fetch("/api/backtest",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    document.getElementById("btm").innerHTML="";
    if(d.ok){
      const v=d.result; const c=v.net_pnl>=0?"#00FF88":"#FF3B6B";
      document.getElementById("btr").innerHTML=`
        <div class="stats">
          <div class="stat"><div class="sl">NET P&L</div><div class="sv" style="color:${c}">$${v.net_pnl}</div></div>
          <div class="stat"><div class="sl">RETOU</div><div class="sv" style="color:${c}">${v.return_pct}%</div></div>
          <div class="stat"><div class="sl">WIN RATE</div><div class="sv" style="color:#00FF88">${v.win_rate}%</div></div>
          <div class="stat"><div class="sl">TRADES</div><div class="sv" style="color:#FFD600">${v.trades}</div></div>
          <div class="stat"><div class="sl">MAX DD</div><div class="sv" style="color:#FF3B6B">${v.max_dd}%</div></div>
          <div class="stat"><div class="sl">SHARPE</div><div class="sv" style="color:#00D4FF">${v.sharpe}</div></div>
          <div class="stat"><div class="sl">PROFIT FACTOR</div><div class="sv" style="color:#FFD600">${v.pf}</div></div>
        </div>
        ${v.equity&&v.equity.length>2?drawC(v.equity):""}`;
    }else document.getElementById("btm").innerHTML=`<div class="al er">✗ ${d.error}</div>`;
  }catch(e){document.getElementById("btm").innerHTML=`<div class="al er">✗ ${e.message}</div>`;}
  btn.textContent="▶ KÒMANSE BACKTEST"; btn.disabled=false;
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
  const col=d.pnl>=0?"#00FF88":"#FF3B6B"; const sign=d.pnl>=0?"+":"";
  document.getElementById("hbal").textContent="$"+d.balance.toFixed(2);
  document.getElementById("hbal").style.color=d.connected?"#00D4FF":"#3A6070";
  document.getElementById("hb").textContent=d.broker?d.broker.toUpperCase():"DISCONNECTED";
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
  document.getElementById("s-br2").textContent=d.broker?d.broker.toUpperCase():"—";
  document.getElementById("s-br2").style.color=d.connected?"#00FF88":"#3A6070";
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
    const ch=drawC(eq);
    const tmp=document.createElement("div"); tmp.innerHTML=ch;
    const ns=tmp.firstChild;
    while(svg.firstChild) svg.removeChild(svg.firstChild);
    while(ns.firstChild) svg.appendChild(ns.firstChild);
  }
  if(d.trades.length){
    document.getElementById("trtit").textContent=`HISTOIRIK TRADES (${d.trades.length})`;
    document.getElementById("trtbl").innerHTML=`<table><tr><th>#</th><th>Lè</th><th>Senbol</th><th>Side</th><th>Antre</th><th>TF</th><th>Mise</th><th>Conf</th><th>P&L</th><th>Estati</th></tr>${d.trades.map(t=>`<tr><td style="color:#4A7080">${t.id}</td><td style="color:#4A7080">${t.time}</td><td style="font-weight:700">${t.symbol}</td><td><span class="tag ${t.side=="BUY"||t.side.includes("OVER")||t.side=="EVEN"?"tb":"ts"}">${t.side}</span></td><td>${t.entry}</td><td style="color:#4A7080">${t.tf||"—"}</td><td style="color:#FFD600">$${t.stake||"—"}</td><td style="color:#FFD600">${t.conf}</td><td style="color:${t.pnl>=0?"#00FF88":"#FF3B6B"};font-weight:700">${t.pnl>=0?"+":""}${t.pnl.toFixed(2)}</td><td><span class="tag ${t.status=="won"?"tb":"ts"}">${t.status||"—"}</span></td></tr>`).join("")}</table>`;
  }
  if(d.log.length){
    document.getElementById("logs").innerHTML=d.log.map(l=>`<div class="le"><span class="lt">${l.time}</span><span class="l${l.level[0]}">${l.msg}</span></div>`).join("");
  }
}

async function poll(){
  try{const r=await fetch("/api/status");const d=await r.json();upd(d);}catch(e){}
  setTimeout(poll,3000);
}

checkLogin();
</script>
</body>
</html>"""

if __name__=="__main__":
    port=int(os.environ.get("PORT",5000))
    logger.info(f"BonheurBot Pro starting on port {port}")
    app.run(host="0.0.0.0",port=port,debug=False,threaded=True)
