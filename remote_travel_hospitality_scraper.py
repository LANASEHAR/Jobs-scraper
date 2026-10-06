#!/usr/bin/env python3
"""Remote Travel/Hospitality job + company-contact discovery for Morocco.

Separate pipeline: writes only to the Google Sheet tab "Remote Travel Hospitality".
It never invents email addresses. A record is kept when it has a public company
email OR a real job URL. Remote eligibility is classified conservatively.
"""
import hashlib, os, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

WEBHOOK=os.getenv("GOOGLE_SHEET_WEBHOOK_URL","").strip()
S=requests.Session()
S.headers.update({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36","Accept-Language":"en-US,en;q=0.9,fr;q=0.8"})
TIMEOUT=10
MAX_RUNTIME=42*60
START=time.monotonic()
EMAIL_RE=re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}")
BAD_DOMAINS={"linkedin.com","facebook.com","instagram.com","twitter.com","x.com","google.com","example.com","sentry.io"}
BAD_LOCAL={"noreply","no-reply","donotreply","mailer-daemon","postmaster"}
BLOCKED_JOB_HOSTS={"linkedin.com","indeed.com","glassdoor.com"}

ROLE_QUERIES=[
 "customer success manager travel","customer success travel tech","account manager travel","account manager hospitality",
 "key account manager travel","travel account manager","partnerships manager travel","business development travel",
 "customer experience travel","travel operations","reservations manager remote","booking operations remote",
 "hotel account manager","hospitality customer success","travel support consultant","commercial operations travel",
 "sales manager travel tech","B2B account manager hospitality","ecommerce manager travel","marketplace manager travel"
]
REGION_QUERIES=[
 "remote Morocco travel hospitality jobs","remote from Morocco travel tech jobs","work from Morocco travel hospitality",
 "Morocco remote customer success travel","Morocco remote account manager travel","Morocco remote hospitality jobs",
 "MENA remote travel customer success","EMEA remote travel account manager"
]
JOB_DOMAINS=["linkedin.com/jobs","indeed.com","greenhouse.io","jobs.ashbyhq.com","jobs.lever.co","workable.com","smartrecruiters.com","job-boards.greenhouse.io","wellfound.com/jobs","remoteok.com","weworkremotely.com","remotive.com","jobgether.com","workingnomads.com","himalayas.app"]
COMPANY_DOMAINS=["linkedin.com/company","crunchbase.com","travelweekly.com"]

def budget_ok(): return time.monotonic()-START<MAX_RUNTIME
def clean(x): return re.sub(r"\s+"," ",str(x or "")).strip()
def host(url):
    try:
        h=urlparse(url).netloc.lower().split(":")[0]
        return h[4:] if h.startswith("www.") else h
    except: return ""
def base_domain(h):
    p=host(h).split(".")
    return ".".join(p[-2:]) if len(p)>=2 else host(h)
def valid_email(e):
    e=clean(e).lower().strip(" <>.,;:'\"()[]")
    if not EMAIL_RE.fullmatch(e): return False
    local,dom=e.rsplit("@",1)
    return local not in BAD_LOCAL and dom not in BAD_DOMAINS
def extract_emails(html):
    if not html:return set()
    text=re.sub(r"\s*(?:\[|\(|\{)\s*(?:at|ät)\s*(?:\]|\)|\})\s*","@",html,flags=re.I)
    text=re.sub(r"\s*(?:\[|\(|\{)\s*(?:dot|punkt)\s*(?:\]|\)|\})\s*",".",text,flags=re.I)
    out={e.lower() for e in EMAIL_RE.findall(text) if valid_email(e)}
    try:
        soup=BeautifulSoup(html,"html.parser")
        for a in soup.select('a[href^="mailto:"]'):
            e=a.get("href","")[7:].split("?",1)[0]
            if valid_email(e):out.add(e.lower())
    except:pass
    return out
def fetch(url):
    if not budget_ok():return ""
    try:
        r=S.get(url,timeout=TIMEOUT,allow_redirects=True)
        return r.text if r.status_code==200 else ""
    except requests.RequestException:return ""
def search(q,limit=12):
    results=[];seen=set()
    for engine in ["https://www.bing.com/search?q=","https://html.duckduckgo.com/html/?q="]:
        if len(results)>=limit or not budget_ok():break
        try:
            r=S.get(engine+quote_plus(q),timeout=8)
            if r.status_code!=200:continue
            soup=BeautifulSoup(r.text,"html.parser")
            sels=["li.b_algo h2 a[href]"] if "bing" in engine else ["a.result__a[href]","a.result-link[href]"]
            for sel in sels:
                for a in soup.select(sel):
                    u=a.get("href","")
                    if not u.startswith("http") or u in seen:continue
                    seen.add(u);results.append((clean(a.get_text(" ",strip=True)),u))
                    if len(results)>=limit:break
                if len(results)>=limit:break
        except requests.RequestException:pass
    return results
def infer_company(title,snippet,url):
    text=clean(title)+" "+clean(snippet)
    patterns=[
      r"\s[-|]\s([^|–—]+?)\s(?:Remote|Morocco|France|Germany|UK|EMEA|MENA|Worldwide|$)",
      r"\bat\s+([A-Z][A-Za-z0-9&.\- ]{2,60})",
      r"^([^|–—:]+?)\s[-|:]\s(?:Customer|Account|Travel|Hospitality|Sales|Support|Operations)"
    ]
    for p in patterns:
        m=re.search(p,text,re.I)
        if m:
            c=clean(m.group(1))
            if 2<len(c)<70 and not any(x in c.lower() for x in ["job","remote","morocco","customer success","account manager"]):return c
    h=host(url)
    return "" if h in BLOCKED_JOB_HOSTS else (h.split(".")[0].replace("-"," ").title() if h else "")
def official_site(company):
    if not company:return ""
    for _,u in search(f'"{company}" official website travel hospitality',10):
        h=host(u)
        if h and h not in BAD_DOMAINS and not any(x in h for x in ["linkedin","indeed","glassdoor","crunchbase","wikipedia"]):
            return u.split("#")[0].rstrip("/")+"/"
    return ""
def rank_email(e):
    local=e.split("@",1)[0].lower()
    preferred=["careers","career","recruit","recruitment","jobs","job","talent","hiring","hr","people","join","bewerbung"]
    return (0 if any(k in local for k in preferred) else 1,len(local))
def company_contact(company):
    site=official_site(company)
    if not site:return "",""
    paths=["","/contact","/contact-us","/careers","/career","/jobs","/join-us","/work-with-us","/recruitment","/hr","/human-resources","/about","/impressum"]
    found=set()
    for p in paths:
        found |= extract_emails(fetch(urljoin(site,p)))
        if len(found)>=8:break
    return (sorted(found,key=rank_email)[0] if found else ""),site
def remote_access(text):
    t=clean(text).lower()
    if re.search(r"\b(morocco|maroc|casablanca)\b",t) and re.search(r"remote|work from home|distributed|anywhere",t):
        return "CONFIRMED_MOROCCO"
    if re.search(r"\b(emea|mena|africa|worldwide|global|europe|european union|international)\b",t) and "remote" in t:
        return "REGION_MATCH_VERIFY"
    if "remote" in t:return "REMOTE_VERIFY"
    return "NOT_REMOTE_SIGNAL"
def fit_score(title,desc):
    t=(title+" "+desc).lower();score=0;reasons=[]
    groups=[
      (25,["customer success","customer experience","account manager","key account","client success"],"Customer Success / Account Management"),
      (22,["travel","travel-tech","travel tech","hospitality","hotel","tourism","booking","ota","dmc"],"Travel / Hospitality"),
      (18,["b2b","sales","business development","partnership","commercial","supplier"],"B2B / Commercial"),
      (14,["operations","reservations","onboarding","activation","support","customer service"],"Operations / Support"),
      (8,["french","français","francophone"],"French"),
      (8,["arabic","arabe"],"Arabic"),
      (6,["english","anglais"],"English"),
      (4,["german","allemand","deutsch"],"German"),
      (10,["morocco","maroc","mena","emea","africa"],"Morocco/Regional remote")
    ]
    for pts,words,label in groups:
        if any(w in t for w in words):score+=pts;reasons.append(label)
    return min(score,100),"; ".join(reasons)
def make_record(title,company,location,url,source,snippet,email="",site="",access=""):
    desc=clean(snippet)
    remote=bool(re.search(r"remote|work from home|distributed|anywhere", (title+" "+location+" "+desc),re.I))
    score,reasons=fit_score(title,desc)
    return {
      "id":"remote_"+hashlib.sha256((url.split("?")[0] if url else title+company).encode()).hexdigest()[:18],
      "date_detection":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
      "statut":"NEW","role_cible":title,"intitule":title,"entreprise":company,"lieu":location or "Remote",
      "remote":remote,"source":source,"lien":url,"company_site":site,"emails_rh":email,
      "deep_status":"DONE","fit_score":score,"fit_reasons":reasons,"salary":"","description":desc,
      "posted_age":"","posted_within_24h":"UNKNOWN","search_type":"REMOTE_TRAVEL_HOSPITALITY",
      "email_status":"FOUND" if email else "NO_EMAIL","email_source":"company_site" if email else "",
      "spontaneous":"YES" if email and not url else "NO",
      "access_from_morocco":access
    }
def discover():
    raw=[]
    queries=REGION_QUERIES+ROLE_QUERIES
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs=[ex.submit(search,q+" 2026",12) for q in queries]
        for f in as_completed(futs):
            try:raw.extend(f.result())
            except:pass
    seen=set();records=[]
    for title,url in raw:
        if url in seen:continue
        seen.add(url)
        page=fetch(url) if any(d in host(url) for d in JOB_DOMAINS) else ""
        soup=BeautifulSoup(page,"html.parser") if page else None
        text=clean(soup.get_text(" ",strip=True)[:12000] if soup else title)
        company=infer_company(title,text,url)
        if not company:continue
        access=remote_access(title+" "+text)
        if "remote" not in (title+" "+text).lower():continue
        email,site=company_contact(company)
        # Accept either a real offer URL or a verified public company email.
        if not url and not email:continue
        records.append(make_record(title,company,"Remote / Morocco eligibility to verify",url,host(url),text[:1800],email,site,access))
        if len(records)>=350:break
    # company-first enrichment from travel/hospitality company searches
    for q in ['"travel technology" company "careers" email','"hospitality technology" company careers contact','"travel agency" remote careers Morocco']:
        for title,url in search(q,10):
            company=infer_company(title,"",url)
            if not company:continue
            email,site=company_contact(company)
            if not email:continue
            rec=make_record("Spontaneous application – Travel / Hospitality",company,"Remote from Morocco to verify",site or url,"Company contact",title,email,site,"REMOTE_VERIFY")
            if rec["id"] not in {x["id"] for x in records}:records.append(rec)
    records.sort(key=lambda x:(x["email_status"]=="FOUND",int(x["fit_score"] or 0)),reverse=True)
    return records[:400]
def post(records):
    if not WEBHOOK or not records:
        print("WEBHOOK missing or no records",len(records));return
    for i in range(0,len(records),25):
        payload={"mode":"jobs","sheet":"Remote Travel Hospitality","jobs":records[i:i+25]}
        for attempt in range(3):
            try:
                r=requests.post(WEBHOOK,json=payload,timeout=90)
                print("[WEBHOOK]",r.status_code,r.text[:300],flush=True)
                if r.ok:break
            except requests.RequestException as e:print("[WEBHOOK ERROR]",e,flush=True)
            time.sleep(2*(attempt+1))
def main():
    records=discover()
    print("REMOTE_RECORDS",len(records),flush=True)
    print("WITH_EMAIL",sum(bool(x["emails_rh"]) for x in records),flush=True)
    print("CONFIRMED_MOROCCO",sum(x["access_from_morocco"]=="CONFIRMED_MOROCCO" for x in records),flush=True)
    post(records)
    import csv
    fields=["date_detection","statut","poste","entreprise","lieu","type_remote","email","site_entreprise","source","lien_offre","linkedin_url","id","fit_score","fit_reason","type_poste","salaire","langues_requises","acces_depuis_maroc","contact_status"]
    with open("remote_travel_hospitality.csv","w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for x in records:
            w.writerow({
              "date_detection":x["date_detection"],"statut":x["statut"],"poste":x["intitule"],"entreprise":x["entreprise"],
              "lieu":x["lieu"],"type_remote":"YES" if x["remote"] else "NO","email":x["emails_rh"],
              "site_entreprise":x["company_site"],"source":x["source"],"lien_offre":x["lien"],"linkedin_url":"",
              "id":x["id"],"fit_score":x["fit_score"],"fit_reason":x["fit_reasons"],"type_poste":"REMOTE_TRAVEL_HOSPITALITY",
              "salaire":"","langues_requises":"","acces_depuis_maroc":x["access_from_morocco"],"contact_status":x["email_status"]})
if __name__=="__main__":main()
