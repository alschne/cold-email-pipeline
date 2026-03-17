"""
main.py
-------
Orchestrates the full cold email pipeline. Run this daily via Cloud Run.

Execution order:
  1. Poll IMAP for replies / bounces — update sheet first so nothing
     sends to someone who already replied or bounced
  2. Generate missing email addresses (all leads)
  3. Derive static fields — subject_line, cta (all leads)
  4. Calculate today's send budget
  5. Identify exactly which leads are sending today (follow-ups + initials)
  6. Generate personalization ONLY for today's senders — not all leads
  7. Send follow-ups
  8. Send initial emails
  9. Send summary notification

Budget logic:
  - MAX_TOTAL and MIN_INITIALS_RESERVED read from config tab in sheet
  - Follow-ups are prioritized; within follow-ups, window-closing leads
    (fewest days remaining) are sent first
  - At least MIN_INITIALS_RESERVED slots are always reserved for initials
    even on heavy follow-up days
  - Leads with reply_status set (replied/bounced/left_company) are skipped

Usage:
  python main.py              # normal run
  python main.py --dry-run    # prints actions without sending
  python main.py --force      # run regardless of day (for testing)
"""

from dotenv import load_dotenv
load_dotenv()

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
    STATUS_OUT_OF_OFFICE,
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
from notifier import PipelineSummary, send_summary
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run regardless of day (for testing)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Guard: should we run today?
# ---------------------------------------------------------------------------

def should_run_today(run_date: date) -> bool:
    """Returns False if today is not a valid sending day."""
    return is_sending_day(run_date)


# ---------------------------------------------------------------------------
# Derive static fields
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
# Generate email addresses
# ---------------------------------------------------------------------------

def step_generate_emails(leads: list, dry_run: bool, summary: PipelineSummary) -> None:
    logger.info("--- Step: Generate missing email addresses ---")
    for lead in leads:
        if lead.get("email", "").strip():
            continue

        if dry_run:
            logger.info(f"[DRY RUN] Would generate email for row {lead.get('_row_number')}: "
                        f"{lead.get('first_name')} {lead.get('last_name')} @ {lead.get('domain')}")
            continue

        email_addr, verif_result = generate_and_verify_email(lead)
        fields: dict = {"verification_result": verif_result}

        if email_addr:
            fields["email"] = email_addr
            summary.emails_generated += 1
            if verif_result == VERIF_INVALID:
                fields["status"] = STATUS_NEEDS_REVIEW
                summary.verification_failures += 1
            else:
                if not lead.get("status", "").strip():
                    fields["status"] = STATUS_READY
        else:
            fields["status"] = STATUS_NEEDS_REVIEW
            summary.verification_failures += 1

        update_lead_fields(lead, fields)
        lead.update(fields)


# ---------------------------------------------------------------------------
# Follow-up eligibility
# ---------------------------------------------------------------------------

def _is_terminal(lead: Lead) -> bool:
    """Lead should receive no more emails."""
    return lead.get("reply_status", "").strip() in (
        STATUS_REPLIED, STATUS_BOUNCED, STATUS_LEFT_COMPANY
    ) or lead.get("status", "").strip() in (STATUS_BOUNCED, STATUS_LEFT_COMPANY)


def _get_due_followups(leads: list, run_date: date) -> list:
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


def _get_ready_initials(leads: list) -> list:
    """Returns leads ready for initial send, in sheet order."""
    return [
        lead for lead in leads
        if lead.get("status", "").strip() == STATUS_READY
        and lead.get("email", "").strip()
        and not _is_terminal(lead)
    ]


# ---------------------------------------------------------------------------
# Generate personalization — only for today's senders
# ---------------------------------------------------------------------------

