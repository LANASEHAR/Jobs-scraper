"""Job discovery pipeline: LinkedIn + Indeed + public web + Casablanca company discovery."""
import argparse, hashlib, os, random, re, time
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup

WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "").strip()

ROLES = [
    "Customer Success Manager","Customer Success Specialist","Account Manager",
    "Customer Account Manager","Customer Support Specialist","Customer Experience Specialist",
    "Sales Development Representative","Business Development Representative",
    "Inside Sales Representative","Sales Executive","Account Executive",
    "Operations Coordinator","Business Operations Specialist","Administrative Coordinator",
    "Sales Operations Specialist","Commercial Operations Specialist","E-commerce Specialist",
    "Shopify Specialist","CRM Specialist","Back Office Specialist","Project Coordinator","Designer"
]
EXCLUDE = ["senior","sr.","sr ","lead","principal","director","vp ","vice president","head of ",
           "chief","staff","architect","internship","intern ","doctor","nurse","software engineer",
           "developer","data scientist","machine learning","devops","lawyer","accountant",
           "physician","warehouse worker","driver"]
UA = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/139.0 Safari/537.36"
]
S = requests.Session()
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD = {"example.com","sentry.io","schema.org","google.com","facebook.com","linkedin.com"}
BLOCKED = {"linkedin.com","facebook.com","instagram.com","twitter.com","x.com","indeed.com",
           "glassdoor.com","crunchbase.com","wikipedia.org","duckduckgo.com","google.com",
           "bing.com","youtube.com"}
PATHS = ["","/contact","/contact-us","/careers","/career","/jobs","/join-us","/work-with-us",
         "/recruitment","/human-resources","/hr","/about","/en/contact","/en/careers","/fr/contact"]

def now(): return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def clean(x): return re.sub(r"\s+"," ",str(x or "")).strip()
def hdr(): return {"User-Agent":random.choice(UA),"Accept-Language":"en-US,en;q=0.9,fr;q=0.8",
                   "Accept":"text/html,application/xhtml+xml"}

def fetch(url, timeout=15, retries=1):
    for i in range(retries+1):
        try:
            r=S.get(url,headers=hdr(),timeout=timeout,allow_redirects=True)
            if r.status_code==200 and r.text:return r.text
            print(f"[HTTP] {r.status_code} {url}",flush=True)
        except requests.RequestException as e: print(f"[HTTP] error {url}: {e}",flush=True)
        if i<retries: time.sleep(1+i)
    return None

def age_hours(t):
    t=clean(t).lower()
    if any(x in t for x in ["just posted","today","il y a quelques","il y a 1 heure","new"]): return 0
    m=re.search(r"(\d+)\s*(?:hours?|heures?)\b",t)
    if m:return int(m.group(1))
    m=re.search(r"(\d+)\s*(?:minutes?|mins?)\b",t)
    if m:return int(m.group(1))/60
    m=re.search(r"(\d+)\s*(?:days?|jours?)\b",t)
    if m:return int(m.group(1))*24
    return None
def fresh(t):
    h=age_hours(t); return h is not None and h<=24
def target(t,d=""): return not any(x in (t+" "+d).lower() for x in EXCLUDE)
def jid(source,url,title): return "job_"+hashlib.sha256((source+"|"+url+"|"+title).encode()).hexdigest()[:18]

def job(title,company,loc,remote,source,url,age="",desc="",kind=None):
    return {"id":jid(source,url,title),"date_detection":now(),"statut":"NEW","role_cible":title,
            "intitule":title,"entreprise":company or "Unknown","lieu":loc or ("Remote / Worldwide" if remote else "Casablanca"),
            "remote":bool(remote),"source":source,"lien":url,"company_site":"","emails_rh":"",
            "deep_status":"PENDING","fit_score":"","fit_reasons":"","salary":"","description":clean(desc),
            "posted_age":clean(age),"posted_within_24h":"YES" if fresh(age) else "UNKNOWN",
            "search_type":kind or ("REMOTE" if remote else "CASABLANCA"),"email_status":"PENDING",
            "email_source":"","spontaneous":"NO"}

