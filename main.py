import asyncio
import http.server
import json
import os
import socketserver
import threading
import time
import requests
import websockets

# --- CONFIGURATION IDENTIFIANTS ---
TELEGRAM_BOT_TOKEN = "8834699234:AAHnqWUWz8auv0LbJDuMePTaeky8kmqIu0"
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
LOOKBACK_CANDLES = 15

# Variable globale de boucle asyncio pour déclencher les scans manuels
MAIN_LOOP = None


# Serveur HTTP pour Render
class HealthCheckHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    with socketserver.TCPServer(("", port), HealthCheckHandler) as httpd:
        print(f"Serveur HTTP actif sur le port {port}")
        httpd.serve_forever()


def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            print(f"Erreur Envoi Telegram HTTP {res.status_code}: {res.text}")
    except Exception as e:
        print(f"Exception Envoi Telegram: {e}")


async def get_candles(symbol):
    async with websockets.connect(DERIV_WS_URL) as ws:
        req = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": LOOKBACK_CANDLES + 6,
            "end": "latest",
            "style": "candles",
            "granularity": 900,
        }
        await ws.send(json.dumps(req))
        res = json.loads(await ws.recv())
        return res.get("candles", [])


def check_liquidity_reentry(candles, market_name):
    if len(candles) < LOOKBACK_CANDLES + 3:
        return None

    c2 = candles[-2]
    c1 = candles[-3]
    prev_candles = candles[-(LOOKBACK_CANDLES + 3) : -3]

    o1, c1_close, h1, l1 = float(c1["open"]), float(c1["close"]), float(c1["high"]), float(c1["low"])
    o2, c2_close, h2, l2 = float(c2["open"]), float(c2["close"]), float(c2["high"]), float(c2["low"])

    swing_high = max(float(c["high"]) for c in prev_candles)
    swing_low = min(float(c["low"]) for c in prev_candles)

    range_c2 = h2 - l2
    if range_c2 <= 0:
        return None
    body_c2 = abs(c2_close - o2)
    body_ratio_c2 = body_c2 / range_c2
    is_strong_body = body_ratio_c2 >= 0.50

    # Setup ACHAT
    if (c1_close < o1) and (c1_close < swing_low) and (c2_close > o2) and (c2_close > swing_low) and is_strong_body:
        sl = min(l1, l2)
        body_pct = round(body_ratio_c2 * 100, 1)
        return (
            f"🟢 *SIGNAL ACHAT (Cassure & Réintégration M15)* 🟢\n\n"
            f"📊 *Marché* : {market_name}\n"
            f"🎯 *Entrée (Buy)* : `{c2_close}`\n"
            f"🛑 *Stop Loss (SL)* : `{sl}`\n"
            f"📌 *Creux de référence* : `{swing_low}`\n"
            f"📉 *Bougie 1* : Rouge (clôturée sous le creux)\n"
            f"📈 *Bougie 2* : Verte (réintégration, corps: {body_pct}%)"
        )

    # Setup VENTE
    if (c1_close > o1) and (c1_close > swing_high) and (c2_close < o2) and (c2_close < swing_high) and is_strong_body:
        sl = max(h1, h2)
        body_pct = round(body_ratio_c2 * 100, 1)
        return (
            f"🚨 *SIGNAL VENTE (Cassure & Réintégration M15)* 🚨\n\n"
            f"📊 *Marché* : {market_name}\n"
            f"🎯 *Entrée (Sell)* : `{c2_close}`\n"
            f"🛑 *Stop Loss (SL)* : `{sl}`\n"
            f"📌 *Sommet de référence* : `{swing_high}`\n"
            f"📈 *Bougie 1* : Verte (clôturée au-dessus du sommet)\n"
            f"📉 *Bougie 2* : Rouge (réintégration, corps: {body_pct}%)"
        )

    return None


async def run_scan(is_manual=False):
    found_signals = 0
    if is_manual:
        send_telegram_alert("⏳ *Analyse manuelle en cours sur vos 22 marchés...*")

    for mkt in MARKETS:
        try:
            candles = await get_candles(mkt["symbol"])
            alert = check_liquidity_reentry(candles, mkt["name"])
            if alert:
                send_telegram_alert(alert)
                found_signals += 1
        except Exception as e:
            print(f"Erreur sur {mkt['symbol']}: {e}")

    if is_manual and found_signals == 0:
        send_telegram_alert("ℹ️ *Scan terminé : Aucun signal détecté pour le moment.*")


def telegram_listener_thread():
    last_update_id = None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    print("Ecouteur Telegram demarre...")

    while True:
        try:
            params = {"timeout": 5, "offset": last_update_id}
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("result", []):
                    last_update_id = item["update_id"] + 1
                    msg = item.get("message", {})
                    text = msg.get("text", "").strip().lower()
                    sender_id = str(msg.get("chat", {}).get("id", ""))

                    if sender_id == str(TELEGRAM_CHAT_ID):
                        if text in ["/scan", "scan", "/start"]:
                            if MAIN_LOOP and MAIN_LOOP.is_running():
                                asyncio.run_coroutine_threadsafe(run_scan(is_manual=True), MAIN_LOOP)
            else:
                print(f"Erreur getUpdates HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"Exception Telegram Listener: {e}")
        time.sleep(2)


async def scheduled_scanner():
    last_scanned_min = -1
    while True:
        now = time.gmtime()
        m = now.tm_min
        if m in [0, 15, 30, 45] and m != last_scanned_min:
            await asyncio.sleep(5)
            await run_scan(is_manual=False)
            last_scanned_min = m
        await asyncio.sleep(10)


async def main_async():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()

    send_telegram_alert(
        "🤖 *Scanner M15 actif et en ligne.*\n\n"
        "• *Scans automatiques* : toutes les 15 minutes\n"
        "• *Scan manuel* : envoyez `/scan` pour tester."
    )

    await scheduled_scanner()


def main():
    threading.Thread(target=run_http_server, daemon=True).start()
    threading.Thread(target=telegram_listener_thread, daemon=True).start()
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
