"""
sheets_handler.py
-----------------
All Google Sheets read/write operations.

Uses a service account for auth. The sheet must be shared with the service
account email (see SETUP.md Step 2).

Tab structure:
  - leads       : one row per lead, all pipeline columns
  - pattern_db  : domain → pattern lookup
  - config      : MAX_TOTAL, MIN_INITIALS_RESERVED
"""

import json
import os
from typing import Any, Optional

import gspread
from google.oauth2.service_account import Credentials

from config import (
    GOOGLE_SHEET_ID,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    LEADS_TAB,
    PATTERN_DB_TAB,
    CONFIG_TAB,
)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]


def _get_client() -> gspread.Client:
    """
    Builds a gspread client from either a JSON file path or a JSON string
    stored directly in the environment variable (Cloud Run injects secrets
    as env vars, not files).
    """
    sa_value = GOOGLE_SERVICE_ACCOUNT_JSON

    # If the value looks like a file path and that file exists, use it
    if os.path.isfile(sa_value):
        creds = Credentials.from_service_account_file(sa_value, scopes=_SCOPES)
    else:
        # Treat as a raw JSON string (Secret Manager injects this way)
        info = json.loads(sa_value)
        creds = Credentials.from_service_account_info(info, scopes=_SCOPES)

    return gspread.authorize(creds)


# def _get_sheet() -> gspread.Spreadsheet:
#     client = _get_client()
#     return client.open_by_key(GOOGLE_SHEET_ID)
_sheet_cache = None

def _get_sheet() -> gspread.Spreadsheet:
    global _sheet_cache
    if _sheet_cache is None:
        client = _get_client()
        _sheet_cache = client.open_by_key(GOOGLE_SHEET_ID)
    return _sheet_cache

_header_cache = None

def _get_header(ws) -> list[str]:
    global _header_cache
    if _header_cache is None:
        _header_cache = ws.row_values(1)
    return _header_cache

# ---------------------------------------------------------------------------
# Config tab
# ---------------------------------------------------------------------------

def get_config() -> dict[str, Any]:
    """
    Reads the config tab and returns a dict.
    Expected rows: key | value
    Returns MAX_TOTAL (int) and MIN_INITIALS_RESERVED (int).
    """
    sheet = _get_sheet()
    ws = sheet.worksheet(CONFIG_TAB)
    rows = ws.get_all_values()

    config: dict[str, Any] = {}
    for row in rows[1:]:  # skip header
        if len(row) >= 2 and row[0].strip():
            key = row[0].strip()
            raw = row[1].strip()
            try:
                config[key] = int(raw)
            except ValueError:
                config[key] = raw

    return config


# ---------------------------------------------------------------------------
# Pattern DB tab
# ---------------------------------------------------------------------------

def get_pattern_db() -> dict[str, str]:
    """Returns {domain: pattern} from the pattern_db tab."""
    sheet = _get_sheet()
    ws = sheet.worksheet(PATTERN_DB_TAB)
    rows = ws.get_all_values()

    db: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) >= 2 and row[0].strip():
            db[row[0].strip().lower()] = row[1].strip().lower()
    return db


def upsert_pattern_db(domain: str, pattern: str) -> None:
    """Adds or updates a domain → pattern entry."""
    sheet = _get_sheet()
    ws = sheet.worksheet(PATTERN_DB_TAB)
    rows = ws.get_all_values()

    domain = domain.strip().lower()
    pattern = pattern.strip().lower()

    for i, row in enumerate(rows[1:], start=2):
        if row and row[0].strip().lower() == domain:
            ws.update_cell(i, 2, pattern)
            return

    # Not found — append
    ws.append_row([domain, pattern])


# ---------------------------------------------------------------------------
# Leads tab — column index mapping
# ---------------------------------------------------------------------------

# Column names must match the sheet header exactly (case-sensitive)
LEAD_COLUMNS = [
    "first_name",
    "last_name",
    "company",
    "domain",
    "industry",
    "role_level",
    "role_context",
    "title",
    "email",
    "verification_result",
    "personalization",
    "personalization_nudge",
    "subject_line",
    "cta",
    "status",
    "message_id",
    "date_sent",
    "fu1_target",
    "fu1_sent",
    "fu2_target",
    "fu2_sent",
    "nudge_target",
    "nudge_sent",
    "reply_status",
    "notes",
]


def _build_col_index(header_row: list[str]) -> dict[str, int]:
    """Maps column name → 0-based index from the actual sheet header row."""
    return {name.strip(): i for i, name in enumerate(header_row)}


# ---------------------------------------------------------------------------
# Leads tab — read
# ---------------------------------------------------------------------------

class Lead(dict):
    """
    A dict subclass representing one leads row.
    Extras: _row_number (1-based sheet row) for writing back.
    """
    pass


def get_all_leads() -> list[Lead]:
    """
    Fetches all rows from the leads tab.
    Returns a list of Lead dicts with _row_number set.
    """
    sheet = _get_sheet()
    ws = sheet.worksheet(LEADS_TAB)
    all_values = ws.get_all_values()

    if not all_values:
        return []

    header = all_values[0]
    col_index = _build_col_index(header)
    leads = []

    for row_num, row in enumerate(all_values[1:], start=2):
        # Pad row to header length if trailing cells are empty
        padded = row + [""] * (len(header) - len(row))
        lead = Lead({col: padded[idx] for col, idx in col_index.items()})
        lead["_row_number"] = row_num
        leads.append(lead)

    return leads


# ---------------------------------------------------------------------------
# Leads tab — write
# ---------------------------------------------------------------------------

def update_lead_fields(lead: Lead, fields: dict[str, Any]) -> None:
    """
    Updates specific columns for a lead row.
    fields = {column_name: new_value}
    Only writes cells that actually changed.
    """
    sheet = _get_sheet()
    ws = sheet.worksheet(LEADS_TAB)
    # header = ws.row_values(1)
    header = _get_header(ws)
    col_index = _build_col_index(header)

    row_num = lead["_row_number"]
    updates = []

    for col_name, value in fields.items():
        if col_name not in col_index:
            raise ValueError(f"Column '{col_name}' not found in sheet header")
        col_num = col_index[col_name] + 1  # gspread is 1-based
        updates.append({
            "range": gspread.utils.rowcol_to_a1(row_num, col_num),
            "values": [[str(value) if value is not None else ""]],
        })

    if updates:
        ws.batch_update(updates)


def append_lead(lead_data: dict[str, Any]) -> None:
    """
    Appends a new row to the leads tab.
    lead_data keys must match LEAD_COLUMNS.
    Used primarily by the future leads-finding pipeline.
    """
    sheet = _get_sheet()
    ws = sheet.worksheet(LEADS_TAB)
    header = ws.row_values(1)

    row = [str(lead_data.get(col, "")) for col in header]
    ws.append_row(row)
