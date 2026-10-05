"""Robust job discovery scraper.
Discovery is independent from email enrichment: a blocked board can never erase
or prevent jobs found by other sources.
"""
import argparse, hashlib, json, os, random, re, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
UA = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
]
S = requests.Session()

SEARCHES = [
    ("Customer Success", ["customer success manager","customer success","client success","client experience","customer onboarding","customer enablement","customer experience manager"]),
    ("Account Management", ["account manager","account management","key account manager","strategic account manager","client account manager","partner manager","customer account manager"]),
    ("Revenue & Partnerships", ["partnerships manager","partner success","business partnerships","sales operations","revenue operations","commercial operations","sales enablement"]),
    ("Travel-Tech & Hospitality", ["travel tech","travel technology","hospitality technology","hotel tech","travel account manager","travel customer success","hospitality account manager"]),
    ("E-commerce & Digital Operations", ["ecommerce manager","e-commerce manager","ecommerce operations","shopify manager","digital operations","marketplace manager","ecommerce customer success"]),
    ("Business Operations", ["business operations","operations coordinator","operations specialist","project coordinator","commercial coordinator","sales coordinator","business support"]),
    ("ADV & Sales Administration", ["administration des ventes","ADV","sales administration","sales administrator","order management","order administrator","customer operations"]),
    ("Executive & Administrative Support", ["executive assistant","executive secretary","administrative assistant","personal assistant","office manager","assistante de direction","assistante administrative","assistante polyvalente"]),
]
VARIANTS = [x for _, xs in SEARCHES for x in xs]
EXCLUDE = [
    "director","vice president","vp ","head of ","chief","architect","doctor","nurse",
    "software engineer","developer","data scientist","machine learning","devops","lawyer",
    "accountant","physician","warehouse worker","driver","internship","intern ",
    "cold calling","cold-call","cold call","100+ calls","high volume calls",
    "high-volume calls","commission only","commission-only","door to door","telemarketing",
    "night shift","overnight","rotating shifts","24/7","weekend shifts","unpaid","volunteer",
]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}")
BAD_EMAIL_DOMAINS = {"example.com","sentry.io","schema.org","google.com","facebook.com","linkedin.com"}
BLOCKED_DOMAINS = {
    "linkedin.com","facebook.com","instagram.com","twitter.com","x.com","indeed.com",
    "glassdoor.com","crunchbase.com","wikipedia.org","duckduckgo.com","google.com",
    "bing.com","youtube.com",
}
SEARCH_DOMAINS = [
    "indeed.com","emploi.ma","rekrute.com","bayt.com","novojob.com","optioncarriere.ma",
    "glassdoor.com","linkedin.com","welcometothejungle.com","wellfound.com","remotive.com",
    "weworkremotely.com","himalayas.app","jobgether.com","workingnomads.com","remoteok.com",
    "topcsjobs.com","supportdriven.com",
]
JOB_BOARD_SEARCHES = [
    ("Indeed","indeed.com"),("Emploi.ma","emploi.ma"),("ReKrute","rekrute.com"),
    ("Bayt","bayt.com"),("Novojob","novojob.com"),("Optioncarriere","optioncarriere.ma"),
    ("Glassdoor","glassdoor.com"),("LinkedIn Jobs","linkedin.com/jobs"),
    ("Welcome to the Jungle","welcometothejungle.com"),("Wellfound","wellfound.com"),
    ("Remotive","remotive.com"),("We Work Remotely","weworkremotely.com"),
    ("Himalayas","himalayas.app"),("Jobgether","jobgether.com"),
    ("Working Nomads","workingnomads.com"),("Remote OK","remoteok.com"),
    ("TopCSJobs","topcsjobs.com"),("Support Driven","supportdriven.com"),
]
SOURCE_STATS = {}
BOARD_STATS = {}
NO_EMAIL_BUFFER = []

DEEP_TIMEOUT = 5
DEEP_WORKERS = 16
WEBHOOK_TIMEOUT = 90
WEBHOOK_RETRIES = 3
WEBHOOK_BATCH = 25
PATHS = [
    "","/contact","/contact-us","/careers","/career","/jobs","/join-us","/work-with-us",
    "/recruitment","/human-resources","/hr","/about","/en/contact","/en/careers",
    "/fr/contact","/fr/carriere","/fr/recrutement","/legal","/imprint","/impressum",
    "/kontakt","/karriere","/bewerbung","/stellenangebote",
]
START = time.monotonic()
MAX_RUNTIME = 40 * 60
SEARCH_CACHE = {}
SEARCH_CACHE_LOCK = threading.Lock()
LINKEDIN_API_DISABLED = False
LINKEDIN_API_LOCK = threading.Lock()

