# Jobs Scraper — Email-First Remote + Casablanca Pipeline

Goal: find realistic higher-value jobs for Halima Essaouaf, enrich every opportunity to a real public company email, write only email-ready rows to Google Sheets, then let Google Apps Script send a personalized English application from Gmail with the CV.

## Candidate profile used for targeting

The current CV supports:
- 5+ years across B2B/B2C sales, customer success, account management, commercial administration, customer support, sourcing and operations.
- HBX Group / Hotelbeds / Bedsonline: 600+ B2B travel-agency accounts, onboarding, training, retention, upselling and CRM.
- Current commercial/admin role: suppliers, purchasing, logistics, customer support, Shopify/e-commerce, Canva/Meta Ads and Excel reporting.
- Languages: Arabic native, French C1, English C1, German B1 progressing, Spanish A2.
- Tools: Salesforce, Zendesk, Excel, Tableau, Slack, Shopify, Sage, Figma, Notion and Google Workspace.

## Target roles

The scraper is deliberately focused on roles that can leverage this profile without defaulting to high-volume call-center work:

1. Customer Success / Client Success / Onboarding
2. Account Management / Key Account / Strategic Account / Partner Manager
3. Revenue Operations / Sales Operations / Sales Enablement / Partnerships
4. Travel-Tech / Hospitality-Tech / Hotel-Tech
5. E-commerce / Shopify / Digital Operations / Marketplace
6. Business Operations / Commercial Coordination / Project Coordination

Explicitly excluded or penalized:
- cold calling / cold-call heavy roles
- very high-volume calling
- commission-only jobs
- night/overnight/rotating-shift roles
- unpaid roles
- senior director/VP/chief roles that are above the current profile

The fit score also considers remote signals, French+English requirements, SaaS/travel/e-commerce relevance, autonomy/flexibility signals and disclosed salary.

## Search sources

The public-web layer searches/indexes:
- LinkedIn
- Indeed
- Welcome to the Jungle
- Wellfound
- Remotive
- We Work Remotely
- Himalayas
- Jobgether
- Working Nomads
- Remote OK
- TopCSJobs
- Support Driven
- Emploi.ma
- Rekrute
- Bayt
- Novojob
- Optioncarriere
- Glassdoor

The scraper also searches company websites and public Careers / Recruitment / HR / Contact pages.

## Email-first rule

An offer is not written to the application tabs unless a real public email has been found.

The enrichment process:
1. Find the official company website.
2. Crawl Contact / Careers / Recruitment / HR and related pages.
3. Search public web results for company-domain email addresses.
4. Reject placeholders and common non-contact addresses.
5. Prefer careers/recruitment/talent/jobs/hr addresses.
6. Store the evidence/source of the email.
7. Only then write the row to Google Sheets.

No invented emails. No CAPTCHA, authentication or anti-bot bypass.

## Schedule

.github/workflows/remote_jobs.yml runs the email-first pipeline twice per day at 00:15 and 12:15 UTC, with manual dispatch available.

The scraper searches a 72-hour freshness window so an offer is not lost if one run misses it. The Sheet keeps the Posted <=24h field so the freshest opportunities remain filterable.

## Google Sheets + Gmail

Code.gs:
- receives email-ready jobs from GitHub;
- keeps the three tabs: Worldwide Remote, Morocco Remote, Casablanca Onsite;
- sends personalized English applications from the Gmail account authorizing the Apps Script;
- attaches CV_Halima_Essaouaf.pdf from Google Drive;
- records status/date sent;
- can send one follow-up after 5 days;
- keeps a daily safety ceiling of 95 recipients and 25 per execution;
- does not send outside the configured 07:00–20:00 window.

### One-time setup

1. Put CV_Halima_Essaouaf.pdf in the Google Drive account used by Apps Script.
2. Replace the current Apps Script Code.gs with the repository Code.gs.
3. Verify SPREADSHEET_ID.
4. Run setupRemoteSheet() once.
5. Run setupAutomation() once and authorize Gmail/Drive/Sheets.
6. Deploy the script as a Web App (Execute as me, access suitable for your GitHub webhook).
7. Keep the deployed /exec URL in GitHub secret GOOGLE_SHEET_WEBHOOK_URL.

## Important quota note

Apps Script consumer accounts have a daily email-recipient quota. The script deliberately checks MailApp.getRemainingDailyQuota() and keeps its own lower cap instead of trying to force unlimited Gmail sending.

## Core pipeline

LinkedIn / Indeed / remote boards / Morocco / Casablanca
→ fresh job
→ official company
→ public professional email
→ fit/salary/conditions analysis
→ Google Sheet
→ personalized English Gmail + CV
→ optional follow-up
