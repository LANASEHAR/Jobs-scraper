"""Company-first discovery independent of active vacancies.
Stores verified companies even when no public email is found; those rows remain pending
for the separate email_enricher.py pass. Never guesses email addresses or company names.
"""
import hashlib
import os
import re
import time
from urllib.parse import quote_plus, urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
MAX_COMPANIES = max(20, min(200, int(os.getenv("COMPANY_DISCOVERY_MAX", "100"))))
TIMEOUT = 12
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}", re.I)
BAD_DOMAINS = {"example.com", "sentry.io", "schema.org", "google.com", "facebook.com"}
BLOCKED_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "indeed.com", "glassdoor.com", "crunchbase.com", "wikipedia.org", "youtube.com",
    "bing.com", "duckduckgo.com", "google.com", "search.yahoo.com"
}
PATHS = [
    "", "contact", "contact-us", "contactus", "about", "about-us", "company",
    "team", "our-team", "people", "leadership", "offices", "locations", "sales",
    "support", "customer-support", "partners", "press", "media", "careers",
    "career", "jobs", "recruitment", "hr", "human-resources", "legal", "imprint",
    "impressum", "kontakt", "karriere", "fr/contact", "fr/a-propos", "fr/recrutement",
    "en/contact", "en/careers", "de/kontakt", "de/karriere"
]
QUERIES = [
    "SaaS companies official website contact",
    "customer success software companies official website",
    "travel technology companies official website contact",
    "hospitality technology companies official website",
    "ecommerce platforms companies contact",
    "B2B services companies Morocco official website",
    "multinational companies Casablanca official website",
    "shared services companies Morocco contact",
    "business process outsourcing companies Morocco",
    "digital agencies Morocco official website",
    "French SaaS companies official website contact",
    "German Mittelstand companies official website",
    "European remote-first companies official website",
    "CRM software companies contact",
    "payment technology companies official website",
    "HR technology companies official website",
    "hotel groups international official website",
    "airline travel technology companies official website",
    "procurement software companies official website",
    "business services companies France official website",
    "business services companies Spain official website",
    "business services companies Portugal official website",
    "customer support platforms official website",
    "marketplace companies official website contact",
    "e-commerce brands Morocco official website"
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,de;q=0.7"
}
BLOCKED_RUNTIME = set()


def host(url):
    value = urlparse(url).netloc.lower().split(":")[0]
    return value[4:] if value.startswith("www.") else value


def is_blocked(url):
    h = host(url)
    return not h or h in BLOCKED_RUNTIME or any(h == d or h.endswith("." + d) for d in BLOCKED_DOMAINS)


def get(url):
    if is_blocked(url):
        return "", url
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if response.status_code == 200 and "text/html" in response.headers.get("content-type", "text/html"):
            return response.text, response.url
        if response.status_code in (401, 403, 429):
            BLOCKED_RUNTIME.add(host(url))
            print(f"[SITE BLOCKED] HTTP {response.status_code} {host(url)}", flush=True)
        elif response.status_code not in (404, 410):
            print(f"[SITE WARNING] HTTP {response.status_code} {url}", flush=True)
    except requests.RequestException as exc:
        print(f"[SITE WARNING] {type(exc).__name__} {url}", flush=True)
    return "", url


