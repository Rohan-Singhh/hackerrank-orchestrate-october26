# Evaluation report

Generated 2026-10-25 04:04 UTC by `python code/evaluation/report.py`.

## 1. Method

Predictions are compared with gold answers as parsed JSON (key order and whitespace ignored, array order kept).
- **Case-level**: `deletion_status` equality.
- **Step-level**: a step is the tuple (phase, system_id, resource_id, operation, state, evidence_id) without `step_no`; precision/recall/F1 over multiset intersection.
- **Field-level**: each gold step is aligned to the most similar unused predicted step; accuracy per field.
- **Sequence**: the whole ordered list (and status) must match exactly.

## 2. Public samples (32 cases, `dataset/sample_output.csv`)

### Sample metrics

| Metric | Value |
|---|---|
| Cases | 32 |
| Case-level status accuracy | 1.000 (32/32) |
| Step-level precision / recall / F1 (exact step match) | 0.950 / 0.886 / 0.917 (tp=171, fp=9, fn=22) |
| Sequence accuracy (whole ordered list exact) | 0.531 (17/32) |
| Field accuracy: phase | 0.933 |
| Field accuracy: system_id | 0.855 |
| Field accuracy: resource_id | 0.808 |
| Field accuracy: operation | 0.855 |
| Field accuracy: state | 0.855 |
| Field accuracy: evidence_id | 0.927 |

| Case | Gold status | Predicted | Exact | Missing steps | Extra steps |
|---|---|---|---|---|---|
| case_01 | COMPLETE | COMPLETE | yes | - | - |
| case_02 | COMPLETE | COMPLETE | yes | - | - |
| case_03 | COMPLETE | COMPLETE | no | VERIFY_RECORD_ANONYMIZED analytics_9406 | - |
| case_04 | COMPLETE | COMPLETE | no | VERIFY_RECORD_ABSENT account_004; VERIFY_RECORD_ANONYMIZED analytics_901 | VERIFY_RECORD_ABSENT account_2387 |
| case_05 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_06 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_07 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_08 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_09 | INCOMPLETE | INCOMPLETE | no | VERIFY_RECORD_PRESENT audit_record_4902 | - |
| case_10 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_11 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_12 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_13 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_14 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_15 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_16 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_17 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_18 | INCOMPLETE | INCOMPLETE | no | VERIFY_RECORD_ABSENT account_5521; VERIFY_RECORD_PRESENT audit_record_6645 | VERIFY_RECORD_ABSENT account_3610 |
| case_19 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_20 | INCOMPLETE | INCOMPLETE | no | VERIFY_RECORD_PRESENT audit_record_5569 | - |
| case_21 | COMPLETE | COMPLETE | no | VERIFY_RECORD_ANONYMIZED analytics_5335 | - |
| case_22 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_23 | INCOMPLETE | INCOMPLETE | no | VERIFY_RECORD_ABSENT account_410; VERIFY_RECORD_PRESENT audit_record_4128 | VERIFY_RECORD_ABSENT account_7015 |
| case_24 | INCOMPLETE | INCOMPLETE | no | VERIFY_RECORD_PRESENT audit_record_7095 | - |
| case_25 | COMPLETE | COMPLETE | no | VERIFY_RECORD_ABSENT account_3136; VERIFY_RECORD_ANONYMIZED analytics_1895 | VERIFY_RECORD_ABSENT account_5684 |
| case_26 | COMPLETE | COMPLETE | no | VERIFY_RECORD_ABSENT account_5684; VERIFY_RECORD_ANONYMIZED analytics_4566 | VERIFY_RECORD_ABSENT account_3136; VERIFY_RECORD_ANONYMIZED analytics_1895 |
| case_27 | COMPLETE | COMPLETE | no | VERIFY_RECORD_ANONYMIZED analytics_4574 | - |
| case_28 | COMPLETE | COMPLETE | no | VERIFY_RECORD_ABSENT account_5482; VERIFY_RECORD_ANONYMIZED analytics_6077 | VERIFY_RECORD_ABSENT account_7012 |
| case_29 | INCOMPLETE | INCOMPLETE | no | VERIFY_RECORD_ABSENT account_7012; VERIFY_RECORD_PRESENT audit_record_3954 | VERIFY_RECORD_ABSENT account_5482 |
| case_30 | INCOMPLETE | INCOMPLETE | no | VERIFY_RECORD_PRESENT audit_record_1707 | - |
| case_31 | COMPLETE | COMPLETE | no | VERIFY_RECORD_ABSENT account_4857 | VERIFY_RECORD_ABSENT account_1810 |
| case_32 | INCOMPLETE | INCOMPLETE | yes | - | - |

