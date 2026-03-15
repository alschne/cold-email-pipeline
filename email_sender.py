"""
email_sender.py
---------------
Handles all email sending via Zoho SMTP.

Responsibilities:
  - Load and render templates with lead data
  - Inject subject_line, cta, personalization, role_context
  - Send initial emails
  - Send threaded follow-ups (In-Reply-To + References headers)
  - Return the Message-ID of each sent email for thread storage

Threading works by:
  1. Capturing the Message-ID from the initial send
  2. Setting In-Reply-To and References on every follow-up
  3. Using "RE: {subject}" as the follow-up subject
  This causes all emails to appear as one thread in the recipient's inbox.
"""

import logging
import smtplib
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate
from pathlib import Path
from typing import Optional

from config import (
    SENDER_EMAIL,
    SENDER_NAME,
    ZOHO_APP_PASSWORD,
    ZOHO_EMAIL,
    ZOHO_SMTP_HOST,
    ZOHO_SMTP_PORT,
    SUBJECT_LINES,
    CTAS,
)
from sheets_handler import Lead

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Template loading and rendering
# ---------------------------------------------------------------------------

def _load_template(name: str) -> str:
    """Loads a template file from the templates/ directory."""
    path = TEMPLATES_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return path.read_text(encoding="utf-8")


def _render_template(template_str: str, lead: Lead, is_nudge: bool = False) -> str:
    """
    Replaces all {placeholders} in the template with lead data.

    Placeholders:
      {first_name}       — lead first name
      {company}          — lead company
      {industry}         — lead industry (free text from sheet)
      {role_context}     — e.g. "HR teams", "founders and CEOs"
      {personalization}  — Gemini-generated hook
      {cta}              — derived from role_level
    """
    personalization = (
        lead.get("personalization_nudge", "") if is_nudge
        else lead.get("personalization", "")
    )

    replacements = {
        "{first_name}":      lead.get("first_name", "").strip(),
        "{company}":         lead.get("company", "").strip(),
        "{industry}":        lead.get("industry", "").strip(),
        "{role_context}":    lead.get("role_context", "").strip(),
        "{personalization}": personalization.strip(),
        "{cta}":             lead.get("cta", "").strip(),
    }

    result = template_str
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)

    return result


# ---------------------------------------------------------------------------
# Message-ID generation
# ---------------------------------------------------------------------------

def _generate_message_id() -> str:
    """Generates a unique RFC 2822 Message-ID."""
    domain = SENDER_EMAIL.split("@")[-1]
    unique = uuid.uuid4().hex
    return f"<{unique}@{domain}>"


# ---------------------------------------------------------------------------
# SMTP send
# ---------------------------------------------------------------------------

def _build_mime_message(
    to_email: str,
    subject: str,
    body: str,
    message_id: str,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> MIMEMultipart:
    """Builds a MIME email message with all required headers."""
    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((SENDER_NAME, SENDER_EMAIL))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = message_id

    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to

    # Plain text only — better deliverability than HTML for cold email
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


# def _send_via_smtp(msg: MIMEMultipart, to_email: str) -> None:
#     """Opens an SMTP connection and sends a single message."""
#     with smtplib.SMTP(ZOHO_SMTP_HOST, ZOHO_SMTP_PORT) as server:
#         server.ehlo()
#         server.starttls()
#         server.ehlo()
#         server.login(ZOHO_EMAIL, ZOHO_APP_PASSWORD)
#         server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
def _send_via_smtp(msg: MIMEMultipart, to_email: str) -> None:
    with smtplib.SMTP_SSL(ZOHO_SMTP_HOST, ZOHO_SMTP_PORT) as server:
        server.ehlo()
        server.login(ZOHO_EMAIL, ZOHO_APP_PASSWORD)
        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())


# ---------------------------------------------------------------------------
# Public send functions
# ---------------------------------------------------------------------------

def send_initial_email(lead: Lead) -> Optional[str]:
    """
    Sends the initial email to a lead.

    Returns the Message-ID string on success, None on failure.
    The caller is responsible for writing it to the sheet.
    """
    to_email = lead.get("email", "").strip()
    subject = lead.get("subject_line", "").strip()

    if not to_email or not subject:
        logger.error(f"Cannot send initial — missing email or subject for row {lead.get('_row_number')}")
        return None

    try:
        template_str = _load_template("initial.txt")
        body = _render_template(template_str, lead)
        message_id = _generate_message_id()

        msg = _build_mime_message(
            to_email=to_email,
            subject=subject,
            body=body,
            message_id=message_id,
        )

        _send_via_smtp(msg, to_email)
        logger.info(f"Sent initial to {to_email} (row {lead.get('_row_number')})")
        return message_id

    except Exception as e:
        logger.error(f"Failed to send initial to {to_email}: {e}")
        return None


def send_followup(lead: Lead, followup_number: int) -> bool:
    """
    Sends a threaded follow-up email.

    followup_number: 1, 2, or 3 (3 = nudge)
    Returns True on success, False on failure.

    Uses the stored message_id to thread correctly via In-Reply-To.
    """
    to_email = lead.get("email", "").strip()
    subject_line = lead.get("subject_line", "").strip()
    original_message_id = lead.get("message_id", "").strip()

    if not to_email or not subject_line:
        logger.error(f"Cannot send FU{followup_number} — missing email/subject for row {lead.get('_row_number')}")
        return False

    if not original_message_id:
        logger.error(f"Cannot send FU{followup_number} — no message_id stored for row {lead.get('_row_number')}")
        return False

    template_map = {
        1: "followup1.txt",
        2: "followup2.txt",
        3: "nudge.txt",
    }

    template_file = template_map.get(followup_number)
    if not template_file:
        logger.error(f"Unknown followup_number: {followup_number}")
        return False

    is_nudge = followup_number == 3

    try:
        template_str = _load_template(template_file)
        body = _render_template(template_str, lead, is_nudge=is_nudge)
        message_id = _generate_message_id()
        subject = f"RE: {subject_line}"

        msg = _build_mime_message(
            to_email=to_email,
            subject=subject,
            body=body,
            message_id=message_id,
            in_reply_to=original_message_id,
            references=original_message_id,
        )

        _send_via_smtp(msg, to_email)
        logger.info(f"Sent FU{followup_number} to {to_email} (row {lead.get('_row_number')})")
        return True

    except Exception as e:
        logger.error(f"Failed to send FU{followup_number} to {to_email}: {e}")
        return False
