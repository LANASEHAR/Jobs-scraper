"""
MOROCCO / CASABLANCA JOB SCRAPER
Discovery base: LinkedIn public job pages, using search-engine indexing as a fallback
when direct LinkedIn public search is blocked in GitHub Actions.
Targets: remote jobs based in Morocco + onsite/hybrid/remote jobs in Casablanca.
Deep search is separate: LinkedIn -> Google Sheet immediately -> company-site research -> enrichment.
No login, CAPTCHA bypass, fabricated email or fabricated company website.
"""
import argparse, hashlib, os, random, re, time
from urllib.parse import quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
SHEET_NAME = "Morocco Jobs"

ROLE_QUERIES = [
    "Customer Success Manager", "Customer Success Specialist", "Customer Success Associate",
    "Account Manager", "Key Account Manager", "Customer Account Manager",
    "Customer Support Specialist", "Customer Experience Specialist", "Client Success Manager",
    "Sales Executive", "Account Executive", "Business Development Representative",
    "Sales Development Representative", "Inside Sales Representative", "B2B Sales Representative",
    "Operations Coordinator", "Business Operations Specialist", "Administrative Coordinator",
    "Administrative Assistant", "Sales Operations Specialist", "Commercial Operations Specialist",
    "Back Office Specialist", "Project Coordinator", "E-commerce Specialist", "Shopify Specialist",
    "CRM Specialist", "Digital Marketing Specialist", "Junior UX UI Designer", "Junior UX Designer",
    "Junior UI Designer",
]

# Geography is intentionally explicit so the scraper can find both categories requested:
# 1) remote jobs open to candidates in Morocco
# 2) onsite/hybrid/remote jobs located in Casablanca
GEO_QUERIES = [
    "Morocco remote", "Maroc remote", "Casablanca", "Casablanca Morocco", "Casablanca Maroc"
]

EXCLUDE_TERMS = [
    "senior", "sr.", "sr ", "lead", "principal", "director", "vp ", "vice president",
    "head of", "chief", "staff", "architect", "internship", "intern ", "doctor", "nurse",
    "software engineer", "software developer", "data scientist", "machine learning", "devops",
    "lawyer", "accountant", "physician", "warehouse worker", "driver", "technician",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
]

session = requests.Session()


def headers(referer="https://www.linkedin.com/"):
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
        "Accept": "text/html,application/xhtml+xml",
        "Referer": referer,
    }


def fetch(url, timeout=15, retries=2):
    for attempt in range(retries + 1):
        try:
            r = session.get(url, headers=headers(), timeout=timeout, allow_redirects=True)
            if r.status_code == 200:
                return r.text
            if r.status_code in (403, 429):
                time.sleep((attempt + 1) * 2 + random.random())
        except requests.RequestException:
            time.sleep(1 + attempt)
    return None


def clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def job_id(url, title=""):
    return "ma_" + hashlib.sha256((url + "|" + title).encode()).hexdigest()[:16]


def is_target(title, description=""):
    text = (title + " " + description).lower()
    return not any(x in text for x in EXCLUDE_TERMS)


def make_job(title, company, location, url, remote=False):
    title = clean(title)
    company = clean(company) or "Unknown"
    location = clean(location) or "Morocco / Casablanca"
    return {
        "id": job_id(url, title),
        "date_detection": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "statut": "NEW", "role_cible": title, "intitule": title, "entreprise": company,
        "lieu": location, "remote": bool(remote), "source": "LinkedIn",
        "lien": url, "company_site": "", "emails_rh": "", "deep_status": "PENDING",
        "fit_score": "", "fit_reasons": "", "salary": "", "description": "",
    }


def parse_linkedin_search(html):
    soup = BeautifulSoup(html, "html.parser")
    jobs, seen = [], set()
    for a in soup.select('a[href*="/jobs/view/"]'):
        href = a.get("href", "").split("?")[0]
        m = re.search(r"/jobs/view/(?:[^/]+-)?(\d+)", href)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        card = a.find_parent(class_=re.compile("base-card|job-search-card|result-card")) or a.parent
        title = clean(a.get_text(" ", strip=True))
        company = location = ""
        if card:
            c = card.select_one(".base-search-card__subtitle, .hidden-nested-link")
            l = card.select_one(".job-search-card__location")
            company = clean(c.get_text(" ", strip=True) if c else "")
            location = clean(l.get_text(" ", strip=True) if l else "")
        if title and is_target(title):
            jobs.append(make_job(title, company, location, urljoin("https://www.linkedin.com", href), remote=True))
    return jobs


