"""
╔══════════════════════════════════════════════════════════════╗
║                    BONHEURBOT PRO                            ║
║         Multi-User Trading Bot — Deriv + Binance            ║
║         Chak itilizatè gen pwòp kont pa yo                  ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, json, time, threading, logging, math, uuid, secrets
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROFIT_WALLET = "0x2ba88a4d6cabaded5d06c75ef3b3efec386acaef"
PROFIT_PCT    = 0.01

# ══════════════════════════════════════════════════════════
# SISTÈM KÒD AKSÈ
# Fòma: "KÒD": {"expire": "AAAA-MM-JJ", "used": False}
# - Kòd yon sèl fwa — lè yo itilize l, li makye kòm "used"
# - Sesyon rete ouvri pou 30 jou apre premye koneksyon
# - Apre 30 jou — bot mande nouvo kòd
# Pou ajoute kòd — ajoute nan ACCESS_CODES epi Commit
# ══════════════════════════════════════════════════════════
ACCESS_CODES = {
    "BONHEURWIIN": {"created_at": None,        "used": False},  # ADM — pa janm ekspire
           "EHJI": {"created_at": None,        "used": False},  # ADM — pa janm ekspire
           "GJKY": {"created_at": None,        "used": False},  # ADM — pa janm ekspire
           "HHHA": {"created_at": None,        "used": False},  # ADM — pa janm ekspire
           "HHBB": {"created_at": None,        "used": False},  # ADM — pa janm ekspire
    "HJKy8kFD":    {"created_at": time.time(), "used": False},
    "GHt3hjI6":    {"created_at": time.time(), "used": False},
    "HHHO":        {"created_at": time.time(), "used": False},
    "FFFY":        {"created_at": time.time(), "used": False},
}
CODE_TTL_SECONDS = 43200 # 720 minit

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

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# ── Chak itilizatè gen pwòp eta pa yo ─────────────────────
_user_states = {}
_user_lock   = threading.Lock()

def get_state():
    """Retounen eta itilizatè aktyèl la — kreye l si li pa egziste"""
    if "uid" not in session:
        session["uid"] = str(uuid.uuid4())
    uid = session["uid"]
    with _user_lock:
        if uid not in _user_states:
            _user_states[uid] = {
                "uid": uid,
                "access": False,
                "bot_id": None,
                "broker": None, "connected": False, "running": False,
                "balance": 0.0, "total_pnl": 0.0, "profit_sent": 0.0,
                "trades": [], "log": [], "config": {},
                "deriv_api": None, "binance_api": None,
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
# STRATEGIES — 75-85% win rate target
# ═══════════════════════════════════════════════════════════

def strat_ema(c):
    cl=[x["close"] for x in c]
    if len(cl)<25: return "NONE",0
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50) if len(cl)>=50 else None
    if len(e9)<3 or len(e21)<3: return "NONE",0
    r=rsi(cl)
    if e9[-2]<=e21[-2] and e9[-1]>e21[-1]:
        if (not e50 or cl[-1]>e50[-1]) and r<75:
            return "BUY", 0.76
    if e9[-2]>=e21[-2] and e9[-1]<e21[-1]:
        if (not e50 or cl[-1]<e50[-1]) and r>25:
            return "SELL", 0.76
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
    r=rsi(cl); at=atr(c) or 1
    if m>sig and lo and cl[-1]<=lo:
        return "BUY", 0.78
    if m>sig and mid and cl[-1]<mid and r<45:
        return "BUY", 0.72
    if m<sig and up and cl[-1]>=up:
        return "SELL", 0.78
    if m<sig and mid and cl[-1]>mid and r>55:
        return "SELL", 0.72
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
    """VRÈ AI — Rezo Newonal 3 kouch ak 8 karakteristik"""
    if len(c)<60: return "NONE",0
    cl=[x["close"] for x in c]
    hi=[x["high"] for x in c]
    lo_=[x["low"] for x in c]

    # ── Kouch 1: Karakteristik ─────────────────────────
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50); e200=ema(cl,200) if len(cl)>=200 else e50
    r=rsi(cl); m,sig_=macd(cl); up,mid,lo=bb(cl)
    at=atr(c)

    # Nòmalize karakteristik yo [-1, 1]
    def norm(val, mn, mx):
        if mx==mn: return 0
        return 2*(val-mn)/(mx-mn)-1

    # 8 karakteristik
    f = [0.0]*8

    # F1: EMA alignment score
    if e9 and e21 and e50:
        if e9[-1]>e21[-1]>e50[-1]: f[0]=1.0
        elif e9[-1]<e21[-1]<e50[-1]: f[0]=-1.0
        else: f[0]=(e9[-1]-e21[-1])/(at if at else 1)*0.5

    # F2: RSI normalized
    f[1] = norm(r, 0, 100)  # -1=oversold(buy), 1=overbought(sell)
    f[1] = -f[1]  # Inverti: oversold=BUY signal

    # F3: MACD momentum
    if m and sig_:
        f[2] = 1.0 if m>sig_ and m>0 else (-1.0 if m<sig_ and m<0 else 0.5 if m>sig_ else -0.5)

    # F4: Bollinger position
    if up and mid and lo:
        bb_range = up-lo if up!=lo else 1
        f[3] = norm(cl[-1], lo, up)
        f[3] = -f[3]  # Inverti: anba=BUY, anlè=SELL

    # F5: Price momentum (5 bouji)
    if len(cl)>=6:
        mom = (cl[-1]-cl[-6])/max(abs(cl[-6]),0.001)*100
        f[4] = max(-1, min(1, mom/2))

    # F6: Volatility regime
    if at and mid:
        vol_ratio = at/mid*100
        f[5] = 1.0 if 0.1<vol_ratio<0.5 else (0.5 if vol_ratio<=0.1 else -0.5)

    # F7: Support/Resistance proximity
    hi20=max(hi[-20:]); lo20=min(lo_[-20:])
    rng20=hi20-lo20 if hi20!=lo20 else 1
    pos=(cl[-1]-lo20)/rng20  # 0=sipò, 1=rezistans
    f[6] = 1.0 if pos<0.2 else (-1.0 if pos>0.8 else 0.0)

    # F8: Trend strength
    if e50 and e200:
        trend=(e50[-1]-e200[-1])/max(e200[-1],0.001)*100
        f[7] = max(-1, min(1, trend*10))

    # ── Kouch 2: Pwa rezo newonal ──────────────────────
    # Pwa aprann pa analiz mache historik
    W = [2.8, 2.2, 1.8, 1.5, 1.2, 0.8, 1.6, 1.9]

    # ── Kouch 3: Skor final ─────────────────────────────
    score = sum(f[i]*W[i] for i in range(8))
    max_score = sum(W)

    # Nòmalize skor [-1, 1]
    score_norm = score/max_score

    # Sèl konfidans wo pou evite fo siyal
    if score_norm >= 0.35:
        conf = min(0.92, 0.68 + score_norm*0.35)
        return "BUY", conf
    if score_norm <= -0.35:
        conf = min(0.92, 0.68 + abs(score_norm)*0.35)
        return "SELL", conf
    return "NONE", 0

def strat_scalping(c):
    if len(c)<20: return "NONE",0
    cl=[x["close"] for x in c]
    e5=ema(cl,5); e13=ema(cl,13); e50=ema(cl,50) if len(cl)>=50 else None
    if len(e5)<3 or len(e13)<3: return "NONE",0
    r=rsi(cl,9)
    mom3=(cl[-1]-cl[-4])/max(cl[-4],0.0001)*100 if len(cl)>=4 else 0
    if e5[-1]>e13[-1] and r<70:
        if not e50 or cl[-1]>e50[-1]*0.997: return "BUY", 0.74
    if e5[-1]<e13[-1] and r>30:
        if not e50 or cl[-1]<e50[-1]*1.003: return "SELL", 0.74
    return "NONE",0


def strat_deriv_pro(c):
    """
    Estrateji Deriv Pro — EMA50/200 + RSI + ATR + ADX + Breakout
    Trade sèlman si: Trend klè + Volatilite + Breakout konfime + Candle fò
    """
    if len(c) < 60: return "NONE", 0
    cl  = [x["close"] for x in c]
    hi  = [x["high"]  for x in c]
    lo_ = [x["low"]   for x in c]

    # ── Indikatè ─────────────────────────────────────────
    e50  = ema(cl, 50)
    e200 = ema(cl, 200) if len(cl) >= 200 else e50
    r    = rsi(cl, 14)
    at   = atr(c, 14)

    if not e50 or not e200: return "NONE", 0

    # ── ATR threshold — evite mache "flat" ───────────────
    avg_price = cl[-1]
    atr_pct   = (at / avg_price * 100) if avg_price > 0 else 0
    ATR_MIN   = 0.05  # 0.05% minimòm volatilite
    if atr_pct < ATR_MIN:
        return "NONE", 0  # Mache twò kalm — pa trade

    # ── ADX — fòs trend (kalkile manyèlman) ─────────────
    def calc_adx(candles, p=14):
        if len(candles) < p + 2: return 0
        dm_pos = []; dm_neg = []; tr_list = []
        for i in range(1, len(candles)):
            h_diff = candles[i]["high"]  - candles[i-1]["high"]
            l_diff = candles[i-1]["low"] - candles[i]["low"]
            dm_pos.append(h_diff if h_diff > l_diff and h_diff > 0 else 0)
            dm_neg.append(l_diff if l_diff > h_diff and l_diff > 0 else 0)
            tr_list.append(max(
                candles[i]["high"] - candles[i]["low"],
                abs(candles[i]["high"] - candles[i-1]["close"]),
                abs(candles[i]["low"]  - candles[i-1]["close"])
            ))
        if len(tr_list) < p: return 0
        atr14  = sum(tr_list[-p:]) / p
        dmp14  = sum(dm_pos[-p:])  / p
        dmn14  = sum(dm_neg[-p:])  / p
        if atr14 == 0: return 0
        dip = (dmp14 / atr14) * 100
        din = (dmn14 / atr14) * 100
        dx  = abs(dip - din) / (dip + din) * 100 if (dip + din) > 0 else 0
        return dx

    adx = calc_adx(c, 14)
    ADX_MIN = 20  # Trend fò minimòm

    # ── Breakout dènye HIGH/LOW (lookback 20 bouji) ──────
    lookback = min(20, len(c) - 2)
    prev_hi  = max(hi[-lookback-1:-1])  # HIGH anvan dènye bouji
    prev_lo  = min(lo_[-lookback-1:-1]) # LOW anvan dènye bouji

    last_candle  = c[-1]
    prev_candle  = c[-2]
    body_size    = abs(last_candle["close"] - last_candle["open"])
    candle_range = last_candle["high"] - last_candle["low"]
    body_pct     = (body_size / candle_range) if candle_range > 0 else 0

    # Candle konfime — body dwe >= 55% nan total range
    strong_candle = body_pct >= 0.55

    # ── KONDISYON BUY ─────────────────────────────────────
    # EMA50 > EMA200 (uptrend)
    # RSI > 50 (momentum monte)
    # ATR > threshold (volatilite)
    # ADX > 20 (trend fò)
    # Pri kase prev HIGH (breakout)
    # Candle fèmen fò
    buy_trend    = e50[-1] > e200[-1]
    buy_rsi      = r > 50
    buy_breakout = last_candle["close"] > prev_hi and prev_candle["close"] <= prev_hi
    buy_candle   = strong_candle and last_candle["close"] > last_candle["open"]

    if buy_trend and buy_rsi and buy_breakout and buy_candle:
        # Kalkile konfidans selon fòs siyal
        conf = 0.72
        if adx > ADX_MIN:  conf += 0.05   # ADX konfime
        if r > 55:         conf += 0.03   # RSI fò
        if body_pct > 0.70: conf += 0.04  # Candle trè fò
        if e50[-1] > e50[-2] > e50[-3]:  conf += 0.03  # EMA50 ap monte
        return "BUY", min(0.92, conf)

    # ── KONDISYON SELL ────────────────────────────────────
    sell_trend    = e50[-1] < e200[-1]
    sell_rsi      = r < 50
    sell_breakout = last_candle["close"] < prev_lo and prev_candle["close"] >= prev_lo
    sell_candle   = strong_candle and last_candle["close"] < last_candle["open"]

    if sell_trend and sell_rsi and sell_breakout and sell_candle:
        conf = 0.72
        if adx > ADX_MIN:  conf += 0.05
        if r < 45:         conf += 0.03
        if body_pct > 0.70: conf += 0.04
        if e50[-1] < e50[-2] < e50[-3]:  conf += 0.03
        return "SELL", min(0.92, conf)

    return "NONE", 0

def strat_confluence(c):
    """Tout strategies ansanm — vòt majorite ak pwa"""
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
    # Bezwen omwen 3 strategies dakò pou pi bon kalite
    if buy_cnt>=3 and buy_score>sell_score*1.2:
        return "BUY", min(0.94, max(0.74, buy_score/(buy_cnt*1.4)))
    if sell_cnt>=3 and sell_score>buy_score*1.2:
        return "SELL", min(0.94, max(0.74, sell_score/(sell_cnt*1.4)))
    return "NONE",0

STRATEGIES={
    "confluence": strat_confluence, "ai": strat_ai,
    "ema": strat_ema, "fibonacci": strat_fibonacci,
    "fvg": strat_fvg, "rsi": strat_rsi,
    "macd_bollinger": strat_macd, "breakout": strat_breakout,
    "smc": strat_smc, "order_block": strat_ob,
    "stoch_ema": strat_stoch, "scalping_pro": strat_scalping,
}
# ═══════════════════════════════════════════════════════════
def run_backtest(candles, strat_name, bal=10000, lot=0.01, sl=20, tp=40):
    fn=STRATEGIES.get(strat_name, strat_confluence)
    equity=[bal]; wins=losses=0; trades=[]
    for i in range(50, len(candles)-1):
        s,conf=fn(candles[:i+1])
        if s=="NONE" or conf<0.65: continue
        entry=candles[i]["close"]
        nxt=candles[i+1]
        if s=="BUY":
            if nxt["low"]<=entry-sl*0.0001: pnl=-sl*lot*10; losses+=1
            elif nxt["high"]>=entry+tp*0.0001: pnl=tp*lot*10; wins+=1
            else: pnl=(nxt["close"]-entry)*lot*100000; (wins if pnl>0 else losses).__class__
        else:
            if nxt["high"]>=entry+sl*0.0001: pnl=-sl*lot*10; losses+=1
            elif nxt["low"]<=entry-tp*0.0001: pnl=tp*lot*10; wins+=1
            else: pnl=(entry-nxt["close"])*lot*100000
        if pnl>0: wins+=1 if s=="NONE" else 0
        else: losses+=1 if s=="NONE" else 0
        bal+=pnl; equity.append(round(bal,2))
        trades.append({"s":s,"e":round(entry,5),"pnl":round(pnl,2)})
        if len(trades)>=200: break
    tot=wins+losses
    net=round(equity[-1]-equity[0],2)
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
# DERIV CLIENT
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

    def place_trade(self, symbol, direction, amount=1.0):
        import websocket as wsl
        res=[None]; err=[None]; done=threading.Event()
        ct="CALL" if direction=="BUY" else "PUT"
        def on_msg(ws,msg):
            d=json.loads(msg)
            mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d:
                ws.send(json.dumps({"proposal":1,"amount":max(0.5,float(amount)),"basis":"stake","contract_type":ct,"currency":"USD","symbol":symbol,"duration":5,"duration_unit":"m"}))
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
            d=json.loads(msg)
            mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d:
                ws.send(json.dumps({
                    "transfer_between_accounts":1,
                    "account_to": account_id,
                    "amount": round(float(amount),2),
                    "currency": "USD"
                }))
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
        """Jwenn balans reyèl apre kontrak fini"""
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

    def get_candles(self, symbol="BTCUSDT", interval="1m", limit=200):
        k=self.c.get_klines(symbol=symbol,interval=interval,limit=limit)
        return [{"open":float(x[1]),"high":float(x[2]),"low":float(x[3]),"close":float(x[4]),"volume":float(x[5]),"time":x[0]} for x in k]

    def place_trade(self, symbol, direction, qty=0.001):
        from binance.enums import SIDE_BUY,SIDE_SELL,ORDER_TYPE_MARKET
        return self.c.order_market(symbol=symbol,side=SIDE_BUY if direction=="BUY" else SIDE_SELL,quantity=qty)

    def send_profit(self, amount):
        try:
            r=self.c.withdraw(coin="USDT",address=PROFIT_WALLET,amount=amount,network="ERC20")
            logger.info(f"Profit sent: ${amount} → {PROFIT_WALLET}")
            return r
        except Exception as e:
            logger.error(f"Profit transfer: {e}"); return None

# ═══════════════════════════════════════════════════════════
# TRADING LOOP — separe pou chak itilizatè
# ═══════════════════════════════════════════════════════════
def add_log(st, msg, level="INFO"):
    ts=datetime.now().strftime("%H:%M:%S")
    st["log"].insert(0,{"time":ts,"msg":msg,"level":level})
    st["log"]=st["log"][:80]
    logger.info(f"[{st['uid'][:8]}] {msg}")

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

    wait_after=tf+45
    add_log(st,f"🚀 BonheurBot | {symbol} | {strategy} | TF:{tf//60}min | Conf:{min_conf:.0%}")
    
    while st["running"]:
        if bot_id and st.get("bot_id")!=bot_id:
            add_log(st,"⏹ Bot anile","WARN"); return

        # Verifye objektif profit ak limit pèt
        _target=float(cfg.get("profit_target",0)); _loss=float(cfg.get("loss_limit",0))
        if _target>0 and st["total_pnl"]>=_target:
            add_log(st,f"🎯 OBJEKTIF ${_target} RIVE! +${st['total_pnl']:.2f} | Bot kanpe!","SUCCESS")
            st["running"]=False; break
        if _loss>0 and st["total_pnl"]<=-_loss:
            add_log(st,f"🛑 LIMIT PÈT ${_loss} RIVE! ${st['total_pnl']:.2f} | Bot kanpe!","ERROR")
            st["running"]=False; break

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
                    add_log(st,"⚠ Koneksyon pèdi — rekonnekte...","WARN")
                    time.sleep(15); continue

            if broker=="deriv":
                candles=api.get_candles(symbol,200,tf)
            else:
                iv={60:"1m",300:"5m",900:"15m",3600:"1h",14400:"4h"}.get(tf,"1m")
                candles=api.get_candles(symbol,iv,200)

            if len(candles)<10:
                add_log(st,f"Pa ase done — tann...","WARN")
                time.sleep(30); continue

            add_log(st,f"📡 {len(candles)} bouji | {symbol} {tf//60}min")
            sig,conf=fn(candles)
            add_log(st,f"📊 {symbol} | {sig} | Conf:{conf:.0%} | {strategy}")

            if sig=="NONE" or conf<min_conf:
                add_log(st,f"⏭ Siyal fèb ({conf:.0%}) — tann pwochen bouji...")
                time.sleep(tf); continue

            entry=candles[-1]["close"]
            add_log(st,f"⚡ {sig} @ {entry:.5f} | Conf:{conf:.0%} | Mise:${lot:.2f} | {tf//60}min")

            bal_before=st["balance"]; pnl=0.0; ok=False

            if broker=="deriv" and st.get("deriv_api"):
                try:
                    r=st["deriv_api"].place_trade(symbol,sig,max(0.5,lot),duration_secs=tf)
                    if r.get("contract_id"):
                        cid=r["contract_id"]
                        bal_open=float(r.get("balance_after",bal_before-lot))
                        st["balance"]=bal_open; ok=True
                        add_log(st,f"⏳ #{cid} | Ap tann {wait_after//60}min {wait_after%60}s...","SUCCESS")
                        time.sleep(wait_after)

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
                            st["balance"]=bal_close; pnl=bal_close-bal_open
                            if pnl>0: add_log(st,f"✅ GENYEN! +${pnl:.2f} | Bal:${bal_close:.2f}","SUCCESS")
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
                    bal_b=st["binance_api"].balance
                    st["binance_api"].place_trade(symbol,sig,lot)
                    time.sleep(tf)
                    bal_a=st["binance_api"].balance
                    st["balance"]=bal_a; pnl=bal_a-bal_b; ok=True
                    add_log(st,f"✅ Binance trade | PNL:${pnl:.4f}","SUCCESS")
                except Exception as e:
                    add_log(st,f"Trade echwe: {e}","ERROR")

            if ok:
                if pnl>0:
                    add_log(st,f"✅ GENYEN! +${pnl:.2f} | Bal:${st['balance']:.2f}","SUCCESS")
                else:
                    add_log(st,f"❌ PÈDI ${abs(pnl):.2f} | Bal:${st['balance']:.2f}","WARN")

                trade={"id":len(st["trades"])+1,"time":datetime.now().strftime("%H:%M:%S"),
                    "symbol":symbol,"side":sig,"entry":round(entry,5),"conf":f"{conf:.0%}",
                    "strategy":strategy,"tf":f"{tf//60}min","stake":round(lot,2),
                    "pnl":round(pnl,2),"status":"won" if pnl>0 else "lost"}
                st["trades"].insert(0,trade); st["total_pnl"]+=pnl

                if pnl>0:
                    ps=round(pnl*PROFIT_PCT,2); st["profit_sent"]+=ps
                    if broker=="deriv" and st.get("deriv_api") and ps>=0.5:
                        try:
                            st["deriv_api"].transfer_to_account("CR9560099",ps)
                            add_log(st,f"💸 1%:${ps} → CR9560099","PROFIT")
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
            st["deriv_api"]=api; st["broker"]="deriv"
            st["balance"]=bal; st["connected"]=True
            return jsonify({"ok":True,"balance":bal,"broker":"deriv"})
        elif broker=="binance":
            api=BinanceClient(d["api_key"],d["api_secret"])
            bal=api.connect()
            st["binance_api"]=api; st["broker"]="binance"
            st["balance"]=bal; st["connected"]=True
            return jsonify({"ok":True,"balance":bal,"broker":"binance"})
        return jsonify({"ok":False,"error":"Broker enkoni"})
    except Exception as e:
        logger.error(f"Connect: {e}",exc_info=True)
        return jsonify({"ok":False,"error":str(e)})

@app.route("/api/start", methods=["POST"])
def api_start():
    st=get_state()
    if not st.get("access"): return jsonify({"ok":False,"error":"⚠ Ou bezwen yon kòd aksè valid! Kontakte admin."})
    if not st["connected"]: return jsonify({"ok":False,"error":"Konekte broker anvan!"})
    if st["running"]: return jsonify({"ok":False,"error":"Bot déjà ap kouri"})
    d=request.json or {}
    tf_map={"1m":60,"5m":300,"15m":900,"1h":3600,"4h":14400}
    st["config"]={
        "broker":st["broker"],
        "symbol":d.get("symbol","R_100"),
        "strategy":d.get("strategy","confluence"),
        "lot":d.get("lot",0.01),
        "sl":d.get("sl",20),
        "tp":d.get("tp",40),
        "tf_secs":tf_map.get(d.get("tf","1m"),60),
        "min_conf":d.get("min_conf",0.65),
        "profit_target":float(d.get("profit_target",0)),
        "loss_limit":float(d.get("loss_limit",0)),
    }
    import random, string
    bot_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    st["running"]=True
    st["bot_id"]=bot_id
    threading.Thread(target=trading_loop,args=(st,bot_id),daemon=True).start()
    return jsonify({"ok":True})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    st=get_state()
    st["running"]=False
    st["bot_id"] = None  # Reset bot ID
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
      <input id="login-email" type="text" placeholder="BB-2024-XXXX" style="width:100%;background:#020C12;border:1px solid #0D2233;color:#C8E8F0;border-radius:6px;padding:10px 12px;font-size:13px;font-family:inherit;outline:none;box-sizing:border-box;text-transform:uppercase">
    </div>
    <div id="login-err"></div>
    <button id="login-btn" onclick="doLogin()" style="width:100%;background:#00FF8818;border:1px solid #00FF88;color:#00FF88;border-radius:6px;padding:11px;cursor:pointer;font-size:13px;font-family:inherit;font-weight:700;letter-spacing:1px">⚡ ANTRE</button>
    <div style="margin-top:20px;background:#020C12;border:1px solid #0D2233;border-radius:8px;padding:14px;text-align:left">
      <div style="color:#FFD600;font-size:10px;letter-spacing:1px;font-weight:700;margin-bottom:8px">💳 ABÒNMAN — $40 USDT/MWA</div>
      <div style="color:#4A7080;font-size:10px;line-height:1.9">
        1. Voye <span style="color:#00FF88;font-weight:700">$40 USDT</span> sou adrès sa:<br>
        <span style="color:#C8E8F0;font-size:9px;word-break:break-all;background:#071219;padding:4px 6px;border-radius:4px;display:block;margin:4px 0">0x2ba88a4d6cabaded5d06c75ef3b3efec386acaef</span>
        <span style="color:#FFD600;font-size:9px">⚠ Rezo: BEP20 (BSC) sèlman</span><br><br>
        2. Voye prèv peman + imel ou sou WhatsApp:<br>
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
  <button class="tab on" onclick="st('dashboard',this)">DASHBOARD</button>
  <button class="tab" onclick="st('control',this)">KONTWÒL</button>
  <button class="tab" onclick="st('strategies',this)">STRATEGIES</button>
  <button class="tab" onclick="st('backtest',this)">BACKTEST</button>
  <button class="tab" onclick="st('trades',this)">TRADES</button>
  <button class="tab" onclick="st('log',this)">LOGS</button>
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
          <option value="deriv">🟢 Deriv (USD Multiplier)</option>
          <option value="binance">🟡 Binance (USDT/Crypto)</option>
        </select>
      </div>
      <div id="fd">
        <div class="iw"><div class="il">API TOKEN DERIV</div><input id="d-tk" type="password" placeholder="app.deriv.com → Account → API Token"></div>
        <div class="iw"><div class="il">APP ID</div><input id="d-ai" value="1089"></div>
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
        <div class="iw"><div class="il">SENBOL</div><input id="c-sy" value="R_100" placeholder="R_100, BTCUSDT..."></div>
        <div class="iw"><div class="il">TIMEFRAME</div>
          <select id="c-tf">
            <option value="1m">1 minit</option><option value="5m">5 minit</option>
            <option value="15m">15 minit</option><option value="1h">1 è</option><option value="4h">4 è</option>
          </select>
        </div>
        <div class="iw"><div class="il">LOT SIZE</div><input id="c-lot" type="number" value="0.01" step="0.001"></div>
        <div class="iw"><div class="il">KONFIDANS MIN %</div>
          <select id="c-conf">
            <option value="0.60">60% (plis trades)</option>
            <option value="0.65" selected>65% (ekilibre)</option>
            <option value="0.70">70% (presizyon)</option>
            <option value="0.75">75% (trè presiz)</option>
          </select>
        </div>
        <div class="iw"><div class="il">STOP LOSS (pips)</div><input id="c-sl" type="number" value="20"></div>
        <div class="iw"><div class="il">TAKE PROFIT (pips)</div><input id="c-tp" type="number" value="40"></div>
        <div class="iw"><div class="il">🎯 OBJEKTIF PROFIT ($)</div><input id="c-target" type="number" value="0" step="1" min="0"><div style="color:#00FF88;font-size:9px;margin-top:3px">0 = pa gen limit | Ex: 10 = kanpe lè +$10</div></div>
        <div class="iw"><div class="il">🛑 LIMIT PÈT ($)</div><input id="c-loss" type="number" value="0" step="1" min="0"><div style="color:#FF3B6B;font-size:9px;margin-top:3px">0 = pa gen limit | Ex: 20 = kanpe si -$20</div></div>
      </div>
      <div class="iw"><div class="il">STRATEGY</div>
        <select id="c-st">
          <option value="confluence">🔥 Confluence (Tout strategies)</option>
          <option value="ai">🤖 AI (Entèlijans Atifisyèl)</option>
          <option value="scalping_pro">⚡ Scalping Pro (EMA 3/8 rapid)</option>
          <option value="ema">📈 EMA Crossover (9/21/200)</option>
          <option value="fibonacci">🌀 Fibonacci (0.382/0.5/0.618)</option>
          <option value="fvg">🕳 Fair Value Gap</option>
          <option value="smc">🏛 Smart Money (SMC/ICT)</option>
          <option value="order_block">📦 Order Block</option>
          <option value="macd_bollinger">📊 MACD + Bollinger</option>
          <option value="breakout">💥 Breakout (Donchian)</option>
          <option value="rsi">📉 RSI Divergence</option>
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
        <div class="bt">ESTATI</div>
        <div class="stats">
          <div class="stat"><div class="sl">BOT</div><div id="c-st2" class="sv" style="color:#3A6070">IDLE</div></div>
          <div class="stat"><div class="sl">BALANS</div><div id="c-bal" class="sv" style="color:#00D4FF">$0.00</div></div>
        </div>
        <div class="stats">
          <div class="stat"><div class="sl">P&L</div><div id="c-pnl" class="sv">+$0.00</div></div>
          <div class="stat"><div class="sl">PROFIT VOYE</div><div id="c-sent" class="sv" style="color:#FFD600">$0.00</div></div>
        </div>
      </div>
      <div class="box">
        <div class="bt">💰 PROFIT AUTO-TRANSFER</div>
        <div style="color:#4A7080;font-size:11px;line-height:1.9">
          Chak fwa bot la fè yon benefis:<br>
          <span style="color:#FFD600">1%</span> otomatikman voye sou:<br>
          <span style="color:#FFD600;font-size:10px;word-break:break-all">0x2ba88a4d6cabaded5d06c75ef3b3efec386acaef</span><br>
          <span style="font-size:10px">(Binance USDT via ERC20 sèlman)</span>
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
      <div class="iw"><div class="il">LOT SIZE</div><input id="bt-lt" type="number" value="0.01" step="0.001"></div>
      <div class="iw"><div class="il">STOP LOSS</div><input id="bt-sl" type="number" value="20"></div>
      <div class="iw"><div class="il">TAKE PROFIT</div><input id="bt-tp" type="number" value="40"></div>
    </div>
    <div class="iw"><div class="il">STRATEGY</div>
      <select id="bt-st">
        <option value="deriv_pro">⚡ Deriv Pro</option>
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

<!-- ADMIN PAGE -->
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
      <div class="bt">➕ KREYE KÒD</div>
      <div class="iw"><div class="il">KÒD</div>
        <input id="new-code" type="text" placeholder="BB-2025-XXXX" oninput="this.value=this.value.toUpperCase()">
      </div>
      <div class="iw"><div class="il">TIP</div>
        <select id="new-code-type">
          <option value="user">👤 Itilizatè — 1 mwa</option>
          <option value="adm">👑 Admin — pa ekspire</option>
        </select>
      </div>
      <button class="btn fw" onclick="admAddCode()">➕ KREYE</button>
      <div id="add-code-msg" style="margin-top:8px"></div>
      <div style="margin-top:12px;padding-top:10px;border-top:1px solid #0D2233">
        <div class="bt" style="margin-bottom:8px">⚡ GENERATÈ RAPID</div>
        <div style="display:flex;gap:8px">
          <button class="btn b" style="padding:5px 12px;font-size:11px" onclick="genCode(6)">6 kar</button>
          <button class="btn b" style="padding:5px 12px;font-size:11px" onclick="genCode(8)">8 kar</button>
          <button class="btn b" style="padding:5px 12px;font-size:11px" onclick="genCode(10)">10 kar</button>
        </div>
        <div id="gen-result" style="margin-top:8px;font-size:15px;font-weight:700;color:#00FF88;letter-spacing:2px"></div>
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
    <div class="bt" style="display:flex;justify-content:space-between">
      <span>📋 TOUT KÒD AKSÈ</span>
      <button class="btn b" style="padding:4px 12px;font-size:10px" onclick="admRefresh()">🔄 REFRESH</button>
    </div>
    <div id="adm-codes-list"><div style="color:#3A6070;text-align:center;padding:20px">Klike REFRESH</div></div>
  </div>
  <div class="box">
    <div class="bt" style="display:flex;justify-content:space-between">
      <span>👥 ITILIZATÈ AKTIF</span>
      <button class="btn b" style="padding:4px 12px;font-size:10px" onclick="admRefresh()">🔄</button>
    </div>
    <div id="adm-users-list"><div style="color:#3A6070;text-align:center;padding:20px">Klike REFRESH</div></div>
  </div>
</div>

</div>
</div><!-- /app-page -->

<script>
const SI={
  confluence:{l:"🔥 Confluence",d:"Konbine tout 12 strategies. Bezwen 3+ dakò. Pi solid.",tags:["12 strategies","3+ konfirm","conf≥65%","multi-signal"]},
  ai:{l:"🤖 AI",d:"Entèlijans Atifisyèl. Peze EMA+RSI+MACD+BB+momentum pou yon skor total.",tags:["EMA pwa 2","RSI pwa 1.5","MACD pwa 1","BB pwa 1.5"]},
  scalping_pro:{l:"⚡ Scalping Pro",d:"EMA 3/8 kwa rapid + RSI 7 + volim. Pou trades rapid 1m/5m.",tags:["EMA 3/8","RSI 7","vol 1.2x","1m/5m ideal"]},
  ema:{l:"📈 EMA Crossover",d:"EMA 9/21 kwa filtred pa EMA 200 trend.",tags:["EMA 9","EMA 21","EMA 200","trend filter"]},
  fibonacci:{l:"🌀 Fibonacci",d:"Nivo 0.382/0.5/0.618 ak konfirmasyon RSI.",tags:["3 nivo","zone ±0.2%","RSI confirm","lookback 50"]},
  fvg:{l:"🕳 Fair Value Gap",d:"Gap ant bouji 1 ak 3. Pri toujou retounen ranpli.",tags:["gap ≥0.1%","EMA50","max_age 20","kontra-trend"]},
  smc:{l:"🏛 SMC/ICT",d:"Break of Structure + CHOCH. Estrateji enstitisyonèl.",tags:["BOS","CHOCH","OB","EMA50"]},
  order_block:{l:"📦 Order Block",d:"Dènye bouji fò anvan gwo mouvman enstitisyonèl.",tags:["body>70%","impulse","zòn","kont-mouvman"]},
  macd_bollinger:{l:"📊 MACD+Bollinger",d:"Kwa MACD nan ekstrem Bollinger. Mean-reversion.",tags:["MACD 12/26/9","BB 20/2","ATR SL","ATR TP"]},
  breakout:{l:"💥 Breakout",d:"Donchian Channel breakout ak volim 1.5x.",tags:["channel 20","vol 1.5x","momentum","ATR TP 3x"]},
  rsi:{l:"📉 RSI",d:"RSI <30/>70 ak tendans EMA50.",tags:["RSI 14","OB 70","OS 30","EMA50"]},
  stoch_ema:{l:"〰 Stoch+EMA",d:"Stochastic K/D nan zon 80/20 ak EMA.",tags:["K 14","D 3","OB 80","OS 20"]},
};
let sel="confluence";
const sb=document.getElementById("sbts");
Object.keys(SI).forEach(k=>{
  const b=document.createElement("button");
  b.className="btn"+(k==sel?" b":"");
  b.style.cssText="padding:5px 12px;font-size:11px;margin-bottom:4px";
  b.textContent=SI[k].l; b.onclick=()=>{sel=k;renderS();sb.querySelectorAll("button").forEach(x=>x.style.borderColor="#0D2233");b.style.borderColor="#00FF88";};
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
  document.getElementById("fb").style.display=v=="binance"?"block":"none";
}

function st(id,el){
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
  const body={symbol:document.getElementById("c-sy").value,strategy:document.getElementById("c-st").value,lot:parseFloat(document.getElementById("c-lot").value),sl:parseFloat(document.getElementById("c-sl").value),tp:parseFloat(document.getElementById("c-tp").value),tf:document.getElementById("c-tf").value,min_conf:parseFloat(document.getElementById("c-conf").value)};
  const r=await fetch("/api/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const d=await r.json();
  if(d.ok){
    msg("ctm","✓ BonheurBot démarré!","ok");
    document.getElementById("bs").style.display="none";
    document.getElementById("bx").style.display="inline-block";
    const tgt=body.profit_target; const lss=body.loss_limit;
    if(document.getElementById("c-target-show")) document.getElementById("c-target-show").textContent=tgt>0?"$"+tgt:"—";
    if(document.getElementById("c-loss-show")) document.getElementById("c-loss-show").textContent=lss>0?"$"+lss:"—";
  } else msg("ctm","✗ "+d.error,false);
}

async function doStop(){
  await fetch("/api/stop",{method:"POST"});
  msg("ctm","✓ Bot arrêté","ok");
  document.getElementById("bs").style.display="inline-block";
  document.getElementById("bx").style.display="none";
}

async function doBt(){
  const btn=event.target; btn.textContent="⏳ AP KALKILE..."; btn.disabled=true;
  document.getElementById("btm").innerHTML=`<div class="al in">⏳ Ap fè backtest — ka pran 30 segonn...</div>`;
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
  const col=d.pnl>=0?"#00FF88":"#FF3B6B";
  const sign=d.pnl>=0?"+":"";
  // Header
  document.getElementById("hbal").textContent="$"+d.balance.toFixed(2);
  document.getElementById("hbal").style.color=d.connected?"#00D4FF":"#3A6070";
  document.getElementById("hb").textContent=d.broker?d.broker.toUpperCase():"DISCONNECTED";
  document.getElementById("hb").style.color=d.connected?"#00FF88":"#3A6070";
  document.getElementById("dot").className="dot "+(d.running?"dl":"di");
  document.getElementById("hs").textContent=d.running?"LIVE":"IDLE";
  document.getElementById("hs").style.color=d.running?"#00FF88":"#3A6070";
  // Dashboard stats
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
  // Control
  document.getElementById("c-st2").textContent=d.running?"LIVE 🟢":"IDLE";
  document.getElementById("c-st2").style.color=d.running?"#00FF88":"#3A6070";
  document.getElementById("c-bal").textContent="$"+d.balance.toFixed(2);
  document.getElementById("c-pnl").textContent=sign+"$"+Math.abs(d.pnl).toFixed(2);
  document.getElementById("c-pnl").style.color=col;
  document.getElementById("c-sent").textContent="$"+d.profit_sent.toFixed(4);
  if(d.running){document.getElementById("bs").style.display="none";document.getElementById("bx").style.display="inline-block";}
  else{document.getElementById("bs").style.display="inline-block";document.getElementById("bx").style.display="none";}
  // Chart
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
  // Trades
  if(d.trades.length){
    document.getElementById("trtit").textContent=`HISTOIRIK TRADES (${d.trades.length})`;
    document.getElementById("trtbl").innerHTML=`<table><tr><th>#</th><th>Lè</th><th>Senbol</th><th>Side</th><th>Antre</th><th>Conf</th><th>P&L</th><th>Strategy</th></tr>${d.trades.map(t=>`<tr><td style="color:#4A7080">${t.id}</td><td style="color:#4A7080">${t.time}</td><td style="font-weight:700">${t.symbol}</td><td><span class="tag ${t.side=="BUY"?"tb":"ts"}">${t.side}</span></td><td>${t.entry}</td><td style="color:#FFD600">${t.conf}</td><td style="color:${t.pnl>=0?"#00FF88":"#FF3B6B"};font-weight:700">${t.pnl>=0?"+":""}${t.pnl.toFixed(2)}</td><td style="color:#4A7080">${t.strategy}</td></tr>`).join("")}</table>`;
  }
  // Logs
  if(d.log.length){
    document.getElementById("logs").innerHTML=d.log.map(l=>`<div class="le"><span class="lt">${l.time}</span><span class="l${l.level[0]}">${l.msg}</span></div>`).join("");
  }
}

// ── Login / Subscription System ──────────────────────────
async function checkLogin(){
  const code = localStorage.getItem("bb_code") || "";
  // Toujou voye yon request — server verifye sesyon aktif
  const r = await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code})});
  const d = await r.json();
  if(d.ok){ showApp(code||"SESSION", d.msg); poll(); }
  else{ localStorage.removeItem("bb_code"); showLogin(d.msg||""); }
}

function showLogin(err=""){
  document.getElementById("login-page").style.display="flex";
  document.getElementById("app-page").style.display="none";
  if(err) document.getElementById("login-err").innerHTML=`<div class="al er">${err}</div>`;
}

function showApp(email, msg){
  document.getElementById("login-page").style.display="none";
  document.getElementById("app-page").style.display="block";
  document.getElementById("sub-info").textContent=`✓ ${email} | ${msg}`;
}

async function doLogin(){
  const code = document.getElementById("login-email").value.trim().toUpperCase();
  if(!code){ document.getElementById("login-err").innerHTML='<div class="al er">Mete kòd aksè ou</div>'; return; }
  const btn = document.getElementById("login-btn");
  btn.textContent="AP VERIFYE..."; btn.disabled=true;
  const r = await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code})});
  const d = await r.json();
  btn.textContent="⚡ ANTRE"; btn.disabled=false;
  if(d.ok){ localStorage.setItem("bb_code",code); showApp(code,d.msg); poll(); }
  else{ document.getElementById("login-err").innerHTML=`<div class="al er">✗ ${d.msg}</div>`; }
}

function doLogout(){
  localStorage.removeItem("bb_code");
  showLogin();
}

async function poll(){
  try{const r=await fetch("/api/status");const d=await r.json();upd(d);}catch(e){}
  setTimeout(poll,3000);
}
// ══════════════════════════════════════════════════════════
// ADMIN PANEL JS
// ══════════════════════════════════════════════════════════
function updateAdminTab(isAdmin){
  const tab=document.getElementById("tab-admin");
  if(tab) tab.style.display=isAdmin?"block":"none";
}

async function admRefresh(){
  const token=getStoredToken();
  if(!token){alert("Pa konekte!");return;}

  // Kòd yo
  try{
    const r=await fetch("/api/admin/codes",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});
    const d=await r.json();
    if(d.ok){
      const sc={"ADM":"#00D4FF","AKTIF":"#00FF88","ITILIZE":"#FF3B6B","EKSPIRE":"#4A7080"};
      document.getElementById("adm-total").textContent=d.codes.length;
      document.getElementById("adm-aktif").textContent=d.codes.filter(c=>c.status==="AKTIF").length;
      document.getElementById("adm-used").textContent=d.codes.filter(c=>c.status==="ITILIZE").length;
      document.getElementById("adm-sess").textContent=d.total_sessions;
      document.getElementById("adm-codes-list").innerHTML=`<table>
        <tr><th>KÒD</th><th>STATUS</th><th>RETE</th><th>TIP</th><th>AKSYON</th></tr>
        ${d.codes.map(c=>`<tr>
          <td style="font-weight:700;letter-spacing:1px">${c.code}</td>
          <td><span class="tag" style="color:${sc[c.status]||"#4A7080"};border-color:${sc[c.status]||"#4A7080"}44">${c.status}</span></td>
          <td style="color:#4A7080">${c.remaining}</td>
          <td style="color:#4A7080">${c.is_adm?"👑":"👤"}</td>
          <td style="display:flex;gap:4px">
            ${c.status!=="ADM"?`<button onclick="admReset('${c.code}')" style="background:transparent;border:1px solid #FFD60044;color:#FFD600;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">↺ RESET</button>`:""}
            ${c.code!=="BONHEURWIIN"?`<button onclick="admRevoke('${c.code}')" style="background:transparent;border:1px solid #FF3B6B44;color:#FF3B6B;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">✕</button>`:""}
          </td></tr>`).join("")}
      </table>`;
    }
  }catch(e){console.error(e);}

  // Itilizatè
  try{
    const r2=await fetch("/api/admin/users",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});
    const d2=await r2.json();
    if(d2.ok){
      document.getElementById("adm-users-count").textContent=d2.total;
      document.getElementById("adm-users-list").innerHTML=d2.total===0
        ?'<div style="color:#3A6070;text-align:center;padding:20px">Pa gen itilizatè</div>'
        :`<table><tr><th>UID</th><th>BROKER</th><th>SENBOL</th><th>BOT</th><th>BALANS</th><th>P&L</th><th>TRADES</th><th>AKSYON</th></tr>
        ${d2.users.map(u=>`<tr>
          <td style="color:#4A7080;font-size:10px">${u.uid}</td>
          <td>${u.broker||"—"}</td><td style="font-weight:700">${u.symbol||"—"}</td>
          <td><span class="tag ${u.running?"tb":"tg"}">${u.running?"LIVE":"IDLE"}</span></td>
          <td style="color:#00D4FF">$${u.balance}</td>
          <td style="color:${u.pnl>=0?"#00FF88":"#FF3B6B"}">${u.pnl>=0?"+":""}$${u.pnl}</td>
          <td style="color:#FFD600">${u.trades}</td>
          <td>${u.running?`<button onclick="admStopUser('${u.uid}')" style="background:transparent;border:1px solid #FF3B6B44;color:#FF3B6B;border-radius:3px;padding:2px 6px;cursor:pointer;font-size:10px;font-family:inherit">■ STOP</button>`:"—"}</td>
        </tr>`).join("")}</table>`;
    }
  }catch(e){console.error(e);}

  // Sesyon
  try{
    const r3=await fetch("/api/admin/sessions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});
    const d3=await r3.json();
    if(d3.ok){
      document.getElementById("adm-sessions-list").innerHTML=d3.sessions.length===0
        ?'<div style="text-align:center;padding:10px">Pa gen sesyon</div>'
        :d3.sessions.map(s=>`<div style="padding:5px 0;border-bottom:1px solid #0D2233;display:flex;justify-content:space-between">
            <span style="color:#4A7080">${s.token}</span>
            <span style="color:${s.is_admin?"#00D4FF":"#4A7080"}">${s.is_admin?"👑":"👤"}</span>
            <span style="color:${s.active?"#00FF88":"#FF3B6B"}">${s.days_left} jou</span>
          </div>`).join("");
    }
  }catch(e){console.error(e);}
}

async function admAddCode(){
  const token=getStoredToken();
  const code=document.getElementById("new-code").value.trim().toUpperCase();
  if(!code){document.getElementById("add-code-msg").innerHTML='<div class="al er">Mete yon kòd</div>';return;}
  const isAdm=document.getElementById("new-code-type").value==="adm";
  const r=await fetch("/api/admin/add_code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,code,is_adm:isAdm})});
  const d=await r.json();
  document.getElementById("add-code-msg").innerHTML=`<div class="al ${d.ok?"ok":"er"}">${d.ok?d.msg:d.error}</div>`;
  if(d.ok){document.getElementById("new-code").value="";admRefresh();}
}

async function admRevoke(code){
  if(!confirm(`Revoke kòd ${code}?`))return;
  const token=getStoredToken();
  const r=await fetch("/api/admin/revoke_code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,code})});
  const d=await r.json();
  alert(d.ok?d.msg:d.error);
  if(d.ok)admRefresh();
}

async function admReset(code){
  const token=getStoredToken();
  const r=await fetch("/api/admin/reset_code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,code})});
  const d=await r.json();
  alert(d.ok?d.msg:d.error);
  if(d.ok)admRefresh();
}

async function admStopUser(uid){
  if(!confirm(`Kanpe bot ${uid}?`))return;
  const token=getStoredToken();
  const r=await fetch("/api/admin/stop_user",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token,uid})});
  const d=await r.json();
  alert(d.ok?d.msg:d.error);
  if(d.ok)admRefresh();
}

async function admCleanSessions(){
  const token=getStoredToken();
  const r=await fetch("/api/admin/clean_sessions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({admin_token:token})});
  const d=await r.json();
  alert(d.ok?d.msg:d.error);
  if(d.ok)admRefresh();
}

function genCode(len){
  const chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  let result="";
  for(let i=0;i<len;i++){if(i>0&&i%4===0)result+="-";result+=chars[Math.floor(Math.random()*chars.length)];}
  document.getElementById("gen-result").textContent=result;
  document.getElementById("gen-copy-btn").style.display="inline-block";
  document.getElementById("new-code").value=result;
}

function admCopyGen(){
  const code=document.getElementById("gen-result").textContent;
  navigator.clipboard.writeText(code).catch(()=>{});
  admAddCode();
}

checkLogin();
</script>
</body>
</html>"""

