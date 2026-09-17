"""Remote jobs scraper: LinkedIn discovery -> Google Sheet -> company/email deep search."""
import argparse, hashlib, os, random, re, time
from urllib.parse import quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup
WEBHOOK=os.getenv("GOOGLE_SHEET_WEBHOOK_URL","").strip()
ROLE_QUERIES=["Customer Success Manager","Customer Success Specialist","Account Manager","Customer Account Manager","Customer Support Specialist","Customer Experience Specialist","Sales Development Representative","Business Development Representative","Inside Sales Representative","Sales Executive","Account Executive","Operations Coordinator","Business Operations Specialist","Administrative Coordinator","Sales Operations Specialist","Commercial Operations Specialist","E-commerce Specialist","Shopify Specialist","CRM Specialist","Back Office Specialist","Project Coordinator","Junior UX UI Designer","Junior UX Designer","Junior UI Designer"]
EXCLUDE_TERMS=["senior","sr.","sr ","lead","principal","director","vp ","vice president","head of","chief","staff","architect","internship","intern ","doctor","nurse","software engineer","developer","data scientist","machine learning","devops","lawyer","accountant","physician","warehouse worker","driver"]
USER_AGENTS=["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36","Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"]
session=requests.Session()
def headers(referer="https://www.linkedin.com/"): return {"User-Agent":random.choice(USER_AGENTS),"Accept-Language":"en-US,en;q=0.9","Accept":"text/html,application/xhtml+xml","Referer":referer}
def fetch(url,timeout=15,retries=2):
    for attempt in range(retries+1):
        try:
            r=session.get(url,headers=headers(),timeout=timeout,allow_redirects=True)
            if r.status_code==200:return r.text
            time.sleep((attempt+1)*1.5)
        except requests.RequestException: time.sleep(1+attempt)
    return None
def clean(s): return re.sub(r"\s+"," ",str(s or "")).strip()
def job_id(url,title=""): return "li_"+hashlib.sha256((url+"|"+title).encode()).hexdigest()[:16]
def is_target(title,description=""):
    text=(title+" "+description).lower();return not any(x in text for x in EXCLUDE_TERMS)
def parse_linkedin_search(html):
    soup=BeautifulSoup(html,"html.parser");jobs=[];seen=set()
    for a in soup.select('a[href*="/jobs/view/"]'):
        href=a.get("href","").split("?")[0];m=re.search(r"/jobs/view/(?:[^/]+-)?(\d+)",href)
        if not m or m.group(1) in seen: continue
        seen.add(m.group(1));card=a.find_parent(class_=re.compile("base-card|job-search-card|result-card")) or a.parent
        title=clean(a.get_text(" ",strip=True));company="";location=""
        if card:
            c=card.select_one(".base-search-card__subtitle,.hidden-nested-link");l=card.select_one(".job-search-card__location")
            company=clean(c.get_text(" ",strip=True) if c else "");location=clean(l.get_text(" ",strip=True) if l else "")
        if not title or not is_target(title):continue
        full=urljoin("https://www.linkedin.com",href)
        jobs.append({"id":job_id(full,title),"date_detection":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"statut":"NEW","role_cible":title,"intitule":title,"entreprise":company or "Unknown","lieu":location or "Remote / Worldwide","remote":True,"source":"LinkedIn","lien":full,"company_site":"","emails_rh":"","deep_status":"PENDING","fit_score":"","fit_reasons":"","salary":"","description":""})
    return jobs
def parse_indexed_linkedin(html):
    if not html:return []
    soup=BeautifulSoup(html,"html.parser");jobs=[];seen=set()
    for result in soup.select(".result"):
        a=result.select_one("a.result__a");href=a.get("href","") if a else ""
        m=re.search(r"https?://(?:www\.)?linkedin\.com/jobs/view/[^\s\"&<>]+",str(result),re.I) or re.search(r"https?://(?:www\.)?linkedin\.com/jobs/view/[^\s\"<>]+",href,re.I)
        if not a or not m:continue
        url=m.group(0).rstrip("')>\"").split("?")[0]
        if url in seen:continue
        seen.add(url);title=clean(re.sub(r"\s*\|\s*LinkedIn$","",a.get_text(" ",strip=True),flags=re.I));text=clean(result.get_text(" ",strip=True));company=""
        if " - " in title:title,company=[x.strip() for x in title.split(" - ",1)]
        if not title or not is_target(title):continue
        loc="Morocco" if any(x in text.lower() for x in ("morocco","maroc")) else "Remote / Worldwide"
        jobs.append({"id":job_id(url,title),"date_detection":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"statut":"NEW","role_cible":title,"intitule":title,"entreprise":company or "Unknown","lieu":loc,"remote":True,"source":"LinkedIn indexed","lien":url,"company_site":"","emails_rh":"","deep_status":"PENDING","fit_score":"","fit_reasons":"","salary":"","description":""})
    return jobs
def scrape_linkedin(max_pages=3,max_per_query=25):
    all_jobs=[];seen=set()
    for query in ROLE_QUERIES:
        direct=False
        for page in range(max_pages):
            url=f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(query)}&f_WT=2&start={page*25}"
            print(f"[LinkedIn] {query} page={page+1}");html=fetch(url)
            if not html:
                print("  [!] direct LinkedIn unavailable");break
            jobs=parse_linkedin_search(html);direct=direct or bool(jobs);added=0
            for j in jobs[:max_per_query]:
                if j["id"] not in seen:seen.add(j["id"]);all_jobs.append(j);added+=1
            print(f"  +{added}")
            if len(jobs)<5:break
            time.sleep(random.uniform(.7,1.5))
        if not direct:
            q=quote_plus(f'site:linkedin.com/jobs/view/ "{query}" "remote"');ih=fetch(f"https://html.duckduckgo.com/html/?q={q}",15,1);jobs=parse_indexed_linkedin(ih);added=0
            for j in jobs[:max_per_query]:
                if j["id"] not in seen:seen.add(j["id"]);all_jobs.append(j);added+=1
            print(f"  indexed fallback +{added}")
    return all_jobs
