"""
email_generator.py
------------------
Generates candidate email addresses from a lead's name and domain,
then verifies them via QuickEmailVerification.

Pattern priority order (from config.EMAIL_PATTERNS):
  1. first.last   (e.g. jane.doe@acme.com)
  2. first        (e.g. jane@acme.com)
  3. f.last       (e.g. j.doe@acme.com)
  4. firstlast    (e.g. janedoe@acme.com)

If the domain is already in the pattern_db, that pattern is tried first.
Credits are consumed per verification attempt, so we stop as soon as we
find a valid or catch_all result.
"""

import logging
import time
from typing import Optional

import requests

from config import (
    QEV_API_KEY,
    QEV_BASE_URL,
    EMAIL_PATTERNS,
    VERIF_VALID,
    VERIF_CATCH_ALL,
    VERIF_INVALID,
    VERIF_UNVERIFIABLE,
)
from sheets_handler import Lead, get_pattern_db, upsert_pattern_db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Email address construction
# ---------------------------------------------------------------------------

def _clean(name: str) -> str:
    """Lowercases and strips whitespace from a name part."""
    return name.strip().lower()


def build_candidate(first: str, last: str, domain: str, pattern: str) -> str:
    """
    Constructs one candidate email address from a name and pattern.

    Patterns:
      first.last  → jane.doe@acme.com
      first       → jane@acme.com
      f.last      → j.doe@acme.com
      firstlast   → janedoe@acme.com
    """
    f = _clean(first)
    l = _clean(last)
    d = _clean(domain)

    mapping = {
        "first.last": f"{f}.{l}@{d}",
        "first":      f"{f}@{d}",
        "f.last":     f"{f[0]}.{l}@{d}",
        "firstlast":  f"{f}{l}@{d}",
    }

    if pattern not in mapping:
        raise ValueError(f"Unknown pattern: {pattern}")

    return mapping[pattern]


# ---------------------------------------------------------------------------
# QuickEmailVerification API
# ---------------------------------------------------------------------------

def _verify_email(email: str) -> str:
    """
    Calls QuickEmailVerification and returns a normalized result string:
      valid | catch_all | invalid | unverifiable

    Rate limit: QEV free tier supports reasonable request rates.
    We add a small sleep between calls to be safe.
    """
    try:
        response = requests.get(
            QEV_BASE_URL,
            params={"apikey": QEV_API_KEY, "email": email},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        result = data.get("result", "").lower()
        # QEV result values: valid, invalid, unknown, accept_all
        if result == "valid":
            return VERIF_VALID
        elif result in ("accept_all", "accept-all"):
            return VERIF_CATCH_ALL
        elif result == "invalid":
            return VERIF_INVALID
        else:
            # unknown or unexpected value
            return VERIF_UNVERIFIABLE

    except requests.exceptions.Timeout:
        logger.warning(f"QEV timeout for {email}")
        return VERIF_UNVERIFIABLE
    except requests.exceptions.RequestException as e:
        logger.warning(f"QEV request error for {email}: {e}")
        return VERIF_UNVERIFIABLE
    finally:
        time.sleep(0.5)  # gentle rate limiting


# ---------------------------------------------------------------------------
# Main generation + verification flow
# ---------------------------------------------------------------------------

def generate_and_verify_email(lead: Lead) -> tuple[Optional[str], str]:
    """
    Attempts to find a verified email for a lead.

    Returns:
        (email_address, verification_result)
        email_address is None if no valid/catch_all found.

    Flow:
        1. Check pattern_db for the domain — try known pattern first
        2. Try remaining patterns in priority order
        3. Stop on first valid or catch_all result
        4. Update pattern_db with successful pattern
        5. If all patterns exhausted, return (None, 'unverifiable')
    """
    first = lead.get("first_name", "").strip()
    last = lead.get("last_name", "").strip()
    domain = lead.get("domain", "").strip().lower()

    if not first or not last or not domain:
        logger.warning(f"Skipping email generation — missing name/domain for row {lead.get('_row_number')}")
        return None, VERIF_UNVERIFIABLE

    # Build ordered list of patterns to try — known pattern goes first
    pattern_db = get_pattern_db()
    known_pattern = pattern_db.get(domain)

    if known_pattern and known_pattern in EMAIL_PATTERNS:
        patterns_to_try = [known_pattern] + [
            p for p in EMAIL_PATTERNS if p != known_pattern
        ]
    else:
        patterns_to_try = list(EMAIL_PATTERNS)

    for pattern in patterns_to_try:
        candidate = build_candidate(first, last, domain, pattern)
        result = _verify_email(candidate)

        logger.info(f"Verified {candidate} → {result}")

        if result == VERIF_VALID:
            upsert_pattern_db(domain, pattern)
            return candidate, VERIF_VALID

        elif result == VERIF_CATCH_ALL:
            upsert_pattern_db(domain, pattern)
            return candidate, VERIF_CATCH_ALL

        elif result == VERIF_INVALID:
            # Try next pattern
            continue

        elif result == VERIF_UNVERIFIABLE:
            # Server not responding — no point trying other patterns
            # for the same domain this run
            logger.warning(f"Domain {domain} unverifiable — skipping remaining patterns")
            return None, VERIF_UNVERIFIABLE

    # All patterns exhausted with no valid/catch_all result
    logger.warning(f"All patterns exhausted for {domain} — marking needs_manual_review")
    return None, VERIF_INVALID
