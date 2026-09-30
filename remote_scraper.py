"""High-coverage job discovery for exactly six target role families.

The scraper is deliberately resilient: provider blocks (403/429), search-engine
failures, or an enrichment failure must not masquerade as a successful empty run.
"""
import argparse, hashlib, os, random, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

WEBHOOK=os.getenv("GOOGLE_SHEET_WEBHOOK_URL","").strip()
SEARCHES=[
 ("Customer Success",["customer success manager","customer success","client success","client experience","customer onboarding","customer enablement","customer experience manager"]),
 ("Account Management",["account manager","account management","key account manager","strategic account manager","client account manager","partner manager","customer account manager"]),
 ("Revenue & Partnerships",["partnerships manager","partner success","business partnerships","sales operations","revenue operations","commercial operations","sales enablement"]),
 ("Travel-Tech & Hospitality",["travel tech","travel technology","hospitality technology","hotel tech","travel account manager","travel customer success","hospitality account manager"]),
 ("E-commerce & Digital Operations",["ecommerce manager","e-commerce manager","ecommerce operations","shopify manager","digital operations","marketplace manager","ecommerce customer success"]),
 ("Business Operations",["business operations","operations coordinator","operations specialist","project coordinator","commercial coordinator","sales coordinator","business support"]),
 ("ADV & Sales Administration",["administration des ventes","ADV","sales administration","sales administrator","order management","order administrator","customer operations"]),
 ("Executive & Administrative Support",["executive assistant","executive secretary","administrative assistant","personal assistant","office manager","assistante de direction","assistante administrative","assistante polyvalente"])
]
# Keep exactly six role families. Expand query variants inside those families only.
VARIANTS=[v for _,vs in SEARCHES for v in vs]
EXCLUDE=["director","vice president","vp ","head of ","chief","architect","doctor","nurse","software engineer","developer","data scientist","machine learning","devops","lawyer","accountant","physician","warehouse worker","driver","internship","intern ","cold calling","cold-call","cold call","100+ calls","high volume calls","high-volume calls","commission only","commission-only","door to door","telemarketing","night shift","overnight","rotating shifts","24/7","weekend shifts","unpaid","volunteer"]
UA=[
 "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
 "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/139.0 Safari/537.36"
]
S=requests.Session()
EMAIL_RE=re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD={"example.com","sentry.io","schema.org","google.com","facebook.com","linkedin.com"}
BLOCKED={"linkedin.com","facebook.com","instagram.com","twitter.com","x.com","indeed.com","glassdoor.com","crunchbase.com","wikipedia.org","duckduckgo.com","google.com","bing.com","youtube.com"}
PATHS=["","/contact","/contact-us","/careers","/career","/jobs","/join-us","/work-with-us","/recruitment","/human-resources","/hr","/about","/en/contact","/en/careers","/fr/contact","/fr/carriere","/fr/recrutement","/legal","/imprint","/impressum"]
SEARCH_DOMAINS=["indeed.com","emploi.ma","rekrute.com","bayt.com","novojob.com","optioncarriere.ma","glassdoor.com","linkedin.com","welcometothejungle.com","wellfound.com","remotive.com","weworkremotely.com","himalayas.app","jobgether.com","workingnomads.com","remoteok.com","topcsjobs.com","supportdriven.com"]
SOURCE_STATS={}
NO_EMAIL_BUFFER=[]

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

def fresh_window(t,max_hours=72):
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
        if title and company and target(title) and (fresh_window(age,72) or not age):
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
            selectors=["li.b_algo h2 a","li.b_algo a","a.result__a","a.result-link","a[data-testid='result-title-a']","a[href*='/url?q=']"]
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
    """Enrich immediately; write verified-email jobs now and buffer no-email jobs for the final fallback pass."""
    if not jobs:
        return
    enriched=[]
    for j in jobs:
        try:
            u=enrich(j)
            j.update(u)
            if u.get("email_status")=="FOUND" and u.get("emails_rh"):
                enriched.append(j)
                print(f"[EMAIL-FIRST] {label}: {j.get('entreprise')} -> {u.get('emails_rh')}",flush=True)
            else:
                NO_EMAIL_BUFFER.append(j)
                print(f"[EMAIL-FIRST] {label}: buffered {j.get('entreprise')} — no public email after enrichment",flush=True)
        except Exception as e:
            # An enrichment failure must not make a discovered offer disappear.
            # Keep it for the final no-email fallback rather than inventing an address.
            j["email_status"]="ERROR"
            j["deep_status"]="ERROR"
            NO_EMAIL_BUFFER.append(j)
            print(f"[EMAIL-FIRST ERROR] {label} / {j.get('entreprise')}: {e} — buffered for final fallback",flush=True)
    aliases={"WORLDWIDE_REMOTE":"Worldwide Remote","MOROCCO_REMOTE":"Morocco Remote","CASABLANCA_ONSITE":"Casablanca Onsite"}
    for key,sheet in aliases.items():
        batch=[j for j in enriched if j.get("search_type")==key]
        if batch:
            try:
                result=post_jobs(batch,sheet)
                print(f"[PROGRESS] {label} -> {sheet}: email_found={len(batch)} added={result.get('added',0)}",flush=True)
            except Exception as e:
                print(f"[PROGRESS ERROR] {label} -> {sheet}: {e}",flush=True)
    return enriched

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
        if age and not fresh_window(age,72):continue
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
                  f'site:ma.indeed.com "{keyword}" "{loc}"',
                  f'site:indeed.com "{keyword}" Morocco'
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


