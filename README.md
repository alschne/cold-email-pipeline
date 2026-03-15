# Cold Email Pipeline

An automated B2B cold outreach pipeline built for consulting lead generation. The pipeline handles email address generation, AI-powered personalization, sending, threaded follow-ups, reply detection, and bounce tracking — with a Google Sheet as the single source of truth.

---

## What This Does

You add leads to a Google Sheet. The pipeline does everything else.

Every weekday morning (Monday–Thursday, 9am Mountain Time), a Cloud Run job fires and works through this sequence:

1. **Polls your inbox** for replies, bounces, and out-of-office messages — updates the sheet before sending anything
2. **Generates email addresses** for new leads using pattern detection and email verification
3. **Generates personalization** for new leads using Gemini AI — a 1–2 sentence hook tailored to the lead's role and industry
4. **Sends initial emails** to leads marked `ready_to_send`, up to the configured daily limit
5. **Sends threaded follow-ups** to leads whose follow-up window is due — these appear as replies in the same email thread
6. **Logs everything** back to the sheet — send dates, message IDs, statuses, reply detection

Your only manual task is adding leads to the sheet and occasionally bumping the daily send limit as your domain warms up.

---

## Architecture

```
Google Sheet (leads, pattern_db, config tabs)
        ↕
    main.py  ← runs daily via Cloud Run Job + Cloud Scheduler
        ↕
┌─────────────────────────────────────────────────────┐
│  imap_poller.py      — Zoho IMAP reply detection    │
│  email_generator.py  — pattern + QEV verification   │
│  ai_personalization  — Gemini 2.0 Flash             │
│  email_sender.py     — Zoho SMTP send               │
│  sheets_handler.py   — all sheet read/write         │
│  utils.py            — business day logic           │
└─────────────────────────────────────────────────────┘
        ↕
   Zoho Mail (SMTP send + IMAP receive)
```

**External services used:**
| Service | Purpose | Cost |
|---|---|---|
| Google Sheets | Lead database and config | Free |
| Google Cloud Run Jobs | Runs the pipeline daily | Free tier |
| Google Cloud Scheduler | Triggers the job Mon–Thu 9am MT | Free tier |
| Google Secret Manager | Stores credentials securely | Free tier |
| Zoho Mail | Sends and receives email via SMTP/IMAP | Existing paid plan |
| Gemini 2.0 Flash | Generates personalization lines | Free tier (AI Studio) |
| QuickEmailVerification | Verifies email addresses before sending | Free tier (3,000/month) |

---

## Repository Structure

```
cold_email_pipeline/
│
├── main.py                   # Orchestration — this is what runs daily
├── config.py                 # All constants, credentials, hardcoded values
├── sheets_handler.py         # Every read/write operation against the sheet
├── email_generator.py        # Builds candidate emails, verifies via QEV API
├── ai_personalization.py     # Gemini prompts for personalization + nudge
├── email_sender.py           # SMTP send, template rendering, thread headers
├── imap_poller.py            # IMAP polling, reply classification
├── utils.py                  # Business day math, window logic, date helpers
│
├── templates/
│   ├── initial.txt           # Initial outreach email
│   ├── followup1.txt         # Follow-up 1 (3–5 business days after initial)
│   ├── followup2.txt         # Follow-up 2 (10–14 business days after initial)
│   └── nudge.txt             # Final nudge (40–50 business days after initial)
│
├── Dockerfile                # Container definition for Cloud Run
├── requirements.txt          # Python dependencies
├── .env                      # Local credentials — never committed
├── .env.example              # Template showing required env vars
├── .gitignore
├── SETUP.md                  # Full setup instructions
├── MAINTENANCE.md            # Ongoing maintenance and health checks
└── README.md                 # This file
```

---

## Google Sheet Structure

The sheet has three tabs:

### `leads` tab
One row per lead. You fill in the left columns manually; the pipeline fills in the rest.

**You fill in:**
- `first_name`, `last_name`, `company`, `domain`, `industry`
- `role_level` — dropdown: `ceo_founder` or `hr_leader`
- `role_context` — dropdown: `HR teams`, `HR and people leaders`, `founders and CEOs`, `leadership teams`
- `title` — their actual job title
- `status` — set to `ready_to_send` when you want the pipeline to pick them up

**Pipeline fills in:**
- `email`, `verification_result` — generated and verified on first run
- `personalization` — Gemini-generated hook, written once and reused for initial, FU1, FU2
- `personalization_nudge` — regenerated fresh when the nudge is due (~10 weeks later)
- `subject_line`, `cta` — derived from `role_level`
- `message_id` — SMTP Message-ID from the initial send, used to thread follow-ups
- `date_sent`, `fu1_target`, `fu1_sent`, `fu2_target`, `fu2_sent`, `nudge_target`, `nudge_sent`
- `reply_status` — `replied`, `bounced`, `left_company`, or `out_of_office`
- `notes` — raw OOO reply text if applicable

### `pattern_db` tab
Two columns: `domain` and `pattern`. The pipeline builds this automatically as it discovers which email pattern a domain uses (e.g. `acme.com → first.last`). On subsequent leads from the same domain, the known pattern is tried first.

### `config` tab
Two rows that control send volume:

| key | value |
|---|---|
| MAX_TOTAL | 5 (start here, ramp weekly) |
| MIN_INITIALS_RESERVED | 2 |

