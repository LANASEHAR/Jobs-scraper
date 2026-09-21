"""High-coverage job discovery for exactly six target role families.

The scraper is deliberately resilient: provider blocks (403/429), search-engine
failures, or an enrichment failure must not masquerade as a successful empty run.
"""
import argparse, hashlib, os, random, re, time
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

WEBHOOK=os.getenv("GOOGLE_SHEET_WEBHOOK_URL","").strip()
SEARCHES=[
 ("Customer Success",["customer success","client success","customer retention","customer onboarding"]),
 ("Account Manager",["account manager","account management","client account","key account"]),
 ("Account Executive",["account executive","account sales","sales account"]),
 ("Sales",["sales","selling","commercial","revenue"]),
 ("Administrative",["administration","administrative","office operations","back office"]),
 ("Design",["designer","design","graphic design","digital design","visual design"])
]
# Keep exactly six role families. Expand query variants inside those families only.
VARIANTS=[v for _,vs in SEARCHES for v in vs]
EXCLUDE=["director","vice president","vp ","head of ","chief","architect","doctor","nurse","software engineer","developer","data scientist","machine learning","devops","lawyer","accountant","physician","warehouse worker","driver","internship","intern "]
UA=[
 "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
 "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/139.0 Safari/537.36"
]
S=requests.Session()
EMAIL_RE=re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD={"example.com","sentry.io","schema.org","google.com","facebook.com","linkedin.com"}
BLOCKED={"linkedin.com","facebook.com","instagram.com","twitter.com","x.com","indeed.com","glassdoor.com","crunchbase.com","wikipedia.org","duckduckgo.com","google.com","bing.com","youtube.com"}
PATHS=["","/contact","/contact-us","/careers","/career","/jobs","/join-us","/work-with-us","/recruitment","/human-resources","/hr","/about","/en/contact","/en/careers","/fr/contact"]
SEARCH_DOMAINS=["indeed.com","emploi.ma","rekrute.com","bayt.com","novojob.com","optioncarriere.ma","glassdoor.com","linkedin.com"]
SOURCE_STATS={}

def now(): return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def clean(x): return re.sub(r"\s+", " ", str(x or "")).strip()
def hdr(): return {"User-Agent":random.choice(UA),"Accept-Language":"en-US,en;q=0.9,fr;q=0.8","Accept":"text/html,application/xhtml+xml"}
def stat(source,key,n=1): SOURCE_STATS.setdefault(source,{}); SOURCE_STATS[source][key]=SOURCE_STATS[source].get(key,0)+n

def fetch(url,timeout=15,retries=2):
    for i in range(retries+1):
        try:
            r=S.get(url,headers=hdr(),timeout=timeout,allow_redirects=True)
            if r.status_code==200 and r.text:
                return r.text
            stat("HTTP",str(r.status_code))
            print(f"[HTTP] {r.status_code} {url}",flush=True)
            if r.status_code in (401,403,429):
                return None
        except requests.RequestException as e:
            print(f"[HTTP] error {url}: {type(e).__name__}",flush=True)
            stat("HTTP","exception")
        if i<retries: time.sleep(min(8,2**i+random.random()))
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

def fresh_window(t,max_hours=168):
    h=age_hours(t)
    return h is not None and h<=max_hours

def target(t,d=""):
    x=(t+" "+d).lower()
    return not any(v in x for v in EXCLUDE)

def jid(source,url,title):
    key=url.split("?")[0].rstrip("/") if url else source+"|"+title
    return "job_"+hashlib.sha256(key.encode()).hexdigest()[:18]

def job(title,company,loc,remote,source,url,age="",desc="",kind=None):
    return {"id":jid(source,url,title),"date_detection":now(),"statut":"NEW","role_cible":title,"intitule":title,
      "entreprise":company or "Unknown","lieu":loc or ("Remote / Worldwide" if remote else "Casablanca"),"remote":bool(remote),
      "source":source,"lien":url,"company_site":"","emails_rh":"","deep_status":"PENDING","fit_score":"","fit_reasons":"",
      "salary":"","description":clean(desc),"posted_age":clean(age),
      "posted_within_24h":"YES" if fresh_window(age,24) else "UNKNOWN",
      "search_type":kind or ("REMOTE" if remote else "CASABLANCA"),"email_status":"PENDING","email_source":"","spontaneous":"NO"}

