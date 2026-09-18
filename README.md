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
