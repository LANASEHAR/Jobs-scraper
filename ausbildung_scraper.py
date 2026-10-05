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
    (100, "Hotelfachfrau", [
        "hotelfachfrau","hotelfachmann","hotelfachleute","hotelfachkraft",
        "ausbildung hotelfachfrau","ausbildung hotelfachmann","ausbildung im hotelfach"
    ]),
    (98, "Kaufmann/-frau für Hotelmanagement", [
        "kaufmann hotelmanagement","kauffrau hotelmanagement","hotelmanagement ausbildung",
        "hotel kaufmann ausbildung","hotel kaufrau ausbildung"
    ]),
    (95, "Fachfrau für Systemgastronomie", [
        "fachfrau für systemgastronomie","fachmann für systemgastronomie",
        "systemgastronomie ausbildung","fachkraft systemgastronomie"
    ]),
    (92, "Fachfrau für Restaurants und Veranstaltungsgastronomie", [
        "restaurants und veranstaltungsgastronomie","restaurantfachfrau",
        "restaurantfachmann","veranstaltungsgastronomie ausbildung",
        "restaurantfachkraft ausbildung"
    ]),
    (90, "Fachkraft für Gastronomie", [
        "fachkraft gastronomie","fachkraft für gastronomie","gastronomie ausbildung",
        "fachkraft gastgewerbe ausbildung"
    ]),
    (87, "Kaufmann/-frau für Tourismus und Freizeit", [
        "kaufmann tourismus und freizeit","kauffrau tourismus und freizeit",
        "tourismuskaufmann ausbildung","tourismuskauffrau ausbildung",
        "tourismus ausbildung"
    ]),
    (85, "Kaufmann/-frau für Spedition und Logistikdienstleistung", [
        "spedition und logistikdienstleistung","speditionskaufmann ausbildung",
        "speditionskauffrau ausbildung","kaufmann spedition logistik",
        "kauffrau spedition logistik"
    ]),
    (82, "Kaufmann/-frau im Groß- und Außenhandelsmanagement", [
        "groß und außenhandelsmanagement","groß- und außenhandelsmanagement",
        "großhandel ausbildung","außenhandel ausbildung",
        "kaufmann großhandel","kauffrau großhandel"
    ]),
    (78, "Kaufmann/-frau im E-Commerce", [
        "kaufmann e-commerce","kauffrau e-commerce","e-commerce ausbildung",
        "kaufmann im e-commerce","kauffrau im e-commerce"
    ]),
    (76, "Kaufmann/-frau im Einzelhandel", [
        "kaufmann einzelhandel ausbildung","kauffrau einzelhandel ausbildung",
        "einzelhandel ausbildung","kaufmann im einzelhandel","kauffrau im einzelhandel"
    ]),
    (74, "Verkäufer/in", [
        "verkäufer ausbildung","verkäuferin ausbildung","ausbildung verkäufer",
        "ausbildung verkäuferin"
    ]),
    (68, "Kaufmann/-frau für Büromanagement", [
        "büromanagement ausbildung","kaufmann büromanagement","kauffrau büromanagement",
        "kaufmann für büromanagement","kauffrau für büromanagement"
    ]),
    (58, "Industriekaufmann/-frau", [
        "industriekaufmann ausbildung","industriekauffrau ausbildung",
        "industriekaufmann","industriekauffrau"
    ]),
]

PORTALS = [
    "arbeitsagentur.de","ausbildung.de","azubiyo.de","ihk-lehrstellenboerse.de",
    "meine-ausbildung-in-niedersachsen.de","ausbildung.nrw","meine-ausbildung.de",
    "ihk-ausbildungsatlas.de","ausbildungsatlas.ihk.de","ausbildungsatlas.unikam.de",
    "yourfirm.de","hotelcareer.de","hogapage.de","dehoga.de",
    "systemgastronomie-ausbildung.de","logistikmitarbeiter.de","gastgebervonmorgen.de"
]

