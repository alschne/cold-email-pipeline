"""
notifier.py
-----------
Sends a summary email to Allie after each pipeline run.

Called at the end of every Mon-Thu run regardless of send count.
Uses the existing Zoho SMTP connection — no new services needed.

Summary includes:
  - Emails sent (initials, FU1, FU2, nudges, total)
  - Pipeline health (emails generated, verification failures, Gemini failures)
  - Reply activity since last run (replies, bounces, left company, OOO)
  - Sheet health (total leads, ready to send, needs manual review)
"""

import logging
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

from config import (
    ZOHO_EMAIL,
    ZOHO_APP_PASSWORD,
    ZOHO_SMTP_HOST,
    ZOHO_SMTP_PORT,
    SENDER_NAME,
    SENDER_EMAIL,
    GOOGLE_SHEET_ID,
    NOTIFICATION_EMAIL,
    NOTIFICATION_SENDER_NAME,
)
from sheets_handler import Lead

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Summary data container
# ---------------------------------------------------------------------------

class PipelineSummary:
    """
    Collects metrics throughout the pipeline run.
    Pass this object into each step and call the appropriate increment methods.
    """

    def __init__(self, run_date: date, max_total: int):
        self.run_date = run_date
        self.max_total = max_total

        # Sends
        self.initials_sent: int = 0
        self.fu1_sent: int = 0
        self.fu2_sent: int = 0
        self.nudges_sent: int = 0

        # Pipeline health
        self.emails_generated: int = 0
        self.verification_failures: int = 0
        self.gemini_failures: int = 0

        # Reply activity (detected this run by IMAP poller)
        self.new_replies: int = 0
        self.new_bounces: int = 0
        self.new_left_company: int = 0
        self.new_out_of_office: int = 0

        # Sheet health (snapshot at end of run)
        self.total_leads: int = 0
        self.ready_to_send: int = 0
        self.needs_manual_review: int = 0
        self.sequence_complete: int = 0  # nudge sent, no reply

    @property
    def total_sent(self) -> int:
        return self.initials_sent + self.fu1_sent + self.fu2_sent + self.nudges_sent

    def snapshot_leads(self, leads: list[Lead]) -> None:
        """
        Takes a snapshot of sheet health from the final leads list.
        Call this at the end of the run after all sends are complete.
        """
        from config import STATUS_READY, STATUS_NEEDS_REVIEW

        self.total_leads = len(leads)
        for lead in leads:
            status = lead.get("status", "").strip()
            reply_status = lead.get("reply_status", "").strip()
            nudge_sent = lead.get("nudge_sent", "").strip()

            if status == STATUS_READY:
                self.ready_to_send += 1
            elif status == STATUS_NEEDS_REVIEW:
                self.needs_manual_review += 1

            if nudge_sent and not reply_status:
                self.sequence_complete += 1


# ---------------------------------------------------------------------------
# Email rendering
# ---------------------------------------------------------------------------

def _render_summary_email(summary: PipelineSummary) -> str:
    """Renders the plain text summary email body."""

    total_followups = summary.fu1_sent + summary.fu2_sent + summary.nudges_sent

    lines = [
        f"Cold Email Pipeline — Daily Summary",
        f"Run date: {summary.run_date.strftime('%A, %B %-d, %Y')}",
        f"Daily limit (MAX_TOTAL): {summary.max_total}",
        f"",
        f"─────────────────────────────────────",
        f"EMAILS SENT TODAY",
        f"─────────────────────────────────────",
        f"  Initial emails:   {summary.initials_sent}",
        f"  Follow-up 1:      {summary.fu1_sent}",
        f"  Follow-up 2:      {summary.fu2_sent}",
        f"  Nudges:           {summary.nudges_sent}",
        f"  ─────────────────",
        f"  Total sent:       {summary.total_sent} / {summary.max_total}",
        f"",
        f"─────────────────────────────────────",
        f"PIPELINE HEALTH",
        f"─────────────────────────────────────",
        f"  New emails generated:     {summary.emails_generated}",
        f"  Verification failures:    {summary.verification_failures}",
        f"  Personalization failures: {summary.gemini_failures}",
    ]

    # Flag issues prominently
    if summary.verification_failures > 0:
        lines.append(f"  ⚠️  Check 'needs_manual_review' rows in sheet")
    if summary.gemini_failures > 0:
        lines.append(f"  ⚠️  Gemini errors — some leads may be missing personalization")

    lines += [
        f"",
        f"─────────────────────────────────────",
        f"REPLY ACTIVITY (detected this run)",
        f"─────────────────────────────────────",
        f"  Replies received:    {summary.new_replies}",
        f"  Bounces detected:    {summary.new_bounces}",
        f"  Left company:        {summary.new_left_company}",
        f"  Out of office:       {summary.new_out_of_office}",
    ]

    if summary.new_bounces > 0:
        lines.append(f"  ⚠️  Review bounces — check domain health if rate exceeds 3%")
    if summary.new_left_company > 0:
        lines.append(f"  ℹ️  Left company leads have been stopped automatically")

    lines += [
        f"",
        f"─────────────────────────────────────",
        f"SHEET HEALTH",
        f"─────────────────────────────────────",
        f"  Total leads in sheet:      {summary.total_leads}",
        f"  Ready to send (queued):    {summary.ready_to_send}",
        f"  Needs manual review:       {summary.needs_manual_review}",
        f"  Sequence complete:         {summary.sequence_complete}",
    ]

    if summary.ready_to_send == 0:
        lines.append(f"  ⚠️  No leads queued — add leads to keep pipeline active")
    if summary.needs_manual_review > 0:
        lines.append(f"  ℹ️  Manual review rows: email could not be verified — check sheet")

    lines += [
        f"",
        f"─────────────────────────────────────",
        f"View sheet: https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}",
        f"─────────────────────────────────────",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Send notification
# ---------------------------------------------------------------------------

def send_summary(summary: PipelineSummary, dry_run: bool = False) -> None:
    """
    Sends the pipeline summary email to the sender (Allie).
    Skipped in dry-run mode — prints to log instead.
    """
    body = _render_summary_email(summary)

    if dry_run:
        logger.info("[DRY RUN] Would send summary email:\n" + body)
        return

    subject = (
        f"Cold Email Summary — {summary.total_sent} sent"
        if summary.total_sent > 0
        else f"Cold Email Summary — No sends today ({summary.run_date.strftime('%a %b %-d')})"
    )

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = formataddr((NOTIFICATION_SENDER_NAME, SENDER_EMAIL))
        msg["To"] = NOTIFICATION_EMAIL
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP_SSL(ZOHO_SMTP_HOST, ZOHO_SMTP_PORT) as server:
            server.ehlo()
            server.login(ZOHO_EMAIL, ZOHO_APP_PASSWORD)
            server.sendmail(SENDER_EMAIL, NOTIFICATION_EMAIL, msg.as_string())

        logger.info(f"Summary email sent — {summary.total_sent} emails sent today")

    except Exception as e:
        logger.error(f"Failed to send summary email: {e}")
        # Never let a notification failure crash the pipeline