import csv
import hashlib
import os
import random
import re
import sys
import time
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import requests

# Fix encodage console Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# ==============================================================================
# 1. MOTS-CLÉS GLOBAUX ÉTENDUS (> 10 MOTS-CLÉS STRATÉGIQUES)
# ==============================================================================
GLOBAL_KEYWORDS = [
    'Sales',
    'Account Manager',
    'Customer Success',
    'Business Development',
    'Commercial',
    'Administration',
    'ADV',
    'Inside Sales',
    'Operations',
    'Support',
    'B2B',
    'Remote Sales',
    'Assistant',
]

EXCLUDED_DOMAINS = [
    'sentry.io',
    'wixpress.com',
    'schema.org',
    'example.com',
    'google.com',
    'facebook.com',
    'linkedin.com',
    'indeed.com',
    'youtube.com',
    'wikipedia.org',
    'rekrute.com',
    'emploi.ma',
]

EXCLUDED_PREFIXES = [
    'noreply@',
    'no-reply@',
    'support@',
    'privacy@',
    'security@',
    'billing@',
    'admin@',
    'datenschutz@',
]

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (Version/17.4 Safari/605.1.15)',
]

EMAIL_REGEX = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'

RH_KEYWORDS = [
    'recrutement',
    'rh',
    'careers',
    'jobs',
    'talent',
    'hiring',
    'contact',
    'hr',
    'bewerbung',
]

def get_random_headers():
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept-Language': 'fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7',
    }

def clean_email(email):
    if not email:
        return None
    email = email.strip().strip('.').lower()
    email = re.sub(r'^[u003e\\\'"]+', '', email)
    
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        return None

    domain = email.split('@')[-1]
    if any(ex in domain for ex in EXCLUDED_DOMAINS) or any(
        email.startswith(pre) for pre in EXCLUDED_PREFIXES
    ):
        return None
    return email

def extract_real_emails(text):
    if not text:
        return set()
    clean_text = (
        text.replace(' [at] ', '@')
        .replace(' (at) ', '@')
        .replace('[at]', '@')
        .replace('(at)', '@')
        .replace(' [dot] ', '.')
    )
    soup = BeautifulSoup(text, "html.parser") if '<' in text else None
    raw = re.findall(EMAIL_REGEX, clean_text)

    if soup:
        for a in soup.find_all("a", href=True):
            if a["href"].startswith("mailto:"):
                mail = a["href"].replace("mailto:", "").split("?")[0].strip()
                raw.append(mail)

    return {clean_email(e) for e in raw if clean_email(e)}

def find_company_website(company_name):
    """Trouve le site web officiel d'une entreprise via Google Search."""
    if not company_name or company_name.lower() in ['n/a', 'entreprise', 'entreprises']:
        return None
    
    query = f"{company_name} site officiel contact email"
    url = f"https://www.google.com/search?q={requests.utils.quote(query)}"
    try:
        time.sleep(random.uniform(1.0, 2.0))
        res = requests.get(url, headers=get_random_headers(), timeout=6)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for g in soup.find_all("div", class_="g"):
                a = g.find("a", href=True)
                if a and "http" in a["href"]:
                    href = a["href"]
                    parsed = urlparse(href)
                    domain = parsed.netloc.lower()
                    if not any(ex in domain for ex in ['google', 'linkedin', 'facebook', 'youtube', 'indeed', 'rekrute', 'wikipedia']):
                        return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return None

def scan_website_for_hr_emails(company_url):
    """Exploration des sous-pages RH d'une entreprise (/contact, /careers, /about, /recrutement, etc.)."""
    if not company_url or 'http' not in company_url:
        return None
        
    found_emails = set()
    sub_paths = ['', '/contact', '/careers', '/recrutement', '/jobs', '/about', '/mentions-legales', '/impressum']
    
    for path in sub_paths:
        try:
            target_url = urljoin(company_url, path)
            time.sleep(random.uniform(0.2, 0.4))
            resp = requests.get(target_url, headers=get_random_headers(), timeout=5)
            if resp.status_code == 200:
                emails = extract_real_emails(resp.text)
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
        return ' / '.join(sorted_emails[:2])
    return None

def deep_extract_email_for_company(company_name, company_url=None):
    """Deep Scraping : trouve le site de l'entreprise et extrait l'e-mail RH réel."""
    email = None
    if company_url and 'http' in company_url:
        email = scan_website_for_hr_emails(company_url)
    
    if not email:
        site = find_company_website(company_name)
        if site:
            email = scan_website_for_hr_emails(site)
            
    return email

# ==============================================================================
# 2. DÉCOUVERTE DYNAMIQUE DE 100+ ENTREPRISES (AVEC EXTRACTION STRICTE DES EMAILS)
# ==============================================================================
def discover_daily_companies():
    print('[+] Recherche dynamique des entreprises (Casablanca, Maroc, Remote)...')
    companies = []
    query_variations = [
        'multinational companies Casablanca hiring sales customer success',
        'tech companies remote Morocco jobs contact email',
        'foreign companies operating in Casablanca office careers',
        'Casanearshore companies list contact email',
        'Casablanca Finance City members directory jobs contact',
    ]

    for q in query_variations:
        for start in [0, 10]:
            url = f'https://www.google.com/search?q={requests.utils.quote(q)}&start={start}'
            try:
                time.sleep(random.uniform(1.5, 2.5))
                res = requests.get(url, headers=get_random_headers(), timeout=8)
                if res.status_code == 200:
                    soup = BeautifulSoup(res.text, 'html.parser')
                    for g in soup.find_all('div', class_='g'):
                        a = g.find('a', href=True)
                        title_tag = g.find('h3')
                        if a and title_tag:
                            link = a['href']
                            name = title_tag.text.split('-')[0].split('|')[0].strip()[:40]
                            if not any(ex in link for ex in ['linkedin.com', 'youtube.com', 'wikipedia.org', 'indeed.com']):
                                companies.append({'name': name, 'site': link})
            except Exception:
                continue

    unique_comps = list({c['name']: c for c in companies}.values())
    print(f'[+] {len(unique_comps)} entreprises identifiées.')
    return unique_comps

