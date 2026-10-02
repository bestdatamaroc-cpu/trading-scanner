import asyncio
import os
import re
import unicodedata
import urllib.parse
from aiohttp import web, ClientSession, ClientTimeout
import feedparser
from bs4 import BeautifulSoup

# --- CONFIGURATION IDENTIFIANTS TELEGRAM ---
TELEGRAM_BOT_TOKEN = "8834699234:AAHnqWUWz8auv0LbJDuMePTaeky8kmqIu0o"
TELEGRAM_CHAT_ID = "759626963"

# --- LISTE DES MOTS-CLÉS CIBLES ---
KEYWORDS_RAW = [
    "informatique",
    "technicien",
    "technical helper",
    "data analyst",
    "dataanalyste",
    "analyste",
    "chef de projet",
    "project manager",
    "database",
    "base de donnees",
    "sql",
    "reseau",
    "reseaux",
    "network",
    "infrastructure",
    "systeme",
    "systemes",
    "support it",
    "technicien support",
]

def clean_text(text):
    """Supprime les accents et met en minuscules pour comparer facilement."""
    if not text:
        return ""
    text = unicodedata.normalize('NFD', text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    return text.lower()

KEYWORDS_CLEAN = [clean_text(k) for k in KEYWORDS_RAW]

SENT_JOBS = set()
SCAN_IN_PROGRESS = False
HTTP_SESSION = None

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-CA,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}

REQUEST_TIMEOUT = ClientTimeout(total=8)


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
# 1. GUICHET-EMPLOIS / JOB BANK (Flux RSS direct par recherche de mots-clés)
# ---------------------------------------------------------------------------
async def fetch_jobbank():
    jobs = []
    # Recherche directe sur Job Bank pour "informatique" et "technicien"
    search_queries = ["informatique", "technicien", "sql"]
    
    for q in search_queries:
        feed_url = f"https://www.jobbank.gc.ca/jobsearch/feed/rss?searchstring={urllib.parse.quote(q)}&sort=D"
        try:
            global HTTP_SESSION
            if HTTP_SESSION is None or HTTP_SESSION.closed:
                HTTP_SESSION = ClientSession()

            async with HTTP_SESSION.get(feed_url, headers=HEADERS, timeout=REQUEST_TIMEOUT) as resp:
                if resp.status == 200:
                    raw_xml = await resp.text()
                    feed = await asyncio.to_thread(feedparser.parse, raw_xml)
                    for entry in feed.entries[:15]:
                        title = entry.get("title", "")
                        link = entry.get("link", "")
                        summary = entry.get("summary", "")

                        matched = find_matched_keywords(f"{title} {summary}")
                        if matched:
                            jobs.append({
                                "source": "Guichet-Emplois (Job Bank)",
                                "title": title,
                                "url": link,
                                "keywords": matched,
                            })
        except Exception as e:
            print(f"Erreur Job Bank ({q}): {e}")
            
    return jobs


# ---------------------------------------------------------------------------
# 2. JOBILLICO (Extraction web)
# ---------------------------------------------------------------------------
async def fetch_jobillico():
    jobs = []
    queries = ["technicien-informatique", "sql"]
    for q in queries:
        url = f"https://www.jobillico.com/fr/recherche-emploi/{q}"
        try:
            global HTTP_SESSION
            if HTTP_SESSION is None or HTTP_SESSION.closed:
                HTTP_SESSION = ClientSession()

            async with HTTP_SESSION.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = await asyncio.to_thread(BeautifulSoup, html, "html.parser")
                    articles = soup.find_all(["article", "div"], class_=re.compile("job|listing", re.I))

                    for art in articles[:10]:
                        a_tag = art.find("a", href=True)
                        if a_tag:
                            title = a_tag.get_text(strip=True)
                            link = a_tag["href"]
                            if link.startswith("/"):
                                link = f"https://www.jobillico.com{link}"

                            full_text = f"{title} {art.get_text(strip=True)}"
                            matched = find_matched_keywords(full_text)
                            if matched and len(title) > 3:
                                jobs.append({
                                    "source": "Jobillico",
                                    "title": title,
                                    "url": link,
                                    "keywords": matched,
                                })
        except Exception as e:
            print(f"Erreur Jobillico ({q}): {e}")
    return jobs


