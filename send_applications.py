"""Send matched job applications from GitHub Actions.

SMTP credentials and the CV are supplied only through GitHub Actions secrets.
The CV is never committed to this public repository.
"""
import base64
import os
import re
import smtplib
import ssl
import time
from email.message import EmailMessage

import requests

WEBHOOK = os.environ["GOOGLE_SHEET_WEBHOOK_URL"].strip()
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ["SMTP_USERNAME"].strip()
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USERNAME).strip()
FROM_NAME = os.getenv("FROM_NAME", "Halima Essaouaf").strip()
MAX_PER_RUN = int(os.getenv("MAX_EMAILS_PER_RUN", "30"))
MIN_FIT_SCORE = int(os.getenv("MIN_FIT_SCORE", "65"))
CV_B64 = "".join(os.getenv(f"CV_PDF_BASE64_{i}", "") for i in range(1, 5)) or os.getenv("CV_PDF_BASE64", "")

def webhook(payload):
    last = ""
    for attempt in range(4):
        try:
            r = requests.post(WEBHOOK, json=payload, timeout=(10, 60))
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "success":
                return data
            last = repr(data)
        except Exception as exc:
            last = str(exc)
        time.sleep(min(8, 2 ** attempt))
    raise RuntimeError(f"Google Sheets webhook failed: {last}")

def first_email(value):
    for item in re.split(r"\s*[/;,|]+\s*", str(value or "")):
        m = re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", item, re.I)
        if m:
            return m.group(0).lower()
    return ""

def priority(email):
    local = email.split("@", 1)[0]
    exact = {
        "careers": 100, "recruitment": 98, "recrutement": 98, "jobs": 96,
        "hiring": 94, "talent": 92, "hr": 90, "humanresources": 88,
        "recrutementrh": 88, "info": 50, "contact": 45, "admin": 40,
    }
    if local in exact:
        return exact[local]
    if any(k in local for k in ("career", "recruit", "recrut", "talent", "hiring", "hr")):
        return 80
    return 30

def build_message(job):
    title = str(job.get("intitule") or job.get("role_cible") or "the position").strip()
    company = str(job.get("entreprise") or "your company").strip()
    link = str(job.get("lien") or "").strip()
    reasons = str(job.get("fit_reasons") or "").strip()
    subject = f"Application – {title} – {company}"
    body = f"""Hello,

I am writing to apply for the {title} position at {company}.

My background combines B2B/B2C customer success, account management, sales,
administrative coordination, sourcing and business operations. I have worked
with international clients and teams in Arabic, French and English, and I am
also progressing in German.

This opportunity caught my attention because my experience is particularly
relevant to: {reasons or "the responsibilities described in the vacancy"}.

Job posting: {link or "available upon request"}

Please find my CV attached. I would be happy to discuss my profile and provide
any additional information.

Best regards,
Halima Essaouaf
Casablanca, Morocco
"""
    return subject, body

def send_one(server, job, cv_bytes):
    email = first_email(job.get("emails_rh"))
    if not email:
        return False, "NO_EMAIL"
    subject, body = build_message(job)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{SMTP_FROM}>"
    msg["To"] = email
    msg["Reply-To"] = SMTP_FROM
    msg.set_content(body)
    msg.add_attachment(cv_bytes, maintype="application", subtype="pdf",
                       filename="CV_Halima_Essaouaf.pdf")
    server.send_message(msg)
    return True, email

def main():
    cv_bytes = base64.b64decode(CV_B64, validate=True)
    jobs = webhook({
        "mode": "applications",
        "limit": 5000,
        "min_fit_score": MIN_FIT_SCORE,
    }).get("jobs", [])

    jobs.sort(key=lambda j: (
        -int(j.get("fit_score") or 0),
        -priority(first_email(j.get("emails_rh"))),
        0 if str(j.get("posted_within_24h", "")).upper() == "YES" else 1,
    ))
    jobs = jobs[:MAX_PER_RUN]

    if not jobs:
        print(f"[APPLICATIONS] no eligible matches at fit >= {MIN_FIT_SCORE}")
        return

    context = ssl.create_default_context()
    sent = errors = 0
    updates = []

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)

        for job in jobs:
            try:
                ok, info = send_one(server, job, cv_bytes)
                if ok:
                    sent += 1
                    updates.append({
                        "id": job["id"], "sheet": job["sheet"],
                        "email_status": "SENT",
                        "email_source": "GitHub Actions SMTP - personalized CV application",
                    })
                    print(f"[APPLICATIONS] sent {sent}/{MAX_PER_RUN}")
            except Exception as exc:
                errors += 1
                updates.append({
                    "id": job["id"], "sheet": job["sheet"],
                    "email_status": "ERROR",
                    "email_source": "GitHub SMTP error: " + str(exc)[:180],
                })

            if len(updates) >= 10:
                webhook({"mode": "enrich", "updates": updates})
                updates = []

    if updates:
        webhook({"mode": "enrich", "updates": updates})

    print(f"[APPLICATIONS] completed sent={sent} errors={errors} selected={len(jobs)}")

if __name__ == "__main__":
    main()
