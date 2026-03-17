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

Caching strategy:
  All gspread client, spreadsheet, and worksheet objects are cached at the
  module level for the duration of a single pipeline run. This avoids
  repeated read requests to the Sheets API which would trigger 429 rate
  limit errors at scale. The cache is process-scoped — Cloud Run spins up
  a fresh process each run so there is no risk of stale data across runs.
"""

import json
import logging
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

logger = logging.getLogger(__name__)

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

    if os.path.isfile(sa_value):
        creds = Credentials.from_service_account_file(sa_value, scopes=_SCOPES)
    else:
        info = json.loads(sa_value)
        creds = Credentials.from_service_account_info(info, scopes=_SCOPES)

    return gspread.authorize(creds)


# ---------------------------------------------------------------------------
# Module-level cache — one connection, one spreadsheet, three worksheets
# for the entire pipeline run. Every sheet.worksheet() call is a read
# request — caching eliminates all of them after the first.
# ---------------------------------------------------------------------------

_sheet_cache: Optional[gspread.Spreadsheet] = None
_leads_ws_cache: Optional[gspread.Worksheet] = None
_pattern_ws_cache: Optional[gspread.Worksheet] = None
_config_ws_cache: Optional[gspread.Worksheet] = None
_header_cache: Optional[list] = None


def _get_sheet() -> gspread.Spreadsheet:
    global _sheet_cache
    if _sheet_cache is None:
        client = _get_client()
        _sheet_cache = client.open_by_key(GOOGLE_SHEET_ID)
        logger.info("Sheets connection established")
    return _sheet_cache


def _get_leads_ws() -> gspread.Worksheet:
    global _leads_ws_cache
    if _leads_ws_cache is None:
        _leads_ws_cache = _get_sheet().worksheet(LEADS_TAB)
    return _leads_ws_cache


def _get_pattern_ws() -> gspread.Worksheet:
    global _pattern_ws_cache
    if _pattern_ws_cache is None:
        _pattern_ws_cache = _get_sheet().worksheet(PATTERN_DB_TAB)
    return _pattern_ws_cache


def _get_config_ws() -> gspread.Worksheet:
    global _config_ws_cache
    if _config_ws_cache is None:
        _config_ws_cache = _get_sheet().worksheet(CONFIG_TAB)
    return _config_ws_cache


def _get_header() -> list:
    global _header_cache
    if _header_cache is None:
        _header_cache = _get_leads_ws().row_values(1)
    return _header_cache


# ---------------------------------------------------------------------------
# Config tab
# ---------------------------------------------------------------------------

def get_config() -> dict:
    """
    Reads the config tab and returns a dict.
    Expected rows: key | value
    Returns MAX_TOTAL (int) and MIN_INITIALS_RESERVED (int).
    """
    ws = _get_config_ws()
    rows = ws.get_all_values()

    config = {}
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

def get_pattern_db() -> dict:
    """Returns {domain: pattern} from the pattern_db tab."""
    ws = _get_pattern_ws()
    rows = ws.get_all_values()

    db = {}
    for row in rows[1:]:
        if len(row) >= 2 and row[0].strip():
            db[row[0].strip().lower()] = row[1].strip().lower()
    return db


def upsert_pattern_db(domain: str, pattern: str) -> None:
    """Adds or updates a domain -> pattern entry."""
    ws = _get_pattern_ws()
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


def _build_col_index(header_row: list) -> dict:
    """Maps column name -> 0-based index from the actual sheet header row."""
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


def get_all_leads() -> list:
    """
    Fetches all rows from the leads tab.
    Returns a list of Lead dicts with _row_number set.
    """
    ws = _get_leads_ws()
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

def update_lead_fields(lead: Lead, fields: dict) -> None:
    """
    Updates specific columns for a lead row.
    fields = {column_name: new_value}
    Batches all updates for the row into a single API call.
    """
    ws = _get_leads_ws()
    header = _get_header()
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


def append_lead(lead_data: dict) -> None:
    """
    Appends a new row to the leads tab.
    lead_data keys must match LEAD_COLUMNS.
    Used primarily by the future leads-finding pipeline.
    """
    ws = _get_leads_ws()
    header = _get_header()

    row = [str(lead_data.get(col, "")) for col in header]
    ws.append_row(row)