import asyncio
import os
import re
import unicodedata
import urllib.parse
from aiohttp import web, ClientSession, ClientTimeout

# --- CONFIGURATION TELEGRAM ---
TELEGRAM_BOT_TOKEN = "8834699234:AAHnqWUWz8auv0LbJDuMePTaeky8kmqIu0o"
TELEGRAM_CHAT_ID = "759626963"

# --- MOTS-CLÉS CIBLÉS ---
KEYWORDS_RAW = [
    "informatique",
    "technicien",
    "technical helper",
    "data analyst",
    "dataanalyste",
    "chef de projet",
    "database",
    "base de donnees",
    "sql",
    "reseau",
    "reseaux",
    "network",
    "infrastructure",
    "systeme d'information",
    "it support",
    "administrateur",
]

def clean_text(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.lower()

KEYWORDS_CLEAN = [clean_text(k) for k in KEYWORDS_RAW]

SENT_JOBS = set()
SCAN_IN_PROGRESS = False
HTTP_SESSION = None

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "fr-CA,fr;q=0.9,en-CA;q=0.8,en;q=0.7",
}

REQUEST_TIMEOUT = ClientTimeout(total=10)


async def send_telegram_alert(message):
    global HTTP_SESSION
    if HTTP_SESSION is None or HTTP_SESSION.closed:
        HTTP_SESSION = ClientSession()

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    try:
        async with HTTP_SESSION.post(url, json=payload, timeout=ClientTimeout(total=5)) as resp:
            pass
    except Exception as e:
        print(f"Erreur envoi Telegram: {e}")


def find_matched_keywords(text):
    norm = clean_text(text)
    matched = []
    for raw_k, clean_k in zip(KEYWORDS_RAW, KEYWORDS_CLEAN):
        if clean_k in norm:
            matched.append(raw_k)
    return list(set(matched))