# ==============================================================================
# 3. SCRAPING STRICT : SEULES LES OFFRES AVEC UN E-MAIL RH RÉEL SONT CONSERVÉES
# ==============================================================================
def scrape_all_sources_strictly():
    valid_jobs = []
    print('[+] Scraping des offres ET extraction systématique des e-mails RH sur le site de chaque entreprise...')

    # 1. LinkedIn Jobs
    print('  -> Exploration LinkedIn...')
    for kw in GLOBAL_KEYWORDS[:6]:
        url = f'https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={requests.utils.quote(kw)}&location=Morocco&start=0'
        try:
            time.sleep(random.uniform(1.0, 2.0))
            res = requests.get(url, headers=get_random_headers(), timeout=8)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                cards = soup.find_all('li')
                for card in cards[:10]:
                    title_elem = card.find('h3', class_='base-search-card__title')
                    comp_elem = card.find('h4', class_='base-search-card__subtitle')
                    link_elem = card.find('a', class_='base-card__full-link')
                    if title_elem and comp_elem and link_elem:
                        title = title_elem.text.strip()
                        company = comp_elem.text.strip()
                        link = link_elem['href'].split('?')[0]

                        # Deep Scraping : Aller chercher le vrai mail RH sur le site de l'entreprise
                        email_rh = deep_extract_email_for_company(company)

                        # FILTRE STRICT : On ne conserve l'offre QUE si un e-mail RH réel existe
                        if email_rh:
                            job_id = f"li_{hashlib.md5(link.encode()).hexdigest()[:8]}"
                            valid_jobs.append({
                                'date_detection': time.strftime('%Y-%m-%d %H:%M'),
                                'statut': 'NOUVEAU',
                                'role_cible': kw,
                                'intitule': title,
                                'entreprise': company,
                                'lieu': 'Maroc / Remote',
                                'emails_rh': email_rh,
                                'source': 'LinkedIn',
                                'lien': link,
                                'id': job_id,
                            })
                            print(f"      ✅ Offre conservée avec e-mail RH : {company} -> {email_rh}")
        except Exception as e:
            continue

    # 2. Entreprises & Candidatures Spontanées
    print('  -> Exploration des sites d’entreprises...')
    companies = discover_daily_companies()
    for m in companies[:15]:
        email_rh = deep_extract_email_for_company(m['name'], m['site'])
        if email_rh:
            job_id = f"sp_{m['name'].lower().replace(' ', '_')[:10]}_{hashlib.md5(m['site'].encode()).hexdigest()[:6]}"
            valid_jobs.append({
                'date_detection': time.strftime('%Y-%m-%d %H:%M'),
                'statut': 'NOUVEAU',
                'role_cible': 'Sales / Customer Success / Commercial',
                'intitule': f"Commercial / Customer Success - {m['name']}",
                'entreprise': m['name'],
                'lieu': 'Maroc / Remote International',
                'emails_rh': email_rh,
                'source': 'Site Entreprise Direct',
                'lien': m['site'],
                'id': job_id,
            })
            print(f"      ✅ Entreprise conservée avec e-mail RH : {m['name']} -> {email_rh}")

    return valid_jobs

# ==============================================================================
# 4. ENVOI AUTOMATIQUE WEBHOOK GOOGLE SHEET & EXPORT CSV
# ==============================================================================
def send_to_google_sheet_webhook(jobs):
    webhook_url = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", "")
    if not webhook_url:
        print("[i] Aucun GOOGLE_SHEET_WEBHOOK_URL configuré. Enregistrement CSV uniquement.")
        return
    try:
        res = requests.post(webhook_url, json=jobs, timeout=15)
        if res.status_code == 200:
            print(f"🚀 SUCCÈS WEBHOOK : {len(jobs)} offres avec e-mails RH insérées directement dans ton Google Sheet !")
        else:
            print(f"[!] Code retour Webhook : {res.status_code}")
    except Exception as e:
        print(f"[!] Erreur d'envoi Webhook : {e}")

if __name__ == '__main__':
    print("=== SCRAPER MAROC & REMOTE (EXTRACTION STRICTE E-MAILS RH RÉELS) ===")
    valid_jobs = scrape_all_sources_strictly()

    filename = 'offres_rh_maroc_quotidien.csv'
    if valid_jobs:
        unique_jobs = list({j["id"]: j for j in valid_jobs}.values())
        fieldnames = list(unique_jobs[0].keys())
        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(unique_jobs)
        print(
            f'\n✅ SUCCÈS TOTAL : {len(unique_jobs)} offres AVEC E-MAILS RH RÉELS enregistrées dans {filename} !'
        )
        send_to_google_sheet_webhook(unique_jobs)
    else:
        print('\n[!] Aucune offre avec e-mail RH valide détectée lors de cette session.')