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
CANDLE_COUNT = 60
ADX_THRESHOLD = 25.0
MIN_BARS_AGO = 4
MAX_BARS_AGO = 25

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


def calculate_ema_trend(candles, period=21):
    closes = [float(c["close"]) for c in candles]
    if len(closes) < period + 4:
        return None, False, False
    k = 2 / (period + 1)
    
    ema_series = []
    ema_val = sum(closes[:period]) / period
    ema_series.append(ema_val)
    
    for c_close in closes[period:]:
        ema_val = (c_close * k) + (ema_val * (1 - k))
        ema_series.append(ema_val)
        
    curr = ema_series[-1]
    ago_2 = ema_series[-3]
    
    is_pointing_up = curr > ago_2
    is_pointing_down = curr < ago_2
    
    return curr, is_pointing_up, is_pointing_down


def calculate_adx_dmi(candles, period=7):
    if len(candles) < period * 3:
        return None, None, None

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
        dx_list.append((dx, p_di, m_di))

    if len(dx_list) < period:
        return None, None, None

    adx_val = sum(x[0] for x in dx_list[:period]) / period
    for item in dx_list[period:]:
        adx_val = ((adx_val * (period - 1)) + item[0]) / period

    latest_plus_di = dx_list[-1][1]
    latest_minus_di = dx_list[-1][2]

    return round(adx_val, 2), round(latest_plus_di, 2), round(latest_minus_di, 2)


def extract_pivots_5bars(history_candles):
    n = len(history_candles)
    highs = []
    lows = []

    for i in range(2, n - 2):
        curr_h = float(history_candles[i]["high"])
        curr_l = float(history_candles[i]["low"])

        if (curr_h > float(history_candles[i - 1]["high"]) and
            curr_h > float(history_candles[i - 2]["high"]) and
            curr_h > float(history_candles[i + 1]["high"]) and
            curr_h > float(history_candles[i + 2]["high"])):
            highs.append((curr_h, i))

        if (curr_l < float(history_candles[i - 1]["low"]) and
            curr_l < float(history_candles[i - 2]["low"]) and
            curr_l < float(history_candles[i + 1]["low"]) and
            curr_l < float(history_candles[i + 2]["low"])):
            lows.append((curr_l, i))

    return highs, lows