# ---------------------------------------------------------------------------
# EXTRACTION GUICHET-EMPLOIS (Parsing Regex natif sans dépendance externe)
# ---------------------------------------------------------------------------
async def fetch_jobbank_query(term):
    jobs = []
    url = f"https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring={urllib.parse.quote(term)}&sort=D"
    try:
        global HTTP_SESSION
        if HTTP_SESSION is None or HTTP_SESSION.closed:
            HTTP_SESSION = ClientSession()

        async with HTTP_SESSION.get(url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status == 200:
                html = await resp.text()

                # Extraction des blocs d'offres (<article ... </article>)
                articles = re.findall(r"<article[\s\S]*?</article>", html, re.IGNORECASE)

                for art in articles[:20]:
                    # Extraction du lien href
                    href_match = re.search(r'href="([^"]+)"', art)
                    if not href_match:
                        continue
                    link = href_match.group(1)
                    if not link.startswith("http"):
                        link = f"https://www.jobbank.gc.ca{link}"

                    # Nettoyage des balises HTML pour extraire le texte brut
                    clean_article = re.sub(r"<[^>]+>", " ", art)
                    clean_article = " ".join(clean_article.split())

                    # Extraction du titre
                    title_match = re.search(r'class="noctitle">([^<]+)<', art)
                    if title_match:
                        title = title_match.group(1).strip()
                    else:
                        title = clean_article[:70]

                    matched_kw = find_matched_keywords(clean_article)
                    if matched_kw and len(title) > 3:
                        jobs.append({
                            "source": "Guichet-Emplois (Job Bank)",
                            "title": title,
                            "url": link,
                            "keywords": matched_kw,
                        })
    except Exception as e:
        print(f"Erreur Job Bank ({term}): {e}")

    return jobs


# ---------------------------------------------------------------------------
# GESTION DES ANALYSES
# ---------------------------------------------------------------------------
async def run_jobs_scan(is_manual=False):
    global SCAN_IN_PROGRESS, SENT_JOBS
    if SCAN_IN_PROGRESS:
        if is_manual:
            await send_telegram_alert("⚠️ *Une analyse est déjà en cours...*")
        return

    SCAN_IN_PROGRESS = True
    try:
        if is_manual:
            await send_telegram_alert("⏳ *Recherche des offres en direct sur Guichet-Emplois...*")

        # Recherche sur les termes informatiques principaux
        terms = ["informatique", "technicien", "reseaux", "sql"]
        tasks = [fetch_jobbank_query(t) for t in terms]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_jobs = []
        for r in results:
            if isinstance(r, list):
                all_jobs.extend(r)

        # Déduplication par URL
        unique_jobs = {}
        for j in all_jobs:
            if j["url"] not in unique_jobs:
                unique_jobs[j["url"]] = j

        new_alerts = 0
        for url, job in unique_jobs.items():
            if url not in SENT_JOBS:
                SENT_JOBS.add(url)
                new_alerts += 1

                kw_str = ", ".join(job["keywords"][:4])
                msg = (
                    f"💼 *NOUVELLE OFFRE GUICHET-EMPLOIS* 💼\n\n"
                    f"📌 *Poste* : `{job['title']}`\n"
                    f"🔑 *Mots-clés* : `{kw_str}`\n\n"
                    f"🔗 [Voir l'offre et postuler]({job['url']})"
                )
                await send_telegram_alert(msg)
                await asyncio.sleep(0.5)

        if is_manual:
            if new_alerts == 0 and len(unique_jobs) > 0:
                await send_telegram_alert(f"ℹ️ *{len(unique_jobs)} offres trouvées, toutes déjà envoyées.*")
            elif new_alerts == 0 and len(unique_jobs) == 0:
                await send_telegram_alert("ℹ️ *Aucune offre correspondante actuellement.*")
            else:
                await send_telegram_alert(f"✅ *{new_alerts} nouvelle(s) offre(s) envoyée(s).*")

    except Exception as e:
        print(f"Erreur globale scan: {e}")
    finally:
        SCAN_IN_PROGRESS = False


# ---------------------------------------------------------------------------
# ÉCOUTE TELEGRAM
# ---------------------------------------------------------------------------
async def listen_telegram():
    global HTTP_SESSION
    last_update_id = None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    try:
        if HTTP_SESSION is None or HTTP_SESSION.closed:
            HTTP_SESSION = ClientSession()
        async with HTTP_SESSION.get(url, params={"offset": -1}, timeout=ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get("result", [])
                if results:
                    last_update_id = results[-1]["update_id"] + 1
    except Exception as e:
        print(f"Erreur purge: {e}")

    while True:
        try:
            if HTTP_SESSION is None or HTTP_SESSION.closed:
                HTTP_SESSION = ClientSession()

            params = {"timeout": 10, "offset": last_update_id}
            async with HTTP_SESSION.get(url, params=params, timeout=ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for item in data.get("result", []):
                        last_update_id = item["update_id"] + 1
                        msg = item.get("message", {})
                        text = msg.get("text", "").strip().lower()
                        sender_id = str(msg.get("chat", {}).get("id", ""))

                        if sender_id == str(TELEGRAM_CHAT_ID):
                            if text in ["/check", "/scan", "scan", "check", "/start"]:
                                asyncio.create_task(run_jobs_scan(is_manual=True))
        except Exception as e:
            print(f"Erreur polling Telegram: {e}")
        await asyncio.sleep(1)


# ---------------------------------------------------------------------------
# SCAN AUTOMATIQUE (Toutes les 60 minutes)
# ---------------------------------------------------------------------------
async def scheduled_scanner():
    await asyncio.sleep(5)
    await run_jobs_scan(is_manual=False)
    while True:
        await asyncio.sleep(3600)
        await run_jobs_scan(is_manual=False)


# ---------------------------------------------------------------------------
# SERVEUR WEB HEALTHCHECK RENDER
# ---------------------------------------------------------------------------
async def handle_ping(request):
    return web.Response(text="Bot Emploi actif 24/7 sur Render")


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
        "🤖 *Job Alert Bot Guichet-Emplois en ligne !*\n\n"
        "• Surveillance active des postes IT / Réseaux / SQL.\n"
        "• Scan automatique toutes les 60 minutes.\n"
        "• Tapez `/check` pour forcer une vérification immédiate."
    )

    await asyncio.gather(
        scheduled_scanner(),
        listen_telegram(),
    )


if __name__ == "__main__":
    asyncio.run(main())