def spontaneous_casablanca():
    """After job discovery, search Casablanca companies that may fit the profile even without an advertised vacancy."""
    roles=[
      "customer success manager",
      "account manager",
      "key account manager",
      "sales administrator",
      "administration des ventes",
      "executive assistant",
      "assistante de direction",
      "assistante administrative",
      "office manager",
      "business operations",
      "commercial coordinator",
      "travel account manager"
    ]
    company_queries=[
      '"multinationale" Casablanca recrutement',
      '"multinational" Casablanca Morocco careers',
      '"international company" Casablanca Morocco careers',
      '"shared services" Casablanca Morocco recruitment',
      '"BPO" Casablanca Morocco headquarters careers',
      '"SaaS" Casablanca Morocco company',
      '"travel" Casablanca Morocco company careers',
      '"logistics" Casablanca Morocco company careers',
      '"ecommerce" Casablanca Morocco company careers',
      '"FMCG" Casablanca Morocco company careers'
    ]
    companies={}
    for q in company_queries:
        for title,u in web_search(q,20):
            host=urlparse(u).netloc.lower().replace("www.","")
            if not host or any(host==b or host.endswith("."+b) for b in BLOCKED): continue
            # Prefer actual company/career/contact pages over job-board pages.
            if any(x in host for x in ["linkedin","indeed","glassdoor","bayt","rekrute","emploi.ma","novojob"]): continue
            name=clean(re.sub(r"\s*[-|–]\s*(careers|jobs|recruitment|casablanca).*$","",title,flags=re.I))
            if len(name)<2: name=host.split(".")[0].replace("-"," ").title()
            key=host
            companies[key]={"name":name,"site":"https://"+host}
        time.sleep(.5)
    out=[]
    seen=set()
    for domain,info in companies.items():
        role="Customer Success / Account Management / Sales Administration / Executive Support"
        j=job(
              role,
              info["name"],
              "Casablanca, Morocco",
              False,
              "Spontaneous Company Search",
              info["site"],
              "",
              "Potential fit — no vacancy required",
              "CASABLANCA_SPONTANEOUS"
            )
        j["company_site"]=info["site"]
        j["spontaneous"]="YES"
        # enrich() will verify the public company email before writing it.
        try:
            u=enrich(j); j.update(u)
            if j.get("email_status")=="FOUND" and j.get("emails_rh"):
                key=domain+"|"+j["emails_rh"]
                if key not in seen:
                    seen.add(key); out.append(j)
                    print(f"[SPONTANEOUS EMAIL] {info['name']} -> {j['emails_rh']}",flush=True)
        except Exception as e:
            print(f"[SPONTANEOUS ERROR] {info['name']}: {e}",flush=True)
        time.sleep(.3)
    return out


# Every board is searched explicitly. Search-engine discovery remains a
# supplementary layer, never the only way a board is searched.
JOB_BOARD_SEARCHES = [
    ("Indeed", "indeed.com"),
    ("Emploi.ma", "emploi.ma"),
    ("ReKrute", "rekrute.com"),
    ("Bayt", "bayt.com"),
    ("Novojob", "novojob.com"),
    ("Optioncarriere", "optioncarriere.ma"),
    ("Glassdoor", "glassdoor.com"),
    ("LinkedIn Jobs", "linkedin.com/jobs"),
    ("Welcome to the Jungle", "welcometothejungle.com"),
    ("Wellfound", "wellfound.com"),
    ("Remotive", "remotive.com"),
    ("We Work Remotely", "weworkremotely.com"),
    ("Himalayas", "himalayas.app"),
    ("Jobgether", "jobgether.com"),
    ("Working Nomads", "workingnomads.com"),
    ("Remote OK", "remoteok.com"),
    ("TopCSJobs", "topcsjobs.com"),
    ("Support Driven", "supportdriven.com"),
]

def _board_queries(role_expression, domain, kind):
    """Keep board-by-board coverage broad without exploding the run time."""
    if kind == "WORLDWIDE_REMOTE":
        places = ['"remote worldwide"', '"fully remote"', '"work from anywhere"']
    elif kind == "MOROCCO_REMOTE":
        places = ['"remote Morocco"', '"work from Morocco"', 'Morocco remote']
    else:
        places = ['"Casablanca"', '"Casablanca Morocco"']

    # One compact query per location signal. The role expression contains all
    # relevant variants for that role family.
    return [
        f'site:{domain} ({role_expression}) {place} jobs'
        for place in places
    ]