# Portal-native crawling configuration.
# Each portal is treated as an independent source. Native crawling is attempted
# before search-engine discovery; DDG remains only a fallback for inaccessible pages.
PORTAL_CONFIG = {
    "ihk-lehrstellenboerse.de": {"start":"https://www.ihk-lehrstellenboerse.de/"},
    "meine-ausbildung-in-niedersachsen.de": {"start":"https://meine-ausbildung-in-niedersachsen.de/"},
    "ausbildung.nrw": {"start":"https://www.ausbildung.nrw/"},
    "meine-ausbildung.de": {"start":"https://www.meine-ausbildung.de/"},
    "ihk-ausbildungsatlas.de": {"start":"https://www.ihk-ausbildungsatlas.de/"},
    "ausbildungsatlas.ihk.de": {"start":"https://ausbildungsatlas.ihk.de/"},
    "ausbildungsatlas.unikam.de": {"start":"https://ausbildungsatlas.unikam.de/"},
    "yourfirm.de": {"start":"https://www.yourfirm.de/"},
    "hotelcareer.de": {"start":"https://www.hotelcareer.de/"},
    "hogapage.de": {"start":"https://www.hogapage.de/"},
    "dehoga.de": {"start":"https://www.dehoga.de/"},
    "systemgastronomie-ausbildung.de": {"start":"https://www.systemgastronomie-ausbildung.de/"},
    "logistikmitarbeiter.de": {"start":"https://www.logistikmitarbeiter.de/"},
    "gastgebervonmorgen.de": {"start":"https://www.gastgebervonmorgen.de/"},
    "azubiyo.de": {"start":"https://www.azubiyo.de/"},
    "ausbildung.de": {"start":"https://www.ausbildung.de/"},
}

CRAWL_MAX_SITEMAPS = 24
CRAWL_MAX_URLS_PER_PORTAL = 650
CRAWL_MAX_DETAIL_PAGES_PER_PORTAL = 260

REGION_HINTS = [
    "Baden-Württemberg","Nordrhein-Westfalen","Niedersachsen","Bayern",
    "Sachsen","Thüringen","Sachsen-Anhalt","Mecklenburg-Vorpommern"
]

