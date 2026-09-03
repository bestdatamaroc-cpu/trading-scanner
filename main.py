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
CANDLE_COUNT = 90
ADX_THRESHOLD = 25.0
MIN_BARS_AGO = 4
MAX_BARS_AGO = 15

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
            "granularity": 900,  # M15
        }
        await ws.send(json.dumps(req))
        res = json.loads(await ws.recv())
        return res.get("candles", [])


def calculate_ema(closes, period):
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema_val = sum(closes[:period]) / period
    for c_close in closes[period:]:
        ema_val = (c_close * k) + (ema_val * (1 - k))
    return ema_val


def calculate_rsi(candles, period=7):
    closes = [float(c["close"]) for c in candles]
    if len(closes) < period + 1:
        return None
    
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
        
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
        
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def calculate_adx_dmi(candles, period=14):
    if len(candles) < period * 2:
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

    latest_pdi = dx_list[-1][1]
    latest_mdi = dx_list[-1][2]
    return round(adx_val, 2), round(latest_pdi, 2), round(latest_mdi, 2)


def extract_internal_pivots(candles_list):
    highs, lows = [], []
    n = len(candles_list)
    for i in range(2, n - 2):
        h = float(candles_list[i]["high"])
        l = float(candles_list[i]["low"])
        if (h > float(candles_list[i - 1]["high"]) and
            h > float(candles_list[i - 2]["high"]) and
            h > float(candles_list[i + 1]["high"]) and
            h > float(candles_list[i + 2]["high"])):
            highs.append((h, i))
        if (l < float(candles_list[i - 1]["low"]) and
            l < float(candles_list[i - 2]["low"]) and
            l < float(candles_list[i + 1]["low"]) and
            l < float(candles_list[i + 2]["low"])):
            lows.append((l, i))
    return highs, lows