def _search_specific_board(board,domain,role_expression,kind,remote):
    found=[];seen=set()
    for q in _board_queries(role_expression,domain,kind):
        items=web_search(q,20)
        parsed=parse_web_jobs(items,kind,remote)
        for j in parsed:
            host=urlparse(j.get("lien","")).netloc.lower().replace("www.","")
            if not (host==domain or host.endswith("."+domain)):
                continue
            if j["id"] in seen: continue
            seen.add(j["id"])
            j["source"]=board
            found.append(j)
        time.sleep(.2+random.random()*.4)
    return found

def public_web_jobs():
    out=[];seen=set()
    target_sets=[
        ("WORLDWIDE_REMOTE","remote",True),
        ("MOROCCO_REMOTE","Morocco remote",True),
        ("CASABLANCA_ONSITE","Casablanca",False),
    ]

    # Explicit board-by-board pass.
    for board,domain in JOB_BOARD_SEARCHES:
        board_total=0
        for family,variants in SEARCHES:
            role_expression=" OR ".join(f'"{v}"' for v in dict.fromkeys(variants))
            for kind,place,remote in target_sets:
                found=_search_specific_board(board,domain,role_expression,kind,remote)
                board_total += len(found)
                for j in found:
                    if j["id"] in seen: continue
                    seen.add(j["id"])
                    out.append(j)
        print(f"[BOARD SEARCH] {board}: discovered={board_total}",flush=True)

    # General public-web pass remains supplementary and can discover boards or
    # company career pages that are not in the fixed board list.
    for family,variants in SEARCHES:
        seeds=list(dict.fromkeys(variants[:3]))
        for seed in seeds:
            for kind,place,remote in target_sets:
                queries=[
                  f'"{seed}" {place} jobs',
                  f'"{seed}" {place} hiring',
                  f'"{seed}" {place} recrutement',
                ]
                for sq in queries:
                    items=web_search(sq,20)
                    found=parse_web_jobs(items,kind,remote)
                    for j in found:
                        if j["id"] in seen: continue
                        seen.add(j["id"])
                        out.append(j)
                    time.sleep(.3+random.random()*.5)

    print(f"[Web + ALL BOARDS] total={len(out)}",flush=True)
    return out

def company_site(company):
    """Resolve the employer's official domain independently of the job portal."""
    if not company or company=="Unknown": return None
    company=clean(company)
    for q in [f'"{company}" official website', f'"{company}" contact', f'"{company}" careers']:
        for _,u in web_search(q,20):
            host=urlparse(u).netloc.lower().replace("www.","")
            if not host or any(host==b or host.endswith("."+b) for b in BLOCKED):
                continue
            if any(x in host for x in ("careerjet","jobboard")):
                continue
            return "https://"+host
    return None

def extract_emails(html):
    text=re.sub(r"\s*(?:\[at\]|\(at\)|\{at\})\s*","@",html,flags=re.I)
    text=re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*",".",text,flags=re.I)
    soup=BeautifulSoup(text,"html.parser");found=set(EMAIL_RE.findall(text))
    for a in soup.select('a[href^="mailto:"]'):found.add(a.get("href","")[7:].split("?")[0])
    return {e.lower().strip(" .;,<>\"'") for e in found if "@" in e and e.lower().split("@")[-1] not in BAD and not e.lower().startswith(("noreply@","no-reply@","privacy@","security@"))}

def extract_salary(text):
    x=clean(text)
    patterns=[
      r"(?:€|EUR|USD|\$|£|GBP)\s?([0-9]{2,3}(?:[.,][0-9]{3})?(?:[.,][0-9]{2})?)\s*(?:k|K)?",
      r"([0-9]{2,3}(?:[.,][0-9]{3})?)\s?(?:k|K)\s?(?:€|EUR|USD|\$|£|GBP)",
      r"(?:salary|compensation|pay|package|salaire)\s*[:\-]?\s*([^\n|]{3,40})"
    ]
    for p in patterns:
        m=re.search(p,x,re.I)
        if m:
            return clean(m.group(0))
    return ""

def fit_job(j):
    text=clean(" ".join(str(j.get(k,"")) for k in ("intitule","role_cible","description","entreprise"))).lower()
    score=50
    reasons=[]
    if any(k in text for k in ["customer success","account manager","client success","partner manager","customer experience"]):
        score+=15; reasons.append("Strong match with B2B customer/account experience")
    if any(k in text for k in ["saas","travel tech","travel technology","hospitality tech","ecommerce","shopify"]):
        score+=10; reasons.append("Relevant digital/travel/e-commerce environment")
    if "french" in text and "english" in text:
        score+=8; reasons.append("French + English requested")
    elif "french" in text or "english" in text:
        score+=4; reasons.append("Language match")
    if any(k in text for k in ["remote","work from home","distributed","home-based"]):
        score+=8; reasons.append("Remote-friendly")
    if any(k in text for k in ["async","autonomy","flexible","flexibility","wellbeing","work-life"]):
        score+=5; reasons.append("Positive flexibility/autonomy signal")
    salary_text=str(j.get("salary",""))
    sm=re.search(r"(?:€|eur|usd|\$|£|gbp)\s?([0-9]{2,3})(?:[.,]?[0-9]{0,3})?\s*k", salary_text, re.I)
    if sm:
        amount=int(sm.group(1))
        if ("€" in salary_text or "eur" in salary_text.lower()) and amount>=35:
            score+=10; reasons.append("Salary signal at or above €35k")
        elif ("$" in salary_text or "usd" in salary_text.lower()) and amount>=40:
            score+=10; reasons.append("Salary signal at or above $40k")
        elif amount>=30:
            score+=5; reasons.append("Salary disclosed")
    elif salary_text:
        score+=3; reasons.append("Salary disclosed")
    if any(k in text for k in ["cold call","cold calling","100 calls","high volume calls","high-volume calls","commission only","night shift","rotating shifts","weekends"]):
        score-=25; reasons.append("Potential high-pressure/unsocial-hours signal")
    if any(k in text for k in ["director","vp ","vice president","chief"]):
        score-=20; reasons.append("Above current seniority target")
    return max(0,min(100,score)), "; ".join(reasons)

