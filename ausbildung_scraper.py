"""
================================================================================
 AUSBILDUNG SCRAPER — MULTI-SOURCES ALLEMAGNE (VERSION PRODUCTION)
 Sources: Agentur für Arbeit API | Ausbildung.de | DuckDuckGo (Spontanées)
 Logique: Deep Scraping /kontakt /impressum /karriere | Zéro Hallucination
 Output: Google Sheet via Webhook + CSV local de sauvegarde
================================================================================
"""

import csv
import hashlib
import os
import random
import re
import sys
import time
from urllib.parse import urljoin, urlparse, quote_plus

import requests
from bs4 import BeautifulSoup

# Fix encodage console Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ==============================================================================
# CONFIGURATION CENTRALE
# ==============================================================================

# Spécialités Ausbildung Kaufmann ciblées (5 domaines)
SPECIALITES = [
    ("Kaufmann/-frau für Büromanagement",                    "buero"),
    ("Kauffrau im E-Commerce",                               "ecommerce"),
    ("Kaufmann/-frau im Groß- und Außenhandelsmanagement",   "handel"),
    ("Kaufmann/-frau für Spedition und Logistikdienstleistung", "spedition"),
    ("Kaufmann/-frau für Tourismus und Freizeit",            "tourismus"),
]

# Domaines à exclure des emails extraits (parasites, trackers, images)
EXCLUDED_EMAIL_DOMAINS = {
    "sentry.io", "wixpress.com", "schema.org", "example.com",
    "google.com", "facebook.com", "linkedin.com", "indeed.com",
    "youtube.com", "wikipedia.org", "twitter.com", "xing.com",
    "stepstone.de", "monster.de", "ausbildung.de", "azubiyo.de",
    "bundesagentur.de", "placeholder.com", "test.com", "example.de",
}

# Extensions d'images à exclure (faux emails type logo@banner.png)
EXCLUDED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf", ".zip"}

# Préfixes d'emails systèmes à ignorer
EXCLUDED_PREFIXES = {
    "noreply@", "no-reply@", "support@", "privacy@",
    "security@", "billing@", "admin@", "datenschutz@",
    "info-dse@", "newsletter@", "bounce@", "mailer-daemon@",
}

# Mots-clés indiquant un email RH prioritaire
RH_KEYWORDS = [
    "bewerbung", "karriere", "ausbildung", "hr", "recruiting",
    "personal", "kontakt", "jobs", "ausbildungsbuero", "bewerber",
]

# Pages à explorer sur chaque site d'entreprise (deep scraping)
DEEP_PAGES = [
    "", "/kontakt", "/karriere", "/ausbildung", "/impressum",
    "/jobs", "/stellenangebote", "/ausbildung-bewerbung",
    "/en/contact", "/de/kontakt",
]

# User-Agents réalistes pour éviter le blocage
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Regex email stricte
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


# ==============================================================================
# UTILITAIRES HTTP & EMAIL
# ==============================================================================

def get_headers(referer="https://www.google.de/"):
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.7,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": referer,
        "DNT": "1",
        "Connection": "keep-alive",
    }


def fetch(url, timeout=8, retries=2):
    """Télécharge une page HTML avec gestion des erreurs et retries."""
    for attempt in range(retries):
        try:
            time.sleep(random.uniform(0.4, 1.2))
            resp = requests.get(url, headers=get_headers(url), timeout=timeout, allow_redirects=True)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code in (403, 429):
                time.sleep(2 ** attempt * 3)
        except Exception:
            pass
    return None


def clean_email(raw):
    """
    Nettoie et valide un email extrait.
    Retourne None si invalide, tracker, image, etc.
    ZERO HALLUCINATION : ne retourne que de vrais emails valides.
    """
    if not raw:
        return None
    email = raw.strip().lower()
    email = re.sub(r"^[u003e>\"'\\]+", "", email)
    email = email.strip(".").strip()

    if "@" not in email or "." not in email.split("@")[-1]:
        return None
    if any(email.endswith(ext) for ext in EXCLUDED_EXTENSIONS):
        return None
    if not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", email):
        return None

    domain = email.split("@")[-1]
    if any(excl in domain for excl in EXCLUDED_EMAIL_DOMAINS):
        return None
    if any(email.startswith(pre) for pre in EXCLUDED_PREFIXES):
        return None
    if len(email.split("@")[0]) > 50:
        return None
    return email