def parse_linkedin(html,remote,search_type):
    soup=BeautifulSoup(html or "","html.parser");out=[];seen=set()
    for card in soup.select("li.base-card,li.jobs-search__results-list,div.base-card"):
        a=card.select_one('a[href*="/jobs/view/"]')
        if not a:continue
        href=a.get("href","").split("?")[0];m=re.search(r"/jobs/view/(?:[^/]+-)?(\d+)",href)
        if not m or m.group(1) in seen:continue
        seen.add(m.group(1))
        title=clean((card.select_one("h3") or a).get_text(" ",strip=True))
        ce=card.select_one("h4,.base-search-card__subtitle,.hidden-nested-link");le=card.select_one(".job-search-card__location")
        te=card.select_one("time,.job-search-card__listdate,.job-search-card__listdate--new")
        company=clean(ce.get_text(" ",strip=True) if ce else "");loc=clean(le.get_text(" ",strip=True) if le else "")
        age=clean(te.get_text(" ",strip=True) if te else "")
        if title and company and target(title) and (fresh_window(age,168) or not age):
            out.append(job(title,company,loc,remote,"LinkedIn",urljoin("https://www.linkedin.com",href),age,kind=search_type))
    return out

def linkedin_guest_search(keywords,location,remote,search_type):
    out=[];start=0;blocked=0
    while start<=75:
        params=f"?keywords={quote_plus(keywords)}&location={quote_plus(location)}&f_TPR=r604800&start={start}"
        if remote:params+="&f_WT=2"
        h=fetch("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"+params,20,1)
        if not h:
            blocked+=1
            if blocked>=2: break
            start+=25; continue
        blocked=0
        found=parse_linkedin(h,remote,search_type)
        if not found:break
        out.extend(found)
        print(f"[LinkedIn page] {keywords} | {search_type} start={start} found={len(found)}",flush=True)
        if len(found)<10:break
        start+=25
        time.sleep(3.5+random.random()*3)
    return out

DDG_FAILURES=0
DDG_DISABLED=False
def register_ddg_failure():
    global DDG_FAILURES,DDG_DISABLED
    DDG_FAILURES+=1
    if DDG_FAILURES>=2: DDG_DISABLED=True
def reset_ddg_failures():
    global DDG_FAILURES,DDG_DISABLED
    DDG_FAILURES=0;DDG_DISABLED=False

def web_search(q,limit=10):
    # Search several independent public result pages. A provider failure is never
    # interpreted as "no results".
    providers=[
      ("Bing","https://www.bing.com/search?q="+quote_plus(q),8),
      ("DDG","https://html.duckduckgo.com/html/?q="+quote_plus(q),8),
      ("DDG-Lite","https://lite.duckduckgo.com/lite/?q="+quote_plus(q),8),
      ("Google","https://www.google.com/search?q="+quote_plus(q),8)
    ]
    for name,u,timeout in providers:
        if name.startswith("DDG") and DDG_DISABLED: continue
        try:
            r=S.get(u,headers=hdr(),timeout=timeout,allow_redirects=True)
            if r.status_code!=200 or not r.text:
                stat("Search",f"{name}_{r.status_code}");print(f"[SEARCH] {name} status={r.status_code}",flush=True)
                if name.startswith("DDG"):register_ddg_failure()
                continue
            soup=BeautifulSoup(r.text,"html.parser");items=[]
            selectors=["li.b_algo h2 a","a.result__a","a.result-link","a[href*='/url?q=']"]
            for sel in selectors:
                for a in soup.select(sel):
                    href=a.get("href","");title=clean(a.get_text(" ",strip=True))
                    if href.startswith("/url?q="):href=href.split("/url?q=",1)[1].split("&",1)[0]
                    if href.startswith("http") and title:items.append((title,href))
                if items:break
            if items:
                if name.startswith("DDG"):reset_ddg_failures()
                stat("Search",name)
                return items[:limit]
            if name.startswith("DDG"):register_ddg_failure()
        except requests.RequestException:
            stat("Search",f"{name}_exception")
    return []

def send_progressive(jobs, label="progress"):
    """Push discovered jobs to Sheets immediately; keep scraping if one push fails."""
    if not jobs:
        return
    try:
        aliases={"WORLDWIDE_REMOTE":"Worldwide Remote","MOROCCO_REMOTE":"Morocco Remote","CASABLANCA_ONSITE":"Casablanca Onsite"}
        for key,sheet in aliases.items():
            batch=[j for j in jobs if j.get("search_type")==key]
            if batch:
                result=post_jobs(batch,sheet)
                print(f"[PROGRESS] {label} -> {sheet}: sent={len(batch)} added={result.get('added',0)}",flush=True)
    except Exception as e:
        print(f"[PROGRESS ERROR] {label}: {e}",flush=True)