def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def clean(x):
    return re.sub(r"\s+", " ", str(x or "")).strip()

def hdr():
    return {
        "User-Agent": random.choice(UA),
        "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,de;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

def stat(source, key, n=1):
    SOURCE_STATS.setdefault(source, {})
    SOURCE_STATS[source][key] = SOURCE_STATS[source].get(key, 0) + n

def board_stat(board, key, n=1):
    BOARD_STATS.setdefault(board, {})
    BOARD_STATS[board][key] = BOARD_STATS[board].get(key, 0) + n

def budget_ok():
    return time.monotonic() - START < MAX_RUNTIME

def target(title, description=""):
    text = (clean(title) + " " + clean(description)).lower()
    return bool(title) and not any(x in text for x in EXCLUDE)

def fresh_window(age, hours=72):
    s = clean(age).lower()
    if not s:
        return True
    if any(x in s for x in ("just posted","today","new","il y a quelques","il y a 1 heure")):
        return True
    m = re.search(r"(\d+)\s*(?:hours?|heures?)\b", s)
    if m: return int(m.group(1)) <= hours
    m = re.search(r"(\d+)\s*(?:days?|jours?)\b", s)
    if m: return int(m.group(1)) * 24 <= hours
    m = re.search(r"(\d+)\s*(?:minutes?|mins?)\b", s)
    if m: return int(m.group(1)) / 60 <= hours
    return True

def base_domain(host):
    host = clean(host).lower().replace("https://","").replace("http://","").replace("www.","").split("/")[0].split(":")[0]
    p = host.split(".")
    return ".".join(p[-2:]) if len(p) >= 2 else host

def host(url):
    try:
        h = urlparse(url).netloc.lower().split(":")[0]
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""

def jid(source, url, title):
    key = url.split("?")[0].rstrip("/") if url else source + "|" + title
    return "job_" + hashlib.sha256(key.encode()).hexdigest()[:18]

def make_job(title, company, loc, remote, source, url, age="", desc="", kind=None):
    return {
        "id": jid(source, url, title),
        "date_detection": now(), "statut": "NEW",
        "role_cible": title, "intitule": title,
        "entreprise": company or "Unknown",
        "lieu": loc or ("Remote / Worldwide" if remote else "Casablanca, Morocco"),
        "remote": bool(remote), "source": source, "lien": url,
        "company_site": "", "emails_rh": "", "deep_status": "PENDING",
        "fit_score": "", "fit_reasons": "", "salary": "",
        "description": clean(desc), "posted_age": clean(age),
        "posted_within_24h": "YES" if fresh_window(age,24) else "UNKNOWN",
        "search_type": kind or ("REMOTE" if remote else "CASABLANCA"),
        "email_status": "PENDING", "email_source": "", "spontaneous": "NO",
    }

def fetch(url, timeout=10, retries=1):
    if not budget_ok(): return None
    for attempt in range(retries + 1):
        try:
            r = S.get(url, headers=hdr(), timeout=timeout, allow_redirects=True)
            if r.status_code == 200 and r.text:
                return r.text
            stat("HTTP", str(r.status_code))
            print(f"[HTTP] {r.status_code} {url}", flush=True)
            if r.status_code in (401,403,429):
                return None
        except requests.RequestException as e:
            stat("HTTP","exception")
            print(f"[HTTP ERROR] {type(e).__name__} {url}", flush=True)
        if attempt < retries:
            time.sleep(min(3, 1.2 ** attempt))
    return None

def web_search(q, limit=20):
    """Use a bounded public-index fanout. Cache identical queries and never let one engine block the run."""
    key=(clean(q),int(limit))
    with SEARCH_CACHE_LOCK:
        cached=SEARCH_CACHE.get(key)
    if cached is not None:
        return list(cached)

    providers=[
        ("Bing","https://www.bing.com/search?q="+quote_plus(q)),
        ("DDG","https://html.duckduckgo.com/html/?q="+quote_plus(q)),
    ]
    merged=[]; seen=set()
    for name,url in providers:
        if not budget_ok(): break
        try:
            r=S.get(url,headers=hdr(),timeout=7,allow_redirects=True)
            if r.status_code != 200 or not r.text:
                stat("Search",f"{name}_{r.status_code}")
                continue
            soup=BeautifulSoup(r.text,"html.parser")
            if name=="Bing":
                links=[(clean(a.get_text(" ",strip=True)),a.get("href",""))
                       for a in soup.select("li.b_algo h2 a[href]")]
            else:
                links=[(clean(a.get_text(" ",strip=True)),a.get("href",""))
                       for a in soup.select("a.result__a[href],a.result-link[href]")]
            for title,u in links:
                if not u.startswith("http") or not title: continue
                u=u.split("#",1)[0]
                if u in seen: continue
                seen.add(u); merged.append((title,u))
                if len(merged)>=limit: break
        except requests.RequestException:
            stat("Search",f"{name}_exception")
        if len(merged)>=limit: break

    # Google is a fallback only when both primary indexes returned too little.
    if len(merged) < min(5,limit) and budget_ok():
        try:
            url="https://www.google.com/search?q="+quote_plus(q)
            r=S.get(url,headers=hdr(),timeout=7,allow_redirects=True)
            if r.status_code==200 and r.text:
                soup=BeautifulSoup(r.text,"html.parser")
                for a in soup.select("a[href]"):
                    u=a.get("href","")
                    title=clean(a.get_text(" ",strip=True))
                    if not u.startswith("http") or not title: continue
                    u=u.split("#",1)[0]
                    if u in seen: continue
                    seen.add(u); merged.append((title,u))
                    if len(merged)>=limit: break
        except requests.RequestException:
            stat("Search","Google_exception")

    result=merged[:limit]
    with SEARCH_CACHE_LOCK:
        SEARCH_CACHE[key]=list(result)
    if result: stat("Search","merged")
    return result

def parse_linkedin(html, remote, kind):
    soup = BeautifulSoup(html or "", "html.parser")
    out=[]; seen=set()
    for card in soup.select("li.base-card,li.jobs-search__results-list,div.base-card"):
        a=card.select_one('a[href*="/jobs/view/"]')
        if not a: continue
        u=a.get("href","").split("?")[0]
        m=re.search(r"/jobs/view/(?:[^/]+-)?(\d+)",u)
        if not m or m.group(1) in seen: continue
        seen.add(m.group(1))
        title=clean((card.select_one("h3") or a).get_text(" ",strip=True))
        ce=card.select_one("h4,.base-search-card__subtitle,.hidden-nested-link")
        le=card.select_one(".job-search-card__location")
        te=card.select_one("time,.job-search-card__listdate,.job-search-card__listdate--new")
        company=clean(ce.get_text(" ",strip=True) if ce else "")
        loc=clean(le.get_text(" ",strip=True) if le else "")
        age=clean(te.get_text(" ",strip=True) if te else "")
        if title and company and target(title) and fresh_window(age):
            out.append(make_job(title,company,loc,remote,"LinkedIn",urljoin("https://www.linkedin.com",u),age,"",kind))
    return out

def linkedin_query(keywords, location, remote, kind):
    global LINKEDIN_API_DISABLED
    out=[]
    if not LINKEDIN_API_DISABLED and budget_ok():
        params=f"?keywords={quote_plus(keywords)}&location={quote_plus(location)}&f_TPR=r604800&start=0"
        if remote: params += "&f_WT=2"
        try:
            h=fetch("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"+params,12,0)
            if h:
                found=parse_linkedin(h,remote,kind)
                out.extend(found)
                print(f"[LinkedIn page] {keywords} {kind} start=0 found={len(found)}",flush=True)
            else:
                with LINKEDIN_API_LOCK:
                    LINKEDIN_API_DISABLED=True
                print("[LinkedIn] API rate-limited/unavailable; switching remaining discovery to public indexes",flush=True)
        except Exception:
            with LINKEDIN_API_LOCK:
                LINKEDIN_API_DISABLED=True

    # Public indexes remain available even when LinkedIn's guest API rate-limits the runner.
    if len(out)<5 and budget_ok():
        q=f'site:linkedin.com/jobs/view "{keywords}" "{location}"'
        for title,u in web_search(q,20):
            if "/jobs/view/" not in u: continue
            if not target(title): continue
            parts=[clean(x) for x in re.split(r"\s[|–—-]\s*",title) if clean(x)]
            company=parts[-1] if len(parts)>1 else "LinkedIn Employer"
            out.append(make_job(parts[0] if parts else title,company,location,remote,"LinkedIn Search",u,"",title,kind))
    return out

def linkedin():
    targets=[("WORLDWIDE_REMOTE","Remote / Worldwide",True),("MOROCCO_REMOTE","Morocco",True),("CASABLANCA_ONSITE","Casablanca, Morocco",False)]
    tasks=[]
    for family,variants in SEARCHES:
        q=f'"{variants[0]}" OR "{variants[1]}"'
        for kind,loc,remote in targets:
            tasks.append((family,q,kind,loc,remote))
    out=[];seen=set()
    def one(t):
        family,q,kind,loc,remote=t
        found=linkedin_query(q,loc,remote,kind)
        if len(found)<3 and budget_ok():
            for title,u in web_search(f'site:linkedin.com/jobs/view "{q}" "{loc}"',20):
                if "/jobs/view/" not in u: continue
                if not target(title): continue
                parts=[clean(x) for x in re.split(r"\s[|–—-]\s*",title) if clean(x)]
                company=parts[-1] if len(parts)>1 else "LinkedIn Employer"
                found.append(make_job(parts[0] if parts else title,company,loc,remote,"LinkedIn Search",u,"",title,kind))
        return found
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures=[ex.submit(one,t) for t in tasks]
        for f in as_completed(futures):
            try:
                for j in f.result():
                    if j["id"] not in seen: seen.add(j["id"]);out.append(j)
            except Exception as e:
                stat("LinkedIn","errors"); print(f"[LinkedIn ERROR] {e}",flush=True)
    print(f"[LinkedIn] total={len(out)}",flush=True)
    return out

def indeed():
    """Recover Indeed listings through public indexes without hammering 403-protected Indeed HTML."""
    targets=[("WORLDWIDE_REMOTE","Remote",True),("MOROCCO_REMOTE","Morocco",True),("CASABLANCA_ONSITE","Casablanca",False)]
    tasks=[]
    for family,variants in SEARCHES:
        seeds=" OR ".join(f'"{x}"' for x in variants[:3])
        for kind,loc,remote in targets:
            tasks.append((family,kind,loc,remote,seeds))

    out=[]; seen=set()
    def one(t):
        family,kind,loc,remote,seeds=t
        q=f'site:indeed.com/viewjob ({seeds}) "{loc}"'
        found=[]
        for title,u in web_search(q,30):
            h=host(u)
            if "indeed.com" not in h or ("/viewjob" not in u and "/jobs/view" not in u): continue
            if not target(title): continue
            parts=[clean(x) for x in re.split(r"\\s[|–—-]\\s*",title) if clean(x)]
            jt=parts[0] if parts else clean(title)
            company=parts[-1] if len(parts)>1 else "Indeed Employer"
            found.append(make_job(jt,company,loc,remote,"Indeed",u.split("?")[0],"",title,kind))
        return found

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures=[ex.submit(one,t) for t in tasks]
        for f in as_completed(futures):
            try:
                for j in f.result():
                    if j["id"] not in seen:
                        seen.add(j["id"]); out.append(j)
            except Exception as e:
                board_stat("Indeed","ERROR")
                print(f"[Indeed ERROR] {e}",flush=True)
    board_stat("Indeed","INDEXED_FOUND",len(out))
    print(f"[Indeed] indexed total={len(out)}",flush=True)
    return out

def accept_job(title, desc=""):
    text=(clean(title)+" "+clean(desc)).lower()
    return target(title,desc) and any(v.lower() in text for v in VARIANTS)

def parse_search_jobs(items,kind,remote,board):
    out=[];seen=set();allowed=base_domain(board)
    for title,u in items:
        if base_domain(host(u)) != allowed: continue
        if not accept_job(title): continue
        low=(title+" "+u).lower()
        if not any(x in low for x in ("job","jobs","career","vacan","stellen","emploi","recrut","position","opening","apply","viewjob")):
            continue
        parts=[clean(x) for x in re.split(r"\s[|–—-]\s*",title) if clean(x)]
        company=parts[-1] if len(parts)>1 else host(u).split(".")[0].replace("-"," ").title()
        if u.split("#",1)[0] in seen: continue
        seen.add(u.split("#",1)[0])
        out.append(make_job(parts[0] if parts else title,company,
                            "Remote / Worldwide" if remote else ("Casablanca, Morocco" if kind=="CASABLANCA_ONSITE" else "Morocco"),
                            remote,board,u.split("#",1)[0],"",title,kind))
    return out

def direct_himalayas(kind):
    out=[];seen=set(); board="Himalayas"
    for _,variants in SEARCHES:
        seed=" OR ".join(variants[:3])
        if not budget_ok(): return out
        try:
            r=S.get("https://himalayas.app/jobs/api/search",params={"q":seed,"sort":"recent","page":1},headers=hdr(),timeout=12)
            if r.status_code!=200: board_stat(board,"HTTP_"+str(r.status_code)); continue
            for x in r.json().get("jobs",[]):
                title=clean(x.get("title")); desc=BeautifulSoup(x.get("description",""),"html.parser").get_text(" ",strip=True)
                if not accept_job(title,desc): continue
                u=x.get("applicationLink") or x.get("guid")
                if not u or u in seen: continue
                seen.add(u)
                loc=", ".join(x.get("locationRestrictions") or []) or "Remote / Worldwide"
                out.append(make_job(title,x.get("companyName",""),loc,True,board,u,"",desc,kind))
        except (requests.RequestException,ValueError) as e:
            board_stat(board,"ERROR"); print(f"[{board}] {type(e).__name__}",flush=True)
    board_stat(board,"FOUND",len(out)); return out

def direct_remotive(kind):
    out=[];seen=set(); board="Remotive"
    for _,variants in SEARCHES:
        seed=" OR ".join(variants[:3])
        if not budget_ok(): return out
        try:
            r=S.get("https://remotive.com/api/remote-jobs",params={"search":seed,"limit":100},headers=hdr(),timeout=12)
            if r.status_code!=200:
                board_stat(board,"HTTP_"+str(r.status_code)); continue
            for x in r.json().get("jobs",[]):
                title=clean(x.get("title")); desc=BeautifulSoup(x.get("description",""),"html.parser").get_text(" ",strip=True)
                if not accept_job(title,desc): continue
                u=x.get("url")
                if not u or u in seen: continue
                seen.add(u)
                out.append(make_job(title,x.get("company_name",""),
                    clean(x.get("candidate_required_location","")) or "Remote / Worldwide",
                    True,board,u,clean(x.get("publication_date","")),desc,kind))
        except (requests.RequestException,ValueError) as e:
            board_stat(board,"ERROR"); print(f"[{board}] {type(e).__name__}",flush=True)
    board_stat(board,"FOUND",len(out)); return out

def direct_board_search(board,domain,kind,remote):
    if board=="Indeed": return []
    role_expr=" OR ".join(f'"{x}"' for _,vs in SEARCHES for x in vs[:2])
    if kind=="WORLDWIDE_REMOTE":
        place='(remote OR "work from home" OR worldwide)'
    elif kind=="MOROCCO_REMOTE":
        place='(Morocco OR Maroc OR remote)'
    else:
        place='(Casablanca OR "Casablanca, Morocco")'
    q=f"site:{domain} ({role_expr}) {place}"
    parsed=parse_search_jobs(web_search(q,30),kind,remote,board)
    board_stat(board,"FOUND",len(parsed))
    return parsed

def public_web_jobs():
    targets=[("WORLDWIDE_REMOTE",True),("MOROCCO_REMOTE",True),("CASABLANCA_ONSITE",False)]
    tasks=[]
    for kind,remote in targets:
        if remote:
            tasks.append(("Himalayas",lambda k=kind:direct_himalayas(k)))
            tasks.append(("Remotive",lambda k=kind:direct_remotive(k)))
        for board,domain in JOB_BOARD_SEARCHES:
            tasks.append((board,lambda b=board,d=domain,k=kind,r=remote:direct_board_search(b,d,k,r)))
    out=[];seen=set()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures={ex.submit(fn):name for name,fn in tasks}
        for f in as_completed(futures):
            name=futures[f]
            try:
                for j in f.result() or []:
                    if j["id"] not in seen: seen.add(j["id"]);out.append(j)
            except Exception as e:
                board_stat(name,"ERROR"); print(f"[BOARD ERROR] {name}: {e}",flush=True)

    print(f"[Web + ALL BOARDS] total={len(out)}",flush=True)
    return out

def extract_emails(html):
    text=re.sub(r"\s*(?:\[at\]|\(at\)|\{at\})\s*","@",html,flags=re.I)
    text=re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*",".",text,flags=re.I)
    soup=BeautifulSoup(text,"html.parser")
    found=set(EMAIL_RE.findall(text))
    for a in soup.select('a[href^="mailto:"]'):
        found.add(a.get("href","")[7:].split("?")[0])
    return {e.lower().strip(" .;,<>\"'") for e in found
            if "@" in e and e.lower().split("@")[-1] not in BAD_EMAIL_DOMAINS
            and not e.lower().startswith(("noreply@","no-reply@","privacy@","security@"))}

def official_site(company):
    if not company or clean(company).lower() in {"unknown","indeed employer","linkedin employer"}: return ""
    for q in (f'"{company}" official website',f'"{company}" contact careers',f'"{company}" recruitment email'):
        for _,u in web_search(q,12):
            h=host(u)
            if not h or any(h==x or h.endswith("."+x) for x in BLOCKED_DOMAINS): continue
            if any(x in h for x in ("careerjet","jobboard","jooble","talent.com","simplyhired")): continue
            return "https://"+h+"/"
    return ""

def enrich_one(j):
    company=clean(j.get("entreprise",""))
    site=j.get("company_site") or official_site(company)
    if not site:
        return {"company_site":"","emails_rh":"","deep_status":"NO_SITE","email_status":"NO_EMAIL","email_source":""}
    p=urlparse(site); base=f"{p.scheme}://{p.netloc}"
    emails=set()
    pages=False
    for path in PATHS:
        h=fetch(base+path,DEEP_TIMEOUT,0)
        if h:
            pages=True; emails |= extract_emails(h)
    good=sorted(emails)
    return {
        "company_site":site,
        "emails_rh":" / ".join(good),
        "deep_status":"DONE" if pages else "SITE_FOUND_NO_PAGES",
        "email_status":"FOUND" if good else "NO_EMAIL",
        "email_source":"Verified public company website" if good else "",
    }

def enrich_jobs(jobs):
    groups={}
    for j in jobs:
        key=clean(j.get("entreprise","")).lower()
        if not key: continue
        groups.setdefault(key,[]).append(j)
    print(f"[ENRICH] unique companies={len(groups)}",flush=True)
    def one(group):
        try: return group,enrich_one(group[0])
        except Exception as e: return group,{"deep_status":"ERROR","email_status":"ERROR","emails_rh":"","company_site":"","email_source":"","error":str(e)[:200]}
    updates=[]
    with ThreadPoolExecutor(max_workers=DEEP_WORKERS) as ex:
        futures=[ex.submit(one,group) for group in groups.values()]
        for f in as_completed(futures):
            try:
                group,u=f.result()
                for j in group:
                    j.update(u)
                    updates.append({
                        "id":j["id"],"sheet":sheet_for(j),
                        "company_site":j.get("company_site",""),
                        "emails_rh":j.get("emails_rh",""),
                        "deep_status":j.get("deep_status",""),
                        "email_status":j.get("email_status",""),
                        "email_source":j.get("email_source",""),
                        "salary":j.get("salary",""),
                        "fit_score":j.get("fit_score",""),
                        "fit_reasons":j.get("fit_reasons",""),
                    })
                if len(updates)>=25:
                    try: post({"mode":"enrich","updates":updates})
                    except Exception as e: print(f"[ENRICH WRITE] {e}",flush=True)
                    updates=[]
            except Exception as e:
                print(f"[ENRICH ERROR] {e}",flush=True)
    if updates:
        try: post({"mode":"enrich","updates":updates})
        except Exception as e: print(f"[ENRICH WRITE] {e}",flush=True)

def fit_job(j):
    text=clean(" ".join(str(j.get(k,"")) for k in ("intitule","description","entreprise"))).lower()
    score=50; reasons=[]
    if any(k in text for k in ("customer success","account manager","client success","partner manager","customer experience")):
        score+=15; reasons.append("Relevant customer/account experience")
    if any(k in text for k in ("saas","travel tech","travel technology","ecommerce","shopify")):
        score+=10; reasons.append("Relevant digital/travel/e-commerce environment")
    if any(k in text for k in ("remote","work from home","distributed","home-based")):
        score+=8; reasons.append("Remote-friendly")
    if any(k in text for k in ("cold call","cold calling","commission only","night shift","rotating shifts","weekends")):
        score-=25; reasons.append("Potential high-pressure/unsocial-hours signal")
    return max(0,min(100,score)), "; ".join(reasons)

def sheet_for(j):
    return {
        "WORLDWIDE_REMOTE":"Worldwide Remote",
        "MOROCCO_REMOTE":"Morocco Remote",
        "CASABLANCA_ONSITE":"Casablanca Onsite",
        "CASABLANCA_SPONTANEOUS":"Casablanca Spontaneous",
    }.get(j.get("search_type",""),"Worldwide Remote")

def webhook_url():
    u=WEBHOOK.strip().strip('"').strip("'")
    if not u: raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    if not re.match(r"^https?://",u,re.I): u="https://"+u.lstrip("/")
    p=urlparse(u)
    if p.scheme not in ("http","https") or not p.netloc: raise RuntimeError("Invalid GOOGLE_SHEET_WEBHOOK_URL")
    return u

def post(payload, expected="success"):
    u=webhook_url()
    last=""
    for attempt in range(1,WEBHOOK_RETRIES+1):
        try:
            r=S.post(u,json=payload,headers={"Content-Type":"application/json"},allow_redirects=True,timeout=(15,WEBHOOK_TIMEOUT))
            if not (200 <= r.status_code < 300):
                last=f"HTTP {r.status_code}"; print(f"[WEBHOOK] {last}",flush=True)
            else:
                try: data=r.json()
                except ValueError: data={}
                if data.get("status")==expected: return data
                last=f"Unexpected response {data!r}"
        except (requests.RequestException,RuntimeError) as e:
            last=str(e); print(f"[WEBHOOK] attempt {attempt}/{WEBHOOK_RETRIES}: {last}",flush=True)
        if attempt < WEBHOOK_RETRIES: time.sleep(3*attempt)
    raise RuntimeError("Webhook failed: "+last)

def post_jobs(jobs,sheet):
    if not jobs: return {"added":0}
    for j in jobs: j["sheet"]=sheet
    total=0
    for i in range(0,len(jobs),WEBHOOK_BATCH):
        data=post({"mode":"jobs","jobs":jobs[i:i+WEBHOOK_BATCH],"sheet":sheet})
        total += int(data.get("added",0))
    return {"added":total}

def spontaneous_casablanca():
    queries=[
        '"multinationale" Casablanca recrutement','"multinational" Casablanca Morocco careers',
        '"international company" Casablanca Morocco careers','"shared services" Casablanca Morocco recruitment',
        '"BPO" Casablanca Morocco careers','"SaaS" Casablanca Morocco company',
        '"travel" Casablanca Morocco company careers','"logistics" Casablanca Morocco company careers',
        '"ecommerce" Casablanca Morocco company careers','"FMCG" Casablanca Morocco company careers',
    ]
    companies={}
    for q in queries:
        if not budget_ok(): break
        for title,u in web_search(q,15):
            h=host(u)
            if not h or any(h==x or h.endswith("."+x) for x in BLOCKED_DOMAINS): continue
            if any(x in h for x in ("linkedin","indeed","glassdoor","bayt","rekrute","emploi.ma","novojob")): continue
            name=clean(re.sub(r"\s*[-|–]\s*(careers|jobs|recruitment|casablanca).*$","",title,flags=re.I)) or h.split(".")[0].title()
            companies[h]={"name":name,"site":"https://"+h+"/"}
    out=[]
    for h,info in companies.items():
        j=make_job("Customer Success / Account Management / Sales Administration / Executive Support",
                   info["name"],"Casablanca, Morocco",False,"Spontaneous Company Search",
                   info["site"],"","Potential fit — spontaneous application","CASABLANCA_SPONTANEOUS")
        j["company_site"]=info["site"];j["spontaneous"]="YES";out.append(j)
    return out

def discover_source(name, fn):
    SOURCE_STATS[name]={}
    try: jobs=fn()
    except Exception as e:
        stat(name,"errors"); print(f"[SOURCE ERROR] {name}: {e}",flush=True); jobs=[]
    return name,jobs

def scrape():
    webhook_url()
    print("[START] concurrent high-coverage discovery",flush=True)
    NO_EMAIL_BUFFER.clear()
    seen={}; source_totals={}; added_total=0

    sources=[("LinkedIn",linkedin),("Indeed",indeed),("Web",public_web_jobs)]
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures=[ex.submit(discover_source,n,f) for n,f in sources]
        for f in as_completed(futures):
            name,jobs=f.result()
            unique=[]
            for j in jobs:
                if j["id"] in seen: continue
                seen[j["id"]]=j;unique.append(j)
            by_sheet={}
            for j in unique: by_sheet.setdefault(sheet_for(j),[]).append(j)
            added=0
            for sheet,batch in by_sheet.items():
                try:
                    added += int(post_jobs(batch,sheet).get("added",0))
                except Exception as e: print(f"[WRITE ERROR] {name}/{sheet}: {e}",flush=True)
            added_total += added
            source_totals[name]={"discovered":len(unique),"sheet_added":added,"stats":SOURCE_STATS.get(name,{})}
            print(f"[SOURCE DONE] {name}: discovered={len(unique)} added={added}",flush=True)

    try:
        spontaneous=spontaneous_casablanca()
        unique=[]
        for j in spontaneous:
            if j["id"] in seen: continue
            seen[j["id"]]=j;unique.append(j)
        by_sheet={}
        for j in unique: by_sheet.setdefault(sheet_for(j),[]).append(j)
        added=0
        for sheet,batch in by_sheet.items():
            try: added += int(post_jobs(batch,sheet).get("added",0))
            except Exception as e: print(f"[SPONTANEOUS WRITE] {e}",flush=True)
        added_total += added
        source_totals["Casablanca Spontaneous"]={"discovered":len(unique),"sheet_added":added}
    except Exception as e:
        source_totals["Casablanca Spontaneous"]={"error":str(e)}

    jobs=list(seen.values())
    print(f"[COLLECTED] unique={len(jobs)}",flush=True)
    for j in jobs:
        score,reasons=fit_job(j);j["fit_score"]=str(score);j["fit_reasons"]=reasons

    try:
        enrich_jobs(jobs)
    except Exception as e:
        print(f"[ENRICHMENT ERROR] {e}",flush=True)

    # Final update is safe because discovery was already written.
    updates=[]
    for j in jobs:
        updates.append({
            "id":j["id"],"sheet":sheet_for(j),
            "company_site":j.get("company_site",""),"emails_rh":j.get("emails_rh",""),
            "deep_status":j.get("deep_status",""),"email_status":j.get("email_status",""),
            "email_source":j.get("email_source",""),"salary":j.get("salary",""),
            "fit_score":j.get("fit_score",""),"fit_reasons":j.get("fit_reasons",""),
        })
    for i in range(0,len(updates),50):
        try: post({"mode":"enrich","updates":updates[i:i+50]})
        except Exception as e: print(f"[ENRICH FINAL WRITE] {e}",flush=True)

    run={
        "finished_at":now(),"total_unique":len(jobs),"added":added_total,
        "email_found":sum(1 for j in jobs if j.get("emails_rh")),
        "no_email":sum(1 for j in jobs if not j.get("emails_rh")),
        "source_totals":source_totals,"board_stats":BOARD_STATS,"source_stats":SOURCE_STATS,
    }
    print(f"[DONE] {run}",flush=True)
    try: post({"mode":"log","run":run})
    except Exception as e: print(f"[LOG ERROR] {e}",flush=True)

def pending():
    data=post({"mode":"pending","limit":5000,"sheet":"ALL"})
    return data.get("jobs",[])

def deep():
    jobs=pending()
    print(f"[DEEP] pending={len(jobs)}",flush=True)
    updates=[]
    for j in jobs:
        try:
            u=enrich_one(j);u["id"]=j.get("id");u["sheet"]=j.get("sheet")
            updates.append(u)
        except Exception as e:
            updates.append({"id":j.get("id"),"sheet":j.get("sheet"),"deep_status":"ERROR","email_status":"ERROR"})
        if len(updates)>=25:
            post({"mode":"enrich","updates":updates});updates=[]
    if updates: post({"mode":"enrich","updates":updates})
    print("[DEEP] complete",flush=True)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mode",choices=["scrape","deep"],default="scrape")
    a=p.parse_args()
    if a.mode=="scrape": scrape()
    else: deep()

if __name__=="__main__":
    main()
