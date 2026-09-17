"""Morocco/Casablanca LinkedIn job discovery + separate company-site deep search."""
import argparse, hashlib, os, random, re, time
from urllib.parse import quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
SHEET = "Morocco Jobs"
ROLES = [
    "Customer Success Manager", "Customer Success Specialist", "Customer Success Associate",
    "Account Manager", "Key Account Manager", "Customer Account Manager", "Client Success Manager",
    "Customer Support Specialist", "Customer Experience Specialist", "Sales Executive", "Account Executive",
    "Business Development Representative", "Sales Development Representative", "Inside Sales Representative",
    "B2B Sales Representative", "Operations Coordinator", "Business Operations Specialist",
    "Administrative Coordinator", "Administrative Assistant", "Sales Operations Specialist",
    "Commercial Operations Specialist", "Back Office Specialist", "Project Coordinator",
    "E-commerce Specialist", "Shopify Specialist", "CRM Specialist", "Digital Marketing Specialist",
    "Junior UX UI Designer", "Junior UX Designer", "Junior UI Designer",
]
EXCLUDE = ["senior", "sr.", "lead", "principal", "director", "vp ", "vice president", "head of", "chief", "staff", "architect", "intern", "doctor", "nurse", "software engineer", "software developer", "data scientist", "machine learning", "devops", "lawyer", "accountant", "physician", "warehouse worker", "driver", "technician"]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
s = requests.Session()


def clean(x): return re.sub(r"\s+", " ", str(x or "")).strip()
def target(title): return not any(x in title.lower() for x in EXCLUDE)
def jid(url, title): return "ma_" + hashlib.sha256((url + "|" + title).encode()).hexdigest()[:16]

def fetch(url, timeout=15):
    try:
        r = s.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9,fr;q=0.8"}, timeout=timeout, allow_redirects=True)
        return r.text if r.status_code == 200 else None
    except requests.RequestException:
        return None

def job(title, company, location, url, remote):
    return {"id": jid(url, title), "date_detection": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "role_cible": title, "intitule": title, "entreprise": clean(company) or "Unknown", "lieu": clean(location) or "Morocco / Casablanca", "remote": remote, "source": "LinkedIn", "lien": url, "company_site": "", "emails_rh": "", "deep_status": "PENDING", "fit_score": "", "fit_reasons": "", "salary": "", "description": ""}

def parse_linkedin(html):
    soup = BeautifulSoup(html, "html.parser"); out=[]; seen=set()
    for a in soup.select('a[href*="/jobs/view/"]'):
        href=a.get("href", "").split("?")[0]; m=re.search(r"/jobs/view/(?:[^/]+-)?(\d+)", href)
        if not m or m.group(1) in seen: continue
        seen.add(m.group(1)); card=a.find_parent(class_=re.compile("base-card|job-search-card|result-card")) or a.parent
        title=clean(a.get_text(" ", strip=True)); company=location=""
        if card:
            c=card.select_one(".base-search-card__subtitle, .hidden-nested-link"); l=card.select_one(".job-search-card__location")
            company=clean(c.get_text(" ", strip=True) if c else ""); location=clean(l.get_text(" ", strip=True) if l else "")
        if title and target(title): out.append(job(title, company, location, urljoin("https://www.linkedin.com", href), True))
    return out

def indexed(role, geo):
    q=quote_plus(f'site:linkedin.com/jobs/view/ "{role}" "{geo}"')
    html=fetch(f"https://html.duckduckgo.com/html/?q={q}")
    if not html: return []
    soup=BeautifulSoup(html, "html.parser"); out=[]; seen=set()
    for result in soup.select(".result"):
        a=result.select_one("a.result__a"); raw=str(result)
        if not a: continue
        m=re.search(r"https?://(?:www\.)?linkedin\.com/jobs/view/[^\s\"&<>]+", raw, re.I)
        if not m: continue
        url=m.group(0).rstrip("')>\"").split("?")[0]
        if url in seen: continue
        seen.add(url)
        title=clean(re.sub(r"\s*\|\s*LinkedIn$", "", a.get_text(" ", strip=True), flags=re.I)); company=""
        if " - " in title: title,company=title.split(" - ",1); title=title.strip(); company=company.strip()
        if not title or not target(title): continue
        snippet=clean(result.get_text(" ", strip=True)); low=snippet.lower()
        loc="Casablanca, Morocco" if "casablanca" in low else ("Morocco" if "morocco" in low or "maroc" in low else geo)
        out.append(job(title, company, loc, url, "remote" in (title+" "+snippet).lower()))
    return out

