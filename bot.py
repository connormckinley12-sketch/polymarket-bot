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
MIN_CONFIDENCE = 3  # out of 7 indicators must agree

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
        # --- 1m candles (short term) ---
        o1, h1, l1, c1, v1 = get_btc_candles("1m", 30)
        current = c1[-1]
        print(f"\nBTC price: ${current:,.2f}")

        # 1. RSI 1m
        rsi_1m = calc_rsi(c1)
        print(f"RSI 1m: {rsi_1m:.1f}")
        if rsi_1m < 45:
            score -= 1
            signals.append("RSI_1m oversold → DOWN")
        elif rsi_1m > 55:
            score += 1
            signals.append("RSI_1m overbought → UP")

        # 2. MACD 1m
        macd, signal = calc_macd(c1)
        print(f"MACD 1m: {macd:.2f} Signal: {signal:.2f}")
        if macd > signal:
            score += 1
            signals.append("MACD bullish → UP")
        else:
            score -= 1
            signals.append("MACD bearish → DOWN")

        # 3. Bollinger Bands 1m
        upper, mid, lower = calc_bollinger(c1)
        print(f"Bollinger: {lower:.0f} / {mid:.0f} / {upper:.0f}")
        if current < lower:
            score += 1
            signals.append("Below lower band → UP (mean reversion)")
        elif current > upper:
            score -= 1
            signals.append("Above upper band → DOWN (mean reversion)")

        # 4. Volume trend 1m
        avg_vol = np.mean(v1[:-1])
        last_vol = v1[-1]
        price_change = c1[-1] - c1[-2]
        print(f"Volume: {last_vol:.2f} vs avg {avg_vol:.2f}")
        if last_vol > avg_vol * 1.2 and price_change > 0:
            score += 1
            signals.append("High volume UP candle → UP")
        elif last_vol > avg_vol * 1.2 and price_change < 0:
            score -= 1
            signals.append("High volume DOWN candle → DOWN")

        # 5. VWAP 1m
        vwap = calc_vwap(c1, v1)
        print(f"VWAP: ${vwap:.2f}")
        if current > vwap:
            score += 1
            signals.append("Price above VWAP → UP")
        else:
            score -= 1
            signals.append("Price below VWAP → DOWN")

        # --- 5m candles (medium term) ---
        o5, h5, l5, c5, v5 = get_btc_candles("5m", 20)

        # 6. RSI 5m
        rsi_5m = calc_rsi(c5)
        print(f"RSI 5m: {rsi_5m:.1f}")
        if rsi_5m < 45:
            score -= 1
            signals.append("RSI_5m bearish → DOWN")
        elif rsi_5m > 55:
            score += 1
            signals.append("RSI_5m bullish → UP")

        # 7. 5m trend (EMA crossover)
        ema5 = np.mean(c5[-5:])
        ema10 = np.mean(c5[-10:])
        print(f"EMA5: {ema5:.2f} EMA10: {ema10:.2f}")
        if ema5 > ema10:
            score += 1
            signals.append("EMA5 > EMA10 → UP trend")
        else:
            score -= 1
            signals.append("EMA5 < EMA10 → DOWN trend")

        print(f"\nSignals:")
        for s in signals:
            print(f"  {s}")
        print(f"Total score: {score} / 7")

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
    while True:
        try:
            seconds_left = time_until_next_window()
            print(f"\n{'='*40}")
            print(f"Seconds until expiry: {seconds_left}")

            if seconds_left < 30:
                print(f"Too close to expiry, waiting {seconds_left + 5}s...")
                time.sleep(seconds_left + 5)
                continue

            direction, btc_price, score = analyze_btc()
            print(f"\nFinal decision: {direction} (score: {score})")

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
                else:
                    print("No down token found!")

            sleep_time = min(seconds_left - 25, 60)
            print(f"Sleeping {max(sleep_time, 10)}s...")
            time.sleep(max(sleep_time, 10))

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run()
