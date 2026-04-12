import time
import os
import json
import requests
import numpy as np
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.constants import POLYGON

load_dotenv()

BUY = "BUY"
SELL = "SELL"
HOST = "https://clob.polymarket.com"
CHAIN_ID = POLYGON
ORDER_SIZE = 5.0
MIN_CONFIDENCE = 4
STOP_LOSS = 5.0

def get_client():
    client = ClobClient(
        host=HOST,
        chain_id=CHAIN_ID,
        key=os.getenv("PRIVATE_KEY"),
        signature_type=2,
        funder=os.getenv("FUNDER_ADDRESS"),
    )
    try:
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
    except Exception as e:
        print(f"Creds error: {e}")
    return client

def get_balance(client):
    try:
        balance = client.get_balance()
        return float(balance) / 1000000
    except Exception as e:
        print(f"Balance error: {e}")
        return None

def get_btc_candles(interval, limit):
    resp = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": interval, "limit": limit},
        timeout=10
    )
    candles = resp.json()
    opens   = [float(c[1]) for c in candles]
    highs   = [float(c[2]) for c in candles]
    lows    = [float(c[3]) for c in candles]
    closes  = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]
    return opens, highs, lows, closes, volumes

def calc_rsi(closes, period=14):
    diffs = np.diff(closes)
    gains = np.where(diffs > 0, diffs, 0)
    losses = np.where(diffs < 0, -diffs, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_macd(closes):
    closes = np.array(closes)
    ema12 = closes[-12:].mean()
    ema26 = closes[-26:].mean() if len(closes) >= 26 else closes.mean()
    macd = ema12 - ema26
    signal = closes[-9:].mean() - closes[-18:].mean() if len(closes) >= 18 else 0
    return macd, signal

def calc_bollinger(closes, period=20):
    if len(closes) < period:
        period = len(closes)
    arr = np.array(closes[-period:])
    mean = arr.mean()
    std = arr.std()
    upper = mean + 2 * std
    lower = mean - 2 * std
    return upper, mean, lower

def calc_vwap(closes, volumes):
    closes = np.array(closes)
    volumes = np.array(volumes)
    return np.sum(closes * volumes) / np.sum(volumes)

def analyze_btc():
    score = 0
    signals = []
    try:
        o1, h1, l1, c1, v1 = get_btc_candles("1m", 30)
        current = c1[-1]
        print(f"\nBTC price: ${current:,.2f}")

        rsi_1m = calc_rsi(c1)
        print(f"RSI 1m: {rsi_1m:.1f}")
        if rsi_1m < 45:
            score -= 1
            signals.append("RSI_1m bearish → DOWN")
        elif rsi_1m > 55:
            score += 1
            signals.append("RSI_1m bullish → UP")

        macd, signal = calc_macd(c1)
        if macd > signal:
            score += 1
            signals.append("MACD bullish → UP")
        else:
            score -= 1
            signals.append("MACD bearish → DOWN")

        upper, mid, lower = calc_bollinger(c1)
        if current < lower:
            score += 1
            signals.append("Below lower band → UP")
        elif current > upper:
            score -= 1
            signals.append("Above upper band → DOWN")

        avg_vol = np.mean(v1[:-1])
        last_vol = v1[-1]
        price_change = c1[-1] - c1[-2]
        if last_vol > avg_vol * 1.2 and price_change > 0:
            score += 1
            signals.append("High volume UP → UP")
        elif last_vol > avg_vol * 1.2 and price_change < 0:
            score -= 1
            signals.append("High volume DOWN → DOWN")

        vwap = calc_vwap(c1, v1)
        if current > vwap:
            score += 1
            signals.append("Above VWAP → UP")
        else:
            score -= 1
            signals.append("Below VWAP → DOWN")

        o5, h5, l5, c5, v5 = get_btc_candles("5m", 20)
        rsi_5m = calc_rsi(c5)
        print(f"RSI 5m: {rsi_5m:.1f}")
        if rsi_5m < 45:
            score -= 1
            signals.append("RSI_5m bearish → DOWN")
        elif rsi_5m > 55:
            score += 1
            signals.append("RSI_5m bullish → UP")

        ema5 = np.mean(c5[-5:])
        ema10 = np.mean(c5[-10:])
        if ema5 > ema10:
            score += 1
            signals.append("EMA bullish → UP")
        else:
            score -= 1
            signals.append("EMA bearish → DOWN")

        print(f"Signals: {signals}")
        print(f"Score: {score} / 7")

        if score >= MIN_CONFIDENCE:
            return "UP", current, score
        elif score <= -MIN_CONFIDENCE:
            return "DOWN", current, score
        else:
            return "NEUTRAL", current, score

    except Exception as e:
        print(f"Analysis error: {e}")
        return "NEUTRAL", 0, 0

def get_current_token_ids():
    now = int(time.time())
    for offset in [0, -300, 300]:
        window_ts = now - (now % 300) + offset
        slug = f"btc-updown-5m-{window_ts}"
        try:
            resp = requests.get(
                f"https://gamma-api.polymarket.com/events?slug={slug}",
                timeout=10
            )
            data = resp.json()
            if data and len(data) > 0:
                markets = data[0].get("markets", [])
                if markets:
                    token_ids_raw = markets[0].get("clobTokenIds", "[]")
                    if isinstance(token_ids_raw, str):
                        token_ids = json.loads(token_ids_raw)
                    else:
                        token_ids = token_ids_raw
                    if token_ids:
                        print(f"Found market: {slug}")
                        return token_ids[0], token_ids[1] if len(token_ids) > 1 else None, slug
        except Exception as e:
            print(f"Error fetching {slug}: {e}")
    return None, None, None

def cancel_all(client):
    try:
        client.cancel_all()
    except Exception as e:
        print(f"Cancel error: {e}")

def place_order(client, token_id, price):
    try:
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=ORDER_SIZE,
            side=BUY,
        )
        signed = client.create_order(order_args)
        resp = client.post_order(signed, OrderType.GTC)
        print(f"Placed BUY @ {price}: {resp}")
    except Exception as e:
        print(f"Order error: {e}")