def search(query, limit=8):
    results, seen = [], set()
    engines = [
        ("Bing RSS", "https://www.bing.com/search?format=rss&q=" + quote_plus(query), True),
        ("DDG Lite", "https://lite.duckduckgo.com/lite/?q=" + quote_plus(query), False),
        ("Bing HTML", "https://www.bing.com/search?q=" + quote_plus(query), False),
        ("Yahoo", "https://search.yahoo.com/search?p=" + quote_plus(query), False),
    ]
    for engine, endpoint, rss in engines:
        try:
            response = requests.get(endpoint, headers=HEADERS, timeout=TIMEOUT)
            if response.status_code != 200 or not response.text:
                print(f"[SEARCH WARNING] {engine} HTTP {response.status_code}", flush=True)
                continue
            soup = BeautifulSoup(response.text, "xml" if rss else "html.parser")
            links = []
            if rss:
                for item in soup.find_all("item"):
                    title = item.find("title")
                    link = item.find("link")
                    if title and link:
                        links.append((title.get_text(" ", strip=True), link.get_text(strip=True)))
            else:
                selectors = ["a.result-link[href]", "a.result__a[href]", "li.b_algo h2 a[href]", "h2 a[href]", "a[href]"]
                for selector in selectors:
                    for anchor in soup.select(selector):
                        url = anchor.get("href", "").strip()
                        if url.startswith("//"):
                            url = "https:" + url
                        if url.startswith("/"):
                            params = parse_qs(urlparse(url).query)
                            url = next((params[k][0] for k in ("uddg", "url", "u", "RU") if params.get(k)), "")
                        label = anchor.get_text(" ", strip=True)
                        if label and url.startswith(("http://", "https://")):
                            links.append((label, url))
                    if links:
                        break
            added_here = 0
            for title, url in links:
                url = url.split("#", 1)[0]
                if not url.startswith(("http://", "https://")) or is_blocked(url) or url in seen:
                    continue
                seen.add(url)
                results.append((title, url))
                added_here += 1
                if len(results) >= limit:
                    return results
            print(f"[SEARCH] {engine}: {added_here} usable results", flush=True)
        except requests.RequestException as exc:
            print(f"[SEARCH WARNING] {engine} {type(exc).__name__}", flush=True)
        time.sleep(0.25)
    return results


def extract_name(title, html):
    soup = BeautifulSoup(html, "html.parser")
    for selector in ('meta[property="og:site_name"]', 'meta[name="application-name"]'):
        node = soup.select_one(selector)
        if node and node.get("content", "").strip():
            name = node["content"].strip()
            if 2 <= len(name) <= 120:
                return name
    if soup.title:
        title = soup.title.get_text(" ", strip=True) or title
    name = re.split(r"\s+[|–—]\s+|\s+-\s+", title or "")[0].strip(" -|–—")
    name = re.sub(r"\b(official website|contact us|contact|about us|home page|careers|jobs)\b.*$", "", name, flags=re.I).strip()
    if not 2 <= len(name) <= 100 or name.lower() in {
        "home", "welcome", "contact", "about", "careers", "jobs", "company", "website"
    }:
        return ""
    if any(word in name.lower() for word in ("search results", "login", "sign in", "job listing", "wikipedia")):
        return ""
    return name


def extract_emails(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s*(?:\[at\]|\(at\)|\{at\})\s*", "@", text, flags=re.I)
    text = re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*", ".", text, flags=re.I)
    found = set(EMAIL_RE.findall(text))
    for anchor in soup.select('a[href^="mailto:"]'):
        found.add(anchor.get("href", "")[7:].split("?", 1)[0])
    valid = set()
    for value in found:
        email = value.lower().strip(" .;,<>")
        if not EMAIL_RE.fullmatch(email):
            continue
        local, domain = email.split("@", 1)
        if domain in BAD_DOMAINS or local in {"noreply", "no-reply", "donotreply", "postmaster", "test", "example"}:
            continue
        if re.search(r"\.(?:png|jpe?g|gif|webp|svg|css|js|woff2?)$", local, re.I):
            continue
        valid.add(email)
    return valid


def crawl_site(site, max_pages=22):
    domain = host(site)
    base = site.rstrip("/") + "/"
    queue = [urljoin(base, path) for path in PATHS]
    seen, emails = set(), set()
    accessible_pages = 0
    while queue and len(seen) < max_pages:
        url = queue.pop(0)
        if url in seen or host(url) != domain:
            continue
        seen.add(url)
        html, final_url = get(url)
        if not html:
            continue
        accessible_pages += 1
        emails.update(extract_emails(html))
        soup = BeautifulSoup(html, "html.parser")
        candidates = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "")
            if href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            target = urljoin(final_url, href).split("#", 1)[0]
            if host(target) != domain or target in seen:
                continue
            label = (anchor.get_text(" ", strip=True) + " " + urlparse(target).path).lower()
            priority = 0 if any(w in label for w in (
                "contact", "about", "team", "people", "leadership", "sales", "support",
                "career", "recruit", "hr", "partner", "press", "legal", "imprint"
            )) else 1
            candidates.append((priority, target))
        for _, target in sorted(candidates, key=lambda item: (item[0], len(item[1]))):
            if target not in seen and target not in queue:
                queue.append(target)
            if len(queue) > 60:
                break
    return emails, accessible_pages


