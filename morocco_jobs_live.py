"""Morocco/Casablanca LinkedIn scraper. Discovery is written to Sheets in batches while scraping; deep search is separate."""
import argparse, hashlib, os, re, time
from urllib.parse import quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup
WEBHOOK=os.getenv("GOOGLE_SHEET_WEBHOOK_URL","").strip(); SHEET="Morocco Jobs"
ROLES=["Customer Success Manager","Customer Success Specialist","Customer Success Associate","Account Manager","Key Account Manager","Customer Account Manager","Client Success Manager","Customer Support Specialist","Customer Experience Specialist","Sales Executive","Account Executive","Business Development Representative","Sales Development Representative","Inside Sales Representative","B2B Sales Representative","Operations Coordinator","Business Operations Specialist","Administrative Coordinator","Administrative Assistant","Sales Operations Specialist","Commercial Operations Specialist","Back Office Specialist","Project Coordinator","E-commerce Specialist","Shopify Specialist","CRM Specialist","Digital Marketing Specialist","Junior UX UI Designer","Junior UX Designer","Junior UI Designer"]
EXCLUDE=["senior","sr.","lead","principal","director","vp ","vice president","head of","chief","staff","architect","intern","doctor","nurse","software engineer","software developer","data scientist","machine learning","devops","lawyer","accountant","physician","warehouse worker","driver","technician"]
S=requests.Session(); UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
def clean(x): return re.sub(r"\s+"," ",str(x or "")).strip()
def target(t): return not any(x in t.lower() for x in EXCLUDE)
def get(url,timeout=15):
 try:
  r=S.get(url,headers={"User-Agent":UA,"Accept-Language":"en-US,en;q=0.9,fr;q=0.8"},timeout=timeout); return r.text if r.status_code==200 else None
 except requests.RequestException:return None
def make(title,company,loc,url,remote): return {"id":"ma_"+hashlib.sha256((url+"|"+title).encode()).hexdigest()[:16],"date_detection":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"role_cible":title,"intitule":title,"entreprise":clean(company) or "Unknown","lieu":clean(loc) or "Morocco / Casablanca","remote":remote,"source":"LinkedIn","lien":url,"company_site":"","emails_rh":"","deep_status":"PENDING","fit_score":"","fit_reasons":"","salary":"","description":""}
def parse_li(html):
 soup=BeautifulSoup(html,"html.parser"); out=[]; seen=set()
 for a in soup.select('a[href*="/jobs/view/"]'):
  h=a.get("href","").split("?")[0]; m=re.search(r"/jobs/view/(?:[^/]+-)?(\d+)",h)
  if not m or m.group(1) in seen: continue
  seen.add(m.group(1)); card=a.find_parent(class_=re.compile("base-card|job-search-card|result-card")) or a.parent; t=clean(a.get_text(" ",strip=True)); c=l=""
  if card:
   x=card.select_one(".base-search-card__subtitle,.hidden-nested-link"); y=card.select_one(".job-search-card__location"); c=clean(x.get_text(" ",strip=True) if x else ""); l=clean(y.get_text(" ",strip=True) if y else "")
  if t and target(t): out.append(make(t,c,l,urljoin("https://www.linkedin.com",h),True))
 return out
def indexed(role,geo):
 html=get("https://html.duckduckgo.com/html/?q="+quote_plus(f'site:linkedin.com/jobs/view/ "{role}" "{geo}"'))
 if not html:return []
 soup=BeautifulSoup(html,"html.parser"); out=[]; seen=set()
 for res in soup.select(".result"):
  a=res.select_one("a.result__a"); raw=str(res)
  if not a:continue
  m=re.search(r"https?://(?:www\.)?linkedin\.com/jobs/view/[^\s\"&<>]+",raw,re.I)
  if not m:continue
  u=m.group(0).rstrip("')>\"").split("?")[0]
  if u in seen:continue
  seen.add(u); t=clean(re.sub(r"\s*\|\s*LinkedIn$","",a.get_text(" ",strip=True),flags=re.I)); c=""
  if " - " in t:t,c=t.split(" - ",1);t=t.strip();c=c.strip()
  if not t or not target(t):continue
  sn=clean(res.get_text(" ",strip=True)).lower(); loc="Casablanca, Morocco" if "casablanca" in sn else ("Morocco" if "morocco" in sn or "maroc" in sn else geo)
  out.append(make(t,c,loc,u,"remote" in (t+" "+sn)))
 return out
