import time
import os
import json
import requests
import numpy as np
from datetime import datetime, timedelta
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.constants import POLYGON

load_dotenv()

BUY = "BUY"
HOST = "https://clob.polymarket.com"
CHAIN_ID = POLYGON
MIN_EDGE = 0.12
ORDER_SIZE = 5.0
STOP_LOSS = 5.0

CITIES = {
    "nyc":      {"lat": 40.71, "lon": -74.01, "aliases": ["new york", "nyc"]},
    "chicago":  {"lat": 41.85, "lon": -87.65, "aliases": ["chicago"]},
    "toronto":  {"lat": 43.65, "lon": -79.38, "aliases": ["toronto"]},
    "shanghai": {"lat": 31.23, "lon": 121.47, "aliases": ["shanghai"]},
    "miami":    {"lat": 25.77, "lon": -80.19, "aliases": ["miami"]},
    "dallas":   {"lat": 32.78, "lon": -96.80, "aliases": ["dallas"]},
    "atlanta":  {"lat": 33.75, "lon": -84.39, "aliases": ["atlanta"]},
    "seattle":  {"lat": 47.61, "lon": -122.33, "aliases": ["seattle"]},
}

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

def get_ensemble_forecast(city_key):
    coords = CITIES[city_key]
    resp = requests.get(
        "https://ensemble-api.open-meteo.com/v1/ensemble",
        params={
            "latitude": coords["lat"],
            "longitude": coords["lon"],
            "hourly": "temperature_2m",
            "models": "gfs_seamless",
            "forecast_days": 3,
            "temperature_unit": "fahrenheit",
        },
        timeout=15
    )
    return resp.json()

def celsius_to_fahrenheit(c):
    return c * 9/5 + 32

def get_daily_high_distribution(city_key, target_date):
    data = get_ensemble_forecast(city_key)
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temp_members = [k for k in hourly.keys() if "temperature_2m" in k]
    date_str = target_date.strftime("%Y-%m-%d")
    day_indices = [i for i, t in enumerate(times) if t.startswith(date_str)]
    if not day_indices:
        return None
    daily_highs = []
    for member in temp_members:
        member_temps = [hourly[member][i] for i in day_indices if i < len(hourly[member])]
        if member_temps:
            daily_highs.append(max(member_temps))
    return np.array(daily_highs)

def parse_temperature_question(question):
    """
    Parse temperature market question into (low, high, unit) tuple.
    Returns (low_f, high_f) in Fahrenheit, or (None, X) for 'X or below',
    or (X, None) for 'X or higher'.
    Returns (None, None, None) if can't parse.
    """
    import re
    q = question.lower()

    # Detect unit
    is_celsius = "°c" in q or " c " in q or q.endswith("c?") or q.endswith("c")
# Skip Celsius markets entirely
    if is_celsius:
        return None, None, None
    # Extract all numbers
    nums = [int(n) for n in re.findall(r'\d+', q)]
    
    # Filter to temperature-range numbers only
    if is_celsius:
        temps = [n for n in nums if 0 <= n <= 50]
    else:
        temps = [n for n in nums if 40 <= n <= 130]

    if not temps:
        return None, None, None

    # "X or higher" / "X or above"
    if "or higher" in q or "or above" in q:
        threshold = temps[-1]
        if is_celsius:
            threshold = celsius_to_fahrenheit(threshold)
        return threshold, None, "above"

    # "X or below" / "X or under"
    if "or below" in q or "or under" in q:
        threshold = temps[-1]
        if is_celsius:
            threshold = celsius_to_fahrenheit(threshold)
        return None, threshold, "below"

    # "between X-Y" or "between X and Y"
    if len(temps) >= 2:
        low, high = temps[0], temps[1]
        if is_celsius:
            low = celsius_to_fahrenheit(low)
            high = celsius_to_fahrenheit(high)
        return low, high, "range"

    return None, None, None

def prob_for_market(highs, low, high, market_type):
    """Calculate probability given ensemble highs and market type."""
    if market_type == "above":
        return float(np.mean(highs >= low))
    elif market_type == "below":
        return float(np.mean(highs <= high))
    elif market_type == "range":
        return float(np.mean((highs >= low) & (highs <= high)))
    return None