def time_until_next_window():
    now = int(time.time())
    next_window = (now // 300 + 1) * 300
    return next_window - now

def run():
    client = get_client()
    print("Starting smart BTC prediction bot...")

    starting_balance = get_balance(client)
    if starting_balance is None:
        print("Could not get starting balance, using $20 as default")
        starting_balance = 20.0
    print(f"Starting balance: ${starting_balance:.2f}")
    print(f"Stop loss set at: ${starting_balance - STOP_LOSS:.2f}")

    while True:
        try:
            # Check stop loss
            current_balance = get_balance(client)
            if current_balance is not None:
                loss = starting_balance - current_balance
                print(f"\nBalance: ${current_balance:.2f} | Loss: ${loss:.2f} / ${STOP_LOSS:.2f}")
                if loss >= STOP_LOSS:
                    print(f"STOP LOSS HIT! Lost ${loss:.2f}. Bot shutting down.")
                    cancel_all(client)
                    break

            seconds_left = time_until_next_window()
            print(f"{'='*40}")
            print(f"Seconds until expiry: {seconds_left}")

            if seconds_left < 30:
                print(f"Too close to expiry, waiting {seconds_left + 5}s...")
                time.sleep(seconds_left + 5)
                continue

            direction, btc_price, score = analyze_btc()
            print(f"Decision: {direction} (score: {score})")

            if direction == "NEUTRAL":
                print("Signal not strong enough, skipping...")
                time.sleep(30)
                continue

            up_token, down_token, slug = get_current_token_ids()
            if up_token is None:
                print("No market found, waiting...")
                time.sleep(30)
                continue

            cancel_all(client)

            if direction == "UP":
                print(f"Betting UP @ ${btc_price:,.2f}")
                place_order(client, up_token, 0.55)
            elif direction == "DOWN":
                print(f"Betting DOWN @ ${btc_price:,.2f}")
                if down_token:
                    place_order(client, down_token, 0.55)

            sleep_time = min(seconds_left - 25, 60)
            print(f"Sleeping {max(sleep_time, 10)}s...")
            time.sleep(max(sleep_time, 10))

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run()
