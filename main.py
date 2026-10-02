import asyncio
import os
import re
import unicodedata
import urllib.parse
from aiohttp import web, ClientSession, ClientTimeout

# --- CONFIGURATION TELEGRAM ---
TELEGRAM_BOT_TOKEN = "8834699234:AAHnqWUWz8auv0LbJDuMePTaeky8kmqIu0o"
TELEGRAM_CHAT_ID = "759626963"

# --- LISTE DES MOTS-CLÉS CIBLÉS ---
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

# Fichier local pour mémoriser les offres déjà envoyées
HISTORY_FILE = "seen_jobs.txt"
SENT_JOB_IDS = set()

def load_history():
    global SENT_JOB_IDS
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                SENT_JOB_IDS = set(line.strip() for line in f if line.strip())
        except Exception as e:
            print(f"Erreur lecture historique: {e}")

def save_job_id(job_id):
    global SENT_JOB_IDS
    SENT_JOB_IDS.add(job_id)
    try:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(f"{job_id}\n")
    except Exception as e:
        print(f"Erreur écriture historique: {e}")

# Charger l'historique au lancement
load_history()

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


def extract_canonical_job_id(url, title):
    """
    Extrait l'identifiant unique strict de Job Bank (ex: 43219876)
    pour neutraliser tous les paramètres de tracking d'URL.
    """
    id_match = re.search(r'/jobposting/(\d+)', url)
    if id_match:
        return f"jobbank_{id_match.group(1)}"
    
    # Nettoyage de l'URL si pas d'ID chiffré direct
    clean_url = url.split("?")[0].strip().rstrip("/")
    if clean_url:
        return clean_url
    
    # Fallback par titre normalisé
    return clean_text(title)[:50]


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
                articles = re.findall(r"<article[\s\S]*?</article>", html, re.IGNORECASE)

                for art in articles[:20]:
                    href_match = re.search(r'href="([^"]+)"', art)
                    if not href_match:
                        continue
                    link = href_match.group(1)
                    if not link.startswith("http"):
                        link = f"https://www.jobbank.gc.ca{link}"

                    clean_article = re.sub(r"<[^>]+>", " ", art)
                    clean_article = " ".join(clean_article.split())

                    title_match = re.search(r'class="noctitle">([^<]+)<', art)
                    if title_match:
                        title = title_match.group(1).strip()
                    else:
                        title = clean_article[:70]

                    matched_kw = find_matched_keywords(clean_article)
                    if matched_kw and len(title) > 3:
                        unique_id = extract_canonical_job_id(link, title)
                        jobs.append({
                            "id": unique_id,
                            "source": "Guichet-Emplois (Job Bank)",
                            "title": title,
                            "url": link.split("?")[0],  # URL propre sans paramètres
                            "keywords": matched_kw,
                        })
    except Exception as e:
        print(f"Erreur Job Bank ({term}): {e}")

    return jobs


async def run_jobs_scan(is_manual=False):
    global SCAN_IN_PROGRESS, SENT_JOB_IDS
    if SCAN_IN_PROGRESS:
        if is_manual:
            await send_telegram_alert("⚠️ *Une analyse est déjà en cours...*")
        return

    SCAN_IN_PROGRESS = True
    try:
        if is_manual:
            await send_telegram_alert("⏳ *Recherche des nouvelles offres en cours...*")

        terms = ["informatique", "technicien", "reseaux", "sql"]
        tasks = [fetch_jobbank_query(t) for t in terms]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_jobs = []
        for r in results:
            if isinstance(r, list):
                all_jobs.extend(r)

        # Déduplication en mémoire vive du lot actuel
        batch_unique = {}
        for j in all_jobs:
            job_key = j["id"]
            if job_key not in batch_unique:
                batch_unique[job_key] = j

        new_alerts = 0
        for job_id, job in batch_unique.items():
            if job_id not in SENT_JOB_IDS:
                save_job_id(job_id)
                new_alerts += 1

                kw_str = ", ".join(job["keywords"][:4])
                msg = (
                    f"💼 *NOUVELLE OFFRE D'EMPLOI* 💼\n\n"
                    f"📌 *Poste* : `{job['title']}`\n"
                    f"🔑 *Mots-clés* : `{kw_str}`\n\n"
                    f"🔗 [Voir l'offre et postuler]({job['url']})"
                )
                await send_telegram_alert(msg)
                await asyncio.sleep(0.5)

        if is_manual:
            if new_alerts == 0:
                await send_telegram_alert("ℹ️ *Aucune nouvelle offre inédite : les doublons ont été filtrés.*")
            else:
                await send_telegram_alert(f"✅ *{new_alerts} nouvelle(s) offre(s) unique(s) envoyée(s).*")

    except Exception as e:
        print(f"Erreur globale scan: {e}")
    finally:
        SCAN_IN_PROGRESS = False


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


async def scheduled_scanner():
    await asyncio.sleep(5)
    await run_jobs_scan(is_manual=False)
    while True:
        await asyncio.sleep(3600)  # Scan toutes les heures
        await run_jobs_scan(is_manual=False)


async def handle_ping(request):
    return web.Response(text="Bot Emploi anti-doublon actif sur Render")


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
        "🛡️ *Système Anti-Doublon Activé*\n\n"
        "• Filtrage par ID d'offre strict.\n"
        "• Mémoire sur disque persistante.\n"
        "• Tapez `/check` pour lancer une recherche."
    )

    await asyncio.gather(
        scheduled_scanner(),
        listen_telegram(),
    )


if __name__ == "__main__":
    asyncio.run(main())
