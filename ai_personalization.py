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
You are writing a single personalization line for a cold outreach email from a compensation consultant.

STRICT RULES — follow all of these without exception:
- Write 1 sentence. 2 sentences only if the second meaningfully adds to the first — never pad.
- Write in second person ("As a [role]..." or "When you're..." or "At a company like...")
- Name a specific, felt pain — something the reader would recognize from their own week
- Do NOT use vague phrases like "manual overhead", "data behind them", "compensation complexity", "navigate", "leverage", or "optimize"
- Do NOT mention the sender or their services — that comes later in the email
- Do NOT make claims about the specific company you cannot verify
- Do NOT say "HR company" — the industry field is the industry the company operates IN, not what the company does
- The pain must connect naturally to compensation structure (pay decisions, benchmarking, retention, raises, incentives)
- Output ONLY the personalization text. No preamble, no quotes, no explanation.

Examples of GOOD personalization lines:
- "When you're a founder making 30 hiring decisions a year, it's easy to end up with pay that made sense case-by-case but looks inconsistent the moment someone compares notes."
- "At a growing manufacturing company, compensation decisions often fall to whoever has the most context — which means raises get decided in hallways and bonus structures get rebuilt every cycle."
- "As an HR leader at a fast-growing firm, you're probably the person explaining pay decisions you didn't fully design — and defending numbers you're not totally confident in."

Examples of BAD personalization lines (never write like these):
- "You are likely navigating the friction of keeping pay models accurate while managing the data behind them." (vague, jargon-heavy)
- "I love what [company] is doing in the [industry] space." (hollow flattery)
- "As a leader at [company], you understand the importance of compensation." (says nothing)
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
    - Company: {company}
    - Industry: {industry}
    - Role: {role_description}

    Write a 1–2 sentence personalization line for the opening of a cold email
    about compensation structure. Name the specific pain this type of person
    feels in their role and industry — something they would recognize from
    their own week.
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
    - Company: {company}
    - Industry: {industry}
    - Role: {role_description}

    This is a final nudge sent approximately 10 weeks after the initial outreach.
    The tone should be warm and low-pressure — a friendly check-in, not another pitch.
    Write a 1–2 sentence personalization line that gently re-surfaces the
    compensation pain point while acknowledging that time has passed.

    Examples of GOOD nudge personalization:
    - "A lot can change in a few months — new hires, a promotion cycle, maybe a conversation that made pay structure feel more urgent than it did before."
    - "I know compensation structure isn't always top of mind until it suddenly is — a retention problem, a tough promotion conversation, or a new hire who asked hard questions."
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