def enrich(j):
    company=j.get("entreprise","")
    site=j.get("company_site") or company_site(company)
    if not site:
        return {"id":j["id"],"sheet":j.get("sheet"),"company_site":"","emails_rh":"",
                "deep_status":"NO_SITE","email_status":"NOT_FOUND","email_source":"",
                "salary":extract_salary(j.get("description",""))}
    p=urlparse(site);base=f"{p.scheme}://{p.netloc}";domain=p.netloc.lower().replace("www.","")
    es=set();pages=False
    for path in PATHS:
        h=fetch(base+path,10,1)
        if h:
            pages=True
            es|=extract_emails(h)
    queries=[
      f'"{company}" "{domain}" email',
      f'"{company}" "{domain}" careers recruitment',
      f'"{company}" "@{domain}"',
      f'"{company}" contact email',
      f'"{company}" careers email'
    ]
    for q in queries:
        for _,u in web_search(q,10):
            host=urlparse(u).netloc.lower().replace("www.","")
            if host==domain or host.endswith("."+domain):
                h=fetch(u,10,1)
                if h:
                    pages=True
                    es|=extract_emails(h)
    def score_email(e):
        local,dom=e.split("@",1);s=0
        if dom==domain or dom.endswith("."+domain):s-=50
        if any(k in local for k in ("career","recruit","recrut","talent","jobs","hiring","hr")):s-=20
        if local in ("info","contact"):s+=5
        if local in ("support","sales","admin"):s+=20
        return s
    selected=sorted(es,key=score_email)
    j["salary"]=extract_salary(j.get("description",""))
    fit_score,fit_reasons=fit_job(j)
    return {
      "id":j["id"],"sheet":j.get("sheet"),"company_site":site,
      "emails_rh":" / ".join(selected),
      "deep_status":"DONE" if pages else "SITE_FOUND_NO_PAGES",
      "email_status":"FOUND" if selected else ("NO_EMAIL" if pages else "NOT_FOUND"),
      "email_source":"Company website / public web" if selected else "",
      "salary":extract_salary(j.get("description","")),
      "fit_score":str(fit_score),
      "fit_reasons":fit_reasons
    }

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
    print(f"[START] {len(SEARCHES)} role families; freshness window=72h; EMAIL-FIRST=ON",flush=True)
    seen=set();email_found=0;source_totals={}
    for source_name,fn in [("LinkedIn",linkedin),("Indeed",indeed),("Web",public_web_jobs)]:
        SOURCE_STATS[source_name]={}
        try:
            jobs=fn()
        except Exception as e:
            stat(source_name,"errors");print(f"[SOURCE ERROR] {source_name}: {e}",flush=True);jobs=[]
        unique=[]
        for j in jobs:
            if j["id"] in seen:continue
            seen.add(j["id"]);unique.append(j)
        found=sum(1 for j in unique if j.get("email_status")=="FOUND")
        email_found+=found
        source_totals[source_name]={"discovered":len(unique),"email_found":found,"stats":SOURCE_STATS[source_name]}
        print(f"[SOURCE DONE] {source_name}: discovered={len(unique)} email_found={found}",flush=True)

    # Only after advertised-job discovery is exhausted, switch to proactive company hunting.
    try:
        spontaneous=spontaneous_casablanca()
        source_totals["Casablanca Spontaneous"]={"discovered":len(spontaneous),"email_found":len(spontaneous)}
        email_found+=len(spontaneous)
    except Exception as e:
        source_totals["Casablanca Spontaneous"]={"error":str(e)}
        print(f"[SPONTANEOUS ERROR] {e}",flush=True)

    # Final fallback, inspired by the Ausbildung scraper: keep every genuinely
    # discovered offer even when exhaustive public-email enrichment found nothing.
    # Email-bearing offers were already written progressively; Apps Script dedupes by ID.
    fallback={}
    for j in NO_EMAIL_BUFFER:
        fallback[j.get("id")]=j
    for j in list(fallback.values()):
        if j.get("email_status")=="FOUND" and j.get("emails_rh"):
            continue
        j["email_status"]=j.get("email_status") or "NO_EMAIL"
        j["email_source"]=""
    by_sheet={}
    aliases={"WORLDWIDE_REMOTE":"Worldwide Remote","MOROCCO_REMOTE":"Morocco Remote","CASABLANCA_ONSITE":"Casablanca Onsite","CASABLANCA_SPONTANEOUS":"Casablanca Spontaneous"}
    for j in fallback.values():
        st=j.get("search_type","")
        sheet=aliases.get(st,"Worldwide Remote")
        by_sheet.setdefault(sheet,[]).append(j)
    fallback_added=0
    for sheet,batch in by_sheet.items():
        try:
            result=post_jobs(batch,sheet)
            fallback_added+=int(result.get("added",0))
            print(f"[FINAL FALLBACK] {sheet}: no_email={len(batch)} added={result.get('added',0)}",flush=True)
        except Exception as e:
            print(f"[FINAL FALLBACK ERROR] {sheet}: {e}",flush=True)

    print(f"[DONE] total_unique={len(seen)} email_found={email_found} no_email_buffer={len(fallback)} fallback_added={fallback_added} source_totals={source_totals}",flush=True)
    post({"mode":"log","run":{"finished_at":now(),"total_unique":len(seen),"email_found":email_found,"no_email_buffer":len(fallback),"fallback_added":fallback_added,"source_stats":SOURCE_STATS}})


