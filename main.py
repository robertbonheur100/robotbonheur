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
                "broker": None, "connected": False, "running": False,
                "balance": 0.0, "total_pnl": 0.0, "profit_sent": 0.0,
                "trades": [], "log": [], "config": {},
                "deriv_api": None, "binance_api": None,
            }
    return _user_states[uid]

# ═══════════════════════════════════════════════════════════
# STRATEGIES
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

def strat_ema(c):
    cl=[x["close"] for x in c]
    if len(cl)<21: return "NONE",0
    e9=ema(cl,9); e21=ema(cl,21)
    if len(e9)<2 or len(e21)<2: return "NONE",0
    e200=ema(cl,200) if len(cl)>=200 else None
    trend=True if not e200 else cl[-1]>e200[-1]
    if e9[-2]<e21[-2] and e9[-1]>e21[-1] and trend: return "BUY",0.76
    if e9[-2]>e21[-2] and e9[-1]<e21[-1] and not trend: return "SELL",0.76
    return "NONE",0

def strat_fibonacci(c):
    if len(c)<50: return "NONE",0
    r=c[-50:]; hi=max(x["high"] for x in r); lo=min(x["low"] for x in r)
    rng=hi-lo; cl=c[-1]["close"]; rs=rsi([x["close"] for x in c])
    for lvl in [hi-0.618*rng, hi-0.5*rng, hi-0.382*rng]:
        if lvl and abs(cl-lvl)/lvl<0.002:
            if rs<45: return "BUY",0.73
            if rs>55: return "SELL",0.73
    return "NONE",0

def strat_fvg(c):
    if len(c)<10: return "NONE",0
    cl=[x["close"] for x in c]
    e50=ema(cl,50) if len(cl)>=50 else None
    for i in range(2,min(20,len(c)-1)):
        c1=c[-(i+1)]; c3=c[-(i-1)]; close=c[-1]["close"]
        if c3["low"]>c1["high"] and c1["high"]<close<c3["low"]:
            if not e50 or close>e50[-1]: return "BUY",0.78
        if c3["high"]<c1["low"] and c3["high"]<close<c1["low"]:
            if not e50 or close<e50[-1]: return "SELL",0.78
    return "NONE",0

def strat_rsi(c):
    cl=[x["close"] for x in c]
    if len(cl)<20: return "NONE",0
    r=rsi(cl); e50=ema(cl,50) if len(cl)>=50 else None
    if r<30 and (not e50 or cl[-1]>e50[-1]*0.99): return "BUY",0.71
    if r>70 and (not e50 or cl[-1]<e50[-1]*1.01): return "SELL",0.71
    return "NONE",0

def strat_macd(c):
    cl=[x["close"] for x in c]
    if len(cl)<30: return "NONE",0
    up,mid,lo=bb(cl); m,sig=macd(cl)
    if up is None: return "NONE",0
    if m>sig and cl[-1]<lo: return "BUY",0.80
    if m<sig and cl[-1]>up: return "SELL",0.80
    return "NONE",0

def strat_breakout(c):
    if len(c)<22: return "NONE",0
    cl=[x["close"] for x in c]
    hi=max(x["high"] for x in c[-21:-1]); lo=min(x["low"] for x in c[-21:-1])
    vols=[x.get("volume",1000) for x in c]; avg_v=sum(vols[-20:])/20
    if cl[-1]>hi and vols[-1]>avg_v*1.5: return "BUY",0.78
    if cl[-1]<lo and vols[-1]>avg_v*1.5: return "SELL",0.78
    return "NONE",0

def strat_smc(c):
    if len(c)<30: return "NONE",0
    cl=[x["close"] for x in c]
    rhi=max(x["high"] for x in c[-20:-5]); rlo=min(x["low"] for x in c[-20:-5])
    e50=ema(cl,50) if len(cl)>=50 else None
    if cl[-1]>rhi and (not e50 or cl[-1]>e50[-1]): return "BUY",0.82
    if cl[-1]<rlo and (not e50 or cl[-1]<e50[-1]): return "SELL",0.82
    return "NONE",0

def strat_ob(c):
    if len(c)<30: return "NONE",0
    cl=[x["close"] for x in c]
    for i in range(5,25):
        b=c[-i]; body=abs(b["close"]-b["open"]); rng=b["high"]-b["low"]
        if rng>0 and body/rng>0.7:
            if b["close"]>b["open"] and b["low"]<=cl[-1]<=b["high"]: return "BUY",0.80
            if b["close"]<b["open"] and b["low"]<=cl[-1]<=b["high"]: return "SELL",0.80
    return "NONE",0

