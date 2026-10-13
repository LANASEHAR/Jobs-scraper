"""
Scraper optimisé anti-blocage (LinkedIn, Indeed, Remote APIs, Job Boards)
Conçu pour tourner jusqu'à 4h30 avec enrichissement intelligent d'emails.
"""

import os
import re
import time
import json
import random
import requests
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from jobspy import scrape_jobs

WEBHOOK_URL = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "").strip()

# Configuration des User-Agents récents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
EXCLUDE_DOMAINS = {"example.com", "sentry.io", "google.com", "wixpress.com", "schema.org"}
EXCLUDE_PREFIX = {"noreply", "no-reply", "mailer-daemon", "donotreply", "support-auto"}

def get_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    return s

SESSION = get_session()

# ==========================================
# 1. SCRAPING INDEED & LINKEDIN VIA JOBSPY
# ==========================================
def scrape_linkedin_and_indeed(query="Customer Success Manager", location="Casablanca", is_remote=False):
    """
    Extrait Indeed & LinkedIn sans être banni en utilisant python-jobspy.
    """
    print(f"[*] Recherche de postes pour '{query}' à '{location}' (Remote={is_remote})...")
    site_list = ["indeed", "linkedin"]
    
    try:
        jobs = scrape_jobs(
            site_name=site_list,
            search_term=query,
            location=location,
            is_remote=is_remote,
            results_wanted=30,
            hours_old=72,
            country_indeed='morocco' if "casablanca" in location.lower() or "morocco" in location.lower() else 'usa'
        )
        
        results = []
        for _, row in jobs.iterrows():
            company = str(row.get('company', '')).strip()
            title = str(row.get('title', '')).strip()
            job_url = str(row.get('job_url', '')).strip()
            site = str(row.get('site', '')).strip()
            location_res = str(row.get('location', location)).strip()
            
            if not company or not title:
                continue
                
            results.append({
                "company": company,
                "title": title,
                "url": job_url,
                "site": site,
                "location": location_res,
                "is_remote": is_remote
            })
        return results
    except Exception as e:
        print(f"[!] Erreur JobSpy : {e}")
        return []

# ==========================================
# 2. APIS REMOTE OFFICIELLES (Zéro blocage)
# ==========================================
def scrape_remote_apis():
    jobs = []
    # RemoteOK
    try:
        r = SESSION.get("https://remoteok.com/api", timeout=10)
        if r.status_code == 200:
            for item in r.json():
                if isinstance(item, dict) and item.get("company") and item.get("position"):
                    pos = item["position"].lower()
                    if any(term in pos for term in ["customer success", "account manager", "sales", "operations"]):
                        jobs.append({
                            "company": item["company"],
                            "title": item["position"],
                            "url": item.get("url", ""),
                            "site": "RemoteOK",
                            "location": "Remote",
                            "is_remote": True
                        })
    except Exception as e:
        print(f"[!] Erreur RemoteOK : {e}")

    # Jobicy API
    try:
        r = SESSION.get("https://jobicy.com/api/v2/remote-jobs?count=100", timeout=10)
        if r.status_code == 200:
            for item in r.json().get("jobs", []):
                title = item.get("jobTitle", "").lower()
                if any(term in title for term in ["customer success", "account manager", "sales", "operations"]):
                    jobs.append({
                        "company": item.get("companyName"),
                        "title": item.get("jobTitle"),
                        "url": item.get("url"),
                        "site": "Jobicy",
                        "location": "Remote",
                        "is_remote": True
                    })
    except Exception as e:
        print(f"[!] Erreur Jobicy : {e}")

    return jobs

# ==========================================
# 3. ENRICHISSEMENT INTELLIGENT D'EMAILS
# ==========================================
def find_company_website(company_name):
    """Trouve le domaine officiel via DuckDuckGo HTML avec polite delay."""
    try:
        q = f"{company_name} official website"
        r = SESSION.get(f"https://html.duckduckgo.com/html/?q={requests.utils.quote(q)}", timeout=8)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a.result__url"):
                href = a.get("href", "").strip()
                if href.startswith("//"): href = "https:" + href
                parsed = urlparse(href)
                domain = parsed.netloc.lower().replace("www.", "")
                if domain and not any(bad in domain for bad in ["linkedin", "indeed", "glassdoor", "wikipedia", "facebook", "twitter"]):
                    return f"https://{domain}"
    except Exception:
        pass
    return None

