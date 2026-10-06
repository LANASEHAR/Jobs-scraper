#!/usr/bin/env python3
"""Company-first Travel / Hospitality prospect scraper.

This pipeline discovers companies, finds their official sites and public emails,
and classifies remote/international compatibility. It does NOT scrape job offers.
"""
import csv
import hashlib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
TIMEOUT = 10
MAX_RUNTIME = 42 * 60
START = time.monotonic()

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,de;q=0.7",
})

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}")
BAD_EMAIL_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "google.com", "example.com", "sentry.io", "cloudflare.com"
}
BAD_LOCAL_PARTS = {"noreply", "no-reply", "donotreply", "mailer-daemon", "postmaster"}

BLOCKED_HOSTS = {
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "flexjobs.com", "remoteok.com", "weworkremotely.com", "remotive.com",
    "jobgether.com", "workingnomads.com", "himalayas.app",
    "greenhouse.io", "job-boards.greenhouse.io", "jobs.ashbyhq.com",
    "jobs.lever.co", "workable.com", "smartrecruiters.com",
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "wikipedia.org", "crunchbase.com"
}

DIRECTORY_HOSTS = {
    "thepearsonco.com", "revenuebase.ai", "kustiq.com",
    "hoteltechreport.com", "flexjobs.com", "ilha.org", "bookdomits.com"
}

SEED_COMPANIES = [
    "HBX Group", "Hotelbeds", "Bedsonline", "WeTravel", "Cloudbeds", "Nuitee",
    "TravelPerk", "Amadeus", "Sabre", "RateGain", "SiteMinder", "Mews",
    "D-EDGE", "Octorate", "Guesty", "Hostaway", "Lodgify", "Cendyn",
    "Sojern", "Mirai", "HotelRunner", "DerbySoft", "WebBeds",
    "Emerging Travel Group", "Booking.com", "Expedia Group", "GetYourGuide",
    "Agoda", "Trip.com", "Klook", "Traveloka", "Omio", "Trainline",
    "Airbnb", "Hopper", "Tourlane", "TBO", "HotelPlanner",
    "Travels Tech", "Wamasol", "Centra", "Hotel-Spider", "SimpleBooking",
    "Duetto", "IDeaS", "Guestline", "RoomRaccoon"
]

COMPANY_QUERIES = [
    '"travel technology" companies B2B travel platform',
    '"travel tech" companies SaaS booking platform',
    '"hospitality technology" companies hotel tech',
    '"hotel technology" companies PMS CRS booking engine',
    '"B2B travel" technology companies',
    '"travel SaaS" companies',
    '"online travel" technology companies',
    '"hotel software" companies hospitality SaaS',
    '"travel API" companies B2B',
    '"travel distribution" technology companies',
    '"hospitality SaaS" companies',
    '"travel marketplace" technology companies',
    '"travel management" technology companies',
    '"hotel booking" technology companies',
    '"tourism technology" companies',
    '"travel platform" company careers',
    '"hospitality tech" company careers',
    '"travel tech" company careers remote'
]

CONTACT_PATHS = [
    "", "/contact", "/contact-us", "/contactus", "/careers", "/career",
    "/jobs", "/join-us", "/work-with-us", "/about", "/about-us",
    "/company", "/team", "/impressum", "/legal", "/privacy"
]

REMOTE_TERMS = re.compile(
    r"remote|work from home|distributed|work from anywhere|anywhere in the world|"
    r"global team|fully remote|hybrid|flexible work|international team|emea|mena",
    re.I
)
MOROCCO_TERMS = re.compile(r"\b(morocco|maroc|casablanca|morocco-based)\b", re.I)
TRAVEL_TERMS = re.compile(
    r"travel|tourism|hospitality|hotel|booking|ota|dmc|tour operator|"
    r"property management|reservation|travel agency|guest experience|"
    r"travel technology|travel tech|hotel tech",
    re.I
)

