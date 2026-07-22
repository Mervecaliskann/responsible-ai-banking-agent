"""PII detection & redaction, powered by Microsoft Presidio.

Used in two places, with two deliberately different policies:

  1. Before a user's question reaches the LLM (agent/banking_agent.py) —
     `redact_for_llm`. Precision-first: a false-positive redaction here can
     corrupt the very words the intent classifier depends on (e.g. Turkish
     "harcama" = spending). Only checksum/pattern-validated recognizers run:
     TCKN, IBAN, phone, and a stoplist-filtered Turkish name heuristic.

  2. Before any free-text is written to the audit log (agent/audit.py) —
     `redact_for_audit`. Recall-first: a false-positive redaction in a log
     is harmless, but a missed one is a compliance failure. This adds
     spaCy NER (PERSON) on top of the precision recognizers.

KNOWN LIMITATION: Presidio ships no Turkish NER model, and English/
multilingual spaCy models have a high false-positive rate on Turkish
text (ordinary words get tagged PERSON). That's exactly why the two
policies above are split instead of sharing one recognizer set — see the
model card for how this is documented as an open item.
"""

from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache
from typing import Optional

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import PhoneRecognizer, SpacyRecognizer
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# Every entity we might emit, mapped to its placeholder token. TR_NAME (our
# regex heuristic) and PERSON (spaCy NER, audit-log path only) share [NAME].
ENTITY_PLACEHOLDERS = {
    "PERSON": "NAME",
    "TR_NAME": "NAME",
    "TCKN": "TCKN",
    "IBAN_CODE": "IBAN",
    "PHONE_NUMBER": "PHONE",
}

# No NER involved - safe for text headed to the LLM.
_PRECISION_ENTITIES = ["TCKN", "IBAN_CODE", "PHONE_NUMBER", "TR_NAME"]
# Adds NER-based PERSON detection - audit log only, false positives are fine.
_RECALL_ENTITIES = _PRECISION_ENTITIES + ["PERSON"]


# --- checksum validators ----------------------------------------------------


def _tckn_checksum_valid(digits: str) -> bool:
    """Validate a TC Kimlik No using the official checksum algorithm."""
    if len(digits) != 11 or not digits.isdigit() or digits[0] == "0":
        return False
    d = [int(c) for c in digits]
    odd_sum = d[0] + d[2] + d[4] + d[6] + d[8]
    even_sum = d[1] + d[3] + d[5] + d[7]
    check10 = ((odd_sum * 7) - even_sum) % 10
    if check10 != d[9]:
        return False
    check11 = sum(d[:10]) % 10
    return check11 == d[10]


def _iban_checksum_valid(iban: str) -> bool:
    """Validate an IBAN using the ISO 7064 MOD97-10 checksum."""
    iban = iban.replace(" ", "").upper()
    if len(iban) < 15 or len(iban) > 34 or not iban[:2].isalpha() or not iban[2:4].isdigit():
        return False
    rearranged = iban[4:] + iban[:4]
    try:
        numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    except ValueError:
        return False
    return int(numeric) % 97 == 1


class TCKNRecognizer(PatternRecognizer):
    """Turkish national ID number (TC Kimlik No): 11 digits + checksum."""

    PATTERNS = [Pattern(name="tckn", regex=r"\b[1-9]\d{10}\b", score=0.4)]
    CONTEXT = ["tc kimlik", "tckn", "kimlik no", "t.c.", "tc no"]

    def __init__(self):
        super().__init__(
            supported_entity="TCKN",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="en",
        )

    def validate_result(self, pattern_text: str) -> Optional[bool]:
        digits = re.sub(r"\D", "", pattern_text)
        return True if _tckn_checksum_valid(digits) else False


class IBANRecognizer(PatternRecognizer):
    """IBAN, with a Turkish-shaped pattern prioritized; MOD97 checksum-validated."""

    PATTERNS = [
        Pattern(name="iban_tr", regex=r"\bTR\d{2}\s?(?:\d{4}\s?){5}\d{2}\b", score=0.5),
        Pattern(name="iban_generic", regex=r"\b[A-Z]{2}\d{2}\s?[A-Z0-9]{11,30}\b", score=0.3),
    ]
    CONTEXT = ["iban", "hesap no", "iban no"]

    def __init__(self):
        super().__init__(
            supported_entity="IBAN_CODE",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="en",
        )

    def validate_result(self, pattern_text: str) -> Optional[bool]:
        return True if _iban_checksum_valid(pattern_text) else False


# --- Turkish name heuristic (precision path) --------------------------------

_TR_UPPER = "A-ZÇĞİÖŞÜ"
_TR_LOWER = "a-zçğıöşü"

# Multi-word merchant names from data/generate_data.py's CATEGORY_MERCHANTS.
# Title-Case, so they'd otherwise look exactly like "Ad Soyad" name pairs.
_MERCHANT_PHRASES = [
    "Migros", "BİM", "A101", "ŞOK", "CarrefourSA",
    "Türk Telekom", "Vodafone", "Enerjisa", "İGDAŞ", "Turkcell",
    "Yemeksepeti", "Getir Yemek", "Burger King", "Köfteci Yusuf", "Starbucks",
    "İstanbulkart Dolum", "Shell Benzin", "BiTaksi", "Metro İstanbul", "Opet",
    "Netflix", "Spotify", "Sinema Maximum", "PlayStation Store", "Bubilet",
    "Eczane", "Acıbadem Hastanesi", "Memorial Sağlık", "Medical Park", "Optik Dünyası",
    "ATM Çekim",
]
_MERCHANT_STOPWORDS = {word.lower() for phrase in _MERCHANT_PHRASES for word in phrase.split()}

