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
├── .env.example              # Safe to commit — shows structure only
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

Leave empty — the pipeline populates this as it discovers patterns.

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
2. Create a new project or use an existing one
3. Enable the **Google Sheets API**:
   - APIs & Services → Enable APIs & Services → search "Google Sheets API" → Enable
4. Create a Service Account:
   - APIs & Services → Credentials → Create Credentials → Service Account
   - Name it: `cold-email-pipeline`
   - Role: Editor
5. Create a JSON key:
   - Click the service account → Keys → Add Key → JSON
   - Download the file → rename it `service_account.json`
   - Place it in your project root (it is in `.gitignore` — never commit it)
6. Share your Google Sheet with the service account email:
   - Open the sheet → Share → paste the service account email (looks like `cold-email-pipeline@your-project.iam.gserviceaccount.com`)
   - Give it Editor access

---

## Step 3 — Zoho SMTP Setup

**Important — paid plan hosts:** If you are on a Zoho paid/professional plan, your SMTP and IMAP hosts are different from personal Zoho accounts. Go to Zoho Mail → Settings → Mail Accounts → IMAP Access to see the correct hosts displayed in the configuration table. Paid plans typically use `smtppro.zoho.com` and `imappro.zoho.com`. Update these in `config.py`.

1. Log into Zoho Mail → Settings → Mail Accounts
2. SMTP is enabled by default — the "save copy of sent emails" radio button being selected confirms this. No other configuration is needed in this section.
3. Generate an **App Password**:
   - Go to **https://accounts.zoho.com** (not your mail interface — this is your Zoho account portal)
   - Click your profile avatar top right → My Account
   - Left sidebar → Security → App Passwords
   - Click Generate New Password
   - Name it `cold-email-pipeline`
   - **Copy it immediately** — you cannot view it again after closing the dialog
   - Note: App Passwords require two-factor authentication. Enable 2FA in the same Security section first if you have not already.

---

## Step 4 — Zoho IMAP Setup

IMAP is enabled by default on paid Zoho plans. Confirm by going to Zoho Mail → Settings → Mail Accounts → IMAP Access — the radio button should be selected.

The IMAP configuration table shown in Zoho (host, port) is for your reference only — those values go in your code, not in Zoho. Your username is your full Zoho email address and your password is the app password generated in Step 3.

Update `config.py` with the correct hosts shown in that table.

---

## Step 5 — Gemini API Key

1. Go to https://aistudio.google.com/app/apikey
2. Create API Key → Copy it
3. The model is set in `config.py` as `gemini-2.0-flash` — do not change this
4. The free tier gives you 15 RPM and 1,500 requests/day — more than enough
5. Store the key in your `.env` and as a Cloud Run secret (Step 8)

**Important:** Generate your key from https://aistudio.google.com, not from the Google Cloud Console — they are different and have different quota structures. AI Studio keys have a generous free tier with no billing required.

Never commit your API key to git. If you accidentally expose it, Google will automatically revoke it. Generate a new key immediately and rotate it in both `.env` and GCP Secret Manager.

---

## Step 6 — QuickEmailVerification API Key

1. Go to https://quickemailverification.com
2. Sign up for free (no credit card required)
3. Dashboard → API Key → Copy it
4. Free tier: 3,000 verifications/month, credits never expire
5. Store the key in your `.env` and as a Cloud Run secret (Step 8)

---

## Step 7 — Local `.env` File

Create a file named `.env` in your project root. **Never commit this file — it is in `.gitignore`.**

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

### 8a — Verify prerequisites

Check if you already have the required tools:

```bash
gcloud --version
docker --version
```

If both return version numbers you are good — do not reinstall. If either returns `command not found`:
- Google Cloud SDK: https://cloud.google.com/sdk/docs/install
- Docker: https://docs.docker.com/get-docker/

Confirm you are authenticated and on the right project:

```bash
gcloud auth list
gcloud projects list
gcloud config set project YOUR_PROJECT_ID
```

### 8b — Store secrets in Secret Manager

Run these from your **project root** (where `service_account.json` lives):

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

Run from your **project root** (where your `Dockerfile` lives):

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

If prompted to enable the Cloud Build API, say yes. If you get a `PERMISSION_DENIED` error, go to console.cloud.google.com → APIs & Services → Enable APIs and manually enable **Cloud Build API**, then retry.

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

If the job already exists, use `update` instead of `create` (same flags, same values).

If you get a `Permission denied on secret` error, grant the compute service account access:

