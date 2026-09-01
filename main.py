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
CANDLE_COUNT = 80
ADX_THRESHOLD = 20.0

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


def calculate_adx(candles, period=14):
    if len(candles) < period * 2:
        return None

    tr_list, plus_dm_list, minus_dm_list = [], [], []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        prev_h = float(candles[i - 1]["high"])
        prev_l = float(candles[i - 1]["low"])
        prev_c = float(candles[i - 1]["close"])

        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)

        up_move = h - prev_h
        down_move = prev_l - l
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    smooth_tr = sum(tr_list[:period])
    smooth_plus_dm = sum(plus_dm_list[:period])
    smooth_minus_dm = sum(minus_dm_list[:period])
    dx_list = []

    for i in range(period, len(tr_list)):
        smooth_tr = smooth_tr - (smooth_tr / period) + tr_list[i]
        smooth_plus_dm = smooth_plus_dm - (smooth_plus_dm / period) + plus_dm_list[i]
        smooth_minus_dm = smooth_minus_dm - (smooth_minus_dm / period) + minus_dm_list[i]

        if smooth_tr == 0:
            continue
        p_di = 100 * (smooth_plus_dm / smooth_tr)
        m_di = 100 * (smooth_minus_dm / smooth_tr)
        di_sum = p_di + m_di
        dx = (100 * abs(p_di - m_di) / di_sum) if di_sum != 0 else 0
        dx_list.append(dx)

    if len(dx_list) < period:
        return None

    adx_val = sum(dx_list[:period]) / period
    for item in dx_list[period:]:
        adx_val = ((adx_val * (period - 1)) + item) / period

    return round(adx_val, 2)


