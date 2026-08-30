import asyncio
import os
import time
from aiohttp import web, ClientSession
import websockets
import json

# --- CONFIGURATION IDENTIFIANTS ---
TELEGRAM_BOT_TOKEN = "8834699234:AAHnqWUWz8auv0LbJDuMePTaeky8kmqIu0o"
TELEGRAM_CHAT_ID = "759626963"

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
CANDLE_COUNT = 50

HTTP_SESSION = None
SCAN_IN_PROGRESS = False


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
        print(f"Erreur Envoi Telegram: {e}")


async def get_candles(symbol):
    async with websockets.connect(DERIV_WS_URL) as ws:
        req = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": CANDLE_COUNT,
            "end": "latest",
            "style": "candles",
            "granularity": 900,  # 15 minutes
        }
        await ws.send(json.dumps(req))
        res = json.loads(await ws.recv())
        return res.get("candles", [])


def extract_pivots_5bars(history_candles):
    """
    Détecte les vrais creux et sommets standard (Fractale 5 bougies : 2 à gauche, 2 à droite).
    """
    n = len(history_candles)
    highs = []
    lows = []

    for i in range(2, n - 2):
        curr_h = float(history_candles[i]["high"])
        curr_l = float(history_candles[i]["low"])

        # Sommet Pivot (plus haut que les 2 précédentes et les 2 suivantes)
        if (curr_h > float(history_candles[i - 1]["high"]) and
            curr_h > float(history_candles[i - 2]["high"]) and
            curr_h > float(history_candles[i + 1]["high"]) and
            curr_h > float(history_candles[i + 2]["high"])):
            highs.append((curr_h, i))

        # Creux Pivot (plus bas que les 2 précédentes et les 2 suivantes)
        if (curr_l < float(history_candles[i - 1]["low"]) and
            curr_l < float(history_candles[i - 2]["low"]) and
            curr_l < float(history_candles[i + 1]["low"]) and
            curr_l < float(history_candles[i + 2]["low"])):
            lows.append((curr_l, i))

    return highs, lows


def check_liquidity_reentry(candles, market_name):
    if len(candles) < 25:
        return None

    # candles[-1] = Bougie en cours (ignorée)
    # candles[-2] = Bougie 2 (Réintégration, 100% clôturée)
    # candles[-3] = Bougie 1 (Prise de liquidité / Cassure, 100% clôturée)
    c2 = candles[-2]
    c1 = candles[-3]
    history = candles[:-3]

    pivots_high, pivots_low = extract_pivots_5bars(history)

    o1, c1_close, h1, l1 = float(c1["open"]), float(c1["close"]), float(c1["high"]), float(c1["low"])
    o2, c2_close, h2, l2 = float(c2["open"]), float(c2["close"]), float(c2["high"]), float(c2["low"])

    range_c2 = h2 - l2
    if range_c2 <= 0:
        return None
    body_c2 = abs(c2_close - o2)
    body_ratio_c2 = body_c2 / range_c2
    is_strong_body = body_ratio_c2 >= 0.50

    if not is_strong_body:
        return None

    len_history = len(history)

    # 1. SETUP VENTE (Prise de Liquidité sur Vrai Sommet 5 barres)
    if c2_close < o2:  # Bougie 2 Rouge
        for sommet_ref, idx in reversed(pivots_high):
            # Écart minimum de 4 bougies entre le sommet et la cassure
            if (len_history - idx) >= 4:
                if c1_close > sommet_ref and c2_close < sommet_ref:
                    sl = max(h1, h2)
                    body_pct = round(body_ratio_c2 * 100, 1)
                    bars_ago = (len_history - idx) + 2
                    return (
                        f"🚨 *SIGNAL VENTE (Prise de Liquidité M15)* 🚨\n\n"
                        f"📊 *Marché* : {market_name}\n"
                        f"🎯 *Entrée (Sell)* : `{c2_close}`\n"
                        f"🛑 *Stop Loss (SL)* : `{sl}`\n"
                        f"📌 *Sommet balayé* : `{sommet_ref}` (il y a {bars_ago} bougies)\n"
                        f"📈 *Bougie 1* : Clôture au-dessus de la mèche du sommet\n"
                        f"📉 *Bougie 2* : Réintégration sous la mèche (Corps: {body_pct}%)"
                    )

    # 2. SETUP ACHAT (Prise de Liquidité sur Vrai Creux 5 barres)
    if c2_close > o2:  # Bougie 2 Verte
        for creux_ref, idx in reversed(pivots_low):
            # Écart minimum de 4 bougies entre le creux et la cassure
            if (len_history - idx) >= 4:
                if c1_close < creux_ref and c2_close > creux_ref:
                    sl = min(l1, l2)
                    body_pct = round(body_ratio_c2 * 100, 1)
                    bars_ago = (len_history - idx) + 2
                    return (
                        f"🟢 *SIGNAL ACHAT (Prise de Liquidité M15)* 🟢\n\n"
                        f"📊 *Marché* : {market_name}\n"
                        f"🎯 *Entrée (Buy)* : `{c2_close}`\n"
                        f"🛑 *Stop Loss (SL)* : `{sl}`\n"
                        f"📌 *Creux balayé* : `{creux_ref}` (il y a {bars_ago} bougies)\n"
                        f"📉 *Bougie 1* : Clôture sous la mèche du creux\n"
                        f"📈 *Bougie 2* : Réintégration au-dessus de la mèche (Corps: {body_pct}%)"
                    )

    return None


async def run_scan(is_manual=False):
    global SCAN_IN_PROGRESS
    if SCAN_IN_PROGRESS:
        if is_manual:
            await send_telegram_alert("⚠️ *Une analyse est déjà en cours...*")
        return

    SCAN_IN_PROGRESS = True
    try:
        found_signals = 0
        if is_manual:
            await send_telegram_alert("⏳ *Analyse manuelle en cours sur vos 22 marchés...*")

        for mkt in MARKETS:
            try:
                candles = await get_candles(mkt["symbol"])
                alert = check_liquidity_reentry(candles, mkt["name"])
                if alert:
                    await send_telegram_alert(alert)
                    found_signals += 1
            except Exception as e:
                print(f"Erreur sur {mkt['symbol']}: {e}")

        if is_manual and found_signals == 0:
            await send_telegram_alert("ℹ️ *Scan terminé : Aucun signal détecté pour le moment.*")
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
                                asyncio.create_task(run_scan(is_manual=True))
        except Exception as e:
            print(f"Polling exception: {e}")
        await asyncio.sleep(1)


async def scheduled_scanner():
    last_scanned_min = -1
    while True:
        now = time.gmtime()
        m = now.tm_min
        if m in [0, 15, 30, 45] and m != last_scanned_min:
            await asyncio.sleep(5)
            await run_scan(is_manual=False)
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
        "🤖 *Scanner M15 calibré (Pivots 5 bougies).*\n\n"
        "• Détection équilibrée des sommets et creux clés.\n"
        "• Envoyez `/scan` pour déclencher une analyse manuelle."
    )

    await asyncio.gather(
        scheduled_scanner(),
        listen_telegram(),
    )


if __name__ == "__main__":
    asyncio.run(main())
