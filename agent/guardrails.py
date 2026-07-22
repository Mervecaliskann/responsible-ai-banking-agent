"""Input/output guardrails for the banking agent.

Input guardrails run on the user's raw message before it reaches the LLM
at all (ahead of PII redaction and before any tool/LLM call is made) and
block:
  - prompt injection / instruction-override attempts
  - system-prompt / instruction extraction attempts
  - customer_id manipulation (asking for another customer's data)
  - SQL-injection-shaped input aimed at the tools layer

The tools layer (tools/query_tools.py) already uses parameterized
queries, so SQL injection isn't actually exploitable there today - this
check is defense-in-depth / anomaly detection, not a patch for a real
vulnerability.

Output guardrails run on the LLM's generated response before it reaches
the user and block:
  - financial/investment advice (buy/sell/invest recommendations)
  - cross-customer data leakage (response references a different
    customer_id than the current session)
  - raw PII (TCKN/IBAN/phone) that survived redaction

Every block decision - either direction - is logged to the audit log via
agent.audit.log_guardrail_block. Both check_input and check_output fail
closed: an unexpected internal error is treated as a block, not a
silent pass-through, since that's the safer failure mode for a
compliance control.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from . import privacy


@dataclass
class GuardrailResult:
    allowed: bool
    rule: Optional[str] = None
    reason: Optional[str] = None
    matched_text: Optional[str] = None

    @property
    def blocked(self) -> bool:
        return not self.allowed


def _allowed() -> GuardrailResult:
    return GuardrailResult(allowed=True)


def _blocked(rule: str, reason: str, matched_text: str) -> GuardrailResult:
    return GuardrailResult(allowed=False, rule=rule, reason=reason, matched_text=matched_text)


# Turkish text shows up both with proper diacritics and, very commonly,
# typed plain-ASCII on non-Turkish keyboards ("musteri" vs "müşteri"). All
# guardrail regexes below are written in normalized (ASCII, lowercase)
# form and matched against normalized text, rather than doubling every
# pattern for both spellings.
_TR_TRANSLATION = str.maketrans(
    {
        "ç": "c", "Ç": "c",
        "ğ": "g", "Ğ": "g",
        "ı": "i", "I": "i", "İ": "i",
        "ö": "o", "Ö": "o",
        "ş": "s", "Ş": "s",
        "ü": "u", "Ü": "u",
    }
)


def _normalize(text: str) -> str:
    return text.translate(_TR_TRANSLATION).lower()


def _first_match(patterns: list[str], text: str) -> tuple[Optional[str], Optional[str]]:
    normalized = _normalize(text)
    for pattern in patterns:
        m = re.search(pattern, normalized)
        if m:
            return pattern, m.group(0)
    return None, None


# --- input guardrails --------------------------------------------------

_INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above) instructions",
    r"disregard (all |any )?(the )?(previous|prior|above)",
    r"forget (all |any )?(the )?(previous|prior|above) instructions",
    r"you are now (a|an) ",
    r"act as (a|an) (unrestricted|unfiltered|jailbroken)",
    r"pretend (that )?you are",
    r"new instructions?\s*:",
    r"jailbreak",
    r"dan mode",
    r"onceki (talimatlarini|talimatlari|kurallari) (unut|yok say|yoksay|gormezden gel)",
    r"yukaridaki (talimatlari|kurallari) (unut|yok say|yoksay)",
    r"artik (sen|siz) .*(kisitlamasiz|sinirsiz)",
    r"kurallari (unut|yok say|yoksay|gormezden gel)",
]

_PROMPT_EXTRACTION_PATTERNS = [
    r"what is your system prompt",
    r"show me your (instructions|prompt|rules)",
    r"repeat (the )?(instructions|prompt) above",
    r"print your (instructions|prompt)",
    r"reveal your (prompt|instructions)",
    r"system prompt",
    r"sistem\s*prompt",
    r"talimatlarini (goster|yaz|paylas)",
    r"promptunu (goster|yaz|paylas)",
]

_SQLI_PATTERNS = [
    r";\s*drop\s+table",
    r";\s*delete\s+from",
    r";\s*update\s+\w+\s+set",
    r"union\s+select",
    r"'\s*or\s*'?1'?\s*=\s*'?1",
    r"\bor\s+1\s*=\s*1\b",
    r"'\s*--",
    r"xp_cmdshell",
]

_CUSTOMER_ID_PATTERN = re.compile(r"(?:customer_id|musteri\s*(?:no|numarasi|id)?)\s*[:=#]?\s*(\d+)")
_OTHER_CUSTOMER_PHRASES = [
    r"baska (bir )?musteri",
    r"diger musteri",
    r"other customer",
    r"someone else'?s account",
    r"baskasinin hesab",
]


def check_prompt_injection(text: str) -> GuardrailResult:
    pattern, matched = _first_match(_INJECTION_PATTERNS, text)
    if pattern:
        return _blocked("prompt_injection", "Instruction-override attempt detected", matched)
    return _allowed()


def check_prompt_extraction(text: str) -> GuardrailResult:
    pattern, matched = _first_match(_PROMPT_EXTRACTION_PATTERNS, text)
    if pattern:
        return _blocked("prompt_extraction", "System prompt / instruction extraction attempt detected", matched)
    return _allowed()


def check_sql_injection(text: str) -> GuardrailResult:
    pattern, matched = _first_match(_SQLI_PATTERNS, text)
    if pattern:
        return _blocked("sql_injection", "SQL-injection-shaped input detected", matched)
    return _allowed()


def check_customer_id_manipulation(text: str, expected_customer_id: int) -> GuardrailResult:
    for m in _CUSTOMER_ID_PATTERN.finditer(_normalize(text)):
        mentioned_id = int(m.group(1))
        if mentioned_id != int(expected_customer_id):
            return _blocked(
                "customer_id_manipulation",
                f"Message references customer_id={mentioned_id}, session is customer_id={expected_customer_id}",
                m.group(0),
            )
    pattern, matched = _first_match(_OTHER_CUSTOMER_PHRASES, text)
    if pattern:
        return _blocked("customer_id_manipulation", "Message asks for another customer's data", matched)
    return _allowed()


def check_input(text: str, customer_id: int) -> GuardrailResult:
    """Run all input guardrails; return the first block, or allowed()."""
    try:
        for check in (check_prompt_injection, check_prompt_extraction, check_sql_injection):
            result = check(text)
            if result.blocked:
                return result
        return check_customer_id_manipulation(text, customer_id)
    except Exception as exc:  # pragma: no cover - fail closed, never crash the caller
        return _blocked("guardrail_error", f"Input guardrail raised: {exc}", text[:200])


# --- output guardrails ---------------------------------------------------

_FINANCIAL_ADVICE_PATTERNS = [
    r"\b(you should|i recommend|i suggest|i'd recommend) (buy|sell|invest)",
    r"\bbuy (stock|shares|bitcoin|crypto|gold)\b",
    r"\bsell (your |the )?(stock|shares|bitcoin|crypto|gold)\b",
    r"\binvest (in|into)\b",
    r"(hisse|kripto|bitcoin|altin|doviz|fon).{0,20}(alin\b|almaniz|satmaniz)",
    r"yatirim yap(in|maniz)?\b",
    r"yatirim tavsiyesi",
    r"(hisse senedi|kripto para)\s*tavsiye",
    r"(almanizi|satmanizi)\s*(oneririm|tavsiye ederim)",
]


def check_financial_advice(text: str) -> GuardrailResult:
    pattern, matched = _first_match(_FINANCIAL_ADVICE_PATTERNS, text)
    if pattern:
        return _blocked("financial_advice", "Response contains investment/financial advice", matched)
    return _allowed()


def check_cross_customer_leak(text: str, expected_customer_id: int) -> GuardrailResult:
    for m in _CUSTOMER_ID_PATTERN.finditer(_normalize(text)):
        mentioned_id = int(m.group(1))
        if mentioned_id != int(expected_customer_id):
            return _blocked(
                "cross_customer_leak",
                f"Response references customer_id={mentioned_id}, session is customer_id={expected_customer_id}",
                m.group(0),
            )
    return _allowed()


def check_pii_leak(text: str) -> GuardrailResult:
    """Detect raw TCKN/IBAN/phone in the response using the checksum-validated,
    precision recognizers only (agent/privacy.py) - low false-positive rate,
    so this is safe to use as a hard block rather than just a redaction."""
    results = privacy._precision_analyzer().analyze(
        text=text, language="en", entities=["TCKN", "IBAN_CODE", "PHONE_NUMBER"]
    )
    if results:
        r = results[0]
        return _blocked("pii_leak", f"Raw {r.entity_type} detected in response", text[r.start : r.end])
    return _allowed()


def check_output(text: str, customer_id: int) -> GuardrailResult:
    """Run all output guardrails; return the first block, or allowed()."""
    try:
        result = check_financial_advice(text)
        if result.blocked:
            return result
        result = check_cross_customer_leak(text, customer_id)
        if result.blocked:
            return result
        return check_pii_leak(text)
    except Exception as exc:  # pragma: no cover - fail closed, never crash the caller
        return _blocked("guardrail_error", f"Output guardrail raised: {exc}", text[:200])