def parse_linkedin(html,remote,search_type):
    soup=BeautifulSoup(html,"html.parser");out=[];seen=set()
    for card in soup.select("li.base-card,li.jobs-search__results-list,div.base-card"):
        a=card.select_one('a[href*="/jobs/view/"]')
        if not a:continue
        href=a.get("href","").split("?")[0]
        m=re.search(r"/jobs/view/(?:[^/]+-)?(\d+)",href)
        if not m or m.group(1) in seen:continue
        seen.add(m.group(1))
        title=clean((card.select_one("h3") or a).get_text(" ",strip=True))
        ce=card.select_one("h4,.base-search-card__subtitle,.hidden-nested-link")
        le=card.select_one(".job-search-card__location")
        te=card.select_one("time,.job-search-card__listdate,.job-search-card__listdate--new")
        company=clean(ce.get_text(" ",strip=True) if ce else "")
        loc=clean(le.get_text(" ",strip=True) if le else "")
        age=clean(te.get_text(" ",strip=True) if te else "")
        if title and company and target(title) and (fresh(age) or not age):
            out.append(job(title,company,loc,remote,"LinkedIn",urljoin("https://www.linkedin.com",href),age,kind=search_type))
    return out

def linkedin_guest_search(keywords,location,remote,search_type):
    out=[];seen=set()
    for start in [0,25,50,75,100]:
        params=f"?keywords={quote_plus(keywords)}&location={quote_plus(location)}&f_TPR=r86400&start={start}"
        if remote:params+="&f_WT=2"
        h=fetch("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"+params,15,1)
        if not h:break
        found=parse_linkedin(h,remote,search_type)
        if not found and start>0:break
        for j in found:
            if j["id"] not in seen:seen.add(j["id"]);out.append(j)
    return out

def results(q,limit=10):
    h=fetch("https://html.duckduckgo.com/html/?q="+quote_plus(q),15,1)
    if not h:return []
    return [(clean(a.get_text(" ",strip=True)),a.get("href",""))
            for a in BeautifulSoup(h,"html.parser").select("a.result__a")[:limit]
            if a.get("href","").startswith("http")]

def company_from_linkedin_url(url):
    m=re.search(r"-at-([^-]+(?:-[^-]+){0,8})-(\d{6,})/?$",url)
    if not m:return ""
    return clean(m.group(1).replace("-"," ")).title()

def linkedin_web_fallback(keywords,location,remote,search_type):
    queries=[
        f'site:linkedin.com/jobs/view "{keywords}" "{location}"',
        f'site:linkedin.com/jobs/view "{keywords}" remote',
        f'site:linkedin.com/jobs/view "{keywords}" Morocco'
    ] if remote else [f'site:linkedin.com/jobs/view "{keywords}" Casablanca']
    out=[];seen=set()
    for q in queries:
        for title,url in results(q,15):
            if "/jobs/view/" not in url:continue
            company=company_from_linkedin_url(url)
            if not company:continue
            key=url.split("?")[0]
            if key in seen:continue
            seen.add(key)
            clean_title=title.split(" | ")[0].strip() or keywords
            if target(clean_title):
                out.append(job(clean_title,company,location,remote,"LinkedIn Search",key,"",title,search_type))
    return out

def linkedin():
    out=[];seen=set()
    targets=[("WORLDWIDE_REMOTE","Remote / Worldwide",True),("MOROCCO_REMOTE","Morocco",True),
             ("CASABLANCA_ONSITE","Casablanca, Morocco",False)]
    for q in ROLES:
        for st,loc,remote in targets:
            print(f"[LinkedIn] {q} | {st}",flush=True)
            found=linkedin_guest_search(q,loc,remote,st)
            if not found:found=linkedin_web_fallback(q,loc,remote,st)
            for j in found:
                if j["id"] not in seen:seen.add(j["id"]);out.append(j)
    print(f"[LinkedIn] total={len(out)}",flush=True);return out

