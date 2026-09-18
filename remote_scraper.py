"""Job discovery pipeline: LinkedIn + Indeed + public web + Casablanca company discovery."""
import argparse, hashlib, os, random, re, time
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
ROLES = [
    "Customer Success Manager","Customer Success Specialist","Customer Support Specialist",
    "Customer Experience Specialist","Customer Care Specialist","Account Manager",
    "Customer Account Manager","Sales Development Representative","Business Development Representative",
    "Inside Sales Representative","Sales Executive","Account Executive","Sales Representative",
    "Operations Coordinator","Business Operations Specialist","Administrative Coordinator",
    "Sales Operations Specialist","Commercial Operations Specialist","E-commerce Specialist",
    "Shopify Specialist","CRM Specialist","Back Office Specialist","Project Coordinator","Designer"
]
EXCLUDE = ["senior","sr.","sr ","lead","principal","director","vp ","vice president","head of ","chief","staff",
           "architect","internship","intern ","doctor","nurse","software engineer","developer","data scientist",
           "machine learning","devops","lawyer","accountant","physician","warehouse worker","driver"]
UA = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/139.0 Safari/537.36"
]
S = requests.Session()
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD = {"example.com","sentry.io","schema.org","google.com","facebook.com","linkedin.com"}
BLOCKED = {"linkedin.com","facebook.com","instagram.com","twitter.com","x.com","indeed.com",
           "glassdoor.com","crunchbase.com","wikipedia.org","duckduckgo.com","google.com","bing.com","youtube.com"}
PATHS = ["","/contact","/contact-us","/careers","/career","/jobs","/join-us","/work-with-us",
         "/recruitment","/human-resources","/hr","/about","/en/contact","/en/careers","/fr/contact"]

def now(): return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def clean(x): return re.sub(r"\s+"," ",str(x or "")).strip()
def hdr():
    return {"User-Agent":random.choice(UA),"Accept-Language":"en-US,en;q=0.9,fr;q=0.8",
            "Accept":"text/html,application/xhtml+xml"}

def _json_response(resp):
    try:
        data = resp.json()
        return data
    except ValueError:
        body = (resp.text or "").strip()
        print(f"[WEBHOOK] non-JSON response status={resp.status_code} content_type={resp.headers.get('content-type','')} body={body[:500]!r}", flush=True)
        # Apps Script may redirect/return HTML. Do not pretend it is JSON.
        return None

def webhook_request(method, **kwargs):
    try:
        if method == "GET":
            r = requests.get(WEBHOOK, allow_redirects=True, timeout=(10,60), **kwargs)
        else:
            r = requests.post(WEBHOOK, allow_redirects=True, timeout=(10,60), **kwargs)
        print(f"[WEBHOOK] {method} final_url={r.url} status={r.status_code} content_type={r.headers.get('content-type','')}", flush=True)
        return r
    except requests.RequestException as e:
        print(f"[WEBHOOK] {method} error: {e}", flush=True)
        return None

def fetch(url, timeout=15, retries=0):
    for i in range(retries + 1):
        try:
            r = S.get(url, headers=hdr(), timeout=timeout, allow_redirects=True)
            if r.status_code == 200 and r.text:
                return r.text
            if r.status_code == 429:
                print(f"[RATE_LIMIT] 429 {url}", flush=True)
                return None
            print(f"[HTTP] {r.status_code} {url}", flush=True)
        except requests.RequestException as e:
            print(f"[HTTP] error {url}: {e}", flush=True)
        if i < retries: time.sleep(2 + i * 2)
    return None

def age_hours(t):
    t = clean(t).lower()
    if any(x in t for x in ["just posted","today","il y a quelques","il y a 1 heure","new"]): return 0
    m = re.search(r"(\d+)\s*(?:hours?|heures?)\b", t)
    if m: return int(m.group(1))
    m = re.search(r"(\d+)\s*(?:minutes?|mins?)\b", t)
    if m: return int(m.group(1)) / 60
    m = re.search(r"(\d+)\s*(?:days?|jours?)\b", t)
    if m: return int(m.group(1)) * 24
    return None

def fresh(t):
    h = age_hours(t)
    return h is not None and h <= 24

def target(t,d=""):
    return not any(x in (t + " " + d).lower() for x in EXCLUDE)

def jid(source,url,title):
    key = url.split("?")[0].rstrip("/") if url else source + "|" + title
    return "job_" + hashlib.sha256(key.encode()).hexdigest()[:18]

