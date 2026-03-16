import time
import numpy as np

MIN_CONF = 0.75
RISK_PER_TRADE = 0.01
MULTIPLIER = 40  # <-- Fikse multiplier nan 40 otomatikman
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

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

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

    gains = []
    losses = []

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


STRATEGIES = {

    "rsi": strat_rsi,
    "ema": strat_ema_cross,
    "breakout": strat_breakout

}


# ===============================
# CONFLUENCE
# ===============================

def strat_confluence(candles):

    signals = []
    confs = []

    for f in STRATEGIES.values():

        s,conf = f(candles)

        if s != "NONE":
            signals.append(s)
            confs.append(conf)

    buy = signals.count("BUY")
    sell = signals.count("SELL")

    if buy >= 2 and buy > sell:
        return "BUY", sum(confs)/len(confs)

    if sell >= 2 and sell > buy:
        return "SELL", sum(confs)/len(confs)

    return "NONE",0


# ===============================
# RISK MANAGEMENT
# ===============================

def calculate_lot(balance):

    risk = balance * RISK_PER_TRADE

    return risk


def place_trade(signal, balance, candles):

    lot = calculate_lot(balance)

    price = candles[-1]["close"]

    atr_value = atr(candles)

    if not atr_value:
        return

    stop_loss = atr_value * 1.5
    take_profit = atr_value * 3

    print("Trade:",signal)
    print("Size:",lot)
    print("Entry:",price)
    print("TP:",price + take_profit if signal=="BUY" else price - take_profit)
    print("SL:",price - stop_loss if signal=="BUY" else price + stop_loss)


# ===============================
# BOT LOOP
# ===============================

def run_bot(get_candles, get_balance):

    while True:

        candles = get_candles()

        balance = get_balance()

        trend = trend_filter(candles)

        signal,conf = strat_confluence(candles)

        if conf < MIN_CONF:
            time.sleep(10)
            continue

        if signal != trend:
            time.sleep(10)
            continue

        if signal != "NONE":

            place_trade(signal,balance,candles)

        time.sleep(15)
