import time
import os
import requests
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.constants import POLYGON

load_dotenv()

BUY = "BUY"
SELL = "SELL"

HOST = "https://clob.polymarket.com"
CHAIN_ID = POLYGON
SPREAD = 0.02
ORDER_SIZE = 1.0

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

def get_current_token_id():
    now = int(time.time())
    # Try current and previous window in case of timing issues
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
                    token_ids = markets[0].get("clobTokenIds", [])
                    if token_ids:
                        print(f"Found market: {slug}")
                        print(f"Token ID: {token_ids[0]}")
                        return token_ids[0], slug, window_ts
        except Exception as e:
            print(f"Error fetching {slug}: {e}")
    return None, None, None

def get_midpoint(client, token_id):
    book = client.get_order_book(token_id)
    bids = book.bids
    asks = book.asks
    if not bids or not asks:
        return None
    best_bid = float(bids[0].price)
    best_ask = float(asks[0].price)
    return (best_bid + best_ask) / 2

def cancel_all(client):
    try:
        client.cancel_all()
    except Exception as e:
        print(f"Cancel error: {e}")

def place_quotes(client, token_id, mid):
    bid_price = round(mid - SPREAD / 2, 4)
    ask_price = round(mid + SPREAD / 2, 4)
    bid_price = max(0.01, min(bid_price, 0.99))
    ask_price = max(0.01, min(ask_price, 0.99))

    for side, price in [(BUY, bid_price), (SELL, ask_price)]:
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
            print(f"Order error {side}: {e}")

def time_until_next_window():
    now = int(time.time())
    next_window = (now // 300 + 1) * 300
    return next_window - now

def run():
    client = get_client()
    print("Starting BTC 5-minute market maker bot...")

    while True:
        try:
            token_id, slug, window_ts = get_current_token_id()
            seconds_left = time_until_next_window()

            print(f"Seconds until expiry: {seconds_left}")

            if token_id is None:
                print("No market found, waiting 30s...")
                time.sleep(30)
                continue

            if seconds_left < 30:
                print(f"Too close to expiry, waiting {seconds_left + 5}s...")
                time.sleep(seconds_left + 5)
                continue

            cancel_all(client)

            mid = get_midpoint(client, token_id)
            if mid is None:
                print("No orderbook data, skipping...")
            else:
                print(f"Mid: {mid:.4f}")
                place_quotes(client, token_id, mid)

            sleep_time = min(seconds_left - 25, 60)
            print(f"Sleeping {max(sleep_time, 10)}s...")
            time.sleep(max(sleep_time, 10))

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run()