def budget_ok():
    return time.monotonic() - START < MAX_RUNTIME

def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()

def host(url):
    try:
        h = urlparse(url).netloc.lower().split(":")[0]
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""

def root_domain(url):
    h = host(url)
    parts = h.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else h

def valid_email(email):
    email = clean(email).lower().strip(" <>.,;:'\"()[]")
    if not EMAIL_RE.fullmatch(email):
        return False
    local, domain = email.rsplit("@", 1)
    return local not in BAD_LOCAL_PARTS and domain not in BAD_EMAIL_DOMAINS

def extract_emails(html):
    if not html:
        return set()
    text = re.sub(r"\s*(?:\[|\(|\{)\s*(?:at|ät)\s*(?:\]|\)|\})\s*", "@", html, flags=re.I)
    text = re.sub(r"\s*(?:\[|\(|\{)\s*(?:dot|punkt)\s*(?:\]|\)|\})\s*", ".", text, flags=re.I)
    found = {e.lower() for e in EMAIL_RE.findall(text) if valid_email(e)}
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select('a[href^="mailto:"]'):
            e = a.get("href", "")[7:].split("?", 1)[0]
            if valid_email(e):
                found.add(e.lower())
    except Exception:
        pass
    return found

def fetch(url):
    if not url or not budget_ok():
        return ""
    try:
        response = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
        return response.text if response.status_code == 200 else ""
    except requests.RequestException:
        return ""

def search(query, limit=12):
    results, seen = [], set()
    for engine in ("https://www.bing.com/search?q=", "https://html.duckduckgo.com/html/?q="):
        if len(results) >= limit or not budget_ok():
            break
        try:
            response = SESSION.get(engine + quote_plus(query), timeout=8)
            if response.status_code != 200:
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            selectors = ["li.b_algo h2 a[href]"] if "bing" in engine else ["a.result__a[href]", "a.result-link[href]"]
            for selector in selectors:
                for anchor in soup.select(selector):
                    url = anchor.get("href", "")
                    if not url.startswith("http") or url in seen:
                        continue
                    seen.add(url)
                    results.append((clean(anchor.get_text(" ", strip=True)), url))
                    if len(results) >= limit:
                        break
                if len(results) >= limit:
                    break
        except requests.RequestException:
            continue
    return results

def is_good_official_site(url):
    h = host(url)
    if not h or h in BLOCKED_HOSTS or h in DIRECTORY_HOSTS:
        return False
    return not any(x in h for x in ("linkedin", "indeed", "glassdoor", "crunchbase", "wikipedia"))

def company_name_from_host(url):
    h = host(url)
    if not is_good_official_site(url):
        return ""
    label = re.sub(r"[-_]+", " ", h.split(".")[0]).strip()
    return label.title()

def extract_site_identity(html, fallback=""):
    if not html:
        return fallback
    try:
        soup = BeautifulSoup(html, "html.parser")
        for selector in ['meta[property="og:site_name"]', 'meta[name="application-name"]']:
            tag = soup.select_one(selector)
            if tag and tag.get("content"):
                name = clean(tag["content"])
                if 2 <= len(name) <= 80:
                    return name
        title = clean(soup.title.get_text(" ", strip=True) if soup.title else "")
        if title:
            title = re.split(r"\s+[|–—-]\s+|\s+::\s+", title)[0].strip()
            if 2 <= len(title) <= 80:
                return title
    except Exception:
        pass
    return fallback

def find_official_site(company):
    for query in (f'"{company}" official website', f'"{company}" travel hospitality', f'"{company}" careers contact'):
        for _, url in search(query, 8):
            if not is_good_official_site(url):
                continue
            html = fetch(url)
            if not html:
                continue
            text = clean(BeautifulSoup(html, "html.parser").get_text(" ", strip=True)[:12000])
            if TRAVEL_TERMS.search(text) or TRAVEL_TERMS.search(company):
                return url.split("#")[0].rstrip("/") + "/"
    return ""

