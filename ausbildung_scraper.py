import csv
import hashlib
import os
import random
import re
import sys
import time
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import requests

# Fix encodage console Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# ==============================================================================
# CONFIGURATION & KEYWORDS KAUFMANN (AUCUNE LIMITATION DE RECHERCHE)
# ==============================================================================
SPECIALITES_KAUFMANN = [
    "Kaufmann fuer Buromanagement",
    "Kaufmann im E-Commerce",
    "Kaufmann im Gross- und Aussenhandelsmanagement",
    "Kaufmann fuer Spedition und Logistikdienstleistung",
    "Kaufmann fuer Tourismus und Freizeit",
    "Kaufmann",  # Recherche générale Kaufmann / Ausbildung libre
    "Ausbildung Kaufmann"
]

EXCLUDED_DOMAINS = [
    "sentry.io", "wixpress.com", "schema.org", "example.com", 
    "google.com", "facebook.com", "linkedin.com", "placeholder.com",
    "ausbildung.de", "azubiyo.de"
]

EXCLUDED_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf"]

EXCLUDED_PREFIXES = [
    "noreply@", "no-reply@", "support@", "privacy@", 
    "security@", "billing@", "admin@", "datenschutz@"
]

RH_KEYWORDS = [
    "bewerbung", "karriere", "ausbildung", "hr", 
    "recruiting", "personal", "kontakt", "jobs"
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (Version/17.4 Safari/605.1.15)"
]

def get_random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    }

# ==============================================================================
# EXTRACTION STRICTE E-MAILS (ZÉRO INVENTIONS)
# ==============================================================================
def clean_email(email):
    if not email:
        return None
    email = email.strip().strip(".").lower()
    email = re.sub(r'^[u003e\\\'"]+', '', email)
    
    if any(email.endswith(ext) for ext in EXCLUDED_EXTENSIONS):
        return None
        
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        return None
        
    domain = email.split("@")[-1]
    if any(ex in domain for ex in EXCLUDED_DOMAINS) or any(email.startswith(pre) for pre in EXCLUDED_PREFIXES):
        return None
        
    return email

