"""
╔══════════════════════════════════════════════════════════════╗
║              BONHEURBOT PRO — QUOTEX EDITION                 ║
║         Multi-User Trading Bot — Quotex (Binary)             ║
║   Confluence Strategies | Martingale | 3-Loss Pause          ║
║   FIX: pyquotex 1.1.0 API kòrèk + verifikasyon estrik        ║
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
WHATSAPP    = "50942867885"

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
                "quotex_api": None, "account_type": "PRACTICE",
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

# ═══════════════════════════════════════════════════════════
# CONFLUENCE ELITE
# ═══════════════════════════════════════════════════════════
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
    return {
        "pp":pp, "r1":r1, "r2":r2,
        "s1":s1, "s2":s2,
        "fib_r1":pp+0.382*rng, "fib_r2":pp+0.618*rng,
        "fib_s1":pp-0.382*rng, "fib_s2":pp-0.618*rng,
    }

def pivot_signal(candles, trend):
    pv = calc_pivot_points(candles)
    if not pv: return False, 0.0
    price = candles[-1]["close"]; tol = 0.008
    if trend == "TRENDING_UP":
        for lvl in [pv["s1"], pv["s2"], pv["fib_s1"], pv["fib_s2"], pv["pp"]]:
            if abs(price - lvl) / max(lvl, 0.0001) < tol:
                bonus = 0.07 if lvl in (pv["s1"], pv["fib_s1"]) else 0.05
                return True, bonus
    elif trend == "TRENDING_DN":
        for lvl in [pv["r1"], pv["r2"], pv["fib_r1"], pv["fib_r2"], pv["pp"]]:
            if abs(price - lvl) / max(lvl, 0.0001) < tol:
                bonus = 0.07 if lvl in (pv["r1"], pv["fib_r1"]) else 0.05
                return True, bonus
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
    NEW_WEIGHT = 2.5
    if st_sig == "BUY"  and st_conf >= min_per_conf:
        buy_score  += st_conf * NEW_WEIGHT; buy_cnt  += 1
    elif st_sig == "SELL" and st_conf >= min_per_conf:
        sell_score += st_conf * NEW_WEIGHT; sell_cnt += 1
    if ha_sig == "BUY"  and ha_conf >= min_per_conf:
        buy_score  += ha_conf * NEW_WEIGHT; buy_cnt  += 1
    elif ha_sig == "SELL" and ha_conf >= min_per_conf:
        sell_score += ha_conf * NEW_WEIGHT; sell_cnt += 1
    if ce_sig == "BUY"  and ce_conf >= min_per_conf:
        buy_score  += ce_conf * NEW_WEIGHT; buy_cnt  += 1
    elif ce_sig == "SELL" and ce_conf >= min_per_conf:
        sell_score += ce_conf * NEW_WEIGHT; sell_cnt += 1
    if vw_sig == "BUY"  and vw_conf >= min_per_conf:
        buy_score  += vw_conf * 1.8; buy_cnt  += 1
    elif vw_sig == "SELL" and vw_conf >= min_per_conf:
        sell_score += vw_conf * 1.8; sell_cnt += 1
    for fn, w in classic_fns:
        try:
            s, conf = fn(c)
            if s=="BUY" and conf>=min_per_conf:
                buy_score+=conf*w; buy_cnt+=1
            elif s=="SELL" and conf>=min_per_conf:
                sell_score+=conf*w; sell_cnt+=1
        except: pass
    if regime == "VOLATILE": return "NONE", 0
    dom_ratio = 1.15
    if regime == "RANGING":
        new_sigs = [st_sig, ha_sig, ce_sig]
        buy_new  = sum(1 for s in new_sigs if s == "BUY")
        sell_new = sum(1 for s in new_sigs if s == "SELL")
        if buy_new >= 2 and buy_cnt >= min_strats:
            if buy_score > sell_score * dom_ratio:
                _, piv_bonus = pivot_signal(c, "TRENDING_UP")
                final = min(0.92, 0.74 + (buy_score / max(buy_cnt, 1) / 5.0) * 0.12 + piv_bonus)
                return "BUY", round(final, 3)
        if sell_new >= 2 and sell_cnt >= min_strats:
            if sell_score > buy_score * dom_ratio:
                _, piv_bonus = pivot_signal(c, "TRENDING_DN")
                final = min(0.92, 0.74 + (sell_score / max(sell_cnt, 1) / 5.0) * 0.12 + piv_bonus)
                return "SELL", round(final, 3)
        return "NONE", 0
    if regime == "TRENDING_UP" and buy_cnt >= min_strats:
        if buy_score > sell_score * dom_ratio:
            _, piv_bonus = pivot_signal(c, "TRENDING_UP")
            adx_bonus = min(0.05, adx / 500)
            final = min(0.95, 0.75 + (buy_score / max(buy_cnt, 1) / 5.0) * 0.13 + piv_bonus + adx_bonus)
            return "BUY", round(final, 3)
    if regime == "TRENDING_DN" and sell_cnt >= min_strats:
        if sell_score > buy_score * dom_ratio:
            _, piv_bonus = pivot_signal(c, "TRENDING_DN")
            adx_bonus = min(0.05, adx / 500)
            final = min(0.95, 0.75 + (sell_score / max(sell_cnt, 1) / 5.0) * 0.13 + piv_bonus + adx_bonus)
            return "SELL", round(final, 3)
    return "NONE", 0

def strat_pro_elite(c):
    if len(c)<50: return "NONE",0
    cl=[x["close"] for x in c]
    hi=[x["high"] for x in c]; lo_=[x["low"] for x in c]
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50) if len(cl)>=50 else None
    if not e9 or not e21 or len(e9)<3 or len(e21)<3: return "NONE",0
    r=rsi(cl,14); at=atr(c); m,sig_=macd(cl)
    macd_hist=m-sig_
    if len(cl)>=2:
        m_prev,sig_prev=macd(cl[:-1]); macd_hist_prev=m_prev-sig_prev
    else: macd_hist_prev=0
    up_bb,mid_bb,lo_bb=bb(cl,20,2.0)
    k=stoch_k(c,14); kp=stoch_k(c[:-2]) if len(c)>2 else k
    if at==0 or not mid_bb: return "NONE",0
    atr_pct=at/mid_bb*100
    if atr_pct < 0.01 or atr_pct > 5.0: return "NONE",0
    adx,pdi,mdi=calc_adx_full(c,14)
    if adx < 12: return "NONE",0
    trend_up   = (e9[-1]>e21[-1])
    trend_down = (e9[-1]<e21[-1])
    if e50:
        trend_up   = trend_up   and cl[-1]>e50[-1]*0.998
        trend_down = trend_down and cl[-1]<e50[-1]*1.002
    if not trend_up and not trend_down: return "NONE",0
    if trend_up  and not (e9[-1]>e9[-2] or e21[-1]>e21[-2]): return "NONE",0
    if trend_down and not (e9[-1]<e9[-2] or e21[-1]<e21[-2]): return "NONE",0
    hi20=max(hi[-21:-1]); lo20=min(lo_[-21:-1])
    hi10=max(hi[-11:-1]); lo10=min(lo_[-11:-1])
    roc3=(cl[-1]-cl[-4])/max(abs(cl[-4]),0.001)*100 if len(cl)>=4 else 0
    roc5v=(cl[-1]-cl[-6])/max(abs(cl[-6]),0.001)*100 if len(cl)>=6 else 0
    last_body=abs(cl[-1]-c[-1]["open"])
    last_range=max(c[-1]["high"]-c[-1]["low"],0.00001)
    body_ratio=last_body/last_range
    st_sig, _ = supertrend(c, p=10, mult=3.0)
    if trend_up:
        score=0.0
        bo_score=0.0
        if cl[-1]>hi20 and cl[-2]<=hi20:  bo_score+=2.0
        elif cl[-1]>hi20*0.997:            bo_score+=0.8
        if cl[-1]>hi10 and cl[-2]<=hi10:  bo_score+=1.0
        elif cl[-1]>hi10*0.998:            bo_score+=0.4
        score+=min(3.5, bo_score)
        if 25<=r<=45:       score+=3.0
        elif 45<r<=55:      score+=2.0
        elif 55<r<=65:      score+=1.2
        elif r<25:          score+=2.5
        elif r<70:          score+=0.8
        macd_ok=(m>sig_ and macd_hist>macd_hist_prev)
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
        if st_sig == "BUY": score += 2.0
        in_piv, _ = pivot_signal(c, "TRENDING_UP")
        if in_piv: score += 1.5
        if score >= 5.0:
            pct = score/15.0
            conf = min(0.95, 0.76 + pct*0.25)
            if adx>=50: conf=min(0.95,conf+0.02)
            return "BUY", round(conf, 3)
    if trend_down:
        score=0.0
        bo_score=0.0
        if cl[-1]<lo20 and cl[-2]>=lo20:  bo_score+=2.0
        elif cl[-1]<lo20*1.003:            bo_score+=0.8
        if cl[-1]<lo10 and cl[-2]>=lo10:  bo_score+=1.0
        elif cl[-1]<lo10*1.002:            bo_score+=0.4
        score+=min(3.5, bo_score)
        if 55<=r<=75:       score+=3.0
        elif 45<=r<55:      score+=2.0
        elif 35<=r<45:      score+=1.2
        elif r>75:          score+=2.5
        elif r>30:          score+=0.8
        macd_ok=(m<sig_ and macd_hist<macd_hist_prev)
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
        if st_sig == "SELL": score += 2.0
        in_piv, _ = pivot_signal(c, "TRENDING_DN")
        if in_piv: score += 1.5
        if score >= 5.0:
            pct = score/15.0
            conf = min(0.95, 0.76 + pct*0.25)
            if adx>=50: conf=min(0.95,conf+0.02)
            return "SELL", round(conf, 3)
    return "NONE", 0

STRATEGIES={
    "confluence":strat_confluence_elite,
    "pro_elite":strat_pro_elite,
    "supertrend":supertrend,
    "heikin_ashi":heikin_ashi_trend,
    "chandelier":chandelier_exit,
    "ai":strat_ai,
    "ema":strat_ema,"fibonacci":strat_fibonacci,
    "fvg":strat_fvg,"rsi":strat_rsi,
    "macd_bollinger":strat_macd,"breakout":strat_breakout,
    "smc":strat_smc,"order_block":strat_ob,
    "stoch_ema":strat_stoch,"scalping_pro":strat_scalping,
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
    return {
        "trades":tot,"wins":wins,"losses":losses,
        "win_rate":round(wins/tot*100,1) if tot else 0,
        "net_pnl":net,"return_pct":round(net/equity[0]*100,2),
        "max_dd":round(dd,2),"pf":round(gp/gl,2) if gl else 999,
        "sharpe":round(avg/std*math.sqrt(252),2) if std and std>0 else 0,
        "equity":equity[-50:],
    }

# ═══════════════════════════════════════════════════════════
# QUOTEX CLIENT — pyquotex 1.1.0 API KÒRÈK
# ✅ connect() retounen (bool, str) — pa tuple unpack erè
# ✅ get_balance() retounen float dirèkteman (pa need float())
# ✅ get_payout_by_asset(asset, timeframe="1") — signature kòrèk
# ✅ buy(amount, asset, direction, duration) — "call"/"put"
# ✅ check_win(order_id, duration) — retounen (str, float)
# ✅ set_account_mode("PRACTICE" | "REAL")
# ═══════════════════════════════════════════════════════════
class QuotexClient:
    def __init__(self, email, password, is_demo=True):
        self.email    = email
        self.password = password
        self.is_demo  = is_demo
        self.client   = None
        self.loop     = None
        self.thread   = None
        self._bal     = 0.0
        self._connected = False

    def _start_loop(self):
        self.loop = asyncio.new_event_loop()
        def _run():
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()
        self.thread = threading.Thread(target=_run, daemon=True)
        self.thread.start()
        time.sleep(0.2)

    def _run(self, coro, timeout=30):
        if not self.loop or not self.loop.is_running():
            raise Exception("Event loop pa ap kouri — rekonekte")
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout=timeout)

    def connect(self):
        """
        Konekte ak Quotex epi verifye balans.
        Leve Exception si: login echwe, websocket echwe, balans <= 0
        Retounen balans (float) si koneksyon reyèl.
        """
        try:
            from pyquotex.stable_api import Quotex
        except ImportError as e:
            raise Exception(f"pyquotex pa enstale: {e}")

        self._start_loop()

        async def _do_connect():
            # Kreye kliyan
            self.client = Quotex(
                email=self.email,
                password=self.password,
                lang="pt"
            )
            # Mete mode kont (PRACTICE oswa REAL)
            mode = "PRACTICE" if self.is_demo else "REAL"
            self.client.set_account_mode(mode)

            # ── connect() retounen (bool, str) ──────────────────────────
            check, reason = await self.client.connect()
            if not check:
                raise Exception(
                    f"Login Quotex echwe — email oswa password ou pa bon. "
                    f"Verifye nan sit Quotex la direkteman. (raison: {reason})"
                )

            # ── verifye websocket ────────────────────────────────────────
            try:
                is_ok = await self.client.check_connect()
                if is_ok is False:
                    raise Exception(
                        "Quotex websocket pa otantifye — "
                        "email oswa password ou pa bon."
                    )
            except Exception as ck_err:
                err_str = str(ck_err)
                if "pa bon" in err_str or "websocket" in err_str or "otantifye" in err_str:
                    raise
                # Metòd pa disponib nan vèsyon sa — kontinye

            # ── jwenn balans reyèl ───────────────────────────────────────
            await asyncio.sleep(2.0)
            # get_balance() nan 1.1.0 retounen float dirèkteman
            bal = await self.client.get_balance()

            if bal is None or float(bal) <= 0:
                raise Exception(
                    "Balans $0 oswa vid apre koneksyon — "
                    "email/password ou pa bon, oswa kont ou vid nèt. "
                    "Konekte nan sit Quotex la e verifye kont ou."
                )
            return float(bal)

        try:
            bal = self._run(_do_connect(), timeout=60)
        except Exception as e:
            self._connected = False
            self.client = None
            try:
                if self.loop:
                    self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass
            raise Exception(str(e))

        self._connected = True
        self._bal = bal
        return bal

    def get_candles(self, asset, count=200, gran=60):
        """
        Jwenn chandèl yo depi Quotex.
        Retounen lis dict: {open, high, low, close, volume}
        """
        if not self._connected:
            raise Exception("Pa konekte ak Quotex")

        async def _get():
            try:
                # get_candle_v2(asset, period, timeout=30)
                candles = await self.client.get_candle_v2(asset, gran, timeout=25)
            except Exception as e:
                logger.error(f"get_candle_v2 erè: {e}")
                candles = None
            return candles or []

        raw = self._run(_get(), timeout=35)
        if not raw:
            return []

        out = []
        for c in raw[-count:]:
            try:
                out.append({
                    "open":   float(c.get("open",  c.get("o", 0))),
                    "high":   float(c.get("high",  c.get("h", 0))),
                    "low":    float(c.get("low",   c.get("l", 0))),
                    "close":  float(c.get("close", c.get("c", 0))),
                    "volume": float(c.get("volume", c.get("v", 1000)) or 1000),
                })
            except Exception:
                continue
        return out

    def place_trade(self, asset, direction, amount=1.0, duration_secs=60):
        """
        Pase yon trade binary options sou Quotex.
        direction: "BUY" → "call" | "SELL" → "put"
        Retounen (status: bool, info: Any)
        """
        if not self._connected:
            raise Exception("Pa konekte ak Quotex — rekonekte epi eseye ankò")

        async def _buy():
            d = "call" if direction == "BUY" else "put"
            # buy(amount, asset, direction, duration) — duration an segonn
            status, info = await self.client.buy(
                float(amount), asset, d, int(duration_secs)
            )
            return status, info

        return self._run(_buy(), timeout=duration_secs + 20)

    def check_win(self, order_id, duration_secs=60):
        """
        Verifye rezilta yon trade.
        Retounen (status: str, profit: float)
        status: "win" | "loss" | "equal"
        """
        async def _check():
            # check_win(order_id, duration=0) — retounen (str, float)
            status, profit = await self.client.check_win(order_id, int(duration_secs))
            return status, profit

        return self._run(_check(), timeout=duration_secs + 35)

    def get_balance_sync(self):
        """
        Jwenn balans aktyèl la.
        Retounen float — $0 oswa erè = koneksyon pèdi.
        """
        if not self._connected:
            return self._bal

        async def _bal():
            # get_balance(timeout=30) — retounen float
            return await self.client.get_balance(timeout=20)

        try:
            b = self._run(_bal(), timeout=25)
            if b is not None:
                b = float(b)
                if b > 0:
                    self._bal = b
                else:
                    logger.warning("get_balance_sync: balans $0 — koneksyon ka pèdi")
        except Exception as e:
            logger.error(f"get_balance_sync: {e}")
        return self._bal

    def get_payout(self, asset):
        """
        Jwenn pousantaj payout pou aktif la.
        Retounen float (ex: 0.85 = 85%)
        """
        if not self._connected:
            return 0.85

        try:
            # get_payout_by_asset(asset_name, timeframe="1")
            # retounen float | dict | None
            # timeframe "1" = 1 minit, "5" = 5 minit, "24H" = 24h
            data = self.client.get_payout_by_asset(asset, timeframe="1")

            if data is None:
                # eseye san timeframe
                data = self.client.get_payout_by_asset(asset)

            if isinstance(data, (int, float)) and data is not None:
                pct = float(data)
                # Si valè a se 85 (pousantaj) oswa 0.85 (desimal)
                if pct > 1:
                    return pct / 100.0
                return pct

            if isinstance(data, dict):
                # ka retounen {"turbo_payment": 85, "payment": 80, ...}
                pct = data.get("turbo_payment") or data.get("payment") or 85
                return float(pct) / 100.0 if float(pct) > 1 else float(pct)

        except Exception as e:
            logger.error(f"get_payout: {e}")

        return 0.85  # valè defo si echwe

    def close(self):
        """Fèmen koneksyon an pwòpman."""
        self._connected = False
        try:
            if self.client:
                async def _close():
                    await self.client.close()
                self._run(_close(), timeout=10)
        except Exception:
            pass
        try:
            if self.loop and self.loop.is_running():
                self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass

    @property
    def balance(self):
        return self._bal

    @property
    def connected(self):
        return self._connected


# ═══════════════════════════════════════════════════════════
# LOG HELPER
# ═══════════════════════════════════════════════════════════
def add_log(st, msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    st["log"].insert(0, {"time": ts, "msg": msg, "level": level})
    st["log"] = st["log"][:80]
    logger.info(f"[{st['uid'][:8]}] {msg}")


# ═══════════════════════════════════════════════════════════
# QUOTEX TRADING LOOP — Binary Options + Martingale
# ═══════════════════════════════════════════════════════════
def quotex_trading_loop(st, bot_id=None):
    if bot_id and st.get("bot_id") != bot_id:
        return

    cfg       = st["config"]
    asset     = cfg.get("asset", "EURUSD_otc")
    strategy  = cfg.get("strategy", "confluence")
    lot       = float(cfg.get("lot", 1.0))
    duration  = int(cfg.get("duration", 60))
    min_conf  = float(cfg.get("min_conf", 0.65))
    mart_mult = float(cfg.get("martingale", 2.0))

    fn = STRATEGIES.get(strategy, strat_confluence_elite)

    base_lot    = round(max(1.0, lot), 2)
    current_lot = base_lot
    consec_losses = 0
    total_lost    = 0.0
    MAX_LOSSES_BEFORE_PAUSE = 3
    PAUSE_WAIT_SECS         = 45

    add_log(st, f"🚀 BonheurBot Quotex | {asset} | {strategy} | Dire:{duration}s | Mise:${base_lot}")
    add_log(st, f"📌 Confluence ELITE: SuperTrend+HA+Chandelier | ADX>12 | Martingale x{mart_mult}")

    while st["running"]:
        if bot_id and st.get("bot_id") != bot_id:
            add_log(st, "⏹ Bot anile", "WARN")
            return

        # ── Verifye objektif/limit ───────────────────────────────────────
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
            if not api or not api.connected:
                add_log(st, "Quotex pa konekte — STOP", "ERROR")
                st["running"] = False; break

            # ── Aktyalize balans ─────────────────────────────────────────
            try:
                b = api.get_balance_sync()
                if b and b > 0:
                    st["balance"] = b
            except Exception:
                add_log(st, "⚠ Koneksyon pèdi — tann...", "WARN")
                time.sleep(15); continue

            if st["balance"] < current_lot:
                add_log(st, f"⚠ Balans ${st['balance']:.2f} < Mise ${current_lot:.2f} — reset", "WARN")
                current_lot = base_lot; consec_losses = 0; total_lost = 0.0
                time.sleep(10); continue

            # ── Jwenn chandèl yo ─────────────────────────────────────────
            candles = api.get_candles(asset, 200, 60)
            if len(candles) < 30:
                add_log(st, f"Pa ase done ({len(candles)}) — tann...", "WARN")
                time.sleep(15); continue

            # ── Analiz mache ─────────────────────────────────────────────
            regime, regime_score = market_regime(candles)
            adx_val, pdi_val, mdi_val = calc_adx_full(candles, 14)
            st_sig, st_c = supertrend(candles)
            ha_sig, ha_c = heikin_ashi_trend(candles)

            add_log(st,
                f"📡 {len(candles)} bouji | {asset} | {regime} | ADX:{adx_val:.0f} | "
                f"ST:{st_sig}({st_c:.0%}) | HA:{ha_sig}({ha_c:.0%})")

            # ── Pause si 3 pèt konsekitif ────────────────────────────────
            if consec_losses >= MAX_LOSSES_BEFORE_PAUSE:
                mache_bon = regime in ("TRENDING_UP", "TRENDING_DN", "RANGING") and adx_val >= 12
                if regime == "RANGING":
                    mache_bon = (st_sig != "NONE") and (ha_sig != "NONE") and adx_val >= 10
                if not mache_bon:
                    add_log(st,
                        f"⏸ PÒZ APRE {consec_losses} PÈT | Mache:{regime}(ADX:{adx_val:.0f}) — "
                        f"Ap tann siyal... ({PAUSE_WAIT_SECS}sek)", "WARN")
                    time.sleep(PAUSE_WAIT_SECS); continue
                else:
                    add_log(st, f"✅ MACHE BON ANKÒ! {regime} ADX:{adx_val:.0f} | Reprann ${current_lot:.2f}", "SUCCESS")

            # ── Evite mache VOLATILE ─────────────────────────────────────
            if regime == "VOLATILE":
                add_log(st, f"⏸ Mache VOLATILE — pa trade. Tann {min(duration,120)}sek...", "WARN")
                time.sleep(min(duration, 120)); continue

            # ── Kalkile siyal ────────────────────────────────────────────
            if strategy == "confluence":
                req_strats = 3 if consec_losses == 0 else (4 if consec_losses <= 2 else 5)
                sig, conf = strat_confluence_elite(candles, min_strats=req_strats, min_per_conf=0.65)
                add_log(st, f"📊 {asset} | {sig} | Conf:{conf:.0%} | Elite({req_strats}strat)")
            else:
                sig, conf = fn(candles)
                add_log(st, f"📊 {asset} | {sig} | Conf:{conf:.0%} | {strategy}")

            # ── Filtre kontra-trend ──────────────────────────────────────
            if sig == "BUY" and regime == "TRENDING_DN":
                add_log(st, f"⛔ REJTE BUY — Mache ap DESANN. {st_sig}/{ha_sig}", "WARN")
                time.sleep(duration); continue
            if sig == "SELL" and regime == "TRENDING_UP":
                add_log(st, f"⛔ REJTE SELL — Mache ap MONTE. {st_sig}/{ha_sig}", "WARN")
                time.sleep(duration); continue

            # ── Konfidans adaptif ────────────────────────────────────────
            adaptive_conf = min_conf + (0.02 if consec_losses == 1 else (0.04 if consec_losses >= 2 else 0))
            if sig == "NONE" or conf < adaptive_conf:
                reason = "Pa gen siyal" if sig == "NONE" else f"Conf {conf:.0%} < {adaptive_conf:.0%}"
                add_log(st, f"⏭ {reason} — tann pwochen bouji...")
                time.sleep(duration); continue

            # ── Pivot info ───────────────────────────────────────────────
            pv_sig_dir = "TRENDING_UP" if sig == "BUY" else "TRENDING_DN"
            in_pivot, _ = pivot_signal(candles, pv_sig_dir)
            pivot_info = " 🎯+PIVOT" if in_pivot else ""

            # ── Payout ───────────────────────────────────────────────────
            payout = api.get_payout(asset)
            entry  = candles[-1]["close"]

            add_log(st,
                f"⚡ {sig} @ {entry:.5f} | Conf:{conf:.0%} | ADX:{adx_val:.0f} | "
                f"Payout:{payout:.0%} | Mise:${current_lot:.2f}{pivot_info}")

            # ── Pase trade ───────────────────────────────────────────────
            try:
                status, info = api.place_trade(asset, sig, current_lot, duration)
            except Exception as e:
                add_log(st, f"❌ Trade echwe: {e}", "ERROR")
                time.sleep(10); continue

            if not status:
                add_log(st, f"❌ Trade echwe: {info}", "ERROR")
                time.sleep(10); continue

            # Ekstrè trade ID
            trade_id = None
            if isinstance(info, dict):
                trade_id = info.get("id") or info.get("order_id")
            elif isinstance(info, (int, str)):
                trade_id = info

            add_log(st, f"⏳ #{trade_id} ouvri | Ap tann {duration}s...", "SUCCESS")
            time.sleep(duration + 3)

            # ── Verifye rezilta ──────────────────────────────────────────
            pnl = 0.0; won = False
            if trade_id:
                try:
                    # check_win retounen (str, float): ("win"|"loss"|"equal", profit)
                    result, profit = api.check_win(trade_id, duration)
                    profit = float(profit) if profit is not None else 0.0
                    if result in ("win", "won"):
                        pnl = round(profit, 2) if profit > 0 else round(current_lot * payout, 2)
                        won = True
                    elif result in ("loss", "loose", "lost"):
                        pnl = -current_lot
                        won = False
                    elif result == "equal":
                        pnl = 0.0
                        won = False
                    else:
                        # rezilta enkoni — itilize balans
                        nb = api.get_balance_sync()
                        pnl = round(nb - st["balance"], 2)
                        won = pnl > 0
                        st["balance"] = nb
                except Exception as e:
                    add_log(st, f"check_win erè: {e}", "WARN")
                    nb = api.get_balance_sync()
                    pnl = round(nb - st["balance"], 2)
                    won = pnl > 0
                    st["balance"] = nb
            else:
                # Pa gen ID — kalkile depi balans
                nb = api.get_balance_sync()
                pnl = round(nb - st["balance"], 2)
                won = pnl > 0
                st["balance"] = nb

            # ── Afiche rezilta ───────────────────────────────────────────
            if won:
                if pnl <= 0:
                    pnl = round(current_lot * payout, 2)
                add_log(st, f"✅ GENYEN! +${pnl:.2f}", "SUCCESS")
            else:
                if pnl >= 0 and not won:
                    pnl = -current_lot
                add_log(st, f"❌ PÈDI ${abs(pnl):.2f}", "WARN")

            st["total_pnl"] += pnl

            # Aktyalize balans apre trade
            try:
                nb = api.get_balance_sync()
                if nb and nb > 0:
                    st["balance"] = nb
            except Exception:
                pass

            stake_used = current_lot

            # ── Martingale / Reset ───────────────────────────────────────
            if won:
                prev_losses = consec_losses
                current_lot   = base_lot
                consec_losses = 0
                total_lost    = 0.0
                if prev_losses > 0:
                    add_log(st, f"🏆 REKIPERE! (te gen {prev_losses} pèt) ← Reset ${base_lot:.2f}", "SUCCESS")
            else:
                total_lost    += current_lot
                consec_losses += 1
                next_lot       = round(current_lot * mart_mult, 2)
                current_lot    = max(base_lot, min(next_lot, 500.0))
                if consec_losses < MAX_LOSSES_BEFORE_PAUSE:
                    add_log(st,
                        f"⚠ PÈT #{consec_losses}/{MAX_LOSSES_BEFORE_PAUSE-1} | "
                        f"Total:${total_lost:.2f} | Prochèn:${current_lot:.2f}", "WARN")
                else:
                    add_log(st,
                        f"🚨 {consec_losses} PÈT AFILE! PÒZE OTOMATIK | "
                        f"Total:${total_lost:.2f} | Mise rekipere:${current_lot:.2f} | Ap tann mache...", "WARN")

            # ── Anrejistre trade ─────────────────────────────────────────
            trade = {
                "id":       len(st["trades"]) + 1,
                "time":     datetime.now().strftime("%H:%M:%S"),
                "asset":    asset,
                "side":     sig,
                "entry":    round(entry, 5),
                "conf":     f"{conf:.0%}",
                "strategy": strategy,
                "duration": f"{duration}s",
                "stake":    round(stake_used, 2),
                "pnl":      round(pnl, 2),
                "status":   "won" if won else "lost",
                "regime":   regime,
            }
            st["trades"].insert(0, trade)

        except Exception as e:
            add_log(st, f"Erè: {e}", "ERROR")
            time.sleep(15)

    add_log(st, "⏹ BonheurBot Quotex arrêté")


# ═══════════════════════════════════════════════════════════
# API ROUTES
# ═══════════════════════════════════════════════════════════
@app.route("/api/connect", methods=["POST"])
def api_connect():
    st = get_state()
    if not st.get("access"):
        return jsonify({"ok": False, "error": "⚠ Ou bezwen yon kòd aksè valid!"})
    try:
        d        = request.json or {}
        email    = d.get("email", "").strip()
        password = d.get("password", "").strip()
        is_demo  = bool(d.get("is_demo", True))

        if not email or not password:
            return jsonify({"ok": False, "error": "Email ak password obligatwa"})

        # Fèmen ansyen koneksyon an
        old_api = st.get("quotex_api")
        if old_api:
            try:
                old_api.close()
            except Exception:
                pass
            st["quotex_api"] = None
            st["connected"]  = False
            st["balance"]    = 0.0

        add_log(st, f"⏳ Ap konekte Quotex ({email[:3]}***) — tann 30-60sek...")

        api = QuotexClient(email, password, is_demo)
        try:
            bal = api.connect()
        except Exception as conn_err:
            st["connected"]  = False
            st["quotex_api"] = None
            st["balance"]    = 0.0
            err_msg = str(conn_err)
            add_log(st, f"✗ Koneksyon echwe: {err_msg[:150]}", "ERROR")
            return jsonify({"ok": False, "error": err_msg})

        # Sèlman rive isit si bal > 0
        st["quotex_api"]   = api
        st["balance"]      = bal
        st["connected"]    = True
        st["account_type"] = "PRACTICE" if is_demo else "REAL"
        mode_label = "DEMO" if is_demo else "REYÈL"
        add_log(st, f"✅ Konekte Quotex ({mode_label}) | Balans: ${bal:.2f}", "SUCCESS")
        return jsonify({"ok": True, "balance": bal, "mode": mode_label})

    except Exception as e:
        logger.error(f"api_connect inatandi: {e}", exc_info=True)
        st["connected"]  = False
        st["quotex_api"] = None
        st["balance"]    = 0.0
        add_log(st, f"✗ Erè inatandi: {str(e)[:150]}", "ERROR")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/start", methods=["POST"])
def api_start():
    st = get_state()
    if not st.get("access"):
        return jsonify({"ok": False, "error": "⚠ Ou bezwen yon kòd aksè valid!"})
    api = st.get("quotex_api")
    if not st["connected"] or not api or not api.connected:
        return jsonify({"ok": False, "error": "Konekte kont Quotex ou anvan! (email/password)"})
    if st["running"]:
        return jsonify({"ok": False, "error": "Bot déjà ap kouri"})

    d = request.json or {}
    dur_map = {"30s": 30, "1m": 60, "2m": 120, "5m": 300}
    st["config"] = {
        "asset":         d.get("asset", "EURUSD_otc"),
        "strategy":      d.get("strategy", "confluence"),
        "lot":           float(d.get("lot", 1.0)),
        "duration":      dur_map.get(d.get("duration", "1m"), 60),
        "min_conf":      float(d.get("min_conf", 0.65)),
        "martingale":    float(d.get("martingale", 2.0)),
        "profit_target": float(d.get("profit_target", 0)),
        "loss_limit":    float(d.get("loss_limit", 0)),
    }

    import random, string
    bot_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    st["running"]    = True
    st["bot_id"]     = bot_id
    st["total_pnl"]  = 0.0

    threading.Thread(target=quotex_trading_loop, args=(st, bot_id), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    st = get_state()
    st["running"] = False
    st["bot_id"]  = None
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    st = get_state()
    return jsonify({
        "connected":    st["connected"],
        "account_type": st.get("account_type", "PRACTICE"),
        "running":      st["running"],
        "balance":      round(st["balance"], 2),
        "pnl":          round(st["total_pnl"], 2),
        "trades":       st["trades"][:20],
        "log":          st["log"][:30],
        "config":       st["config"],
    })


@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    st = get_state()
    try:
        d     = request.json or {}
        asset = d.get("asset", "EURUSD_otc")
        strat = d.get("strategy", "confluence")
        api   = st.get("quotex_api")
        if not api or not api.connected:
            return jsonify({"ok": False, "error": "Konekte Quotex anvan!"})
        candles = api.get_candles(asset, 500, 60)
        if len(candles) < 100:
            return jsonify({"ok": False, "error": f"Pa ase done ({len(candles)})"})
        r = run_backtest(
            candles, strat,
            float(d.get("balance", 10000)),
            float(d.get("lot", 0.01)),
            float(d.get("sl", 20)),
            float(d.get("tp", 40))
        )
        return jsonify({"ok": True, "result": r})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/login", methods=["POST"])
def api_login():
    st = get_state()
    d  = request.json or {}
    token = d.get("session_token", "").strip()
    code  = d.get("code", "").strip().upper()

    if token:
        ok, msg_text = validate_session(token)
        if ok:
            with _sess_lock:
                is_adm = _sessions.get(token, {}).get("is_admin", False)
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
        is_adm = ACCESS_CODES.get(code, {}).get("is_adm", False) or \
                 ACCESS_CODES.get(code, {}).get("created_at") is None
        with _sess_lock:
            _sessions[new_token]["is_admin"] = is_adm
            _save_sessions()
        st["access"] = True; st["session_token"] = new_token; st["is_admin"] = is_adm
        msg_out = "✓ Aksè Admin! 30 jou rete" if is_adm else "✓ Aksè akòde! 30 jou rete"
        return jsonify({"ok": True, "msg": msg_out, "session_token": new_token,
                        "expire": expire, "is_admin": is_adm})

    return jsonify({"ok": False, "msg": msg_text, "need_code": True})


def require_admin(d):
    token = d.get("admin_token", "").strip()
    if not token: return False
    with _sess_lock:
        sess = _sessions.get(token)
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
        codes.append({
            "code": c, "status": status, "remaining": remaining,
            "used": entry["used"],
            "is_adm": entry.get("is_adm", False) or entry["created_at"] is None
        })
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
    typ = "Admin" if is_adm else "Itilizatè (1 mwa)"
    return jsonify({"ok": True, "msg": f"✓ Kòd {code} kreye [{typ}]"})


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
                "uid":      uid[:8] + "...",
                "connected": st.get("connected", False),
                "running":   st.get("running", False),
                "balance":   round(st.get("balance", 0), 2),
                "pnl":       round(st.get("total_pnl", 0), 2),
                "trades":    len(st.get("trades", [])),
                "asset":     st.get("config", {}).get("asset", "—"),
                "strategy":  st.get("config", {}).get("strategy", "—"),
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
            sessions.append({
                "token":    token[:8] + "...",
                "expire":   sess["expire"],
                "days_left": (exp - today).days,
                "is_admin": sess.get("is_admin", False),
                "active":   (exp - today).days > 0,
            })
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
                st["trades"] = []; st["total_pnl"] = 0.0; st["log"] = []
                cleared += 1
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
                st["trades"] = []
                cleared += 1
    return jsonify({"ok": True, "msg": f"✓ {cleared} itilizatè: trades efase (log + pnl konsève)"})


@app.route("/")
def index():
    return render_template_string(HTML)


# ═══════════════════════════════════════════════════════════
# HTML INTERFACE
# ═══════════════════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>💰 BonheurBot — Quotex Edition</title>
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

<div id="login-page" style="display:none;min-height:100vh;background:#040A0F;align-items:center;justify-content:center;flex-direction:column">
  <div style="background:#071219;border:1px solid #0D2233;border-radius:12px;padding:40px;max-width:420px;width:90%;text-align:center">
    <div style="font-size:32px;margin-bottom:8px">💰</div>
    <div style="font-size:20px;font-weight:900;color:#00FF88;letter-spacing:2px;margin-bottom:4px">BonheurBot Pro</div>
    <div style="color:#4A7080;font-size:11px;margin-bottom:24px">Quotex Edition — Multi-User</div>
    <div style="margin-bottom:16px">
      <div style="color:#4A7080;font-size:10px;letter-spacing:1px;margin-bottom:6px;text-align:left">KÒD AKSÈ</div>
      <input id="login-code" type="text" placeholder="BB-XXXX-XXXX" style="width:100%;background:#020C12;border:1px solid #0D2233;color:#C8E8F0;border-radius:6px;padding:10px 12px;font-size:13px;font-family:inherit;outline:none;box-sizing:border-box;text-transform:uppercase">
    </div>
    <div id="login-err"></div>
    <button id="login-btn" onclick="doLogin()" style="width:100%;background:#00FF8818;border:1px solid #00FF88;color:#00FF88;border-radius:6px;padding:11px;cursor:pointer;font-size:13px;font-family:inherit;font-weight:700;letter-spacing:1px">⚡ ANTRE</button>
    <div style="margin-top:20px;background:#020C12;border:1px solid #0D2233;border-radius:8px;padding:14px;text-align:left">
      <div style="color:#FFD600;font-size:10px;letter-spacing:1px;font-weight:700;margin-bottom:8px">💳 ABÒNMAN — $__SUB_PRICE__/MWA</div>
      <div style="color:#4A7080;font-size:10px;line-height:1.9">
        Pou jwenn yon kòd aksè, voye prèv peman ($__SUB_PRICE__) sou WhatsApp:<br><br>
        <a href="https://wa.me/__WHATSAPP__" target="_blank" style="display:inline-flex;align-items:center;gap:6px;margin-top:6px;background:#25D36618;border:1px solid #25D36644;color:#25D366;border-radius:6px;padding:6px 12px;text-decoration:none;font-size:11px;font-weight:700">
          📱 WhatsApp: +509 4286-7885
        </a>
        <div style="margin-top:8px;color:#3A6070">Admin ap kreye epi voye kòd aksè ou apre verifikasyon.</div>
      </div>
    </div>
  </div>
</div>

<div id="app-page" style="display:none">
<div class="hdr">
  <div style="display:flex;align-items:center;gap:12px">
    <div class="logo">💰 Bonheur<span>Bot</span> <span style="font-size:10px;color:#FFD600">QUOTEX</span></div>
    <div style="width:1px;height:20px;background:#0D2233"></div>
    <span id="hb" class="tag tg">DISCONNECTED</span>
    <span id="hacc" class="tag tg" style="font-size:10px">—</span>
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
    <div class="stat"><div class="sl">NET P&L (SESYON)</div><div class="sv" id="s-pnl">+$0.00</div></div>
    <div class="stat"><div class="sl">TRADES</div><div class="sv" id="s-tr" style="color:#FFD600">0</div></div>
    <div class="stat"><div class="sl">BOT</div><div class="sv" id="s-bot" style="color:#3A6070">IDLE</div></div>
  </div>
  <div class="g2">
    <div class="box">
      <div class="bt">🔗 KONEKTE KONT QUOTEX</div>
      <div class="iw"><div class="il">EMAIL QUOTEX</div><input id="q-email" type="email" placeholder="email@gmail.com"></div>
      <div class="iw"><div class="il">PASSWORD QUOTEX</div><input id="q-pass" type="password" placeholder="••••••••"></div>
      <div class="iw"><div class="il">TIP KONT</div>
        <select id="q-demo">
          <option value="true">🧪 Demo (Pratike)</option>
          <option value="false">💰 Reyèl (Live)</option>
        </select>
      </div>
      <div id="cm"></div>
      <button class="btn b fw" onclick="doConn()">⚡ KONEKTE</button>
      <div id="cs" style="margin-top:10px"></div>
      <div style="margin-top:10px;color:#4A7080;font-size:10px;line-height:1.8">
        ⚠️ Si email/password pa bon, w ap wè yon mesaj erè klè.<br>
        ⚠️ Balans $0 = kont pa aksepte — verifye nan Quotex direkteman.<br>
        ✅ Kont reyèl konfime sèlman si balans &gt; $0 jwenn apre login.
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
        <div class="stat"><div class="sl">AKTIF</div><div id="s-sym" style="font-size:12px;font-weight:700">—</div></div>
        <div class="stat"><div class="sl">DIRE</div><div id="s-dur" style="font-size:12px;font-weight:700;color:#3A6070">—</div></div>
      </div>
    </div>
  </div>
  <div class="box" style="background:#00FF8808;border-color:#00FF8822">
    <div class="bt" style="color:#00FF88">🚀 SISTÈM ELITE — QUOTEX BINARY</div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;font-size:11px;color:#4A7080;line-height:1.9">
      <div>
        <div style="color:#00FF88;font-weight:700;margin-bottom:4px">📈 SuperTrend</div>
        ATR x3.0 multiplier<br>
        Siyal klè BUY/SELL (CALL/PUT)<br>
        <span style="color:#00FF88">Pwa: 2.5x (pi wo)</span>
      </div>
      <div>
        <div style="color:#FFD600;font-weight:700;margin-bottom:4px">🕯 Heikin Ashi</div>
        5 bouji konsekitif<br>
        Filtre bwi mache<br>
        <span style="color:#FFD600">Pwa: 2.5x (pi wo)</span>
      </div>
      <div>
        <div style="color:#00D4FF;font-weight:700;margin-bottom:4px">🔔 Chandelier Exit</div>
        Highest high/lowest low<br>
        Chanjman trend detekte<br>
        <span style="color:#00D4FF">Pwa: 2.5x (pi wo)</span>
      </div>
      <div>
        <div style="color:#FF3B6B;font-weight:700;margin-bottom:4px">🛡 Martingale + Pause</div>
        ADX sèyil: 12<br>
        3 strategies dakò minimòm<br>
        Pause 3 pèt konsekitif<br>
        <span style="color:#FF3B6B">→ Mise rekipere otomatik</span>
      </div>
    </div>
  </div>
</div>

<div id="pg-control" class="pg">
  <div class="g2">
    <div class="box">
      <div class="bt">PARAMÈT BOT — QUOTEX</div>
      <div class="g2">
        <div class="iw"><div class="il">AKTIF (OTC = louvri 24/7)</div>
          <select id="c-asset">
            <option value="EURUSD_otc" selected>EUR/USD OTC</option>
            <option value="GBPUSD_otc">GBP/USD OTC</option>
            <option value="USDJPY_otc">USD/JPY OTC</option>
            <option value="AUDCAD_otc">AUD/CAD OTC</option>
            <option value="EURJPY_otc">EUR/JPY OTC</option>
            <option value="USDCAD_otc">USD/CAD OTC</option>
            <option value="NZDUSD_otc">NZD/USD OTC</option>
            <option value="AUDUSD_otc">AUD/USD OTC</option>
            <option value="BTCUSD_otc">BTC/USD OTC</option>
            <option value="ETHUSD_otc">ETH/USD OTC</option>
            <option value="EURUSD">EUR/USD (Live)</option>
            <option value="GBPUSD">GBP/USD (Live)</option>
          </select>
        </div>
        <div class="iw"><div class="il">DIRE KONTRA</div>
          <select id="c-duration">
            <option value="30s">30 segonn</option>
            <option value="1m" selected>1 minit ★</option>
            <option value="2m">2 minit</option>
            <option value="5m">5 minit ★★★</option>
          </select>
        </div>
      </div>
      <div class="g2">
        <div class="iw"><div class="il">MISE DEPA ($) — Min $1</div><input id="c-lot" type="number" value="1" step="0.5" min="1"></div>
        <div class="iw"><div class="il">STRATEGY</div>
          <select id="c-strat">
            <option value="confluence">🔥 Confluence ELITE (ST+HA+CE)</option>
            <option value="pro_elite">🚀 Pro ELITE (score+ST)</option>
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
        <div class="iw"><div class="il">MARTINGALE x</div>
          <select id="c-mart">
            <option value="2" selected>x2 (Klasik)</option>
            <option value="2.2">x2.2</option>
            <option value="2.5">x2.5</option>
            <option value="3">x3</option>
          </select>
        </div>
      </div>
      <div class="g2">
        <div class="iw"><div class="il">🎯 STOP PROFIT ($)</div><input id="c-target" type="number" value="0" step="1" min="0"><div style="color:#00FF88;font-size:9px;margin-top:2px">0 = pa gen limit</div></div>
        <div class="iw"><div class="il">🛑 STOP LOSS ($)</div><input id="c-loss" type="number" value="0" step="1" min="0"><div style="color:#FF3B6B;font-size:9px;margin-top:2px">REKÒMANDE: toujou mete!</div></div>
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
          <div class="stat"><div class="sl">BOT</div><div id="c-st2" class="sv" style="color:#3A6070">IDLE</div></div>
          <div class="stat"><div class="sl">BALANS</div><div id="c-bal" class="sv" style="color:#00D4FF">$0.00</div></div>
        </div>
        <div class="stats">
          <div class="stat"><div class="sl">P&L NET</div><div id="c-pnl" class="sv">+$0.00</div></div>
        </div>
      </div>
      <div class="box" style="background:#00FF8808;border-color:#00FF8822">
        <div class="bt" style="color:#00FF88">🧠 LOJIK SIYAL</div>
        <div style="color:#4A7080;font-size:10px;line-height:2.1">
          <span style="color:#00FF88">✓ SuperTrend:</span> ATR×3 — CALL/PUT klè<br>
          <span style="color:#00FF88">✓ Heikin Ashi:</span> 5 bouji — trend konfime<br>
          <span style="color:#00FF88">✓ Chandelier:</span> HH/LL — chanjman detekte<br>
          <span style="color:#FFD600">⚠ 1-2 pèt:</span> Conf+2-4%, mise monte (martingale)<br>
          <span style="color:#FF3B6B">🛑 3 pèt:</span> PÒZE — tann siyal bon, reset mise<br>
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
    <div class="bt">BACKTEST ENGINE (sou done Quotex)</div>
    <div class="g3">
      <div class="iw"><div class="il">AKTIF</div><input id="bt-sy" value="EURUSD_otc"></div>
      <div class="iw"><div class="il">BALANS ($)</div><input id="bt-bl" type="number" value="10000"></div>
      <div class="iw"><div class="il">LOT SIZE</div><input id="bt-lt" type="number" value="0.50" step="0.10"></div>
      <div class="iw"><div class="il">STOP LOSS</div><input id="bt-sl" type="number" value="20"></div>
      <div class="iw"><div class="il">TAKE PROFIT</div><input id="bt-tp" type="number" value="40"></div>
    </div>
    <div class="iw"><div class="il">STRATEGY</div>
      <select id="bt-st">
        <option value="confluence">🔥 Confluence ELITE</option>
        <option value="pro_elite">🚀 Pro ELITE</option>
        <option value="supertrend">📈 SuperTrend</option>
        <option value="heikin_ashi">🕯 Heikin Ashi</option>
        <option value="chandelier">🔔 Chandelier Exit</option>
        <option value="ai">🤖 AI Score</option>
        <option value="smc">🏛 SMC</option>
        <option value="macd_bollinger">📊 MACD+BB</option>
        <option value="rsi">📉 RSI</option>
      </select>
    </div>
    <div style="color:#4A7080;font-size:10px;margin-bottom:10px">
      ⚠️ Konekte kont Quotex ou nan Dashboard anvan ou fè backtest — bezwen done bouji.
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
      <div class="bt">➕ KREYE KÒD AKSÈ ($__SUB_PRICE__/mwa)</div>
      <div class="iw"><div class="il">KÒD</div>
        <input id="new-code" type="text" placeholder="BB-2026-XXXX" oninput="this.value=this.value.toUpperCase()">
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
  <div class="box" style="background:#FFD60008;border-color:#FFD60022">
    <div class="bt" style="color:#FFD600">📊 LEJANN BOUTON AKSYON ITILIZATÈ</div>
    <div style="font-size:11px;color:#4A7080;line-height:2.0">
      <span style="background:transparent;border:1px solid #FFD60044;color:#FFD600;border-radius:3px;padding:2px 8px">📊🗑</span> — Efase <b style="color:#FFD600">trades sèlman</b> (log konsève)<br>
      <span style="background:transparent;border:1px solid #4A708044;color:#4A7080;border-radius:3px;padding:2px 8px">🗑</span> — Efase <b style="color:#C8E8F0">TOUT</b> (trades + log + pnl reset)<br>
      <span style="background:transparent;border:1px solid #FF3B6B44;color:#FF3B6B;border-radius:3px;padding:2px 8px">■ STOP</span> — Kanpe bot itilizatè a
    </div>
  </div>
</div>

</div>
</div>

<script>
const SESSION_KEY="bb_session_quotex_v2";
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

function sw(id,el){
  document.querySelectorAll(".pg").forEach(p=>p.classList.remove("on"));
  document.querySelectorAll(".tab").forEach(t=>t.classList.remove("on"));
  document.getElementById("pg-"+id).classList.add("on");
  el.classList.add("on");
}
function msg(id,txt,ok){document.getElementById(id).innerHTML=`<div class="al ${ok?"ok":"er"}">${txt}</div>`;}

async function doConn(){
  const btn=event.target;btn.textContent="AP KONEKTE...";btn.disabled=true;
  msg("cm","⏳ Ap konekte ak Quotex — tann 30-60 segonn...","ok");
  const body={
    email:document.getElementById("q-email").value.trim(),
    password:document.getElementById("q-pass").value.trim(),
    is_demo:document.getElementById("q-demo").value==="true"
  };
  try{
    const r=await fetch("/api/connect",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(d.ok){
      msg("cm",`✅ Konekte (${d.mode}) | Balans reyèl: $${d.balance.toFixed(2)}`,"ok");
      document.getElementById("cs").innerHTML=`<div class="al ok">✅ <b>Quotex ${d.mode}</b> | $${d.balance.toFixed(2)}</div>`;
    }else{
      msg("cm","✗ "+d.error,false);
      document.getElementById("cs").innerHTML=`<div class="al er">✗ ${d.error}</div>`;
    }
  }catch(e){msg("cm","✗ "+e.message,false);}
  btn.textContent="⚡ KONEKTE";btn.disabled=false;
}

function getStartParams(){
  return{
    asset:document.getElementById("c-asset").value,
    strategy:document.getElementById("c-strat").value,
    lot:parseFloat(document.getElementById("c-lot").value),
    duration:document.getElementById("c-duration").value,
    min_conf:parseFloat(document.getElementById("c-conf").value),
    martingale:parseFloat(document.getElementById("c-mart").value),
    profit_target:parseFloat(document.getElementById("c-target").value||0),
    loss_limit:parseFloat(document.getElementById("c-loss").value||0),
  };
}

async function doStart(){
  const body=getStartParams();
  const r=await fetch("/api/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const d=await r.json();
  if(d.ok){msg("ctm","✓ BonheurBot Quotex démarre!","ok");document.getElementById("bs").style.display="none";document.getElementById("bx").style.display="inline-block";}
  else msg("ctm","✗ "+d.error,false);
}
async function doStop(){
  await fetch("/api/stop",{method:"POST"});
  msg("ctm","✓ Bot arrêté","ok");
  document.getElementById("bs").style.display="inline-block";
  document.getElementById("bx").style.display="none";
}

const SI={
  confluence:{l:"🔥 Confluence ELITE",d:"SuperTrend(pwa 2.5x) + HeikinAshi(pwa 2.5x) + Chandelier(pwa 2.5x) + 11 strategies klasik. ADX≥12, 3 strat minimòm, RANGING oke si ST+HA dakò.",tags:["SuperTrend","HeikinAshi","Chandelier","ADX≥12","3 strat"]},
  pro_elite:{l:"🚀 Pro ELITE",d:"Score 5.0/15 + ADX≥12 + SuperTrend bonus 2.0pts. Plis siyal, menm presizyon.",tags:["score 5/15","ADX≥12","ST bonus"]},
  supertrend:{l:"📈 SuperTrend",d:"ATR × 3.0 multiplier. Siyal klè CALL/PUT. Travèse bann = siyal solid.",tags:["ATR×3","travèse=BUY","conf 75-92%"]},
  heikin_ashi:{l:"🕯 Heikin Ashi",d:"5 bouji konsekitif menm direksyon = trend solid. Filtre bwi mache.",tags:["5 bouji","filtre bwi","conf 72-83%"]},
  chandelier:{l:"🔔 Chandelier Exit",d:"Highest High - ATR×3 (long). Lowest Low + ATR×3 (short). Chanjman trend an tan reyèl.",tags:["HH-ATR×3","LL+ATR×3","conf 75-90%"]},
  ai:{l:"🤖 AI Score",d:"8 faktè ak pwa: EMA+RSI+MACD+BB+momentum+volatilite+position+trend.",tags:["8 faktè","score nòm","conf 68-92%"]},
  smc:{l:"🏛 SMC",d:"Break of Structure + swing high/low + EMA50 filtre.",tags:["BOS","swing","EMA50","conf 84%"]},
  scalping_pro:{l:"⚡ Scalping",d:"EMA 5/13 + RSI 9. Rapid pou 30s/1m.",tags:["EMA 5/13","RSI 9","rapid"]},
  rsi:{l:"📉 RSI",d:"RSI <30/>70 + EMA50 filtre.",tags:["RSI 14","OB 70","OS 30","EMA50"]},
  ema:{l:"📊 EMA Classic",d:"EMA 9/21 crossover + RSI filtre.",tags:["EMA 9/21","RSI"]},
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

async function doBt(){
  const btn=event.target;btn.textContent="⏳ AP KALKILE...";btn.disabled=true;
  document.getElementById("btm").innerHTML=`<div class="al in">⏳ Ap fè backtest — tann 15-30 segonn...</div>`;
  const body={asset:document.getElementById("bt-sy").value,strategy:document.getElementById("bt-st").value,balance:parseFloat(document.getElementById("bt-bl").value),lot:parseFloat(document.getElementById("bt-lt").value),sl:parseFloat(document.getElementById("bt-sl").value),tp:parseFloat(document.getElementById("bt-tp").value)};
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
  document.getElementById("hb").textContent=d.connected?"QUOTEX":"DISCONNECTED";
  document.getElementById("hb").style.color=d.connected?"#00FF88":"#3A6070";
  document.getElementById("hacc").textContent=d.account_type||"—";
  document.getElementById("hacc").style.color=d.account_type==="REAL"?"#FF3B6B":"#FFD600";
  document.getElementById("dot").className="dot "+(d.running?"dl":"di");
  document.getElementById("hs").textContent=d.running?"LIVE":"IDLE";
  document.getElementById("hs").style.color=d.running?"#00FF88":"#3A6070";
  document.getElementById("s-bal").textContent="$"+d.balance.toFixed(2);
  document.getElementById("s-pnl").textContent=sign+"$"+Math.abs(d.pnl).toFixed(2);
  document.getElementById("s-pnl").style.color=col;
  document.getElementById("s-pnl2").textContent=sign+"$"+Math.abs(d.pnl).toFixed(2);
  document.getElementById("s-pnl2").style.color=col;
  document.getElementById("s-tr").textContent=d.trades.length;
  document.getElementById("s-bot").textContent=d.running?"LIVE 🟢":"IDLE";
  document.getElementById("s-bot").style.color=d.running?"#00FF88":"#3A6070";
  document.getElementById("s-strat").textContent=d.config.strategy||"—";
  document.getElementById("s-sym").textContent=d.config.asset||"—";
  document.getElementById("s-dur").textContent=d.config.duration?d.config.duration+"s":"—";
  document.getElementById("c-st2").textContent=d.running?"LIVE 🟢":"IDLE";
  document.getElementById("c-st2").style.color=d.running?"#00FF88":"#3A6070";
  document.getElementById("c-bal").textContent="$"+d.balance.toFixed(2);
  document.getElementById("c-pnl").textContent=sign+"$"+Math.abs(d.pnl).toFixed(2);
  document.getElementById("c-pnl").style.color=col;
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
    document.getElementById("trtbl").innerHTML=`<table><tr><th>#</th><th>Lè</th><th>Aktif</th><th>Side</th><th>Antre</th><th>Regime</th><th>Mise</th><th>Conf</th><th>P&L</th><th>Estati</th></tr>${d.trades.map(t=>`<tr><td style="color:#4A7080">${t.id}</td><td style="color:#4A7080">${t.time}</td><td style="font-weight:700">${t.asset}</td><td><span class="tag ${t.side=="BUY"?"tb":"ts"}">${t.side=="BUY"?"CALL":"PUT"}</span></td><td>${t.entry}</td><td style="color:#4A7080;font-size:10px">${t.regime||"—"}</td><td style="color:#FFD600">$${t.stake||"—"}</td><td style="color:#FFD600">${t.conf}</td><td style="color:${t.pnl>=0?"#00FF88":"#FF3B6B"};font-weight:700">${t.pnl>=0?"+":""}${t.pnl.toFixed(2)}</td><td><span class="tag ${t.status=="won"?"tb":"ts"}">${t.status||"—"}</span></td></tr>`).join("")}</table>`;
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
      document.getElementById("adm-users-list").innerHTML=d2.total===0
        ?'<div style="color:#3A6070;text-align:center;padding:20px">Pa gen itilizatè</div>'
        :`<table><tr><th>UID</th><th>AKTIF</th><th>BOT</th><th>BALANS</th><th>P&L</th><th>TRADES</th><th>AKSYON</th></tr>${d2.users.map(u=>`<tr>
          <td style="color:#4A7080;font-size:10px">${u.uid}</td>
          <td style="font-weight:700">${u.asset||"—"}</td>
          <td><span class="tag ${u.running?"tb":"tg"}">${u.running?"LIVE":"IDLE"}</span></td>
          <td style="color:#00D4FF">$${u.balance}</td>
          <td style="color:${u.pnl>=0?"#00FF88":"#FF3B6B"}">${u.pnl>=0?"+":""}$${u.pnl}</td>
          <td>${u.trades}</td>
          <td style="display:flex;gap:4px;align-items:center">
            ${u.running?`<button onclick="admStopUser('${u.uid}')" title="Kanpe bot" style="background:transparent;border:1px solid #FF3B6B44;color:#FF3B6B;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">■</button>`:""}
            <button onclick="admClearTrades('${u.uid}')" title="Efase trades sèlman" style="background:transparent;border:1px solid #FFD60044;color:#FFD600;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">📊🗑</button>
            <button onclick="admClearUser('${u.uid}')" title="Efase tout" style="background:transparent;border:1px solid #4A708044;color:#4A7080;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">🗑</button>
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

async function admClearUser(uid){
  if(!confirm(`Efase TOUT istorik ${uid}?`))return;
  const token=getStoredToken();
  const r=await fetch("/api/admin/clear_user",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,uid})});
  const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh();
}

async function admClearTrades(uid){
  if(!confirm(`Efase trades sèlman pou ${uid}?`))return;
  const token=getStoredToken();
  const r=await fetch("/api/admin/clear_trades",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,uid})});
  const d=await r.json();alert(d.ok?d.msg:d.error);if(d.ok)admRefresh();
}

function genCode(len){const chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789";let result="";for(let i=0;i<len;i++){if(i>0&&i%4===0)result+="-";result+=chars[Math.floor(Math.random()*chars.length)];}document.getElementById("gen-result").textContent=result;document.getElementById("gen-copy-btn").style.display="inline-block";document.getElementById("new-code").value=result;}
function admCopyGen(){const code=document.getElementById("gen-result").textContent;navigator.clipboard.writeText(code).catch(()=>{});admAddCode();}

checkLogin();
</script>
</body>
</html>"""

HTML = HTML.replace("__SUB_PRICE__", str(SUB_PRICE)).replace("__WHATSAPP__", WHATSAPP)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"BonheurBot Quotex Edition starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
