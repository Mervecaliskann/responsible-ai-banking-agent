"""Unit tests for agent/privacy.py (PII detection, redaction, pseudonymization).

Two redaction policies are tested separately:
  - redact_for_llm: precision-first (no NER), used on text sent to the LLM.
  - redact_for_audit: recall-first (adds spaCy NER), used on audit log text.
"""

import json
import logging

import pytest

from agent import audit, privacy


# --- checksum validators -------------------------------------------------


class TestTCKNChecksum:
    def test_valid_tckn_passes(self):
        assert privacy._tckn_checksum_valid("10000000146") is True

    def test_wrong_checksum_digits_fail(self):
        assert privacy._tckn_checksum_valid("12345678901") is False
        assert privacy._tckn_checksum_valid("11111111111") is False

    def test_leading_zero_is_invalid(self):
        assert privacy._tckn_checksum_valid("01234567890") is False

    def test_wrong_length_is_invalid(self):
        assert privacy._tckn_checksum_valid("123456789") is False
        assert privacy._tckn_checksum_valid("100000001460") is False

    def test_non_digit_is_invalid(self):
        assert privacy._tckn_checksum_valid("1000000014a") is False


class TestIBANChecksum:
    def test_valid_turkish_iban_passes(self):
        assert privacy._iban_checksum_valid("TR330006100519786457841326") is True

    def test_off_by_one_checksum_fails(self):
        assert privacy._iban_checksum_valid("TR330006100519786457841327") is False

    def test_too_short_is_invalid(self):
        assert privacy._iban_checksum_valid("TR33") is False

    def test_lowercase_and_spaces_are_normalized(self):
        assert privacy._iban_checksum_valid("tr33 0006 1005 1978 6457 8413 26") is True


# --- redact_for_llm() : precision-first, no NER -----------------------------


class TestRedactForLLM:
    def test_empty_and_none_pass_through(self):
        assert privacy.redact_for_llm(None) == ""
        assert privacy.redact_for_llm("") == ""

    def test_ordinary_turkish_question_is_unchanged(self):
        text = "Bu ay en cok neye harcama yaptim?"
        assert privacy.redact_for_llm(text) == text

    def test_valid_tckn_is_replaced(self):
        result = privacy.redact_for_llm("TC kimlik numaram 10000000146.")
        assert "[TCKN]" in result
        assert "10000000146" not in result

    def test_invalid_tckn_shaped_number_is_not_tagged_as_tckn(self):
        # Fails the checksum, so it must not be classified TCKN (it may
        # still match the built-in phone heuristic - that's out of scope
        # for the TCKN recognizer's own precision guarantee).
        result = privacy.redact_for_llm("Siparis numaram 12345678901.")
        assert "[TCKN]" not in result

    def test_iban_is_replaced(self):
        result = privacy.redact_for_llm("Gonderim hesabi: TR330006100519786457841326")
        assert "[IBAN]" in result
        assert "TR330006100519786457841326" not in result

    def test_phone_number_is_replaced(self):
        result = privacy.redact_for_llm("Beni 0532 123 45 67 numarasindan arayin.")
        assert "[PHONE]" in result
        assert "0532 123 45 67" not in result

    def test_turkish_name_pair_is_replaced(self):
        result = privacy.redact_for_llm("Merhaba, ben Mehmet Yilmaz. Bakiyem ne kadar?")
        assert "[NAME]" in result
        assert "Mehmet Yilmaz" not in result
        # The rest of the sentence, including banking vocabulary, survives.
        assert "Bakiyem ne kadar?" in result

    def test_merchant_names_are_not_flagged_as_names(self):
        text = "Bu ay Migros ve Netflix icin cok harcadim, Turk Telekom faturami odedim."
        assert privacy.redact_for_llm(text) == text

    def test_multiple_entities_in_one_message(self):
        text = "TC kimlik numaram 10000000146, IBAN TR330006100519786457841326."
        result = privacy.redact_for_llm(text)
        assert "[TCKN]" in result
        assert "[IBAN]" in result
        assert "10000000146" not in result
        assert "TR330006100519786457841326" not in result

    def test_redact_never_raises_on_weird_input(self):
        privacy.redact_for_llm("\x00\x01" * 100)
        privacy.redact_for_llm("a" * 5000)


# --- redact_for_audit() : recall-first, adds NER ----------------------------