def check_complete_sweep_mss(candles, market_name):
    if len(candles) < 40:
        return None

    c_trigger = candles[-2]
    c_prev = candles[-3]
    closed_candles = candles[:-1]
    n = len(closed_candles)

    adx_val = calculate_adx(closed_candles, period=14)
    if adx_val is not None and adx_val < ADX_THRESHOLD:
        return None

    # ----------------------------------------------------
    # 1. SCÉNARIO VENTE (Balayage Sommet -> Max 4 barres en dehors -> Cassure Creux)
    # ----------------------------------------------------
    for ref_idx in range(n - 6, max(n - 35, 5), -1):
        ref_high = float(closed_candles[ref_idx]["high"])

        is_major_pivot_h = (
            ref_high > float(closed_candles[ref_idx - 1]["high"]) and
            ref_high > float(closed_candles[ref_idx - 2]["high"]) and
            ref_high > float(closed_candles[ref_idx + 1]["high"]) and
            ref_high > float(closed_candles[ref_idx + 2]["high"])
        )
        if not is_major_pivot_h:
            continue

        # Trouver la première bougie qui perce au-dessus du sommet
        first_break_idx = -1
        for k in range(ref_idx + 3, n - 1):
            if float(closed_candles[k]["high"]) > ref_high:
                first_break_idx = k
                break

        if first_break_idx == -1:
            continue

        # Vérifier que la réintégration a lieu en MAX 4 bougies après la première percée
        reentry_idx = -1
        for r in range(first_break_idx, min(first_break_idx + 4, n - 1)):
            if float(closed_candles[r]["close"]) < ref_high:
                reentry_idx = r
                break

        # Si aucune réintégration sous 4 barres, le sweep est rejeté
        if reentry_idx == -1:
            continue

        # Identifier le creux d'impulsion entre le pivot et la percée
        base_low = float("inf")
        for k in range(ref_idx, first_break_idx + 1):
            low_k = float(closed_candles[k]["low"])
            if low_k < base_low:
                base_low = low_k

        if base_low == float("inf"):
            continue

        trigger_close = float(c_trigger["close"])
        prev_close = float(c_prev["close"])

        # Déclenchement sur cassure du creux d'origine
        if prev_close >= base_low and trigger_close < base_low:
            highest_wick = max(float(closed_candles[i]["high"]) for i in range(first_break_idx, reentry_idx + 1))
            sl = highest_wick
            risk = sl - trigger_close
            if risk <= 0:
                continue
            tp = round(trigger_close - (3.0 * risk), 4)

            return (
                f"🚨 *SIGNAL VENTE - BALAYAGE & MSS (M15)* 🚨\n\n"
                f"📊 *Marché* : {market_name}\n"
                f"🎯 *Entrée (Sell)* : `{trigger_close}`\n"
                f"🛑 *Stop Loss (SL mèche)* : `{sl}`\n"
                f"🎯 *Take Profit (TP 1:3)* : `{tp}`\n"
                f"📌 *1. Sommet balayé* : `{ref_high}`\n"
                f"⚡ *2. Réintégration stricte* : Fait en {reentry_idx - first_break_idx + 1} bougie(s) ($\le 4$ max)\n"
                f"📉 *3. Creux d'impulsion cassé* : `{base_low}`\n"
                f"📈 *ADX(14)* : `{adx_val}`"
            )

    # ----------------------------------------------------
    # 2. SCÉNARIO ACHAT (Balayage Creux -> Max 4 barres en dehors -> Cassure Sommet)
    # ----------------------------------------------------
    for ref_idx in range(n - 6, max(n - 35, 5), -1):
        ref_low = float(closed_candles[ref_idx]["low"])

        is_major_pivot_l = (
            ref_low < float(closed_candles[ref_idx - 1]["low"]) and
            ref_low < float(closed_candles[ref_idx - 2]["low"]) and
            ref_low < float(closed_candles[ref_idx + 1]["low"]) and
            ref_low < float(closed_candles[ref_idx + 2]["low"])
        )
        if not is_major_pivot_l:
            continue

        # Trouver la première bougie qui perce sous le creux
        first_break_idx = -1
        for k in range(ref_idx + 3, n - 1):
            if float(closed_candles[k]["low"]) < ref_low:
                first_break_idx = k
                break

        if first_break_idx == -1:
            continue

        # Vérifier que la réintégration a lieu en MAX 4 bougies après la première percée
        reentry_idx = -1
        for r in range(first_break_idx, min(first_break_idx + 4, n - 1)):
            if float(closed_candles[r]["close"]) > ref_low:
                reentry_idx = r
                break

        # Si le prix reste sous le niveau plus de 4 bougies, on rejette
        if reentry_idx == -1:
            continue

        # Identifier le sommet d'impulsion entre le pivot et la percée
        base_high = float("-inf")
        for k in range(ref_idx, first_break_idx + 1):
            high_k = float(closed_candles[k]["high"])
            if high_k > base_high:
                base_high = high_k

        if base_high == float("-inf"):
            continue

        trigger_close = float(c_trigger["close"])
        prev_close = float(c_prev["close"])

        # Déclenchement sur cassure du sommet d'origine
        if prev_close <= base_high and trigger_close > base_high:
            lowest_wick = min(float(closed_candles[i]["low"]) for i in range(first_break_idx, reentry_idx + 1))
            sl = lowest_wick
            risk = trigger_close - sl
            if risk <= 0:
                continue
            tp = round(trigger_close + (3.0 * risk), 4)

            return (
                f"🟢 *SIGNAL ACHAT - BALAYAGE & MSS (M15)* 🟢\n\n"
                f"📊 *Marché* : {market_name}\n"
                f"🎯 *Entrée (Buy)* : `{trigger_close}`\n"
                f"🛑 *Stop Loss (SL mèche)* : `{sl}`\n"
                f"🎯 *Take Profit (TP 1:3)* : `{tp}`\n"
                f"📌 *1. Creux balayé* : `{ref_low}`\n"
                f"⚡ *2. Réintégration stricte* : Fait en {reentry_idx - first_break_idx + 1} bougie(s) ($\le 4$ max)\n"
                f"📈 *3. Sommet d'impulsion cassé* : `{base_high}`\n"
                f"📈 *ADX(14)* : `{adx_val}`"
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
            await send_telegram_alert("⏳ *Scan M15 en cours (Filtre Strict : Réintégration $\le$ 4 barres totales)...*")

        for mkt in MARKETS:
            try:
                candles = await get_candles(mkt["symbol"])
                alert = check_complete_sweep_mss(candles, mkt["name"])
                if alert:
                    await send_telegram_alert(alert)
                    found_signals += 1
            except Exception as e:
                print(f"Erreur sur {mkt['symbol']}: {e}")

        if is_manual and found_signals == 0:
            await send_telegram_alert("ℹ️ *Scan terminé : Aucun setup valide.*")
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
        "🤖 *Scanner M15 actif (Filtre Verrouillé : Temps hors-niveau $\le$ 4 bougies max).* \n\n"
        "• Rejet automatique si le cours reste plus de 4 bougies en dessous/au-dessus du pivot.\n"
        "• Envoyez `/scan` pour tester manuellement."
    )

    await asyncio.gather(
        scheduled_scanner(),
        listen_telegram(),
    )


if __name__ == "__main__":
    asyncio.run(main())