# ============================================================
# COMPANY-CENTRIC ENRICHMENT — aligned with ausbildung-scraper
# ============================================================

DEEP_SEARCH_TIMEOUT = 7
DEEP_SEARCH_WORKERS = 20
DEEP_CRAWL_SECONDS = 25
WEBHOOK_TIMEOUT = 120
WEBHOOK_RETRIES = 3
WEBHOOK_BATCH_SIZE = 25

DEEP_CONTACT_WORDS = (
    "kontakt","contact","impressum","ansprechpartner","karriere","career",
    "bewerbung","bewerben","jobs","job","personal","hr","recruit","recruiting",
    "human resources","talent","hiring","team"
)
DEEP_BAD_DOMAINS = set(BLOCKED) | {
    "stepstone.de","stepstone.com","careerjet.com","careerjet.de",
    "jobrapido.com","jooble.org","adzuna.com","talent.com","simplyhired.com",
    "ziprecruiter.com","jobisjob.com","kununu.com","xing.com"
}
DEEP_BAD_EMAIL_LOCALS = {
    "noreply","no-reply","donotreply","do-not-reply","mailer-daemon",
    "postmaster","hostmaster","privacy","security"
}

def _norm_company(name):
    x=clean(name).lower()
    x=re.sub(r"\b(gmbh|ag|kg|ohg|e\.k\.|gmbh\s*&\s*co\.?\s*kg|ug|se|mbh|ltd|limited|inc|llc|corp)\b"," ",x)
    return clean(x)

def _host(url):
    try:
        h=urlparse(url).netloc.lower().split(":")[0]
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""

def _bad_domain(url):
    h=_host(url)
    return not h or any(h==d or h.endswith("."+d) for d in DEEP_BAD_DOMAINS)

def _company_tokens(company):
    generic={"gmbh","ag","kg","ohg","ug","se","mbh","co","group","holding","company",
             "deutschland","france","morocco","international","ltd","limited","inc","llc"}
    return [x for x in re.findall(r"[a-z0-9äöüß]{3,}",_norm_company(company)) if x not in generic]

def _deep_email(value):
    text=str(value or "")
    text=re.sub(r"\s*(?:\[|\(|\{)\s*(?:at|ät)\s*(?:\]|\)|\})\s*","@",text,flags=re.I)
    text=re.sub(r"\s*(?:\[|\(|\{)\s*(?:dot|punkt)\s*(?:\]|\)|\})\s*",".",text,flags=re.I)
    for e in EMAIL_RE.findall(text):
        e=e.strip(" <>.,;:\"'()[]").lower()
        if not re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,63}",e):
            continue
        local,dom=e.rsplit("@",1)
        if local in DEEP_BAD_EMAIL_LOCALS or dom in BAD:
            continue
        return e
    soup=BeautifulSoup(text,"html.parser")
    for a in soup.select('a[href^="mailto:"]'):
        e=a.get("href","")[7:].split("?",1)[0].strip().lower()
        if re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,63}",e):
            local=e.split("@",1)[0]
            if local not in DEEP_BAD_EMAIL_LOCALS and e.split("@",1)[1] not in BAD:
                return e
    return ""

def _verify_official_site(url,company):
    if _bad_domain(url): return "",""
    h=_host(url)
    if not h: return "",""
    root="https://"+h+"/"
    tokens=_company_tokens(company)
    try:
        r=S.get(root,headers=hdr(),timeout=DEEP_SEARCH_TIMEOUT,allow_redirects=True)
        if r.status_code!=200 or not r.text: return "",""
        final="https://"+_host(r.url)+"/"
        if _bad_domain(final): return "",""
        soup=BeautifulSoup(r.text,"html.parser")
        title=clean(soup.title.get_text(" ",strip=True) if soup.title else "").lower()
        text=clean(soup.get_text(" ",strip=True)).lower()
        if tokens and not any(t in text or t in title for t in tokens):
            if not any(t in _host(final) for t in tokens):
                return "",""
        return final,_deep_email(r.text)
    except requests.RequestException:
        return "",""

