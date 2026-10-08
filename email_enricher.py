"""Second-pass company/email enrichment for the remote jobs scraper.

This module is deliberately company-first:
1. Recover the real employer from the job detail page when the board result
   contains no reliable company name.
2. Find the employer's official website.
3. Search the public web for published company email addresses.
4. Crawl contact/careers/legal pages and mailto links.
5. Never invent an email or company name.
"""
import json, os, re, time
from urllib.parse import quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
TIMEOUT = 12
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}", re.I)
BAD_DOMAINS = {"example.com","sentry.io","schema.org","google.com","facebook.com","linkedin.com"}
BLOCKED_HOSTS = {
    "linkedin.com","facebook.com","instagram.com","twitter.com","x.com","indeed.com",
    "glassdoor.com","crunchbase.com","wikipedia.org","duckduckgo.com","google.com",
    "bing.com","youtube.com","rekrute.com","emploi.ma","bayt.com","novojob.com"
}
PLACEHOLDERS = {"unknown","indeed employer","linkedin employer","company","employer"}

S = requests.Session()
S.headers.update({
    "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
    "Accept-Language":"en-US,en;q=0.9,fr;q=0.8,de;q=0.7"
})

PATHS = [
    "", "/contact", "/contact-us", "/contactus", "/careers", "/career", "/jobs",
    "/join-us", "/work-with-us", "/recruitment", "/hr", "/human-resources",
    "/about", "/en/contact", "/en/careers", "/fr/contact", "/fr/carriere",
    "/fr/recrutement", "/de/kontakt", "/de/karriere", "/legal", "/imprint",
    "/impressum", "/privacy", "/team"
]