def extract_emails_from_url(url):
    try:
        r = SESSION.get(url, timeout=7)
        if r.status_code != 200: return set()
        
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(" ")
        emails = set(EMAIL_REGEX.findall(text))
        
        for mailto in soup.select('a[href^="mailto:"]'):
            raw = mailto.get("href", "").replace("mailto:", "").split("?")[0].strip()
            if EMAIL_REGEX.match(raw):
                emails.add(raw)
                
        # Filtrer les mauvais emails
        valid = set()
        for e in emails:
            e = e.lower().strip()
            domain = e.split("@")[-1]
            prefix = e.split("@")[0]
            if domain not in EXCLUDE_DOMAINS and prefix not in EXCLUDE_PREFIX:
                valid.add(e)
        return valid
    except Exception:
        return set()

def enrich_company_emails(company_site):
    """Visite la home + seulement les liens réels de contact/careers."""
    if not company_site:
        return None
        
    found_emails = set()
    try:
        r = SESSION.get(company_site, timeout=7)
        if r.status_code != 200:
            return None
            
        soup = BeautifulSoup(r.text, "html.parser")
        found_emails.update(extract_emails_from_url(company_site))
        
        # Trouver les liens réels vers contact ou carrières sur la page
        target_links = set()
        for a in soup.find_all("a", href=True):
            href = a['href']
            lower_href = href.lower()
            if any(term in lower_href for term in ["contact", "career", "jobs", "recrut", "about", "mentions", "impressum"]):
                full_url = urljoin(company_site, href)
                # Vérifier qu'on reste sur le même domaine
                if urlparse(full_url).netloc == urlparse(company_site).netloc:
                    target_links.add(full_url)
                    if len(target_links) >= 3: # Max 3 pages pour préserver la bande passante
                        break
                        
        for link in target_links:
            found_emails.update(extract_emails_from_url(link))
            time.sleep(random.uniform(1.0, 2.0))
            
    except Exception:
        pass
        
    return list(found_emails)

# ==========================================
# 4. ENVOI DES LIGNES VERS GOOGLE SHEETS
# ==========================================
def push_to_sheets(payload):
    if not WEBHOOK_URL:
        print("[!] GOOGLE_SHEET_WEBHOOK_URL non configurée.")
        return
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=20)
        print(f"[Sheets] Réponse du Webhook : {r.status_code}")
    except Exception as e:
        print(f"[!] Erreur Webhook : {e}")

# ==========================================
# 5. BOUCLE PRINCIPALE (GESTION DES 4H30)
# ==========================================
def main_run(max_duration_seconds=16200): # 4h30 = 16200 secondes
    start_time = time.time()
    print(f"[*] Démarrage de la session (Durée max : {max_duration_seconds/3600}h)...")
    
    searches = [
        ("Customer Success Manager", "Casablanca", False),
        ("Account Manager", "Casablanca", False),
        ("Sales Operations", "Casablanca", False),
        ("Administration des ventes", "Casablanca", False),
        ("Customer Success Manager", "Worldwide", True),
        ("Account Manager", "EMEA", True),
    ]
    
    seen_companies = set()
    
    # 1. Scraping des APIs (rapide, sans blocage)
    remote_jobs = scrape_remote_apis()
    for j in remote_jobs:
        if j['company'] not in seen_companies:
            process_job(j)
            seen_companies.add(j['company'])
            
    # 2. Scraping Indeed / LinkedIn avec rotation
    while (time.time() - start_time) < max_duration_seconds:
        for query, loc, remote in searches:
            if (time.time() - start_time) >= max_duration_seconds:
                break
                
            jobs = scrape_linkedin_and_indeed(query, loc, remote)
            for j in jobs:
                if j['company'] not in seen_companies:
                    process_job(j)
                    seen_companies.add(j['company'])
                    
                    # Pause respectueuse anti-bannissement entre chaque entreprise
                    sleep_time = random.uniform(3.0, 6.0)
                    time.sleep(sleep_time)
                    
            # Pause entre les blocs de recherche
            time.sleep(random.uniform(10.0, 20.0))
            
        print("[*] Cycle terminé. Mise en veille de 15 minutes avant le prochain cycle...")
        time.sleep(900)

def process_job(job):
    print(f"[+] Traitement de : {job['company']} - {job['title']}")
    site = find_company_website(job['company'])
    emails = enrich_company_emails(site) if site else []
    
    email_str = ", ".join(emails) if emails else ""
    print(f"    -> Site : {site} | Emails : {email_str or 'Non trouvé'}")
    
    if email_str: # Écrire uniquement si un email réel a été découvert
        payload = {
            "action": "addJob",
            "job": {
                "Entreprise": job['company'],
                "Intitulé": job['title'],
                "Lien": job['url'],
                "Lieu": job['location'],
                "Emails RH": email_str,
                "Company Site": site or "",
                "Source": job['site'],
                "Email Status": "FOUND"
            }
        }
        push_to_sheets(payload)

if __name__ == "__main__":
    main_run(max_duration_seconds=16200) # 4h30
