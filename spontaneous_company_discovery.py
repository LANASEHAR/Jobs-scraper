"""Find companies for spontaneous applications even when no vacancy is published."""
import hashlib, os, re, time
from urllib.parse import quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

WEBHOOK = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
MAX_COMPANIES = max(20, min(250, int(os.getenv("SPONTANEOUS_MAX_COMPANIES", "100"))))
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}", re.I)
BAD = {"example.com", "sentry.io", "schema.org", "google.com", "facebook.com"}
BLOCKED = {"linkedin.com","facebook.com","instagram.com","twitter.com","x.com","indeed.com",
 "glassdoor.com","crunchbase.com","wikipedia.org","duckduckgo.com","google.com","bing.com",
 "youtube.com","reddit.com","careerjet.com","talent.com","simplyhired.com","jooble.org"}
JOB_WORDS = ("linkedin", "indeed", "glassdoor", "rekrute", "emploi.ma", "bayt.com",
 "novojob", "careerjet", "jobgether", "remotive", "wellfound")
PATHS = ("", "contact", "contact-us", "about", "about-us", "company", "team", "our-team",
 "offices", "locations", "sales", "support", "partners", "press", "media", "legal",
 "imprint", "impressum", "fr/contact", "en/contact", "de/kontakt")
HEADERS = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
 "Accept-Language":"en-US,en;q=0.9,fr;q=0.8,de;q=0.7"}
QUERIES = [
 "SaaS companies official website contact email", "travel technology companies contact",
 "hospitality technology companies contact", "ecommerce platforms company contact",
 "customer experience software companies contact", "B2B services companies contact page",
 "international companies Morocco contact", "multinational companies Casablanca Morocco official website",
 "shared services companies Morocco contact", "business process outsourcing companies Morocco contact",
 "travel companies Morocco contact", "logistics companies Morocco contact", "ecommerce companies Morocco contact",
 "FMCG companies Morocco contact", "French SaaS companies contact", "European SaaS companies contact",
 "German Mittelstand companies contact", "Belgium technology companies contact",
 "Netherlands software companies contact", "UK customer support SaaS companies contact",
 "remote-first companies official website contact", "B2B marketplace companies contact",
 "CRM software companies contact", "payment technology companies contact", "HR technology companies contact",
 "digital agencies Morocco contact", "marketing technology companies contact", "hotel groups international contact",
 "airline travel technology companies contact", "procurement software companies contact",
 "supply chain software companies contact", "business services companies France contact",
 "business services companies Germany contact", "business services companies Spain contact",
 "business services companies Portugal contact"
]

def host(url):
 h = urlparse(url).netloc.lower().split(":")[0]
 return h[4:] if h.startswith("www.") else h

def blocked(url):
 h = host(url)
 return not h or any(h == d or h.endswith("." + d) for d in BLOCKED) or any(w in h for w in JOB_WORDS)

def get(url):
 try:
  r = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
  if r.status_code == 200 and "text/html" in r.headers.get("content-type", "text/html"):
   return r.text, r.url
 except requests.RequestException:
  pass
 return "", url

def search(query, limit=8):
 out, seen = [], set()
 for endpoint in ("https://html.duckduckgo.com/html/?q="+quote_plus(query),
                  "https://www.bing.com/search?q="+quote_plus(query)):
  try:
   r = requests.get(endpoint, headers=HEADERS, timeout=10)
   if r.status_code != 200: continue
   soup = BeautifulSoup(r.text, "html.parser")
   for selector in ("a.result__a[href]", "a.result-link[href]", "li.b_algo h2 a[href]"):
    for a in soup.select(selector):
     u = a.get("href", "").split("#",1)[0]
     if not u.startswith(("http://","https://")) or blocked(u) or u in seen: continue
     seen.add(u); out.append((a.get_text(" ",strip=True),u))
     if len(out) >= limit: return out
  except requests.RequestException as exc:
   print(f"[SEARCH WARNING] {type(exc).__name__}: {query}", flush=True)
  time.sleep(0.4)
 return out

def emails_from(html):
 text = re.sub(r"\s*(?:\[at\]|\(at\)|\{at\})\s*", "@", html or "", flags=re.I)
 text = re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*", ".", text, flags=re.I)
 soup = BeautifulSoup(text, "html.parser")
 found = set(EMAIL_RE.findall(text))
 for a in soup.select('a[href^="mailto:"]'):
  found.add(a.get("href","")[7:].split("?",1)[0])
 result = set()
 for e in found:
  e = e.lower().strip(" .;,<>")
  if EMAIL_RE.fullmatch(e) and e.split("@",1)[1] not in BAD and e.split("@",1)[0] not in {"noreply","no-reply","donotreply","postmaster"}:
   result.add(e)
 return result