def deobfuscate(text):
    """Désobfusque les emails cachés dans le texte (techniques anti-spam courantes)."""
    patterns = [
        (r"\s*\[at\]\s*",  "@"),
        (r"\s*\(at\)\s*",  "@"),
        (r"\s*\{at\}\s*",  "@"),
        (r"\s+at\s+",      "@"),
        (r"\s*\[dot\]\s*", "."),
        (r"\s*\(dot\)\s*", "."),
        (r"\s*\{dot\}\s*", "."),
        (r"&#64;",         "@"),
        (r"&#46;",         "."),
        (r"%40",           "@"),
    ]
    result = text
    for pattern, replacement in patterns:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


def extract_emails(html):
    """
    Extraction multi-méthodes d'emails depuis un HTML.
    1. Désobfuscation  2. Regex texte brut  3. Liens mailto:  4. Attributs data-
    """
    if not html:
        return set()

    clean_text = deobfuscate(html)
    raw_found = EMAIL_REGEX.findall(clean_text)

    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all("a", href=True):
            href = tag["href"]
            if href.lower().startswith("mailto:"):
                mail_raw = href[7:].split("?")[0].strip()
                raw_found.append(mail_raw)
        for tag in soup.find_all(attrs={"data-email": True}):
            raw_found.append(tag["data-email"])
        for tag in soup.find_all(attrs={"data-mail": True}):
            raw_found.append(tag["data-mail"])
    except Exception:
        pass

    result = set()
    for e in raw_found:
        cleaned = clean_email(e)
        if cleaned:
            result.add(cleaned)
    return result


def best_email(emails):
    """Retourne le(s) email(s) RH les plus pertinents (max 2 séparés par ' / ')."""
    if not emails:
        return None
    ranked = sorted(
        emails,
        key=lambda e: (
            -sum(1 for kw in RH_KEYWORDS if kw in e),
            len(e),
        )
    )
    return " / ".join(ranked[:2])


# ==============================================================================
# DEEP SCRAPING — SITE DE L'ENTREPRISE
# ==============================================================================

def deep_scrape_company(company_url):
    """
    Explore les pages clés d'un site d'entreprise pour trouver un email RH.
    Visite: /, /kontakt, /karriere, /ausbildung, /impressum, /jobs
    S'arrête dès qu'un email RH est trouvé.
    """
    if not company_url or "http" not in company_url:
        return None

    parsed = urlparse(company_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    all_emails = set()

    for path in DEEP_PAGES:
        target = urljoin(base_url, path) if path else company_url
        html = fetch(target, timeout=6)
        if not html:
            continue

        emails = extract_emails(html)
        rh_emails = {e for e in emails if any(kw in e for kw in RH_KEYWORDS)}
        if rh_emails:
            return best_email(rh_emails)
        all_emails.update(emails)

    return best_email(all_emails) if all_emails else None


def find_company_site_via_duckduckgo(company_name):
    """Trouve le site officiel d'une entreprise via DuckDuckGo HTML."""
    if not company_name or company_name.lower() in ("unternehmen deutschland", "n/a", ""):
        return None

    query = f"{company_name} Ausbildung Bewerbung Kontakt Deutschland"
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    html = fetch(url, timeout=10)
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, "lxml")
        for result in soup.find_all("a", class_="result__a"):
            href = result.get("href", "")
            if not href or "http" not in href:
                continue
            parsed = urlparse(href)
            domain = parsed.netloc.lower()
            if any(skip in domain for skip in [
                "linkedin", "facebook", "twitter", "xing", "youtube",
                "indeed", "stepstone", "monster", "ausbildung.de",
                "azubiyo", "wikipedia", "duckduckgo", "google",
                "arbeitsagentur", "bundesagentur",
            ]):
                continue
            if domain:
                return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return None


