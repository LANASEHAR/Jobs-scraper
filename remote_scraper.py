"""LinkedIn + Indeed + Casablanca + company email job pipeline."""
import argparse,hashlib,os,random,re,time
from datetime import datetime,timezone
from urllib.parse import quote_plus,urljoin,urlparse
import requests
from bs4 import BeautifulSoup

WEBHOOK=os.getenv("GOOGLE_SHEET_WEBHOOK_URL","").strip()
ROLES=["Customer Success Manager","Customer Success Specialist","Account Manager","Customer Account Manager","Customer Support Specialist","Customer Experience Specialist","Sales Development Representative","Business Development Representative","Inside Sales Representative","Sales Executive","Account Executive","Operations Coordinator","Business Operations Specialist","Administrative Coordinator","Sales Operations Specialist","Commercial Operations Specialist","E-commerce Specialist","Shopify Specialist","CRM Specialist","Back Office Specialist","Project Coordinator","Designer"]
EXCLUDE=["senior","sr.","sr ","lead","principal","director","vp ","vice president","head of","chief","staff","architect","internship","intern ","doctor","nurse","software engineer","developer","data scientist","machine learning","devops","lawyer","accountant","physician","warehouse worker","driver"]
UA=["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36","Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/139.0 Safari/537.36"]
S=requests.Session()
EMAIL_RE=re.compile(r"[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\.[A-Za-z]{2,}")
BAD={"example.com","sentry.io","schema.org","google.com","facebook.com","linkedin.com"}
BLOCKED={"linkedin.com","facebook.com","instagram.com","twitter.com","x.com","indeed.com","glassdoor.com","crunchbase.com","wikipedia.org","duckduckgo.com","google.com","bing.com","youtube.com"}
PATHS=["","/contact","/contact-us","/careers","/career","/jobs","/join-us","/work-with-us","/recruitment","/human-resources","/hr","/about","/en/contact","/en/careers","/fr/contact"]

def now(): return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def clean(x): return re.sub(r"\\s+"," ",str(x or "")).strip()
def hdr(): return {"User-Agent":random.choice(UA),"Accept-Language":"en-US,en;q=0.9,fr;q=0.8","Accept":"text/html,application/xhtml+xml"}
def fetch(url,timeout=15,retries=1):
    for i in range(retries+1):
        try:
            r=S.get(url,headers=hdr(),timeout=timeout,allow_redirects=True)
            if r.status_code==200 and r.text:return r.text
        except requests.RequestException: pass
        time.sleep(1+i)
    return None
def age_hours(t):
    t=clean(t).lower()
    if any(x in t for x in ["just posted","today","il y a quelques","il y a 1 heure"]): return 0
    m=re.search(r"(\\d+)\\s*(?:hours?|heures?)\\b",t)
    if m:return int(m.group(1))
    m=re.search(r"(\\d+)\\s*(?:minutes?|mins?)\\b",t)
    if m:return int(m.group(1))/60
    m=re.search(r"(\\d+)\\s*(?:days?|jours?)\\b",t)
    if m:return int(m.group(1))*24
    return None
def fresh(t): 
    h=age_hours(t)
    return h is not None and h<=24
def target(t,d=""): return not any(x in (t+" "+d).lower() for x in EXCLUDE)
def jid(source,url,title): return "job_"+hashlib.sha256((source+"|"+url+"|"+title).encode()).hexdigest()[:18]
def job(title,company,loc,remote,source,url,age="",desc="",kind=None):
    return {"id":jid(source,url,title),"date_detection":now(),"statut":"NEW","role_cible":title,"intitule":title,"entreprise":company or "Unknown","lieu":loc or ("Remote / Worldwide" if remote else "Casablanca"),"remote":bool(remote),"source":source,"lien":url,"company_site":"","emails_rh":"","deep_status":"PENDING","fit_score":"","fit_reasons":"","salary":"","description":clean(desc),"posted_age":clean(age),"posted_within_24h":"YES" if fresh(age) else "UNKNOWN","search_type":kind or ("REMOTE" if remote else "CASABLANCA"),"email_status":"PENDING","email_source":"","spontaneous":"NO"}

def parse_linkedin(html,remote,search_type):
    soup=BeautifulSoup(html,"html.parser");out=[];seen=set()
    for card in soup.select("li.base-card,li.jobs-search__results-list,div.base-card"):
        a=card.select_one('a[href*="/jobs/view/"]')
        if not a:continue
        href=a.get("href","").split("?")[0];m=re.search(r"/jobs/view/(?:[^/]+-)?(\\d+)",href)
        if not m or m.group(1) in seen:continue
        seen.add(m.group(1))
        title=clean((card.select_one("h3") or a).get_text(" ",strip=True))
        company=clean((card.select_one("h4") or card.select_one(".base-search-card__subtitle") or card.select_one(".hidden-nested-link") or "").get_text(" ",strip=True) if card.select_one("h4,.base-search-card__subtitle,.hidden-nested-link") else "")
        loc=clean((card.select_one(".job-search-card__location") or "").get_text(" ",strip=True) if card.select_one(".job-search-card__location") else "")
        t=card.select_one("time,.job-search-card__listdate,.job-search-card__listdate--new")
        age=clean(t.get_text(" ",strip=True) if t else "")
        if title and target(title) and fresh(age):
            out.append(job(title,company,loc,remote,"LinkedIn",urljoin("https://www.linkedin.com",href),age,kind=search_type))
    return out

def linkedin_guest_search(keywords,location,remote,search_type):
    params=f"?keywords={quote_plus(keywords)}&location={quote_plus(location)}&f_TPR=r86400&start=0"
    if remote: params += "&f_WT=2"
    url="https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"+params
    h=fetch(url,15,2)
    if not h:return []
    return parse_linkedin(h,remote,search_type)

def linkedin_web_fallback(keywords,location,remote,search_type):
    q=f'site:linkedin.com/jobs/view "{keywords}" "{location}" "hours ago"'
    if remote:q=f'site:linkedin.com/jobs/view "{keywords}" remote "hours ago"'
    out=[]
    for title,url in results(q,10):
        if "/jobs/view/" not in url:continue
        out.append(job(keywords,"",location,remote,"LinkedIn Search",url,"",title,search_type))
    return out

def linkedin():
    out=[];seen=set()
    targets=[("WORLDWIDE_REMOTE","Remote / Worldwide",True),("MOROCCO_REMOTE","Morocco",True),("CASABLANCA_ONSITE","Casablanca, Morocco",False)]
    for q in ROLES:
        for search_type,location,remote in targets:
            found=linkedin_guest_search(q,location,remote,search_type)
            if not found:found=linkedin_web_fallback(q,location,remote,search_type)
            for j in found:
                if j["id"] not in seen:seen.add(j["id"]);out.append(j)
    return out
def indeed():
    out=[];seen=set()
    targets=[("WORLDWIDE_REMOTE","Remote",True),("MOROCCO_REMOTE","Morocco",True),("CASABLANCA_ONSITE","Casablanca",False)]
    for q in ROLES:
        for kind,loc,remote in targets:
            u=f"https://ma.indeed.com/jobs?q={quote_plus(q)}&l={quote_plus(loc)}&fromage=1"
            h=fetch(u,15,2)
            if not h:continue
            for j in parse_indeed(h,kind):
                j["remote"]=remote;j["search_type"]=kind
                if j["id"] not in seen:seen.add(j["id"]);out.append(j)
    return out