`MAX_TOTAL` is the global daily send ceiling. Update this directly in the sheet — no code change or redeploy needed. `MIN_INITIALS_RESERVED` guarantees at least this many initial email slots even on heavy follow-up days, so the pipeline never goes days without adding new leads to the sequence.

---

## Email Sequence

Each lead receives up to 4 emails, all appearing as one thread in their inbox:

| Email | Timing | Template | Personalization |
|---|---|---|---|
| Initial | When status = `ready_to_send` | `initial.txt` | `{personalization}` — generated once |
| Follow-up 1 | 3–5 business days after initial | `followup1.txt` | `{personalization}` — same as initial |
| Follow-up 2 | 10–14 business days after initial | `followup2.txt` | `{personalization}` — same as initial |
| Nudge | 40–50 business days after initial | `nudge.txt` | `{personalization_nudge}` — regenerated fresh |

Timing windows are randomized within the range (e.g. anywhere from day 3 to day 5 for FU1) to avoid robotic regularity. The target date is computed once and stored in the sheet so it does not re-randomize on every run.

Follow-ups are skipped automatically if `reply_status` is set (replied, bounced, or left company). Follow-ups with the least time remaining in their window are sent first — so a FU1 on its last eligible day is prioritized over a FU1 that still has days to spare.

**Business days** in this pipeline are Monday–Thursday only. Friday, Saturday, and Sunday never count. Major US holidays are also skipped (New Year's Day, Memorial Day, July 4th, Labor Day, Thanksgiving, Black Friday, Christmas Eve, Christmas Day).

---

## Email Templates

Templates live in the `templates/` folder as plain `.txt` files. Edit them freely — just rebuild and redeploy after any changes (see MAINTENANCE.md).

**Placeholders available in all templates:**
- `{first_name}` — lead first name
- `{company}` — lead company name
- `{industry}` — free text from sheet (whatever the lead calls their industry)
- `{role_context}` — from sheet dropdown (e.g. "HR teams", "founders and CEOs")
- `{personalization}` — Gemini-generated hook (nudge.txt uses `{personalization_nudge}`)
- `{cta}` — call to action, derived from `role_level`

**Subject lines** (auto-populated in sheet, used as-is for initial, prefixed with `RE:` for all follow-ups):
- `ceo_founder` → "When compensation starts slowing growth"
- `hr_leader` → "When pay decisions get harder to explain"

---

## Daily Budget Logic

Follow-ups are prioritized over new initial emails because they are time-sensitive — a missed follow-up window goes cold. The daily logic works like this:

1. Follow-ups get first claim on the budget, up to `MAX_TOTAL - MIN_INITIALS_RESERVED`
2. At least `MIN_INITIALS_RESERVED` slots are always reserved for new initials
3. If follow-ups don't use their full allocation, initials can use the leftover slots
4. Nothing sends beyond `MAX_TOTAL` regardless

Early weeks will be almost entirely initials since no follow-ups exist yet. As the pipeline matures, some weeks will be heavier on follow-ups and lighter on initials — this is expected and evens out over time.

---

## Email Address Generation

For each new lead the pipeline:

1. Checks `pattern_db` for a known pattern for the domain — tries that first if found
2. Tries patterns in priority order: `first.last`, `first`, `f.last`, `firstlast`
3. Verifies each candidate via QuickEmailVerification API before trying the next
4. Stops on the first `valid` or `catch_all` result
5. Records the successful pattern in `pattern_db` for future leads from the same domain

**Verification results:**
- `valid` — mailbox confirmed, send normally
- `catch_all` — domain accepts all addresses, send but monitor bounces closely
- `invalid` — mailbox does not exist, try next pattern
- `unverifiable` — server not responding, mark for manual review

If all patterns are exhausted with no valid result, the lead is marked `needs_manual_review` and skipped.

---

## Reply Detection

The pipeline polls your Zoho IMAP inbox daily and classifies incoming messages:

| Classification | Meaning | Effect |
|---|---|---|
| `replied` | Genuine human reply | Stops all follow-ups |
| `bounced` | Delivery failure / MAILER-DAEMON | Stops all follow-ups |
| `left_company` | Auto-reply indicating person left | Stops all follow-ups |
| `out_of_office` | Temporary absence auto-reply | Notes raw reply in sheet, follow-ups continue on original schedule |

Threading works by storing the SMTP `Message-ID` from the initial send in the sheet. Every follow-up sets `In-Reply-To` and `References` headers pointing to that ID, causing all emails to appear as one thread in the recipient's inbox.

---

## Running Locally

```bash
# Install dependencies
pip3 install -r requirements.txt

# Dry run — prints what the pipeline would do, no sends, no sheet writes
python3 main.py --dry-run --force

# Live run — actually sends and writes to sheet
python3 main.py --force

# Normal run (respects day-of-week and holiday rules)
python3 main.py
```

`--force` bypasses the Mon–Thu sending day check. Use it for testing on weekends or outside business hours. Never leave it hardcoded.

---

## Credentials and Secrets

All credentials are stored in `.env` locally and in GCP Secret Manager for Cloud Run. See `.env.example` for the required variables. Never commit `.env` or `service_account.json`.

If you need to rotate a secret see MAINTENANCE.md.

---

## Documentation

| File | Purpose |
|---|---|
| `SETUP.md` | Step-by-step initial setup from scratch |
| `MAINTENANCE.md` | Weekly and monthly health checks, redeployment, secret rotation |
| `README.md` | This file — architecture and code overview |
