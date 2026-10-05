#!/usr/bin/env python3
"""
AUSBILDUNG SCRAPER — priority-first pipeline for Halima Essaouaf.
The ranking is deliberately optimized for a candidate applying from Morocco.
No email is invented. Company emails are only accepted when found publicly.
"""
import hashlib, os, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urljoin, urlparse
import requests
from bs4 import BeautifulSoup

PRIORITIES = [
    (100, "Hotelfachfrau", ["hotelfachfrau", "hotelfachmann", "ausbildung hotelfachfrau"]),
    (95, "Fachfrau für Systemgastronomie", ["fachfrau für systemgastronomie", "fachmann für systemgastronomie", "systemgastronomie ausbildung"]),
    (92, "Fachfrau für Restaurants und Veranstaltungsgastronomie", ["restaurants und veranstaltungsgastronomie", "restaurantfachfrau", "veranstaltungsgastronomie ausbildung"]),
    (90, "Fachkraft für Gastronomie", ["fachkraft gastronomie", "fachkraft für gastronomie"]),
    (85, "Kaufmann/-frau für Spedition und Logistikdienstleistung", ["spedition und logistikdienstleistung", "speditionskaufmann ausbildung"]),
    (82, "Kaufmann/-frau im Groß- und Außenhandelsmanagement", ["groß und außenhandelsmanagement", "groß- und außenhandelsmanagement", "großhandel ausbildung"]),
    (76, "Kaufmann/-frau im Einzelhandel", ["kaufmann einzelhandel ausbildung", "kauffrau einzelhandel ausbildung", "einzelhandel ausbildung"]),
    (74, "Verkäufer/in", ["verkäufer ausbildung", "verkäuferin ausbildung"]),
    (68, "Kaufmann/-frau für Büromanagement", ["büromanagement ausbildung", "kaufmann büromanagement", "kauffrau büromanagement"]),
    (64, "Kaufmann/-frau für Hotelmanagement", ["hotelmanagement ausbildung", "kaufmann hotelmanagement"]),
    (58, "Industriekaufmann/-frau", ["industriekaufmann ausbildung", "industriekauffrau ausbildung"]),
]

PORTALS = [
    "arbeitsagentur.de","ausbildung.de","azubiyo.de","ihk-lehrstellenboerse.de",
    "meine-ausbildung-in-niedersachsen.de","ausbildung.nrw","meine-ausbildung.de",
    "ihk-ausbildungsatlas.de","ausbildungsatlas.ihk.de","ausbildungsatlas.unikam.de",
    "yourfirm.de","hotelcareer.de","hogapage.de","dehoga.de",
    "systemgastronomie-ausbildung.de","logistikmitarbeiter.de","gastgebervonmorgen.de"
]

REGION_HINTS = [
    "Baden-Württemberg","Nordrhein-Westfalen","Niedersachsen","Bayern",
    "Sachsen","Thüringen","Sachsen-Anhalt","Mecklenburg-Vorpommern"
]

FOREIGN_SIGNALS = [
    "aus dem ausland","bewerber aus dem ausland","internationale bewerber",
    "ausländische bewerber","foreign applicants","international applicants",
    "zav","visum","visa","einreise","marokko","morocco","unterkunft",
    "wohnraum","unterstützung bei der einreise"
]

BAD_DOMAINS = {"linkedin.com","facebook.com","instagram.com","youtube.com","google.com","example.com","sentry.io","wixpress.com"}
BAD_LOCAL = {"noreply","no-reply","donotreply","privacy","security","postmaster","mailer-daemon"}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}")

S = requests.Session()
S.headers.update({
    "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36",
    "Accept-Language":"de-DE,de;q=0.9,en;q=0.7"
})

def priority_for(title):
    t=(title or "").lower()
    for score,role,terms in PRIORITIES:
        if any(term in t for term in terms):
            return score,role
    return 0,title or "Ausbildung"

def clean(s):
    return re.sub(r"\s+"," ",str(s or "")).strip()

def host(url):
    try:
        h=urlparse(url).netloc.lower().split(":")[0]
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""

def valid_email(e):
    e=clean(e).lower().strip(" <>.,;:'\"()[]")
    if not EMAIL_RE.fullmatch(e): return False
    local,dom=e.rsplit("@",1)
    return local not in BAD_LOCAL and dom not in BAD_DOMAINS

