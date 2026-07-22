# Model Card: Responsible AI Banking Agent

This card follows the [Hugging Face model card](https://huggingface.co/docs/hub/model-cards) structure, adapted for an
**agentic system** built around an unmodified third-party LLM rather than a fine-tuned model. Where a section doesn't
apply cleanly to that distinction, this is noted explicitly.

- **Repository:** [responsible-ai-banking-agent](https://github.com/Mervecaliskann/responsible-ai-banking-agent)
- **Card version:** 1.0, generated against commit `449b2f8` (2026-07-22)
- **Card author / contact:** Merve Çalışkan ([mervcaliskaan@gmail.com](mailto:mervcaliskaan@gmail.com))

---

## Model Details

### Description

The Responsible AI Banking Agent is a conversational assistant that answers natural-language banking questions
("what's my balance?", "what did I spend the most on this month?", "is there unusual spending?") by classifying the
user's intent, running a corresponding query against a SQLite database, and generating a short natural-language reply
from the query result. It is built as a [LangGraph](https://www.langchain.com/langgraph) state machine, not a
fine-tuned model — the underlying LLM is used unmodified.

### System type

Agentic system (state machine + tool calls + governance layer) wrapping a third-party hosted LLM. There is no
fine-tuning, adapter, or weight modification anywhere in this repository — "the model" in the base-LLM sense is
entirely external (see below).

### Base model

- **Model:** `llama-3.3-70b-versatile`
- **Served via:** [Groq](https://groq.com/) API (`langchain-groq`), unmodified, `temperature=0`
- **Fine-tuned from:** N/A — used as-is via API; behavior changes if Groq updates or deprecates this model are outside
  this repository's control (see [Maintenance](#maintenance))

### Architecture

```
parse_intent → [conditional routing on intent] → query_data / summarize / alert → respond
```

- `parse_intent`: the LLM classifies the (redacted) user message into one of `balance`, `transactions`, `spending`,
  `anomaly`, `general`, plus `period`/`category`/`limit` fields, returned as JSON.
- `query_data` / `summarize` / `alert`: call one of five LangChain tools (`get_account_balance`, `get_transactions`,
  `categorize_spending`, `get_monthly_summary`, `detect_anomaly`) against `data/banking.db` using **parameterized**
  SQL — `customer_id` is always taken from the trusted caller/session, never parsed from LLM output or free text.
- `respond`: the LLM turns the tool's JSON result into a short natural-language reply.

Full detail: [`agent/banking_agent.py`](../agent/banking_agent.py).

### Training data

None — no training or fine-tuning occurs in this repository. The base LLM's training data/process is Meta's /
Groq's, not documented here.

### Application data

Entirely synthetic. [`data/generate_data.py`](../data/generate_data.py) generates 10 customers and ~550 transactions
(≈55/customer, spread over the last 3 months) via `Faker('tr_TR')`, across 7 categories (`market`, `fatura`,
`restoran`, `ulaşım`, `eğlence`, `sağlık`, `ATM`), with a deliberately injected anomaly scenario (80–150% spending
increase in one category for some customers) so the anomaly-detection path has something real to detect. **No real
customer or banking data is used anywhere in this system.**

### Language

Turkish (primary conversational language, both prompts and expected user input); English is understood and
guardrails/redaction cover both. Internal code comments and identifiers are a mix of Turkish and English.

### License

Not currently specified in this repository. Should be added before any external distribution or use beyond this
demo/reference context.

---

## Uses

### Direct use (intended)

- Reference / demo implementation of an intent-routed banking Q&A agent, showing how PII redaction, guardrails, and
  audit logging can be layered around a tool-using LLM agent.
- Internal experimentation, training material, or a starting point for a production build — **after** the gaps
  listed in [Known Limitations](#known-limitations) are addressed.

### Out-of-scope use

- **Production deployment against real customer data or real banking infrastructure.** This system has never been
  connected to, or evaluated against, real accounts, real transaction data, or a production core banking system.
- **Financial or investment advice.** The agent is explicitly designed *not* to recommend what to buy, sell, or
  invest in (enforced by the `financial_advice` output guardrail — see below), and must not be repurposed to do so.
- **Any use where an incorrect balance, transaction summary, or anomaly determination could cause financial harm**
  without a human in the loop. The system has no automated fact-checking of the numbers the LLM restates (see
  Known Limitations).
- **Multi-tenant or multi-customer sessions without re-validation** of the customer-scoping assumptions described in
  [Risks and Mitigations](#risks-and-mitigations).

---

## Governance Controls

### Structured audit logging — [`agent/audit.py`](../agent/audit.py)

Every request and every guardrail block is written as one JSON line to `logs/audit.log`:

- Request records: `timestamp`, `request_id`, `user_id` (pseudonymized), `intent`, `tools_called`, `latency_ms`,
  `token_usage`, and redacted `question`/`response`.
- Guardrail-block records (`"event": "guardrail_block"`): `direction` (`input`/`output`), `rule`, `reason`, and a
  redacted `matched_text` excerpt.
- `user_id` is **never stored raw** — it's pseudonymized via `SHA256(salt:user_id)[:16]`, with the salt read from
  the `AUDIT_HASH_SALT` environment variable.

### PII redaction — [`agent/privacy.py`](../agent/privacy.py) (Microsoft Presidio)

Two deliberately different policies, chosen per call site's risk profile:

| Policy | Used on | NER? | Entities | Rationale |
|---|---|---|---|---|
| `redact_for_llm` | User input, before it reaches the LLM | No | `TCKN`, `IBAN_CODE`, `PHONE_NUMBER`, `TR_NAME` (regex heuristic) | Precision-first: a false-positive redaction here can corrupt words the intent classifier depends on (e.g. Turkish *harcama* = "spending") |
| `redact_for_audit` | Question/response text, before it's written to the audit log | Yes (spaCy `en_core_web_sm`, `PERSON`) | All of the above + `PERSON` | Recall-first: a false positive in a log is harmless, a missed one is a compliance failure |

- `TCKN` (Turkish national ID) and `IBAN` recognizers are **custom, checksum-validated** — the official 11-digit
  Turkish ID checksum algorithm and ISO 7064 MOD97-10 respectively — not just shape-matching, so they have a very
  low false-positive rate.
- `TR_NAME` is a regex heuristic (two consecutive Title-Case words), filtered through a stoplist of known merchants
  (Migros, Netflix, Shell, Türk Telekom, etc.) and common capitalized Turkish sentence-starters, because **no
  Turkish NER model is available** — see [Known Limitations](#known-limitations).
- Detected entities are replaced with typed placeholders (`[NAME]`, `[TCKN]`, `[IBAN]`, `[PHONE]`), not dropped, so
  redacted text stays legible.

### Guardrails — [`agent/guardrails.py`](../agent/guardrails.py)

| Direction | Rule | Blocks |
|---|---|---|
| Input (pre-LLM) | `prompt_injection` | Instruction-override attempts ("ignore previous instructions", "önceki talimatları unut") |
| Input | `prompt_extraction` | System-prompt / instruction extraction attempts |
| Input | `customer_id_manipulation` | Messages referencing a different `customer_id`, or asking for another customer's data |
| Input | `sql_injection` | SQL-injection-shaped input aimed at the tools layer |
| Output (pre-user) | `financial_advice` | Buy/sell/invest recommendations |
| Output | `cross_customer_leak` | Response referencing a `customer_id` other than the session's |
| Output | `pii_leak` | Raw TCKN/IBAN/phone number that survived redaction (checked with the same checksum-validated recognizers used above) |

Both directions return a structured `GuardrailResult` (`allowed`, `rule`, `reason`, `matched_text`), fail **closed**
(block) on any internal error, and Turkish patterns match diacritic-insensitively (`musteri` and `müşteri` are
treated the same), since users frequently type without ı/ş/ğ/ü/ö/ç on non-Turkish keyboards. Every block is logged
via `audit.log_guardrail_block`. A blocked input never reaches the LLM at all (no tool call, no API cost); a blocked
output is replaced with a fixed message before being logged or returned.

---

## Evaluation

No formal held-out benchmark or human evaluation has been run. What exists is the automated test suite:

| Suite | Tests | What it covers |
|---|---|---|
| [`tests/test_privacy.py`](../tests/test_privacy.py) | 31 | TCKN checksum validator (valid/invalid/malformed inputs), IBAN MOD97 checksum validator, `redact_for_llm` precision behavior (ordinary Turkish sentences left untouched, merchant names not flagged, TCKN/IBAN/phone/name redaction), `redact_for_audit` recall behavior (demonstrates it catches strictly more than `redact_for_llm` on the same input), `pseudonymize_user_id` stability/uniqueness, and audit-log wiring (pseudonymization and redaction actually applied before anything hits disk) |
| [`tests/test_guardrails.py`](../tests/test_guardrails.py) | 18 | A curated adversarial-prompt report (see below), individual rule-coverage tests for all 7 rules, and audit-wiring tests for `log_guardrail_block` |
| **Total** | **49** | All passing as of commit `449b2f8` |

### Adversarial prompt report

25 adversarial prompts were authored to cover all 7 guardrail rules, mixing English and Turkish, with and without
Turkish diacritics (e.g. both `Önceki talimatları unut` and `Onceki talimatlari unut`):

- **25/25 blocked (100%)**, each attributed to its expected rule.
- A separate control set of 7 benign prompts (ordinary balance/spending/transaction questions and plausible agent
  responses, including ones that mention the customer's own name or common merchants) produced **0/7 false
  positives**.

This is a regression check, not a security certification — see the next section.

### PII recognizer coverage

- `TCKN`: checksum-validated against the real algorithm; unit-tested against a known-valid ID, wrong-checksum
  digits, wrong length, leading zero, and non-digit input.
- `IBAN_CODE`: MOD97-validated; unit-tested against a valid Turkish IBAN, an off-by-one (invalid) checksum, too-short
  input, and lowercase/spaced input.
- `PHONE_NUMBER`: Presidio's built-in recognizer, scoped to `TR`/`US` regions.
- `TR_NAME` / `PERSON`: see [Known Limitations](#known-limitations) — this is the weakest-precision/weakest-recall
  part of the system by a wide margin, and is explicitly *not* checksum-validated (names have no checksum).

---

## Known Limitations

Stated explicitly and without softening, since this is the section most likely to be skipped over:

1. **No reliable Turkish NER model exists.** Both `en_core_web_sm` (English) and `xx_ent_wiki_sm` (multilingual)
   spaCy models were evaluated during development and both produced a high false-positive rate on Turkish text —
   ordinary words like *harcama* ("spending") were tagged as `PERSON`. Because of this, the LLM-input redaction path
   (`redact_for_llm`) uses a regex heuristic (Title-Case word pairs) instead of NER, which is **lower recall**: it
   will miss single first names, unusual capitalization, or names not in "Ad Soyad" form. The audit-log path
   (`redact_for_audit`) does use NER for higher recall, since a false positive there is harmless — but this means
   the two paths have a real, permanent detection gap between them by design, not by oversight.
2. **Guardrails are pattern/regex-based, not model-based — defense-in-depth, not a hardened jailbreak defense.** A
   determined adversary can rephrase around the curated pattern list: novel injection phrasings, paraphrasing,
   translation into a third language, encoding tricks (base64, leetspeak, unicode homoglyphs), or splitting an
   attack across multiple turns are all plausible bypasses that were not tested.
3. **Synthetic data only.** All customer and transaction data comes from `Faker('tr_TR')` with a fixed seed. The
   system has never been evaluated against real banking data volume, real transaction patterns, or real names/IDs,
   which may behave differently against the regex heuristics above (e.g. real Turkish names with more varied
   capitalization or diacritic use than the synthetic set).
4. **No adversarial red-teaming beyond the test suite described above.** The 25 adversarial prompts were authored by
   the same people who built the guardrails, not an independent red team, so they reflect *known* attack patterns
   rather than exhaustive or adversarially-discovered coverage.
5. **LLM outputs are non-deterministic**, even at `temperature=0` — this reduces but does not eliminate variation
   from Groq's inference stack or model updates. `parse_intent`'s JSON parsing falls back to intent `"general"` if
   the LLM's output isn't parseable JSON, but there is no retry or stricter output validation beyond that.
6. **Recognizer overlap isn't perfectly clean.** An 11-digit number that fails the TCKN checksum can still be
   matched by Presidio's built-in phone recognizer (observed during testing). This doesn't cause a functional
   failure, but shows the entity types aren't fully disjoint.
7. **`customer_id_manipulation` is a detection layer, not the only access control.** In the current architecture,
   the `customer_id` used for every tool call always comes from the trusted caller/session, never from parsed LLM
   output or free text — so this guardrail is defense-in-depth today. If the architecture ever changes to derive
   `customer_id` from LLM output or user text, this guardrail becomes load-bearing and must be re-audited before
   that change ships.
8. **No rate limiting or abuse throttling.** Repeated adversarial attempts from the same user are logged individually
   but not rate-limited, throttled, or auto-escalated at the session or account level.
9. **Guardrail/redaction latency is not benchmarked.** Every request now runs Presidio analysis (and, on the audit
   path, spaCy NER) in addition to the LLM calls; the added latency has not been measured or documented.
10. **No automated fact-checking of the LLM's numbers.** The output guardrails check for advice, cross-customer
    leakage, and raw PII — they do **not** verify that a number the LLM restates in its natural-language reply
    actually matches the source `query_result` JSON. The response prompt instructs the LLM to answer "based on the
    given data," but that is a prompting convention, not a technical control.

---

## Risks and Mitigations

| Risk | Mitigation | Residual risk |
|---|---|---|
| **Cross-customer data access** — a user sees another customer's balance/transactions | `customer_id` for every tool call comes from the trusted session, never from LLM output or free text; `customer_id_manipulation` input guardrail and `cross_customer_leak` output guardrail add detection on top | If the trusted-session assumption is ever violated (e.g. a future change routes `customer_id` through the LLM), the guardrails are pattern-based and could be evaded — see Limitation 7 |
| **Prompt injection / jailbreak** | `prompt_injection` / `prompt_extraction` input guardrails block known phrasings before the LLM is even called | Pattern-based; bypassable by rephrasing, translation, encoding, or multi-turn attacks (Limitation 2) |
| **PII leakage into logs** | `redact_for_audit` (recall-first, NER-backed) applied to all logged text; `user_id` pseudonymized with a salted hash, never stored raw | NER-based name detection still has a nonzero false-negative rate; non-PII sensitive text (e.g. cross-customer IDs) is redacted differently, via guardrails, not `privacy.py` |
| **PII leakage into the LLM's context** | `redact_for_llm` (precision-first) applied to every user message before it reaches the LLM | Precision-first by design trades away some recall (Limitation 1); the LLM's own generated response is not redacted before being checked by `pii_leak`, only checked against it |
| **Unauthorized financial/investment advice** | `financial_advice` output guardrail blocks buy/sell/invest language; the response prompt doesn't ask for advice in the first place | Pattern-based, may miss novel advice phrasing not covered by the current pattern list |
| **Hallucinated financial figures** | Response prompt instructs the LLM to answer only from the provided `query_result` data | **No technical control** — this is prompt design only; not verified automatically (Limitation 10) |
| **SQL injection against the tools layer** | `tools/query_tools.py` already uses parameterized queries (not exploitable today, independent of guardrails); `sql_injection` input guardrail adds detection/logging on top | None known today, but this should be re-verified if the tools layer is ever refactored to build queries dynamically |

---

## Technical Specifications

- **Orchestration:** LangGraph (`langgraph`)
- **LLM client:** `langchain-groq` → Groq-hosted `llama-3.3-70b-versatile`, `temperature=0`
- **Data layer:** SQLite (`data/banking.db`) + `pandas`
- **PII detection:** `presidio-analyzer` + `presidio-anonymizer`, with `spacy` (`en_core_web_sm`) for the
  recall-path NER
- **UI:** Streamlit (`app.py`)
- **Test runner:** `pytest`

See [`requirements.txt`](../requirements.txt) for exact package constraints.

---

## How to Get Started

See the [README](../README.md#setup-and-running) for setup and run instructions. In short:
install `requirements.txt`, set `GROQ_API_KEY` in `.env`, run `python data/generate_data.py` to build the synthetic
database, then `streamlit run app.py`.

---

## Maintenance

- **Model version:** No formal semantic-versioning scheme exists for this repository yet — recommended before any
  production use. This card is generated against git commit `449b2f8` (2026-07-22); treat it as stale past that
  commit until refreshed.
- **Base LLM version:** `llama-3.3-70b-versatile`, served by Groq. Groq/Meta control if and when this model is
  updated or deprecated; such a change would not appear in this repository's git history but could change agent
  behavior. There is currently no automated check for base-model drift.
- **Owner:** Merve Çalışkan ([mervcaliskaan@gmail.com](mailto:mervcaliskaan@gmail.com), GitHub: `Mervecaliskann`)
- **Recommended review cadence:** at minimum quarterly given no formal schedule exists yet, and additionally
  whenever any of the following change: `tools/query_tools.py` (the trusted-`customer_id` assumption underlying
  several mitigations above), `agent/guardrails.py` (rule coverage), `agent/privacy.py` (redaction policy or
  recognizer set), or the base LLM/provider.
- **This card should be updated** whenever a guardrail rule, redaction policy, or the base model changes, and at
  minimum whenever the adversarial test suite's block rate changes from the 100%/0% baseline recorded above.
