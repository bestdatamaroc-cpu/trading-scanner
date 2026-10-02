import asyncio
import os
import re
import urllib.parse
from aiohttp import web, ClientSession, ClientTimeout
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

KEYWORD_PATTERNS = [re.compile(rf"\b{re.escape(k)}\b", re.IGNORECASE) for k in KEYWORDS]

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

# Timeout court de 6 secondes pour éviter tout gel du script
REQUEST_TIMEOUT = ClientTimeout(total=6)


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


def matches_keywords(text):
    if not text:
        return []
    matched = []
    for kw, pattern in zip(KEYWORDS, KEYWORD_PATTERNS):
        if pattern.search(text):
            matched.append(kw)
    return list(set(matched))


# ---------------------------------------------------------------------------
# 1. SCRAPER GUICHET-EMPLOIS / JOB BANK (Flux RSS direct - Rapide)
# ---------------------------------------------------------------------------
async def fetch_jobbank():
    jobs = []
    feed_url = "https://www.jobbank.gc.ca/jobsearch/feed/rss?sort=D&fcat=21"
    try:
        global HTTP_SESSION
        if HTTP_SESSION is None or HTTP_SESSION.closed:
            HTTP_SESSION = ClientSession()

        async with HTTP_SESSION.get(feed_url, headers=HEADERS, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status == 200:
                raw_xml = await resp.text()
                # Parse le flux dans un thread séparé pour ne pas geler asyncio
                feed = await asyncio.to_thread(feedparser.parse, raw_xml)
                for entry in feed.entries[:20]:
                    title = entry.get("title", "")
                    link = entry.get("link", "")
                    summary = entry.get("summary", "")

                    matched_kw = matches_keywords(f"{title} {summary}")
                    if matched_kw:
                        jobs.append({
                            "source": "Guichet-Emplois (Job Bank)",
                            "title": title,
                            "url": link,
                            "keywords": matched_kw,
                        })
    except Exception as e:
        print(f"Job Bank timeout ou erreur: {e}")
    return jobs


# ---------------------------------------------------------------------------
# 2. SCRAPER JOBILLICO (Requête rapide ciblée)
# ---------------------------------------------------------------------------
async def fetch_jobillico_keyword(q):
    jobs = []
    url = f"https://www.jobillico.com/fr/recherche-emploi?keyword={urllib.parse.quote(q)}"
    try:
        global HTTP_SESSION
        if HTTP_SESSION is None or HTTP_SESSION.closed:
            HTTP_SESSION = ClientSession()

        async with HTTP_SESSION.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status == 200:
                html = await resp.text()
                soup = await asyncio.to_thread(BeautifulSoup, html, "html.parser")
                articles = soup.select("article, .job-listing, .job-card")

                for item in articles[:8]:
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
        print(f"Jobillico ({q}) timeout ou erreur: {e}")
    return jobs


async def fetch_jobillico():
    results = await asyncio.gather(
        fetch_jobillico_keyword("informatique"),
        fetch_jobillico_keyword("sql"),
        return_exceptions=True
    )
    combined = []
    for r in results:
        if isinstance(r, list):
            combined.extend(r)
    return combined


# ---------------------------------------------------------------------------
# 3. SCRAPER INDEED CANADA (Protection par timeout strict)
# ---------------------------------------------------------------------------
async def fetch_indeed_keyword(term):
    jobs = []
    rss_url = f"https://ca.indeed.com/rss?q={term}&sort=date"
    try:
        global HTTP_SESSION
        if HTTP_SESSION is None or HTTP_SESSION.closed:
            HTTP_SESSION = ClientSession()

        async with HTTP_SESSION.get(rss_url, headers=HEADERS, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status == 200:
                raw_xml = await resp.text()
                feed = await asyncio.to_thread(feedparser.parse, raw_xml)
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
        print(f"Indeed ({term}) bloqué ou timeout: {e}")
    return jobs


async def fetch_indeed():
    results = await asyncio.gather(
        fetch_indeed_keyword("informatique"),
        fetch_indeed_keyword("sql"),
        return_exceptions=True
    )
    combined = []
    for r in results:
        if isinstance(r, list):
            combined.extend(r)
    return combined


# ---------------------------------------------------------------------------
# EXÉCUTION DU SCAN
# ---------------------------------------------------------------------------
async def run_jobs_scan(is_manual=False):
    global SCAN_IN_PROGRESS, SENT_JOBS
    if SCAN_IN_PROGRESS:
        if is_manual:
            await send_telegram_alert("⚠️ *Une analyse est déjà en cours, merci de patienter quelques secondes...*")
        return

    SCAN_IN_PROGRESS = True
    try:
        if is_manual:
            await send_telegram_alert("⏳ *Recherche rapide en cours sur Guichet-Emplois, Jobillico et Indeed...*")

        # Exécution parallèle avec timeout global
        res_jobbank, res_jobillico, res_indeed = await asyncio.gather(
            fetch_jobbank(),
            fetch_jobillico(),
            fetch_indeed(),
            return_exceptions=True
        )

        all_jobs = []
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
                    f"💼 *NOUVELLE OFFRE D'EMPLOI* 💼\n\n"
                    f"🏢 *Plateforme* : {job['source']}\n"
                    f"📌 *Intitulé* : `{job['title']}`\n"
                    f"🔑 *Mots-clés* : `{kw_str}`\n\n"
                    f"🔗 [Consulter et postuler]({job['url']})"
                )
                await send_telegram_alert(msg)
                await asyncio.sleep(0.5)

        if is_manual and new_alerts == 0:
            await send_telegram_alert("ℹ️ *Scan terminé : Aucun nouveau poste détecté pour le moment.*")

    except Exception as e:
        print(f"Erreur globale scan: {e}")
    finally:
        # Déblocage systématique garanti
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
        print(f"Erreur purge Telegram: {e}")

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
            print(f"Polling Telegram exception: {e}")
        await asyncio.sleep(1)


# ---------------------------------------------------------------------------
# PLANIFICATEUR HORAIRE (Toutes les 60 minutes)
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
        "🤖 *Job Alert Bot Opérationnel (Version Haute Vitesse)*\n\n"
        "• Plateformes : Guichet-Emplois, Jobillico, Indeed.\n"
        "• Timeouts stricts appliqués : aucun risque de blocage.\n"
        "• Tapez `/check` pour lancer une vérification."
    )

    await asyncio.gather(
        scheduled_scanner(),
        listen_telegram(),
    )


if __name__ == "__main__":
    asyncio.run(main())