def make_record(name, site, emails):
    is_morocco = any(word in (name + " " + site).lower() for word in ("morocco", "maroc", "casablanca", "rabat"))
    sheet = "Casablanca Spontaneous" if is_morocco else "Worldwide Remote"
    title = "Spontaneous application — Customer Success / Account Management / Sales Administration"
    return {
        "id": "company_" + hashlib.sha256(host(site).encode()).hexdigest()[:20],
        "date_detection": time.strftime("%Y-%m-%d"),
        "statut": "NEW", "role_cible": title, "intitule": title, "entreprise": name,
        "lieu": "Casablanca, Morocco" if is_morocco else "International — company prospect",
        "remote": not is_morocco, "source": "Company-First Public Discovery", "lien": site,
        "company_site": site, "emails_rh": " / ".join(sorted(emails)),
        "deep_status": "DONE" if emails else "PENDING", "fit_score": "",
        "fit_reasons": "Company discovered independently of job vacancies",
        "salary": "", "description": "Publicly discoverable company; no active vacancy required.",
        "posted_age": "", "posted_within_24h": "N/A",
        "search_type": "CASABLANCA_SPONTANEOUS" if is_morocco else "REMOTE",
        "email_status": "FOUND" if emails else "PENDING",
        "email_source": "Public company website" if emails else "",
        "spontaneous": "YES", "sheet": sheet
    }


def write_rows(sheet, rows):
    last_error = ""
    for attempt in range(3):
        try:
            response = requests.post(
                WEBHOOK,
                json={"mode": "jobs", "sheet": sheet, "jobs": rows},
                headers={"Content-Type": "application/json"}, timeout=45
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "success":
                return int(data.get("added", 0))
            last_error = "unexpected response: " + str(data)
        except (requests.RequestException, ValueError) as exc:
            last_error = str(exc)
        if attempt < 2:
            time.sleep(2 ** attempt)
    raise RuntimeError("Google Sheets write failed after 3 attempts: " + last_error)


def main():
    if not WEBHOOK:
        raise SystemExit("ERROR: GOOGLE_SHEET_WEBHOOK_URL is missing")
    companies = {}
    accessible_sites = set()
    pages_accessible = 0
    emails_found = 0
    for index, query in enumerate(QUERIES, 1):
        print(f"[QUERY {index}/{len(QUERIES)}] {query}", flush=True)
        for title, result_url in search(query, 10):
            domain = host(result_url)
            if not domain or domain in companies:
                continue
            html, final_url = get(result_url)
            if not html:
                continue
            site_domain = host(final_url)
            if not site_domain or is_blocked(final_url):
                continue
            site = urlparse(final_url).scheme + "://" + site_domain + "/"
            name = extract_name(title, html)
            if not name:
                print(f"[SKIP] No reliable company name for {site}", flush=True)
                continue
            accessible_sites.add(site_domain)
            emails = extract_emails(html)
            crawled_emails, page_count = crawl_site(site)
            emails.update(crawled_emails)
            pages_accessible += page_count
            emails_found += len(emails)
            companies[site_domain] = make_record(name, site, emails)
            print(f"[COMPANY] {name} | site={site_domain} | accessible_pages={page_count} | public_emails={len(emails)}", flush=True)
            if len(companies) >= MAX_COMPANIES:
                break
        if len(companies) >= MAX_COMPANIES:
            break

    grouped = {}
    for row in companies.values():
        grouped.setdefault(row["sheet"], []).append(row)
    rows_added = 0
    for sheet, rows in grouped.items():
        for start in range(0, len(rows), 25):
            rows_added += write_rows(sheet, rows[start:start + 25])
    pending = sum(1 for row in companies.values() if not row["emails_rh"])
    print("[METRICS] companies_discovered=" + str(len(companies))
          + " | official_sites_accessible=" + str(len(accessible_sites))
          + " | pages_accessible=" + str(pages_accessible)
          + " | companies_with_public_emails=" + str(sum(1 for row in companies.values() if row["emails_rh"]))
          + " | public_email_addresses=" + str(emails_found)
          + " | companies_pending_enrichment=" + str(pending)
          + " | sheet_rows_actually_added=" + str(rows_added), flush=True)
    print("[DONE] Company-first discovery finished.", flush=True)


if __name__ == "__main__":
    main()
