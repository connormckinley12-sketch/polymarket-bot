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
MIN_EDGE = 0.15       # Only bet if our probability is 15%+ different from market
ORDER_SIZE = 5.0
STOP_LOSS = 5.0

CITIES = {
    "NYC":      {"lat": 40.71, "lon": -74.01},
    "Chicago":  {"lat": 41.85, "lon": -87.65},
    "Toronto":  {"lat": 43.65, "lon": -79.38},
    "Shanghai": {"lat": 31.23, "lon": 121.47},
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

def get_balance(client):
    try:
        balance = client.get_balance()
        return float(balance) / 1000000
    except Exception as e:
        print(f"Balance error: {e}")
        return None

def get_ensemble_forecast(city):
    """Fetch 31-member GFS ensemble forecast for a city."""
    coords = CITIES[city]
    resp = requests.get(
        "https://ensemble-api.open-meteo.com/v1/ensemble",
        params={
            "latitude": coords["lat"],
            "longitude": coords["lon"],
            "hourly": "temperature_2m,precipitation,windspeed_10m",
            "models": "gfs_seamless",
            "forecast_days": 3,
            "temperature_unit": "fahrenheit",
        },
        timeout=15
    )
    return resp.json()

def calc_daily_probs(city):
    """Calculate daily weather probabilities from ensemble."""
    data = get_ensemble_forecast(city)
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    
    # Get all temperature member keys
    temp_members = [k for k in hourly.keys() if "temperature_2m" in k]
    precip_members = [k for k in hourly.keys() if "precipitation" in k]
    
    results = []
    today = datetime.utcnow().date()
    
    for day_offset in range(1, 4):
        target_date = today + timedelta(days=day_offset)
        date_str = target_date.strftime("%Y-%m-%d")
        
        # Get hourly indices for this day
        day_indices = [i for i, t in enumerate(times) if t.startswith(date_str)]
        
        if not day_indices:
            continue
        
        # Calculate daily high temp across all ensemble members
        daily_highs = []
        daily_precip_totals = []
        
        for member in temp_members:
            member_temps = [hourly[member][i] for i in day_indices if i < len(hourly[member])]
            if member_temps:
                daily_highs.append(max(member_temps))
        
        for member in precip_members:
            member_precip = [hourly[member][i] for i in day_indices if i < len(hourly[member])]
            if member_precip:
                daily_precip_totals.append(sum(member_precip))
        
        if not daily_highs:
            continue
        
        highs = np.array(daily_highs)
        precips = np.array(daily_precip_totals) if daily_precip_totals else np.zeros(len(daily_highs))
        
        # Calculate probabilities for common thresholds
        result = {
            "city": city,
            "date": date_str,
            "mean_high": float(np.mean(highs)),
            "std_high": float(np.std(highs)),
            "members": len(daily_highs),
            "probs": {}
        }
        
        # Temperature thresholds
        for threshold in [50, 60, 70, 75, 80, 85, 90, 95]:
            prob = float(np.mean(highs > threshold))
            result["probs"][f"high_above_{threshold}f"] = prob
        
        # Rain probability
        rain_prob = float(np.mean(precips > 0.01))
        result["probs"]["any_rain"] = rain_prob
        
        # Heavy rain
        heavy_rain_prob = float(np.mean(precips > 0.5))
        result["probs"]["heavy_rain"] = heavy_rain_prob
        
        results.append(result)
    
    return results

def find_weather_markets():
    """Search Polymarket for active weather markets."""
    markets = []
    try:
        resp = requests.get(
            "https://gamma-api.polymarket.com/events",
            params={
                "limit": 50,
                "active": "true",
                "tag_slug": "weather",
            },
            timeout=10
        )
        data = resp.json()
        for event in data:
            for market in event.get("markets", []):
                question = market.get("question", "").lower()
                token_ids_raw = market.get("clobTokenIds", "[]")
                if isinstance(token_ids_raw, str):
                    token_ids = json.loads(token_ids_raw)
                else:
                    token_ids = token_ids_raw
                
                if token_ids:
                    markets.append({
                        "question": market.get("question", ""),
                        "token_id": token_ids[0],
                        "no_token_id": token_ids[1] if len(token_ids) > 1 else None,
                        "price": float(market.get("bestAsk", 0.5) or 0.5),
                    })
    except Exception as e:
        print(f"Market fetch error: {e}")
    return markets

def match_market_to_forecast(market, forecasts):
    """Try to match a Polymarket question to our forecast data."""
    question = market["question"].lower()
    
    for forecast in forecasts:
        city = forecast["city"].lower()
        date = forecast["date"]
        
        if city not in question and city[:3] not in question:
            continue
        if date not in question and date[5:] not in question:
            continue
        
        probs = forecast["probs"]
        
        # Match temperature thresholds
        for threshold in [50, 60, 70, 75, 80, 85, 90, 95]:
            if str(threshold) in question and "high" in question:
                our_prob = probs.get(f"high_above_{threshold}f", None)
                if our_prob is not None:
                    return our_prob, f"high_above_{threshold}f"
        
        # Match rain
        if "rain" in question or "precipitation" in question:
            if "heavy" in question:
                return probs.get("heavy_rain", None), "heavy_rain"
            return probs.get("any_rain", None), "any_rain"
    
    return None, None

def find_edges(forecasts, markets):
    """Find markets where we have a significant edge."""
    edges = []
    
    for market in markets:
        our_prob, market_type = match_market_to_forecast(market, forecasts)
        
        if our_prob is None:
            continue
        
        market_price = market["price"]
        edge = our_prob - market_price
        
        print(f"\nMarket: {market['question']}")
        print(f"Our probability: {our_prob:.1%}")
        print(f"Market price:    {market_price:.1%}")
        print(f"Edge:            {edge:+.1%}")
        
        if abs(edge) >= MIN_EDGE:
            edges.append({
                "market": market,
                "our_prob": our_prob,
                "market_price": market_price,
                "edge": edge,
                "bet_yes": edge > 0,
            })
    
    return sorted(edges, key=lambda x: abs(x["edge"]), reverse=True)

def place_bet(client, token_id, price, yes=True):
    try:
        order_args = OrderArgs(
            token_id=token_id,
            price=round(price, 2),
            size=ORDER_SIZE,
            side=BUY,
        )
        signed = client.create_order(order_args)
        resp = client.post_order(signed, OrderType.GTC)
        print(f"Placed {'YES' if yes else 'NO'} bet @ {price}: {resp}")
    except Exception as e:
        print(f"Bet error: {e}")

def run():
    client = get_client()
    print("Starting Weather Edge Bot...")
    print(f"Cities: {list(CITIES.keys())}")
    print(f"Min edge: {MIN_EDGE:.0%}")
    print(f"Stop loss: ${STOP_LOSS}")

    starting_balance = get_balance(client)
    if starting_balance is None:
        starting_balance = 10.0
    print(f"Starting balance: ${starting_balance:.2f}")

    while True:
        try:
            # Check stop loss
            current_balance = get_balance(client)
            if current_balance is not None:
                loss = starting_balance - current_balance
                print(f"\nBalance: ${current_balance:.2f} | Loss: ${loss:.2f}")
                if loss >= STOP_LOSS:
                    print(f"STOP LOSS HIT! Lost ${loss:.2f}. Shutting down.")
                    break

            print(f"\n{'='*50}")
            print(f"Scanning weather markets... {datetime.utcnow().strftime('%H:%M UTC')}")

            # Get forecasts for all cities
            all_forecasts = []
            for city in CITIES:
                print(f"Fetching {city} forecast...")
                try:
                    forecasts = calc_daily_probs(city)
                    all_forecasts.extend(forecasts)
                    for f in forecasts:
                        print(f"  {city} {f['date']}: mean high {f['mean_high']:.1f}°F")
                except Exception as e:
                    print(f"  Error fetching {city}: {e}")

            # Find weather markets
            print("\nScanning Polymarket for weather markets...")
            markets = find_weather_markets()
            print(f"Found {len(markets)} weather markets")
            for m in markets[:15]:
    print(f"  - {m['question']}")

            if markets:
                # Find edges
                edges = find_edges(all_forecasts, markets)
                print(f"\nFound {len(edges)} edges >= {MIN_EDGE:.0%}")

                for edge in edges[:3]:  # Max 3 bets per cycle
                    market = edge["market"]
                    print(f"\nBetting on: {market['question']}")
                    print(f"Edge: {edge['edge']:+.1%}")

                    if edge["bet_yes"]:
                        place_bet(client, market["token_id"], edge["our_prob"] - 0.02, yes=True)
                    else:
                        if market["no_token_id"]:
                            place_bet(client, market["no_token_id"], 1 - edge["our_prob"] - 0.02, yes=False)
            else:
                print("No weather markets found on Polymarket right now.")
                print("This bot works best when Polymarket has active weather markets.")

            print("\nSleeping 1 hour before next scan...")
            time.sleep(3600)

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    run()