def _site_pages(site):
    root=site.rstrip("/")+"/"
    urls=[];seen=set()
    def add(u):
        if not u:return
        u=u.split("#",1)[0]
        if _host(u)==_host(root) and u not in seen:
            seen.add(u);urls.append(u)
    add(root)
    fixed=[
      "/kontakt","/contact","/impressum","/ansprechpartner","/karriere","/career",
      "/bewerbung","/jobs","/ausbildung","/stellenangebote","/jobs-karriere",
      "/bewerber","/personal","/hr","/recruitment","/human-resources",
      "/contact-us","/careers","/join-us","/work-with-us","/fr/contact",
      "/fr/recrutement","/en/contact","/en/careers"
    ]
    try:
        r=S.get(root,headers=hdr(),timeout=DEEP_SEARCH_TIMEOUT)
        if r.status_code==200:
            soup=BeautifulSoup(r.text,"html.parser")
            for a in soup.find_all("a",href=True):
                href=urljoin(root,a["href"])
                val=(href+" "+clean(a.get_text(" ",strip=True))).lower()
                if any(w in val for w in DEEP_CONTACT_WORDS): add(href)
            for sm in ("sitemap.xml","sitemap_index.xml"):
                try:
                    sr=S.get(urljoin(root,sm),headers=hdr(),timeout=DEEP_SEARCH_TIMEOUT)
                    if sr.status_code==200:
                        ss=BeautifulSoup(sr.text,"xml")
                        for loc in ss.find_all("loc"):
                            href=clean(loc.get_text(" ",strip=True))
                            if any(w in href.lower() for w in DEEP_CONTACT_WORDS): add(href)
                except requests.RequestException: pass
    except requests.RequestException: pass
    for p in fixed: add(urljoin(root,p.lstrip("/")))
    return urls

def _crawl_verified_site(site):
    queue=_site_pages(site)
    seen=set(queue)
    started=time.monotonic()
    while queue and time.monotonic()-started < DEEP_CRAWL_SECONDS:
        u=queue.pop(0)
        try:
            r=S.get(u,headers=hdr(),timeout=DEEP_SEARCH_TIMEOUT,allow_redirects=True)
            if r.status_code!=200 or not r.text: continue
            ct=r.headers.get("Content-Type","").lower()
            if ct and "html" not in ct and "xml" not in ct: continue
            e=_deep_email(r.text)
            if e:return e
            soup=BeautifulSoup(r.text,"html.parser")
            for a in soup.find_all("a",href=True):
                href=urljoin(r.url,a["href"])
                val=(href+" "+clean(a.get_text(" ",strip=True))).lower()
                if _host(href)==_host(site) and any(w in val for w in DEEP_CONTACT_WORDS) and href not in seen:
                    seen.add(href);queue.append(href)
        except requests.RequestException: pass
    return ""


def web_search(q,limit=10):
    """Multi-engine public search. Provider failure is never treated as zero results."""
    providers=[
      ("Bing","https://www.bing.com/search?q="+quote_plus(q)),
      ("DDG","https://html.duckduckgo.com/html/?q="+quote_plus(q)),
      ("DDG-Lite","https://lite.duckduckgo.com/lite/?q="+quote_plus(q))
    ]
    merged=[];seen=set()
    for name,u in providers:
        try:
            r=S.get(u,headers=hdr(),timeout=8,allow_redirects=True)
            if r.status_code!=200 or not r.text:
                stat("Search",f"{name}_{r.status_code}")
                continue
            soup=BeautifulSoup(r.text,"html.parser")
            links=[]
            if name=="Bing":
                for a in soup.select("li.b_algo h2 a[href]"):
                    links.append((clean(a.get_text(" ",strip=True)),a.get("href","")))
            else:
                for a in soup.select("a.result__a[href],a.result-link[href],a[href]"):
                    title=clean(a.get_text(" ",strip=True));href=a.get("href","")
                    if title and href.startswith("http"):links.append((title,href))
            for title,u2 in links:
                if not u2.startswith("http"):continue
                key=u2.split("#",1)[0]
                if key in seen:continue
                seen.add(key);merged.append((title,u2))
                if len(merged)>=limit: break
            if len(merged)>=limit: break
        except requests.RequestException:
            stat("Search",f"{name}_exception")
    if merged: stat("Search","merged")
    return merged[:limit]