def find_weather_markets_for_date(target_date):
    markets = []
    try:
        resp = requests.get(
            "https://gamma-api.polymarket.com/events",
            params={
                "limit": 50,
                "active": "true",
                "tag_slug": "weather",
                "end_date_min": target_date.strftime("%Y-%m-%d"),
            },
            timeout=10
        )
        data = resp.json()
        for event in data:
            title = event.get("title", "").lower()
            if "temperature" not in title and "highest" not in title:
                continue
            for market in event.get("markets", []):
                question = market.get("question", "")
                token_ids_raw = market.get("clobTokenIds", "[]")
                if isinstance(token_ids_raw, str):
                    token_ids = json.loads(token_ids_raw)
                else:
                    token_ids = token_ids_raw
                if token_ids:
                    markets.append({
                        "question": question,
                        "title": event.get("title", ""),
                        "token_id": token_ids[0],
                        "price": float(market.get("bestAsk", 0.5) or 0.5),
                    })
    except Exception as e:
        print(f"Market fetch error: {e}")
    return markets

def find_city_for_market(market_title):
    title = market_title.lower()
    for city_key, city_data in CITIES.items():
        for alias in city_data["aliases"]:
            if alias in title:
                return city_key
    return None

def run():
    client = get_client()
    print("Starting Weather Edge Bot v3 (bug fixed)...")
    print(f"Cities: {list(CITIES.keys())}")
    print(f"Min edge: {MIN_EDGE:.0%} | Stop loss: ${STOP_LOSS}")

    while True:
        try:
            print(f"\n{'='*50}")
            print(f"Scanning... {datetime.utcnow().strftime('%H:%M UTC')}")

            tomorrow = datetime.utcnow().date() + timedelta(days=1)
            print(f"Target date: {tomorrow}")

            markets = find_weather_markets_for_date(tomorrow)
            print(f"Found {len(markets)} temperature markets")

            if not markets:
                print("No markets found, sleeping 1 hour...")
                time.sleep(3600)
                continue

            # Group by city
            city_markets = {}
            for market in markets:
                city_key = find_city_for_market(market["title"])
                if city_key:
                    if city_key not in city_markets:
                        city_markets[city_key] = []
                    city_markets[city_key].append(market)

            print(f"Cities with markets: {list(city_markets.keys())}")

            bets = []
            for city_key, city_mkt_list in city_markets.items():
                print(f"\n--- {city_key.upper()} ---")
                try:
                    highs = get_daily_high_distribution(city_key, tomorrow)
                    if highs is None:
                        continue
                    mean_high = np.mean(highs)
                    std_high = np.std(highs)
                    print(f"Ensemble: mean={mean_high:.1f}°F std={std_high:.1f}°F ({len(highs)} members)")

                    for market in city_mkt_list:
                        low, high, mtype = parse_temperature_question(market["question"])
                        if mtype is None:
                            continue

                        # Sanity check: skip extreme threshold markets
                        # "or higher" bets: threshold must be within 2 std of mean
                        if mtype == "above" and low > mean_high + 2 * std_high:
                            print(f"  SKIP (threshold too high): {market['question']}")
                            continue
                        # "or below" bets: threshold must be within 2 std of mean
                        if mtype == "below" and high < mean_high - 2 * std_high:
                            print(f"  SKIP (threshold too low): {market['question']}")
                            continue

                        our_prob = prob_for_market(highs, low, high, mtype)
                        if our_prob is None:
                            continue

                        market_price = market["price"]
                        edge = our_prob - market_price

                        print(f"  {market['question']}")
                        print(f"  Our: {our_prob:.1%} | Market: {market_price:.1%} | Edge: {edge:+.1%}")

                        if abs(edge) >= MIN_EDGE and 0.03 < our_prob < 0.97:
                            bets.append({
                                "market": market,
                                "our_prob": our_prob,
                                "market_price": market_price,
                                "edge": edge,
                                "city": city_key,
                            })

                except Exception as e:
                    print(f"Error analyzing {city_key}: {e}")

            bets = sorted(bets, key=lambda x: abs(x["edge"]), reverse=True)
            print(f"\n{'='*50}")
            print(f"Found {len(bets)} valid betting opportunities!")

            for bet in bets[:5]:
                print(f"\n✅ {bet['market']['question']}")
                print(f"   Edge: {bet['edge']:+.1%} | Our: {bet['our_prob']:.1%} | Market: {bet['market_price']:.1%}")

                if bet["edge"] > 0:
                    price = min(round(bet["our_prob"] - 0.02, 2), 0.90)
                    try:
                        order_args = OrderArgs(
                            token_id=bet["market"]["token_id"],
                            price=price,
                            size=ORDER_SIZE,
                            side=BUY,
                        )
                        signed = client.create_order(order_args)
                        resp = client.post_order(signed, OrderType.GTC)
                        print(f"   Placed YES bet @ {price}: {resp}")
                    except Exception as e:
                        print(f"   Bet error: {e}")

            print("\nSleeping 1 hour...")
            time.sleep(3600)

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    run()