def company_name(title, html):
 soup = BeautifulSoup(html, "html.parser")
 for selector in ('meta[property="og:site_name"]','meta[name="application-name"]'):
  node = soup.select_one(selector)
  if node and node.get("content","").strip(): return node["content"].strip()[:150]
 if soup.title and soup.title.get_text(strip=True): title = soup.title.get_text(" ",strip=True)
 name = re.split(r"\s+[|–—]\s+|\s+-\s+", title or "")[0].strip()
 name = re.sub(r"\b(official website|contact us|contact|about us|home page|careers|jobs)\b.*$","",name,flags=re.I).strip(" -|–—")
 if not 2 <= len(name) <= 100 or name.lower() in {"home","welcome","contact","about","careers","jobs","company"}: return ""
 if any(x in name.lower() for x in ("search results","login","sign in","job listing")): return ""
 return name

def crawl(site):
 found, seen = set(), set()
 domain, base = host(site), site.rstrip("/") + "/"
 queue = [urljoin(base,p) for p in PATHS]
 while queue and len(seen) < 18:
  url = queue.pop(0)
  if url in seen or host(url) != domain: continue
  seen.add(url)
  html, final = get(url)
  if not html: continue
  found |= emails_from(html)
  soup = BeautifulSoup(html, "html.parser")
  links = []
  for a in soup.select("a[href]"):
   href = a.get("href","")
   if href.startswith(("mailto:","tel:","javascript:")): continue
   target = urljoin(final,href).split("#",1)[0]
   if host(target) != domain or target in seen: continue
   label = (a.get_text(" ",strip=True)+" "+urlparse(target).path).lower()
   priority = 0 if any(k in label for k in ("contact","about","team","office","sales","support","press","partner","legal","imprint")) else 1
   links.append((priority,target))
  for _, target in sorted(links,key=lambda x:(x[0],len(x[1]))):
   if target not in seen and target not in queue: queue.append(target)
   if len(queue) > 30: break
 return found

def record(name, site, emails):
 morocco = any(x in (site+" "+name).lower() for x in ("morocco","maroc","casablanca","rabat"))
 sheet = "Casablanca Spontaneous" if morocco else "Worldwide Remote"
 kind = "CASABLANCA_SPONTANEOUS" if morocco else "REMOTE"
 title = "Spontaneous application — Customer Success / Account Management / Sales Administration"
 return {"id":"spont_"+hashlib.sha256(host(site).encode()).hexdigest()[:18],
  "date_detection":time.strftime("%Y-%m-%d"),"statut":"NEW","role_cible":title,"intitule":title,
  "entreprise":name,"lieu":"Casablanca, Morocco" if morocco else "International — spontaneous outreach",
  "remote":not morocco,"source":"Spontaneous Company Discovery","lien":site,"company_site":site,
  "emails_rh":" / ".join(sorted(emails)),"deep_status":"DONE" if emails else "PENDING",
  "fit_score":"","fit_reasons":"Company discovered independently of job vacancies","salary":"",
  "description":"Company found from public web sources; no active vacancy required.",
  "posted_age":"","posted_within_24h":"N/A","search_type":kind,
  "email_status":"FOUND" if emails else "PENDING",
  "email_source":"Public company website / public web" if emails else "",
  "spontaneous":"YES","sheet":sheet}

def write(sheet, rows):
 if not WEBHOOK: raise RuntimeError("GOOGLE_SHEET_WEBHOOK_URL is missing")
 last = ""
 for attempt in range(3):
  try:
   r = requests.post(WEBHOOK,json={"mode":"jobs","sheet":sheet,"jobs":rows},
     headers={"Content-Type":"application/json"},timeout=45)
   r.raise_for_status(); data = r.json()
   if data.get("status") == "success": return data
   last = f"unexpected response: {data}"
  except (requests.RequestException,ValueError) as exc: last = str(exc)
  if attempt < 2: time.sleep(2**attempt)
 raise RuntimeError(f"Google Sheets write failed after 3 attempts: {last}")

def main():
 if not WEBHOOK: raise SystemExit("ERROR: GOOGLE_SHEET_WEBHOOK_URL is missing")
 companies = {}
 for i, query in enumerate(QUERIES,1):
  print(f"[DISCOVERY {i}/{len(QUERIES)}] {query}",flush=True)
  for title,url in search(query,8):
   domain = host(url)
   if not domain or domain in companies: continue
   html, final = get(url)
   if not html: continue
   name = company_name(title,html)
   if not name: continue
   site = urlparse(final).scheme + "://" + host(final) + "/"
   emails = emails_from(html) | crawl(site)
   companies[domain] = record(name,site,emails)
   print(f"[COMPANY] {name} | {domain} | public_emails={len(emails)}",flush=True)
   if len(companies) >= MAX_COMPANIES: break
  if len(companies) >= MAX_COMPANIES: break
 groups = {}
 for row in companies.values(): groups.setdefault(row["sheet"],[]).append(row)
 for sheet,rows in groups.items():
  added = 0
  for i in range(0,len(rows),25): added += int(write(sheet,rows[i:i+25]).get("added",0))
  print(f"[SHEETS] {sheet}: discovered={len(rows)} added={added}",flush=True)
 print(f"[DONE] unique_companies={len(companies)} with_public_emails={sum(bool(x['emails_rh']) for x in companies.values())}",flush=True)

if __name__ == "__main__": main()
