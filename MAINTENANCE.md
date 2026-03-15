# Cold Email Pipeline – Maintenance Guide

## Overview

Maintenance falls into three categories:
1. **Weekly** — health checks before bumping send volume
2. **Monthly** — deeper deliverability audit
3. **As needed** — redeploying after code changes, rotating secrets, scrubbing git history

---

## Weekly Maintenance

Do this every week before increasing MAX_TOTAL in the config tab.

### 1. Check Cloud Run Logs
Confirm the pipeline ran successfully every scheduled day (Mon–Thu).

1. Go to console.cloud.google.com → Cloud Run → Jobs → cold-email-pipeline
2. Click the **Executions** tab
3. Every execution should show a green checkmark and exit code 0
4. Click any execution → Logs to see the full output
5. Look for any `[ERROR]` or `[WARNING]` lines — investigate anything unexpected

Or via CLI:
```bash
gcloud logging read "resource.type=cloud_run_job" --limit 50 --format "value(textPayload)"
```

### 2. Check Zoho Sending Logs
1. Log into Zoho Mail
2. Settings → Mail Logs or Sent Mail
3. Look for any delivery failures, bounces, or unusual patterns
4. If bounce rate for the week exceeds 3% — do not increase MAX_TOTAL, investigate first

### 3. Check Bounce Rate in Your Sheet
1. Open the `leads` tab
2. Filter `reply_status` column for `bounced`
3. Count bounces vs total emails sent that week
4. Keep bounce rate under 3% — above that pause sending and investigate

### 4. Check MXToolbox Blacklist
Go to https://mxtoolbox.com/blacklists.aspx and enter your domain. You should get all green. If your domain appears on any blacklist:
- Stop sending immediately
- Follow the delisting instructions for that specific blacklist
- Do not resume until delisted and you have identified why you were listed

### 5. Review Sheet for Anomalies
Scan the `reply_status` column for anything unexpected:
- Spike in `left_company` — your lead list may be outdated
- Spike in `out_of_office` — could just be a holiday week, check notes column
- Any `needs_manual_review` rows — investigate and either manually set an email or remove the lead

### 6. Update MAX_TOTAL if Everything Looks Clean
Only increase if:
- Bounce rate under 3%
- No blacklist hits
- No errors in Cloud Run logs
- Zoho logs look clean

Suggested ramp:
| Week | MAX_TOTAL |
|---|---|
| 1–2 | 5 |
| 3–4 | 10 |
| 5–6 | 20 |
| 7–8 | 30 |
| 9+ | 40–45 |

Update MAX_TOTAL directly in the `config` tab of your Google Sheet — no code change or redeploy needed.

---

## Monthly Maintenance

### Full Deliverability Audit

Run these checks once a month to confirm your domain health has not degraded.

#### MXToolbox Full Check
Go to https://mxtoolbox.com/SuperTool.aspx and run all four:

**1. MX Lookup**
- Enter your domain, select MX Lookup
- Should return green — confirms mail routing is correct

**2. SPF Record Lookup**
- Enter your domain, select SPF Record Lookup
- Should return green
- Your SPF record should include `include:zohomail.com`

**3. DKIM Lookup**
- Enter `zoho._domainkey.yourdomain.com` (replace with your actual domain)
- Select TXT Lookup
- Should return a long public key string — this confirms DKIM is published and active
- If this returns nothing, log into https://mailadmin.zoho.com → Domains → your domain → Email Authentication → DKIM and confirm the selector shows status Verified

**4. DMARC Lookup**
- Enter your domain, select DMARC Lookup
- Should return green
- You will see a warning about `DMARC policy not enabled` — this is expected and fine
- Your policy is correctly set to `p=none` (monitor only) while ramping up
- After 2–3 months of clean sending, consider tightening to `p=quarantine`
- Ignore the BIMI warning entirely — not relevant for cold email

#### Mail-Tester Score
Run this monthly to catch any deliverability degradation:
1. Go to https://www.mail-tester.com
2. Copy the unique test address
3. Send a real email to that address from your Zoho account (manually, not the pipeline)
4. Click "Check your score" — you should maintain 9/10 or higher
5. If your score drops, read the detailed breakdown — it will tell you exactly what changed