def post(payload):
    if not WEBHOOK:raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    for attempt in range(3):
        try:
            r=requests.post(WEBHOOK,json=payload,timeout=30);r.raise_for_status();print("[Sheet POST]",r.status_code,r.text[:300]);return
        except requests.RequestException as exc:
            if attempt==2:raise RuntimeError(f"Sheet webhook POST failed: {exc}")
            time.sleep(2**attempt)
def parse_json_response(r,context):
    text=(r.text or "").strip()
    if not text:raise RuntimeError(f"Webhook returned empty response during {context} (HTTP {r.status_code})")
    try:return r.json()
    except ValueError as exc:raise RuntimeError(f"Webhook returned non-JSON during {context}: {re.sub(r'\\s+',' ',text)[:250]!r}") from exc
def discover_company_site(company):
    if not company or company=="Unknown":return None
    html=fetch(f"https://html.duckduckgo.com/html/?q={quote_plus(chr(34)+company+chr(34)+' official website careers jobs')}")
    if not html:return None
    blocked={"linkedin.com","facebook.com","instagram.com","twitter.com","x.com","indeed.com","glassdoor.com","crunchbase.com","wikipedia.org","duckduckgo.com"}
    for a in BeautifulSoup(html,"html.parser").select("a.result__a"):
        href=a.get("href","")
        if href.startswith("http"):
            host=urlparse(href).netloc.lower().replace("www.","")
            if host and not any(x in host for x in blocked):return "https://"+host
    return None
EMAIL_RE=re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}");EMAIL_BAD={"example.com","sentry.io","schema.org","google.com","facebook.com","linkedin.com"};EMAIL_SKIP_PREFIX=("noreply@","no-reply@","privacy@","security@","support@","mailer-daemon@");DEEP_PATHS=["","/contact","/contact-us","/careers","/jobs","/about","/imprint","/impressum","/en/contact","/en/careers"]
def extract_emails(html):
    text=re.sub(r"\s*(?:\[at\]|\(at\)|\{at\})\s*","@",html,flags=re.I);text=re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*",".",text,flags=re.I);soup=BeautifulSoup(text,"html.parser");found=set(EMAIL_RE.findall(text))
    for a in soup.select('a[href^="mailto:"]'):found.add(a.get("href","")[7:].split("?")[0])
    return {e.lower().strip(" .;,<>\"'") for e in found if "@" in e and e.lower().split("@")[-1] not in EMAIL_BAD and not e.lower().startswith(EMAIL_SKIP_PREFIX)}
def deep_search_job(job):
    site=job.get("company_site") or discover_company_site(job.get("entreprise",""))
    if not site:return {"id":job["id"],"company_site":"","emails_rh":"","deep_status":"NO_SITE"}
    p=urlparse(site);base=f"{p.scheme}://{p.netloc}";emails=set();pages_seen=False
    for path in DEEP_PATHS:
        html=fetch(base+path,10,1)
        if html:pages_seen=True;emails|=extract_emails(html)
    ranked=sorted(emails,key=lambda e:(0 if any(k in e for k in ("career","recruit","hr","talent","jobs","hiring")) else 1,len(e)))
    return {"id":job["id"],"company_site":site,"emails_rh":" / ".join(ranked[:3]),"deep_status":"DONE" if pages_seen else "SITE_FOUND_NO_PAGES"}
def deep_run(limit=100):
    if not WEBHOOK:raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    r=requests.get(WEBHOOK,params={"action":"pending","limit":min(limit,500)},timeout=30);r.raise_for_status();data=parse_json_response(r,"pending read");jobs=data.get("jobs",data if isinstance(data,list) else [])
    if not isinstance(jobs,list):raise RuntimeError(f"Unexpected pending response shape: {type(jobs).__name__}")
    updates=[]
    for i,job in enumerate(jobs,1):
        print(f"[DEEP] {i}/{len(jobs)} {job.get('entreprise')} — {job.get('intitule')}")
        try:updates.append(deep_search_job(job))
        except Exception as exc:updates.append({"id":job.get("id"),"deep_status":"ERROR","deep_error":str(exc)[:200]})
        if len(updates)>=10:post({"mode":"enrich","updates":updates});updates=[]
    if updates:post({"mode":"enrich","updates":updates})
def main():
    p=argparse.ArgumentParser();p.add_argument("--mode",choices=["scrape","deep"],default="scrape");p.add_argument("--pages",type=int,default=int(os.getenv("LINKEDIN_MAX_PAGES","3")));p.add_argument("--deep-limit",type=int,default=int(os.getenv("DEEP_LIMIT","100")));a=p.parse_args()
    if a.mode=="scrape":
        jobs=scrape_linkedin(a.pages)
        if jobs:post({"mode":"jobs","jobs":jobs})
        print(f"DONE: {len(jobs)} LinkedIn jobs discovered and sent to Sheet")
    else:deep_run(a.deep_limit)
if __name__=="__main__":main()