def extract_real_emails_from_html(html_content):
    if not html_content:
        return set()
        
    clean_text = (
        html_content.replace(" [at] ", "@")
        .replace(" (at) ", "@")
        .replace("[at]", "@")
        .replace("(at)", "@")
        .replace(" [dot] ", ".")
    )
    
    regex = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    raw = re.findall(regex, clean_text)

    soup = BeautifulSoup(html_content, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:"):
            mail = href.replace("mailto:", "").split("?")[0].strip()
            raw.append(mail)

    return {clean_email(e) for e in raw if clean_email(e)}

def deep_scrape_company_site(company_url):
    """Deep Scraping des sous-pages RH clés en Allemagne (/kontakt, /karriere, etc.)."""
    if not company_url or "http" not in company_url:
        return "Non détecté (Postuler via lien)"

    found_emails = set()
    sub_paths = ["", "/kontakt", "/karriere", "/ausbildung", "/impressum"]

    for path in sub_paths:
        try:
            target_url = urljoin(company_url, path)
            time.sleep(random.uniform(0.1, 0.2))
            resp = requests.get(target_url, headers=get_random_headers(), timeout=4)
            if resp.status_code == 200:
                emails = extract_real_emails_from_html(resp.text)
                rh_filtered = {e for e in emails if any(k in e for k in RH_KEYWORDS)}
                if rh_filtered:
                    found_emails.update(rh_filtered)
                    break
                found_emails.update(emails)
        except Exception:
            continue

    if found_emails:
        sorted_emails = sorted(
            list(found_emails),
            key=lambda x: any(k in x for k in RH_KEYWORDS),
            reverse=True,
        )
        return " / ".join(sorted_emails[:2])

    return "Non détecté (Postuler via lien)"

# ==============================================================================
# SCRAPER PORTAILS ALLEMANDS (SANS RESTRICTION NI LIMITES DE NOMBRE)
# ==============================================================================
def scrape_german_portals():
    all_jobs = []
    print("[+] Lancement du scraping massif des offres d'Ausbildung Kaufmann (Sans Limite)...")

    for role in SPECIALITES_KAUFMANN:
        print(f"\n[+] Recherche massive : {role}")
        
        # Scrape sur Ausbildung.de (Pagination / Recherche illimitée)
        for page in range(1, 10): # Scrape jusqu'à 10 pages par spécialité
            search_url = f"https://www.ausbildung.de/suche/?q={role.replace(' ', '+')}&page={page}"
            try:
                time.sleep(random.uniform(0.5, 1.0))
                res = requests.get(search_url, headers=get_random_headers(), timeout=8)
                if res.status_code != 200:
                    break

                soup = BeautifulSoup(res.text, "html.parser")
                offer_links = soup.find_all("a", href=re.compile(r"/stellen/"))
                
                if not offer_links:
                    break

                seen_links = set()
                page_count = 0
                
                for a in offer_links:
                    href = a["href"]
                    if href in seen_links or "faq" in href or "ratgeber" in href:
                        continue
                    seen_links.add(href)

                    link = urljoin("https://www.ausbildung.de", href)
                    title = a.text.strip() if a.text.strip() else role
                    company = "Unternehmen Deutschland"
                    
                    match_comp = re.search(r"bei-([a-z0-9-]+)-in-", href)
                    if match_comp:
                        company = match_comp.group(1).replace("-", " ").title()

                    email_rh = deep_scrape_company_site(link)
                    job_id = f"aus_{hashlib.md5(link.encode()).hexdigest()[:8]}"

                    all_jobs.append({
                        "date_detection": time.strftime("%Y-%m-%d %H:%M"),
                        "statut": "NOUVEAU",
                        "role_cible": role,
                        "intitule": title,
                        "entreprise": company,
                        "lieu": "Deutschland (Allemagne)",
                        "emails_rh": email_rh,
                        "source": "Ausbildung.de",
                        "lien": link,
                        "id": job_id,
                    })
                    page_count += 1
                        
                print(f"    -> Page {page} (Ausbildung.de) : {page_count} offres extraites")
            except Exception as e:
                print(f"    [!] Erreur sur page {page} : {e}")
                break

    return all_jobs

def send_to_google_sheet_webhook(jobs):
    webhook_url = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", "")
    if not webhook_url:
        print("[i] Aucun GOOGLE_SHEET_WEBHOOK_URL configuré. Données uniquement enregistrées en CSV.")
        return
    try:
        res = requests.post(webhook_url, json=jobs, timeout=15)
        if res.status_code == 200:
            print("🚀 SUCCÈS WEBHOOK : Offres envoyées et insérées directement dans ton Google Sheet !")
        else:
            print(f"[!] Code retour Webhook : {res.status_code}")
    except Exception as e:
        print(f"[!] Erreur d'envoi Webhook : {e}")

def run_scraper_job():
    jobs = scrape_german_portals()
    filename = "ausbildung_applications_export.csv"

    if jobs:
        # Fusionner avec les données existantes si le fichier CSV existe déjà
        existing_jobs = {}
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        existing_jobs[row["id"]] = row
            except Exception:
                pass

        # Ajouter les nouvelles offres
        for j in jobs:
            if j["id"] not in existing_jobs:
                existing_jobs[j["id"]] = j

        fieldnames = ["date_detection", "statut", "role_cible", "intitule", "entreprise", "lieu", "emails_rh", "source", "lien", "id"]
        
        with open(filename, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(existing_jobs.values())
        print(f"\n✅ SUCCÈS TOTAL : {len(existing_jobs)} offres au total enregistrées dans '{filename}'.")
        
        # Envoi direct vers Google Sheet via Webhook
        send_to_google_sheet_webhook(jobs)
    else:
        print("\n[!] Aucune nouvelle offre récupérée lors de cette session.")

if __name__ == "__main__":
    print("=== SCRAPER AUSBILDUNG KAUFMANN (MODE CLOUD / GITHUB ACTIONS) ===")
    run_scraper_job()