def rank_email(email):
    local = email.split("@", 1)[0].lower()
    preferred = ["careers", "career", "recruit", "recruitment", "jobs", "job", "talent", "hiring", "hr", "people", "join", "bewerbung", "hello", "info", "contact", "team"]
    return (0 if any(k in local for k in preferred) else 1, len(local))

def company_contact(company, site):
    if not site:
        return "", "", ""
    urls = [urljoin(site, path) for path in CONTACT_PATHS]
    pages = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch, u): u for u in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                pages[url] = future.result()
            except Exception:
                pages[url] = ""

    emails = set()
    texts = []
    for html in pages.values():
        emails |= extract_emails(html)
        if html:
            try:
                texts.append(BeautifulSoup(html, "html.parser").get_text(" ", strip=True)[:6000])
            except Exception:
                pass

    combined = clean(" ".join(texts))
    email = sorted(emails, key=rank_email)[0] if emails else ""
    return email, combined, "; ".join(sorted(emails)[:10])

def classify_access(text):
    t = clean(text)
    if MOROCCO_TERMS.search(t) and REMOTE_TERMS.search(t):
        return "CONFIRMED_MOROCCO"
    if re.search(r"\b(emea|mena|africa|europe|european union|worldwide|global)\b", t, re.I) and REMOTE_TERMS.search(t):
        return "REGION_MATCH_VERIFY"
    if REMOTE_TERMS.search(t):
        return "REMOTE_VERIFY"
    return "NOT_VERIFIED"

def fit_score(company, description):
    t = (company + " " + description).lower()
    groups = [
        (30, ["travel", "travel-tech", "travel tech", "tourism", "hotel", "hospitality", "ota", "dmc"], "Travel / Hospitality"),
        (22, ["b2b", "business travel", "supplier", "distribution", "platform", "api"], "B2B / Platform"),
        (18, ["customer success", "account management", "partnership", "sales", "commercial"], "Commercial / Account Management"),
        (12, ["booking", "reservation", "pms", "crs", "guest", "property management"], "Booking / Hotel Operations"),
        (10, ["remote", "distributed", "global team", "emea", "mena"], "Remote / International"),
        (8, ["morocco", "maroc", "africa"], "Morocco / Africa relevance"),
    ]
    score, reasons = 0, []
    for points, words, label in groups:
        if any(word in t for word in words):
            score += points
            reasons.append(label)
    return min(score, 100), "; ".join(reasons)

def make_record(company, site, email, source, text, access, all_emails):
    description = clean(text)[:2500]
    score, reasons = fit_score(company, description)
    stable = root_domain(site) or company.lower()
    return {
        "id": "company_" + hashlib.sha256(stable.encode()).hexdigest()[:18],
        "date_detection": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "statut": "NEW",
        "role_cible": "Spontaneous application – Travel / Hospitality",
        "intitule": "Spontaneous application – Travel / Hospitality",
        "entreprise": company,
        "lieu": "International / Remote eligibility to verify",
        "remote": access != "NOT_VERIFIED",
        "source": source,
        "lien": site,
        "company_site": site,
        "emails_rh": all_emails,
        "all_public_emails": all_emails,
        "deep_status": "DONE",
        "fit_score": score,
        "fit_reasons": reasons,
        "salary": "",
        "description": description,
        "posted_age": "",
        "posted_within_24h": "N/A",
        "search_type": "COMPANY_FIRST_TRAVEL_HOSPITALITY",
        "email_status": "FOUND" if all_emails else "NO_EMAIL",
        "email_source": "official_company_site" if email else "",
        "spontaneous": "YES",
        "access_from_morocco": access,
    }

def discover_candidates():
    candidates = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(search, q, 12) for q in COMPANY_QUERIES]
        for future in as_completed(futures):
            try:
                candidates.extend(future.result())
            except Exception:
                pass
    for company in SEED_COMPANIES:
        candidates.append((company, ""))

    unique, seen = [], set()
    for title, url in candidates:
        key = root_domain(url) if url else clean(title).lower()
        if key and key not in seen:
            seen.add(key)
            unique.append((title, url))
    return unique

