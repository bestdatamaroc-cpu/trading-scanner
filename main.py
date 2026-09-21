import asyncio
import os
import time
from aiohttp import web, ClientSession
import websockets
import json

# --- CONFIGURATION TELEGRAM ---
TELEGRAM_BOT_TOKEN = "8834699234:AAHnqWUWz8auv0LbJDuMePTaeky8kmqIu0o"
TELEGRAM_CHAT_ID = "759626963"

# --- LISTE DES MARCHÉS DERIV ---
MARKETS = [
    # Volatility Indices
    {"symbol": "R_10", "name": "Volatility 10 Index"},
    {"symbol": "R_25", "name": "Volatility 25 Index"},
    {"symbol": "R_50", "name": "Volatility 50 Index"},
    {"symbol": "R_75", "name": "Volatility 75 Index"},
    {"symbol": "R_100", "name": "Volatility 100 Index"},
    # Volatility (1s) Indices
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
    # Jump & Step
    {"symbol": "JD10", "name": "Jump 10 Index"},
    {"symbol": "JD25", "name": "Jump 25 Index"},
    {"symbol": "JD75", "name": "Jump 75 Index"},
    {"symbol": "JD100", "name": "Jump 100 Index"},
    {"symbol": "stpRNG", "name": "Step Index"},
    # Commodities
    {"symbol": "frxXAUUSD", "name": "Gold (XAUUSD)"},
    {"symbol": "frxXAGUSD", "name": "Silver (XAGUSD)"},
]

# --- TIMEFRAMES SUPPORTÉS ---
TIMEFRAMES = [
    {"label": "M15", "seconds": 900},
    {"label": "M30", "seconds": 1800},
    {"label": "1H",  "seconds": 3600},
    {"label": "2H",  "seconds": 7200},
    {"label": "3H",  "seconds": 10800},
    {"label": "4H",  "seconds": 14400},
]

APP_ID = "1089"
DERIV_WS_URL = f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"
TOLERANCE_PCT = 0.015  # 1.5% max de mèche tolérée

HTTP_SESSION = None
SCAN_IN_PROGRESS = False
ALREADY_ALERTED = set()


async def send_telegram_alert(message):
    global HTTP_SESSION
    if HTTP_SESSION is None or HTTP_SESSION.closed:
        HTTP_SESSION = ClientSession()

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        async with HTTP_SESSION.post(url, json=payload, timeout=10) as resp:
            pass
    except Exception as e:
        print(f"Erreur Telegram: {e}")


async def get_candles(symbol, granularity, count=32):
    async with websockets.connect(DERIV_WS_URL) as ws:
        req = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "style": "candles",
            "granularity": granularity,
        }
        await ws.send(json.dumps(req))
        res = json.loads(await ws.recv())
        return res.get("candles", [])


def analyze_trend_24_candles(candles_24):
    """
    Analyse les 24 bougies clôturées en retenant uniquement celles
    dont le corps représente plus de 50% de la hauteur totale (High - Low).
    """
    green_impulsive = 0
    red_impulsive = 0
    neutral_count = 0

    for c in candles_24:
        o = float(c["open"])
        cl = float(c["close"])
        h = float(c["high"])
        l = float(c["low"])

        candle_range = h - l
        if candle_range <= 0:
            neutral_count += 1
            continue

        body_size = abs(cl - o)
        body_ratio = body_size / candle_range

        if body_ratio > 0.50:
            if cl > o:
                green_impulsive += 1
            else:
                red_impulsive += 1
        else:
            neutral_count += 1

    total_impulsive = green_impulsive + red_impulsive
    if total_impulsive == 0:
        verdict = "⚪ Neutre (Aucun corps > 50%)"
    elif green_impulsive > red_impulsive:
        verdict = f"🟢 Flux Haussier ({green_impulsive} vertes vs {red_impulsive} rouges)"
    elif red_impulsive > green_impulsive:
        verdict = f"🔴 Flux Baissier ({red_impulsive} rouges vs {green_impulsive} vertes)"
    else:
        verdict = f"⚪ Équilibré ({green_impulsive} vertes / {red_impulsive} rouges)"

    return f"{verdict} | Dojis/Faibles : {neutral_count}/24"


def check_marubozu_pattern(candle, tolerance_pct=TOLERANCE_PCT):
    o = float(candle["open"])
    c = float(candle["close"])
    h = float(candle["high"])
    l = float(candle["low"])

    total_range = h - l
    if total_range <= 0:
        return None

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    # 1. Bougie ROUGE sans mèche haute
    if c < o:
        if (upper_wick / total_range) <= tolerance_pct:
            return "RED_NO_UPPER_WICK", o, c, h, l

    # 2. Bougie VERTE sans mèche basse
    elif c > o:
        if (lower_wick / total_range) <= tolerance_pct:
            return "GREEN_NO_LOWER_WICK", o, c, h, l

    return None


