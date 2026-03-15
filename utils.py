"""
utils.py
--------
Business day calculations, follow-up window logic, and date helpers.

Business days in this pipeline = Monday through Thursday only.
Friday, Saturday, Sunday never count.
US holidays (from config) are also skipped.
"""

import random
from datetime import date, timedelta
from typing import Optional

from config import US_HOLIDAYS


# ---------------------------------------------------------------------------
# Core business day helpers
# ---------------------------------------------------------------------------

def is_sending_day(d: date) -> bool:
    """Returns True if the pipeline should send on this date."""
    return d.weekday() < 4 and d not in US_HOLIDAYS  # 0=Mon, 3=Thu


def next_sending_day(d: date) -> date:
    """Returns the next valid sending day on or after d."""
    while not is_sending_day(d):
        d += timedelta(days=1)
    return d


def add_business_days(start: date, n: int) -> date:
    """
    Adds n business days (Mon–Thu, skipping holidays) to start.
    start itself is not counted.
    """
    current = start
    counted = 0
    while counted < n:
        current += timedelta(days=1)
        if is_sending_day(current):
            counted += 1
    return current


def business_days_between(start: date, end: date) -> int:
    """
    Counts business days (Mon–Thu, skipping holidays) between two dates.
    start is exclusive, end is inclusive.
    """
    count = 0
    current = start + timedelta(days=1)
    while current <= end:
        if is_sending_day(current):
            count += 1
        current += timedelta(days=1)
    return count


# ---------------------------------------------------------------------------
# Follow-up target date generation
# ---------------------------------------------------------------------------

def compute_target_date(date_sent: date, window: tuple[int, int]) -> date:
    """
    Picks a random business day within [min_days, max_days] business days
    after date_sent. The randomness staggers sends naturally and avoids
    robotic regularity.
    """
    min_days, max_days = window
    offset = random.randint(min_days, max_days)
    candidate = add_business_days(date_sent, offset)
    # Ensure the result is itself a valid sending day
    return next_sending_day(candidate)


# ---------------------------------------------------------------------------
# Window closing priority
# ---------------------------------------------------------------------------

def days_remaining_in_window(
    date_sent: date,
    window: tuple[int, int],
    today: Optional[date] = None,
) -> int:
    """
    Returns how many business days remain before the window closes.
    A lead at 0 or negative is overdue — highest priority.
    A lead with fewer days remaining is higher priority than one with more.
    """
    if today is None:
        today = date.today()
    _, max_days = window
    window_close = add_business_days(date_sent, max_days)
    return business_days_between(today, window_close)


def is_within_window(
    date_sent: date,
    window: tuple[int, int],
    today: Optional[date] = None,
) -> bool:
    """Returns True if today falls within the follow-up window."""
    if today is None:
        today = date.today()
    min_days, max_days = window
    days_since = business_days_between(date_sent, today)
    return min_days <= days_since <= max_days


def is_past_window(
    date_sent: date,
    window: tuple[int, int],
    today: Optional[date] = None,
) -> bool:
    """Returns True if the window has already closed (overdue)."""
    if today is None:
        today = date.today()
    _, max_days = window
    days_since = business_days_between(date_sent, today)
    return days_since > max_days


# ---------------------------------------------------------------------------
# Date parsing helpers
# ---------------------------------------------------------------------------

DATE_FORMAT = "%Y-%m-%d"


def parse_date(value: str) -> Optional[date]:
    """Parses a YYYY-MM-DD string. Returns None if blank or invalid."""
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def format_date(d: date) -> str:
    """Formats a date as YYYY-MM-DD for sheet storage."""
    return d.strftime(DATE_FORMAT)


def today() -> date:
    """Wrapper so tests can monkeypatch this easily."""
    return date.today()
