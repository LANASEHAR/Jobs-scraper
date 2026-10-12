"""High-coverage public company email discovery.
Collects any genuine publicly published company email, not only HR/recruitment.
Never guesses addresses, bypasses access controls, or submits forms.
"""
import json, os, random, re, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
TIMEOUT = 8
WORKERS = max(4, min(12, int(os.getenv("EMAIL_WORKERS", "8"))))
MAX_SITE_PAGES = max(10, min(40, int(os.getenv("EMAIL_MAX_SITE_PAGES", "25"))))
MAX_COMPANIES_PER_RUN = max(25, min(200, int(os.getenv("EMAIL_MAX_COMPANIES_PER_RUN", "100"))))
PENDING_SCAN_PER_SHEET = max(250, min(5000, int(os.getenv("EMAIL_PENDING_SCAN_PER_SHEET", "1000"))))
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}", re.I)
BAD_DOMAINS = {"example.com", "sentry.io", "schema.org", "google.com", "facebook.com", "linkedin.com"}
BLOCKED_HOSTS = {
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "indeed.com", "glassdoor.com", "crunchbase.com", "wikipedia.org",
    "duckduckgo.com", "google.com", "bing.com", "youtube.com"
}
PLACEHOLDERS = {"unknown", "indeed employer", "linkedin employer", "company", "employer"}
SEARCH_CACHE = {}
SEARCH_CACHE_LOCK = threading.Lock()
PATHS = [
    "", "/contact", "/contact-us", "/contactus", "/about", "/about-us", "/team",
    "/our-team", "/company", "/offices", "/locations", "/careers", "/career",
    "/jobs", "/join-us", "/work-with-us", "/recruitment", "/hr",
    "/human-resources", "/sales", "/support", "/customer-support", "/partners",
    "/partnerships", "/press", "/media", "/newsroom", "/legal", "/privacy",
    "/imprint", "/impressum", "/kontakt", "/karriere", "/bewerbung",
    "/en/contact", "/en/careers", "/fr/contact", "/fr/carriere",
    "/fr/recrutement", "/de/kontakt", "/de/karriere"
]
_thread = threading.local()

def session():
    if not hasattr(_thread, "session"):
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,de;q=0.7"
        })
        _thread.session = s
    return _thread.session