def parse_ddg_linkedin(html, fallback_role, fallback_geo):
    """Extract publicly indexed LinkedIn job URLs from DuckDuckGo result pages."""
    soup = BeautifulSoup(html, "html.parser")
    jobs, seen = [], set()
    for result in soup.select(".result"):
        a = result.select_one("a.result__a")
        if not a:
            continue
        href = a.get("href", "")
        # DDG may wrap the destination in a redirect URL.
        match = re.search(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/jobs/view/[^&\s]+", href, re.I)
        if not match:
            match = re.search(r"https?://(?:www\.)?linkedin\.com/jobs/view/[^\s]+", str(result), re.I)
        if not match:
            continue
        url = match.group(0).rstrip("&\"'>)")
        url = url.split("?")[0]
        if url in seen:
            continue
        seen.add(url)
        title_text = clean(a.get_text(" ", strip=True))
        title = re.sub(r"\s*\|\s*LinkedIn\s*$", "", title_text, flags=re.I).strip()
        # Search-result titles are normally "Role - Company | LinkedIn".
        company = ""
        if " - " in title:
            parts = title.split(" - ", 1)
            title, company = parts[0].strip(), parts[1].strip()
        if not title or not is_target(title):
            continue
        snippet = clean(result.get_text(" ", strip=True))
        location = fallback_geo
        for marker in ["Casablanca", "Morocco", "Maroc"]:
            if marker.lower() in snippet.lower():
                location = "Casablanca, Morocco" if marker.lower() == "casablanca" else "Morocco"
                break
        remote = "remote" in (title + " " + snippet).lower()
        jobs.append(make_job(title, company, location, url, remote=remote))
    return jobs


def linkedin_direct(query, max_pages=2):
    jobs = []
    for page in range(max_pages):
        start = page * 25
        # f_WT=2 = remote; this is used for the Morocco-remote searches.
        url = f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(query)}&f_WT=2&location=Morocco&start={start}"
        print(f"[LinkedIn direct] {query} page={page+1}")
        html = fetch(url)
        if not html:
            print("  [!] blocked/unavailable")
            break
        found = parse_linkedin_search(html)
        jobs.extend(found)
        print(f"  +{len(found)}")
        if len(found) < 5:
            break
        time.sleep(random.uniform(.8, 1.5))
    return jobs


def linkedin_indexed(role, geo):
    q = quote_plus(f'site:linkedin.com/jobs/view/ "{role}" "{geo}"')
    url = f"https://html.duckduckgo.com/html/?q={q}"
    print(f"[LinkedIn indexed] {role} — {geo}")
    html = fetch(url, timeout=15, retries=1)
    if not html:
        return []
    return parse_ddg_linkedin(html, role, geo)


def scrape():
    all_jobs, seen = [], set()
    # First attempt direct LinkedIn for remote-in-Morocco. If blocked/empty, use indexed LinkedIn.
    for role in ROLE_QUERIES:
        direct = linkedin_direct(role, max_pages=1)
        candidates = direct or linkedin_indexed(role, "Morocco remote")
        for j in candidates:
            if j["id"] not in seen and is_target(j["intitule"]):
                seen.add(j["id"]); all_jobs.append(j)
        time.sleep(.2)

    # Casablanca: indexed LinkedIn search covers onsite/hybrid/remote and avoids assuming f_WT=2.
    for role in ROLE_QUERIES:
        for geo in ["Casablanca", "Casablanca Morocco"]:
            for j in linkedin_indexed(role, geo):
                if j["id"] in seen or not is_target(j["intitule"]):
                    continue
                # Keep only jobs whose indexed text points to Casablanca/Morocco or remote.
                txt = (j["intitule"] + " " + j["lieu"]).lower()
                if any(k in txt for k in ["casablanca", "morocco", "maroc", "remote"]):
                    seen.add(j["id"]); all_jobs.append(j)
            time.sleep(.2)
    return all_jobs


