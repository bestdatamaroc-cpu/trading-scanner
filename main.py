import asyncio
import os
import re
import urllib.parse
from aiohttp import web, ClientSession
import feedparser
from bs4 import BeautifulSoup

# --- CONFIGURATION IDENTIFIANTS TELEGRAM ---
TELEGRAM_BOT_TOKEN = "8834699234:AAHnqWUWz8auv0LbJDuMePTaeky8kmqIu0o"
TELEGRAM_CHAT_ID = "759626963"

# --- MOTS-CLÉS ET FILTRES ---
KEYWORDS = [
    "informatique",
    "technicien informatique",
    "technical helper",
    "dataanalyste",
    "data analyst",
    "data analyste",
    "chef de projet",
    "database",
    "base de données",
    "sql",
    "réseaux informatiques",
    "reseaux informatiques",
    "network",
    "infrastructures informatiques",
    "infrastructure",
    "système d'information",
    "systeme d'information",
    "systemes d'information",
    "it support",
]

# Normalisation regex pour une détection insensible à la casse
KEYWORD_PATTERNS = [re.compile(rf"\b{re.escape(k)}\b", re.IGNORECASE) for k in KEYWORDS]

# Mémoire anti-doublon des offres déjà envoyées
SENT_JOBS = set()
SCAN_IN_PROGRESS = False
HTTP_SESSION = None

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-CA,fr;q=0.9,en-CA;q=0.8,en;q=0.7",
}


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
        async with HTTP_SESSION.post(url, json=payload, timeout=10) as resp:
            pass
    except Exception as e:
        print(f"Erreur envoi Telegram: {e}")


def matches_keywords(text):
    if not text:
        return []
    matched = []
    for kw, pattern in zip(KEYWORDS, KEYWORD_PATTERNS):
        if pattern.search(text):
            matched.append(kw)
    return list(set(matched))


# ---------------------------------------------------------------------------
# 1. SCRAPER GUICHET-EMPLOIS / JOB BANK (Via Flux RSS officiel structuré)
# ---------------------------------------------------------------------------
async def fetch_jobbank():
    jobs = []
    # Flux RSS officiel Guichet-Emplois ciblant les postes IT récents
    feed_url = "https://www.jobbank.gc.ca/jobsearch/feed/rss?sort=D&fcat=21"
    try:
        global HTTP_SESSION
        if HTTP_SESSION is None or HTTP_SESSION.closed:
            HTTP_SESSION = ClientSession()

        async with HTTP_SESSION.get(feed_url, headers=HEADERS, timeout=15) as resp:
            if resp.status == 200:
                raw_xml = await resp.text()
                feed = feedparser.parse(raw_xml)
                for entry in feed.entries[:25]:
                    title = entry.get("title", "")
                    link = entry.get("link", "")
                    summary = entry.get("summary", "")
                    full_content = f"{title} {summary}"

                    matched_kw = matches_keywords(full_content)
                    if matched_kw:
                        jobs.append({
                            "source": "Guichet-Emplois (Job Bank)",
                            "title": title,
                            "url": link,
                            "keywords": matched_kw,
                        })
    except Exception as e:
        print(f"Erreur Job Bank: {e}")
    return jobs


# ---------------------------------------------------------------------------
# 2. SCRAPER JOBILLICO
# ---------------------------------------------------------------------------
async def fetch_jobillico():
    jobs = []
    search_queries = ["informatique", "sql", "reseau"]
    for q in search_queries:
        url = f"https://www.jobillico.com/fr/recherche-emploi?keyword={urllib.parse.quote(q)}"
        try:
            global HTTP_SESSION
            if HTTP_SESSION is None or HTTP_SESSION.closed:
                HTTP_SESSION = ClientSession()

            async with HTTP_SESSION.get(url, headers=HEADERS, timeout=15) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")
                    articles = soup.select("article, .job-listing, .job-card")

                    for item in articles[:10]:
                        title_tag = item.select_one("h2, h3, a.title, .job-title")
                        link_tag = item.select_one("a[href]")

                        if title_tag and link_tag:
                            title = title_tag.get_text(strip=True)
                            link = link_tag.get("href", "")
                            if link.startswith("/"):
                                link = f"https://www.jobillico.com{link}"

                            matched_kw = matches_keywords(f"{title} {item.get_text()}")
                            if matched_kw:
                                jobs.append({
                                    "source": "Jobillico",
                                    "title": title,
                                    "url": link,
                                    "keywords": matched_kw,
                                })
        except Exception as e:
            print(f"Erreur Jobillico ({q}): {e}")
    return jobs


