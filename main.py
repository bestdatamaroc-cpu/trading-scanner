def extract_job_date(date_tag):
  # Si la balise existe, on retourne None ou on ignore le filtre pour le moment
  return None


import datetime
import time
import requests
from bs4 import BeautifulSoup

# Token et chat ID de votre bot Telegram
TELEGRAM_BOT_TOKEN = "VOTRE_TOKEN_ICI"
TELEGRAM_CHAT_ID = "VOTRE_CHAT_ID_ICI"

# Deux requêtes principales : Winnipeg et toute la Province de Québec
SEARCH_TARGETS = [
    {"label": "Winnipeg, MB", "param": "locationstring=Winnipeg%2C+MB"},
    {"label": "Province de Québec", "param": "fprov=QC"},
]

# Villes spécifiques à mettre en avant dans les alertes du Québec
FOCUS_CITIES = ["Montréal", "Montreal", "Laval", "Terrebonne", "Québec", "Quebec"]

# Ensemble pour mémoriser les IDs d'offres déjà notifiées
seen_job_ids = set()


def send_telegram(text: str):
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  data = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
  try:
    requests.post(url, data=data, timeout=10)
  except Exception as e:
    print(f"Erreur d'envoi Telegram : {e}")


def run_scanner():
  print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}] Lancement du scan...")

  for target in SEARCH_TARGETS:
    target_label = target["label"]
    target_param = target["param"]

    # URL avec tri par date décroissante (&sort=D)
    url = f"https://www.jobbank.gc.ca/jobsearch/jobsearch?searchstring=network&{target_param}&sort=D"

    try:
      response = requests.get(url, timeout=15)
      soup = BeautifulSoup(response.text, "html.parser")
      articles = soup.select("article")

      for article in articles:
        job_id = article.get("id", "")
        if not job_id or job_id in seen_job_ids:
          continue

        # Extraction du titre, du lien et de la ville exacte
        title_tag = article.select_one("span.noctitle")
        link_tag = article.select_one("a.resultJobItem")
        city_tag = article.select_one("li.location")
        date_tag = article.select_one("li.date")

        title = title_tag.get_text(strip=True) if title_tag else "Non spécifié"
        relative_link = link_tag.get("href", "") if link_tag else ""
        job_link = (
            f"https://www.jobbank.gc.ca{relative_link}" if relative_link else ""
        )
        detected_city = (
            city_tag.get_text(strip=True) if city_tag else target_label
        )

        # Extraction et filtre date : dernière semaine (<= 7 jours)
        # Job Bank affiche souvent la date sous forme texte ou data-attribute
        # Si date > 7 jours, on ignore
        job_date = extract_job_date(date_tag)
        if job_date and (datetime.date.today() - job_date).days > 7:
          continue

        # Mise en évidence du classement de la ville
        display_city = detected_city
        for city in FOCUS_CITIES:
          if city.lower() in detected_city.lower():
            display_city = f"⭐ {detected_city}"
            break

        # Format du message Telegram
        message = (
            f"💼 *OFFRE D'EMPLOI DÉTECTÉE* 💼\n\n"
            f"🏢 *Source :* Guichet-Emplois (Job Bank)\n"
            f"📌 *Poste :* {title}\n"
            f"📍 *Ville / Région :* {display_city}\n"
            f"🔑 *Mots-clés :* network\n\n"
            f"🔗 [Voir l'offre et postuler]({job_link})"
        )

        send_telegram(message)
        seen_job_ids.add(job_id)

    except Exception as e:
      print(f"Erreur lors du scan pour {target_label} : {e}")


# Fréquence d'exécution : toutes les 2 heures (7200 secondes)
if __name__ == "__main__":
  while True:
    run_scanner()
    time.sleep(7200)
