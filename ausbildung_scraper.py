"""Ausbildung scraper: Kaufmann roles in Germany, email-first, CSV + Google Sheet."""
import csv, hashlib, os, re, sys, time
from urllib.parse import quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

SPECIALITES=[
("Kaufmann/-frau für Büromanagement","buero"),
("Kauffrau im E-Commerce","ecommerce"),
("Kaufmann/-frau im Groß- und Außenhandelsmanagement","handel"),
("Kaufmann/-frau für Spedition und Logistikdienstleistung","spedition"),
("Kaufmann/-frau für Tourismus und Freizeit","tourismus"),
]
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
EMAIL=re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD_DOMAINS={"sentry.io","google.com","facebook.com","linkedin.com","indeed.com","youtube.com","wikipedia.org","twitter.com","xing.com","stepstone.de","monster.de","ausbildung.de","azubiyo.de","bundesagentur.de","example.com","example.de"}
BAD_PREFIXES=("noreply@","no-reply@","support@","privacy@","security@","billing@","admin@","newsletter@","bounce@","mailer-daemon@")
RH_WORDS=("bewerbung","karriere","ausbildung","recruiting","personal","jobs","bewerber","hr")
DEEP=("","/kontakt","/karriere","/ausbildung","/impressum","/jobs","/stellenangebote","/bewerbung")


def fetch(url, timeout=10):
    try:
        r=requests.get(url,headers={"User-Agent":UA,"Accept":"text/html,application/xhtml+xml","Accept-Language":"de-DE,de;q=0.9,en;q=0.7"},timeout=timeout,allow_redirects=True)
        return r.text if r.status_code==200 else None
    except requests.RequestException:
        return None


def valid_email(e):
    e=e.lower().strip(" .;,:\"'<>")
    if not EMAIL.fullmatch(e): return None
    local,domain=e.split("@",1)
    if local.startswith(BAD_PREFIXES) or any(x in domain for x in BAD_DOMAINS): return None
    return e


def extract_emails(html):
    if not html: return set()
    text=re.sub(r"\s*(?:\[at\]|\(at\)|\{at\})\s*","@",html,flags=re.I)
    text=re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*",".",text,flags=re.I)
    found=set(valid_email(x) for x in EMAIL.findall(text)); found.discard(None)
    try:
        soup=BeautifulSoup(html,"lxml")
        for a in soup.find_all("a",href=True):
            h=a["href"]
            if h.lower().startswith("mailto:"):
                e=valid_email(h[7:].split("?",1)[0]);
                if e: found.add(e)
    except Exception: pass
    return found


def best_email(emails):
    if not emails: return ""
    return sorted(emails,key=lambda e:(-sum(k in e for k in RH_WORDS),len(e)))[:2][0]


def deep_company(site):
    if not site or not str(site).startswith("http"): return ""
    p=urlparse(site); base=f"{p.scheme}://{p.netloc}"; allm=set()
    for path in DEEP:
        html=fetch(urljoin(base,path) if path else site,7)
        if not html: continue
        emails=extract_emails(html); allm.update(emails)
        rh={e for e in emails if any(k in e for k in RH_WORDS)}
        if rh: return best_email(rh)
    return best_email(allm)


def find_site(company):
    if not company: return ""
    html=fetch("https://html.duckduckgo.com/html/?q="+quote_plus(company+" Ausbildung Bewerbung Kontakt Deutschland"),10)
    if not html: return ""
    try:
        soup=BeautifulSoup(html,"lxml")
        for a in soup.select("a.result__a"):
            h=a.get("href","")
            if not h.startswith("http"): continue
            d=urlparse(h).netloc.lower()
            if any(x in d for x in BAD_DOMAINS)|any(x in d for x in ("duckduckgo","arbeitsagentur","google","wikipedia")): continue
            return f"https://{d}"
    except Exception: pass
    return ""


def offer_email(url,company,site=""):
    emails=extract_emails(fetch(url,8))
    e=best_email(emails)
    if e: return e
    e=deep_company(site)
    if e: return e
    return deep_company(find_site(company))


def job_id(source,key): return source+"_"+hashlib.md5(key.encode()).hexdigest()[:10]