def discover():
    out=[]; seen=set()
    for role in ROLES:
        # Direct LinkedIn first; indexed public LinkedIn pages are the fallback when GitHub is blocked.
        url=f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(role)}&f_WT=2&location=Morocco&start=0"
        direct=parse_linkedin(fetch(url) or "")
        candidates=direct or indexed(role, "Morocco remote")
        for j in candidates:
            if j["id"] not in seen: seen.add(j["id"]); out.append(j)
    # Casablanca: do not force remote; collect onsite/hybrid/remote indexed LinkedIn results.
    for role in ROLES:
        for geo in ("Casablanca", "Casablanca Morocco"):
            for j in indexed(role, geo):
                if j["id"] not in seen: seen.add(j["id"]); out.append(j)
    return out

def post(payload):
    if not WEBHOOK: raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    p=dict(payload); p["sheet"]=SHEET
    r=requests.post(WEBHOOK, json=p, timeout=30); r.raise_for_status(); print(f"[Sheet] {r.status_code}: {r.text[:300]}")

def pending(limit):
    r=requests.post(WEBHOOK, json={"mode":"pending","sheet":SHEET,"limit":limit}, timeout=30); r.raise_for_status()
    try: data=r.json()
    except ValueError as e: raise RuntimeError(f"Apps Script pending response is not JSON: {r.text[:300]!r}. Redeploy the Web App /exec endpoint.") from e
    if data.get("status")!="success": raise RuntimeError(str(data))
    return data.get("jobs",[])

EMAIL_RE=re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD_DOM={"example.com","sentry.io","schema.org","google.com","facebook.com","linkedin.com"}
SKIP=("noreply@","no-reply@","privacy@","security@","support@","mailer-daemon@")
PATHS=["","/contact","/contact-us","/careers","/jobs","/about","/imprint","/impressum","/en/contact","/en/careers"]

def company_site(company):
    if not company or company=="Unknown": return None
    html=fetch("https://html.duckduckgo.com/html/?q="+quote_plus(f'"{company}" official website careers jobs'))
    if not html: return None
    soup=BeautifulSoup(html,"html.parser"); blocked=("linkedin.com","facebook.com","instagram.com","twitter.com","x.com","indeed.com","glassdoor.com","crunchbase.com","wikipedia.org","duckduckgo.com")
    for a in soup.select("a.result__a"):
        h=a.get("href","")
        if h.startswith("http"):
            host=urlparse(h).netloc.lower().replace("www.","")
            if host and not any(x in host for x in blocked): return "https://"+host
    return None

def emails(html):
    text=re.sub(r"\s*(?:\[at\]|\(at\)|\{at\})\s*","@",html,flags=re.I); text=re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*",".",text,flags=re.I)
    found=set(EMAIL_RE.findall(text)); soup=BeautifulSoup(text,"html.parser")
    for a in soup.select('a[href^="mailto:"]'): found.add(a.get("href","")[7:].split("?")[0])
    return {e.lower().strip(" .;,<>\"'") for e in found if "@" in e and e.lower().split("@")[-1] not in BAD_DOM and not e.lower().startswith(SKIP)}

def enrich(j):
    site=j.get("company_site") or company_site(j.get("entreprise",""))
    if not site: return {"id":j["id"],"company_site":"","emails_rh":"","deep_status":"NO_SITE"}
    p=urlparse(site); base=f"{p.scheme}://{p.netloc}"; found=set(); pages=0
    for path in PATHS:
        h=fetch(base+path,10)
        if h: pages+=1; found |= emails(h)
    ranked=sorted(found,key=lambda e:(0 if any(k in e for k in ("career","recruit","hr","talent","jobs","hiring")) else 1,len(e)))
    return {"id":j["id"],"company_site":site,"emails_rh":" / ".join(ranked[:3]),"deep_status":"DONE" if pages else "SITE_FOUND_NO_PAGES"}

def deep(limit):
    jobs=pending(limit); updates=[]
    for i,j in enumerate(jobs,1):
        print(f"[DEEP] {i}/{len(jobs)} {j.get('entreprise')} — {j.get('intitule')}")
        try: updates.append(enrich(j))
        except Exception as e: updates.append({"id":j.get("id"),"deep_status":"ERROR","deep_error":str(e)[:200]})
    if updates: post({"mode":"enrich","updates":updates})

def main():
    p=argparse.ArgumentParser(); p.add_argument("--mode",choices=("scrape","deep"),default="scrape"); p.add_argument("--deep-limit",type=int,default=100); a=p.parse_args()
    if a.mode=="scrape":
        jobs=discover()
        if jobs: post({"mode":"jobs","jobs":jobs})
        print(f"DONE: {len(jobs)} Morocco/Casablanca LinkedIn jobs sent immediately to Sheet")
    else: deep(a.deep_limit)

if __name__=="__main__": main()
