"""
imap_poller.py
--------------
Polls Zoho IMAP inbox for replies to sent emails.

For each reply found, classifies it as:
  replied        — genuine human reply
  bounced        — MAILER-DAEMON / delivery failure
  left_company   — auto-reply indicating person no longer works there
  out_of_office  — auto-reply indicating temporary absence

Matching is done against the original Message-ID stored in the sheet.
The IMAP search looks in the inbox for messages with In-Reply-To headers
that match stored message IDs, plus the Sent/Spam folders for bounces.

OOO handling: status set to out_of_office, raw reply body copied to notes.
No change to follow-up target dates (manual review if rescheduling needed).
"""

import email
import imaplib
import logging
import re
from typing import Optional

from config import (
    ZOHO_EMAIL,
    ZOHO_APP_PASSWORD,
    ZOHO_IMAP_HOST,
    ZOHO_IMAP_PORT,
    STATUS_BOUNCED,
    STATUS_LEFT_COMPANY,
    STATUS_OUT_OF_OFFICE,
    STATUS_REPLIED,
)
from sheets_handler import Lead, get_all_leads, update_lead_fields

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Classification patterns
# ---------------------------------------------------------------------------

# Bounce indicators — typically from MAILER-DAEMON or postmaster
_BOUNCE_FROM_PATTERNS = [
    r"mailer-daemon",
    r"postmaster",
    r"mail delivery subsystem",
    r"delivery status notification",
]

# Subject line patterns for bounces
_BOUNCE_SUBJECT_PATTERNS = [
    r"delivery status notification",
    r"undeliverable",
    r"delivery failure",
    r"mail delivery failed",
    r"returned mail",
    r"message not delivered",
]

# Body patterns indicating the person left the company
_LEFT_COMPANY_PATTERNS = [
    r"no longer with",
    r"no longer at",
    r"no longer employed",
    r"has left the company",
    r"has left our organization",
    r"is no longer",
    r"left the organization",
    r"departed from",
    r"please contact .* instead",
    r"email address is no longer valid",
]

# Subject/body patterns for out-of-office
_OOO_PATTERNS = [
    r"out of office",
    r"out of the office",
    r"away from the office",
    r"on vacation",
    r"on leave",
    r"on holiday",
    r"automatic reply",
    r"auto.reply",
    r"i am currently away",
    r"i will be out",
    r"i'm out",
]


def _matches_any(text: str, patterns: list[str]) -> bool:
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in patterns)


# ---------------------------------------------------------------------------
# IMAP connection
# ---------------------------------------------------------------------------

def _connect() -> imaplib.IMAP4_SSL:
    conn = imaplib.IMAP4_SSL(ZOHO_IMAP_HOST, ZOHO_IMAP_PORT)
    conn.login(ZOHO_EMAIL, ZOHO_APP_PASSWORD)
    return conn


# ---------------------------------------------------------------------------
# Fetch and classify replies
# ---------------------------------------------------------------------------

def _classify_message(msg: email.message.Message) -> str:
    """
    Classifies an email message into one of four reply statuses.
    Checks in priority order: bounce → left_company → ooo → replied
    """
    from_header = msg.get("From", "")
    subject = msg.get("Subject", "")
    body = _extract_body(msg)

    # Bounce check — from header and subject are most reliable
    if _matches_any(from_header, _BOUNCE_FROM_PATTERNS):
        return STATUS_BOUNCED
    if _matches_any(subject, _BOUNCE_SUBJECT_PATTERNS):
        return STATUS_BOUNCED

    # Left company — check body
    if _matches_any(body, _LEFT_COMPANY_PATTERNS):
        return STATUS_LEFT_COMPANY

    # Out of office — check subject and body
    if _matches_any(subject, _OOO_PATTERNS) or _matches_any(body, _OOO_PATTERNS):
        return STATUS_OUT_OF_OFFICE

    # Default: genuine reply
    return STATUS_REPLIED


def _extract_body(msg: email.message.Message) -> str:
    """Extracts plain text body from a MIME message."""
    body_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body_parts.append(
                        part.get_payload(decode=True).decode("utf-8", errors="replace")
                    )
                except Exception:
                    pass
    else:
        try:
            body_parts.append(
                msg.get_payload(decode=True).decode("utf-8", errors="replace")
            )
        except Exception:
            pass
    return "\n".join(body_parts)[:2000]  # cap at 2000 chars for notes storage