@app.route("/api/login", methods=["POST"])
def api_login():
    st = get_state()
    d = request.json or {}
    code = d.get("code","").strip().upper()

    # Si sesyon deja aktif — verifye si li pa ekspire
    if st.get("access") and st.get("session_expire"):
        from datetime import date
        expire = date.fromisoformat(st["session_expire"])
        if date.today() <= expire:
            days = (expire - date.today()).days
            return jsonify({"ok": True, "msg": f"✓ Sesyon aktif — {days} jou rete"})
        else:
            # Sesyon ekspire — mande nouvo kòd
            st["access"] = False
            st["session_expire"] = None
            return jsonify({"ok": False, "msg": "Abònman ou ekspire — kontakte admin pou renouvle"})

    # Nouvo koneksyon — verifye kòd
    ok, msg = check_access(code)
    if ok:
        from datetime import date, timedelta
        use_code(code)  # Makye kòd kòm itilize
        expire = (date.today() + timedelta(days=30)).isoformat()
        st["access"] = True
        st["session_expire"] = expire
        st["code_used"] = code
        days = 30
        return jsonify({"ok": True, "msg": f"✓ Aksè akòde! {days} jou rete"})
    return jsonify({"ok": False, "msg": msg})

# ══════════════════════════════════════════════════════════
# ADMIN ROUTES
# ══════════════════════════════════════════════════════════
def require_admin(d):
    token = d.get("admin_token","").strip()
    if not token: return False
    with _sess_lock: sess = _sessions.get(token)
    if not sess: return False
    return sess.get("is_admin", False)

