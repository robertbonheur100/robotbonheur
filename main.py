f"""
╔══════════════════════════════════════════════════════════════╗
║              BONHEURBOT PRO — QUOTEX EDITION                 ║
║         Multi-User Trading Bot — Quotex (Binary)             ║
║   Confluence Strategies | Martingale | 3-Loss Pause          ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, json, time, threading, logging, math, uuid, secrets, asyncio
from datetime import datetime, timedelta, date
from flask import Flask, request, jsonify, render_template_string, session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# ABÒNMAN / ACCESS CODES
# ═══════════════════════════════════════════════════════════
SUB_PRICE   = 50
WHATSAPP    = "50942867885"   # +509 4286-7885

ACCESS_CODES = {
    "BONHEURWIIN": {"created_at": None, "used": False, "is_adm": True},
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
                "bot_id": None, "connected": False, "running": False,
                "balance": 0.0, "total_pnl": 0.0,
                "trades": [], "log": [], "config": {},
                "quotex_api": None,
            }
    return _user_states[uid]

# ═══════════════════════════════════════════════════════════
# INDIKATÈ TEKNIK
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
    if len(candles) < p+5:
        return "NONE", 0.0
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]
    trs = []
    for i in range(1, len(candles)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    atr_vals = []
    for i in range(p-1, len(trs)):
        atr_vals.append(sum(trs[i-p+1:i+1]) / p)
    if not atr_vals:
        return "NONE", 0.0
    n = len(atr_vals)
    hl2 = [(highs[i+1] + lows[i+1]) / 2 for i in range(n)]
    upper_basic = [hl2[i] + mult * atr_vals[i] for i in range(n)]
    lower_basic = [hl2[i] - mult * atr_vals[i] for i in range(n)]
    upper = list(upper_basic); lower = list(lower_basic)
    for i in range(1, n):
        upper[i] = min(upper_basic[i], upper[i-1]) if closes[i+p-1] <= upper[i-1] else upper_basic[i]
        lower[i] = max(lower_basic[i], lower[i-1]) if closes[i+p-1] >= lower[i-1] else lower_basic[i]
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
    if len(candles) < p+2: return "NONE", 0.0
    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    at = atr(candles, p)
    if at == 0: return "NONE", 0.0
    highest_high = max(highs[-p:]); lowest_low = min(lows[-p:])
    ce_long  = highest_high - mult * at
    ce_short = lowest_low   + mult * at
    price = closes[-1]; prev = closes[-2] if len(closes) >= 2 else price
    if price > ce_long and prev <= ce_long:
        gap = (price - ce_long) / max(at, 0.0001)
        return "BUY", min(0.90, 0.78 + min(gap * 0.04, 0.12))
    elif price < ce_short and prev >= ce_short:
        gap = (ce_short - price) / max(at, 0.0001)
        return "SELL", min(0.90, 0.78 + min(gap * 0.04, 0.12))
    elif price > ce_long: return "BUY", 0.75
    elif price < ce_short: return "SELL", 0.75
    return "NONE", 0.0

def heikin_ashi_trend(candles, lookback=5):
    if len(candles) < lookback + 3: return "NONE", 0.0
    ha = []
    prev_o = (candles[0]["open"] + candles[0]["close"]) / 2
    prev_c = (candles[0]["open"] + candles[0]["high"] + candles[0]["low"] + candles[0]["close"]) / 4
    for c in candles:
        ha_c = (c["open"] + c["high"] + c["low"] + c["close"]) / 4
        ha_o = (prev_o + prev_c) / 2
        ha_h = max(c["high"], ha_o, ha_c)
        ha_l = min(c["low"],  ha_o, ha_c)
        ha.append({"open": ha_o, "high": ha_h, "low": ha_l, "close": ha_c})
        prev_o = ha_o; prev_c = ha_c
    recent = ha[-lookback:]
    bullish = [b for b in recent if b["close"] > b["open"]]
    bearish = [b for b in recent if b["close"] < b["open"]]
    if len(bullish) == lookback:
        bodies = [abs(b["close"] - b["open"]) for b in bullish]
        growing = bodies[-1] >= bodies[0] * 0.7
        return "BUY", (0.83 if growing else 0.77)
    if len(bearish) == lookback:
        bodies = [abs(b["close"] - b["open"]) for b in bearish]
        growing = bodies[-1] >= bodies[0] * 0.7
        return "SELL", (0.83 if growing else 0.77)
    if len(bullish) >= lookback - 1: return "BUY", 0.72
    if len(bearish) >= lookback - 1: return "SELL", 0.72
    return "NONE", 0.0

def vwap_signal(candles, lookback=20):
    if len(candles) < lookback: return "NONE", 0.0
    recent = candles[-lookback:]
    total_pv = 0.0; total_v = 0.0
    for c in recent:
        typ = (c["high"] + c["low"] + c["close"]) / 3
        vol = c.get("volume", 1000)
        total_pv += typ * vol; total_v += vol
    if total_v == 0: return "NONE", 0.0
    vwap = total_pv / total_v
    price = candles[-1]["close"]; at = atr(candles, 14)
    if at == 0: return "NONE", 0.0
    dist_pct = (price - vwap) / max(at, 0.0001)
    if dist_pct > 0.3:
        conf = min(0.88, 0.72 + min(dist_pct * 0.03, 0.16))
        return "BUY", conf
    elif dist_pct < -0.3:
        conf = min(0.88, 0.72 + min(abs(dist_pct) * 0.03, 0.16))
        return "SELL", conf
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
    k=stoch_k(c); kp=stoch_k(c[:-1]) if len(c)>1 else k
    e50=ema(cl,50) if len(cl)>=50 else None
    r=rsi(cl)
    if k>kp and k<30 and (not e50 or cl[-1]>e50[-1]*0.997): return "BUY", 0.80
    if k<kp and k>70 and (not e50 or cl[-1]<e50[-1]*1.003): return "SELL", 0.80
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