class TestRedactForAudit:
    def test_empty_and_none_pass_through(self):
        assert privacy.redact_for_audit(None) == ""
        assert privacy.redact_for_audit("") == ""

    def test_still_catches_everything_the_precision_policy_catches(self):
        text = "TC kimlik numaram 10000000146, IBAN TR330006100519786457841326."
        result = privacy.redact_for_audit(text)
        assert "[TCKN]" in result
        assert "[IBAN]" in result

    def test_is_strictly_more_aggressive_than_llm_policy(self):
        # A sentence the precision policy leaves untouched (no checksum-valid
        # ID, no merchant/name pair) can still get NER-flagged by the recall
        # policy - that's the whole point of splitting the two policies.
        text = "Bu ay en cok neye harcama yaptim?"
        assert privacy.redact_for_llm(text) == text
        assert privacy.redact_for_audit(text) != text


# --- pseudonymize_user_id() -------------------------------------------------


class TestPseudonymizeUserId:
    def test_stable_for_same_input(self):
        assert privacy.pseudonymize_user_id(42, salt="s1") == privacy.pseudonymize_user_id(42, salt="s1")

    def test_different_users_get_different_hashes(self):
        assert privacy.pseudonymize_user_id(42, salt="s1") != privacy.pseudonymize_user_id(43, salt="s1")

    def test_different_salt_changes_hash(self):
        assert privacy.pseudonymize_user_id(42, salt="s1") != privacy.pseudonymize_user_id(42, salt="s2")

    def test_raw_user_id_not_present_in_output(self):
        digest = privacy.pseudonymize_user_id(42, salt="s1")
        assert "42" not in digest

    def test_uses_env_salt_when_not_passed(self, monkeypatch):
        monkeypatch.setenv("AUDIT_HASH_SALT", "env-salt")
        with_env = privacy.pseudonymize_user_id(7)
        explicit = privacy.pseudonymize_user_id(7, salt="env-salt")
        assert with_env == explicit


# --- wiring into audit logging ---------------------------------------------


@pytest.fixture
def audit_log_file(tmp_path, monkeypatch):
    """Redirect audit logging to a throwaway file for this test only."""
    log_file = tmp_path / "audit.log"
    monkeypatch.setattr(audit, "LOG_DIR", tmp_path)
    monkeypatch.setattr(audit, "LOG_FILE", log_file)
    monkeypatch.setattr(audit, "_logger", None)

    logger = logging.getLogger("banking_agent.audit")
    logger.handlers.clear()

    yield log_file

    logger.handlers.clear()


class TestAuditWiring:
    def test_user_id_is_pseudonymized_not_raw(self, audit_log_file):
        audit.log_request(user_id=12345, intent="balance")
        record = json.loads(audit_log_file.read_text(encoding="utf-8").strip())
        assert record["user_id"] != 12345
        assert record["user_id"] == privacy.pseudonymize_user_id(12345)

    def test_log_request_redacts_via_the_audit_policy(self, audit_log_file, monkeypatch):
        """log_request must call redact_for_audit (recall-first), not redact_for_llm."""
        calls = []

        def fake_redact_for_audit(text):
            calls.append(text)
            return f"REDACTED::{text}"

        monkeypatch.setattr(privacy, "redact_for_audit", fake_redact_for_audit)

        audit.log_request(user_id=1, intent="general", question="q-text", response="r-text")

        assert calls == ["q-text", "r-text"]
        record = json.loads(audit_log_file.read_text(encoding="utf-8").strip())
        assert record["question"] == "REDACTED::q-text"
        assert record["response"] == "REDACTED::r-text"

    def test_pii_does_not_reach_disk_unredacted(self, audit_log_file):
        audit.log_request(
            user_id=1,
            intent="general",
            question="Benim TC kimlik numaram 10000000146.",
            response="IBAN'iniz TR330006100519786457841326 olarak kaydedildi.",
        )
        raw_contents = audit_log_file.read_text(encoding="utf-8")
        assert "10000000146" not in raw_contents
        assert "TR330006100519786457841326" not in raw_contents

        record = json.loads(raw_contents.strip())
        assert "[TCKN]" in record["question"]
        assert "[IBAN]" in record["response"]

    def test_question_and_response_omitted_when_not_provided(self, audit_log_file):
        audit.log_request(user_id=1, intent="balance")
        record = json.loads(audit_log_file.read_text(encoding="utf-8").strip())
        assert "question" not in record
        assert "response" not in record
