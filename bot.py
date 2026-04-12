import time
import os
import traceback
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
ORDER_SIZE = 5.0
REFRESH_INTERVAL = 60

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
        print(f"Got creds: {creds}")
        client.set_api_creds(creds)
    except Exception as e:
        print(f"Creds error: {e}")
        traceback.print_exc()
    return client

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
    client.cancel_all()

def place_quotes(client, token_id, mid):
    bid_price = round(mid - SPREAD / 2, 4)
    ask_price = round(mid + SPREAD / 2, 4)
    bid_price = max(0.01, min(bid_price, 0.99))
    ask_price = max(0.01, min(ask_price, 0.99))

    for side, price in [(BUY, bid_price), (SELL, ask_price)]:
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=ORDER_SIZE,
            side=side,
        )
        signed = client.create_order(order_args)
        resp = client.post_order(signed, OrderType.GTC)
        print(f"Placed {side} @ {price}: {resp}")

def run(token_id):
    client = get_client()
    print(f"Starting bot on token: {token_id}")

    while True:
        try:
            print("Cancelling existing orders...")
            cancel_all(client)
            mid = get_midpoint(client, token_id)
            if mid is None:
                print("No orderbook data, skipping...")
            else:
                print(f"Mid: {mid:.4f}")
                place_quotes(client, token_id, mid)
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(REFRESH_INTERVAL)

if __name__ == "__main__":
    TOKEN_ID = os.getenv("TOKEN_ID")
    run(TOKEN_ID)