def job(title,company,loc,remote,source,url,age="",desc="",kind=None):
    return {"id":jid(source,url,title),"date_detection":now(),"statut":"NEW","role_cible":title,"intitule":title,
            "entreprise":company or "Unknown","lieu":loc or ("Remote / Worldwide" if remote else "Casablanca"),
            "remote":bool(remote),"source":source,"lien":url,"company_site":"","emails_rh":"","deep_status":"PENDING",
            "fit_score":"","fit_reasons":"","salary":"","description":clean(desc),"posted_age":clean(age),
            "posted_within_24h":"YES" if fresh(age) else "UNKNOWN","search_type":kind or ("REMOTE" if remote else "CASABLANCA"),
            "email_status":"PENDING","email_source":"","spontaneous":"NO"}

def parse_linkedin(html,remote,search_type):
    soup=BeautifulSoup(html,"html.parser");out=[];seen=set()
    for card in soup.select("li.base-card,li.jobs-search__results-list,div.base-card"):
        a=card.select_one('a[href*="/jobs/view/"]')
        if not a: continue
        href=a.get("href","").split("?")[0];m=re.search(r"/jobs/view/(?:[^/]+-)?(\d+)",href)
        if not m or m.group(1) in seen: continue
        seen.add(m.group(1));title=clean((card.select_one("h3") or a).get_text(" ",strip=True))
        ce=card.select_one("h4,.base-search-card__subtitle,.hidden-nested-link");le=card.select_one(".job-search-card__location")
        te=card.select_one("time,.job-search-card__listdate,.job-search-card__listdate--new")
        company=clean(ce.get_text(" ",strip=True) if ce else "");loc=clean(le.get_text(" ",strip=True) if le else "")
        age=clean(te.get_text(" ",strip=True) if te else "")
        if title and company and target(title) and (fresh(age) or not age):
            out.append(job(title,company,loc,remote,"LinkedIn",urljoin("https://www.linkedin.com",href),age,kind=search_type))
    return out

def linkedin_guest_search(keywords,location,remote,search_type):
    params=f"?keywords={quote_plus(keywords)}&location={quote_plus(location)}&f_TPR=r86400&start=0"
    if remote: params += "&f_WT=2"
    h=fetch("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"+params,15,0)
    return parse_linkedin(h,remote,search_type) if h else []

def results(q,limit=10):
    h=fetch("https://html.duckduckgo.com/html/?q="+quote_plus(q),15,0)
    if not h: return []
    return [(clean(a.get_text(" ",strip=True)),a.get("href",""))
            for a in BeautifulSoup(h,"html.parser").select("a.result__a")[:limit]
            if a.get("href","").startswith("http")]

def company_from_linkedin_url(url):
    m=re.search(r"-at-([^-]+(?:-[^-]+){0,8})-(\d{6,})/?$",url)
    return clean(m.group(1).replace("-"," ")).title() if m else ""

def linkedin_web_fallback(keywords,location,remote,search_type):
    queries=([f'site:linkedin.com/jobs/view "{keywords}" "{location}"',
              f'site:linkedin.com/jobs/view "{keywords}" remote']
             if remote else [f'site:linkedin.com/jobs/view "{keywords}" Casablanca'])
    out=[];seen=set()
    for q in queries:
        for title,url in results(q,15):
            if "/jobs/view/" not in url: continue
            company=company_from_linkedin_url(url)
            if not company: continue
            key=url.split("?")[0]
            if key in seen: continue
            seen.add(key);clean_title=title.split(" | ")[0].strip() or keywords
            if target(clean_title): out.append(job(clean_title,company,location,remote,"LinkedIn Search",key,"",title,search_type))
        time.sleep(1)
    return out

def linkedin():
    targets=[("WORLDWIDE_REMOTE","Remote / Worldwide",True),("MOROCCO_REMOTE","Morocco",True),("CASABLANCA_ONSITE","Casablanca, Morocco",False)]
    all_out=[];seen=set()
    for q in ROLES:
        for st,loc,remote in targets:
            print(f"[LinkedIn] {q} | {st}",flush=True)
            found=linkedin_guest_search(q,loc,remote,st)
            if len(found)<5: found += linkedin_web_fallback(q,loc,remote,st)
            for j in found:
                if j["id"] not in seen: seen.add(j["id"]); all_out.append(j)
            time.sleep(1.5)
    print(f"[LinkedIn] total={len(all_out)}",flush=True);return all_out