def get_email_for_offer(offer_url, company_name, company_url=None):
    """
    Stratégie complète d'extraction d'email:
    1. Page de l'offre → 2. Site de l'entreprise → 3. DuckDuckGo + deep scrape
    """
    # Étape 1 : Email sur la page de l'offre
    html = fetch(offer_url, timeout=8)
    if html:
        emails = extract_emails(html)
        if emails:
            result = best_email(emails)
            if result:
                return result

    # Étape 2 : Deep scraper le site connu
    if company_url and "http" in str(company_url):
        result = deep_scrape_company(company_url)
        if result:
            return result

    # Étape 3 : Trouver via DuckDuckGo + deep scraper
    site = find_company_site_via_duckduckgo(company_name)
    if site:
        result = deep_scrape_company(site)
        if result:
            return result

    return None


def make_job_id(url, source):
    """Génère un ID unique déterministe pour éviter les doublons."""
    raw = f"{source}::{url}".encode("utf-8")
    return f"{source[:3].lower()}_{hashlib.md5(raw).hexdigest()[:8]}"


# ==============================================================================
# SOURCE 1 : AGENTUR FÜR ARBEIT — API REST OFFICIELLE (100% LÉGALE)
# ==============================================================================

def scrape_agentur_fuer_arbeit():
    """
    Scrape l'API officielle et publique de l'Agentur für Arbeit.
    Endpoint: https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs
    Pagination complète jusqu'à épuisement des offres.
    """
    print("\n[SOURCE 1] Agentur fur Arbeit -- API REST officielle...")
    jobs = []

    API_BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs"
    API_HEADERS = {
        "User-Agent": "Jobsuche/2.9.2 (compatible)",
        "X-API-Key": "jobboerse-jobsuche",
        "Accept": "application/json",
        "Accept-Language": "de-DE",
    }

    for specialite, tag in SPECIALITES:
        print(f"  -> Specialite: {specialite}")
        page = 0
        page_size = 50
        total_found = 0

        while True:
            params = {
                "was": specialite,
                "wo": "Deutschland",
                "angebotsart": 4,  # 4 = Ausbildung
                "page": page,
                "size": page_size,
                "umkreis": 200,
            }

            try:
                time.sleep(random.uniform(0.8, 1.5))
                resp = requests.get(API_BASE, params=params, headers=API_HEADERS, timeout=10)

                if resp.status_code != 200:
                    print(f"    [!] HTTP {resp.status_code} -- arret pagination")
                    break

                data = resp.json()
                stellenangebote = data.get("stellenangebote", [])

                if not stellenangebote:
                    print(f"    OK Fin pagination (page {page}) -- {total_found} offres trouvees")
                    break

                for offer in stellenangebote:
                    title      = offer.get("titel", "").strip()
                    company    = (offer.get("arbeitgeber") or "Unternehmen Deutschland").strip()
                    ref_number = offer.get("refnr", "")
                    location   = offer.get("arbeitsort", {})
                    city       = location.get("ort", "Deutschland")
                    plz        = location.get("plz", "")
                    offer_url  = f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref_number}"
                    company_url = offer.get("arbeitgeberUrl", "") or ""

                    email = get_email_for_offer(offer_url, company, company_url if "http" in str(company_url) else None)

                    if not email:
                        continue  # FILTRE STRICT : pas d'email = pas de ligne

                    job_id = make_job_id(ref_number or offer_url, "ba")
                    jobs.append({
                        "date_detection": time.strftime("%Y-%m-%d %H:%M"),
                        "statut":         "NOUVEAU",
                        "role_cible":     specialite,
                        "intitule":       title or specialite,
                        "entreprise":     company,
                        "lieu":           f"{plz} {city}".strip() if plz else city,
                        "emails_rh":      email,
                        "source":         "Agentur fur Arbeit",
                        "lien":           offer_url,
                        "id":             job_id,
                    })
                    print(f"    OK BA: {company[:35]:35s} -> {email}")
                    total_found += 1

                total_treffer = data.get("maxErgebnisse", 0)
                if (page + 1) * page_size >= total_treffer:
                    break
                page += 1

            except requests.exceptions.RequestException as e:
                print(f"    [!] Erreur reseau BA: {e}")
                break
            except Exception as e:
                print(f"    [!] Erreur BA: {e}")
                break

    print(f"  -> Total Agentur fur Arbeit : {len(jobs)} offres avec email valide")
    return jobs


# ==============================================================================
# SOURCE 2 : AUSBILDUNG.DE — SCRAPING HTML AVEC PAGINATION COMPLÈTE
# ==============================================================================