def parse_indeed(html,kind,remote):
    soup=BeautifulSoup(html,"html.parser");out=[];seen=set()
    for card in soup.select("div.job_seen_beacon,div.cardOutline,td.resultContent"):
        a=card.select_one("h2.jobTitle a,h2 a")
        if not a:continue
        title=clean(a.get_text(" ",strip=True))
        url=urljoin("https://ma.indeed.com",a.get("href","")).split("?")[0]
        ce=card.select_one("[data-testid='company-name'],.companyName")
        le=card.select_one("[data-testid='text-location'],.companyLocation")
        de=card.select_one("span.date,[data-testid='myJobsStateDate']")
        se=card.select_one(".job-snippet")
        age=clean(de.get_text(" ",strip=True) if de else "")
        desc=clean(se.get_text(" ",strip=True) if se else "")
        company=clean(ce.get_text(" ",strip=True) if ce else "")
        if not title or not company or not target(title,desc) or url in seen:continue
        if age and not fresh(age):continue
        seen.add(url)
        out.append(job(title,company,clean(le.get_text(" ",strip=True) if le else ""),
                       remote,"Indeed",url,age,desc,kind))
    return out

def indeed():
    out=[];seen=set()
    targets=[("WORLDWIDE_REMOTE","Remote",True),("MOROCCO_REMOTE","Morocco",True),("CASABLANCA_ONSITE","Casablanca",False)]
    for q in ROLES:
        for kind,loc,remote in targets:
            print(f"[Indeed] {q} | {kind}",flush=True)
            u=f"https://ma.indeed.com/jobs?q={quote_plus(q)}&l={quote_plus(loc)}&fromage=1"
            h=fetch(u,15,1)
            if h:
                for j in parse_indeed(h,kind,remote):
                    if j["id"] not in seen:seen.add(j["id"]);out.append(j)
    print(f"[Indeed] total={len(out)}",flush=True);return out

def company_site(company):
    if not company or company=="Unknown":return None
    for q in [f'"{company}" official website',f'"{company}" careers jobs',f'"{company}" recruitment Casablanca']:
        for _,u in results(q):
            host=urlparse(u).netloc.lower().replace("www.","")
            if host and not any(host==b or host.endswith("."+b) for b in BLOCKED):return "https://"+host
    return None

def extract_emails(html):
    text=re.sub(r"\s*(?:\[at\]|\(at\)|\{at\})\s*","@",html,flags=re.I)
    text=re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*",".",text,flags=re.I)
    soup=BeautifulSoup(text,"html.parser");found=set(EMAIL_RE.findall(text))
    for a in soup.select('a[href^="mailto:"]'):found.add(a.get("href","")[7:].split("?")[0])
    return {e.lower().strip(" .;,<>\"'") for e in found if "@" in e and e.lower().split("@")[-1] not in BAD
            and not e.lower().startswith(("noreply@","no-reply@","privacy@","security@"))}

def enrich(j):
    site=j.get("company_site") or company_site(j.get("entreprise",""))
    if not site:return {"id":j["id"],"sheet":j.get("sheet"),"company_site":"","emails_rh":"",
                        "deep_status":"NO_SITE","email_status":"NOT_FOUND","email_source":""}
    p=urlparse(site);base=f"{p.scheme}://{p.netloc}";domain=p.netloc.lower().replace("www.","");es=set();pages=False
    for path in PATHS:
        h=fetch(base+path,10,1)
        if h:pages=True;es|=extract_emails(h)
    for q in [f'"{j.get("entreprise","")}" recruitment email',f'"{j.get("entreprise","")}" careers email',
              f'"{j.get("entreprise","")}" recrutement email']:
        for _,u in results(q,5):
            host=urlparse(u).netloc.lower().replace("www.","")
            if host==domain or host.endswith("."+domain):
                h=fetch(u,10,1)
                if h:pages=True;es|=extract_emails(h)
    def score(e):
        local,dom=e.split("@",1);s=0
        if dom==domain or dom.endswith("."+domain):s-=50
        if any(k in local for k in ("career","recruit","recrut","talent","jobs","hiring","hr")):s-=20
        if local in ("info","contact"):s+=5
        if local in ("support","sales","admin"):s+=20
        return s
    selected=sorted(es,key=score)[:5]
    return {"id":j["id"],"sheet":j.get("sheet"),"company_site":site,"emails_rh":" / ".join(selected),
            "deep_status":"DONE" if pages else "SITE_FOUND_NO_PAGES",
            "email_status":"FOUND" if selected else ("NO_EMAIL" if pages else "NOT_FOUND"),
            "email_source":"Company website / public web"}