def check_continuation_sweep(candles, market_name):
    if len(candles) < 55:
        return None

    # candles[-1] : En cours
    # candles[-2] : Bougie 2 = Réintégration validée
    # candles[-3] : Bougie 1 = Bougie ayant fait le sweep
    c2 = candles[-2]
    c1 = candles[-3]
    closed_candles = candles[:-1]
    closes = [float(c["close"]) for c in closed_candles]

    ema21 = calculate_ema(closes, 21)
    ema50 = calculate_ema(closes, 50)
    adx_val, p_di, m_di = calculate_adx_dmi(closed_candles, period=14)
    rsi_val = calculate_rsi(closed_candles, period=7)

    if None in (ema21, ema50, adx_val, p_di, m_di, rsi_val):
        return None

    if adx_val < ADX_THRESHOLD:
        return None

    o2, c2_close, h2, l2 = float(c2["open"]), float(c2["close"]), float(c2["high"]), float(c2["low"])
    o1, c1_close, h1, l1 = float(c1["open"]), float(c1["close"]), float(c1["high"]), float(c1["low"])

    range_c2 = h2 - l2
    if range_c2 <= 0:
        return None
    body_ratio_c2 = abs(c2_close - o2) / range_c2
    if body_ratio_c2 < 0.45:
        return None

    history_pivots = candles[:-3]
    total_len = len(history_pivots)
    pivots_high, pivots_low = extract_internal_pivots(history_pivots)

    # ----------------------------------------------------
    # 1. SETUP D'ACHAT EN CONTINUATION
    # ----------------------------------------------------
    is_uptrend = (ema21 > ema50) and (p_di > m_di) and (c2_close > ema21)
    if is_uptrend and c2_close > o2 and rsi_val <= 45:
        for creux_ref, idx in reversed(pivots_low):
            bars_ago = (total_len - idx) + 2
            if MIN_BARS_AGO <= bars_ago <= MAX_BARS_AGO:
                intermediaire_clean = all(float(history_pivots[k]["low"]) >= creux_ref for k in range(idx + 1, total_len))
                if not intermediaire_clean:
                    continue

                sweep_happened = (l1 < creux_ref or l2 < creux_ref)
                reentry_confirmed = (c2_close > creux_ref)

                if sweep_happened and reentry_confirmed:
                    sl = min(l1, l2)
                    risk = c2_close - sl
                    if risk <= 0:
                        continue
                    tp1 = round(c2_close + (2.0 * risk), 4)
                    tp2 = round(c2_close + (3.0 * risk), 4)
                    body_pct = round(body_ratio_c2 * 100, 1)

                    return (
                        f"🟢 *ACHAT CONTINUATION - INTERNAL SWEEP (M15)* 🟢\n\n"
                        f"📊 *Marché* : {market_name}\n"
                        f"🎯 *Entrée (Buy)* : `{c2_close}`\n"
                        f"🛑 *Stop Loss (SL sous mèche)* : `{sl}`\n"
                        f"🎯 *TP 1 (1:2)* : `{tp1}` | *TP 2 (1:3)* : `{tp2}`\n"
                        f"📌 *Creux interne balayé* : `{creux_ref}` ({bars_ago} bougies)\n"
                        f"📈 *EMA* : EMA21 (`{round(ema21, 2)}`) > EMA50 (`{round(ema50, 2)}`)\n"
                        f"🔥 *ADX(14)* : `{adx_val}` (DI+ `{p_di}` > DI- `{m_di}`)\n"
                        f"⚡ *RSI(7)* : `{rsi_val}` | Corps de rejet : {body_pct}%"
                    )

    # ----------------------------------------------------
    # 2. SETUP DE VENTE EN CONTINUATION
    # ----------------------------------------------------
    is_downtrend = (ema21 < ema50) and (m_di > p_di) and (c2_close < ema21)
    if is_downtrend and c2_close < o2 and rsi_val >= 55:
        for sommet_ref, idx in reversed(pivots_high):
            bars_ago = (total_len - idx) + 2
            if MIN_BARS_AGO <= bars_ago <= MAX_BARS_AGO:
                intermediaire_clean = all(float(history_pivots[k]["high"]) <= sommet_ref for k in range(idx + 1, total_len))
                if not intermediaire_clean:
                    continue

                sweep_happened = (h1 > sommet_ref or h2 > sommet_ref)
                reentry_confirmed = (c2_close < sommet_ref)

                if sweep_happened and reentry_confirmed:
                    sl = max(h1, h2)
                    risk = sl - c2_close
                    if risk <= 0:
                        continue
                    tp1 = round(c2_close - (2.0 * risk), 4)
                    tp2 = round(c2_close - (3.0 * risk), 4)
                    body_pct = round(body_ratio_c2 * 100, 1)

                    return (
                        f"🚨 *VENTE CONTINUATION - INTERNAL SWEEP (M15)* 🚨\n\n"
                        f"📊 *Marché* : {market_name}\n"
                        f"🎯 *Entrée (Sell)* : `{c2_close}`\n"
                        f"🛑 *Stop Loss (SL sur mèche)* : `{sl}`\n"
                        f"🎯 *TP 1 (1:2)* : `{tp1}` | *TP 2 (1:3)* : `{tp2}`\n"
                        f"📌 *Sommet interne balayé* : `{sommet_ref}` ({bars_ago} bougies)\n"
                        f"📉 *EMA* : EMA21 (`{round(ema21, 2)}`) < EMA50 (`{round(ema50, 2)}`)\n"
                        f"🔥 *ADX(14)* : `{adx_val}` (DI- `{m_di}` > DI+ `{p_di}`)\n"
                        f"⚡ *RSI(7)* : `{rsi_val}` | Corps de rejet : {body_pct}%"
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
            await send_telegram_alert("⏳ *Scan M15 : Recherche de sweeps de continuation en cours...*")

        for mkt in MARKETS:
            try:
                candles = await get_candles(mkt["symbol"])
                alert = check_continuation_sweep(candles, mkt["name"])
                if alert:
                    await send_telegram_alert(alert)
                    found_signals += 1
            except Exception as e:
                print(f"Erreur sur {mkt['symbol']}: {e}")

        if is_manual and found_signals == 0:
            await send_telegram_alert("ℹ️ *Scan terminé : Aucun setup de continuation validé.*")
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
        "🤖 *Scanner M15 actif : Internal Liquidity Sweep & Trend Flow.*\n\n"
        "• Filtres : EMA 21/50 + ADX(14) >= 25 + RSI(7).\n"
        "• SL sur mèche extrême + Alertes TP 1:2 & TP 1:3.\n"
        "• Envoyez `/scan` pour déclencher une analyse manuelle."
    )

    await asyncio.gather(
        scheduled_scanner(),
        listen_telegram(),
    )


if __name__ == "__main__":
    asyncio.run(main())