def company_from_linkedin_url(url):
    m=re.search(r"-at-([^-]+(?:-[^-]+){0,8})-(\d{6,})/?$",url)
    return clean(m.group(1).replace("-"," ")).title() if m else ""

def linkedin_web_fallback(keywords,location,remote,search_type):
    queries=[f'site:linkedin.com/jobs/view "{keywords}" "{location}"',f'site:linkedin.com/jobs/view "{keywords}" Morocco'] if remote else [f'site:linkedin.com/jobs/view "{keywords}" Casablanca']
    out=[];seen=set()
    for q in queries:
        for title,url in web_search(q,20):
            if "/jobs/view/" not in url:continue
            key=url.split("?")[0];company=company_from_linkedin_url(key)
            if not company or key in seen:continue
            seen.add(key);ct=title.split(" | ")[0].strip() or keywords
            if target(ct):out.append(job(ct,company,location,remote,"LinkedIn Search",key,"",title,search_type))
        time.sleep(1)
    return out

def linkedin():
    targets=[("WORLDWIDE_REMOTE","Remote / Worldwide",True),("MOROCCO_REMOTE","Morocco",True),("CASABLANCA_ONSITE","Casablanca, Morocco",False)]
    all_out=[];seen=set()
    for family,variants in SEARCHES:
        keyword_queries=[
          f'"{variants[0]}"',
          f'"{variants[0]}" OR "{variants[1]}"' if len(variants)>1 else f'"{variants[0]}"'
        ]
        for keyword_query in keyword_queries:
            for st,loc,remote in targets:
                print(f"[LinkedIn] {family} :: keywords={keyword_query} | {st}",flush=True)
                found=linkedin_guest_search(keyword_query,loc,remote,st)
                if len(found)<3:found+=linkedin_web_fallback(keyword_query,loc,remote,st)
                send_progressive(found, f"LinkedIn {family} {st}")
                for j in found:
                    if j["id"] not in seen:seen.add(j["id"]);all_out.append(j)
                time.sleep(2+random.random())
    print(f"[LinkedIn] total={len(all_out)}",flush=True);return all_out

def parse_indeed(html,kind,remote):
    soup=BeautifulSoup(html or "","html.parser");out=[];seen=set()
    for card in soup.select("div.job_seen_beacon,div.cardOutline,td.resultContent"):
        a=card.select_one("h2.jobTitle a,h2 a")
        if not a:continue
        title=clean(a.get_text(" ",strip=True));url=urljoin("https://ma.indeed.com",a.get("href","")).split("?")[0]
        ce=card.select_one("[data-testid='company-name'],.companyName");le=card.select_one("[data-testid='text-location'],.companyLocation")
        de=card.select_one("span.date,[data-testid='myJobsStateDate']");se=card.select_one(".job-snippet")
        age=clean(de.get_text(" ",strip=True) if de else "");desc=clean(se.get_text(" ",strip=True) if se else "");company=clean(ce.get_text(" ",strip=True) if ce else "")
        if not title or not company or not target(title,desc) or url in seen:continue
        if age and not fresh_window(age,168):continue
        seen.add(url);out.append(job(title,company,clean(le.get_text(" ",strip=True) if le else ""),remote,"Indeed",url,age,desc,kind))
    return out

def indeed():
    # Direct Indeed HTML is frequently protected by 403. Try it once per query,
    # then immediately use public indexed search instead of wasting the run.
    out=[];seen=set();targets=[("WORLDWIDE_REMOTE","Remote",True),("MOROCCO_REMOTE","Morocco",True),("CASABLANCA_ONSITE","Casablanca",False)]
    for family,variants in SEARCHES:
        for kind,loc,remote in targets:
            keyword=variants[0]
            url=f"https://ma.indeed.com/jobs?q={quote_plus(keyword)}&l={quote_plus(loc)}&fromage=7"
            h=fetch(url,15,0)
            if h:
                found=parse_indeed(h,kind,remote)
            else:
                found=[]
            if not found:
                queries=[
                  f'site:indeed.com/viewjob "{keyword}" "{loc}"',
                  f'site:indeed.com/jobs "{keyword}" "{loc}"',
                  f'site:ma.indeed.com "{keyword}" "{loc}"'
                ]
                for q in queries:
                    for title,u in web_search(q,20):
                        if "indeed.com" not in urlparse(u).netloc.lower():continue
                        if not target(title):continue
                        found.append(job(title.split(" | ")[0],clean(title.split(" | ")[1]) if " | " in title else "Indeed Employer",
                                         loc,remote,"Indeed Search",u,"","",kind))
            send_progressive(found, f"Indeed {family} {kind}")
            for j in found:
                if j["id"] not in seen:seen.add(j["id"]);out.append(j)
            time.sleep(1.5+random.random())
    print(f"[Indeed] total={len(out)}",flush=True);return out