def emails(html):
    if not html: return set()
    text=re.sub(r"\s*(?:\[|\(|\{)\s*(?:at|ät)\s*(?:\]|\)|\})\s*","@",html,flags=re.I)
    text=re.sub(r"\s*(?:\[|\(|\{)\s*(?:dot|punkt)\s*(?:\]|\)|\})\s*",".",text,flags=re.I)
    found={e.lower() for e in EMAIL_RE.findall(text) if valid_email(e)}
    try:
        soup=BeautifulSoup(html,"html.parser")
        for a in soup.select('a[href^="mailto:"]'):
            e=a.get("href","")[7:].split("?",1)[0]
            if valid_email(e): found.add(e.lower())
    except Exception:
        pass
    return found

def get(url,timeout=12):
    try:
        r=S.get(url,timeout=timeout,allow_redirects=True)
        return r.text if r.status_code==200 else ""
    except requests.RequestException:
        return ""

def official_site(company):
    if not company or company.lower() in {"unknown","unternehmen deutschland"}: return ""
    q=quote_plus(f'"{company}" official website Kontakt Impressum Ausbildung')
    html=get(f"https://html.duckduckgo.com/html/?q={q}&kl=de-de")
    if not html: return ""
    soup=BeautifulSoup(html,"html.parser")
    for a in soup.select("a.result__a"):
        u=a.get("href",""); h=host(u)
        if not h or any(x in h for x in ["linkedin","indeed","stepstone","ausbildung.de","azubiyo","arbeitsagentur"]): continue
        if any(b in h for b in BAD_DOMAINS): continue
        return f"https://{h}/"
    return ""

def company_email(company):
    site=official_site(company)
    if not site: return "", ""
    paths=["","/kontakt","/contact","/impressum","/karriere","/careers","/ausbildung","/jobs","/stellenangebote","/bewerbung","/recruiting"]
    for p in paths:
        es=emails(get(urljoin(site,p)))
        if es:
            ranked=sorted(es,key=lambda x:(not any(k in x for k in ["bewerbung","karriere","ausbildung","jobs","hr","recruit","personal"]),len(x)))
            return ranked[0],site
    return "",site

def score_offer(title,description,source,location):
    priority,role=priority_for(title)
    text=(title+" "+description).lower()
    score=priority; reasons=[f"priority={priority}"]
    if any(x in text for x in FOREIGN_SIGNALS):
        score+=12; reasons.append("foreign-applicant signal")
    if "b2" in text:
        score+=5; reasons.append("B2 signal")
    elif "b1" in text:
        score+=2; reasons.append("B1 signal")
    if any(r.lower() in location.lower() for r in REGION_HINTS):
        score+=3; reasons.append("target region")
    if source in {"Agentur für Arbeit","Make it in Germany"}:
        score+=4; reasons.append("official source")
    if any(x in text for x in ["unterkunft","wohnraum","personalzimmer"]):
        score+=5; reasons.append("housing support")
    return score,role,"; ".join(reasons)

def job_id(url,role):
    return "aus_"+hashlib.sha1((role+"|"+url).encode()).hexdigest()[:14]

def ddg_queries():
    q=[]
    for _,role,terms in PRIORITIES:
        for term in terms[:2]:
            q.append((f'"{term}" Ausbildung Bewerbung Kontakt E-Mail Deutschland 2026',role))
            q.append((f'"{term}" Bewerber aus dem Ausland Deutschland',role))
        if any(x in role for x in ["Hotel","Gastronomie","Restaurants"]):
            q.append((f'"{role}" Marokko Ausbildung Bewerbung',role))
    for portal in PORTALS:
        for term in ["Hotelfachfrau","Systemgastronomie","Spedition Logistik","Groß- und Außenhandel"]:
            q.append((f'site:{portal} "{term}" Ausbildung 2026',term))
    return q