async def run_multi_timeframe_scan(is_manual=False):
    global SCAN_IN_PROGRESS, ALREADY_ALERTED
    if SCAN_IN_PROGRESS:
        if is_manual:
            await send_telegram_alert("⚠️ *Scan déjà en cours...*")
        return

    SCAN_IN_PROGRESS = True
    try:
        found_signals = 0
        if is_manual:
            await send_telegram_alert("⏳ *Scan Multi-Timeframe en cours (Analyse 24 bougies + Détection mèches)...*")

        for mkt in MARKETS:
            for tf in TIMEFRAMES:
                try:
                    candles = await get_candles(mkt["symbol"], granularity=tf["seconds"], count=28)
                    if len(candles) < 26:
                        continue

                    # candles[-1] : bougie en cours
                    # candles[-2] : dernière bougie clôturée (le signal)
                    # candles[-26:-2] : les 24 bougies clôturées précédant le signal
                    closed_candle = candles[-2]
                    history_24 = candles[-26:-2]
                    
                    epoch = closed_candle.get("epoch")
                    result = check_marubozu_pattern(closed_candle)

                    if result:
                        pattern_type, o, c, h, l = result
                        alert_id = f"{mkt['symbol']}_{tf['label']}_{epoch}_{pattern_type}"

                        if alert_id not in ALREADY_ALERTED:
                            ALREADY_ALERTED.add(alert_id)
                            found_signals += 1

                            trend_summary = analyze_trend_24_candles(history_24)

                            if pattern_type == "RED_NO_UPPER_WICK":
                                msg = (
                                    f"🔴 *BOUGIE ROUGE SANS MÈCHE SUPÉRIEURE* 🔴\n\n"
                                    f"📊 *Marché* : {mkt['name']}\n"
                                    f"⏱️ *Timeframe* : `{tf['label']}`\n"
                                    f"🚪 *Open* : `{o}` | *High* : `{h}`\n"
                                    f"🎯 *Clôture* : `{c}` | *Low* : `{l}`\n\n"
                                    f"📊 *Tendance (24 bougies, corps > 50%)* :\n{trend_summary}\n"
                                    f"⚡ *Pression* : Vente immédiate à l'ouverture"
                                )
                            else:
                                msg = (
                                    f"🟢 *BOUGIE VERTE SANS MÈCHE INFÉRIEURE* 🟢\n\n"
                                    f"📊 *Marché* : {mkt['name']}\n"
                                    f"⏱️ *Timeframe* : `{tf['label']}`\n"
                                    f"🚪 *Open* : `{o}` | *Low* : `{l}`\n"
                                    f"🎯 *Clôture* : `{c}` | *High* : `{h}`\n\n"
                                    f"📊 *Tendance (24 bougies, corps > 50%)* :\n{trend_summary}\n"
                                    f"⚡ *Pression* : Achat immédiat à l'ouverture"
                                )
                            await send_telegram_alert(msg)

                except Exception as e:
                    print(f"Erreur sur {mkt['symbol']} en {tf['label']}: {e}")

        if is_manual and found_signals == 0:
            await send_telegram_alert("ℹ️ *Scan terminé : Aucune bougie sans mèche détectée sur les timeframes analysés.*")

    finally:
        SCAN_IN_PROGRESS = False


async def listen_telegram():
    global HTTP_SESSION
    last_update_id = None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    try:
        if HTTP_SESSION is None or HTTP_SESSION.closed:
            HTTP_SESSION = ClientSession()
        async with HTTP_SESSION.get(url, params={"offset": -1}, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get("result", [])
                if results:
                    last_update_id = results[-1]["update_id"] + 1
    except Exception as e:
        print(f"Erreur purge Telegram: {e}")

    while True:
        try:
            if HTTP_SESSION is None or HTTP_SESSION.closed:
                HTTP_SESSION = ClientSession()

            params = {"timeout": 15, "offset": last_update_id}
            async with HTTP_SESSION.get(url, params=params, timeout=20) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data.get("result", []):
                        last_update_id = item["update_id"] + 1
                        msg = item.get("message", {})
                        text = msg.get("text", "").strip().lower()
                        sender_id = str(msg.get("chat", {}).get("id", ""))

                        if sender_id == str(TELEGRAM_CHAT_ID):
                            if text in ["/scan", "scan", "/start", "/ scan"]:
                                asyncio.create_task(run_multi_timeframe_scan(is_manual=True))
        except Exception as e:
            print(f"Polling Telegram exception: {e}")
        await asyncio.sleep(1)


async def scheduled_scanner():
    """Analyse automatique toutes les 15 minutes."""
    last_scanned_min = -1
    while True:
        now = time.gmtime()
        m = now.tm_min
        if m in [0, 15, 30, 45] and m != last_scanned_min:
            await asyncio.sleep(5)
            await run_multi_timeframe_scan(is_manual=False)
            last_scanned_min = m
        await asyncio.sleep(5)


async def handle_ping(request):
    return web.Response(text="Bot actif 24/7")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def main():
    global HTTP_SESSION
    HTTP_SESSION = ClientSession()
    await start_web_server()

    await send_telegram_alert(
        "🤖 *Scanner Multi-Timeframe actif.*\n\n"
        "• Analyse : Détection bougies sans mèche (15m à 4h).\n"
        "• Contexte : Tendance sur les 24 dernières bougies (corps > 50%).\n"
        "• Scan auto toutes les 15 min ou via `/scan`."
    )

    await asyncio.gather(
        scheduled_scanner(),
        listen_telegram(),
    )


if __name__ == "__main__":
    asyncio.run(main())