def _search_company_web(company,location=""):
    company=clean(company)
    key=_norm_company(company)
    if not key or key in {"unknown","indeed employer","company"}: return "",""
    queries=[
      f'"{company}" official website',
      f'"{company}" contact impressum',
      f'"{company}" careers recruitment',
      f'"{company}" Bewerbung E-Mail',
      f'"{company}" Ansprechpartner E-Mail',
      f'"{company}" "{location}" contact' if location else f'"{company}" contact email'
    ]
    candidates=[];seen=set()
    for q in queries:
        for title,u in web_search(q,20):
            if _bad_domain(u): continue
            h=_host(u)
            if not h: continue
            toks=_company_tokens(company)
            score=0
            for t in toks:
                if t in h: score+=10
                if t in (title+" "+u).lower(): score+=2
            if any(w in (title+" "+u).lower() for w in ("official","contact","impressum","careers","career")): score+=2
            if score<=0: continue
            k=(h,u)
            if k not in seen:
                seen.add(k);candidates.append((score,u))
    for _,u in sorted(candidates,key=lambda x:x[0],reverse=True):
        site,home_email=_verify_official_site(u,company)
        if not site: continue
        e=home_email or _crawl_verified_site(site)
        if e:return e,site
    return "",""

def enrich_missing_emails(jobs):
    """One deep enrichment pass per unique company, then copy result to every offer."""
    groups={}
    for j in jobs:
        if j.get("emails_rh"): continue
        company=clean(j.get("entreprise",""))
        key=_norm_company(company)
        if not key: continue
        groups.setdefault(key,{"company":company,"location":clean(j.get("lieu","")),"jobs":[]})["jobs"].append(j)
    print(f"[DEEP] unique companies to verify: {len(groups)}",flush=True)
    def one(group):
        try:
            e,site=_search_company_web(group["company"],group["location"])
            return group,e,site,"DONE"
        except Exception as exc:
            print(f"[DEEP ERROR] {group['company']}: {exc}",flush=True)
            return group,"","", "ERROR"
    if groups:
        with ThreadPoolExecutor(max_workers=DEEP_SEARCH_WORKERS) as ex:
            futures=[ex.submit(one,g) for g in groups.values()]
            for fut in as_completed(futures):
                group,e,site,status=fut.result()
                for j in group["jobs"]:
                    j["company_site"]=site or j.get("company_site","")
                    j["emails_rh"]=e or ""
                    j["deep_status"]=status
                    j["email_status"]="FOUND" if e else "NO_EMAIL"
                    j["email_source"]="Verified official company site" if e else ""
                    if not j.get("salary"): j["salary"]=extract_salary(j.get("description",""))
                    score,reasons=fit_job(j)
                    j["fit_score"]=str(score);j["fit_reasons"]=reasons
                if e:
                    print(f"[DEEP FOUND] {group['company']} -> {e} | offers={len(group['jobs'])}",flush=True)
                else:
                    print(f"[DEEP NO EMAIL] {group['company']} | offers={len(group['jobs'])}",flush=True)
    for j in jobs:
        if not j.get("emails_rh") and not j.get("email_status"):
            j["email_status"]="NO_EMAIL"
    return jobs

def send_progressive(jobs,label="progress"):
    """Discovery stage only: never discard a job because email enrichment is pending."""
    if not jobs:return []
    for j in jobs:
        NO_EMAIL_BUFFER.append(j)
    print(f"[DISCOVERY BUFFER] {label}: {len(jobs)} offers buffered for company-level enrichment",flush=True)
    return jobs

def _webhook_url():
    u=os.getenv("GOOGLE_SHEET_WEBHOOK_URL","").strip().strip('"').strip("'").replace("\\","")
    if not u:
        raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")

    # Normalize a pasted Apps Script URL safely. Do not use a regex with
    # escaped backslashes here: that previously rejected valid https://.../exec
    # secrets because the character class was checking for a literal "\\s".
    if not re.match(r"^https?://",u,re.I):
        u="https://"+u.lstrip("/")

    parsed=urlparse(u)
    if parsed.scheme.lower() not in ("http","https") or not parsed.netloc:
        raise RuntimeError(
            "GOOGLE_SHEET_WEBHOOK_URL is invalid. Expected the complete "
            "Google Apps Script /exec URL."
        )
    if any(ch.isspace() for ch in u):
        raise RuntimeError(
            "GOOGLE_SHEET_WEBHOOK_URL is invalid: the secret contains whitespace."
        )
    return u

def post(payload,expected_status="success"):
    webhook=_webhook_url()
    last_error=""
    for attempt in range(1,WEBHOOK_RETRIES+1):
        try:
            r=S.post(webhook,json=payload,allow_redirects=True,timeout=(15,WEBHOOK_TIMEOUT),
                     headers={"Content-Type":"application/json"})
            r.raise_for_status()
            try:data=r.json()
            except ValueError:data={"raw":r.text[:500]}
            if isinstance(data,dict) and data.get("status")==expected_status:return data
            if isinstance(data,dict) and data.get("status")=="error":
                raise RuntimeError(str(data.get("message")))
            last_error=f"Unexpected webhook response: {data!r}"
        except (requests.RequestException,RuntimeError) as exc:
            last_error=str(exc);print(f"[WEBHOOK] attempt {attempt}/{WEBHOOK_RETRIES}: {last_error}",flush=True)
            if attempt<WEBHOOK_RETRIES: time.sleep(10*attempt)
    raise RuntimeError(f"Webhook failed after retries: {last_error}")

