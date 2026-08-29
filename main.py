import asyncio
import json
import time
import requests
import websockets

# --- CONFIGURATION TELEGRAM ---
TELEGRAM_BOT_TOKEN = "8834699234:AAHnqWUwz8auv0LbJDuMePTaeky8kmqIu0o"
TELEGRAM_CHAT_ID = "759626963"  # Remplacez uniquement par votre numéro Chat ID

MARKETS = [
    # --- Volatility Indices (Standards) ---
    {"symbol": "R_10", "name": "Volatility 10 Index"},
    {"symbol": "R_25", "name": "Volatility 25 Index"},
    {"symbol": "R_50", "name": "Volatility 50 Index"},
    {"symbol": "R_75", "name": "Volatility 75 Index"},
    {"symbol": "R_100", "name": "Volatility 100 Index"},
    # --- Volatility (1s) Indices ---
    {"symbol": "1HZ10V", "name": "Volatility 10 (1s) Index"},
    {"symbol": "1HZ15V", "name": "Volatility 15 (1s) Index"},
    {"symbol": "1HZ25V", "name": "Volatility 25 (1s) Index"},
    {"symbol": "1HZ30V", "name": "Volatility 30 (1s) Index"},
    {"symbol": "1HZ50V", "name": "Volatility 50 (1s) Index"},
    {"symbol": "1HZ75V", "name": "Volatility 75 (1s) Index"},
    {"symbol": "1HZ90V", "name": "Volatility 90 (1s) Index"},
    {"symbol": "1HZ100V", "name": "Volatility 100 (1s) Index"},
    {"symbol": "1HZ150V", "name": "Volatility 150 (1s) Index"},
    {"symbol": "1HZ250V", "name": "Volatility 250 (1s) Index"},
    # --- Jump Indices & Step Index ---
    {"symbol": "JD10", "name": "Jump 10 Index"},
    {"symbol": "JD25", "name": "Jump 25 Index"},
    {"symbol": "JD75", "name": "Jump 75 Index"},
    {"symbol": "JD100", "name": "Jump 100 Index"},
    {"symbol": "stpRNG", "name": "Step Index"},
    # --- Commodities ---
    {"symbol": "frxXAUUSD", "name": "Gold (XAUUSD)"},
    {"symbol": "frxXAGUSD", "name": "Silver (XAGUSD)"},
]

APP_ID = "1089"
DERIV_WS_URL = f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"
LOOKBACK_CANDLES = 15
WICK_THRESHOLD = 0.35


def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Erreur Telegram: {e}")


async def get_candles(symbol):
    async with websockets.connect(DERIV_WS_URL) as ws:
        req = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": LOOKBACK_CANDLES + 5,
            "end": "latest",
            "style": "candles",
            "granularity": 900,  # 15 minutes
        }
        await ws.send(json.dumps(req))
        res = json.loads(await ws.recv())
        return res.get("candles", [])


def check_liquidity_sweep(candles, market_name):
    if len(candles) < LOOKBACK_CANDLES + 2:
        return None

    trigger = candles[-2]
    prev_candles = candles[-(LOOKBACK_CANDLES + 2) : -2]

    high_t = float(trigger["high"])
    low_t = float(trigger["low"])
    open_t = float(trigger["open"])
    close_t = float(trigger["close"])

    swing_high = max(float(c["high"]) for c in prev_candles)
    swing_low = min(float(c["low"]) for c in prev_candles)

    c_range = high_t - low_t
    if c_range <= 0:
        return None

    upper_wick = high_t - max(open_t, close_t)
    lower_wick = min(open_t, close_t) - low_t

    # Vente (Bearish Sweep)
    if (high_t > swing_high) and (close_t < swing_high):
        if (upper_wick / c_range) >= WICK_THRESHOLD:
            return (
                f"🚨 *SIGNAL VENTE (Liquidity Sweep)* 🚨\n\n"
                f"📊 *Marché* : {market_name} (M15)\n"
                f"🎯 *Entrée* : `{close_t}`\n"
                f"🛑 *Stop Loss* : `{high_t}`\n"
                f"📌 *Sommet balayé* : `{swing_high}`"
            )

    # Achat (Bullish Sweep)
    if (low_t < swing_low) and (close_t > swing_low):
        if (lower_wick / c_range) >= WICK_THRESHOLD:
            return (
                f"🟢 *SIGNAL ACHAT (Liquidity Sweep)* 🟢\n\n"
                f"📊 *Marché* : {market_name} (M15)\n"
                f"🎯 *Entrée* : `{close_t}`\n"
                f"🛑 *Stop Loss* : `{low_t}`\n"
                f"📌 *Creux balayé* : `{swing_low}`"
            )
    return None


async def main():
    print("Scanner lancé...")
    send_telegram_alert("🤖 *Scanner M15 actif sur vos marchés.*")
    last_scanned_min = -1

    while True:
        now = time.gmtime()
        m = now.tm_min
        if m in [0, 15, 30, 45] and m != last_scanned_min:
            await asyncio.sleep(5)
            for mkt in MARKETS:
                try:
                    candles = await get_candles(mkt["symbol"])
                    alert = check_liquidity_sweep(candles, mkt["name"])
                    if alert:
                        send_telegram_alert(alert)
                except Exception as e:
                    print(f"Erreur {mkt['symbol']}: {e}")
            last_scanned_min = m
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
