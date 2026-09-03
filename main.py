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
CANDLE_COUNT = 70
ADX_THRESHOLD = 22.0

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


def calculate_ema(closes, period):
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    val = sum(closes[:period]) / period
    for c in closes[period:]:
        val = (c * k) + (val * (1 - k))
    return val


def calculate_adx(candles, period=14):
    if len(candles) < period * 2:
        return None

    tr_list, plus_dm, minus_dm = [], [], []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        prev_h = float(candles[i - 1]["high"])
        prev_l = float(candles[i - 1]["low"])
        prev_c = float(candles[i - 1]["close"])

        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)

        up = h - prev_h
        down = prev_l - l
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)

    smooth_tr = sum(tr_list[:period])
    smooth_plus = sum(plus_dm[:period])
    smooth_minus = sum(minus_dm[:period])
    dx_list = []

    for i in range(period, len(tr_list)):
        smooth_tr = smooth_tr - (smooth_tr / period) + tr_list[i]
        smooth_plus = smooth_plus - (smooth_plus / period) + plus_dm[i]
        smooth_minus = smooth_minus - (smooth_minus / period) + minus_dm[i]

        if smooth_tr == 0:
            continue
        p_di = 100 * (smooth_plus / smooth_tr)
        m_di = 100 * (smooth_minus / smooth_tr)
        di_sum = p_di + m_di
        dx = (100 * abs(p_di - m_di) / di_sum) if di_sum != 0 else 0
        dx_list.append(dx)

    if len(dx_list) < period:
        return None

    adx_val = sum(dx_list[:period]) / period
    for item in dx_list[period:]:
        adx_val = ((adx_val * (period - 1)) + item) / period

    return round(adx_val, 2)


def check_trend_rejection(candles, market_name):
    if len(candles) < 60:
        return None

    # candles[-1] : en cours (ouverte)
    # candles[-2] : dernière bougie clôturée (signal potentiel)
    c_sig = candles[-2]
    closed_candles = candles[:-1]
    closes = [float(c["close"]) for c in closed_candles]

    ema21 = calculate_ema(closes, 21)
    ema50 = calculate_ema(closes, 50)
    ema21_prev = calculate_ema(closes[:-1], 21)

    if None in (ema21, ema50, ema21_prev):
        return None

    adx_val = calculate_adx(closed_candles, period=14)
    if adx_val is None or adx_val < ADX_THRESHOLD:
        return None

    o_sig = float(c_sig["open"])
    c_sig_close = float(c_sig["close"])
    h_sig = float(c_sig["high"])
    l_sig = float(c_sig["low"])

    bar_range = h_sig - l_sig
    if bar_range <= 0:
        return None

    body_size = abs(c_sig_close - o_sig)
    body_ratio = body_size / bar_range

    # ---------------------------------------------------------
    # 1. SETUP ACHAT (Tendance haussière + test EMA + rejet vert)
    # ---------------------------------------------------------
    is_bullish_trend = (ema21 > ema50) and (ema21 > ema21_prev)
    is_green_candle = c_sig_close > o_sig
    # La mèche ou le corps teste la zone EMA 21/50
    zone_tested_buy = (l_sig <= ema21) and (c_sig_close > ema21)

    if is_bullish_trend and is_green_candle and zone_tested_buy and body_ratio >= 0.45:
        sl = round(l_sig, 4)
        risk = c_sig_close - sl
        if risk > 0:
            tp = round(c_sig_close + (3.0 * risk), 4)
            return (
                f"🟢 *SIGNAL ACHAT - REJET DYNAMIQUE M15* 🟢\n\n"
                f"📊 *Marché* : {market_name}\n"
                f"🎯 *Entrée (Buy)* : `{c_sig_close}`\n"
                f"🛑 *Stop Loss (SL sous mèche)* : `{sl}`\n"
                f"🎯 *Take Profit (TP 1:3)* : `{tp}`\n"
                f"📈 *EMA 21/50* : Support dynamique rejeté ↗️\n"
                f"🔥 *ADX(14)* : `{adx_val}` (Tendance active)\n"
                f"🕯️ *Clôture* : Bougie verte directive ({round(body_ratio * 100, 1)}% de corps)"
            )

    # ---------------------------------------------------------
    # 2. SETUP VENTE (Tendance baissière + test EMA + rejet rouge)
    # ---------------------------------------------------------
    is_bearish_trend = (ema21 < ema50) and (ema21 < ema21_prev)
    is_red_candle = c_sig_close < o_sig
    # La mèche ou le corps teste la zone EMA 21/50
    zone_tested_sell = (h_sig >= ema21) and (c_sig_close < ema21)

    if is_bearish_trend and is_red_candle and zone_tested_sell and body_ratio >= 0.45:
        sl = round(h_sig, 4)
        risk = sl - c_sig_close
        if risk > 0:
            tp = round(c_sig_close - (3.0 * risk), 4)
            return (
                f"🚨 *SIGNAL VENTE - REJET DYNAMIQUE M15* 🚨\n\n"
                f"📊 *Marché* : {market_name}\n"
                f"🎯 *Entrée (Sell)* : `{c_sig_close}`\n"
                f"🛑 *Stop Loss (SL sur mèche)* : `{sl}`\n"
                f"🎯 *Take Profit (TP 1:3)* : `{tp}`\n"
                f"📉 *EMA 21/50* : Résistance dynamique rejetée ↘️\n"
                f"🔥 *ADX(14)* : `{adx_val}` (Tendance active)\n"
                f"🕯️ *Clôture* : Bougie rouge directive ({round(body_ratio * 100, 1)}% de corps)"
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
            await send_telegram_alert("⏳ *Scan M15 en cours sur 22 marchés (Stratégie Rejet EMA + TP 1:3)...*")

        for mkt in MARKETS:
            try:
                candles = await get_candles(mkt["symbol"])
                alert = check_trend_rejection(candles, mkt["name"])
                if alert:
                    await send_telegram_alert(alert)
                    found_signals += 1
            except Exception as e:
                print(f"Erreur sur {mkt['symbol']}: {e}")

        if is_manual and found_signals == 0:
            await send_telegram_alert("ℹ️ *Scan terminé : Aucun rejet dynamique EMA 21 validé.*")
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
        "🤖 *Scanner M15 actif (Rejet Dynamique EMA 21/50 + ADX $\ge$ 22 + Ratio TP 1:3).* \n\n"
        "• Fréquence de détection optimisée pour le trading actif.\n"
        "• Envoyez `/scan` pour déclencher une analyse manuelle."
    )

    await asyncio.gather(
        scheduled_scanner(),
        listen_telegram(),
    )


if __name__ == "__main__":
    asyncio.run(main())