def post(payload):
    if not WEBHOOK:
        raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    payload = dict(payload)
    payload["sheet"] = SHEET_NAME
    r = requests.post(WEBHOOK, json=payload, timeout=30)
    r.raise_for_status()
    text = r.text[:500]
    print(f"[Sheet] HTTP {r.status_code}: {text}
")
    if "application/json" in r.headers.get("content-type", "").lower():
        try:
            return r.json()
        except ValueError:
            pass
    return {"status": "unknown", "raw": text}


def pending(limit=100):
    if not WEBHOOK:
        raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    r = requests.post(WEBHOOK, json={"mode": "pending", "sheet": SHEET_NAME, "limit": limit}, timeout=30)
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError as exc:
        raise RuntimeError(
            "Apps Script did not return JSON for pending jobs. "
            f"HTTP={r.status_code}, Content-Type={r.headers.get('content-type')}, "
            f"Body={r.text[:300]!r}. Redeploy the Apps Script Web App and use its /exec URL."
        ) from exc
    if data.get("status") != "success":
        raise RuntimeError(f"Apps Script pending error: {data}")
    return data.get("jobs", [])


def discover_company_site(company):
    if not company or company == "Unknown":
        return None
    q = quote_plus(f'"{company}" official website careers jobs')
    html = fetch(f"https://html.duckduckgo.com/html/?q={q}", timeout=15, retries=1)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    blocked = {"linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "indeed.com", "glassdoor.com", "crunchbase.com", "wikipedia.org", "duckduckgo.com"}
    for a in soup.select("a.result__a"):
        href = a.get("href", "")
        if not href.startswith("http"):
            continue
        host = urlparse(href).netloc.lower().replace("www.", "")
        if host and not any(x in host for x in blocked):
            return f"https://{host}"
    return None

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
EMAIL_BAD = {"example.com", "sentry.io", "schema.org", "google.com", "facebook.com", "linkedin.com"}
EMAIL_SKIP_PREFIX = ("noreply@", "no-reply@", "privacy@", "security@", "support@", "mailer-daemon@")
DEEP_PATHS = ["", "/contact", "/contact-us", "/careers", "/jobs", "/about", "/imprint", "/impressum", "/en/contact", "/en/careers"]


def extract_emails(html):
    text = re.sub(r"\s*(?:\[at\]|\(at\)|\{at\})\s*", "@", html, flags=re.I)
    text = re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*", ".", text, flags=re.I)
    soup = BeautifulSoup(text, "html.parser")
    found = set(EMAIL_RE.findall(text))
    for a in soup.select('a[href^="mailto:"]'):
        found.add(a.get("href", "")[7:].split("?")[0])
    out = set()
    for e in found:
        e = e.lower().strip(" .;,<>\"'")
        domain = e.split("@")[-1]
        if "@" in e and domain not in EMAIL_BAD and not e.startswith(EMAIL_SKIP_PREFIX):
            out.add(e)
    return out


def deep_search_job(job):
    company = job.get("entreprise", "")
    site = job.get("company_site") or discover_company_site(company)
    if not site:
        return {"id": job["id"], "company_site": "", "emails_rh": "", "deep_status": "NO_SITE"}
    parsed = urlparse(site)
    base = f"{parsed.scheme}://{parsed.netloc}"
    emails, pages_seen = set(), []
    for path in DEEP_PATHS:
        html = fetch(base + path, timeout=10, retries=1)
        if not html:
            continue
        pages_seen.append(base + path)
        emails |= extract_emails(html)
    ranked = sorted(emails, key=lambda e: (0 if any(k in e for k in ["career", "careers", "recruit", "recruiting", "hr", "talent", "jobs", "hiring"]) else 1, len(e)))
    return {
        "id": job["id"], "company_site": site, "emails_rh": " / ".join(ranked[:3]),
        "deep_status": "DONE" if pages_seen else "SITE_FOUND_NO_PAGES"
    }


def deep_run(limit=100):
    jobs = pending(limit)
    updates = []
    for i, job in enumerate(jobs, 1):
        print(f"[DEEP] {i}/{len(jobs)} {job.get('entreprise')} — {job.get('intitule')}")
        try:
            updates.append(deep_search_job(job))
        except Exception as exc:
            updates.append({"id": job.get("id"), "deep_status": "ERROR", "deep_error": str(exc)[:200]})
        time.sleep(random.uniform(.4, .9))
    if updates:
        post({"mode": "enrich", "updates": updates})


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["scrape", "deep"], default="scrape")
    p.add_argument("--deep-limit", type=int, default=int(os.getenv("DEEP_LIMIT", "100")))
    args = p.parse_args()
    if args.mode == "scrape":
        jobs = scrape()
        if jobs:
            post({"mode": "jobs", "jobs": jobs})
        print(f"DONE: {len(jobs)} Morocco/Casablanca LinkedIn jobs discovered and sent immediately to Sheet")
    else:
        deep_run(args.deep_limit)


if __name__ == "__main__":
    main()