# Common capitalized Turkish sentence-starters / banking jargon that a
# "two Title Case words in a row" heuristic would otherwise misfire on.
_GENERIC_STOPWORDS = {
    "merhaba", "selam", "lutfen", "lütfen", "tesekkurler", "teşekkürler",
    "bugun", "bugün", "dun", "dün", "yarin", "yarın", "bu", "su", "şu",
    "ben", "benim", "siparis", "sipariş", "hesap", "bakiye", "kart",
    "sube", "şube", "musteri", "müşteri", "islem", "işlem", "para", "banka",
    "ocak", "subat", "şubat", "mart", "nisan", "mayis", "mayıs", "haziran",
    "temmuz", "agustos", "ağustos", "eylul", "eylül", "ekim", "kasim",
    "kasım", "aralik", "aralık",
}

_NAME_STOPWORDS = _MERCHANT_STOPWORDS | _GENERIC_STOPWORDS


class TurkishNameRecognizer(PatternRecognizer):
    """Heuristic 'Ad Soyad' (Title Case word pair) recognizer for Turkish names.

    Presidio has no Turkish NER model, and running English/multilingual
    spaCy NER on Turkish text tags ordinary words as PERSON (see module
    docstring). This trades recall for precision instead: it only fires on
    two consecutive Title Case words, filtered through a stoplist of known
    merchants and common capitalized Turkish words - safe to run on text
    headed to the LLM. It also runs on the audit-log path, on top of NER.
    """

    # Presidio's registry applies a case-insensitive flag globally, which
    # would let the character classes below match lowercase text too - the
    # (?-i:...) scope turns that off just for this pattern.
    PATTERNS = [
        Pattern(
            name="tr_name_pair",
            regex=rf"(?-i:\b[{_TR_UPPER}][{_TR_LOWER}]+[ ][{_TR_UPPER}][{_TR_LOWER}]+\b)",
            score=0.6,
        )
    ]

    def __init__(self):
        super().__init__(
            supported_entity="TR_NAME",
            patterns=self.PATTERNS,
            supported_language="en",
        )

    def validate_result(self, pattern_text: str) -> Optional[bool]:
        words = pattern_text.lower().split()
        if any(word in _NAME_STOPWORDS for word in words):
            return False
        return None  # not confirmed, not rejected - keep the pattern's own score


# --- analyzer construction ---------------------------------------------------


def _nlp_engine():
    """Small English spaCy model, used only for tokenization + optional NER.

    `en_core_web_sm` instead of Presidio's default `en_core_web_lg` to keep
    the install lightweight.
    """
    return NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    ).create_engine()


def _build_registry(nlp_engine, *, include_ner_person: bool) -> RecognizerRegistry:
    registry = RecognizerRegistry()
    registry.add_recognizer(TCKNRecognizer())
    registry.add_recognizer(IBANRecognizer())
    registry.add_recognizer(PhoneRecognizer(supported_regions=("TR", "US")))
    registry.add_recognizer(TurkishNameRecognizer())
    if include_ner_person:
        registry.add_recognizer(SpacyRecognizer(supported_entities=["PERSON"]))
    return registry


@lru_cache(maxsize=1)
def _precision_analyzer() -> AnalyzerEngine:
    nlp_engine = _nlp_engine()
    return AnalyzerEngine(nlp_engine=nlp_engine, registry=_build_registry(nlp_engine, include_ner_person=False))


@lru_cache(maxsize=1)
def _recall_analyzer() -> AnalyzerEngine:
    nlp_engine = _nlp_engine()
    return AnalyzerEngine(nlp_engine=nlp_engine, registry=_build_registry(nlp_engine, include_ner_person=True))


@lru_cache(maxsize=1)
def _anonymizer() -> AnonymizerEngine:
    return AnonymizerEngine()


def _redact(text: Optional[str], analyzer: AnalyzerEngine, entities: list[str]) -> str:
    if not text:
        return text or ""
    try:
        results = analyzer.analyze(text=text, language="en", entities=entities)
        operators = {
            entity: OperatorConfig("replace", {"new_value": f"[{ENTITY_PLACEHOLDERS[entity]}]"})
            for entity in entities
        }
        anonymized = _anonymizer().anonymize(text=text, analyzer_results=results, operators=operators)
        return anonymized.text
    except Exception:  # pragma: no cover - redaction must never crash the caller
        return "[REDACTION_ERROR]"


def redact_for_llm(text: Optional[str]) -> str:
    """Precision-first redaction for text about to reach the LLM.

    Never raises: redaction must not break a live agent request.
    """
    return _redact(text, _precision_analyzer(), _PRECISION_ENTITIES)


def redact_for_audit(text: Optional[str]) -> str:
    """Recall-first redaction for text about to be written to the audit log.

    Never raises: redaction must not break a live agent request.
    """
    return _redact(text, _recall_analyzer(), _RECALL_ENTITIES)


def pseudonymize_user_id(user_id, salt: Optional[str] = None) -> str:
    """Return a stable, salted hash of `user_id` for use in audit logs.

    The raw user_id is never written to logs; this lets records for the
    same user be correlated without exposing the identifier itself.
    """
    salt = salt if salt is not None else os.environ.get("AUDIT_HASH_SALT", "")
    digest = hashlib.sha256(f"{salt}:{user_id}".encode("utf-8")).hexdigest()
    return digest[:16]