def spontaneous():
    out=[];seen=set()
    for q in ROLES:
        print(f"[Spontaneous] {q}",flush=True)
        for title,u in results(f'"{q}" Casablanca careers jobs recrutement',15):
            host=urlparse(u).netloc.lower().replace("www.","");low=(title+" "+u).lower()
            if not host or any(host==b or host.endswith("."+b) for b in BLOCKED) or not any(k in low for k in ("career","careers","jobs","recruit","emploi","job")):continue
            j=job(q+" — candidature spontanée",title or host,"Casablanca",False,"Company Website Search",u,"DIRECT","", "CASABLANCA_ONSITE")
            j["spontaneous"]="YES"
            if j["id"] not in seen:seen.add(j["id"]);out.append(j)
    print(f"[Spontaneous] total={len(out)}",flush=True);return out

def post(payload):
    if not WEBHOOK:raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    for i in range(3):
        try:
            r=requests.post(WEBHOOK,json=payload,timeout=(10,60));r.raise_for_status()
            print(f"[Sheet] {r.status_code} {r.text[:300]}",flush=True);return True
        except requests.RequestException as e:
            print(f"[Sheet] attempt {i+1}: {e}",flush=True);time.sleep(2**i)
    return False

def pending():
    try:
        r=requests.get(WEBHOOK,params={"action":"pending","limit":200},timeout=(10,60));r.raise_for_status()
        return r.json().get("jobs",[])
    except Exception as e:
        print(f"[Pending GET] {e}",flush=True)
        try:
            r=requests.post(WEBHOOK,json={"mode":"pending","limit":200},timeout=(10,60));r.raise_for_status()
            return r.json().get("jobs",[])
        except Exception as e2:
            print(f"[Pending POST] {e2}",flush=True);return []

def deep(limit):
    js=pending()[:limit];print(f"[DEEP] pending={len(js)} limit={limit}",flush=True);updates=[]
    for i,j in enumerate(js,1):
        print(f"[DEEP] {i}/{len(js)} {j.get('entreprise')} — {j.get('intitule')} [{j.get('sheet')}]",flush=True)
        try:u=enrich(j);u["sheet"]=j.get("sheet");updates.append(u)
        except Exception as e:updates.append({"id":j.get("id"),"sheet":j.get("sheet"),"deep_status":"ERROR","email_status":"ERROR","deep_error":str(e)[:250]})
        if len(updates)>=10:post({"mode":"enrich","updates":updates});updates=[]
    if updates:post({"mode":"enrich","updates":updates})
    print("[DEEP] complete",flush=True)

def scrape():
    if not WEBHOOK:raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    print("[START] scraper started",flush=True)
    alljobs=linkedin()+indeed()+spontaneous();seen=set();unique=[]
    for j in alljobs:
        if j["id"] not in seen:seen.add(j["id"]);unique.append(j)
    aliases={"WORLDWIDE_REMOTE":"Worldwide Remote","MOROCCO_REMOTE":"Morocco Remote","CASABLANCA_ONSITE":"Casablanca Onsite"}
    groups={v:[] for v in aliases.values()}
    for j in unique:
        tab=aliases.get(j.get("search_type"))
        if tab:groups[tab].append(j)
    for sheet,jobs in groups.items():
        print(f"[Sheet] {sheet}: {len(jobs)} jobs",flush=True)
        if jobs:post({"mode":"jobs","jobs":jobs,"sheet":sheet})
    print(f"[DONE] total={len(unique)} groups={ {k:len(v) for k,v in groups.items()} }",flush=True)
    return unique

def main():
    p=argparse.ArgumentParser();p.add_argument("--mode",choices=["scrape","deep"],default="scrape")
    p.add_argument("--deep-limit",type=int,default=int(os.getenv("DEEP_LIMIT","200")))
    a=p.parse_args(); scrape() if a.mode=="scrape" else deep(a.deep_limit)

if __name__=="__main__":main()