def search_web():
    out=[]; seen=set()
    for query,fallback_role in ddg_queries():
        html=get("https://html.duckduckgo.com/html/?q="+quote_plus(query)+"&kl=de-de",15)
        if not html: continue
        soup=BeautifulSoup(html,"html.parser")
        for res in soup.select(".result")[:8]:
            a=res.select_one("a.result__a")
            if not a: continue
            u=a.get("href",""); h=host(u)
            if not h or any(x in h for x in ["linkedin.com","facebook.com","instagram.com","google.com","duckduckgo.com"]): continue
            title=clean(a.get_text(" ",strip=True))
            snippet=clean((res.select_one(".result__snippet") or res).get_text(" ",strip=True))
            key=u.split("#",1)[0]
            if key in seen: continue
            seen.add(key)
            company=h.split(".")[0].replace("-"," ").title()
            score,role,reasons=score_offer(title,snippet,f"Web search {h}","Deutschland")
            out.append({
                "date_detection":time.strftime("%Y-%m-%d %H:%M"),"statut":"NOUVEAU",
                "role_cible":role,"intitule":title or fallback_role,"entreprise":company,
                "lieu":"Deutschland","emails_rh":"","source":h,"lien":u,
                "id":job_id(u,role),"fit_score":score,"fit_reasons":reasons,
                "description":snippet,"priority":next((p for p,r,_ in PRIORITIES if r==role),0)
            })
    return out

def search_ba():
    out=[]
    api="https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs"
    headers={"User-Agent":"Jobsuche/2.9.2 (compatible)","X-API-Key":"jobboerse-jobsuche","Accept":"application/json","Accept-Language":"de-DE"}
    for _,role,terms in PRIORITIES:
        for term in terms[:2]:
            try:
                data=S.get(api,params={"was":term,"wo":"Deutschland","angebotsart":4,"size":100},headers=headers,timeout=20).json()
            except Exception:
                continue
            for o in data.get("stellenangebote",[]):
                title=clean(o.get("titel")); company=clean(o.get("arbeitgeber") or "Unknown")
                loc=o.get("arbeitsort") or {}; location=clean(f"{loc.get('plz','')} {loc.get('ort','Deutschland')}")
                url=f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{o.get('refnr','')}"
                desc=clean(str(o))
                score,matched,reasons=score_offer(title,desc,"Agentur für Arbeit",location)
                out.append({
                    "date_detection":time.strftime("%Y-%m-%d %H:%M"),"statut":"NOUVEAU",
                    "role_cible":matched,"intitule":title or matched,"entreprise":company,
                    "lieu":location,"emails_rh":"","source":"Agentur für Arbeit","lien":url,
                    "id":job_id(url,matched),"fit_score":score,"fit_reasons":reasons,
                    "description":desc,"priority":next((p for p,r,_ in PRIORITIES if r==matched),0)
                })
    return out

def enrich(jobs):
    groups={}
    for j in jobs:
        groups.setdefault(clean(j.get("entreprise")),[]).append(j)
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures={ex.submit(company_email,c):c for c in groups if c and c.lower()!="unknown"}
        for fut in as_completed(futures):
            c=futures[fut]
            try: email,site=fut.result()
            except Exception: email,site="",""
            for j in groups[c]:
                j["emails_rh"]=email; j["company_site"]=site
                j["email_status"]="FOUND" if email else "NO_EMAIL"
                j["email_source"]="Verified public company website" if email else ""
    return jobs

def main():
    print("=== AUSBILDUNG PRIORITY SCRAPER ===")
    jobs=search_ba()+search_web()
    unique={j["id"]:j for j in jobs}
    jobs=enrich(list(unique.values()))
    jobs.sort(key=lambda j:(-int(j.get("fit_score",0)),-int(j.get("priority",0))))
    webhook=os.getenv("GOOGLE_SHEET_WEBHOOK_URL","").strip()
    if not webhook:
        print(f"[DONE] {len(jobs)} offers; webhook missing"); return
    for i in range(0,len(jobs),25):
        batch=jobs[i:i+25]
        r=S.post(webhook,json={"mode":"jobs","sheet":"Ausbildung","jobs":batch},timeout=120)
        r.raise_for_status(); print("[SHEET]",r.text[:300]); time.sleep(1)
    print(f"[DONE] total={len(jobs)} top10:")
    for j in jobs[:10]:
        print(j["fit_score"],j["role_cible"],j["entreprise"],j["lieu"])

if __name__=="__main__":
    main()