# ---------------------------------------------------------------------------
# 3. INDEED CANADA (Flux RSS par mot-clé)
# ---------------------------------------------------------------------------
async def fetch_indeed():
    jobs = []
    queries = ["technicien+informatique", "reseau+informatique"]
    for q in queries:
        url = f"https://ca.indeed.com/rss?q={q}&sort=date"
        try:
            global HTTP_SESSION
            if HTTP_SESSION is None or HTTP_SESSION.closed:
                HTTP_SESSION = ClientSession()

            async with HTTP_SESSION.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT) as resp:
                if resp.status == 200:
                    raw_xml = await resp.text()
                    feed = await asyncio.to_thread(feedparser.parse, raw_xml)
                    for entry in feed.entries[:10]:
                        title = entry.get("title", "")
                        link = entry.get("link", "")
                        summary = entry.get("summary", "")

                        matched = find_matched_keywords(f"{title} {summary}")
                        if matched:
                            jobs.append({
                                "source": "Indeed",
                                "title": title,
                                "url": link,
                                "keywords": matched,
                            })
        except Exception as e:
            print(f"Erreur Indeed ({q}): {e}")
    return jobs


# ---------------------------------------------------------------------------
# EXÉCUTION DU SCAN ET ALERTES
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
            await send_telegram_alert("⏳ *Recherche en direct sur Guichet-Emplois, Jobillico et Indeed...*")

        res_jb, res_ji, res_ind = await asyncio.gather(
            fetch_jobbank(),
            fetch_jobillico(),
            fetch_indeed(),
            return_exceptions=True
        )

        jb_list = res_jb if isinstance(res_jb, list) else []
        ji_list = res_ji if isinstance(res_ji, list) else []
        ind_list = res_ind if isinstance(res_ind, list) else []

        all_jobs = jb_list + ji_list + ind_list

        new_alerts = 0
        for job in all_jobs:
            job_url = job["url"].strip()
            if job_url and job_url not in SENT_JOBS:
                SENT_JOBS.add(job_url)
                new_alerts += 1

                kw_str = ", ".join(job["keywords"][:4])
                msg = (
                    f"💼 *OFFRE D'EMPLOI DÉTECTÉE* 💼\n\n"
                    f"🏢 *Source* : {job['source']}\n"
                    f"📌 *Poste* : `{job['title']}`\n"
                    f"🔑 *Mots-clés* : `{kw_str}`\n\n"
                    f"🔗 [Voir l'offre et postuler]({job['url']})"
                )
                await send_telegram_alert(msg)
                await asyncio.sleep(0.5)

        if is_manual:
            status_report = (
                f"📊 *Bilan du Scan* :\n"
                f"• Guichet-Emplois : `{len(jb_list)}` offres trouvées\n"
                f"• Jobillico : `{len(ji_list)}` offres trouvées\n"
                f"• Indeed : `{len(ind_list)}` offres trouvées\n\n"
            )
            if new_alerts == 0:
                status_report += "ℹ️ *Aucune nouvelle offre inédite n'a été ajoutée.*"
            else:
                status_report += f"✅ *{new_alerts} nouvelle(s) alerte(s) envoyée(s).* "
            await send_telegram_alert(status_report)

    except Exception as e:
        print(f"Erreur globale: {e}")
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
            print(f"Erreur polling: {e}")
        await asyncio.sleep(1)


# ---------------------------------------------------------------------------
# PLANIFICATEUR HORAIRE
# ---------------------------------------------------------------------------
async def scheduled_scanner():
    await asyncio.sleep(5)
    await run_jobs_scan(is_manual=False)
    while True:
        await asyncio.sleep(3600)  # Scan automatique toutes les 60 min
        await run_jobs_scan(is_manual=False)


# ---------------------------------------------------------------------------
# SERVEUR WEB HEALTHCHECK RENDER
# ---------------------------------------------------------------------------
async def handle_ping(request):
    return web.Response(text="Job Alert Bot actif sur Render")


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
        "🤖 *Job Alert Bot mis à jour avec diagnostic direct.*\n"
        "Tapez `/check` pour voir le bilan des offres par site."
    )

    await asyncio.gather(
        scheduled_scanner(),
        listen_telegram(),
    )


if __name__ == "__main__":
    asyncio.run(main())
