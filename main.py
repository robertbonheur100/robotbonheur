import time
import numpy as np

# ===============================
# CONFIG
# ===============================
MIN_CONF = 0.75
RISK_PER_TRADE = 0.01
VALID_MULTIPLIERS = [40, 100, 200, 300, 400]
MULTIPLIER = 100  # default, ou ka chanje 40,100,200,300,400

# ===============================
# INDICATORS
# ===============================
def ema(data, period):
    if len(data) < period:
        return None
    ema_values = []
    k = 2 / (period + 1)
    ema_values.append(sum(data[:period]) / period)
    for price in data[period:]:
        ema_values.append(price * k + ema_values[-1] * (1 - k))
    return ema_values

def atr(candles, period=14):
    trs = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i-1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period

# ===============================
# TREND FILTER
# ===============================
def trend_filter(candles):
    closes = [c["close"] for c in candles]
    ema50 = ema(closes,50)
    ema200 = ema(closes,200)
    if not ema50 or not ema200:
        return "NONE"
    if ema50[-1] > ema200[-1]:
        return "BUY"
    if ema50[-1] < ema200[-1]:
        return "SELL"
    return "NONE"

# ===============================
# STRATEGIES
# ===============================
def strat_rsi(c):
    closes = [x["close"] for x in c]
    if len(closes) < 15:
        return "NONE",0
    gains, losses = [], []
    for i in range(1,15):
        diff = closes[-i] - closes[-i-1]
        if diff > 0:
            gains.append(diff)
        else:
            losses.append(abs(diff))
    avg_gain = sum(gains)/14 if gains else 0
    avg_loss = sum(losses)/14 if losses else 1
    rs = avg_gain / avg_loss
    rsi = 100 - (100/(1+rs))
    if rsi < 30:
        return "BUY",0.8
    if rsi > 70:
        return "SELL",0.8
    return "NONE",0

def strat_ema_cross(c):
    closes = [x["close"] for x in c]
    ema9 = ema(closes,9)
    ema21 = ema(closes,21)
    if not ema9 or not ema21:
        return "NONE",0
    if ema9[-1] > ema21[-1]:
        return "BUY",0.78
    if ema9[-1] < ema21[-1]:
        return "SELL",0.78
    return "NONE",0

def strat_breakout(c):
    highs = [x["high"] for x in c]
    lows = [x["low"] for x in c]
    if c[-1]["close"] > max(highs[-10:-1]):
        return "BUY",0.76
    if c[-1]["close"] < min(lows[-10:-1]):
        return "SELL",0.76
    return "NONE",0

# ===============================
# AI PREDICTION SIMULATION
# (ogmante conf pou winrate 80%)
# ===============================
def ai_prediction(candles):
    # Simulated AI: ogmante conf lè mouvman klè
    closes = [x["close"] for x in candles]
    if len(closes) < 20:
        return 0
    recent_move = closes[-1] - closes[-5]
    return 0.05 if abs(recent_move)/closes[-5] > 0.005 else 0

# ===============================
# STRATEGY CONFLUENCE
# ===============================
STRATEGIES = {
    "rsi": strat_rsi,
    "ema": strat_ema_cross,
    "breakout": strat_breakout
}

def strat_confluence(candles):
    signals, confs = [], []
    for f in STRATEGIES.values():
        s, conf = f(candles)
        if s != "NONE":
            signals.append(s)
            confs.append(conf)
    if not signals:
        return "NONE",0
    buy = signals.count("BUY")
    sell = signals.count("SELL")
    conf_avg = sum(confs)/len(confs) + ai_prediction(candles)
    conf_avg = min(conf_avg, 0.95)  # max 95%
    if buy >= 2 and buy > sell:
        return "BUY", conf_avg
    if sell >= 2 and sell > buy:
        return "SELL", conf_avg
    return "NONE",0

# ===============================
# SUPPORT / RESISTANCE SIMULATION
# ===============================
def support_resistance(candles):
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    support = min(lows[-20:])
    resistance = max(highs[-20:])
    return support, resistance

# ===============================
# MULTIPLIER
# ===============================
def get_multiplier(desired=MULTIPLIER):
    if desired in VALID_MULTIPLIERS:
        return int(desired)
    return int(VALID_MULTIPLIERS[0])

# ===============================
# RISK MANAGEMENT
# ===============================
def calculate_lot(balance):
    return balance * RISK_PER_TRADE

# ===============================
# PLACE TRADE
# ===============================
def place_trade(signal, balance, candles):
    lot = calculate_lot(balance)
    price = candles[-1]["close"]
    atr_value = atr(candles)
    if not atr_value:
        return
    stop_loss = atr_value * 1.5
    take_profit = atr_value * 3
    multiplier = get_multiplier(MULTIPLIER)
    support, resistance = support_resistance(candles)
    print("-----")
    print("Trade:",signal)
    print("Size:",lot)
    print("Entry:",price)
    print("TP:",price + take_profit if signal=="BUY" else price - take_profit)
    print("SL:",price - stop_loss if signal=="BUY" else price + stop_loss)
    print("Multiplier:",multiplier)
    print("Support:",support,"Resistance:",resistance)
    print("-----")

# ===============================
# BOT LOOP
# ===============================
def run_bot(get_candles, get_balance):
    while True:
        candles = get_candles()
        balance = get_balance()
        trend = trend_filter(candles)
        signal, conf = strat_confluence(candles)
        if conf < MIN_CONF:
            time.sleep(10)
            continue
        if signal != trend:
            time.sleep(10)
            continue
        if signal != "NONE":
            place_trade(signal, balance, candles)
        time.sleep(15)
