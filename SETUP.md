# Cold Email Pipeline – Setup Guide

## Folder Structure

```
cold_email_pipeline/
│
├── main.py                   # Orchestration — run this
├── config.py                 # Constants, holidays, hardcoded values
├── sheets_handler.py         # All Google Sheets read/write
├── email_generator.py        # Pattern generation + verification
├── ai_personalization.py     # Gemini Flash personalization
├── email_sender.py           # Zoho SMTP send + template rendering
├── imap_poller.py            # Zoho IMAP reply/bounce detection
├── utils.py                  # Business days, window logic, dates
│
├── templates/
│   ├── initial.txt
│   ├── followup1.txt
│   ├── followup2.txt
│   └── nudge.txt
│
├── Dockerfile
├── requirements.txt
├── .env                      # Local dev only — never commit this
├── .gitignore
└── SETUP.md                  # This file
```

---

## Step 1 — Google Sheet Setup

### Create the sheet
1. Go to https://sheets.google.com and create a new sheet
2. Name it: `Cold Email Pipeline`
3. You need **three tabs**:

---

### Tab 1: `leads`
Create these columns in Row 1, exactly as written (case-sensitive):

| Column | Notes |
|---|---|
| `first_name` | |
| `last_name` | |
| `company` | |
| `domain` | e.g. `acme.com` — no http, no www |
| `industry` | Free text — use what they call themselves |
| `role_level` | Dropdown — see below |
| `role_context` | Dropdown — see below |
| `title` | Their actual job title |
| `email` | Pipeline fills this |
| `verification_result` | Pipeline fills: `valid`, `catch_all`, `invalid`, `unverifiable` |
| `personalization` | Pipeline fills via Gemini |
| `personalization_nudge` | Pipeline fills when nudge is due |
| `subject_line` | Pipeline fills based on role_level |
| `cta` | Pipeline fills based on role_level |
| `status` | Pipeline manages: `ready_to_send`, `sent`, `bounced`, `replied`, `left_company`, `out_of_office`, `needs_manual_review` |
| `message_id` | Pipeline fills after first send |
| `date_sent` | Pipeline fills |
| `fu1_target` | Pipeline fills (target send date) |
| `fu1_sent` | Pipeline fills |
| `fu2_target` | Pipeline fills |
| `fu2_sent` | Pipeline fills |
| `nudge_target` | Pipeline fills |
| `nudge_sent` | Pipeline fills |
| `reply_status` | Pipeline fills: `replied`, `bounced`, `left_company`, `out_of_office` |
| `notes` | Pipeline writes OOO raw text here; you can add notes too |

**Set up dropdowns:**

For `role_level` column:
1. Select the entire column (except header)
2. Data → Data validation → Dropdown
3. Options: `ceo_founder`, `hr_leader`

For `role_context` column:
1. Select the entire column (except header)
2. Data → Data validation → Dropdown
3. Options: `HR teams`, `HR and people leaders`, `founders and CEOs`, `leadership teams`

---

### Tab 2: `pattern_db`
Two columns only:

| `domain` | `pattern` |
|---|---|
| acme.com | first.last |

Leave empty for now. The pipeline will populate this as it discovers patterns.

---

### Tab 3: `config`
Two columns: `key` and `value`

| key | value |
|---|---|
| MAX_TOTAL | 5 |
| MIN_INITIALS_RESERVED | 2 |

These are the only two values you touch to control send volume.
**Ramp schedule (suggested):**
- Week 1–2: MAX_TOTAL = 5
- Week 3–4: MAX_TOTAL = 10
- Week 5–6: MAX_TOTAL = 20
- Week 7–8: MAX_TOTAL = 30
- Week 9+: MAX_TOTAL = 40–45

Check your Zoho sending logs before each increase.

---

## Step 2 — Google Cloud Credentials (Sheets API)

1. Go to https://console.cloud.google.com
2. Create a new project (or use existing)
3. Enable the **Google Sheets API**:
   - APIs & Services → Enable APIs → search "Google Sheets API" → Enable
