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
        print(f"Erreur Telegram: {e}")


async def get_candles(symbol, granularity, count):
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


def check_h4_m5_reentry(candles_h4, candles_m5, market_name):
    if len(candles_h4) < 3 or len(candles_m5) < 15:
        return None

    # Bougies H4 :
    # candles_h4[-1] : Bougie H4 en cours (ouverte)
    # b2_h4 : 2ème bougie H4 clôturée (celle qui sweep B1)
    # b1_h4 : 1ère bougie H4 clôturée (référence)
    b2_h4 = candles_h4[-2]
    b1_h4 = candles_h4[-3]

    h1_high = float(b1_h4["high"])
    h1_low = float(b1_h4["low"])
    h2_high = float(b2_h4["high"])
    h2_low = float(b2_h4["low"])
    h2_close = float(b2_h4["close"])

    # Moment précis de clôture de B2 en timestamp epoch (ouverture + 4 heures)
    b2_close_time = int(b2_h4["epoch"]) + 14400

    # Filtrer strictement les bougies M5 fermées créées APRÈS la clôture de B2
    closed_m5_after_b2 = [
        c for c in candles_m5[:-1] if int(c["epoch"]) >= b2_close_time
    ]

    # Il faut au moins 2 bougies M5 clôturées après B2 pour vérifier le franchissement et la clôture
    if len(closed_m5_after_b2) < 2:
        return None

    c_sig = closed_m5_after_b2[-1]
    c_prev = closed_m5_after_b2[-2]
    close_m5 = float(c_sig["close"])

    # -------------------------------------------------------------------------
    # 1. SETUP VENTE
    # H4: B2 balaie le high de B1 et clôture sous ce high
    # M5 (après B2): Monte au-dessus de la clôture B2, puis clôture en dessous
    # -------------------------------------------------------------------------
    h4_sell_pattern = (h2_high > h1_high) and (h2_close < h1_high)
    if h4_sell_pattern:
        wick_above_close_b2 = any(float(c["high"]) > h2_close for c in closed_m5_after_b2)

        if wick_above_close_b2 and close_m5 < h2_close:
            # Signal validé précisément à la réintégration
            if float(c_prev["close"]) >= h2_close or float(c_sig["high"]) > h2_close:
                sl = max(float(c["high"]) for c in closed_m5_after_b2)
                tp = h1_low

                if close_m5 > tp and sl > close_m5:
                    return (
                        f"🚨 *SIGNAL VENTE - BALAYAGE H4 & REJET M5* 🚨\n\n"
                        f"📊 *Marché* : {market_name}\n"
                        f"🎯 *Entrée (Sell)* : `{close_m5}`\n"
                        f"🛑 *Stop Loss (SL mèche M5)* : `{sl}`\n"
                        f"🎯 *Take Profit (Mèche basse B1 H4)* : `{tp}`\n"
                        f"📌 *Balayage H4* : B2 a balayé le sommet B1 (`{h1_high}`) et a fermé sous sa mèche\n"
                        f"⚡ *Déclencheur M5 post-B2* : Montée au-dessus de la clôture B2 (`{h2_close}`), puis clôture en dessous"
                    )

    # -------------------------------------------------------------------------
    # 2. SETUP ACHAT
    # H4: B2 balaie le low de B1 et clôture au-dessus de ce low
    # M5 (après B2): Descend sous la clôture B2, puis clôture au-dessus
    # -------------------------------------------------------------------------
    h4_buy_pattern = (h2_low < h1_low) and (h2_close > h1_low)
    if h4_buy_pattern:
        wick_below_close_b2 = any(float(c["low"]) < h2_close for c in closed_m5_after_b2)

        if wick_below_close_b2 and close_m5 > h2_close:
            # Signal validé précisément à la réintégration
            if float(c_prev["close"]) <= h2_close or float(c_sig["low"]) < h2_close:
                sl = min(float(c["low"]) for c in closed_m5_after_b2)
                tp = h1_high

                if close_m5 < tp and sl < close_m5:
                    return (
                        f"🟢 *SIGNAL ACHAT - BALAYAGE H4 & REJET M5* 🟢\n\n"
                        f"📊 *Marché* : {market_name}\n"
                        f"🎯 *Entrée (Buy)* : `{close_m5}`\n"
                        f"🛑 *Stop Loss (SL mèche M5)* : `{sl}`\n"
                        f"🎯 *Take Profit (Mèche haute B1 H4)* : `{tp}`\n"
                        f"📌 *Balayage H4* : B2 a balayé le creux B1 (`{h1_low}`) et a fermé sur sa mèche\n"
                        f"⚡ *Déclencheur M5 post-B2* : Descente sous la clôture B2 (`{h2_close}`), puis clôture au-dessus"
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
            await send_telegram_alert("⏳ *Scan H4/M5 en cours (Validation stricte post-clôture B2)...*")

        for mkt in MARKETS:
            try:
                # 14400s = H4 (5 bougies), 300s = M5 (60 bougies pour couvrir largement la période post-B2)
                candles_h4 = await get_candles(mkt["symbol"], granularity=14400, count=5)
                candles_m5 = await get_candles(mkt["symbol"], granularity=300, count=60)

                alert = check_h4_m5_reentry(candles_h4, candles_m5, mkt["name"])
                if alert:
                    await send_telegram_alert(alert)
                    found_signals += 1
            except Exception as e:
                print(f"Erreur sur {mkt['symbol']}: {e}")

        if is_manual and found_signals == 0:
            await send_telegram_alert("ℹ️ *Scan terminé : Aucun setup H4/M5 post-clôture validé actuellement.*")
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
        # Vérification à chaque clôture d'une bougie M5
        if m % 5 == 0 and m != last_scanned_min:
            await asyncio.sleep(5)
            await run_scan(is_manual=False)
            last_scanned_min = m
        await asyncio.sleep(5)


async def handle_ping(request):
    return web.Response(text="Bot H4/M5 actif 24/7")


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
        "🤖 *Scanner H4/M5 actif (Séquence post-clôture B2).*\n\n"
        "• Contexte H4 : B2 sweep B1 et clôture sous/sur sa mèche.\n"
        "• M5 : Détection du dépassement et de la réintégration UNIQUEMENT après la fin de B2.\n"
        "• SL sur mèche M5 & TP sur mèche extrême de B1 H4.\n"
        "• Envoyez `/scan` pour tester manuellement."
    )

    await asyncio.gather(
        scheduled_scanner(),
        listen_telegram(),
    )


if __name__ == "__main__":
    asyncio.run(main())