def scrape_ausbildung_de():
    """
    Scrape Ausbildung.de pour chaque spécialité Kaufmann.
    Pagination complète jusqu'à ce qu'il n'y ait plus d'offres.
    """
    print("\n[SOURCE 2] Ausbildung.de -- Scraping HTML pagination complete...")
    jobs = []

    for specialite, tag in SPECIALITES:
        print(f"  -> Specialite: {specialite}")
        page = 1
        total_found = 0

        while True:
            query = quote_plus(specialite)
            url = f"https://www.ausbildung.de/suche/?q={query}&seite={page}"

            html = fetch(url, timeout=10)
            if not html:
                print(f"    [!] Page {page} inaccessible -- arret")
                break

            soup = BeautifulSoup(html, "lxml")

            # Détecter les cartes d'offres
            offer_cards = (
                soup.find_all("article", attrs={"data-testid": re.compile(r"job-card")}) or
                soup.find_all("li", class_=re.compile(r"job|stelle|angebot", re.I)) or
                soup.find_all("a", href=re.compile(r"/stellen/"))
            )

            if not offer_cards:
                print(f"    OK Fin pagination (page {page}) -- {total_found} offres trouvees")
                break

            seen_links_page = set()

            for card in offer_cards:
                if card.name == "a":
                    link_tag = card
                else:
                    link_tag = card.find("a", href=re.compile(r"/stellen/"))

                if not link_tag:
                    continue

                href = link_tag.get("href", "")
                if not href or "faq" in href or "ratgeber" in href:
                    continue

                offer_link = href if href.startswith("http") else urljoin("https://www.ausbildung.de", href)

                if offer_link in seen_links_page:
                    continue
                seen_links_page.add(offer_link)

                title_tag = card.find(["h2", "h3", "span"], class_=re.compile(r"title|name|heading", re.I))
                title = (title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True)) or specialite

                company = "Unternehmen Deutschland"
                match_comp = re.search(r"bei-([a-z0-9\-]+)-in-", href)
                if match_comp:
                    company = match_comp.group(1).replace("-", " ").title()
                else:
                    comp_tag = card.find(attrs={"data-company": True}) or card.find(class_=re.compile(r"company|unternehmen|arbeitgeber", re.I))
                    if comp_tag:
                        company = comp_tag.get_text(strip=True)

                lieu = "Deutschland"
                match_city = re.search(r"-in-([a-z\-]+)-[a-f0-9\-]{36}", href)
                if match_city:
                    lieu = match_city.group(1).replace("-", " ").title()

                email = get_email_for_offer(offer_link, company)

                if not email:
                    continue  # FILTRE STRICT

                job_id = make_job_id(offer_link, "aus")
                jobs.append({
                    "date_detection": time.strftime("%Y-%m-%d %H:%M"),
                    "statut":         "NOUVEAU",
                    "role_cible":     specialite,
                    "intitule":       title,
                    "entreprise":     company,
                    "lieu":           lieu,
                    "emails_rh":      email,
                    "source":         "Ausbildung.de",
                    "lien":           offer_link,
                    "id":             job_id,
                })
                print(f"    OK AUS: {company[:35]:35s} -> {email}")
                total_found += 1

            # Vérifier page suivante
            next_btn = soup.find("a", attrs={"aria-label": re.compile(r"naechste|next|weiter", re.I)})
            if not next_btn or not next_btn.get("href"):
                print(f"    OK Derniere page (page {page})")
                break

            page += 1
            time.sleep(random.uniform(1.0, 2.5))

    print(f"  -> Total Ausbildung.de : {len(jobs)} offres avec email valide")
    return jobs


# ==============================================================================
# SOURCE 3 : DUCKDUCKGO HTML — CANDIDATURES SPONTANÉES (ENTREPRISES DIRECTES)
# ==============================================================================