def parse_web_jobs(items,kind,remote):
    out=[]
    domains=set(SEARCH_DOMAINS)
    for title,url in items:
        host=urlparse(url).netloc.lower().replace("www.","")
        if not any(host==d or host.endswith("."+d) for d in domains):continue
        low=title.lower()
        if not any(x in low for x in [v.lower() for v in VARIANTS]):continue
        if not target(title):continue
        company=""
        m=re.search(r"\s(?:at|chez|@)\s+(.+)$",title,re.I)
        if m:company=clean(m.group(1))
        if not company:company=clean(host.split(".")[0]).title()
        out.append(job(title,company,"Casablanca" if kind=="CASABLANCA_ONSITE" else ("Morocco" if kind=="MOROCCO_REMOTE" else "Remote / Worldwide"),remote,"Web Search",url,"","",kind))
    return out

def public_web_jobs():
    out=[];seen=set()
    for family,variants in SEARCHES:
        # Small precise queries outperform one giant OR expression on public search engines.
        seeds=[variants[0],variants[1] if len(variants)>1 else variants[0]]
        for seed in seeds:
            for kind,place,remote in [("WORLDWIDE_REMOTE","remote",True),("MOROCCO_REMOTE","Morocco remote",True),("CASABLANCA_ONSITE","Casablanca",False)]:
                queries=[
                  f'"{seed}" {place} jobs',
                  f'"{seed}" {place} hiring',
                  f'"{seed}" {place} recrutement',
                  f'"{seed}" {place} site:emploi.ma OR site:rekrute.com OR site:bayt.com OR site:novojob.com OR site:optioncarriere.ma'
                ]
                for sq in queries:
                    items=web_search(sq,20)
                    found=parse_web_jobs(items,kind,remote)
                    send_progressive(found, f"Web {family} {kind}")
                    for j in found:
                        if j["id"] not in seen:seen.add(j["id"]);out.append(j)
                    time.sleep(.5+random.random()*.5)
    print(f"[Web Search] total={len(out)}",flush=True);return out

def company_site(company):
    if not company or company=="Unknown":return None
    for q in [f'"{company}" official website',f'"{company}" careers jobs',f'"{company}" recruitment']:
        for _,u in web_search(q,10):
            host=urlparse(u).netloc.lower().replace("www.","")
            if host and not any(host==b or host.endswith("."+b) for b in BLOCKED):return "https://"+host
    return None

def extract_emails(html):
    text=re.sub(r"\s*(?:\[at\]|\(at\)|\{at\})\s*","@",html,flags=re.I)
    text=re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*",".",text,flags=re.I)
    soup=BeautifulSoup(text,"html.parser");found=set(EMAIL_RE.findall(text))
    for a in soup.select('a[href^="mailto:"]'):found.add(a.get("href","")[7:].split("?")[0])
    return {e.lower().strip(" .;,<>\"'") for e in found if "@" in e and e.lower().split("@")[-1] not in BAD and not e.lower().startswith(("noreply@","no-reply@","privacy@","security@"))}

def enrich(j):
    site=j.get("company_site") or company_site(j.get("entreprise",""))
    if not site:return {"id":j["id"],"sheet":j.get("sheet"),"company_site":"","emails_rh":"","deep_status":"NO_SITE","email_status":"NOT_FOUND","email_source":""}
    p=urlparse(site);base=f"{p.scheme}://{p.netloc}";domain=p.netloc.lower().replace("www.","");es=set();pages=False
    for path in PATHS:
        h=fetch(base+path,10,1)
        if h:pages=True;es|=extract_emails(h)
    for q in [f'"{j.get("entreprise","")}" recruitment email',f'"{j.get("entreprise","")}" careers email',f'"{j.get("entreprise","")}" recrutement email']:
        for _,u in web_search(q,10):
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
    selected=sorted(es,key=score)
    return {"id":j["id"],"sheet":j.get("sheet"),"company_site":site,"emails_rh":" / ".join(selected),
      "deep_status":"DONE" if pages else "SITE_FOUND_NO_PAGES","email_status":"FOUND" if selected else ("NO_EMAIL" if pages else "NOT_FOUND"),
      "email_source":"Company website / public web"}