# ---------------------------------------------------------------------------
# 3. SCRAPER INDEED CANADA (Flux RSS d'alerte Indeed)
# ---------------------------------------------------------------------------
async def fetch_indeed():
    jobs = []
    search_terms = ["informatique", "technicien+informatique", "sql", "reseaux"]
    for term in search_terms:
        # Flux RSS des offres récentes Indeed Canada
        rss_url = f"https://ca.indeed.com/rss?q={term}&sort=date"
        try:
            global HTTP_SESSION
            if HTTP_SESSION is None or HTTP_SESSION.closed:
                HTTP_SESSION = ClientSession()

            async with HTTP_SESSION.get(rss_url, headers=HEADERS, timeout=15) as resp:
                if resp.status == 200:
                    raw_xml = await resp.text()
                    feed = feedparser.parse(raw_xml)
                    for entry in feed.entries[:10]:
                        title = entry.get("title", "")
                        link = entry.get("link", "")
                        summary = entry.get("summary", "")

                        matched_kw = matches_keywords(f"{title} {summary}")
                        if matched_kw:
                            jobs.append({
                                "source": "Indeed",
                                "title": title,
                                "url": link,
                                "keywords": matched_kw,
                            })
        except Exception as e:
            print(f"Erreur Indeed ({term}): {e}")
    return jobs


# ---------------------------------------------------------------------------
# GESTION DES ANALYSES ET DES ALERTES
# ---------------------------------------------------------------------------
async def run_jobs_scan(is_manual=False):
    global SCAN_IN_PROGRESS, SENT_JOBS
    if SCAN_IN_PROGRESS:
        if is_manual:
            await send_telegram_alert("⚠️ *Une analyse des offres est déjà en cours...*")
        return

    SCAN_IN_PROGRESS = True
    try:
        if is_manual:
            await send_telegram_alert("⏳ *Recherche des nouveaux postes en cours sur Guichet-Emplois, Jobillico et Indeed...*")

        all_jobs = []
        res_jobbank, res_jobillico, res_indeed = await asyncio.gather(
            fetch_jobbank(),
            fetch_jobillico(),
            fetch_indeed(),
            return_exceptions=True,
        )

        if isinstance(res_jobbank, list):
            all_jobs.extend(res_jobbank)
        if isinstance(res_jobillico, list):
            all_jobs.extend(res_jobillico)
        if isinstance(res_indeed, list):
            all_jobs.extend(res_indeed)

        new_alerts = 0
        for job in all_jobs:
            job_id = job["url"].strip()
            if job_id and job_id not in SENT_JOBS:
                SENT_JOBS.add(job_id)
                new_alerts += 1

                kw_str = ", ".join(job["keywords"][:4])
                msg = (
                    f"💼 *NOUVELLE OFFRE D'EMPLOI DÉTECTÉE* 💼\n\n"
                    f"🏢 *Plateforme* : {job['source']}\n"
                    f"📌 *Intitulé* : `{job['title']}`\n"
                    f"🔑 *Mots-clés trouvés* : `{kw_str}`\n\n"
                    f"🔗 [Voir l'offre et postuler]({job['url']})"
                )
                await send_telegram_alert(msg)
                await asyncio.sleep(1)

        if is_manual and new_alerts == 0:
            await send_telegram_alert("ℹ️ *Scan terminé : Aucun nouveau poste trouvé pour l'instant.*")

    finally:
        SCAN_IN_PROGRESS = False


# ---------------------------------------------------------------------------
# ÉCOUTE TELEGRAM (Commandes manuelles /check ou /scan)
# ---------------------------------------------------------------------------
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
        print(f"Erreur init Telegram: {e}")

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
                            if text in ["/check", "/scan", "scan", "check", "/start"]:
                                asyncio.create_task(run_jobs_scan(is_manual=True))
        except Exception as e:
            print(f"Polling Telegram exception: {e}")
        await asyncio.sleep(1)


# ---------------------------------------------------------------------------
# PLANIFICATEUR HORAIRE (Toutes les 60 minutes)
# ---------------------------------------------------------------------------
async def scheduled_scanner():
    # Premier scan au démarrage
    await asyncio.sleep(10)
    await run_jobs_scan(is_manual=False)

    while True:
        # Scan récurrent toutes les 60 minutes (3600 secondes)
        await asyncio.sleep(3600)
        await run_jobs_scan(is_manual=False)


# ---------------------------------------------------------------------------
# SERVEUR WEB HEALTHCHECK POUR RENDER
# ---------------------------------------------------------------------------
async def handle_ping(request):
    return web.Response(text="Job Scanner Bot actif 24/7 sur Render")


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
        "🤖 *Job Alert Bot Déployé & Actif!*\n\n"
        "• Plateformes surveillées : Guichet-Emplois (Job Bank), Indeed, Jobillico.\n"
        "• Filtre : Informatique, Réseaux, SQL, Base de données, Chef de projet, etc.\n"
        "• Fréquence : Scan automatique toutes les heures.\n"
        "• Tapez `/check` pour lancer une recherche manuelle immédiate."
    )

    await asyncio.gather(
        scheduled_scanner(),
        listen_telegram(),
    )


if __name__ == "__main__":
    asyncio.run(main())
