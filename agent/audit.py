"""Structured audit logging for the banking agent.

Emits one JSON object per line ("JSON Lines") to logs/audit.log so every agent
run can be parsed and analyzed downstream. Each record captures:

    timestamp, request_id, user_id, intent, tools_called, latency_ms, token_usage

user_id is pseudonymized (salted hash) rather than written raw, and any
free-text fields (question, response) are passed through PII redaction
before being written — see agent/privacy.py. This is defense-in-depth:
callers should already be passing redacted text, but nothing reaches disk
unredacted regardless.

Redaction here uses privacy.redact_for_audit, the recall-first policy
(includes spaCy NER on top of the precision recognizers) — a false
positive in a log is harmless, a missed one is a compliance failure. This
is deliberately more aggressive than the redaction applied to text sent
to the LLM (privacy.redact_for_llm).
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import privacy

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_FILE = LOG_DIR / "audit.log"

_logger: Optional[logging.Logger] = None


def _get_logger() -> logging.Logger:
    """Lazily build a dedicated file logger that writes bare JSON lines."""
    global _logger
    if _logger is not None:
        return _logger

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("banking_agent.audit")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # keep audit records out of the root/app logs

    if not logger.handlers:
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        # The record itself is already JSON, so emit it verbatim.
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

    _logger = logger
    return logger


def new_request_id() -> str:
    """Return a fresh uuid4 request id as a string."""
    return str(uuid.uuid4())


def log_request(
    *,
    user_id,
    intent: Optional[str],
    tools_called: Optional[list[str]] = None,
    latency_ms: float = 0.0,
    token_usage: Optional[dict] = None,
    request_id: Optional[str] = None,
    question: Optional[str] = None,
    response: Optional[str] = None,
) -> str:
    """Write one structured audit record for an agent request.

    Returns the request_id used, so callers can correlate other logs/metrics.
    Never raises: audit logging must not break a live agent request.
    """
    request_id = request_id or new_request_id()
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "user_id": privacy.pseudonymize_user_id(user_id),
        "intent": intent,
        "tools_called": tools_called or [],
        "latency_ms": round(float(latency_ms), 2),
        "token_usage": token_usage or {},
    }
    if question is not None:
        record["question"] = privacy.redact_for_audit(question)
    if response is not None:
        record["response"] = privacy.redact_for_audit(response)
    try:
        _get_logger().info(json.dumps(record, ensure_ascii=False))
    except Exception:  # pragma: no cover - never let auditing break a request
        logging.getLogger(__name__).exception("failed to write audit record")
    return request_id


def log_guardrail_block(
    *,
    user_id,
    direction: str,
    rule: Optional[str],
    reason: Optional[str],
    matched_text: Optional[str] = None,
    intent: Optional[str] = None,
    request_id: Optional[str] = None,
) -> str:
    """Write one audit record for a blocked guardrail decision.

    `direction` is "input" (blocked before reaching the LLM) or "output"
    (blocked before reaching the user) - see agent/guardrails.py.
    matched_text is the short offending excerpt, not the full message; it
    is still passed through redact_for_audit since it may itself contain
    PII (e.g. a leaked TCKN). Never raises: audit logging must not break
    a live agent request.
    """
    request_id = request_id or new_request_id()
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "event": "guardrail_block",
        "direction": direction,
        "user_id": privacy.pseudonymize_user_id(user_id),
        "rule": rule,
        "reason": reason,
        "intent": intent,
    }
    if matched_text is not None:
        record["matched_text"] = privacy.redact_for_audit(matched_text)
    try:
        _get_logger().info(json.dumps(record, ensure_ascii=False))
    except Exception:  # pragma: no cover - never let auditing break a request
        logging.getLogger(__name__).exception("failed to write guardrail audit record")
    return request_id