def scrape_spontaneous_via_duckduckgo():
    """
    Utilise DuckDuckGo HTML pour trouver des sites d'entreprises allemandes
    recrutant en Ausbildung et proposant un email de contact direct.
    """
    print("\n[SOURCE 3] DuckDuckGo HTML -- Candidatures spontanees entreprises...")
    jobs = []

    QUERIES_SPONTANEES = [
        ("Ausbildung Buromanagement Bewerbung Email Kontakt Unternehmen Deutschland 2025 2026", "Kaufmann/-frau für Büromanagement"),
        ("Kaufmann E-Commerce Ausbildung Bewerbung Email Deutschland 2025 2026", "Kauffrau im E-Commerce"),
        ("Ausbildung Spedition Logistik Kaufmann Bewerbung Email Kontakt 2025 2026", "Kaufmann/-frau für Spedition und Logistikdienstleistung"),
        ("Ausbildung Gross Aussenhandel Kaufmann Stelle Email Kontakt", "Kaufmann/-frau im Groß- und Außenhandelsmanagement"),
        ("Ausbildung Tourismus Reisebuero Kauffrau Email Bewerbung 2025 2026", "Kaufmann/-frau für Tourismus und Freizeit"),
        ("Ausbildungsplatz frei Kaufmann Unternehmen Email Kontakt Deutschland 2026", "Kaufmann/-frau für Büromanagement"),
        ("Ausbildung Kaufmann Mittelstand Bewerbung bewerbung@ Deutschland", "Kaufmann/-frau für Büromanagement"),
        ("Ausbildungsplatz Buromanagement Hamburg Berlin Bewerbung Kontakt 2026", "Kaufmann/-frau für Büromanagement"),
        ("Ausbildung Speditionskaufmann Hamburg Frankfurt bewerbung@ kontakt@", "Kaufmann/-frau für Spedition und Logistikdienstleistung"),
        ("Kauffrau ECommerce Ausbildung Unternehmen Bewerbung info@", "Kauffrau im E-Commerce"),
    ]

    for i, (query, specialite_cible) in enumerate(QUERIES_SPONTANEES):
        print(f"  -> Requete {i+1}/{len(QUERIES_SPONTANEES)}: {query[:65]}...")

        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&kl=de-de"
        html = fetch(url, timeout=12)

        if not html:
            continue

        try:
            soup = BeautifulSoup(html, "lxml")
            results = soup.find_all("div", class_="result__body") or soup.find_all("div", class_=re.compile(r"result", re.I))

            for result in results[:8]:
                link_tag = result.find("a", class_=re.compile(r"result__a", re.I)) or result.find("a")
                if not link_tag:
                    continue

                href = link_tag.get("href", "")
                if not href or "http" not in href:
                    continue

                parsed = urlparse(href)
                domain = parsed.netloc.lower()

                if any(skip in domain for skip in [
                    "linkedin", "facebook", "twitter", "xing", "youtube",
                    "indeed", "stepstone", "monster", "ausbildung.de",
                    "azubiyo", "wikipedia", "duckduckgo", "google",
                    "arbeitsagentur", "bundesagentur", "ausbildungsatlas",
                ]):
                    continue

                company_url = f"{parsed.scheme}://{parsed.netloc}"
                company = domain.replace("www.", "").split(".")[0].title()

                email = deep_scrape_company(company_url)

                if not email:
                    continue  # FILTRE STRICT

                job_id = make_job_id(company_url, "sp")
                jobs.append({
                    "date_detection": time.strftime("%Y-%m-%d %H:%M"),
                    "statut":         "NOUVEAU",
                    "role_cible":     specialite_cible,
                    "intitule":       f"Ausbildung {specialite_cible} - Candidature Spontanee",
                    "entreprise":     company,
                    "lieu":           "Deutschland",
                    "emails_rh":      email,
                    "source":         "Candidature Spontanee (DuckDuckGo)",
                    "lien":           company_url,
                    "id":             job_id,
                })
                print(f"    OK SPON: {company[:35]:35s} -> {email}")

        except Exception as e:
            print(f"    [!] Erreur parsing DDG: {e}")

        time.sleep(random.uniform(3.0, 6.0))

    print(f"  -> Total Candidatures Spontanees : {len(jobs)} entreprises avec email valide")
    return jobs


# ==============================================================================
# DÉDUPLICATION & VALIDATION FINALE
# ==============================================================================