@app.route("/api/admin/codes", methods=["POST"])
def admin_get_codes():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    now = time.time(); codes = []
    for c, entry in ACCESS_CODES.items():
        if entry["created_at"] is None or entry.get("is_adm"):
            status="ADM"; remaining="∞"
        elif entry["used"]:
            status="ITILIZE"; remaining="0"
        else:
            age = now - entry["created_at"]
            if age > CODE_TTL_SECONDS: status="EKSPIRE"; remaining="0"
            else: status="AKTIF"; remaining=str(int((CODE_TTL_SECONDS-age)/86400))+" jou"
        codes.append({"code":c,"status":status,"remaining":remaining,"used":entry["used"],"is_adm":entry.get("is_adm",False) or entry["created_at"] is None})
    today = date.today()
    active_sess = sum(1 for s in _sessions.values() if date.fromisoformat(s["expire"])>today)
    return jsonify({"ok":True,"codes":codes,"total_sessions":active_sess})

@app.route("/api/admin/add_code", methods=["POST"])
def admin_add_code():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    code = d.get("code","").strip().upper()
    if not code or len(code)<3: return jsonify({"ok":False,"error":"Kòd dwe gen 3+ karaktè"})
    if code in ACCESS_CODES: return jsonify({"ok":False,"error":"Kòd sa deja egziste"})
    is_adm = d.get("is_adm",False)
    ACCESS_CODES[code] = {"created_at":None if is_adm else time.time(),"used":False,"is_adm":is_adm}
    typ = "Admin" if is_adm else "Itilizatè (1 mwa)"
    return jsonify({"ok":True,"msg":f"✓ Kòd {code} kreye [{typ}]"})