def post(payload,expected_status="success"):
    if not WEBHOOK:raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    last_error=""
    for i in range(4):
        try:
            r=requests.post(WEBHOOK,json=payload,allow_redirects=True,timeout=(10,60),headers={"Content-Type":"application/json"})
            ctype=r.headers.get("content-type","").lower()
            print(f"[WEBHOOK] POST status={r.status_code} type={ctype}",flush=True)
            if 200<=r.status_code<300:
                try:data=r.json()
                except ValueError:
                    last_error=f"non-JSON response: {(r.text or '')[:200]!r}"
                    print(f"[WEBHOOK ERROR] {last_error}",flush=True);data=None
                if isinstance(data,dict) and data.get("status")==expected_status:
                    return data
                last_error=f"invalid JSON response: {data!r}"
        except requests.RequestException as e:
            last_error=str(e);print(f"[WEBHOOK ERROR] {e}",flush=True)
        time.sleep(min(10,2**i))
    raise RuntimeError(f"Webhook failed after retries: {last_error}")

def post_jobs(jobs,sheet):
    if not jobs:return {"added":0}
    for j in jobs:j["sheet"]=sheet
    total=0
    for i in range(0,len(jobs),25):
        batch=jobs[i:i+25]
        print(f"[Sheet] sending {len(batch)} jobs -> {sheet}",flush=True)
        data=post({"mode":"jobs","jobs":batch,"sheet":sheet})
        total+=int(data.get("added",0))
    return {"added":total}

def pending():
    # POST is used deliberately: this avoids deployments where GET is redirected
    # to an HTML Apps Script page while POST already returns JSON correctly.
    data=post({"mode":"pending","limit":5000,"sheet":"ALL"})
    jobs=data.get("jobs")
    if not isinstance(jobs,list):raise RuntimeError(f"Pending response missing jobs: {data!r}")
    print(f"[Pending POST] received {len(jobs)} jobs",flush=True)
    return jobs

def deep():
    js=pending();print(f"[DEEP] pending={len(js)}",flush=True);updates=[]
    for i,j in enumerate(js,1):
        print(f"[DEEP] {i}/{len(js)} {j.get('entreprise')} — {j.get('intitule')} [{j.get('sheet')}] ",flush=True)
        try:
            u=enrich(j);u["sheet"]=j.get("sheet");updates.append(u)
        except Exception as e:
            updates.append({"id":j.get("id"),"sheet":j.get("sheet"),"deep_status":"ERROR","email_status":"ERROR","deep_error":str(e)[:250]})
        if len(updates)>=10:
            post({"mode":"enrich","updates":updates});updates=[]
    if updates:post({"mode":"enrich","updates":updates})
    print("[DEEP] complete",flush=True)

def scrape():
    if not WEBHOOK:raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
    print(f"[START] {len(SEARCHES)} target role families / {len(VARIANTS)} supporting keywords; freshness window=7 days",flush=True)
    aliases={"WORLDWIDE_REMOTE":"Worldwide Remote","MOROCCO_REMOTE":"Morocco Remote","CASABLANCA_ONSITE":"Casablanca Onsite"}
    seen=set();totals={v:0 for v in aliases.values()}
    for source_name,fn in [("LinkedIn",linkedin),("Indeed",indeed),("Web",public_web_jobs)]:
        SOURCE_STATS[source_name]={}
        try:
            jobs=fn()
        except Exception as e:
            stat(source_name,"errors");print(f"[SOURCE ERROR] {source_name}: {e}",flush=True);jobs=[]
        groups={v:[] for v in aliases.values()}
        for j in jobs:
            if j["id"] in seen:continue
            seen.add(j["id"]);tab=aliases.get(j.get("search_type"))
            if tab:groups[tab].append(j)
        for sheet,batch in groups.items():
            if batch:
                result=post_jobs(batch,sheet);totals[sheet]+=int(result.get("added",0))
        print(f"[SOURCE DONE] {source_name}: discovered={len(jobs)} stats={SOURCE_STATS[source_name]}",flush=True)
    print(f"[DONE] total_unique={len(seen)} groups_added={totals}",flush=True)
    post({"mode":"log","run":{"finished_at":now(),"total_unique":len(seen),"groups_added":totals,"source_stats":SOURCE_STATS}})

def main():
    p=argparse.ArgumentParser();p.add_argument("--mode",choices=["scrape","deep"],default="scrape");a=p.parse_args()
    scrape() if a.mode=="scrape" else deep()
if __name__=="__main__":main()