Sample-specific error floor: 3 of 121 gold resources (`audit_record_6645`, `audit_record_5569`, `account_004`) occur in no participant-visible file; several others only in files captured after `audit_at`. Those steps cannot be produced from the supplied data.

## 3. Custom evaluation cases (5 synthetic worlds, `code/evaluation/custom_cases.py`)

| Case | What it pins down | Gold |
|---|---|---|
| custom_01 | historical email as request email; tombstone still carrying a phone | INCOMPLETE: REPORT_EXPOSED_PII tombstone → REDACT; verify account absent, audit record present, tombstone redacted |
| custom_02 | marketing opt-out with a decoy DELETE_RECORD row in the activity stream | NOT_APPLICABLE, [] |
| custom_03 | replica drift (primary absent, regional replica present) + restorable backup | INCOMPLETE: remaining record + restore risk; BLOCK_RESTORE before DELETE_RECORD |
| custom_04 | evidence captured one minute after audit_at must be ignored | COMPLETE on the earlier proof |
| custom_05 | sync job recreated the account after a successful delete (ghost) | INCOMPLETE: REPORT_REMAINING_RECORD on the account |

### Custom-case metrics

| Metric | Value |
|---|---|
| Cases | 5 |
| Case-level status accuracy | 1.000 (5/5) |
| Step-level precision / recall / F1 (exact step match) | 1.000 / 1.000 / 1.000 (tp=16, fp=0, fn=0) |
| Sequence accuracy (whole ordered list exact) | 1.000 (5/5) |
| Field accuracy: phase | 1.000 |
| Field accuracy: system_id | 1.000 |
| Field accuracy: resource_id | 1.000 |
| Field accuracy: operation | 1.000 |
| Field accuracy: state | 1.000 |
| Field accuracy: evidence_id | 1.000 |

| Case | Gold status | Predicted | Exact | Missing steps | Extra steps |
|---|---|---|---|---|---|
| custom_01 | INCOMPLETE | INCOMPLETE | yes | - | - |
| custom_02 | NOT_APPLICABLE | NOT_APPLICABLE | yes | - | - |
| custom_03 | INCOMPLETE | INCOMPLETE | yes | - | - |
| custom_04 | COMPLETE | COMPLETE | yes | - | - |
| custom_05 | INCOMPLETE | INCOMPLETE | yes | - | - |

## 4. Generalisation checks

### Hand-adjudicated pseudo-gold (12 real non-sample cases)

| Metric | Value |
|---|---|
| Cases | 12 |
| Case-level status accuracy | 1.000 (12/12) |
| Step-level precision / recall / F1 (exact step match) | 1.000 / 1.000 / 1.000 (tp=73, fp=0, fn=0) |
| Sequence accuracy (whole ordered list exact) | 1.000 (12/12) |
| Field accuracy: phase | 1.000 |
| Field accuracy: system_id | 1.000 |
| Field accuracy: resource_id | 1.000 |
| Field accuracy: operation | 1.000 |
| Field accuracy: state | 1.000 |
| Field accuracy: evidence_id | 1.000 |
| Case | Gold status | Predicted | Exact | Missing steps | Extra steps |
|---|---|---|---|---|---|
| case_304 | COMPLETE | COMPLETE | yes | - | - |
| case_41 | COMPLETE | COMPLETE | yes | - | - |
| case_413 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_43 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_433 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_44 | COMPLETE | COMPLETE | yes | - | - |
| case_453 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_480 | COMPLETE | COMPLETE | yes | - | - |
| case_64 | COMPLETE | COMPLETE | yes | - | - |
| case_65 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_93 | INCOMPLETE | INCOMPLETE | yes | - | - |
| case_97 | COMPLETE | COMPLETE | yes | - | - |

These cases were adjudicated by hand from the evidence files before the engine was run on them; they are the only test of generalisation beyond the public samples.

