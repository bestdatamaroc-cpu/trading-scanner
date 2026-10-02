import asyncio
import os
import re
import unicodedata
import urllib.parse
from aiohttp import web, ClientSession, ClientTimeout
from bs4 import BeautifulSoup
import feedparser

# --- CONFIGURATION IDENTIFIANTS TELEGRAM ---
TELEGRAM_BOT_TOKEN = "8834699234:AAHnqWUWz8auv0LbJDuMePTaeky8kmqIu0o"
TELEGRAM_CHAT_ID = "759626963"

# --- LISTE DES MOTS-CLÉS ---
KEYWORDS_RAW = [
    "informatique",
    "technicien informatique",
    "technical helper",
    "data analyst",
    "dataanalyste",
    "chef de projet",
    "database",
    "sql",
    "reseau",
    "reseaux",
    "network",
    "infrastructure",
    "systeme d'information",
    "it support",
    "technicien",
]

def clean_text(text):
    if not text:
        return ""
    text = unicodedata.normalize('NFD', text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    return text.lower()

KEYWORDS_CLEAN = [clean_text(k) for k in KEYWORDS_RAW]

SENT_JOBS = set()
SCAN_IN_PROGRESS = False
HTTP_SESSION = None

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-CA,fr;q=0.9,en-CA;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
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
# 1. SCRAPER GUICHET-EMPLOIS DIRECT (Parsing HTML réel des résultats)
# ---------------------------------------------------------------------------
async def fetch_jobbank_live(search_term):
    jobs = []
    # Recherche par date décroissante (sort=D) sur Job Bank
    url = f"https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring={urllib.parse.quote(search_term)}&sort=D"
    
    try:
        global HTTP_SESSION
        if HTTP_SESSION is None or HTTP_SESSION.closed:
            HTTP_SESSION = ClientSession()

        async with HTTP_SESSION.get(url, headers=BROWSER_HEADERS, timeout=ClientTimeout(total=8)) as resp:
            if resp.status == 200:
                html = await resp.text()
                soup = await asyncio.to_thread(BeautifulSoup, html, "html.parser")
                
                # Les offres Job Bank sont encapsulées dans des balises <article>
                articles = soup.find_all("article")
                for art in articles[:15]:
                    # Titre et lien
                    link_elem = art.find("a", href=True)
                    if not link_elem:
                        continue
                    
                    link = link_elem["href"]
                    if not link.startswith("http"):
                        link = f"https://www.jobbank.gc.ca{link}"
                    
                    # On nettoie le titre
                    title_elem = art.find("span", class_="noctitle") or link_elem
                    title = title_elem.get_text(strip=True)
                    
                    full_text = f"{title} {art.get_text(strip=True)}"
                    matched = find_matched_keywords(full_text)
                    
                    if matched and len(title) > 2:
                        jobs.append({
                            "source": "Guichet-Emplois (Job Bank)",
                            "title": title,
                            "url": link,
                            "keywords": matched
                        })
    except Exception as e:
        print(f"Erreur Job Bank ({search_term}): {e}")
        
    return jobs


# ---------------------------------------------------------------------------
# 2. FLUX DIRECT EMPLOI IT CANADA (Canaux d'agrégation d'offres sans blocage)
# ---------------------------------------------------------------------------
async def fetch_tech_jobs_feed():
    jobs = []
    # Flux ouverts certifiés pour les postes IT & Support
    feeds = [
        "https://remoteok.com/remote-it-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss"
    ]
    
    for feed_url in feeds:
        try:
            global HTTP_SESSION
            if HTTP_SESSION is None or HTTP_SESSION.closed:
                HTTP_SESSION = ClientSession()

            async with HTTP_SESSION.get(feed_url, headers=BROWSER_HEADERS, timeout=ClientTimeout(total=8)) as resp:
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
                                "source": "Réseau Recrutement IT",
                                "title": title,
                                "url": link,
                                "keywords": matched
                            })
        except Exception as e:
            print(f"Erreur flux IT: {e}")
            
    return jobs


# ---------------------------------------------------------------------------
# EXÉCUTION DU SCAN ET ENVOI SUR TELEGRAM
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
            await send_telegram_alert("⏳ *Recherche des offres en direct...*")

        # Requêtes simultanées sur Guichet-Emplois pour différents termes clés
        res_jb1, res_jb2, res_jb3, res_tech = await asyncio.gather(
            fetch_jobbank_live("informatique"),
            fetch_jobbank_live("technicien"),
            fetch_jobbank_live("reseau"),
            fetch_tech_jobs_feed(),
            return_exceptions=True
        )

        all_jobs = []
        for r in [res_jb1, res_jb2, res_jb3, res_tech]:
            if isinstance(r, list):
                all_jobs.extend(r)

        # Déduplication des résultats bruts par URL
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
                    f"💼 *OFFRE D'EMPLOI DÉTECTÉE* 💼\n\n"
                    f"🏢 *Source* : {job['source']}\n"
                    f"📌 *Poste* : `{job['title']}`\n"
                    f"🔑 *Mots-clés* : `{kw_str}`\n\n"
                    f"🔗 [Voir l'offre et postuler]({job['url']})"
                )
                await send_telegram_alert(msg)
                await asyncio.sleep(0.5)

        if is_manual:
            status = (
                f"📊 *Bilan du Scan* :\n"
                f"• Offres trouvées : `{len(unique_jobs)}`\n"
                f"• Nouvelles alertes envoyées : `{new_alerts}`\n"
            )
            if new_alerts == 0 and len(unique_jobs) > 0:
                status += "ℹ️ *Toutes les offres trouvées ont déjà été envoyées précédemment.*"
            elif len(unique_jobs) == 0:
                status += "ℹ️ *Aucune offre correspondante actuellement.*"
            await send_telegram_alert(status)

    except Exception as e:
        print(f"Erreur scan: {e}")
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
        "🤖 *Job Alert Bot connecté avec Scraper direct Guichet-Emplois & Réseaux.*\n"
        "Tapez `/check` pour lancer l'analyse."
    )

    await asyncio.gather(
        scheduled_scanner(),
        listen_telegram(),
    )


if __name__ == "__main__":
    asyncio.run(main())