def enrich_candidate(candidate):
    title, url = candidate
    if url and is_good_official_site(url):
        html = fetch(url)
        company = extract_site_identity(html, company_name_from_host(url))
        site = url.split("#")[0].rstrip("/") + "/"
    else:
        company = re.sub(r"\s+(official website|careers|company|travel tech|hospitality tech)$", "", clean(title), flags=re.I).strip()
        site = find_official_site(company)

    if not company or not site:
        return None

    html = fetch(site)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    home_text = clean(soup.get_text(" ", strip=True)[:10000])
    if not TRAVEL_TERMS.search(home_text + " " + company):
        return None

    email, contact_text, all_emails = company_contact(company, site)
    combined = clean(home_text + " " + contact_text)
    access = classify_access(combined)

    return make_record(company, site, email, "official_company_site", combined, access, all_emails)

def discover():
    candidates = discover_candidates()
    records = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(enrich_candidate, c) for c in candidates]
        for future in as_completed(futures):
            if not budget_ok():
                break
            try:
                record = future.result()
                if record:
                    records.append(record)
            except Exception:
                pass

    unique = {}
    for record in records:
        key = root_domain(record["company_site"]) or record["entreprise"].lower()
        previous = unique.get(key)
        if not previous or (not previous["emails_rh"] and record["emails_rh"]):
            unique[key] = record

    records = list(unique.values())
    records.sort(key=lambda x: (
        x["email_status"] == "FOUND",
        x["access_from_morocco"] == "CONFIRMED_MOROCCO",
        x["access_from_morocco"] == "REGION_MATCH_VERIFY",
        int(x["fit_score"] or 0)
    ), reverse=True)
    return records[:400]

def post(records):
    if not WEBHOOK or not records:
        print("WEBHOOK missing or no company records:", len(records), flush=True)
        return

    for i in range(0, len(records), 25):
        payload = {"mode": "jobs", "sheet": "Remote Travel Hospitality", "jobs": records[i:i + 25]}
        for attempt in range(3):
            try:
                response = requests.post(WEBHOOK, json=payload, timeout=90)
                print("[WEBHOOK]", response.status_code, response.text[:300], flush=True)
                if response.ok:
                    break
            except requests.RequestException as exc:
                print("[WEBHOOK ERROR]", exc, flush=True)
            time.sleep(2 * (attempt + 1))

def write_csv(records):
    fields = ["date_detection", "statut", "entreprise", "site_entreprise", "email", "emails_publics", "source", "acces_depuis_maroc", "fit_score", "fit_reason", "type_prospect", "id"]
    with open("remote_travel_hospitality.csv", "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({
                "date_detection": record["date_detection"],
                "statut": record["statut"],
                "entreprise": record["entreprise"],
                "site_entreprise": record["company_site"],
                "email": record["emails_rh"],
                "emails_publics": record["all_public_emails"],
                "source": record["source"],
                "acces_depuis_maroc": record["access_from_morocco"],
                "fit_score": record["fit_score"],
                "fit_reason": record["fit_reasons"],
                "type_prospect": "COMPANY_FIRST_TRAVEL_HOSPITALITY",
                "id": record["id"],
            })

def main():
    records = discover()
    print("COMPANY_RECORDS", len(records), flush=True)
    print("WITH_EMAIL", sum(bool(x["emails_rh"]) for x in records), flush=True)
    print("CONFIRMED_MOROCCO", sum(x["access_from_morocco"] == "CONFIRMED_MOROCCO" for x in records), flush=True)
    print("REGION_MATCH_VERIFY", sum(x["access_from_morocco"] == "REGION_MATCH_VERIFY" for x in records), flush=True)
    post(records)
    write_csv(records)

if __name__ == "__main__":
    main()