#### QuickEmailVerification Credit Check
1. Log into https://quickemailverification.com
2. Check your remaining monthly credits
3. Free tier is 3,000/month — you should be well under this
4. If you are approaching the limit, you are adding leads faster than expected — no action needed, credits reset monthly

---

## Redeploying After Code Changes

Any time you modify the Python code, you need to rebuild the Docker image and update the Cloud Run job. The Google Sheet, secrets, and scheduler do not need to be touched.

```bash
# From your project root
gcloud builds submit --tag us-central1-docker.pkg.dev/YOUR_PROJECT_ID/cold-email/pipeline:latest

gcloud run jobs update cold-email-pipeline \
  --image us-central1-docker.pkg.dev/YOUR_PROJECT_ID/cold-email/pipeline:latest \
  --region us-central1
```

After redeploying, trigger a manual execution to confirm the new code runs cleanly:

```bash
gcloud run jobs execute cold-email-pipeline --region us-central1
```

Then check the logs to confirm no errors.

**You do NOT need to redeploy when:**
- Updating MAX_TOTAL or MIN_INITIALS_RESERVED in the sheet config tab
- Adding leads to the sheet
- Updating email templates (templates are baked into the Docker image — see note below)

**Note on templates:** The `.txt` template files are copied into the Docker image at build time. If you edit `initial.txt`, `followup1.txt`, `followup2.txt`, or `nudge.txt`, you need to rebuild and redeploy for the changes to take effect in Cloud Run. Your local `python3 main.py` will pick up template changes immediately without a redeploy.

---

## Rotating Secrets

If a secret is accidentally exposed (committed to git, shared, etc.), rotate it immediately.

### Rotate a secret in GCP
```bash
echo -n "new-secret-value" | gcloud secrets versions add SECRET_NAME --data-file=-
```

Then update your local `.env` file with the new value as well.

### Secrets that need rotation if exposed
| Secret | Where to regenerate |
|---|---|
| `ZOHO_APP_PASSWORD` | accounts.zoho.com → Security → App Passwords → delete old, generate new |
| `GEMINI_API_KEY` | aistudio.google.com/app/apikey → delete old, create new |
| `QEV_API_KEY` | quickemailverification.com → Dashboard → regenerate |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | console.cloud.google.com → IAM → Service Accounts → your account → Keys → delete old, Add Key |

After rotating, redeploy so Cloud Run picks up the new secret version:
```bash
gcloud run jobs update cold-email-pipeline \
  --image us-central1-docker.pkg.dev/YOUR_PROJECT_ID/cold-email/pipeline:latest \
  --region us-central1
```

---

## Scrubbing Accidentally Committed Secrets from Git History

If you accidentally committed a file containing secrets:

```bash
# Install git-filter-repo if not already installed
pip3 install git-filter-repo

# Remove the file from all history (replace with your actual filename)
git filter-repo --path thefilename.ext --invert-paths --force

# Re-add origin (filter-repo removes it as a safety measure)
git remote add origin git@github.com:yourusername/your-repo.git

# Force push the clean history
git push origin --force --all
```

**Always rotate every secret that was in the exposed file** — even if you scrub the history, treat the secrets as compromised.

Confirm the file is gone:
```bash
git log --all --full-history -- "thefilename.ext"
```

Should return nothing.

---

## DKIM Quick Reference

| Item | Value |
|---|---|
| Selector | `zoho` |
| Full DNS lookup string | `zoho._domainkey.yourdomain.com` |
| Where to verify in Zoho | mailadmin.zoho.com → Domains → your domain → Email Authentication → DKIM |
| Expected MXToolbox result | Long TXT record containing `v=DKIM1; k=rsa; p=...` |
| Status should show | Verified |

If DKIM ever shows as unverified or the TXT record disappears from DNS, log into mailadmin.zoho.com and re-verify. You may need to re-add the DNS record in Namecheap if it was accidentally deleted.