4. Create a Service Account:
   - APIs & Services → Credentials → Create Credentials → Service Account
   - Name it: `cold-email-pipeline`
   - Role: Editor
5. Create a JSON key:
   - Click the service account → Keys → Add Key → JSON
   - Download the file → rename it `service_account.json`
   - Place it in your project root (it goes in `.gitignore` — never commit)
6. Share your Google Sheet with the service account email:
   - Open the sheet → Share → paste the service account email (looks like `cold-email-pipeline@your-project.iam.gserviceaccount.com`)
   - Give it Editor access

---

## Step 3 — Zoho SMTP Setup

1. Log into Zoho Mail → Settings → Mail Accounts → your account
2. Confirm SMTP is enabled (it should be by default on paid Zoho plans)
3. SMTP settings:
   - Host: `smtp.zoho.com`
   - Port: `587` (TLS)
   - Username: your full Zoho email address
   - Password: generate an **App Password** (Settings → Security → App Passwords → Generate)
   - Use the app password, not your main login password

---

## Step 4 — Zoho IMAP Setup

1. In Zoho Mail → Settings → Mail Accounts → IMAP Access → Enable
2. IMAP settings:
   - Host: `imap.zoho.com`
   - Port: `993` (SSL)
   - Username: your full Zoho email address
   - Password: same app password as SMTP

---

## Step 5 — Gemini API Key

1. Go to https://aistudio.google.com/app/apikey
2. Create API Key → Copy it
3. The free tier (Gemini 1.5 Flash) gives you 15 RPM and 1,500 requests/day — more than enough
4. Store the key in your `.env` and as a Cloud Run secret (Step 8)

---

## Step 6 — QuickEmailVerification API Key

1. Go to https://quickemailverification.com
2. Sign up for free (no credit card required)
3. Dashboard → API Key → Copy it
4. Free tier: 3,000 verifications/month, credits never expire
5. Store the key in your `.env` and as a Cloud Run secret (Step 8)

---

## Step 7 — Local `.env` File

Create a file named `.env` in your project root. **Never commit this file.**

```
ZOHO_EMAIL=you@yourdomain.com
ZOHO_APP_PASSWORD=your-app-password
GEMINI_API_KEY=your-gemini-key
QEV_API_KEY=your-quickemailverification-key
GOOGLE_SHEET_ID=your-sheet-id
GOOGLE_SERVICE_ACCOUNT_JSON=service_account.json
```

To find your Sheet ID: open your sheet in the browser — the ID is the long string in the URL between `/d/` and `/edit`.

---

## Step 8 — Google Cloud Run Setup

### 8a — Install prerequisites (if not already installed)
```bash
# Google Cloud SDK
https://cloud.google.com/sdk/docs/install

# Docker
https://docs.docker.com/get-docker/

# Authenticate
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
```

### 8b — Store secrets in Secret Manager
```bash
# Enable Secret Manager
gcloud services enable secretmanager.googleapis.com

# Create each secret
echo -n "you@yourdomain.com" | gcloud secrets create ZOHO_EMAIL --data-file=-
echo -n "your-app-password" | gcloud secrets create ZOHO_APP_PASSWORD --data-file=-
echo -n "your-gemini-key" | gcloud secrets create GEMINI_API_KEY --data-file=-
echo -n "your-qev-key" | gcloud secrets create QEV_API_KEY --data-file=-
echo -n "your-sheet-id" | gcloud secrets create GOOGLE_SHEET_ID --data-file=-

# Upload service account JSON
gcloud secrets create GOOGLE_SERVICE_ACCOUNT_JSON --data-file=service_account.json
```

### 8c — Build and push Docker image
```bash
# Enable required APIs
gcloud services enable run.googleapis.com artifactregistry.googleapis.com

# Create Artifact Registry repo
gcloud artifacts repositories create cold-email \
  --repository-format=docker \
  --location=us-central1

# Build and push
gcloud builds submit --tag us-central1-docker.pkg.dev/YOUR_PROJECT_ID/cold-email/pipeline:latest
```