def post_jobs(jobs,sheet):
    if not jobs:return {"added":0}
    total=0
    for j in jobs:j["sheet"]=sheet
    for i in range(0,len(jobs),WEBHOOK_BATCH_SIZE):
        batch=jobs[i:i+WEBHOOK_BATCH_SIZE]
        print(f"[SHEET] {sheet}: sending {len(batch)} jobs",flush=True)
        data=post({"mode":"jobs","jobs":batch,"sheet":sheet})
        total+=int(data.get("added",0))
    return {"added":total}

def _sheet_for(j):
    return {
      "WORLDWIDE_REMOTE":"Worldwide Remote",
      "MOROCCO_REMOTE":"Morocco Remote",
      "CASABLANCA_ONSITE":"Casablanca Onsite",
      "CASABLANCA_SPONTANEOUS":"Casablanca Spontaneous"
    }.get(j.get("search_type",""),"Worldwide Remote")

def spontaneous_casablanca():
    """Discover companies only after advertised-job discovery, without requiring a vacancy."""
    queries=[
      '"multinationale" Casablanca recrutement',
      '"multinational" Casablanca Morocco careers',
      '"international company" Casablanca Morocco careers',
      '"shared services" Casablanca Morocco recruitment',
      '"BPO" Casablanca Morocco headquarters careers',
      '"SaaS" Casablanca Morocco company',
      '"travel" Casablanca Morocco company careers',
      '"logistics" Casablanca Morocco company careers',
      '"ecommerce" Casablanca Morocco company careers',
      '"FMCG" Casablanca Morocco company careers'
    ]
    companies={}
    for q in queries:
        for title,u in web_search(q,20):
            h=_host(u)
            if _bad_domain(u) or any(x in h for x in ("linkedin","indeed","glassdoor","bayt","rekrute","emploi.ma","novojob","optioncarriere")):
                continue
            name=clean(re.sub(r"\s*[-|–]\s*(careers|jobs|recruitment|casablanca).*$","",title,flags=re.I))
            if len(name)<2:name=h.split(".")[0].replace("-"," ").title()
            companies[h]={"name":name,"site":"https://"+h}
    out=[]
    for h,info in companies.items():
        j=job("Customer Success / Account Management / Sales Administration / Executive Support",
              info["name"],"Casablanca, Morocco",False,"Spontaneous Company Search",
              info["site"],"","Potential fit — spontaneous application","CASABLANCA_SPONTANEOUS")
        j["company_site"]=info["site"];j["spontaneous"]="YES"
        out.append(j)
    print(f"[SPONTANEOUS] companies discovered: {len(out)}",flush=True)
    return out

def scrape():
    _webhook_url()
    print("[START] discovery first; company-level email enrichment second; no-email offers kept",flush=True)
    NO_EMAIL_BUFFER.clear()
    seen={};source_totals={}
    for source_name,fn in [("LinkedIn",linkedin),("Indeed",indeed),("Web",public_web_jobs)]:
        SOURCE_STATS[source_name]={}
        try:jobs=fn()
        except Exception as e:
            stat(source_name,"errors");print(f"[SOURCE ERROR] {source_name}: {e}",flush=True);jobs=[]
        added=0
        for j in jobs:
            if j.get("id") in seen: continue
            seen[j.get("id")]=j;added+=1
        source_totals[source_name]={"discovered":added,"stats":SOURCE_STATS[source_name]}
        print(f"[SOURCE DONE] {source_name}: discovered={added}",flush=True)

    try:
        spontaneous=spontaneous_casablanca()
        for j in spontaneous:
            if j.get("id") not in seen:seen[j["id"]]=j
        source_totals["Casablanca Spontaneous"]={"discovered":len(spontaneous)}
    except Exception as e:
        source_totals["Casablanca Spontaneous"]={"error":str(e)}
        print(f"[SPONTANEOUS ERROR] {e}",flush=True)

    jobs=list(seen.values())
    print(f"[COLLECTED] unique offers={len(jobs)}; unique companies={len({_norm_company(j.get('entreprise','')) for j in jobs})}",flush=True)
    enrich_missing_emails(jobs)

    by_sheet={}
    for j in jobs:
        j["email_status"]="FOUND" if j.get("emails_rh") else ("NO_EMAIL" if j.get("email_status")!="ERROR" else "ERROR")
        by_sheet.setdefault(_sheet_for(j),[]).append(j)

    added_total=0
    for sheet,batch in by_sheet.items():
        result=post_jobs(batch,sheet)
        added_total+=int(result.get("added",0))
        print(f"[FINAL SHEET] {sheet}: {len(batch)} offers, added={result.get('added',0)}",flush=True)

    run={"finished_at":now(),"total_unique":len(jobs),
         "email_found":sum(1 for j in jobs if j.get("emails_rh")),
         "no_email":sum(1 for j in jobs if not j.get("emails_rh")),
         "added":added_total,"source_totals":source_totals,"source_stats":SOURCE_STATS}
    print(f"[DONE] {run}",flush=True)
    post({"mode":"log","run":run})

def main():
    p=argparse.ArgumentParser();p.add_argument("--mode",choices=["scrape","deep"],default="scrape");a=p.parse_args()
    scrape() if a.mode=="scrape" else deep()
if __name__=="__main__":main()