def scrape_ausbildung_de():
    out=[]
    for specialite,_ in SPECIALITES:
        for page in range(1,11):
            html=fetch("https://www.ausbildung.de/suche/?q="+quote_plus(specialite)+"&seite="+str(page),10)
            if not html: break
            soup=BeautifulSoup(html,"lxml")
            links=[]
            for a in soup.find_all("a",href=re.compile(r"/stellen/")):
                h=a.get("href",""); u=urljoin("https://www.ausbildung.de",h)
                if u not in links: links.append(u)
            if not links: break
            for u in links[:50]:
                title=soup.find("h1").get_text(" ",strip=True) if soup.find("h1") else specialite
                m=re.search(r"bei-([a-z0-9\-]+)-in-",u)
                company=m.group(1).replace("-"," ").title() if m else "Unternehmen Deutschland"
                e=offer_email(u,company)
                if e: out.append(dict(date_detection=time.strftime("%Y-%m-%d %H:%M"),statut="NOUVEAU",role_cible=specialite,intitule=title,entreprise=company,lieu="Deutschland",emails_rh=e,source="Ausbildung.de",lien=u,id=job_id("aus",u)))
    return out


def scrape_duckduckgo():
    out=[]
    for specialite,_ in SPECIALITES:
        q=quote_plus('Ausbildung '+specialite+' Bewerbung bewerbung@ kontakt@ Deutschland 2026')
        html=fetch("https://html.duckduckgo.com/html/?q="+q+"&kl=de-de",12)
        if not html: continue
        soup=BeautifulSoup(html,"lxml")
        for a in soup.select("a.result__a")[:10]:
            h=a.get("href","")
            if not h.startswith("http"): continue
            d=urlparse(h).netloc.lower()
            if any(x in d for x in ("linkedin","facebook","xing","indeed","stepstone","ausbildung.de","azubiyo","duckduckgo","google","arbeitsagentur")): continue
            site=f"https://{d}"; company=d.replace("www.","").split(".")[0].title(); e=deep_company(site)
            if e: out.append(dict(date_detection=time.strftime("%Y-%m-%d %H:%M"),statut="NOUVEAU",role_cible=specialite,intitule="Ausbildung "+specialite+" - Candidature Spontanée",entreprise=company,lieu="Deutschland",emails_rh=e,source="Candidature Spontanée (DuckDuckGo)",lien=site,id=job_id("sp",site)))
    return out


def existing_ids(path):
    if not os.path.exists(path): return set()
    try:
        with open(path,encoding="utf-8-sig") as f: return {r.get("id","") for r in csv.DictReader(f)}
    except Exception: return set()


def send_to_sheet(jobs):
    url=os.environ.get("GOOGLE_SHEET_WEBHOOK_URL","").strip()
    if not url: return False
    ok=True
    for i in range(0,len(jobs),50):
        batch=jobs[i:i+50]
        try:
            r=requests.post(url,json={"mode":"jobs","sheet":"Ausbildung Jobs","jobs":batch},headers={"Content-Type":"application/json"},timeout=25)
            text=r.text.strip()
            if r.status_code!=200 or not text: ok=False; print(f"[!] Webhook HTTP {r.status_code}: {text[:200]}"); continue
            try: data=r.json(); print(f"[OK] Sheet batch {i//50+1}: {data.get('added','?')} added")
            except ValueError: ok=False; print(f"[!] Webhook non-JSON: {text[:200]}")
        except requests.RequestException as e: ok=False; print("[!] Webhook error:",e)
    return ok


def main():
    path="ausbildung_applications_export.csv"; fields=["date_detection","statut","role_cible","intitule","entreprise","lieu","emails_rh","source","lien","id"]
    seen=existing_ids(path); all_jobs=[]
    print("AUSBILDUNG SCRAPER START")
    try: all_jobs+=scrape_ausbildung_de()
    except Exception as e: print("[!] Ausbildung.de:",e)
    try: all_jobs+=scrape_duckduckgo()
    except Exception as e: print("[!] DuckDuckGo:",e)
    unique=[]; ids=set(seen)
    for j in all_jobs:
        if j["id"] not in ids and "@" in j.get("emails_rh",""): ids.add(j["id"]); unique.append(j)
    rows={}
    if os.path.exists(path):
        try:
            with open(path,encoding="utf-8-sig") as f:
                rows={r["id"]:r for r in csv.DictReader(f) if r.get("id")}
        except Exception: pass
    for j in unique: rows[j["id"]]=j
    with open(path,"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows.values())
    print(f"[OK] {len(unique)} new offers; {len(rows)} total")
    if unique: send_to_sheet(unique)

if __name__=="__main__": main()