# Explicit city targets. Smaller/medium cities are deliberately included so the
# scraper does not collapse onto Munich, Berlin, Hamburg and other large cities.
TARGET_CITIES = [
    # Baden-Württemberg
    "Stuttgart","Mannheim","Karlsruhe","Freiburg","Heidelberg","Ulm","Heilbronn",
    "Pforzheim","Reutlingen","Tübingen","Konstanz","Offenburg","Ravensburg",
    "Aalen","Esslingen","Ludwigsburg","Göppingen","Villingen-Schwenningen",
    "Baden-Baden","Baiersbronn","Friedrichshafen",
    # Bavaria
    "Nürnberg","Augsburg","Regensburg","Würzburg","Ingolstadt","Bamberg",
    "Bayreuth","Erlangen","Landshut","Passau","Rosenheim","Kempten",
    "Aschaffenburg","Coburg","Hof","Ansbach","Deggendorf",
    # NRW
    "Dortmund","Essen","Duisburg","Münster","Bielefeld","Aachen","Bonn",
    "Paderborn","Siegen","Bochum","Wuppertal","Mönchengladbach","Krefeld",
    "Gelsenkirchen","Hagen",
    # Lower Saxony
    "Hannover","Braunschweig","Göttingen","Osnabrück","Oldenburg","Wolfsburg",
    "Hildesheim","Salzgitter","Lüneburg","Celle","Goslar",
    # Saxony
    "Dresden","Leipzig","Chemnitz","Zwickau","Plauen","Görlitz","Bautzen",
    "Freiberg","Meißen",
    # Thuringia
    "Erfurt","Jena","Weimar","Gera","Eisenach","Gotha","Suhl","Nordhausen",
    # Saxony-Anhalt
    "Magdeburg","Halle","Dessau-Roßlau","Wernigerode","Quedlinburg","Stendal",
    # Mecklenburg-Vorpommern
    "Rostock","Schwerin","Neubrandenburg","Stralsund","Greifswald","Wismar",
    "Güstrow"
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
        for term in terms[:4]:
            q.append((f'"{term}" Ausbildung Bewerbung Kontakt E-Mail Deutschland 2026',role))
            q.append((f'"{term}" Bewerber aus dem Ausland Deutschland',role))
            for city in TARGET_CITIES[:18]:
                q.append((f'"{term}" Ausbildung "{city}"',role))
        if any(x in role.lower() for x in ["hotel","gastronomie","restaurants"]):
            q.append((f'"{role}" Marokko Ausbildung Bewerbung',role))
    for portal in PORTALS:
        if portal=="arbeitsagentur.de": continue
        for _,role,terms in PRIORITIES:
            for term in terms[:2]:
                q.append((f'site:{portal} "{term}" Ausbildung',role))
    return q


# ---------------------------------------------------------------------------
# PORTAL-NATIVE CRAWLER
# ---------------------------------------------------------------------------
# Every portal gets its own native discovery strategy before web-search
# fallback. The crawler:
#   1) reads robots.txt + sitemap indexes;
#   2) discovers native search/listing/category pages;
#   3) submits simple GET search forms when the portal exposes them;
#   4) follows pagination and same-domain job/detail links;
#   5) extracts JSON-LD JobPosting data first, then visible HTML;
#   6) extracts the REAL company, location, contact/email and full description;
#   7) keeps only relevant Ausbildung offers and never invents email addresses.
#
# This is intentionally much closer to the Arbeitsagentur approach than
# site:portal search queries: the portal itself is the primary data source.

PORTAL_RULES = {
    "ihk-lehrstellenboerse.de": {
        "seeds": ["/", "/ausbildung/"],
        "path_terms": ["ausbildung","beruf","dokumente","stellen","lehr","suche"],
        "detail_terms": ["/ausbildung/"],
    },
    "meine-ausbildung-in-niedersachsen.de": {
        "seeds": ["/"],
        "path_terms": ["ausbildung","stellen","unternehmen","beruf","suche"],
        "detail_terms": ["stellen","ausbildung"],
    },
    "ausbildung.nrw": {
        "seeds": ["/", "/app/"],
        "path_terms": ["ausbildung","unternehmen","beruf","stellen","suche","app"],
        "detail_terms": ["ausbildung","unternehmen"],
    },
    "meine-ausbildung.de": {
        "seeds": ["/"],
        "path_terms": ["ausbildung","stellen","beruf","suche","unternehmen"],
        "detail_terms": ["ausbildung","stellen"],
    },
    "ihk-ausbildungsatlas.de": {
        "seeds": ["/"],
        "path_terms": ["ausbildung","suche","unternehmen","beruf","atlas"],
        "detail_terms": ["ausbildung","unternehmen"],
    },
    "ausbildungsatlas.ihk.de": {
        "seeds": ["/"],
        "path_terms": ["ausbildung","suche","unternehmen","beruf","atlas"],
        "detail_terms": ["ausbildung","unternehmen"],
    },
    "ausbildungsatlas.unikam.de": {
        "seeds": ["/", "/ihk-augsburg/suche", "/ihk-wuppertal/suche"],
        "path_terms": ["ausbildung","suche","unternehmen","beruf","atlas","ihk-"],
        "detail_terms": ["ausbildung","unternehmen"],
    },
    "yourfirm.de": {
        "seeds": ["/stellenangebote/ausbildung/"],
        "path_terms": ["stellenangebote","ausbildung","firma","jobs","stellen"],
        "detail_terms": ["/stellenangebote/"],
    },
    "hotelcareer.de": {
        "seeds": ["/jobs/ausbildung-sonstige-ausbildungsplätze"],
        "path_terms": ["jobs","ausbildung","hotel","stellen"],
        "detail_terms": ["/jobs/"],
    },
    "hogapage.de": {
        "seeds": ["/jobs/auszubildenderlehrling"],
        "path_terms": ["jobs","ausbildung","auszubild","lehrling"],
        "detail_terms": ["/jobs/"],
    },
    "dehoga.de": {
        "seeds": ["/"],
        "path_terms": ["ausbildung","karriere","stellen","beruf"],
        "detail_terms": ["ausbildung","stellen"],
    },
    "systemgastronomie-ausbildung.de": {
        "seeds": ["/"],
        "path_terms": ["ausbildung","stellen","beruf","betrieb"],
        "detail_terms": ["ausbildung","stellen"],
    },
    "logistikmitarbeiter.de": {
        "seeds": ["/"],
        "path_terms": ["ausbildung","stellen","jobs","logistik"],
        "detail_terms": ["ausbildung","stellen"],
    },
    "gastgebervonmorgen.de": {
        "seeds": ["/"],
        "path_terms": ["ausbildung","stellen","jobs","hotel","gastro"],
        "detail_terms": ["ausbildung","stellen","jobs"],
    },
    "azubiyo.de": {
        "seeds": ["/ausbildung/"],
        "path_terms": ["ausbildung","stellen","jobs","berufe"],
        "detail_terms": ["/ausbildung/"],
    },
    "ausbildung.de": {
        "seeds": ["/staedte/","/jobs/"],
        "path_terms": ["ausbildung","jobs","staedte","berufe","suche"],
        "detail_terms": ["/ausbildung/","/jobs/","/staedte/"],
    },
}

PORTAL_QUERY_LIMIT = 18
PORTAL_CITY_LIMIT = 24

def portal_url(base, path):
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))