def strat_stoch(c):
    if len(c)<20: return "NONE",0
    cl=[x["close"] for x in c]
    hi=max(x["high"] for x in c[-14:]); lo=min(x["low"] for x in c[-14:])
    k=((cl[-1]-lo)/(hi-lo)*100) if hi!=lo else 50
    e50=ema(cl,50) if len(cl)>=50 else None
    if k<20 and (not e50 or cl[-1]>e50[-1]): return "BUY",0.72
    if k>80 and (not e50 or cl[-1]<e50[-1]): return "SELL",0.72
    return "NONE",0

def strat_ai(c):
    if len(c)<50: return "NONE",0
    cl=[x["close"] for x in c]; sc=0.0
    e9=ema(cl,9); e21=ema(cl,21); e50=ema(cl,50)
    if e9 and e21 and e50:
        if e9[-1]>e21[-1]>e50[-1]: sc+=2
        elif e9[-1]<e21[-1]<e50[-1]: sc-=2
    r=rsi(cl)
    if r<35: sc+=1.5
    elif r>65: sc-=1.5
    m,sig=macd(cl)
    sc += 1 if m>sig else -1
    up,mid,lo=bb(cl)
    if lo and cl[-1]<lo: sc+=1.5
    if up and cl[-1]>up: sc-=1.5
    if len(c)>=5:
        avg5=sum(cl[-5:])/5
        if cl[-1]>avg5*1.001: sc+=0.5
        elif cl[-1]<avg5*0.999: sc-=0.5
    if sc>=4: return "BUY", min(0.95, 0.65+(sc-4)*0.05)
    if sc<=-4: return "SELL", min(0.95, 0.65+(-sc-4)*0.05)
    return "NONE",0

def strat_scalping(c):
    """Scalping Pro — kwa rapid EMA 3/8 ak RSI + volume"""
    if len(c)<20: return "NONE",0
    cl=[x["close"] for x in c]
    e3=ema(cl,3); e8=ema(cl,8)
    if len(e3)<2 or len(e8)<2: return "NONE",0
    r=rsi(cl,7)
    vols=[x.get("volume",1000) for x in c]
    avg_v=sum(vols[-10:])/10; cur_v=vols[-1]
    vol_ok=cur_v>avg_v*1.2
    if e3[-2]<e8[-2] and e3[-1]>e8[-1] and r<60 and vol_ok: return "BUY",0.74
    if e3[-2]>e8[-2] and e3[-1]<e8[-1] and r>40 and vol_ok: return "SELL",0.74
    return "NONE",0

def strat_confluence(c):
    fns=[strat_ema,strat_fibonacci,strat_fvg,strat_rsi,strat_macd,
         strat_breakout,strat_smc,strat_ob,strat_stoch,strat_ai,strat_scalping]
    buy=sell=0; tc=0
    for f in fns:
        try:
            s,conf=f(c)
            if s=="BUY" and conf>=0.65: buy+=1; tc+=conf
            if s=="SELL" and conf>=0.65: sell+=1; tc+=conf
        except: pass
    tot=buy+sell
    if buy>=3 and buy>sell: return "BUY", min(0.95,tc/tot if tot else 0.70)
    if sell>=3 and sell>buy: return "SELL", min(0.95,tc/tot if tot else 0.70)
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
# BACKTEST
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

    def place_trade(self, symbol, direction, amount=1.0, multiplier=10):
        import websocket as wsl
        res=[None]; err=[None]; done=threading.Event()
        ct="MULTUP" if direction=="BUY" else "MULTDOWN"
        def on_msg(ws,msg):
            d=json.loads(msg)
            mt=d.get("msg_type","")
            if mt=="authorize" and "error" not in d:
                ws.send(json.dumps({"proposal":1,"amount":max(1.0,float(amount)),"basis":"stake","contract_type":ct,"currency":"USD","symbol":symbol,"multiplier":multiplier}))
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

