"""
ai_personalization.py
---------------------
Generates personalization lines via Gemini 1.5 Flash (free tier).

Two functions:
  generate_personalization()      — for initial, FU1, FU2
  generate_nudge_personalization() — for the final nudge (regenerated fresh)

The personalization line is a 1–2 sentence hook that acknowledges the
lead's specific context (company, industry, role) and bridges naturally
to Allie's value proposition. It should feel researched, not templated.

Gemini prompt is written tightly to avoid hallucinated claims.
"""

import logging
from typing import Optional

import google.generativeai as genai

from config import GEMINI_API_KEY, GEMINI_MODEL
from sheets_handler import Lead

logger = logging.getLogger(__name__)

# Configure Gemini client once at import time
genai.configure(api_key=GEMINI_API_KEY)
_model = genai.GenerativeModel(GEMINI_MODEL)

# ---------------------------------------------------------------------------
# Shared prompt components
# ---------------------------------------------------------------------------

_SYSTEM_CONSTRAINTS = """
You are writing a single personalization line for a cold outreach email.

STRICT RULES — follow all of these without exception:
- Write exactly 1–2 sentences. No more.
- Do NOT mention specific revenue, headcount, funding, or any claim you
  cannot verify — stick to observable, role-based pain.
- Do NOT use hollow flattery ("I love what you're doing at...").
- Do NOT mention the sender or their services — that comes later in the email.
- Write in second person ("As a [role] at a growing [industry] company, you...").
- The line must bridge naturally to compensation complexity as a pain point.
- Use plain, direct language. No jargon. No exclamation marks.
- Output ONLY the personalization text. No preamble, no quotes, no explanation.
"""


# ---------------------------------------------------------------------------
# Personalization for initial / FU1 / FU2
# ---------------------------------------------------------------------------

def generate_personalization(lead: Lead) -> Optional[str]:
    """
    Generates a personalization line for the initial email, FU1, and FU2.
    Stored once in the sheet and reused across those three sends.

    Returns the personalization string, or None on failure.
    """
    first_name = lead.get("first_name", "").strip()
    company = lead.get("company", "").strip()
    industry = lead.get("industry", "").strip()
    role_level = lead.get("role_level", "").strip()
    title = lead.get("title", "").strip()

    role_description = _role_description(role_level, title)

    prompt = f"""{_SYSTEM_CONSTRAINTS}

Lead context:
- Name: {first_name}
- Company: {company}
- Industry: {industry}
- Role: {role_description}

Write a 1–2 sentence personalization line for the opening of a cold email
about compensation structure complexity. The line should acknowledge the
specific challenge this type of person faces given their role and industry,
without making any claims you cannot verify.
"""

    return _call_gemini(prompt, lead)


# ---------------------------------------------------------------------------
# Personalization for nudge (regenerated fresh ~10 weeks later)
# ---------------------------------------------------------------------------

def generate_nudge_personalization(lead: Lead) -> Optional[str]:
    """
    Generates a fresh personalization line for the final nudge email.
    Called lazily when the nudge is due, not at initial send time.
    Stored in personalization_nudge column.

    The nudge is warmer and lower-pressure than earlier emails — the
    personalization should reflect that tone.

    Returns the personalization string, or None on failure.
    """
    first_name = lead.get("first_name", "").strip()
    company = lead.get("company", "").strip()
    industry = lead.get("industry", "").strip()
    role_level = lead.get("role_level", "").strip()
    title = lead.get("title", "").strip()

    role_description = _role_description(role_level, title)

    prompt = f"""{_SYSTEM_CONSTRAINTS}

Lead context:
- Name: {first_name}
- Company: {company}
- Industry: {industry}
- Role: {role_description}

This is a final nudge email sent approximately 10 weeks after the initial
outreach. The tone should be warm and low-pressure — almost a friendly
check-in rather than another pitch. Write a 1–2 sentence personalization
line that acknowledges time has passed and gently re-surfaces the
compensation structure pain point without being pushy.
"""

    return _call_gemini(prompt, lead)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _role_description(role_level: str, title: str) -> str:
    """
    Builds a descriptive role string for the Gemini prompt.
    Combines the role_level category with their actual title.
    """
    if role_level == "ceo_founder":
        base = "CEO, President, or Founder"
    elif role_level == "hr_leader":
        base = "HR or People Leader"
    else:
        base = "business leader"

    if title:
        return f"{base} (actual title: {title})"
    return base


def _call_gemini(prompt: str, lead: Lead) -> Optional[str]:
    """
    Calls Gemini and returns the response text.
    Returns None on any failure so the pipeline can handle gracefully.
    """
    try:
        response = _model.generate_content(prompt)
        text = response.text.strip()
        if not text:
            logger.warning(f"Empty Gemini response for row {lead.get('_row_number')}")
            return None
        return text
    except Exception as e:
        logger.error(f"Gemini error for row {lead.get('_row_number')}: {e}")
        return None
