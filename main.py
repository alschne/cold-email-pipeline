"""
main.py
-------
Orchestrates the full cold email pipeline. Run this daily via Cloud Run.

Execution order:
  1. Poll IMAP for replies / bounces — update sheet first so nothing
     sends to someone who already replied or bounced
  2. Generate missing emails (pattern + verification)
  3. Generate missing personalization lines
  4. Derive and write subject_line + cta for new leads
  5. Send follow-ups (priority — time-sensitive)
  6. Send initial emails (fill remaining budget)
  7. Log summary

Budget logic:
  - MAX_TOTAL and MIN_INITIALS_RESERVED read from config tab in sheet
  - Follow-ups are prioritized; within follow-ups, window-closing leads
    (fewest days remaining) are sent first
  - At least MIN_INITIALS_RESERVED slots are always reserved for initials
    even on heavy follow-up days
  - Leads with reply_status set (replied/bounced/left_company) are skipped
    for all sends

Usage:
  python main.py              # normal run
  python main.py --dry-run    # prints actions without sending
"""

import argparse
import logging
import sys
from datetime import date
from typing import Optional

from config import (
    STATUS_READY,
    STATUS_SENT,
    STATUS_BOUNCED,
    STATUS_REPLIED,
    STATUS_LEFT_COMPANY,
    STATUS_NEEDS_REVIEW,
    VERIF_INVALID,
    SUBJECT_LINES,
    CTAS,
    FU1_WINDOW,
    FU2_WINDOW,
    NUDGE_WINDOW,
)
from sheets_handler import Lead, get_all_leads, get_config, update_lead_fields
from email_generator import generate_and_verify_email
from ai_personalization import generate_personalization, generate_nudge_personalization
from email_sender import send_initial_email, send_followup
from imap_poller import update_sheet_with_replies
from utils import (
    is_sending_day,
    today,
    compute_target_date,
    is_within_window,
    is_past_window,
    days_remaining_in_window,
    parse_date,
    format_date,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Cold email pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without sending emails or writing to sheet",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Guard: should we run today?
# ---------------------------------------------------------------------------

def should_run_today(run_date: date) -> bool:
    """Returns False if today is not a valid sending day."""
    return is_sending_day(run_date)


# ---------------------------------------------------------------------------
# Step 1: Derive subject_line and cta for new leads
# ---------------------------------------------------------------------------

def derive_static_fields(lead: Lead, dry_run: bool) -> None:
    """
    Writes subject_line and cta to the sheet if not already set.
    These are derived from role_level and never change.
    """
    fields_to_write = {}

    if not lead.get("subject_line", "").strip():
        role_level = lead.get("role_level", "").strip()
        subject = SUBJECT_LINES.get(role_level)
        if subject:
            fields_to_write["subject_line"] = subject
        else:
            logger.warning(f"Unknown role_level '{role_level}' for row {lead.get('_row_number')}")

    if not lead.get("cta", "").strip():
        role_level = lead.get("role_level", "").strip()
        cta = CTAS.get(role_level)
        if cta:
            fields_to_write["cta"] = cta

    if fields_to_write and not dry_run:
        update_lead_fields(lead, fields_to_write)
        lead.update(fields_to_write)


# ---------------------------------------------------------------------------
# Step 2: Generate email address for leads that don't have one
# ---------------------------------------------------------------------------

def step_generate_emails(leads: list[Lead], dry_run: bool) -> None:
    logger.info("--- Step: Generate missing email addresses ---")
    for lead in leads:
        if lead.get("email", "").strip():
            continue  # already has email

        if dry_run:
            logger.info(f"[DRY RUN] Would generate email for row {lead.get('_row_number')}: "
                        f"{lead.get('first_name')} {lead.get('last_name')} @ {lead.get('domain')}")
            continue

        email_addr, verif_result = generate_and_verify_email(lead)

        fields: dict = {"verification_result": verif_result}

        if email_addr:
            fields["email"] = email_addr
            if verif_result == VERIF_INVALID:
                fields["status"] = STATUS_NEEDS_REVIEW
            else:
                # Only set ready_to_send if not already set
                if not lead.get("status", "").strip():
                    fields["status"] = STATUS_READY
        else:
            fields["status"] = STATUS_NEEDS_REVIEW

        update_lead_fields(lead, fields)
        lead.update(fields)


# ---------------------------------------------------------------------------
# Step 3: Generate personalization for leads that need it
# ---------------------------------------------------------------------------

def step_generate_personalization(leads: list[Lead], dry_run: bool) -> None:
    logger.info("--- Step: Generate missing personalization ---")
    for lead in leads:
        # Skip leads we can't send to
        if lead.get("status") in (STATUS_NEEDS_REVIEW, STATUS_BOUNCED, STATUS_LEFT_COMPANY):
            continue

        needs_personalization = not lead.get("personalization", "").strip()
        needs_nudge_personalization = (
            not lead.get("personalization_nudge", "").strip()
            and lead.get("nudge_target", "").strip()
            and not lead.get("nudge_sent", "").strip()
        )

        if needs_personalization:
            if dry_run:
                logger.info(f"[DRY RUN] Would generate personalization for row {lead.get('_row_number')}")
                continue
            text = generate_personalization(lead)
            if text:
                update_lead_fields(lead, {"personalization": text})
                lead["personalization"] = text
            else:
                logger.warning(f"Personalization generation failed for row {lead.get('_row_number')}")

        if needs_nudge_personalization:
            if dry_run:
                logger.info(f"[DRY RUN] Would generate nudge personalization for row {lead.get('_row_number')}")
                continue
            text = generate_nudge_personalization(lead)
            if text:
                update_lead_fields(lead, {"personalization_nudge": text})
                lead["personalization_nudge"] = text


# ---------------------------------------------------------------------------
# Follow-up eligibility
# ---------------------------------------------------------------------------

def _is_terminal(lead: Lead) -> bool:
    """Lead should receive no more emails."""
    return lead.get("reply_status", "").strip() in (
        STATUS_REPLIED, STATUS_BOUNCED, STATUS_LEFT_COMPANY
    ) or lead.get("status", "").strip() in (STATUS_BOUNCED, STATUS_LEFT_COMPANY)


def _get_due_followups(leads: list[Lead], run_date: date) -> list[tuple[Lead, int, int]]:
    """
    Returns list of (lead, followup_number, days_remaining) for all
    follow-ups that are due today or overdue.

    followup_number: 1=FU1, 2=FU2, 3=Nudge
    Sorted by days_remaining ascending (window closing soonest = highest priority).
    """
    due = []

    for lead in leads:
        if _is_terminal(lead):
            continue
        if lead.get("status", "").strip() != STATUS_SENT:
            continue

        date_sent = parse_date(lead.get("date_sent", ""))
        if not date_sent:
            continue

        # FU1
        if not lead.get("fu1_sent", "").strip():
            target = parse_date(lead.get("fu1_target", ""))
            if target and target <= run_date:
                remaining = days_remaining_in_window(date_sent, FU1_WINDOW, run_date)
                due.append((lead, 1, remaining))
            elif not target and (is_within_window(date_sent, FU1_WINDOW, run_date)
                                  or is_past_window(date_sent, FU1_WINDOW, run_date)):
                # Target not yet set — set it now and check eligibility
                remaining = days_remaining_in_window(date_sent, FU1_WINDOW, run_date)
                due.append((lead, 1, remaining))

        # FU2 — only if FU1 already sent
        elif not lead.get("fu2_sent", "").strip() and lead.get("fu1_sent", "").strip():
            target = parse_date(lead.get("fu2_target", ""))
            if target and target <= run_date:
                remaining = days_remaining_in_window(date_sent, FU2_WINDOW, run_date)
                due.append((lead, 2, remaining))
            elif not target and (is_within_window(date_sent, FU2_WINDOW, run_date)
                                  or is_past_window(date_sent, FU2_WINDOW, run_date)):
                remaining = days_remaining_in_window(date_sent, FU2_WINDOW, run_date)
                due.append((lead, 2, remaining))

        # Nudge — only if FU2 already sent
        elif (not lead.get("nudge_sent", "").strip()
              and lead.get("fu2_sent", "").strip()):
            target = parse_date(lead.get("nudge_target", ""))
            if target and target <= run_date:
                remaining = days_remaining_in_window(date_sent, NUDGE_WINDOW, run_date)
                due.append((lead, 3, remaining))
            elif not target and (is_within_window(date_sent, NUDGE_WINDOW, run_date)
                                  or is_past_window(date_sent, NUDGE_WINDOW, run_date)):
                remaining = days_remaining_in_window(date_sent, NUDGE_WINDOW, run_date)
                due.append((lead, 3, remaining))

    # Sort: lowest days_remaining first (most urgent)
    due.sort(key=lambda x: x[2])
    return due


# ---------------------------------------------------------------------------
# Step 4: Send follow-ups
# ---------------------------------------------------------------------------

def step_send_followups(
    leads: list[Lead],
    run_date: date,
    budget: int,
    dry_run: bool,
) -> int:
    """Sends follow-ups in priority order. Returns count sent."""
    logger.info("--- Step: Send follow-ups ---")
    due = _get_due_followups(leads, run_date)
    sent = 0

    for lead, fu_num, days_remaining in due:
        if sent >= budget:
            break

        # Ensure nudge personalization exists before sending
        if fu_num == 3 and not lead.get("personalization_nudge", "").strip():
            logger.warning(f"Skipping nudge for row {lead.get('_row_number')} — nudge personalization not ready")
            continue

        if dry_run:
            logger.info(
                f"[DRY RUN] Would send FU{fu_num} to {lead.get('email')} "
                f"(row {lead.get('_row_number')}, {days_remaining} days remaining in window)"
            )
            sent += 1
            continue

        success = send_followup(lead, fu_num)
        if success:
            fields: dict = {}
            if fu_num == 1:
                fields["fu1_sent"] = format_date(run_date)
                # Set fu2 target date now that fu1 is sent
                date_sent = parse_date(lead.get("date_sent", ""))
                if date_sent and not lead.get("fu2_target", "").strip():
                    fields["fu2_target"] = format_date(compute_target_date(date_sent, FU2_WINDOW))
            elif fu_num == 2:
                fields["fu2_sent"] = format_date(run_date)
                date_sent = parse_date(lead.get("date_sent", ""))
                if date_sent and not lead.get("nudge_target", "").strip():
                    fields["nudge_target"] = format_date(compute_target_date(date_sent, NUDGE_WINDOW))
            elif fu_num == 3:
                fields["nudge_sent"] = format_date(run_date)

            update_lead_fields(lead, fields)
            lead.update(fields)
            sent += 1

    logger.info(f"Follow-ups sent: {sent}")
    return sent


# ---------------------------------------------------------------------------
# Step 5: Send initial emails
# ---------------------------------------------------------------------------

def _get_ready_initials(leads: list[Lead]) -> list[Lead]:
    """Returns leads ready for initial send, in sheet order."""
    return [
        lead for lead in leads
        if lead.get("status", "").strip() == STATUS_READY
        and lead.get("email", "").strip()
        and lead.get("personalization", "").strip()
        and not _is_terminal(lead)
    ]


def step_send_initials(
    leads: list[Lead],
    run_date: date,
    budget: int,
    dry_run: bool,
) -> int:
    """Sends initial emails. Returns count sent."""
    logger.info("--- Step: Send initial emails ---")
    ready = _get_ready_initials(leads)
    sent = 0

    for lead in ready:
        if sent >= budget:
            break

        if dry_run:
            logger.info(
                f"[DRY RUN] Would send initial to {lead.get('email')} "
                f"(row {lead.get('_row_number')})"
            )
            sent += 1
            continue

        message_id = send_initial_email(lead)
        if message_id:
            fu1_target = compute_target_date(run_date, FU1_WINDOW)
            fields = {
                "status": STATUS_SENT,
                "message_id": message_id,
                "date_sent": format_date(run_date),
                "fu1_target": format_date(fu1_target),
            }
            update_lead_fields(lead, fields)
            lead.update(fields)
            sent += 1

    logger.info(f"Initial emails sent: {sent}")
    return sent


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run(dry_run: bool = False) -> None:
    run_date = today()

    if not should_run_today(run_date):
        logger.info(f"Today ({run_date}) is not a sending day. Exiting.")
        return

    logger.info(f"Pipeline starting — {run_date} {'[DRY RUN]' if dry_run else ''}")

    # Load config
    config = get_config()
    max_total = int(config.get("MAX_TOTAL", 5))
    min_initials_reserved = int(config.get("MIN_INITIALS_RESERVED", 2))

    logger.info(f"Config: MAX_TOTAL={max_total}, MIN_INITIALS_RESERVED={min_initials_reserved}")

    # Load all leads once
    leads = get_all_leads()
    logger.info(f"Loaded {len(leads)} leads")

    # Step 1 — Poll for replies first (before any sends)
    logger.info("--- Step: Poll IMAP for replies ---")
    if not dry_run:
        updated = update_sheet_with_replies(leads)
        logger.info(f"Reply statuses updated: {updated}")
        # Reload leads to reflect reply status updates
        leads = get_all_leads()
    else:
        logger.info("[DRY RUN] Skipping IMAP poll")

    # Step 2 — Derive static fields (subject_line, cta)
    for lead in leads:
        derive_static_fields(lead, dry_run)

    # Step 3 — Generate missing emails
    step_generate_emails(leads, dry_run)

    # Step 4 — Generate missing personalization
    step_generate_personalization(leads, dry_run)

    # Budget calculation
    # Follow-ups get priority. Initials guaranteed at least min_initials_reserved
    # slots, unless total budget is too small.
    followup_budget = max(0, max_total - min_initials_reserved)
    initial_budget_floor = min(min_initials_reserved, max_total)

    # Step 5 — Send follow-ups
    followups_sent = step_send_followups(leads, run_date, followup_budget, dry_run)

    # Recalculate initial budget — follow-ups may not have used their full budget
    slots_used = followups_sent
    slots_remaining = max_total - slots_used
    initial_budget = max(initial_budget_floor, slots_remaining)
    initial_budget = min(initial_budget, max_total - slots_used)

    # Step 6 — Send initials
    initials_sent = step_send_initials(leads, run_date, initial_budget, dry_run)

    total_sent = followups_sent + initials_sent
    logger.info(
        f"Pipeline complete — total sent: {total_sent} "
        f"(follow-ups: {followups_sent}, initials: {initials_sent})"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = parse_args()
    run(dry_run=args.dry_run)