def step_generate_personalization_for_senders(
    todays_initials: list,
    todays_nudges: list,
    dry_run: bool,
    summary: PipelineSummary,
) -> None:
    """
    Generates personalization ONLY for leads that are actually sending today.

    todays_initials: leads sending initial email today (need personalization)
    todays_nudges:   leads sending nudge today (need personalization_nudge)

    This keeps Gemini API calls to MAX_TOTAL per run rather than calling
    for every lead in the sheet.
    """
    logger.info("--- Step: Generate personalization for today's senders ---")

    for lead in todays_initials:
        if lead.get("personalization", "").strip():
            continue  # already has it

        if dry_run:
            logger.info(f"[DRY RUN] Would generate personalization for row {lead.get('_row_number')}")
            continue

        text = generate_personalization(lead)
        if text:
            update_lead_fields(lead, {"personalization": text})
            lead["personalization"] = text
            logger.info(f"Generated personalization for row {lead.get('_row_number')}")
        else:
            logger.warning(f"Personalization generation failed for row {lead.get('_row_number')}")
            summary.gemini_failures += 1

    for lead in todays_nudges:
        if lead.get("personalization_nudge", "").strip():
            continue  # already has it

        if dry_run:
            logger.info(f"[DRY RUN] Would generate nudge personalization for row {lead.get('_row_number')}")
            continue

        text = generate_nudge_personalization(lead)
        if text:
            update_lead_fields(lead, {"personalization_nudge": text})
            lead["personalization_nudge"] = text
            logger.info(f"Generated nudge personalization for row {lead.get('_row_number')}")
        else:
            logger.warning(f"Nudge personalization generation failed for row {lead.get('_row_number')}")
            summary.gemini_failures += 1


# ---------------------------------------------------------------------------
# Send follow-ups
# ---------------------------------------------------------------------------

def step_send_followups(
    leads: list,
    run_date: date,
    budget: int,
    dry_run: bool,
    summary: PipelineSummary,
) -> tuple:
    """
    Sends follow-ups in priority order.
    Returns (count_sent, nudge_leads_sent_today) where nudge_leads is
    used to identify who needs nudge personalization generated.
    """
    logger.info("--- Step: Send follow-ups ---")
    due = _get_due_followups(leads, run_date)
    sent = 0
    nudge_leads = []

    for lead, fu_num, days_remaining in due:
        if sent >= budget:
            break

        if fu_num == 3:
            nudge_leads.append(lead)

        if dry_run:
            logger.info(
                f"[DRY RUN] Would send FU{fu_num} to {lead.get('email')} "
                f"(row {lead.get('_row_number')}, {days_remaining} days remaining in window)"
            )
            sent += 1
            continue

        # Skip nudge if personalization not ready (will be generated in next step
        # on subsequent run — should not happen with new order of operations)
        if fu_num == 3 and not lead.get("personalization_nudge", "").strip():
            logger.warning(f"Skipping nudge for row {lead.get('_row_number')} — nudge personalization not ready")
            continue

        success = send_followup(lead, fu_num)
        if success:
            fields: dict = {}
            if fu_num == 1:
                fields["fu1_sent"] = format_date(run_date)
                date_sent = parse_date(lead.get("date_sent", ""))
                if date_sent and not lead.get("fu2_target", "").strip():
                    fields["fu2_target"] = format_date(compute_target_date(date_sent, FU2_WINDOW))
                summary.fu1_sent += 1
            elif fu_num == 2:
                fields["fu2_sent"] = format_date(run_date)
                date_sent = parse_date(lead.get("date_sent", ""))
                if date_sent and not lead.get("nudge_target", "").strip():
                    fields["nudge_target"] = format_date(compute_target_date(date_sent, NUDGE_WINDOW))
                summary.fu2_sent += 1
            elif fu_num == 3:
                fields["nudge_sent"] = format_date(run_date)
                summary.nudges_sent += 1

            update_lead_fields(lead, fields)
            lead.update(fields)
            sent += 1

    logger.info(f"Follow-ups sent: {sent}")
    return sent, nudge_leads


# ---------------------------------------------------------------------------
# Send initial emails
# ---------------------------------------------------------------------------