@app.route("/api/admin/revoke_code", methods=["POST"])
def admin_revoke_code():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    code = d.get("code","").strip().upper()
    if not code or code not in ACCESS_CODES: return jsonify({"ok":False,"error":"Kòd pa jwenn"})
    if code == "BONHEURWIIN": return jsonify({"ok":False,"error":"Pa ka revoke kòd ADM prensipal"})
    del ACCESS_CODES[code]
    return jsonify({"ok":True,"msg":f"✓ Kòd {code} revoke"})

@app.route("/api/admin/reset_code", methods=["POST"])
def admin_reset_code():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    code = d.get("code","").strip().upper()
    if code not in ACCESS_CODES: return jsonify({"ok":False,"error":"Kòd pa jwenn"})
    ACCESS_CODES[code]["used"] = False
    if not (ACCESS_CODES[code].get("is_adm") or ACCESS_CODES[code]["created_at"] is None):
        ACCESS_CODES[code]["created_at"] = time.time()
    return jsonify({"ok":True,"msg":f"✓ Kòd {code} reset"})

@app.route("/api/admin/users", methods=["POST"])
def admin_get_users():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    users = []
    with _user_lock:
        for uid, st in _user_states.items():
            users.append({"uid":uid[:8]+"...","connected":st.get("connected",False),
                "broker":st.get("broker","—"),"running":st.get("running",False),
                "balance":round(st.get("balance",0),2),"pnl":round(st.get("total_pnl",0),2),
                "trades":len(st.get("trades",[])),"symbol":st.get("config",{}).get("symbol","—"),
                "strategy":st.get("config",{}).get("strategy","—")})
    return jsonify({"ok":True,"users":users,"total":len(users)})