```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

Your project number appears in the error message itself. Then rerun the create/update command.

### 8e — Create Cloud Scheduler job

```bash
# Enable scheduler
gcloud services enable cloudscheduler.googleapis.com

# Runs Mon-Thu at 9am Boise time
gcloud scheduler jobs create http cold-email-daily \
  --location us-central1 \
  --schedule "0 9 * * 1-4" \
  --uri "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/YOUR_PROJECT_ID/jobs/cold-email-pipeline:run" \
  --message-body "{}" \
  --oauth-service-account-email YOUR_SERVICE_ACCOUNT_EMAIL \
  --time-zone "America/Boise"
```

`1-4` in cron = Monday through Thursday. Emails go out at 9am Mountain — 11am Eastern, 8am Pacific.

### 8f — Grant scheduler permission to invoke the job

```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:YOUR_SERVICE_ACCOUNT_EMAIL" \
  --role="roles/run.invoker"
```

---

## Step 9 — Deliverability Health Checks

### Verify DKIM is configured in Zoho first
1. Go to **https://mailadmin.zoho.com**
2. Left menu → Domains → your domain → Email Authentication → DKIM
3. You should see a selector (typically `zoho`) with status **Verified**
4. If not configured: click Add Selector, copy the TXT record Zoho generates, add it in Namecheap Advanced DNS as a TXT record with host `zoho._domainkey`, then click Verify in Zoho

### MXToolbox
Go to https://mxtoolbox.com/SuperTool.aspx and run:

1. **MX Lookup** — enter your domain → should be all green
2. **SPF Record Lookup** — enter your domain → should be all green
3. **TXT Lookup** — enter `zoho._domainkey.yourdomain.com` → should return a long public key value
4. **DMARC Lookup** — enter your domain → should be all green

You will see a warning about `DMARC policy not enabled` — this is expected. Your DMARC is correctly set to `p=none` (monitor only) which is the right setting while ramping up. Do not change it to `quarantine` or `reject` until you have several weeks of clean sending confirmed. Ignore the BIMI warning entirely.

### Mail-Tester
1. Go to https://www.mail-tester.com
2. Copy the unique test address
3. Send a real email to that address from your Zoho account (manually, not the pipeline)
4. Click "Check your score" — aim for 9/10 or higher

---

## Step 10 — Testing

### Install dependencies
```bash
pip3 install -r requirements.txt
```

### Local dry run
```bash
python3 main.py --dry-run --force
```

`--force` bypasses the day-of-week check so you can test any day. `--dry-run` prints what the pipeline would do without sending or writing to the sheet. Add at least one lead to the sheet first so you can see it process a real row.

### Live end-to-end test
Add yourself as a lead in the sheet with `status=ready_to_send`, then run:

```bash
python3 main.py --force
```

Check your inbox and verify the sheet updated — `status` should be `sent`, `message_id` populated, `date_sent` filled in, `fu1_target` set, and `personalization` written.

### Trigger Cloud Run job manually
```bash
gcloud run jobs execute cold-email-pipeline --region us-central1
```

### Check logs

**Via CLI:**
```bash
gcloud logging read "resource.type=cloud_run_job" --limit 50 --format "value(textPayload)"
```

**Via UI (easier for ongoing monitoring):**
1. console.cloud.google.com → Cloud Run → Jobs → cold-email-pipeline
2. Executions tab → click any execution → Logs

---

## Step 11 — Redeploying After Code Changes

```bash
gcloud builds submit --tag us-central1-docker.pkg.dev/YOUR_PROJECT_ID/cold-email/pipeline:latest

gcloud run jobs update cold-email-pipeline \
  --image us-central1-docker.pkg.dev/YOUR_PROJECT_ID/cold-email/pipeline:latest \
  --region us-central1
```

---

## Step 12 — Ongoing Maintenance

**Weekly before bumping MAX_TOTAL:**
- Check Cloud Run logs for errors
- Check Zoho sending logs for bounce patterns
- Run MXToolbox blacklist check: https://mxtoolbox.com/blacklists.aspx
- Review `reply_status` column in sheet for anything unexpected
- Check QuickEmailVerification dashboard for credit usage

**When rotating secrets after accidental exposure:**
```bash
echo -n "new-value" | gcloud secrets versions add SECRET_NAME --data-file=-
```

Update your local `.env` as well.

**If you accidentally commit secrets to git:**
```bash
pip3 install git-filter-repo
git filter-repo --path thefilename.ext --invert-paths --force
git remote add origin git@github.com:yourusername/your-repo.git
git push origin --force --all
```

Then rotate every secret that was exposed regardless of whether you think anyone saw them.