def check_liquidity_reentry(candles, market_name):
    if len(candles) < 30:
        return None

    # candles[-1] = Bougie en cours (non finie)
    # candles[-2] = Bougie 3 : Réintégration (signal)
    # candles[-3] = Bougie 2 : 2ème bougie de cassure
    # candles[-4] = Bougie 1 : 1ère bougie de cassure
    c3 = candles[-2]
    c2 = candles[-3]
    c1 = candles[-4]
    history = candles[:-4]

    pivots_high, pivots_low = extract_pivots_5bars(history)
    ema_val, ema_up, ema_down = calculate_ema_trend(candles[:-1], period=21)
    adx_black, plus_di, minus_di = calculate_adx_dmi(candles[:-1], period=7)

    if adx_black is None or adx_black < ADX_THRESHOLD:
        return None

    o1, c1_close, h1, l1 = float(c1["open"]), float(c1["close"]), float(c1["high"]), float(c1["low"])
    o2, c2_close, h2, l2 = float(c2["open"]), float(c2["close"]), float(c2["high"]), float(c2["low"])
    o3, c3_close, h3, l3 = float(c3["open"]), float(c3["close"]), float(c3["high"]), float(c3["low"])

    range_c3 = h3 - l3
    if range_c3 <= 0:
        return None
    body_c3 = abs(c3_close - o3)
    body_ratio_c3 = body_c3 / range_c3
    is_strong_body = body_ratio_c3 >= 0.50

    if not is_strong_body:
        return None

    len_history = len(history)

    # 1. SETUP VENTE (Tendance baissière, 2 bougies de cassure au-dessus du sommet, puis réintégration sous le niveau)
    is_bearish_trend = (minus_di > plus_di) and (ema_val is not None) and (c3_close < ema_val) and ema_down

    if c3_close < o3 and is_bearish_trend:
        for sommet_ref, idx in reversed(pivots_high):
            bars_ago = (len_history - idx) + 3
            if MIN_BARS_AGO <= bars_ago <= MAX_BARS_AGO:
                # 2 bougies successives qui percent au-dessus du sommet
                two_bars_break = (c1_close > sommet_ref or h1 > sommet_ref) and (c2_close > sommet_ref or h2 > sommet_ref)
                if two_bars_break and (c3_close < sommet_ref):
                    sl = max(h1, h2, h3)
                    body_pct = round(body_ratio_c3 * 100, 1)
                    return (
                        f"🚨 *SIGNAL VENTE - DOUBLE CASSURE (M15)* 🚨\n\n"
                        f"📊 *Marché* : {market_name}\n"
                        f"🎯 *Entrée (Sell)* : `{c3_close}`\n"
                        f"🛑 *Stop Loss (SL)* : `{sl}`\n"
                        f"📌 *Sommet balayé* : `{sommet_ref}` (formé il y a {bars_ago} bougies)\n"
                        f"📉 *EMA 21* : Inclinée vers le bas ↘️ & Cours sous EMA\n"
                        f"📈 *ADX(7) Noir* : `{adx_black}` (DI- `{minus_di}` > DI+ `{plus_di}`)\n"
                        f"🕯️ *Confirmation* : 2 bougies de sweep + Réintégration rouge ({body_pct}%)"
                    )

    # 2. SETUP ACHAT (Tendance haussière, 2 bougies de cassure sous le creux, puis réintégration au-dessus)
    is_bullish_trend = (plus_di > minus_di) and (ema_val is not None) and (c3_close > ema_val) and ema_up

    if c3_close > o3 and is_bullish_trend:
        for creux_ref, idx in reversed(pivots_low):
            bars_ago = (len_history - idx) + 3
            if MIN_BARS_AGO <= bars_ago <= MAX_BARS_AGO:
                # 2 bougies successives qui percent en dessous du creux
                two_bars_break = (c1_close < creux_ref or l1 < creux_ref) and (c2_close < creux_ref or l2 < creux_ref)
                if two_bars_break and (c3_close > creux_ref):
                    sl = min(l1, l2, l3)
                    body_pct = round(body_ratio_c3 * 100, 1)
                    return (
                        f"🟢 *SIGNAL ACHAT - DOUBLE CASSURE (M15)* 🟢\n\n"
                        f"📊 *Marché* : {market_name}\n"
                        f"🎯 *Entrée (Buy)* : `{c3_close}`\n"
                        f"🛑 *Stop Loss (SL)* : `{sl}`\n"
                        f"📌 *Creux balayé* : `{creux_ref}` (formé il y a {bars_ago} bougies)\n"
                        f"📈 *EMA 21* : Inclinée vers le haut ↗️ & Cours sur EMA\n"
                        f"📈 *ADX(7) Noir* : `{adx_black}` (DI+ `{plus_di}` > DI- `{minus_di}`)\n"
                        f"🕯️ *Confirmation* : 2 bougies de sweep + Réintégration verte ({body_pct}%)"
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
            await send_telegram_alert("ℹ️ *Scan terminé : Aucun signal (Double cassure + EMA 21 inclinée).*")
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
        "🤖 *Scanner M15 actif (Double Cassure + EMA 21 Inclinée + ADX >= 25).*\n\n"
        "• Configuration calibrée sur sweep à 2 bougies.\n"
        "• Envoyez `/scan` pour déclencher une analyse manuelle."
    )

    await asyncio.gather(
        scheduled_scanner(),
        listen_telegram(),
    )


if __name__ == "__main__":
    asyncio.run(main())