def parse_indeed(html,kind,remote):
    soup=BeautifulSoup(html,"html.parser");out=[];seen=set()
    for card in soup.select("div.job_seen_beacon,div.cardOutline,td.resultContent"):
        a=card.select_one("h2.jobTitle a,h2 a")
        if not a: continue
        title=clean(a.get_text(" ",strip=True));url=urljoin("https://ma.indeed.com",a.get("href","")).split("?")[0]
        ce=card.select_one("[data-testid='company-name'],.companyName");le=card.select_one("[data-testid='text-location'],.companyLocation")
        de=card.select_one("span.date,[data-testid='myJobsStateDate']");se=card.select_one(".job-snippet")
        age=clean(de.get_text(" ",strip=True) if de else "");desc=clean(se.get_text(" ",strip=True) if se else "")
        company=clean(ce.get_text(" ",strip=True) if ce else "")
        if not title or not company or not target(title,desc) or url in seen: continue
        if age and not fresh(age): continue
        seen.add(url);out.append(job(title,company,clean(le.get_text(" ",strip=True) if le else ""),remote,"Indeed",url,age,desc,kind))
    return out

def indeed():
    out=[];seen=set();targets=[("WORLDWIDE_REMOTE","Remote",True),("MOROCCO_REMOTE","Morocco",True),("CASABLANCA_ONSITE","Casablanca",False)]
    for q in ROLES:
        for kind,loc,remote in targets:
            print(f"[Indeed] {q} | {kind}",flush=True)
            u=f"https://ma.indeed.com/jobs?q={quote_plus(q)}&l={quote_plus(loc)}&fromage=1";h=fetch(u,15,0)
            if h:
                for j in parse_indeed(h,kind,remote):
                    if j["id"] not in seen: seen.add(j["id"]);out.append(j)
            time.sleep(1.5)
    print(f"[Indeed] total={len(out)}",flush=True);return out

def company_site(company):
    if not company or company=="Unknown": return None
    for q in [f'"{company}" official website',f'"{company}" careers jobs',f'"{company}" recruitment Casablanca']:
        for _,u in results(q):
            host=urlparse(u).netloc.lower().replace("www.","")
            if host and not any(host==b or host.endswith("."+b) for b in BLOCKED): return "https://"+host
    return None

def extract_emails(html):
    text=re.sub(r"\s*(?:\[at\]|\(at\)|\{at\})\s*","@",html,flags=re.I)
    text=re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*",".",text,flags=re.I)
    soup=BeautifulSoup(text,"html.parser");found=set(EMAIL_RE.findall(text))
    for a in soup.select('a[href^="mailto:"]'): found.add(a.get("href","")[7:].split("?")[0])
    return {e.lower().strip(" .;,<>\"'") for e in found if "@" in e and e.lower().split("@")[-1] not in BAD
            and not e.lower().startswith(("noreply@","no-reply@","privacy@","security@"))}

def enrich(j):
    site=j.get("company_site") or company_site(j.get("entreprise",""))
    if not site: return {"id":j["id"],"sheet":j.get("sheet"),"company_site":"","emails_rh":"","deep_status":"NO_SITE","email_status":"NOT_FOUND","email_source":""}
    p=urlparse(site);base=f"{p.scheme}://{p.netloc}";domain=p.netloc.lower().replace("www.","");es=set();pages=False
    for path in PATHS:
        h=fetch(base+path,10,0)
        if h: pages=True;es |= extract_emails(h)
    for q in [f'"{j.get("entreprise","")}" recruitment email',f'"{j.get("entreprise","")}" careers email',f'"{j.get("entreprise","")}" recrutement email']:
        for _,u in results(q,5):
            host=urlparse(u).netloc.lower().replace("www.","")
            if host==domain or host.endswith("."+domain):
                h=fetch(u,10,0)
                if h: pages=True;es |= extract_emails(h)
    def score(e):
        local,dom=e.split("@",1);s=0
        if dom==domain or dom.endswith("."+domain):s-=50
        if any(k in local for k in ("career","recruit","recrut","talent","jobs","hiring","hr")):s-=20
        if local in ("info","contact"):s+=5
        if local in ("support","sales","admin"):s+=20
        return s
    selected=sorted(es,key=score)[:5]
    return {"id":j["id"],"sheet":j.get("sheet"),"company_site":site,"emails_rh":" / ".join(selected),
            "deep_status":"DONE" if pages else "SITE_FOUND_NO_PAGES","email_status":"FOUND" if selected else ("NO_EMAIL" if pages else "NOT_FOUND"),
            "email_source":"Company website / public web"}