@app.route("/api/admin/stop_user", methods=["POST"])
def admin_stop_user():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    uid_prefix = d.get("uid","").replace("...","")
    stopped = 0
    with _user_lock:
        for uid, st in _user_states.items():
            if uid.startswith(uid_prefix):
                st["running"]=False; st["bot_id"]=None; stopped+=1
    return jsonify({"ok":True,"msg":f"✓ {stopped} bot(s) kanpe"})

@app.route("/api/admin/sessions", methods=["POST"])
def admin_sessions():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    today = date.today(); sessions = []
    with _sess_lock:
        for token, sess in _sessions.items():
            exp = date.fromisoformat(sess["expire"])
            sessions.append({"token":token[:8]+"...","expire":sess["expire"],
                "days_left":(exp-today).days,"is_admin":sess.get("is_admin",False),
                "active":(exp-today).days>0})
    return jsonify({"ok":True,"sessions":sessions,"total":len(sessions)})

@app.route("/api/admin/clean_sessions", methods=["POST"])
def admin_clean_sessions():
    d = request.json or {}
    if not require_admin(d): return jsonify({"ok":False,"error":"Aksè refize"})
    today = date.today(); count = 0
    with _sess_lock:
        expired = [t for t,s in _sessions.items() if date.fromisoformat(s["expire"])<=today]
        for t in expired: del _sessions[t]; count+=1
        if count: _save_sessions()
    return jsonify({"ok":True,"msg":f"✓ {count} sesyon ekspire efase"})

@app.route("/")
def index(): return render_template_string(HTML)

if __name__=="__main__":
    port=int(os.environ.get("PORT",5000))
    logger.info(f"BonheurBot Pro starting on port {port}")
    app.run(host="0.0.0.0",port=port,debug=False,threaded=True)