### 8d — Deploy Cloud Run Job
```bash
gcloud run jobs create cold-email-pipeline \
  --image us-central1-docker.pkg.dev/YOUR_PROJECT_ID/cold-email/pipeline:latest \
  --region us-central1 \
  --set-secrets=ZOHO_EMAIL=ZOHO_EMAIL:latest,\
ZOHO_APP_PASSWORD=ZOHO_APP_PASSWORD:latest,\
GEMINI_API_KEY=GEMINI_API_KEY:latest,\
QEV_API_KEY=QEV_API_KEY:latest,\
GOOGLE_SHEET_ID=GOOGLE_SHEET_ID:latest,\
GOOGLE_SERVICE_ACCOUNT_JSON=GOOGLE_SERVICE_ACCOUNT_JSON:latest \
  --max-retries 1 \
  --task-timeout 300
```

### 8e — Create Cloud Scheduler job
```bash
# Enable scheduler
gcloud services enable cloudscheduler.googleapis.com

# Create scheduler — runs Mon-Thu at 9am your local time
# Adjust timezone to yours (list: https://cloud.google.com/scheduler/docs/configuring/cron-job-schedules)
gcloud scheduler jobs create http cold-email-daily \
  --location us-central1 \
  --schedule "0 9 * * 1-4" \
  --uri "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/YOUR_PROJECT_ID/jobs/cold-email-pipeline:run" \
  --message-body "{}" \
  --oauth-service-account-email YOUR_SERVICE_ACCOUNT_EMAIL \
  --time-zone "America/Boise"
```

Note: `1-4` in cron = Monday through Thursday. Friday is `5`, so `1-4` naturally excludes it.

### 8f — Grant scheduler permission to invoke the job
```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:YOUR_SERVICE_ACCOUNT_EMAIL" \
  --role="roles/run.invoker"
```

---

## Step 9 — Deliverability Health Checks

Run these **before your first send** and **before each volume increase**:

### MXToolbox
1. Go to https://mxtoolbox.com/SuperTool.aspx
2. Run these checks on your domain:
   - MX Lookup — confirms mail routing
   - SPF Record Lookup — confirms SPF is published
   - DKIM Lookup — enter your selector (check Zoho settings for your selector name)
   - DMARC Lookup — confirms DMARC policy exists
3. All four should return green. If any are missing, fix them in Namecheap DNS before sending anything.

### Mail-Tester
1. Go to https://www.mail-tester.com
2. Copy the unique test address they give you
3. Send a real email to that address from your Zoho account (manually, not the pipeline)
4. Click "Check your score"
5. Aim for 9/10 or higher. Fix any issues flagged before running the pipeline.

---

## Step 10 — Manual Test Run

Before scheduling, do a manual test:

```bash
# Local test (uses .env)
pip install -r requirements.txt
python main.py --dry-run   # prints what it would do without sending
python main.py --test-email your@email.com  # sends one test email to yourself
```

Then trigger the Cloud Run job manually once to confirm it runs clean:
```bash
gcloud run jobs execute cold-email-pipeline --region us-central1
```

Check Cloud Run logs:
```bash
gcloud logging read "resource.type=cloud_run_job" --limit 50 --format "value(textPayload)"
```

---

## Step 11 — Ongoing Maintenance

**Weekly (before bumping MAX_TOTAL):**
- Check Zoho sending logs for bounce patterns
- Run MXToolbox blacklist check: https://mxtoolbox.com/blacklists.aspx
- Review `reply_status` column in sheet — anything unexpected?
- Check QuickEmailVerification dashboard for credit usage

**When you redeploy after code changes:**
```bash
gcloud builds submit --tag us-central1-docker.pkg.dev/YOUR_PROJECT_ID/cold-email/pipeline:latest
gcloud run jobs update cold-email-pipeline \
  --image us-central1-docker.pkg.dev/YOUR_PROJECT_ID/cold-email/pipeline:latest \
  --region us-central1
```
