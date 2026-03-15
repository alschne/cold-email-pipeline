import os
from datetime import date

# ---------------------------------------------------------------------------
# Credentials — loaded from environment (set via .env locally, Secret Manager
# in Cloud Run)
# ---------------------------------------------------------------------------
ZOHO_EMAIL: str = os.environ["ZOHO_EMAIL"]
ZOHO_APP_PASSWORD: str = os.environ["ZOHO_APP_PASSWORD"]
ZOHO_SMTP_HOST: str = "smtppro.zoho.com"
ZOHO_SMTP_PORT: int = 465
ZOHO_IMAP_HOST: str = "imappro.zoho.com"
ZOHO_IMAP_PORT: int = 993

GEMINI_API_KEY: str = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL: str = "gemini-1.5-flash"

QEV_API_KEY: str = os.environ["QEV_API_KEY"]  # QuickEmailVerification
QEV_BASE_URL: str = "https://api.quickemailverification.com/v1/verify"

GOOGLE_SHEET_ID: str = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_SERVICE_ACCOUNT_JSON: str = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json"
)

# ---------------------------------------------------------------------------
# Sender identity
# ---------------------------------------------------------------------------
SENDER_NAME: str = "Allie"
SENDER_EMAIL: str = ZOHO_EMAIL

# ---------------------------------------------------------------------------
# Case study — hardcoded until you have more projects
# ---------------------------------------------------------------------------
CASE_STUDY_SIZE: str = "50-person"
CASE_STUDY_INDUSTRY: str = "manufacturing"
CASE_STUDY_ROLES: str = "33"

# ---------------------------------------------------------------------------
# Subject lines — keyed by role_level
# ---------------------------------------------------------------------------
SUBJECT_LINES: dict[str, str] = {
    "ceo_founder": "When compensation starts slowing growth",
    "hr_leader": "When pay decisions get harder to explain",
}

# ---------------------------------------------------------------------------
# CTAs — keyed by role_level
# ---------------------------------------------------------------------------
CTAS: dict[str, str] = {
    "hr_leader": (
        "Curious if this is something you're navigating — would you be open to "
        "a brief 15-minute conversation to see if it could be valuable to your team?"
    ),
    "ceo_founder": "Is this something you're currently navigating?",
}

# ---------------------------------------------------------------------------
# Email address generation — patterns tried in priority order
# ---------------------------------------------------------------------------
EMAIL_PATTERNS: list[str] = ["first.last", "first", "f.last", "firstlast"]

# ---------------------------------------------------------------------------
# Follow-up windows — (min_days, max_days) in business days after date_sent
# Business days = Mon–Thu only
# ---------------------------------------------------------------------------
FU1_WINDOW: tuple[int, int] = (3, 5)
FU2_WINDOW: tuple[int, int] = (10, 14)
NUDGE_WINDOW: tuple[int, int] = (40, 50)

# ---------------------------------------------------------------------------
# Holidays — Mon–Thu sends are skipped on these dates
# Update the year entries annually or generate programmatically if preferred
# ---------------------------------------------------------------------------
def _us_holidays() -> set[date]:
    """
    Returns a set of hardcoded B2B-relevant US holidays for the current
    and next calendar year. Extend this list annually.
    """
    holidays = {
        # 2024
        date(2024, 1, 1),   # New Year's Day
        date(2024, 5, 27),  # Memorial Day
        date(2024, 7, 4),   # Independence Day
        date(2024, 9, 2),   # Labor Day
        date(2024, 11, 28), # Thanksgiving
        date(2024, 11, 29), # Black Friday
        date(2024, 12, 24), # Christmas Eve
        date(2024, 12, 25), # Christmas Day
        # 2025
        date(2025, 1, 1),
        date(2025, 5, 26),
        date(2025, 7, 4),
        date(2025, 9, 1),
        date(2025, 11, 27),
        date(2025, 11, 28),
        date(2025, 12, 24),
        date(2025, 12, 25),
        # 2026
        date(2026, 1, 1),
        date(2026, 5, 25),
        date(2026, 7, 3),   # Observed (July 4 falls on Saturday)
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 11, 27),
        date(2026, 12, 24),
        date(2026, 12, 25),
    }
    return holidays


US_HOLIDAYS: set[date] = _us_holidays()

# ---------------------------------------------------------------------------
# Google Sheets — tab names
# ---------------------------------------------------------------------------
LEADS_TAB: str = "leads"
PATTERN_DB_TAB: str = "pattern_db"
CONFIG_TAB: str = "config"

# ---------------------------------------------------------------------------
# Lead statuses
# ---------------------------------------------------------------------------
STATUS_READY: str = "ready_to_send"
STATUS_SENT: str = "sent"
STATUS_BOUNCED: str = "bounced"
STATUS_REPLIED: str = "replied"
STATUS_LEFT_COMPANY: str = "left_company"
STATUS_OUT_OF_OFFICE: str = "out_of_office"
STATUS_NEEDS_REVIEW: str = "needs_manual_review"

# ---------------------------------------------------------------------------
# Verification results
# ---------------------------------------------------------------------------
VERIF_VALID: str = "valid"
VERIF_CATCH_ALL: str = "catch_all"
VERIF_INVALID: str = "invalid"
VERIF_UNVERIFIABLE: str = "unverifiable"