def _get_in_reply_to(msg: email.message.Message) -> Optional[str]:
    """Extracts the In-Reply-To header value."""
    val = msg.get("In-Reply-To", "").strip()
    return val if val else None


# ---------------------------------------------------------------------------
# Main polling function
# ---------------------------------------------------------------------------

def poll_for_replies() -> dict[str, tuple[str, str]]:
    """
    Connects to Zoho IMAP, scans inbox and relevant folders for replies.

    Returns a dict mapping:
      original_message_id → (reply_status, notes_text)

    The caller (main.py) matches these against sheet rows by message_id.
    """
    results: dict[str, tuple[str, str]] = {}

    try:
        conn = _connect()
    except Exception as e:
        logger.error(f"IMAP connection failed: {e}")
        return results

    # Folders to check — Zoho uses "INBOX" and may use "Junk" for bounces
    folders_to_check = ["INBOX", "Junk", "Spam"]

    for folder in folders_to_check:
        try:
            status, _ = conn.select(folder, readonly=True)
            if status != "OK":
                continue

            # Search for all messages — we'll filter by In-Reply-To locally
            # UNSEEN would miss replies that got auto-marked read
            _, message_nums = conn.search(None, "ALL")
            if not message_nums or not message_nums[0]:
                continue

            for num in message_nums[0].split():
                try:
                    _, msg_data = conn.fetch(num, "(RFC822)")
                    if not msg_data or not msg_data[0]:
                        continue

                    raw = msg_data[0][1]
                    if isinstance(raw, bytes):
                        msg = email.message_from_bytes(raw)
                    else:
                        continue

                    in_reply_to = _get_in_reply_to(msg)
                    if not in_reply_to:
                        continue

                    # Normalize message ID format
                    in_reply_to = in_reply_to.strip()
                    if in_reply_to in results:
                        continue  # already processed a reply to this message

                    status_val = _classify_message(msg)
                    body_snippet = _extract_body(msg)
                    notes = body_snippet[:500] if status_val == STATUS_OUT_OF_OFFICE else ""

                    results[in_reply_to] = (status_val, notes)
                    logger.info(f"Reply detected: {in_reply_to} → {status_val}")

                except Exception as e:
                    logger.warning(f"Error processing message {num}: {e}")
                    continue

        except Exception as e:
            logger.warning(f"Error accessing folder {folder}: {e}")
            continue

    conn.logout()
    return results


# ---------------------------------------------------------------------------
# Apply reply results to sheet
# ---------------------------------------------------------------------------

def update_sheet_with_replies(leads: list[Lead]) -> dict[str, int]:
    """
    Polls IMAP and updates the sheet for any leads that have received replies.

    Returns a dict with counts by reply type:
      {replied, bounced, left_company, out_of_office}
    """
    reply_map = poll_for_replies()

    counts = {
        STATUS_REPLIED: 0,
        STATUS_BOUNCED: 0,
        STATUS_LEFT_COMPANY: 0,
        STATUS_OUT_OF_OFFICE: 0,
    }

    if not reply_map:
        return counts

    for lead in leads:
        message_id = lead.get("message_id", "").strip()
        if not message_id:
            continue

        if message_id in reply_map:
            reply_status, notes = reply_map[message_id]

            # Don't overwrite a more definitive status
            current_reply_status = lead.get("reply_status", "").strip()
            if current_reply_status in (STATUS_REPLIED, STATUS_BOUNCED, STATUS_LEFT_COMPANY):
                continue

            fields: dict = {"reply_status": reply_status}

            if reply_status in (STATUS_BOUNCED, STATUS_LEFT_COMPANY, STATUS_REPLIED):
                fields["status"] = reply_status

            if notes:
                existing_notes = lead.get("notes", "").strip()
                fields["notes"] = f"{existing_notes}\n[OOO] {notes}".strip()

            update_lead_fields(lead, fields)
            counts[reply_status] = counts.get(reply_status, 0) + 1
            logger.info(f"Updated row {lead.get('_row_number')} → {reply_status}")

    return counts