def deduplicate(jobs, existing_ids=None):
    """Supprime les doublons par ID unique. Filtre strict : email doit contenir '@'."""
    seen = set(existing_ids) if existing_ids else set()
    unique = []
    for job in jobs:
        jid = job.get("id", "")
        if jid and jid not in seen:
            email = job.get("emails_rh", "")
            if "@" in email:
                seen.add(jid)
                unique.append(job)
    return unique


def load_existing_ids(filename):
    """Charge les IDs déjà présents dans le CSV local."""
    ids = set()
    if not os.path.exists(filename):
        return ids
    try:
        with open(filename, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("id"):
                    ids.add(row["id"].strip())
    except Exception:
        pass
    return ids


# ==============================================================================
# ENVOI WEBHOOK → GOOGLE SHEET
# ==============================================================================

def send_to_webhook(jobs):
    """Envoie les offres au webhook Google Apps Script (doPost). Lots de 50."""
    webhook_url = os.environ.get("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("\n[i] GOOGLE_SHEET_WEBHOOK_URL non configure -> CSV uniquement")
        return False

    batch_size = 50
    success = True

    for i in range(0, len(jobs), batch_size):
        batch = jobs[i:i + batch_size]
        try:
            resp = requests.post(
                webhook_url,
                json=batch,
                headers={"Content-Type": "application/json"},
                timeout=20,
            )
            if resp.status_code == 200:
                data = resp.json()
                print(f"  Webhook lot {i // batch_size + 1} -> {data.get('added', '?')} offres ajoutees")
            else:
                print(f"  [!] Webhook lot {i // batch_size + 1} -> HTTP {resp.status_code}")
                success = False
        except Exception as e:
            print(f"  [!] Erreur webhook: {e}")
            success = False
        time.sleep(1)

    return success


# ==============================================================================
# POINT D'ENTRÉE PRINCIPAL
# ==============================================================================

def main():
    print("=" * 70)
    print(" AUSBILDUNG SCRAPER -- MULTI-SOURCES ALLEMAGNE (VERSION PRODUCTION)")
    print(f" Demarrage : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    CSV_FILENAME = "ausbildung_applications_export.csv"
    FIELDNAMES = [
        "date_detection", "statut", "role_cible", "intitule",
        "entreprise", "lieu", "emails_rh", "source", "lien", "id"
    ]

    existing_ids = load_existing_ids(CSV_FILENAME)
    print(f"\n[i] IDs existants charges depuis CSV : {len(existing_ids)}")

    all_new_jobs = []

    # Source 1 : Agentur für Arbeit
    try:
        all_new_jobs.extend(scrape_agentur_fuer_arbeit())
    except Exception as e:
        print(f"[!] Source BA echouee: {e}")

    # Source 2 : Ausbildung.de
    try:
        all_new_jobs.extend(scrape_ausbildung_de())
    except Exception as e:
        print(f"[!] Source Ausbildung.de echouee: {e}")

    # Source 3 : DuckDuckGo
    try:
        all_new_jobs.extend(scrape_spontaneous_via_duckduckgo())
    except Exception as e:
        print(f"[!] Source DuckDuckGo echouee: {e}")

    unique_new = deduplicate(all_new_jobs, existing_ids)

    print(f"\n{'=' * 70}")
    print(f" RESUME : {len(all_new_jobs)} offres brutes -> {len(unique_new)} offres uniques avec email valide")
    print(f"{'=' * 70}")

    if not unique_new:
        print("\n[!] Aucune nouvelle offre avec email valide. Fin du scraper.")
        return

    # Sauvegarde CSV local
    existing_rows = {}
    if os.path.exists(CSV_FILENAME):
        try:
            with open(CSV_FILENAME, "r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    if row.get("id"):
                        existing_rows[row["id"]] = row
        except Exception:
            pass

    for job in unique_new:
        existing_rows[job["id"]] = job

    with open(CSV_FILENAME, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing_rows.values())

    print(f"\n[OK] CSV mis a jour : {len(existing_rows)} offres totales dans '{CSV_FILENAME}'")

    # Envoi Webhook Google Sheet
    print(f"\n[>>] Envoi de {len(unique_new)} nouvelles offres vers Google Sheet...")
    send_to_webhook(unique_new)

    print(f"\n[FIN] Scraper termine a {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"      Nouvelles offres envoyees : {len(unique_new)}")


if __name__ == "__main__":
    main()
