"""Morocco/Casablanca job -> company email finder. LinkedIn is discovery; Sheet output is emails only."""
import argparse,hashlib,os,re,time
from urllib.parse import quote_plus,urlparse
import requests
from bs4 import BeautifulSoup
WEBHOOK=os.getenv("GOOGLE_SHEET_WEBHOOK_URL","").strip()
ROLES=["Customer Success Manager","Customer Success Specialist","Customer Success Associate","Account Manager","Key Account Manager","Customer Account Manager","Client Success Manager","Customer Support Specialist","Customer Experience Specialist","Sales Executive","Account Executive","Business Development Representative","Sales Development Representative","Inside Sales Representative","B2B Sales Representative","Operations Coordinator","Business Operations Specialist","Administrative Coordinator","Administrative Assistant","Sales Operations Specialist","Commercial Operations Specialist","Back Office Specialist","Project Coordinator","E-commerce Specialist","Shopify Specialist","CRM Specialist","Digital Marketing Specialist","Junior UX UI Designer","Junior UX Designer","Junior UI Designer"]
EXCLUDE=["senior","sr.","lead","principal","director","vp ","vice president","head of","chief","staff","architect","intern","doctor","nurse","software engineer","software developer","data scientist","machine learning","devops","lawyer","accountant","physician","warehouse worker","driver","technician"]
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36";S=requests.Session()

def clean(x):return re.sub(r"\s+"," ",str(x or "")).strip()
def target(x):return not any(t in x.lower() for t in EXCLUDE)
def fetch(url,timeout=12):
 try:
  r=S.get(url,headers={"User-Agent":UA,"Accept-Language":"en-US,en;q=0.9,fr;q=0.8"},timeout=timeout,allow_redirects=True);return r.text if r.status_code==200 else None
 except requests.RequestException:return None

def indexed(role,geo):
 html=fetch("https://html.duckduckgo.com/html/?q="+quote_plus(f'site:linkedin.com/jobs/view/ "{role}" "{geo}"'))
 if not html:return []
 soup=BeautifulSoup(html,"html.parser");out=[];seen=set()
 for result in soup.select(".result"):
  a=result.select_one("a.result__a");m=re.search(r"https?://(?:www\.)?linkedin\.com/jobs/view/[^\s\"&<>]+",str(result),re.I)
  if not a or not m:continue
  url=m.group(0).rstrip("')>\"").split("?")[0]
  if url in seen:continue
  seen.add(url);title=clean(re.sub(r"\s*\|\s*LinkedIn$","",a.get_text(" ",strip=True),flags=re.I));company=""
  if " - " in title:title,company=title.split(" - ",1);title=title.strip();company=company.strip()
  if title and target(title):
   text=clean(result.get_text(" ",strip=True));low=text.lower();loc="Casablanca, Morocco" if "casablanca" in low else "Morocco" if "morocco" in low or "maroc" in low else geo
   out.append({"id":"ma_"+hashlib.sha256(url.encode()).hexdigest()[:16],"role":title,"company":company or "Unknown","location":loc,"url":url})
 return out

BAD={"example.com","sentry.io","schema.org","google.com","facebook.com","linkedin.com"};SKIP=("noreply@","no-reply@","privacy@","security@","support@","mailer-daemon@");ERE=re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PATHS=["","/contact","/contact-us","/careers","/jobs","/about","/imprint","/impressum","/en/contact","/en/careers"]

def company_site(company):
 if not company or company=="Unknown":return None
 h=fetch("https://html.duckduckgo.com/html/?q="+quote_plus(f'"{company}" official website careers jobs'))
 if not h:return None
 blocked=("linkedin.com","facebook.com","instagram.com","twitter.com","x.com","indeed.com","glassdoor.com","crunchbase.com","wikipedia.org","duckduckgo.com")
 for a in BeautifulSoup(h,"html.parser").select("a.result__a"):
  u=a.get("href","")
  if u.startswith("http"):
   host=urlparse(u).netloc.lower().replace("www.","")
   if host and not any(x in host for x in blocked):return "https://"+host
 return None

def extract(html):
 text=re.sub(r"\s*(?:\[at\]|\(at\)|\{at\})\s*","@",html,flags=re.I);text=re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*",".",text,flags=re.I);soup=BeautifulSoup(text,"html.parser");found=set(ERE.findall(text))
 for a in soup.select('a[href^="mailto:"]'):found.add(a.get("href","")[7:].split("?")[0])
 return {e.lower().strip(" .;,<>\"'") for e in found if "@" in e and e.lower().split("@")[-1] not in BAD and not e.lower().startswith(SKIP)}

def find_emails(company):
 site=company_site(company)
 if not site:return None,[]
 p=urlparse(site);base=f"{p.scheme}://{p.netloc}";found=set()
 for path in PATHS:
  h=fetch(base+path,10)
  if h:found|=extract(h)
 ranked=sorted(found,key=lambda e:(0 if any(k in e for k in ("career","recruit","hr","talent","jobs","hiring")) else 1,len(e)))
 return site,ranked[:3]

def send(contacts):
 if not WEBHOOK:raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
 r=S.post(WEBHOOK,json={"mode":"contacts","contacts":contacts},timeout=30);r.raise_for_status();print("[Sheet]",r.text[:300])

def run():
 seen_companies=set();batch=[]
 for role in ROLES:
  for geo in ("Morocco remote","Casablanca","Casablanca Morocco"):
   for j in indexed(role,geo):
    key=j["company"].lower()
    if key in seen_companies:continue
    seen_companies.add(key);print("[DEEP]",j["company"],"—",j["role"])
    site,emails=find_emails(j["company"])
    for e in emails:
     batch.append({"date_found":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"role":j["role"],"company":j["company"],"location":j["location"],"email":e,"email_type":"Recruitment/HR public email" if any(k in e for k in ("career","recruit","hr","talent","jobs","hiring")) else "Public company email","company_website":site or ""})
    if batch:send(batch);batch=[]
 if batch:send(batch)
 print("DONE: email-first Morocco/Casablanca search finished")

def main():
 p=argparse.ArgumentParser();p.add_argument("--mode",choices=("scrape","deep","email"),default="email");a=p.parse_args();run()
if __name__=="__main__":main()