def same_host(a,b):
    return host(a) == host(b)

def abs_url(base, href):
    try:
        return urljoin(base, href.split("#",1)[0])
    except Exception:
        return ""

def likely_portal_path(url, portal):
    p=urlparse(url).path.lower()
    rule=PORTAL_RULES.get(portal,{})
    terms=rule.get("path_terms",[])
    return any(t in p for t in terms) or p in {"","/"}

def normalize_url(url):
    try:
        u=urlparse(url)
        # Strip trackers but keep meaningful query parameters.
        keep=[]
        for k,v in __import__("urllib.parse",fromlist=["parse_qsl"]).parse_qsl(u.query,keep_blank_values=True):
            if k.lower() not in {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","fbclid","gclid"}:
                keep.append((k,v))
        q=__import__("urllib.parse",fromlist=["urlencode"]).urlencode(keep)
        return u._replace(fragment="",query=q).geturl()
    except Exception:
        return url

def robots_and_sitemaps(base):
    out=[]
    robots=get(urljoin(base,"/robots.txt"),10)
    if robots:
        for line in robots.splitlines():
            if line.lower().startswith("sitemap:"):
                u=line.split(":",1)[1].strip()
                if u: out.append(u)
    out += [
        urljoin(base,"/sitemap.xml"),
        urljoin(base,"/sitemap_index.xml"),
        urljoin(base,"/sitemap-index.xml"),
        urljoin(base,"/wp-sitemap.xml"),
    ]
    return list(dict.fromkeys(out))[:CRAWL_MAX_SITEMAPS]

def sitemap_urls(base):
    found=[]
    seen=set()
    queue=robots_and_sitemaps(base)
    while queue and len(seen)<CRAWL_MAX_SITEMAPS:
        sm=queue.pop(0)
        if sm in seen: continue
        seen.add(sm)
        xml=get(sm,15)
        if not xml: continue
        soup=BeautifulSoup(xml,"xml")
        for loc in soup.find_all("loc"):
            u=clean(loc.get_text())
            if not u: continue
            if u.lower().endswith((".xml",".xml.gz")) and u not in seen:
                queue.append(u)
            elif same_host(u,base):
                found.append(u)
            if len(found)>=CRAWL_MAX_URLS_PER_PORTAL:
                break
    return list(dict.fromkeys(found))[:CRAWL_MAX_URLS_PER_PORTAL]

def role_slug(term):
    s=re.sub(r"[^\w\s-]","",term.lower(),flags=re.UNICODE)
    s=s.replace("ä","ae").replace("ö","oe").replace("ü","ue").replace("ß","ss")
    return re.sub(r"[-\s]+","-",s).strip("-")

def portal_native_seed_urls(portal, base):
    rule=PORTAL_RULES.get(portal,{})
    urls=[portal_url(base,p) for p in rule.get("seeds",[])]
    # Native category/search pages observed on major portals.
    if portal=="hotelcareer.de":
        urls += [portal_url(base,"/jobs/hotelfachfrau-ausbildung"),
                 portal_url(base,"/jobs/hotelkauffrau-ausbildung"),
                 portal_url(base,"/jobs/fachkraft-gastgewerbe")]
    elif portal=="hogapage.de":
        urls += [portal_url(base,"/jobs/auszubildenderlehrling")]
    elif portal=="yourfirm.de":
        urls += [portal_url(base,"/stellenangebote/ausbildung/")]
    elif portal=="azubiyo.de":
        urls += [portal_url(base,f"/ausbildung/{i}/") for i in range(1,9)]
    elif portal=="ausbildung.de":
        urls += [portal_url(base,"/staedte/stelle/")]
    return list(dict.fromkeys(urls))

def build_portal_query_urls(portal, base, term):
    q=quote_plus(term)
    slug=role_slug(term)
    urls=[]
    # Generic GET search parameters. They are harmless on portals that ignore
    # them, while portals with conventional search endpoints use them natively.
    for path in PORTAL_RULES.get(portal,{}).get("seeds",["/"]):
        u=portal_url(base,path)
        for key in ("q","query","search","suchbegriff","keyword"):
            urls.append(u+("&" if "?" in u else "?")+key+"="+q)
    if portal=="hotelcareer.de":
        urls += [
            portal_url(base,f"/jobs/{slug}"),
            portal_url(base,f"/jobs/{slug}-ausbildung"),
        ]
        if term == "hotelfachfrau":
            for city in TARGET_CITIES[:PORTAL_CITY_LIMIT]:
                urls.append(portal_url(base,f"/jobs/{slug}-ausbildung-{role_slug(city)}"))
    elif portal=="hogapage.de":
        urls.append(portal_url(base,f"/jobs/{slug}"))
    elif portal=="yourfirm.de":
        urls.append(portal_url(base,"/stellenangebote/ausbildung/"))
        for city in TARGET_CITIES[:PORTAL_CITY_LIMIT]:
            urls.append(portal_url(base,f"/stellenangebote/ausbildung/{role_slug(city)}/"))
    elif portal=="azubiyo.de":
        # Azubiyo exposes large native paginated Ausbildung result sets.
        urls += [portal_url(base,f"/ausbildung/{letter}/") for letter in "abcdefghijklmnopqrstuvwxyz"]
    elif portal=="ausbildung.de":
        urls += [
            portal_url(base,f"/ausbildung/{slug}/"),
            portal_url(base,f"/jobs/{slug}/"),
        ]
    return list(dict.fromkeys(urls))

def portal_form_search_urls(url, html, term):
    """Submit only simple GET forms; never fabricate a hidden API or bypass auth."""
    out=[]
    try:
        soup=BeautifulSoup(html,"html.parser")
        for form in soup.find_all("form"):
            method=(form.get("method") or "get").lower()
            if method!="get": continue
            action=abs_url(url,form.get("action") or url)
            fields={}
            for inp in form.find_all(["input","select"]):
                name=inp.get("name")
                if not name: continue
                typ=(inp.get("type") or "").lower()
                if typ in {"submit","button","hidden"}: continue
                n=name.lower()
                if any(k in n for k in ["search","such","query","keyword","begriff","beruf","job","q"]):
                    fields[name]=term
            if not fields: continue
            from urllib.parse import urlencode
            sep="&" if "?" in action else "?"
            out.append(action+sep+urlencode(fields))
    except Exception:
        pass
    return out[:6]

def jsonld_objects(html):
    out=[]
    try:
        soup=BeautifulSoup(html,"html.parser")
        for s in soup.select('script[type="application/ld+json"]'):
            raw=s.string or s.get_text()
            try:
                obj=__import__("json").loads(raw)
            except Exception:
                continue
            if isinstance(obj,list): out.extend(obj)
            elif isinstance(obj,dict) and isinstance(obj.get("@graph"),list):
                out.extend(obj["@graph"])
            elif isinstance(obj,dict): out.append(obj)
    except Exception:
        pass
    return out

def jsonld_jobposting(html):
    for obj in jsonld_objects(html):
        typ=obj.get("@type")
        types=typ if isinstance(typ,list) else [typ]
        if "JobPosting" in types:
            return obj
    return {}

def first_text(soup, selectors):
    for sel in selectors:
        node=soup.select_one(sel)
        if node:
            t=clean(node.get_text(" ",strip=True))
            if t: return t
    return ""

def extract_company_from_page(soup, html, jd):
    org=jd.get("hiringOrganization") if isinstance(jd,dict) else None
    if isinstance(org,dict):
        n=clean(org.get("name"))
        if n: return n
    selectors=[
        '[itemprop="hiringOrganization"]','[itemprop="hiringorganization"]',
        '[class*="company"]','[class*="employer"]','[class*="arbeitgeber"]',
        '[class*="unternehmen"]','[class*="firma"]',
        'meta[property="og:site_name"]'
    ]
    v=first_text(soup,selectors)
    if v and len(v)<180:
        return v
    txt=clean(soup.get_text(" ",strip=True))
    patterns=[
        r"(?:Arbeitgeber|Unternehmen|Firma|Ausbildungsbetrieb)\s*[:\-]\s*([^|•]{2,120})",
        r"(?:Unternehmen|Firma)\s+([A-ZÄÖÜ][^|•]{2,100})"
    ]
    for pat in patterns:
        m=re.search(pat,txt,re.I)
        if m:
            cand=clean(m.group(1))
            if cand and len(cand)<180: return cand
    return ""

def extract_location_from_page(soup, html, jd):
    loc=jd.get("jobLocation") if isinstance(jd,dict) else None
    if isinstance(loc,list): loc=loc[0] if loc else None
    if isinstance(loc,dict):
        addr=loc.get("address")
        if isinstance(addr,dict):
            parts=[addr.get(k,"") for k in ("postalCode","addressLocality","addressRegion","addressCountry")]
            v=clean(" ".join(x for x in parts if x))
            if v: return v
        v=clean(loc.get("name"))
        if v: return v
    v=first_text(soup,[
        '[itemprop="jobLocation"]','[class*="location"]','[class*="standort"]',
        '[class*="ort"]','[class*="address"]'
    ])
    if v and len(v)<180: return v
    txt=clean(soup.get_text(" ",strip=True))
    for city in TARGET_CITIES:
        if city.lower() in txt.lower():
            return city
    return "Deutschland"

def extract_date_from_page(jd, soup):
    if isinstance(jd,dict):
        for k in ("datePosted","validThrough"):
            if jd.get(k): return clean(jd[k])
    return first_text(soup,['time[datetime]','[itemprop="datePosted"]','[class*="date"]'])

def offer_page_confidence(url, title, description, jd, portal):
    text=(title+" "+description).lower()
    if "JobPosting" in str(jd.get("@type","")):
        return 100
    if not any(x in text for x in ["ausbildung","auszubild","ausbildungsplatz","lehrstelle","lehrling","bewerbung"]):
        return 0
    if not any(t.lower() in text for _,_,terms in PRIORITIES for t in terms):
        return 0
    p=urlparse(url).path.lower()
    if any(x in p for x in ["/jobs/","/stellen","/ausbildung/","/job/","/detail","/angebot","/lehr"]):
        return 70
    return 35

def parse_portal_offer(url, html, portal, fallback_role="Ausbildung"):
    if not html: return None
    soup=BeautifulSoup(html,"html.parser")
    jd=jsonld_jobposting(html)
    title=clean(
        jd.get("title") if jd else ""
    ) or first_text(soup,["h1","meta[property='og:title']","title"])
    if title.lower().startswith("meta"): title=""
    main=first_text(soup,[
        "article","main","[itemprop='description']",".job-description",
        ".stellenbeschreibung",".job-description-content",
        "[class*='description']","[class*='stellenbeschreibung']"
    ])
    description=clean(jd.get("description","")) if jd else ""
    if len(description)<120: description=main
    if len(description)<120:
        description=clean(soup.get_text(" ",strip=True))[:12000]
    conf=offer_page_confidence(url,title,description,jd,portal)
    if conf<=0: return None
    company=extract_company_from_page(soup,html,jd)
    location=extract_location_from_page(soup,html,jd)
    date=extract_date_from_page(jd,soup)
    pscore,role,reasons=score_offer(title,description,portal,location)
    # Strong native evidence gets a small quality bonus, but relevance still
    # controls ranking.
    pscore += 4 if conf>=70 else 1
    if date:
        reasons += "; native date="+date[:30]
    found=emails(html)
    email=sorted(found,key=lambda x:(not any(k in x for k in ["bewerbung","karriere","ausbildung","jobs","hr","recruit","personal"]),len(x)))[0] if found else ""
    return {
        "date_detection":time.strftime("%Y-%m-%d %H:%M"),
        "statut":"NOUVEAU",
        "role_cible":role or fallback_role,
        "intitule":title or fallback_role,
        "entreprise":company or "Unknown",
        "lieu":location or "Deutschland",
        "emails_rh":email,
        "source":portal,
        "lien":url,
        "id":job_id(url,role or fallback_role),
        "fit_score":pscore,
        "fit_reasons":reasons+"; portal-native extraction",
        "description":description[:16000],
        "priority":next((p for p,r,_ in PRIORITIES if r==role),0),
        "email_status":"FOUND" if email else "NO_EMAIL",
        "email_source":"Verified public portal page" if email else "",
        "portal_native":True,
        "date_posted":date,
    }

def extract_candidate_links(page_url, html, portal):
    soup=BeautifulSoup(html,"html.parser")
    out=[]
    for a in soup.select("a[href]"):
        u=normalize_url(abs_url(page_url,a.get("href","")))
        if not u or not same_host(u,page_url): continue
        if u.startswith(("mailto:","javascript:","tel:")): continue
        if likely_portal_path(u,portal):
            out.append(u)
    return list(dict.fromkeys(out))

def search_portal_native(portal):
    cfg=PORTAL_CONFIG.get(portal,{})
    base=cfg.get("start","")
    if not base: return []
    urls=portal_native_seed_urls(portal,base)
    # Sitemap URLs are a major source of completeness on large portals.
    urls += sitemap_urls(base)
    # Query every relevant role, not only the first two synonyms.
    for _,role,terms in PRIORITIES:
        for term in terms[:5]:
            urls += build_portal_query_urls(portal,base,term)
    urls=list(dict.fromkeys(urls))
    # Native role/search URLs are placed first; sitemap URLs remain the deep-
    # discovery safety net rather than consuming the whole crawl budget.
    query_first=[]
    for _,role,terms in PRIORITIES:
        for term in terms[:5]:
            query_first.extend(build_portal_query_urls(portal,base,term))
    sitemap_first=[u for u in urls if u not in query_first]
    urls=list(dict.fromkeys(query_first + sitemap_first))

    queue=list(urls[:CRAWL_MAX_URLS_PER_PORTAL])
    queued=set(queue); visited=set(); detail_pages=0
    out=[]; seen_jobs=set()

    while queue and len(visited)<CRAWL_MAX_URLS_PER_PORTAL:
        u=queue.pop(0)
        if u in visited: continue
        visited.add(u)
        html=get(u,15)
        if not html: continue

        # If a portal exposes a conventional GET search form, use it too.
        if len(visited)<=40:
            for _,_,terms in PRIORITIES:
                for term in terms[:2]:
                    for su in portal_form_search_urls(u,html,term):
                        if su not in queued and len(queued)<CRAWL_MAX_URLS_PER_PORTAL+120:
                            queue.append(su); queued.add(su)

        offer=parse_portal_offer(u,html,portal)
        if offer and offer["id"] not in seen_jobs:
            seen_jobs.add(offer["id"])
            # The IHK Ausbildungsatlas is a training/company information atlas;
            # it explicitly may not contain open vacancies. Do not turn a
            # static company profile into a fake open job.
            if "ausbildungsatlas" in portal and not any(x in (offer["intitule"]+" "+offer["description"]).lower()
                for x in ["bewerben","bewerbung","offene stelle","ausbildungsplatz","freie ausbildungs","stellenangebot","auszubild"]):
                offer["portal_info_only"]=True
            else:
                out.append(offer)
            detail_pages += 1
            if detail_pages>=CRAWL_MAX_DETAIL_PAGES_PER_PORTAL:
                break

        for v in extract_candidate_links(u,html,portal):
            if v in visited or v in queued: continue
            # Do not spend the crawl budget on legal/news/media/assets pages.
            low=v.lower()
            if any(x in low for x in ["/datenschutz","/impressum","/kontakt","/cookie","/privacy","/newsletter","/magazin","/nachrichten"]):
                continue
            queue.append(v); queued.add(v)
            if len(queued)>=CRAWL_MAX_URLS_PER_PORTAL:
                break

    print(f"[PORTAL] {portal}: visited={len(visited)} offers={len(out)} detail_pages={detail_pages}")
    return out

def search_portals_native():
    portals=[p for p in PORTALS if p!="arbeitsagentur.de"]
    all_jobs=[]
    # Independent portal crawls run in parallel so deeper crawling does not
    # turn the twice-daily workflow into a multi-hour serial crawl.
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures={ex.submit(search_portal_native,p):p for p in portals}
        for fut in as_completed(futures):
            portal=futures[fut]
            try:
                all_jobs.extend(fut.result())
            except Exception as exc:
                print(f"[PORTAL ERROR] {portal}: {type(exc).__name__}: {exc}")
    return all_jobs

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
            company=""
            # Search-result text sometimes contains the employer before the snippet.
            result_text=clean(res.get_text(" ",strip=True))
            for pat in [r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß&.\- ]{2,90})\s+(?:\||–|-)?\s*Ausbildung",
                        r"(?:bei|von)\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß&.\- ]{2,90})"]:
                m=re.search(pat,result_text,re.I)
                if m:
                    company=clean(m.group(1)); break
            if not company:
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
        for term in terms[:5]:
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
    jobs=search_ba()+search_portals_native()+search_web()
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
