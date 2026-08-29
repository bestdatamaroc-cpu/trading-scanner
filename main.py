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
CANDLE_COUNT = 40  # Récupère 40 bougies pour identifier les vrais pivots

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


def find_last_true_pivots(candles_history, left_bars=2, right_bars=2):
    """
    Identifie le dernier VRAI creux (Swing Low) et le dernier VRAI sommet (Swing High)
    avec confirmation de left_bars à gauche et right_bars à droite.
    """
    last_pivot_high = None
    last_pivot_low = None

    n = len(candles_history)
    # On parcourt du plus récent au plus ancien
    for i in range(n - 1 - right_bars, left_bars - 1, -1):
        curr_high = float(candles_history[i]["high"])
        curr_low = float(candles_history[i]["low"])

        # Test Pivot High (Sommet)
        if last_pivot_high is None:
            is_high = True
            for j in range(1, left_bars + 1):
                if float(candles_history[i - j]["high"]) >= curr_high:
                    is_high = False
                    break
            if is_high:
                for j in range(1, right_bars + 1):
                    if float(candles_history[i + j]["high"]) >= curr_high:
                        is_high = False
                        break
            if is_high:
                last_pivot_high = curr_high

        # Test Pivot Low (Creux)
        if last_pivot_low is None:
            is_low = True
            for j in range(1, left_bars + 1):
                if float(candles_history[i - j]["low"]) <= curr_low:
                    is_low = False
                    break
            if is_low:
                for j in range(1, right_bars + 1):
                    if float(candles_history[i + j]["low"]) <= curr_low:
                        is_low = False
                        break
            if is_low:
                last_pivot_low = curr_low

        if last_pivot_high is not None and last_pivot_low is not None:
            break

    return last_pivot_high, last_pivot_low


def check_liquidity_reentry(candles, market_name):
    if len(candles) < 20:
        return None

    # candles[-1] = Bougie en cours (ignorée)
    # candles[-2] = Bougie 2 (Réintégration, fermée)
    # candles[-3] = Bougie 1 (Cassure par le corps, fermée)
    c2 = candles[-2]
    c1 = candles[-3]
    history_before_break = candles[:-3]

    # Recherche du dernier vrai creux et sommet formés AVANT la cassure
    true_swing_high, true_swing_low = find_last_true_pivots(history_before_break, left_bars=2, right_bars=2)

    o1, c1_close, h1, l1 = float(c1["open"]), float(c1["close"]), float(c1["high"]), float(c1["low"])
    o2, c2_close, h2, l2 = float(c2["open"]), float(c2["close"]), float(c2["high"]), float(c2["low"])

    range_c2 = h2 - l2
    if range_c2 <= 0:
        return None
    body_c2 = abs(c2_close - o2)
    body_ratio_c2 = body_c2 / range_c2
    is_strong_body = body_ratio_c2 >= 0.50

    # 1. SETUP ACHAT sur VRAI CREUX
    if true_swing_low is not None:
        if (c1_close < o1) and (c1_close < true_swing_low) and (c2_close > o2) and (c2_close > true_swing_low) and is_strong_body:
            sl = min(l1, l2)
            body_pct = round(body_ratio_c2 * 100, 1)
            return (
                f"🟢 *SIGNAL ACHAT (Cassure & Réintégration M15)* 🟢\n\n"
                f"📊 *Marché* : {market_name}\n"
                f"🎯 *Entrée (Buy)* : `{c2_close}`\n"
                f"🛑 *Stop Loss (SL)* : `{sl}`\n"
                f"📌 *Vrai Creux Pivot* : `{true_swing_low}`\n"
                f"📉 *Bougie 1* : Rouge (clôture du corps sous le creux)\n"
                f"📈 *Bougie 2* : Verte (réintégration confirmée, corps: {body_pct}%)"
            )

    # 2. SETUP VENTE sur VRAI SOMMET
    if true_swing_high is not None:
        if (c1_close > o1) and (c1_close > true_swing_high) and (c2_close < o2) and (c2_close < true_swing_high) and is_strong_body:
            sl = max(h1, h2)
            body_pct = round(body_ratio_c2 * 100, 1)
            return (
                f"🚨 *SIGNAL VENTE (Cassure & Réintégration M15)* 🚨\n\n"
                f"📊 *Marché* : {market_name}\n"
                f"🎯 *Entrée (Sell)* : `{c2_close}`\n"
                f"🛑 *Stop Loss (SL)* : `{sl}`\n"
                f"📌 *Vrai Sommet Pivot* : `{true_swing_high}`\n"
                f"📈 *Bougie 1* : Verte (clôture du corps au-dessus du sommet)\n"
                f"📉 *Bougie 2* : Rouge (réintégration confirmée, corps: {body_pct}%)"
            )

    return None


async def run_scan(is_manual=False):
    global SCAN_IN_PROGRESS
    if SCAN_IN_PROGRESS:
        if is_manual:
            await send_telegram_alert("⚠️ *Une analyse est déjà en cours, merci de patienter.*")
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
    return web.Response(text="Bot is running active 24/7!")


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
        "🤖 *Scanner M15 mis à jour (Détection stricte par Vrais Pivots Swing/Fractales).*\n\n"
        "• Envoyez `/scan` pour déclencher une analyse manuelle."
    )

    await asyncio.gather(
        scheduled_scanner(),
        listen_telegram(),
    )


if __name__ == "__main__":
    asyncio.run(main())