| Evidence files | Parsed | Observations | Subject-linked | Unparsed identifier lines |
|---|---|---|---|---|
| 94 | 87 | 2910 | 2658 | 0 |

Intent gate sources on the full run: `{'regex': 31, 'model': 1, 'fallback': 0}`; review-list items: 9 (ambiguous account attribution, zero-evidence cases, catalog fallbacks).

## 5. Failures found and improvements made

Chronological log of failures found while building and the change each one caused. Metrics are on the 32 public samples unless stated.

| # | Failure found | How it was found | Fix | Effect |
|---|---|---|---|---|
| 1 | Plan assumed the relevant resource set comes from evidence alone. 12 of 121 gold resources in the samples exist only in `system_activity.csv` (deleted accounts leave a 404 with no identity), 3 exist nowhere participant-visible. | Traced every gold `resource_id` to its source file before coding (`code/docs/PLAN.md`, Phase 1 experiments). | Truth model rewritten: resources = subject-linked evidence ∪ workflow rows; evidence decides state; findings need an observation, verification covers the whole set. Account attribution module added. | Made the engine possible at all; first run reached status accuracy 0.84, step F1 0.79. |
| 2 | Support tickets whose only remaining identifier was the stable `user_id` were flagged as exposed PII (cases 26-28 went INCOMPLETE). | Sample diff. | Redaction policy now checks direct identifiers only (name/email/phone/hash); the `user_id` is the permitted tombstone reference. Analytics still counts `user_id` as attributable. | Status 27/32 → 31/32. |
| 3 | Data-catalog `LINK_RESOURCE_IDENTITY` rows added resources the gold ignores (tombstone_17 in case_17). | Sample diff. | Catalog links are used only as a fallback for media-only cases and for account elimination. | −2 false steps. |
| 4 | Older evidence leaked stale resources: a March profile lookup gave case_17 a second account (`account_6523`) that the gold ignores. | Sample diff + CEO review warning. | Observations are taken from files captured after the workflow's `reported_at` (fallback: after `requested_at`); negative-evidence scopes use the same window. | resource_id field accuracy 0.78 → 0.83. |
| 5 | Account attribution by "nearest to reported_at" was wrong inside batches (same-hour cases). | Attribution self-evaluation on the evidence-known subset. | Candidates filtered by `reported_status` ↔ worker result (COMPLETE→SUCCESS, NOT_STARTED→FAILED, PARTIALLY_COMPLETE→PARTIAL; 194/198 on evidence-known cases), elimination of evidence-owned accounts, fewest-candidates-first assignment. | Self-eval 0.83 → 0.88 (179 cases). |
| 6 | Sample requests were counted twice when building batches, so identity-less analytics were assigned to the wrong case. | Sample diff (case_10 got two analytics records). | Requests de-duplicated by case id; batch window tightened to [first account delete − 30 min, reported_at + 2 h]. | −2 false steps. |
| 7 | Non-deletion texts with a deletion verb ("remove me from marketing lists only", "without removing any records") were classified as deletions. | Full-run review list (8 of 9 expected NOT_APPLICABLE missing). | Explicit keep-account markers now win before deletion verbs; mixed texts go to the model. | NOT_APPLICABLE 3 → 8 of 9 without a model. |
| 8 | A sed edit wrote literal backspace bytes into `\b` regexes; three parsers silently matched nothing. | Spy-instrumented the method; `repr()` of the line. | Byte-level repair script; rule: never patch Python regexes through the shell. | Screenshot fallback started working (case_34 INCOMPLETE). |
| 9 | Screenshot/PDF cases had zero steps without a vision model. | Full-run zero-step list. | Deterministic fallback: data-catalog link names the resource, the worker's last result gives the state, the screenshot captured in the audit window is cited. | 10 cases with zero steps → 0. |
| 10 | `output.csv` must be reproducible on the judge's machine. | DX review. | utf-8 + `newline=""` + `\n` line terminator, atomic rename, numeric case-id order, sha256 printed by `check_submission.py`. | — |

Known remaining errors on the samples (not fixable from participant data): `audit_record_6645`, `audit_record_5569`, `account_004` appear in no participant file; `audit_record_4128/7095/3954/1707` only in a bundle captured after `audit_at`; identity-less anonymised analytics in a batch are a coin flip between batch members.