def clean(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()

def valid_email(e):
    e = clean(e).lower()
    if not EMAIL_RE.fullmatch(e): return False
    domain = e.split("@",1)[1]
    return domain not in BAD_DOMAINS and not e.startswith(("noreply@","no-reply@","privacy@","security@"))

def emails_from(html):
    text = re.sub(r"\s*(?:\[at\]|\(at\)|\{at\})\s*", "@", html or "", flags=re.I)
    text = re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*", ".", text, flags=re.I)
    soup = BeautifulSoup(text, "html.parser")
    found = set(EMAIL_RE.findall(text))
    for a in soup.select('a[href^="mailto:"]'):
        found.add(a.get("href","")[7:].split("?",1)[0])
    return {e.lower().strip(" .;,<>\"'") for e in found if valid_email(e)}

def host(url):
    try:
        h = urlparse(url).netloc.lower().split(":")[0]
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""

def blocked(h):
    h = host(h)
    return not h or any(h == x or h.endswith("."+x) for x in BLOCKED_HOSTS)

def get(url):
    try:
        r = S.get(url, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code == 200 and r.text:
            return r.text, r.url
    except requests.RequestException:
        pass
    return "", url

def search(q, limit=12):
    out=[]; seen=set()
    for engine in (
        "https://www.bing.com/search?q="+quote_plus(q),
        "https://html.duckduckgo.com/html/?q="+quote_plus(q)
    ):
        try:
            r=S.get(engine, timeout=8)
            if r.status_code != 200: continue
            soup=BeautifulSoup(r.text,"html.parser")
            selectors = ['li.b_algo h2 a[href]','a.result__a[href]','a.result-link[href]']
            links=[]
            for sel in selectors:
                links.extend(soup.select(sel))
            for a in links:
                u=a.get("href","")
                if not u.startswith("http"): continue
                u=u.split("#",1)[0]
                if u in seen: continue
                seen.add(u); out.append((clean(a.get_text(" ",strip=True)),u))
                if len(out)>=limit: return out
        except requests.RequestException:
            continue
    return out

def reliable_company(v):
    v=clean(v)
    return bool(v) and v.lower() not in PLACEHOLDERS and len(v) >= 2

def extract_company(html):
    if not html: return ""
    soup=BeautifulSoup(html,"html.parser")
    # Prefer structured data because it is less likely to confuse navigation text
    # with an employer name.
    for script in soup.select('script[type="application/ld+json"]'):
        raw=script.string or script.get_text()
        try:
            data=json.loads(raw)
        except Exception:
            continue
        items=data if isinstance(data,list) else [data]
        for obj in items:
            if not isinstance(obj,dict): continue
            typ=str(obj.get("@type","")).lower()
            if "jobposting" in typ:
                org=obj.get("hiringOrganization")
                if isinstance(org,dict) and reliable_company(org.get("name")):
                    return clean(org["name"])
    for meta in soup.select('meta[property="og:site_name"],meta[name="application-name"]'):
        val=clean(meta.get("content",""))
        if reliable_company(val): return val
    for sel in (
        '[class*="company"]','[class*="employer"]','[class*="entreprise"]',
        '[class*="recruiter"]','[class*="organization"]','[data-company]'
    ):
        node=soup.select_one(sel)
        if node:
            val=clean(node.get("data-company") or node.get_text(" ",strip=True))
            if reliable_company(val) and len(val)<160: return val
    return ""

def recover_company(job):
    current=clean(job.get("Entreprise",""))
    if reliable_company(current): return current
    url=clean(job.get("Lien",""))
    if not url: return ""
    html,_=get(url)
    return extract_company(html)

def official_site(company):
    if not reliable_company(company): return ""
    queries=[
        f'"{company}" official website',
        f'"{company}" contact',
        f'"{company}" careers',
        f'"{company}" recruitment email'
    ]
    for q in queries:
        for title,u in search(q,12):
            h=host(u)
            if blocked(u): continue
            if any(x in h for x in ("careerjet","jobboard","jooble","talent.com","simplyhired","glassdoor","indeed")): continue
            # Reject obvious article/search pages and keep the canonical origin.
            p=urlparse(u)
            if p.scheme in ("http","https") and h:
                return p.scheme+"://"+h+"/"
    return ""

def discover_site_emails(site, company):
    if not site: return set()
    base=site.rstrip("/")
    found=set()
    # Search engines often expose emails that are not linked from the homepage.
    for q in (
        f'"{company}" "@{host(site)}"',
        f'site:{host(site)} "@{host(site)}"',
        f'site:{host(site)} (email OR contact OR careers OR recruitment)'
    ):
        for _,u in search(q,12):
            if blocked(u): continue
            html,_=get(u)
            found |= emails_from(html)
    # Crawl likely contact/recruitment pages and discover additional internal links.
    for path in PATHS:
        html,final=get(urljoin(base+"/",path))
        if not html: continue
        found |= emails_from(html)
        soup=BeautifulSoup(html,"html.parser")
        for a in soup.select("a[href]"):
            label=clean(a.get_text(" ",strip=True)).lower()
            href=a.get("href","")
            if not href: continue
            if any(k in (label+" "+href.lower()) for k in ("contact","career","recruit","human","hr","jobs","impressum","kontakt")):
                target=urljoin(final,href)
                if host(target)==host(site):
                    h,_=get(target)
                    found |= emails_from(h)
    return found

def enrich(job):
    company=recover_company(job)
    site=clean(job.get("Company Site","")) or official_site(company)
    found=discover_site_emails(site,company)
    # If the official site search failed, do one final company-name email search
    # without fabricating a domain.
    if not found and reliable_company(company):
        for q in (
            f'"{company}" "@" email',
            f'"{company}" recruitment email',
            f'"{company}" HR email'
        ):
            for _,u in search(q,10):
                h,_=get(u)
                found |= emails_from(h)
    good=sorted(found)
    return {
        "id":clean(job.get("ID","")),
        "sheet":clean(job.get("_sheet","")),
        "entreprise":company,
        "company_site":site,
        "emails_rh":" / ".join(good),
        "deep_status":"DONE" if (site or company) else "NO_COMPANY",
        "email_status":"FOUND" if good else "NO_EMAIL",
        "email_source":"Public company website / public web" if good else ""
    }

def webhook(payload):
    if not WEBHOOK: raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    r=S.post(WEBHOOK,json=payload,timeout=90,headers={"Content-Type":"application/json"})
    r.raise_for_status()
    return r.json()

def main():
    data=webhook({"mode":"pending","limit":5000,"sheet":"ALL"})
    jobs=data.get("jobs",[])
    print(f"[EMAIL ENRICH] pending={len(jobs)}",flush=True)
    updates=[]
    for idx,job in enumerate(jobs,1):
        try:
            u=enrich(job)
            if u["id"]:
                updates.append(u)
                if u["emails_rh"]:
                    print(f"[EMAIL FOUND] {u['entreprise']} -> {u['emails_rh']}",flush=True)
        except Exception as e:
            print(f"[EMAIL ERROR] {idx}: {e}",flush=True)
        if len(updates)>=25:
            webhook({"mode":"enrich","updates":updates}); updates=[]
    if updates: webhook({"mode":"enrich","updates":updates})
    print("[EMAIL ENRICH] complete",flush=True)

if __name__=="__main__":
    main()
