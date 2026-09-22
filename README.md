# Jobs Scraper — LinkedIn + Indeed + Casablanca + Company Emails

Goal: turn every relevant job into a company contact opportunity.

Pipeline:
LinkedIn / Indeed / direct Casablanca company search
-> fresh job/company record
-> immediate Google Sheet write
-> official company website
-> Careers / Jobs / Recruitment / HR / Contact
-> public professional email
-> update the SAME Sheet row.

Sources:
- LinkedIn: Remote worldwide + Casablanca, target freshness last 24h.
- Indeed: Remote + Casablanca, target freshness last 24h.
- Direct company search: Casablanca career/recruitment pages, including spontaneous opportunities.

Every LinkedIn or Indeed offer triggers company research. The offer is not considered complete until the deep-search stage has attempted to find the company website and public professional email.

Email priority:
careers@, recruitment@, recrutement@, jobs@, hiring@, talent@, hr@, then other public company-domain emails.

No invented emails. No CAPTCHA, authentication or anti-bot bypass.

Google Sheet columns:
Date Detection | Status | Role Cible | Intitulé | Entreprise | Lieu | Remote | Source | Lien | ID | Company Site | Emails RH | Deep Status | Fit Score | Fit Reasons | Salary | Description | Last Updated | Posted Age | Posted <=24h | Search Type | Email Status | Email Source | Spontaneous

Useful filters:
Email Status = FOUND
Search Type = REMOTE
Search Type = CASABLANCA
Search Type = SPONTANEOUS_CASABLANCA
Posted <=24h = YES

Setup:
1. Paste Code.gs into Apps Script.
2. Run setupRemoteSheet() once.
3. Deploy as Web App, execute as Me, access Anyone.
4. Store the /exec URL in GitHub Actions secret GOOGLE_SHEET_WEBHOOK_URL.

The scraper runs hourly. Discovery writes to the Sheet before deep research so slow company lookups do not prevent offers from being captured.

Public job scrapers commonly need source-specific fallbacks because job boards can block automated requests or change their HTML. This implementation therefore continues when a source is unavailable rather than stopping the whole pipeline.


## Morning CV-matched applications

The application sender now runs in GitHub Actions instead of Apps Script.

Schedule (Casablanca time):
- 05:10 and 07:10 weekdays: discover + deep-enrich jobs and public company emails.
- 08:05 and 09:05 weekdays: send up to 30 matched applications per window.
- Jobs are scored against the candidate's CV-derived professional profile. The sender uses a minimum fit score of 65 and skips rows already marked SENT.
- The CV itself is NOT stored in this public repository. It is supplied to the workflow through the `CV_PDF_BASE64` GitHub Actions secret.

Required GitHub Actions secrets:
- `GOOGLE_SHEET_WEBHOOK_URL` — existing Apps Script / Sheet webhook.
- `SMTP_HOST` — normally `smtp.gmail.com`.
- `SMTP_PORT` — normally `587`.
- `SMTP_USERNAME` — sending mailbox.
- `SMTP_PASSWORD` — SMTP/app password; never commit it.
- `SMTP_FROM` — sending address.
- `CV_PDF_BASE64_1` through `CV_PDF_BASE64_4` — base64 chunks of the PDF CV (used because GitHub secrets have a size limit).

For Gmail, use an app password only if your account supports it; Google says app passwords require 2-Step Verification. Do not put a normal Google account password in GitHub. The workflow uses TLS.

Because the repository is public, the CV and email credentials must remain GitHub Actions secrets. GitHub encrypts repository secrets and injects them only into workflows that explicitly request them.

After updating `Code.gs`, redeploy the Apps Script web app so the new `applications` webhook mode is available. The old Apps Script hourly sender is intentionally disabled to prevent duplicate applications.

To create the CV secret without pasting the PDF into the GitHub web UI, use GitHub CLI locally:
`base64 -w 0 "CV.pdf" | fold -w 45000 | split -d -a 1 -b 45000 - /tmp/cv.b64.`, then set each generated chunk as `CV_PDF_BASE64_1` ... `CV_PDF_BASE64_4` with `gh secret set NAME < /tmp/cv.b64.N` (use only the chunks that exist).
On macOS, generate the chunks with `base64 < "CV.pdf" | fold -w 45000 | split -d -a 1 -b 45000 - /tmp/cv.b64.` and upload the chunks as the four secrets.

Never commit the CV, SMTP password, app password, or any other credential to this public repository.
