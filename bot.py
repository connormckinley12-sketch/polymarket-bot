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

def get_btc_trend():
    """Fetch BTC 1-minute candles from Binance and predict direction."""
    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "1m", "limit": 10},
            timeout=10
        )
        candles = resp.json()
        closes = [float(c[4]) for c in candles]
        
        # Simple trend: compare last price to average of last 5
        avg = np.mean(closes[-5:])
        current = closes[-1]
        change = (current - closes[0]) / closes[0] * 100
        
        print(f"BTC price: ${current:,.2f}")
        print(f"5-candle avg: ${avg:,.2f}")
        print(f"Change last 10m: {change:.3f}%")
        
        # RSI calculation
        diffs = np.diff(closes)
        gains = np.where(diffs > 0, diffs, 0)
        losses = np.where(diffs < 0, -diffs, 0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        
        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        
        print(f"RSI: {rsi:.1f}")
        
        # Decision logic
        if current > avg and change > 0.01 and rsi < 70:
            return "UP", current
        elif current < avg and change < -0.01 and rsi > 30:
            return "DOWN", current
        else:
            return "NEUTRAL", current
            
    except Exception as e:
        print(f"Binance error: {e}")
        return "NEUTRAL", 0

def get_current_token_ids():
    """Get both UP and DOWN token IDs for current window."""
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
                up_token = None
                down_token = None
                for market in markets:
                    token_ids_raw = market.get("clobTokenIds", "[]")
                    if isinstance(token_ids_raw, str):
                        token_ids = json.loads(token_ids_raw)
                    else:
                        token_ids = token_ids_raw
                    question = market.get("question", "").lower()
                    if token_ids:
                        if "up" in question and "down" in question:
                            up_token = token_ids[0]
                            down_token = token_ids[1] if len(token_ids) > 1 else None
                if up_token:
                    print(f"Found market: {slug}")
                    return up_token, down_token, slug
        except Exception as e:
            print(f"Error fetching {slug}: {e}")
    return None, None, None

def cancel_all(client):
    try:
        client.cancel_all()
    except Exception as e:
        print(f"Cancel error: {e}")

def place_order(client, token_id, price, side):
    try:
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=ORDER_SIZE,
            side=side,
        )
        signed = client.create_order(order_args)
        resp = client.post_order(signed, OrderType.GTC)
        print(f"Placed {side} @ {price}: {resp}")
    except Exception as e:
        print(f"Order error: {e}")

def time_until_next_window():
    now = int(time.time())
    next_window = (now // 300 + 1) * 300
    return next_window - now

def run():
    client = get_client()
    print("Starting BTC prediction bot...")
    while True:
        try:
            seconds_left = time_until_next_window()
            print(f"\nSeconds until expiry: {seconds_left}")

            if seconds_left < 30:
                print(f"Too close to expiry, waiting {seconds_left + 5}s...")
                time.sleep(seconds_left + 5)
                continue

            # Get BTC trend
            direction, btc_price = get_btc_trend()
            print(f"Predicted direction: {direction}")

            if direction == "NEUTRAL":
                print("No clear signal, skipping this window...")
                time.sleep(30)
                continue

            # Get market tokens
            up_token, down_token, slug = get_current_token_ids()
            if up_token is None:
                print("No market found, waiting...")
                time.sleep(30)
                continue

            cancel_all(client)

            # Place bet based on direction
            if direction == "UP":
                print(f"Betting UP on BTC @ ${btc_price:,.2f}")
                place_order(client, up_token, 0.55, BUY)
            elif direction == "DOWN":
                print(f"Betting DOWN on BTC @ ${btc_price:,.2f}")
                place_order(client, down_token, 0.55, BUY)

            sleep_time = min(seconds_left - 25, 60)
            print(f"Sleeping {max(sleep_time, 10)}s...")
            time.sleep(max(sleep_time, 10))

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run()
