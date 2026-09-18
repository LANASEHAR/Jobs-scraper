"""LinkedIn + Indeed + Casablanca + company email job pipeline."""
import argparse,hashlib,os,random,re,time
from datetime import datetime,timezone
from urllib.parse import quote_plus,urljoin,urlparse
import requests
from bs4 import BeautifulSoup

WEBHOOK=os.getenv("GOOGLE_SHEET_WEBHOOK_URL","").strip()
ROLES=["Customer Success Manager","Customer Success Specialist","Account Manager","Customer Account Manager","Customer Support Specialist","Customer Experience Specialist","Sales Development Representative","Business Development Representative","Inside Sales Representative","Sales Executive","Account Executive","Operations Coordinator","Business Operations Specialist","Administrative Coordinator","Sales Operations Specialist","Commercial Operations Specialist","E-commerce Specialist","Shopify Specialist","CRM Specialist","Back Office Specialist","Project Coordinator","Junior UX UI Designer","Junior UX Designer","Junior UI Designer"]
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

def parse_linkedin(html,remote):
    soup=BeautifulSoup(html,"html.parser");out=[];seen=set()
    for a in soup.select('a[href*="/jobs/view/"]'):
        href=a.get("href","").split("?")[0];m=re.search(r"/jobs/view/(?:[^/]+-)?(\\d+)",href)
        if not m or m.group(1) in seen:continue
        seen.add(m.group(1));card=a.find_parent(class_=re.compile("base-card|job-search-card|result-card")) or a.parent
        title=clean(a.get_text(" ",strip=True));company=loc=age=""
        if card:
            e=card.select_one(".base-search-card__subtitle,.hidden-nested-link");l=card.select_one(".job-search-card__location");d=card.select_one("time,.job-search-card__listdate,.job-search-card__listdate--new")
            company=clean(e.get_text(" ",strip=True) if e else "");loc=clean(l.get_text(" ",strip=True) if l else "");age=clean(d.get_text(" ",strip=True) if d else "")
        if title and target(title) and (not age or fresh(age)):
            out.append(job(title,company,loc,remote,"LinkedIn",urljoin("https://www.linkedin.com",href),age,kind="REMOTE" if remote else "CASABLANCA"))
    return out

def linkedin():
    out=[];seen=set()
    for q in ROLES:
        for remote in (True,False):
            if remote:u=f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(q)}&f_TPR=r86400&f_WT=2"
            else:u=f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(q)}&location=Casablanca%2C%20Morocco&f_TPR=r86400"
            h=fetch(u)
            if not h: print("[LinkedIn] unavailable:",q,remote);continue
            for j in parse_linkedin(h,remote):
                if j["id"] not in seen:seen.add(j["id"]);out.append(j)
    return out

def parse_indeed(html,kind):
    soup=BeautifulSoup(html,"html.parser");out=[];seen=set()
    for card in soup.select("div.job_seen_beacon,div.cardOutline,td.resultContent"):
        a=card.select_one("h2.jobTitle a,h2 a")
        if not a:continue
        title=clean(a.get_text(" ",strip=True));url=urljoin("https://ma.indeed.com",a.get("href",""))
        c=card.select_one("[data-testid='company-name'],.companyName");l=card.select_one("[data-testid='text-location'],.companyLocation");d=card.select_one("span.date,[data-testid='myJobsStateDate']");s=card.select_one(".job-snippet")
        age=clean(d.get_text(" ",strip=True) if d else card.get_text(" ",strip=True));desc=clean(s.get_text(" ",strip=True) if s else "")
        if not title or not target(title,desc) or (age and not fresh(age)):continue
        key=url.split("?")[0]
        if key in seen:continue
        seen.add(key);out.append(job(title,clean(c.get_text(" ",strip=True) if c else ""),clean(l.get_text(" ",strip=True) if l else ""),kind=="REMOTE","Indeed",key,age,desc,kind))
    return out

def indeed():
    out=[];seen=set()
    for q in ROLES:
        for kind,loc in [("REMOTE","Remote"),("CASABLANCA","Casablanca")]:
            u=f"https://ma.indeed.com/jobs?q={quote_plus(q)}&l={quote_plus(loc)}&fromage=1"
            h=fetch(u,15,2)
            if not h:continue
            for j in parse_indeed(h,kind):
                if j["id"] not in seen:seen.add(j["id"]);out.append(j)
    return out

def results(q,limit=8):
    h=fetch("https://html.duckduckgo.com/html/?q="+quote_plus(q),15,1)
    if not h:return []
    return [(clean(a.get_text(" ",strip=True)),a.get("href","")) for a in BeautifulSoup(h,"html.parser").select("a.result__a")[:limit] if a.get("href","").startswith("http")]

def company_site(company):
    if not company or company=="Unknown":return None
    for q in [f'"{company}" official website',f'"{company}" careers jobs',f'"{company}" recruitment Casablanca']:
        for _,u in results(q):
            host=urlparse(u).netloc.lower().replace("www.","")
            if host and not any(host==b or host.endswith("."+b) for b in BLOCKED):return "https://"+host
    return None