def spontaneous():
    out=[];seen=set()
    for q in ROLES:
        print(f"[Spontaneous] {q}",flush=True)
        for title,u in results(f'"{q}" Casablanca careers jobs recrutement',15):
            host=urlparse(u).netloc.lower().replace("www.","");low=(title+" "+u).lower()
            if (not host or any(host==b or host.endswith("."+b) for b in BLOCKED)
                or not any(k in low for k in ("career","careers","jobs","recruit","emploi","job"))): continue
            j=job(q+" — candidature spontanée",title or host,"Casablanca",False,"Company Website Search",u,"DIRECT","", "CASABLANCA_ONSITE")
            j["spontaneous"]="YES"
            if j["id"] not in seen:seen.add(j["id"]);out.append(j)
        time.sleep(1.5)
    print(f"[Spontaneous] total={len(out)}",flush=True);return out

def post(payload):
    if not WEBHOOK: raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    for i in range(3):
        r=webhook_request("POST",json=payload)
        if r is not None and 200 <= r.status_code < 300:
            data=_json_response(r)
            if data is not None:
                return data
            # A non-JSON Apps Script response can still mean the request executed.
            # Do not retry automatically: duplicate rows would be possible.
            return None
        time.sleep(2**i)
    return None

def post_jobs(jobs,sheet):
    if not jobs: return
    for j in jobs: j["sheet"]=sheet
    for i in range(0,len(jobs),25):
        batch=jobs[i:i+25]
        print(f"[Sheet] sending {len(batch)} jobs -> {sheet}",flush=True)
        post({"mode":"jobs","jobs":batch,"sheet":sheet})

def pending():
    if not WEBHOOK: raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    r=webhook_request("GET",params={"action":"pending","limit":200,"sheet":"ALL"})
    data=_json_response(r) if r is not None and 200 <= r.status_code < 300 else None
    if isinstance(data,dict) and isinstance(data.get("jobs"),list):
        print(f"[Pending GET] received {len(data['jobs'])} jobs",flush=True);return data["jobs"]
    print("[Pending GET] webhook did not return JSON jobs; deep will use zero safely.",flush=True)
    return []

def deep(limit):
    js=pending()[:limit];print(f"[DEEP] pending={len(js)} limit={limit}",flush=True);updates=[]
    for i,j in enumerate(js,1):
        print(f"[DEEP] {i}/{len(js)} {j.get('entreprise')} — {j.get('intitule')} [{j.get('sheet')}]",flush=True)
        try:
            u=enrich(j);u["sheet"]=j.get("sheet");updates.append(u)
        except Exception as e:
            updates.append({"id":j.get("id"),"sheet":j.get("sheet"),"deep_status":"ERROR","email_status":"ERROR","deep_error":str(e)[:250]})
        if len(updates)>=10: post({"mode":"enrich","updates":updates});updates=[]
    if updates: post({"mode":"enrich","updates":updates})
    print("[DEEP] complete",flush=True)

def scrape():
    if not WEBHOOK: raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    print("[START] scraper started",flush=True)
    aliases={"WORLDWIDE_REMOTE":"Worldwide Remote","MOROCCO_REMOTE":"Morocco Remote","CASABLANCA_ONSITE":"Casablanca Onsite"}
    seen=set();totals={v:0 for v in aliases.values()}
    for source_name,fn in [("LinkedIn",linkedin),("Indeed",indeed),("Spontaneous",spontaneous)]:
        jobs=fn();groups={v:[] for v in aliases.values()}
        for j in jobs:
            if j["id"] in seen: continue
            seen.add(j["id"]);tab=aliases.get(j.get("search_type"))
            if tab: groups[tab].append(j)
        for sheet,batch in groups.items():
            if batch: post_jobs(batch,sheet);totals[sheet]+=len(batch)
        print(f"[SOURCE DONE] {source_name}: {len(jobs)} discovered",flush=True)
    print(f"[DONE] total_unique={len(seen)} groups={totals}",flush=True)
    return seen

def main():
    p=argparse.ArgumentParser();p.add_argument("--mode",choices=["scrape","deep"],default="scrape")
    p.add_argument("--deep-limit",type=int,default=int(os.getenv("DEEP_LIMIT","200")));a=p.parse_args()
    scrape() if a.mode=="scrape" else deep(a.deep_limit)

if __name__=="__main__": main()