def post(payload):
 if not WEBHOOK:raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
 p=dict(payload);p["sheet"]=SHEET;r=requests.post(WEBHOOK,json=p,timeout=30);r.raise_for_status();print("[Sheet]",r.status_code,r.text[:250])
def discover_live():
 seen=set()
 for role in ROLES:
  direct=parse_li(get(f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(role)}&f_WT=2&location=Morocco&start=0") or "")
  candidates=direct or indexed(role,"Morocco remote")
  batch=[j for j in candidates if j["id"] not in seen]
  for j in batch:seen.add(j["id"])
  if batch:post({"mode":"jobs","jobs":batch});print(f"[DISCOVERY] {role}: +{len(batch)} written immediately")
 for role in ROLES:
  for geo in ("Casablanca","Casablanca Morocco"):
   batch=[]
   for j in indexed(role,geo):
    if j["id"] not in seen:seen.add(j["id"]);batch.append(j)
   if batch:post({"mode":"jobs","jobs":batch});print(f"[CASABLANCA] {role} / {geo}: +{len(batch)} written immediately")
 print(f"DONE: {len(seen)} Morocco/Casablanca LinkedIn jobs written during discovery")
def pending(limit):
 r=requests.post(WEBHOOK,json={"mode":"pending","sheet":SHEET,"limit":limit},timeout=30);r.raise_for_status()
 try:d=r.json()
 except ValueError as e:raise RuntimeError(f"Apps Script pending response is not JSON: {r.text[:300]!r}. Redeploy /exec.") from e
 if d.get("status")!="success":raise RuntimeError(str(d))
 return d.get("jobs",[])
EMAIL=re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}");BAD={"example.com","sentry.io","schema.org","google.com","facebook.com","linkedin.com"};SKIP=("noreply@","no-reply@","privacy@","security@","support@","mailer-daemon@");PATHS=["","/contact","/contact-us","/careers","/jobs","/about","/imprint","/impressum","/en/contact","/en/careers"]
def site(company):
 if not company or company=="Unknown":return None
 h=get("https://html.duckduckgo.com/html/?q="+quote_plus(f'"{company}" official website careers jobs'))
 if not h:return None
 blocked=("linkedin.com","facebook.com","instagram.com","twitter.com","x.com","indeed.com","glassdoor.com","crunchbase.com","wikipedia.org","duckduckgo.com")
 for a in BeautifulSoup(h,"html.parser").select("a.result__a"):
  u=a.get("href","")
  if u.startswith("http"):
   host=urlparse(u).netloc.lower().replace("www.","")
   if host and not any(x in host for x in blocked):return "https://"+host
 return None
def extract(h):
 h=re.sub(r"\s*(?:\[at\]|\(at\)|\{at\})\s*","@",h,flags=re.I);h=re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*",".",h,flags=re.I); found=set(EMAIL.findall(h));soup=BeautifulSoup(h,"html.parser")
 for a in soup.select('a[href^="mailto:"]'):found.add(a.get("href","")[7:].split("?")[0])
 return {e.lower().strip(" .;,<>\"'") for e in found if "@" in e and e.lower().split("@")[-1] not in BAD and not e.lower().startswith(SKIP)}
def enrich(j):
 u=j.get("company_site") or site(j.get("entreprise",""))
 if not u:return {"id":j["id"],"company_site":"","emails_rh":"","deep_status":"NO_SITE"}
 p=urlparse(u);base=f"{p.scheme}://{p.netloc}";found=set();pages=0
 for path in PATHS:
  h=get(base+path,10)
  if h:pages+=1;found|=extract(h)
 ranked=sorted(found,key=lambda e:(0 if any(k in e for k in ("career","recruit","hr","talent","jobs","hiring")) else 1,len(e)))
 return {"id":j["id"],"company_site":u,"emails_rh":" / ".join(ranked[:3]),"deep_status":"DONE" if pages else "SITE_FOUND_NO_PAGES"}
def deep(limit):
 jobs=pending(limit);ups=[]
 for i,j in enumerate(jobs,1):
  print(f"[DEEP] {i}/{len(jobs)} {j.get('entreprise')} — {j.get('intitule')}")
  try:ups.append(enrich(j))
  except Exception as e:ups.append({"id":j.get("id"),"deep_status":"ERROR","deep_error":str(e)[:200]})
 if ups:post({"mode":"enrich","updates":ups})
def main():
 p=argparse.ArgumentParser();p.add_argument("--mode",choices=("scrape","deep"),default="scrape");p.add_argument("--deep-limit",type=int,default=100);a=p.parse_args();discover_live() if a.mode=="scrape" else deep(a.deep_limit)
if __name__=="__main__":main()