def clean(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()

def valid_email(e):
    e = clean(e).lower().strip(" .;,<>\"'")
    if not EMAIL_RE.fullmatch(e):
        return False
    local, domain = e.split("@", 1)
    if domain in BAD_DOMAINS:
        return False
    if local in {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster"}:
        return False
    if ".." in e or domain.startswith(".") or domain.endswith("."):
        return False
    return True

def emails_from(html):
    text = re.sub(r"\s*(?:\[at\]|\(at\)|\{at\})\s*", "@", html or "", flags=re.I)
    text = re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*", ".", text, flags=re.I)
    soup = BeautifulSoup(text, "html.parser")
    found = set(EMAIL_RE.findall(text))
    for a in soup.select('a[href^="mailto:"]'):
        found.add(a.get("href", "")[7:].split("?", 1)[0])
    return {e.lower().strip(" .;,<>\"'") for e in found if valid_email(e)}

def host(url):
    try:
        h = urlparse(url).netloc.lower().split(":")[0]
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""

def blocked(url):
    h = host(url)
    return not h or any(h == x or h.endswith("." + x) for x in BLOCKED_HOSTS)

def get(url):
    try:
        r = session().get(url, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code == 200 and r.text:
            return r.text, r.url
    except requests.RequestException:
        pass
    return "", url

def search(q, limit=12):
    """Search public indexes with graceful fallbacks and per-run query caching."""
    key = (clean(q), int(limit))
    with SEARCH_CACHE_LOCK:
        cached = SEARCH_CACHE.get(key)
    if cached is not None:
        return list(cached)

    out, seen = [], set()
    engines = [
        ("Bing RSS", "https://www.bing.com/search?format=rss&q=" + quote_plus(q)),
        ("DDG Lite", "https://lite.duckduckgo.com/lite/?q=" + quote_plus(q)),
        ("Bing HTML", "https://www.bing.com/search?q=" + quote_plus(q)),
        ("Google", "https://www.google.com/search?q=" + quote_plus(q)),
    ]
    for engine, url in engines:
        if len(out) >= limit:
            break
        try:
            r = session().get(url, timeout=9, allow_redirects=True)
            if r.status_code != 200 or not r.text:
                continue
            soup = BeautifulSoup(r.text, "xml" if engine == "Bing RSS" else "html.parser")
            links = []
            if engine == "Bing RSS":
                for item in soup.find_all("item"):
                    title = clean(item.find("title").get_text(" ", strip=True) if item.find("title") else "")
                    link = clean(item.find("link").get_text(strip=True) if item.find("link") else "")
                    if title and link:
                        links.append((title, link))
            elif engine == "Bing HTML":
                links = [(clean(a.get_text(" ", strip=True)), a.get("href", ""))
                         for a in soup.select("li.b_algo h2 a[href]")]
            elif engine == "DDG Lite":
                links = [(clean(a.get_text(" ", strip=True)), a.get("href", ""))
                         for a in soup.select("a.result-link[href], a.result__a[href]")]
                if not links:
                    links = [(clean(a.get_text(" ", strip=True)), a.get("href", ""))
                             for a in soup.select("a[href]")]
            else:
                for a in soup.select("a[href]"):
                    href = a.get("href", "")
                    if href.startswith("/url?"):
                        href = parse_qs(urlparse(href).query).get("q", [""])[0]
                    links.append((clean(a.get_text(" ", strip=True)), href))

            for title, u in links:
                u = clean(u).split("#", 1)[0]
                if u.startswith("//"):
                    u = "https:" + u
                if not u.startswith(("http://", "https://")) or blocked(u) or u in seen:
                    continue
                if any(x in urlparse(u).netloc.lower() for x in ("google.com", "bing.com", "duckduckgo.com")):
                    continue
                seen.add(u)
                out.append((title or host(u), u))
                if len(out) >= limit:
                    break
        except requests.RequestException as exc:
            print(f"[SEARCH WARNING] {engine} {type(exc).__name__}", flush=True)

    result = out[:limit]
    with SEARCH_CACHE_LOCK:
        SEARCH_CACHE[key] = list(result)
    return result

def reliable_company(v):
    v = clean(v)
    return bool(v) and v.lower() not in PLACEHOLDERS and len(v) >= 2

def extract_company(html):
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            obj = stack.pop()
            if isinstance(obj, list):
                stack.extend(obj)
            elif isinstance(obj, dict):
                if "jobposting" in str(obj.get("@type", "")).lower():
                    org = obj.get("hiringOrganization")
                    if isinstance(org, dict) and reliable_company(org.get("name")):
                        return clean(org["name"])
                stack.extend(v for v in obj.values() if isinstance(v, (dict, list)))
    for meta in soup.select('meta[property="og:site_name"],meta[name="application-name"]'):
        val = clean(meta.get("content", ""))
        if reliable_company(val):
            return val
    for sel in ('[class*="company"]', '[class*="employer"]', '[class*="entreprise"]',
                '[class*="organization"]', '[data-company]'):
        node = soup.select_one(sel)
        if node:
            val = clean(node.get("data-company") or node.get_text(" ", strip=True))
            if reliable_company(val) and len(val) < 160:
                return val
    return ""

def recover_company(job):
    current = clean(job.get("Entreprise", job.get("entreprise", "")))
    if reliable_company(current):
        return current
    url = clean(job.get("Lien", job.get("lien", "")))
    if url:
        html, _ = get(url)
        return extract_company(html)
    return ""

def official_site(company):
    if not reliable_company(company):
        return ""
    queries = [
        f'"{company}" official website',
        f'"{company}" contact',
        f'"{company}" careers',
        f'"{company}" email',
        f'"{company}" contact details'
    ]
    for q in queries:
        for _, u in search(q, 12):
            h = host(u)
            if blocked(u) or any(x in h for x in (
                "careerjet", "jobboard", "jooble", "talent.com", "simplyhired",
                "glassdoor", "indeed", "linkedin", "facebook"
            )):
                continue
            p = urlparse(u)
            if p.scheme in ("http", "https") and h:
                return p.scheme + "://" + h + "/"
    return ""

def discover_site_emails(site, company):
    """Collect public emails from search-indexed pages and a broad same-site crawl."""
    if not site:
        return set()
    site_host = host(site)
    base = site.rstrip("/") + "/"
    found, seen, queued = set(), set(), set()
    queue = [urljoin(base, p.lstrip("/")) for p in PATHS]

    # Search all public contact channels, not just recruitment addresses.
    queries = [
        f'"{company}" email', f'"{company}" contact email',
        f'"{company}" "@"', f'"{company}" mailto',
        f'site:{site_host} email', f'site:{site_host} contact',
        f'site:{site_host} "@{site_host}"'
    ]
    for q in queries:
        # Keep a few high-ranking results per query to avoid unbounded crawling.
        for _, u in search(q, 8)[:3]:
            # Search results can include third-party pages about the company;
            # inspect them for publicly published emails, but never treat them as the official site.
            html, _ = get(u)
            if html:
                found |= emails_from(html)

    while queue and len(seen) < MAX_SITE_PAGES:
        url = queue.pop(0).split("#", 1)[0]
        if url in seen or url in queued or host(url) != site_host:
            continue
        queued.add(url)
        seen.add(url)
        html, final = get(url)
        if not html:
            continue
        found |= emails_from(html)
        soup = BeautifulSoup(html, "html.parser")
        links = []
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not href or href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            target = urljoin(final, href).split("#", 1)[0]
            if host(target) != site_host or target in seen or target in queued:
                continue
            label = clean(a.get_text(" ", strip=True)).lower()
            path = urlparse(target).path.lower()
            priority = 0 if any(k in label + " " + path for k in (
                "contact", "about", "team", "company", "office", "email", "career",
                "job", "recruit", "sales", "support", "press", "media", "legal",
                "partner", "business", "imprint", "impressum", "privacy", "location"
            )) else 1
            links.append((priority, target))
        links.sort(key=lambda x: (x[0], len(x[1])))
        for _, target in links:
            if len(queue) + len(seen) >= MAX_SITE_PAGES * 2:
                break
            if target not in queued and target not in seen:
                queue.append(target)
                queued.add(target)
    return found

def enrich(job):
    company = recover_company(job)
    site = clean(job.get("Company Site", job.get("company_site", "")))
    if not site:
        site = official_site(company)
    found = discover_site_emails(site, company)
    # Broad fallback search also works when no official website can be identified.
    if reliable_company(company):
        for q in (f'"{company}" email', f'"{company}" contact email',
                  f'"{company}" "@"', f'"{company}" mailto'):
            for _, u in search(q, 8)[:3]:
                html, _ = get(u)
                if html:
                    found |= emails_from(html)
    good = sorted(found)
    return {
        "id": clean(job.get("ID", job.get("id", ""))),
        "sheet": clean(job.get("_sheet", job.get("sheet", ""))),
        "entreprise": company,
        "company_site": site,
        "emails_rh": " / ".join(good),
        "deep_status": "DONE" if (site or company) else "NO_COMPANY",
        "email_status": "FOUND" if good else ("PENDING" if company else "NO_COMPANY"),
        "email_source": "Public company website / public web" if good else ""
    }

def webhook(payload):
    if not WEBHOOK:
        raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    last_error = None
    for attempt in range(3):
        try:
            r = session().post(WEBHOOK, json=payload, timeout=45,
                               headers={"Content-Type": "application/json"})
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "success":
                raise RuntimeError(f"Unexpected Google Sheets webhook response: {data!r}")
            return data
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Google Sheets webhook failed after 3 attempts: {last_error}")

def main():
    sheets = ("Worldwide Remote", "Morocco Remote", "Casablanca Onsite", "Casablanca Spontaneous")
    by_sheet, seen = {}, set()
    # Bound each run and interleave sheets so one large board cannot starve others.
    for sheet in sheets:
        data = webhook({"mode": "pending", "limit": PENDING_SCAN_PER_SHEET, "sheet": sheet})
        rows = []
        for job in data.get("jobs", []):
            key = (sheet, clean(job.get("ID", job.get("id", ""))))
            if key in seen:
                continue
            seen.add(key)
            job["_sheet"] = sheet
            rows.append(job)
        # Randomize each bounded pending slice so repeated runs do not starve later rows.
        random.shuffle(rows)
        by_sheet[sheet] = rows
    all_jobs = []
    cursor = 0
    while len(all_jobs) < MAX_COMPANIES_PER_RUN:
        added = False
        for sheet in sheets:
            rows = by_sheet.get(sheet, [])
            if cursor < len(rows) and len(all_jobs) < MAX_COMPANIES_PER_RUN:
                all_jobs.append(rows[cursor])
                added = True
        if not added:
            break
        cursor += 1
    print(f"[EMAIL ENRICH] pending={len(all_jobs)} cap={MAX_COMPANIES_PER_RUN} workers={WORKERS} pages_per_site<={MAX_SITE_PAGES}", flush=True)
    updates, found_count, error_count = [], 0, 0

    def process(index, job):
        try:
            return index, enrich(job), ""
        except Exception as e:
            return index, None, str(e)[:250]

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(process, i, job) for i, job in enumerate(all_jobs, 1)]
        for future in as_completed(futures):
            idx, update, error = future.result()
            if error:
                error_count += 1
                job = all_jobs[idx - 1]
                failed = {
                    "id": clean(job.get("ID", job.get("id", ""))),
                    "sheet": clean(job.get("_sheet", job.get("sheet", ""))),
                    "deep_status": "PENDING",
                    "email_status": "PENDING",
                    "email_source": clean(error)[:180]
                }
                if failed["id"]:
                    updates.append(failed)
                print(f"[EMAIL ERROR] row={idx}: {error}", flush=True)
                if len(updates) >= 25:
                    webhook({"mode": "enrich", "updates": updates})
                    updates = []
                continue
            if not update["id"]:
                continue
            updates.append(update)
            if update["emails_rh"]:
                found_count += 1
                print(f"[EMAIL FOUND] {update['entreprise']} -> {update['emails_rh']}", flush=True)
            if len(updates) >= 25:
                try:
                    webhook({"mode": "enrich", "updates": updates})
                    print(f"[SHEETS] persisted batch={len(updates)}", flush=True)
                except Exception as e:
                    print(f"[SHEETS WRITE ERROR] {e}; retrying once", flush=True)
                    time.sleep(2)
                    webhook({"mode": "enrich", "updates": updates})
                updates = []
    if updates:
        webhook({"mode": "enrich", "updates": updates})
    print(f"[EMAIL ENRICH DONE] processed={len(all_jobs)} companies_with_emails={found_count} errors={error_count}", flush=True)

if __name__ == "__main__":
    main()
