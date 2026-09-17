# Remote Jobs Scraper — Worldwide

A separate job-hunting pipeline for Safae/Halima's remote career search.

## Goal

Find worldwide remote roles from LinkedIn that fit the current profile: Customer Success, Account Management, Sales/Business Development, Customer Support, Operations, Administration, E-commerce/Shopify, CRM/Back Office and junior UX/UI.

## Architecture

1. **LinkedIn discovery** — searches remote LinkedIn job pages using multiple target-role queries.
2. **Immediate Sheet write** — discovered jobs are sent to Google Sheets immediately; no deep search blocks discovery.
3. **Deep search** — a separate run reads pending rows, finds the company's official website and checks contact/career pages.
4. **Enrichment** — company site, public email(s), and deep-search status are written back to the same row using the stable job ID.
5. **Deduplication** — stable IDs prevent the same LinkedIn offer from being inserted twice.

## Important behavior

- Remote-first and worldwide; Morocco is the candidate base.
- No LinkedIn login, CAPTCHA bypass, or anti-bot bypass.
- No invented company websites or emails.
- If LinkedIn is temporarily unavailable, the run logs the failure and continues.
- Deep search is intentionally separated so a slow company lookup cannot delay initial job capture.
- The Google Sheet is the source of truth for the application pipeline.

## Google Sheet

The Apps Script creates a `Remote Jobs` tab automatically with:

`Date Detection | Status | Role Cible | Intitulé | Entreprise | Lieu | Remote | Source | Lien | ID | Company Site | Emails RH | Deep Status | Fit Score | Fit Reasons | Salary | Description | Last Updated`

## Setup

### 1. Google Apps Script

Create a Google Apps Script bound to the target Google Sheet, paste `Code.gs`, run `setupRemoteSheet()` once, then deploy it as a Web App:

- Execute as: **Me**
- Who has access: **Anyone**

Copy the `/exec` URL.

### 2. GitHub secret

Repository → Settings → Secrets and variables → Actions → New repository secret:

`GOOGLE_SHEET_WEBHOOK_URL` = your Apps Script `/exec` URL.

Never commit that URL or other secrets to source code.

### 3. Run

GitHub Actions → **Remote Jobs Scraper** → Run workflow.

The workflow also runs hourly. Discovery and deep search are independent jobs, so the expensive enrichment phase does not block initial capture.

## Local commands

```bash
python remote_scraper.py --mode scrape
python remote_scraper.py --mode deep
```

## Expected workflow

**LinkedIn → Sheet immediately → Deep Search → Company Site/Email update**

This keeps the discovery pipeline fast and makes the research phase independently retryable.
