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
    # --- Volatility Indices ---
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
    # --- Jump & Step ---
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
GRANULARITY_H4 = 14400  # 4 heures en secondes

HTTP_SESSION = None
SCAN_IN_PROGRESS = False
# Évite d'envoyer la même alerte en boucle toutes les 30 min sur la même bougie H4
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
        print(f"Erreur envoi Telegram: {e}")


async def get_candles(symbol, granularity=GRANULARITY_H4, count=6):
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


def check_crt_live_h4(candles, symbol, market_name):
    global ALREADY_ALERTED
    if len(candles) < 3:
        return None

    # candles[-1] : Bougie H4 EN COURS (B3 actuelle, suivie toutes les 30 min)
    # candles[-2] : Bougie H4 précédente (B2 = sweep de liquidité validé)
    # candles[-3] : Bougie H4 d'avant (B1 = référence)
    b3_live = candles[-1]
    b2 = candles[-2]
    b1 = candles[-3]

    b3_epoch = b3_live.get("epoch")

    o1, c1, h1, l1 = float(b1["open"]), float(b1["close"]), float(b1["high"]), float(b1["low"])
    o2, c2, h2, l2 = float(b2["open"]), float(b2["close"]), float(b2["high"]), float(b2["low"])
    c3_live = float(b3_live["close"])  # Prix en direct lors du scan
    h3_live = float(b3_live["high"])
    l3_live = float(b3_live["low"])

    # ----------------------------------------------------
    # 1. SETUP VENTE (CRT BEARISH H4)
    # ----------------------------------------------------
    # B1 haussière
    if c1 > o1:
        # B2 balaie le sommet de B1 mais sa clôture reste SOUS la mèche haute de B1
        if h2 > h1 and c2 < h1:
            # B3 en cours casse le niveau de clôture de B2
            if c3_live < c2:
                alert_key = f"{symbol}_SELL_{b3_epoch}"
                if alert_key in ALREADY_ALERTED:
                    return None

                sl = max(h2, h3_live)
                entry = c3_live
                tp = l1  # Bas de la mèche de B1

                risk = sl - entry
                if risk > 0 and entry > tp:
                    reward = entry - tp
                    rr = round(reward / risk, 2)
                    ALREADY_ALERTED.add(alert_key)
                    return (
                        f"🚨 *SIGNAL VENTE - CRT H4 (Scan 30m)* 🚨\n\n"
                        f"📊 *Marché* : {market_name}\n"
                        f"🎯 *Entrée (Sell direct)* : `{entry}`\n"
                        f"🛑 *Stop Loss (Mèche B2/B3)* : `{sl}`\n"
                        f"🎯 *Take Profit (Bas B1 H4)* : `{tp}` (R:R {rr})\n\n"
                        f"📌 *1. Bougie Réf (B1)* : Sommet `{h1}`\n"
                        f"⚡ *2. Liquidity Sweep (B2)* : Mèche `{h2}` rejetée sous `{h1}` | Clôture B2 `{c2}`\n"
                        f"📉 *3. Déclencheur B3 H4* : Prix sous la clôture de B2 (`{c2}`)"
                    )

    # ----------------------------------------------------
    # 2. SETUP ACHAT (CRT BULLISH H4)
    # ----------------------------------------------------
    # B1 baissière
    if c1 < o1:
        # B2 balaie le creux de B1 mais sa clôture reste AU-DESSUS de la mèche basse de B1
        if l2 < l1 and c2 > l1:
            # B3 en cours casse le niveau de clôture de B2
            if c3_live > c2:
                alert_key = f"{symbol}_BUY_{b3_epoch}"
                if alert_key in ALREADY_ALERTED:
                    return None

                sl = min(l2, l3_live)
                entry = c3_live
                tp = h1  # Haut de la mèche de B1

                risk = entry - sl
                if risk > 0 and tp > entry:
                    reward = tp - entry
                    rr = round(reward / risk, 2)
                    ALREADY_ALERTED.add(alert_key)
                    return (
                        f"🟢 *SIGNAL ACHAT - CRT H4 (Scan 30m)* 🟢\n\n"
                        f"📊 *Marché* : {market_name}\n"
                        f"🎯 *Entrée (Buy direct)* : `{entry}`\n"
                        f"🛑 *Stop Loss (Mèche B2/B3)* : `{sl}`\n"
                        f"🎯 *Take Profit (Haut B1 H4)* : `{tp}` (R:R {rr})\n\n"
                        f"📌 *1. Bougie Réf (B1)* : Creux `{l1}`\n"
                        f"⚡ *2. Liquidity Sweep (B2)* : Mèche `{l2}` rejetée sur `{l1}` | Clôture B2 `{c2}`\n"
                        f"📈 *3. Déclencheur B3 H4* : Prix au-dessus de la clôture de B2 (`{c2}`)"
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
            await send_telegram_alert("⏳ *Scan CRT H4 en cours (Vérification toutes les 30 min)...*")

        for mkt in MARKETS:
            try:
                candles = await get_candles(mkt["symbol"])
                alert = check_crt_live_h4(candles, mkt["symbol"], mkt["name"])
                if alert:
                    await send_telegram_alert(alert)
                    found_signals += 1
            except Exception as e:
                print(f"Erreur sur {mkt['symbol']}: {e}")

        if is_manual and found_signals == 0:
            await send_telegram_alert("ℹ️ *Scan terminé : Aucun setup CRT H4 valide à cet instant.*")
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
            print(f"Polling Telegram exception: {e}")
        await asyncio.sleep(1)


async def scheduled_scanner():
    last_scanned_min = -1
    while True:
        now = time.gmtime()
        m = now.tm_min
        # Scan automatique régulier toutes les 30 minutes (:00 et :30)
        if m in [0, 30] and m != last_scanned_min:
            await asyncio.sleep(5)
            await run_scan(is_manual=False)
            last_scanned_min = m
        await asyncio.sleep(5)


async def handle_ping(request):
    return web.Response(text="Bot CRT H4 actif 24/7")


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
        "🤖 *Scanner CRT H4 actif (Scan toutes les 30 min).*\n\n"
        "• Figure : H4 (B1 Réf -> B2 Sweep -> B3 Cassure clôture B2).\n"
        "• Analyse automatique : Toutes les 30 min (:00 et :30).\n"
        "• Tapez `/scan` pour forcer une analyse immédiate."
    )

    await asyncio.gather(
        scheduled_scanner(),
        listen_telegram(),
    )


if __name__ == "__main__":
    asyncio.run(main())