def emails(html):
    text=re.sub(r"\\s*(?:\\[at\\]|\\(at\\)|\\{at\\})\\s*","@",html,flags=re.I);text=re.sub(r"\\s*(?:\\[dot\\]|\\(dot\\)|\\{dot\\})\\s*",".",text,flags=re.I)
    soup=BeautifulSoup(text,"html.parser");found=set(EMAIL_RE.findall(text))
    for a in soup.select('a[href^="mailto:"]'):found.add(a.get("href","")[7:].split("?")[0])
    return {e.lower().strip(" .;,<>\\\"'") for e in found if "@" in e and e.lower().split("@")[-1] not in BAD and not e.lower().startswith(("noreply@","no-reply@","privacy@","security@"))}

def enrich(j):
    site=j.get("company_site") or company_site(j.get("entreprise",""))
    if not site:return {"id":j["id"],"company_site":"","emails_rh":"","deep_status":"NO_SITE","email_status":"NOT_FOUND","email_source":""}
    p=urlparse(site);base=f"{p.scheme}://{p.netloc}";domain=p.netloc.lower().replace("www.","");es=set();pages=False
    for path in PATHS:
        h=fetch(base+path,10,1)
        if h:pages=True;es|=emails(h)
    for q in [f'"{j.get("entreprise","")}" recruitment email',f'"{j.get("entreprise","")}" careers email',f'"{j.get("entreprise","")}" recrutement email']:
        for _,u in results(q,5):
            host=urlparse(u).netloc.lower().replace("www.","")
            if host==domain or host.endswith("."+domain):
                h=fetch(u,10,1)
                if h:pages=True;es|=emails(h)
    def score(e):
        local,dom=e.split("@",1);s=0
        if dom==domain or dom.endswith("."+domain):s-=50
        if any(k in local for k in ("career","recruit","recrut","talent","jobs","hiring","hr")):s-=20
        if local in ("info","contact"):s+=5
        if local in ("support","sales","admin"):s+=20
        return s
    selected=sorted(es,key=score)[:5]
    return {"id":j["id"],"company_site":site,"emails_rh":" / ".join(selected),"deep_status":"DONE" if pages else "SITE_FOUND_NO_PAGES","email_status":"FOUND" if selected else ("NO_EMAIL" if pages else "NOT_FOUND"),"email_source":"Company website / public web","spontaneous":"YES" if selected and any(x in j.get("lien","").lower() for x in ("career","jobs","recruit")) else j.get("spontaneous","NO")}

def spontaneous():
    out=[];seen=set()
    for q in ROLES:
        for title,u in results(f'"{q}" Casablanca careers jobs recrutement',10):
            host=urlparse(u).netloc.lower().replace("www.","")
            low=(title+" "+u).lower()
            if not host or any(host==b or host.endswith("."+b) for b in BLOCKED) or not any(k in low for k in ("career","careers","jobs","recruit","emploi","job")):continue
            t=q+" — candidature spontanée";j=job(t,title or host,"Casablanca",False,"Company Website Search",u,"DIRECT","", "SPONTANEOUS_CASABLANCA");j["spontaneous"]="YES"
            if j["id"] not in seen:seen.add(j["id"]);out.append(j)
    return out

def post(payload):
    if not WEBHOOK:raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    for i in range(3):
        try:
            r=requests.post(WEBHOOK,json=payload,timeout=(10,60));r.raise_for_status();print("[Sheet]",r.status_code);return True
        except requests.RequestException as e:
            print("[Sheet]",i+1,e);time.sleep(2**i)
    return False

def pending():
    try:
        r=requests.get(WEBHOOK,params={"action":"pending","limit":200},timeout=(10,60));r.raise_for_status();return r.json().get("jobs",[])
    except Exception:
        try:
            r=requests.post(WEBHOOK,json={"mode":"pending","limit":200},timeout=(10,60));r.raise_for_status();return r.json().get("jobs",[])
        except Exception:return []

def deep(limit):
    js=pending()[:limit];updates=[]
    for i,j in enumerate(js,1):
        print(f"[DEEP] {i}/{len(js)} {j.get('entreprise')} — {j.get('intitule')}")
        try:updates.append(enrich(j))
        except Exception as e:updates.append({"id":j.get("id"),"deep_status":"ERROR","email_status":"ERROR","deep_error":str(e)[:250]})
        if len(updates)>=10:post({"mode":"enrich","updates":updates});updates=[]
    if updates:post({"mode":"enrich","updates":updates})

def scrape():
    alljobs=linkedin()+indeed()+spontaneous();seen=set();unique=[]
    for j in alljobs:
        if j["id"] not in seen:seen.add(j["id"]);unique.append(j)
    if unique:post({"mode":"jobs","jobs":unique})
    print("DONE:",len(unique),"records")
    return unique

def main():
    p=argparse.ArgumentParser();p.add_argument("--mode",choices=["scrape","deep"],default="scrape");p.add_argument("--deep-limit",type=int,default=int(os.getenv("DEEP_LIMIT","200")));a=p.parse_args()
    if a.mode=="scrape":scrape()
    else:deep(a.deep_limit)
if __name__=="__main__":main()