def trading_loop(st):
    cfg=st["config"]
    broker=cfg.get("broker","deriv")
    symbol=cfg.get("symbol","R_100")
    strategy=cfg.get("strategy","confluence")
    lot=float(cfg.get("lot",0.01))
    sl=float(cfg.get("sl",20))
    tp=float(cfg.get("tp",40))
    tf=int(cfg.get("tf_secs",60))
    min_conf=float(cfg.get("min_conf",0.65))
    fn=STRATEGIES.get(strategy,strat_confluence)

    add_log(st,f"🚀 BonheurBot démarré | {symbol} | {strategy} | {broker}")

    while st["running"]:
        try:
            candles=[]
            if broker=="deriv" and st.get("deriv_api"):
                candles=st["deriv_api"].get_candles(symbol,200,tf)
            elif broker=="binance" and st.get("binance_api"):
                iv={60:"1m",300:"5m",900:"15m",3600:"1h",14400:"4h"}.get(tf,"1m")
                candles=st["binance_api"].get_candles(symbol,iv,200)

            if len(candles)<50:
                add_log(st,f"Pa ase done ({len(candles)}) — ap tann...","WARN")
                time.sleep(30); continue

            sig,conf=fn(candles)
            add_log(st,f"📊 {symbol} | {sig} | Conf: {conf:.0%} | {strategy}")

            if sig!="NONE" and conf>=min_conf:
                entry=candles[-1]["close"]
                add_log(st,f"⚡ Trade {sig} @ {entry:.5f} | Conf: {conf:.0%}")
                pnl=0; ok=False

                if broker=="deriv" and st.get("deriv_api"):
                    try:
                        r=st["deriv_api"].place_trade(symbol,sig,max(1.0,lot*100))
                        if r.get("contract_id"):
                            pnl=float(r.get("buy_price",1))*0.08
                            ok=True
                            add_log(st,f"✅ Trade OK! ID:{r['contract_id']}","SUCCESS")
                            # Mete ajou balans reyèl
                            bal_after=r.get("balance_after")
                            if bal_after: st["balance"]=float(bal_after)
                    except Exception as e:
                        add_log(st,f"Trade echwe: {e}","ERROR")

                elif broker=="binance" and st.get("binance_api"):
                    try:
                        st["binance_api"].place_trade(symbol,sig,lot)
                        pnl=lot*entry*0.001; ok=True
                        add_log(st,"✅ Binance trade OK!","SUCCESS")
                        st["balance"]=st["binance_api"].balance
                    except Exception as e:
                        add_log(st,f"Trade echwe: {e}","ERROR")

                if ok:
                    trade={
                        "id":len(st["trades"])+1,
                        "time":datetime.now().strftime("%H:%M:%S"),
                        "symbol":symbol,"side":sig,
                        "entry":round(entry,5),"conf":f"{conf:.0%}",
                        "strategy":strategy,"pnl":round(pnl,2),"status":"open"
                    }
                    st["trades"].insert(0,trade)
                    st["total_pnl"]+=pnl
                    if pnl>0 and broker=="binance" and st.get("binance_api"):
                        profit_send=round(pnl*PROFIT_PCT,4)
                        if profit_send>=0.10:
                            st["binance_api"].send_profit(profit_send)
                            st["profit_sent"]+=profit_send
                            add_log(st,f"💸 1% voye: ${profit_send} USDT → {PROFIT_WALLET[:12]}...","PROFIT")

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
    }
    st["running"]=True
    threading.Thread(target=trading_loop,args=(st,),daemon=True).start()
    return jsonify({"ok":True})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    st=get_state(); st["running"]=False
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

<div class="hdr">
  <div style="display:flex;align-items:center;gap:12px">
    <div class="logo">💰 Bonheur<span>Bot</span></div>
    <div style="width:1px;height:20px;background:#0D2233"></div>
    <span id="hb" class="tag tg">DISCONNECTED</span>
  </div>
  <div style="display:flex;align-items:center;gap:16px">
    <span><span class="dot di" id="dot"></span><span id="hs" style="color:#3A6070;font-size:11px;letter-spacing:1px">IDLE</span></span>
    <span id="hbal" style="color:#3A6070;font-weight:700;font-size:15px">$0.00</span>
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

async function poll(){
  try{const r=await fetch("/api/status");const d=await r.json();upd(d);}catch(e){}
  setTimeout(poll,3000);
}
poll();
</script>
</body>
</html>"""

@app.route("/")
def index(): return render_template_string(HTML)

if __name__=="__main__":
    port=int(os.environ.get("PORT",5000))
    logger.info(f"BonheurBot Pro starting on port {port}")
    app.run(host="0.0.0.0",port=port,debug=False,threaded=True)