def step_send_initials(
    leads: list,
    run_date: date,
    budget: int,
    dry_run: bool,
    summary: PipelineSummary,
) -> tuple:
    """
    Sends initial emails up to budget.
    Returns (count_sent, leads_sent_today) where leads_sent_today is used
    to identify who needs personalization generated.
    """
    logger.info("--- Step: Send initial emails ---")
    ready = _get_ready_initials(leads)
    sent = 0
    sent_leads = []

    for lead in ready:
        if sent >= budget:
            break
        sent_leads.append(lead)

        if dry_run:
            logger.info(
                f"[DRY RUN] Would send initial to {lead.get('email')} "
                f"(row {lead.get('_row_number')})"
            )
            sent += 1
            continue

        # Skip if personalization missing (should not happen with new order)
        if not lead.get("personalization", "").strip():
            logger.warning(f"Skipping initial for row {lead.get('_row_number')} — personalization not ready")
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
            summary.initials_sent += 1

    logger.info(f"Initial emails sent: {sent}")
    return sent, sent_leads


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run(dry_run: bool = False, force: bool = False) -> None:
    run_date = today()

    if not should_run_today(run_date) and not force:
        logger.info(f"Today ({run_date}) is not a sending day. Exiting.")
        return

    logger.info(f"Pipeline starting — {run_date} {'[DRY RUN]' if dry_run else ''}")

    # Load config
    config = get_config()
    max_total = int(config.get("MAX_TOTAL", 5))
    min_initials_reserved = int(config.get("MIN_INITIALS_RESERVED", 2))
    logger.info(f"Config: MAX_TOTAL={max_total}, MIN_INITIALS_RESERVED={min_initials_reserved}")

    # Initialize summary
    summary = PipelineSummary(run_date=run_date, max_total=max_total)

    # Load all leads once
    leads = get_all_leads()
    logger.info(f"Loaded {len(leads)} leads")

    # Step 1 — Poll for replies first (before any sends)
    logger.info("--- Step: Poll IMAP for replies ---")
    if not dry_run:
        reply_results = update_sheet_with_replies(leads)
        summary.new_replies = reply_results.get(STATUS_REPLIED, 0)
        summary.new_bounces = reply_results.get(STATUS_BOUNCED, 0)
        summary.new_left_company = reply_results.get(STATUS_LEFT_COMPANY, 0)
        summary.new_out_of_office = reply_results.get(STATUS_OUT_OF_OFFICE, 0)
        total_reply_updates = sum(reply_results.values())
        logger.info(f"Reply statuses updated: {total_reply_updates}")
        leads = get_all_leads()
    else:
        logger.info("[DRY RUN] Skipping IMAP poll")

    # Step 2 — Derive static fields (subject_line, cta) for all leads
    for lead in leads:
        derive_static_fields(lead, dry_run)

    # Step 3 — Generate missing email addresses for all leads
    step_generate_emails(leads, dry_run, summary)

    # Step 4 — Calculate budget
    followup_budget = max(0, max_total - min_initials_reserved)
    initial_budget_floor = min(min_initials_reserved, max_total)

    # Step 5 — Identify today's follow-up senders (for nudges, need personalization)
    due_followups = _get_due_followups(leads, run_date)
    todays_nudge_leads = [
        lead for lead, fu_num, _ in due_followups[:followup_budget]
        if fu_num == 3
    ]

    # Step 6 — Identify today's initial senders
    ready_initials = _get_ready_initials(leads)
    # Calculate how many initial slots we'll have after follow-ups
    estimated_followup_count = min(len(due_followups), followup_budget)
    slots_remaining = max_total - estimated_followup_count
    initial_budget = max(initial_budget_floor, slots_remaining)
    initial_budget = min(initial_budget, max_total - estimated_followup_count)
    todays_initial_leads = ready_initials[:initial_budget]

    # Step 7 — Generate personalization ONLY for today's senders
    step_generate_personalization_for_senders(
        todays_initials=todays_initial_leads,
        todays_nudges=todays_nudge_leads,
        dry_run=dry_run,
        summary=summary,
    )

    # Step 8 — Send follow-ups
    followups_sent, _ = step_send_followups(leads, run_date, followup_budget, dry_run, summary)

    # Step 9 — Recalculate initial budget based on actual follow-ups sent
    slots_used = followups_sent
    slots_remaining = max_total - slots_used
    initial_budget = max(initial_budget_floor, slots_remaining)
    initial_budget = min(initial_budget, max_total - slots_used)

    # Step 10 — Send initials
    initials_sent, _ = step_send_initials(leads, run_date, initial_budget, dry_run, summary)

    total_sent = followups_sent + initials_sent
    logger.info(
        f"Pipeline complete — total sent: {total_sent} "
        f"(follow-ups: {followups_sent}, initials: {initials_sent})"
    )

    # Step 11 — Snapshot sheet health and send summary notification
    summary.snapshot_leads(leads)
    send_summary(summary, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = parse_args()
    run(dry_run=args.dry_run, force=args.force)