<!-- /autoplan restore point: /c/Users/rohan/.gstack/projects/interviewstreet-hackerrank-orchestrate-october26/feat-deletion-audit-agent-autoplan-restore-20261025-074214.md -->
# Ghost Data Still Exists — Deletion Audit Agent: Implementation Plan

Branch: feat/deletion-audit-agent | Repo: interviewstreet/hackerrank-orchestrate-october26 (local clone)
Deadline: HackerRank submission closes ~11h from plan time (2026-10-25). Judge interview after.
Deliverables: `code.zip` (code/ + README), root `output.csv` (518 rows), `log.txt` transcript,
`code/evaluation/evaluation_report.md`, `code/evaluation/usage_report.md`.

## 1. Problem (one paragraph)

For each of 518 deletion requests decide `COMPLETE` / `INCOMPLETE` / `NOT_APPLICABLE`, and emit an
ordered `steps_json` of FINDING / REMEDIATION / VERIFICATION steps with exact `system_id`,
`resource_id`, `operation`, `state`, `evidence_id`. Truth must be reconstructed from evidence
captured at or before `audit_at`; workflow statuses are claims. Policy comes from
`deletion_rules.csv` (15 rules keyed by data category). Scoring is exact-match on status,
step set, field values, and sequence against hidden canonical answers.

## 2. What the data actually looks like (recon findings, 25 min)

- 518 requests, 69 distinct `audit_at` timestamps; 52 of them are 5-day cadence dates with
  exactly 10 cases each, and each such date has exactly one evidence file captured minutes
  before `audit_at` (plus sometimes an image/PDF on the same date). 489/518 cases have exactly
  one text evidence file (captured <= audit_at) containing one of the user's identifiers.
- 19 cases have zero text hits: ~8 are image/PDF-only cases (admin console screenshot,
  support ticket console, backup console, object storage browser, security audit explorer,
  marketing profile console, customer statement PDF); 9 are obvious non-deletion requests
  (download data, reset login, correct surname, marketing opt-out, change email, access copy,
  invoices) -> `NOT_APPLICABLE`, `[]`.
- Request email is current for 418 cases, historical (from `user_audit_log.csv` email change)
  for 100 cases. Identifiers to match: user_id, all current+historical emails (case-insensitive),
  all email hashes (current+historical), all phones, full name. Decoys exist: near-miss emails
  (`smartinez6461.jr@gmail.com` vs `smartinez6461@gmail.com`), uppercase emails, unrelated users
  in the same file. Exact normalized match only.
- Evidence formats (94 files): 11 "readback" trace logs (key=value), 11 `service_resource_export`
  JSON, 11 `customer_data_inventory` SQLite SQL dumps, 12 snapshot-bundle JSON, 5 read-replica +
  3 regional-replica + 2 reporting-replica + 2 failover SQL (replication drift), 1 search-primary
  SQL, 4 manifest-style JSON (`records[]` with system/resource_id/record_type/result/subject_reference),
  1 handover TXT (pipe-delimited, same fields), ~12 assorted logs (structured JSON lines,
  bracketed traces, syslog, notification delivery, support replica, search diagnostic),
  11 PNG console screenshots, 4 PDFs (text layer present; pypdf extracts).
- Sample answers (32) establish the output grammar:
  - COMPLETE: only VERIFICATION steps, each citing the evidence file that proves the state,
    covering every relevant resource (account ABSENT, audit record PRESENT, analytics ANONYMIZED,
    support REDACTED, data flow DISABLED, backup BLOCKED, tombstone REDACTED).
  - INCOMPLETE: FINDING for each violation (cited), REMEDIATION for each (evidence ""), then
    VERIFICATION for ALL relevant resources incl. already-compliant ones (evidence ""), always
    including the subject's account `VERIFY_RECORD_ABSENT`.
  - Ordering: phase, then operation order from the allowed-operations table, then system_id,
    then resource_id (string ascending).
- Category -> treatment seen in data: USER_PROFILE/SEARCH_INDEX_ENTRY/USER_EXPORT/
  SUPPORT_ATTACHMENT/MARKETING_PROFILE/CACHE/contact-hash records -> DELETE;
  SUPPORT_MESSAGE with shared context -> REDACT (finding = REPORT_EXPOSED_PII);
  ANALYTICS_PROFILE identifiable -> ANONYMIZE (REPORT_EXPOSED_PII); DATA_FLOW enabled ->
  DISABLE (REPORT_ACTIVE_DATA_FLOW); BACKUP restore_allowed -> BLOCK_RESTORE
  (REPORT_RESTORE_RISK); SECURITY_AUDIT_LOG within retention -> RETAIN
  (VERIFY_RECORD_PRESENT); LEGAL_HOLD active -> RETAIN; ACCOUNT_TOMBSTONE with direct
  identifiers -> REDACT (REPORT_EXPOSED_PII).

## 3. Premises (to confirm at gate)

P1. The grader is exact-match; a deterministic rules engine over parsed evidence beats a
    free-form LLM agent on precision and reproducibility. LLM is used only where language or
    pixels must be interpreted (request intent, screenshots/PDF layout, residual log lines).
P2. Each case's relevant resource set is exactly what the evidence files captured at or before
    `audit_at` say about that user; nothing is inferred from `system_activity.csv` alone
    (activity rows are claims and decoys). `system_activity` is used only as secondary
    evidence IDs are never required by samples; we cite `file_id` for findings.
P3. The 32 sample cases are a faithful subset of the hidden grammar; 100% on samples is the
    gate for the full run, and per-format parsers must be validated on the sample that cites
    that format.
P4. Python 3.12 stdlib + `pypdf` + Anthropic API (vision + text) is sufficient; no vector DB,
    no framework.
P5. Shipping a reproducible run matters more than novelty: cached LLM outputs are committed
    under `code/cache/` so `python code/main.py --offline` reproduces `output.csv` with zero
    API calls; the usage report documents the online run that produced the cache.

## 4. Architecture

```
dataset/*.csv  dataset/evidence/**            ANTHROPIC_API_KEY (optional, env only)
     |               |                                   |
     v               v                                   v
+-----------+  +----------------+   +-----------------------------------------+
| identity  |  | evidence       |   | llm/                                    |
| resolver  |  | loader         |   |  client.py  (Anthropic Messages, retry, |
| (csv)     |  |  manifest +    |   |             usage ledger, disk cache)   |
+-----------+  |  captured_at   |   |  intent.py  (request_text -> DELETION / |
     |         +----------------+   |             NON_DELETION, multilingual)  |
     |               |              |  vision.py  (png/pdf -> observations[])  |
     |               v              |  residue.py (unparsed lines -> obs[])    |
     |    +---------------------+   +-----------------------------------------+
     |    | parsers/ (per fmt)  |<--------------- fallback for residue only
     |    |  sql_snapshot.py    |
     |    |  json_export.py     |   all parsers emit the same record:
     |    |  json_bundle.py     |   Observation(file_id, captured_at, system_id,
     |    |  json_manifest.py   |     resource_id, category, present:bool|None,
     |    |  txt_handover.py    |     identifiers:{email,phone,hash,user_id,name},
     |    |  log_trace.py       |     flags:{enabled, restore_allowed, retention_until,
     |    |  log_generic.py     |            legal_hold, linked_account_id, ...},
     |    +---------------------+     subject_refs:set[str], raw:str)
     |               |
     v               v
+---------------------------------------------------------------+
| audit/engine.py  (pure, deterministic, unit-tested)           |
|  1. intent gate -> NOT_APPLICABLE short-circuit               |
|  2. identity set for the user                                 |
|  3. select observations: captured_at <= audit_at AND          |
|     subject_refs ∩ identity set  (exact normalized match)     |
|  4. latest observation per (system_id, resource_id)           |
|  5. policy.py: category + flags -> required_state, compliant? |
|  6. steps.py: build FINDING/REMEDIATION/VERIFICATION,         |
|     evidence rules, canonical ordering, step numbering        |
+---------------------------------------------------------------+
     |
     v
output.csv (root)  +  code/evaluation/ (scorer, custom cases, reports)
```

Modules (all under `code/`):

```
code/
  main.py                 CLI: --requests, --out, --offline, --limit, --case, --sample
  audit/
    identity.py           build IdentitySet(user_id, emails, hashes, phones, name) from csvs
    evidence.py           manifest load, format dispatch, caching of parsed observations
    observations.py       Observation dataclass + normalization helpers
    policy.py             deletion_rules.csv -> category rules; decide(obs) -> Decision
    steps.py              Decision[] -> ordered steps_json; ORDER tables; evidence_id rules
    engine.py             audit_case(request) -> (status, steps)
  parsers/                one module per evidence format (see diagram)
  llm/
    client.py             thin Anthropic HTTP client (urllib, no SDK dep), usage ledger
    intent.py             classify_request(text) with cache + regex prefilter
    vision.py             extract_observations(image|pdf) -> JSON schema, cache
    residue.py            extract from unparsed-but-matching lines
  evaluation/
    main.py               score(pred, gold): case/step/field/sequence accuracy + diff report
    custom_cases.py       3+ synthetic cases with gold (tombstone, historical email, NA)
    evaluation_report.md  REQUIRED deliverable
    usage_report.md       REQUIRED deliverable (from usage ledger of final run)
  cache/                  llm outputs keyed by sha256(prompt+inputs) -> reproducible offline
  prompts/                intent.md, vision.md, residue.md (kept as markdown, judge-visible)
  tests/                  pytest: identity, parsers (one per format), policy, steps ordering
  docs/PLAN.md            this file
README.md (root)          setup + run + architecture summary
```

## 5. Core algorithm detail

### 5.1 Intent gate
- Regex prefilter (multilingual): strong deletion verbs (delete, erase, remove, suppression,
  eliminar, 削除, löschen, 삭제 ...) vs explicit non-deletion markers ("do not close", "keep the
  account", "download", "copy of", "correct", "marketing lists only", "change the email").
- LLM (claude-haiku-5-5) decides when prefilter is ambiguous or when both signals appear;
  returns `{intent: DELETION|NON_DELETION, confidence, reason}`; cached by text hash.
- 375 distinct texts; offline mode uses cache; if a text is uncached offline, fall back to
  regex verdict and log a WARNING with the case_id.

### 5.2 Identity resolution
- Email -> user_id via current snapshot, else via audit log (old_value or new_value).
- IdentitySet = {user_id} ∪ emails(lowercased, current + all audit old/new) ∪ email_hashes
  (current + audit) ∪ phones (normalized digits, current + audit) ∪ {full name lowercased}.
- Matching an observation: any observation identifier equals a member exactly after the same
  normalization. No substring, no fuzzy. Name alone matches only when the observation has no
  other identifier fields at all (screenshots).

### 5.3 Evidence selection and temporal cut
- Only files with manifest `captured_at <= audit_at`. For multi-snapshot files (bundles with
  per-snapshot `captured_at`), apply the cut per snapshot.
- Latest observation wins per (system_id, resource_id). Ties -> prefer explicit state fields
  (present/absent) over derived.
- Replica drift: a row in any replica table is an observation of the origin system's resource;
  a resource is PRESENT if any copy at/before audit_at still has it and no later capture of
  that copy shows it gone.
- Evidence citation for a step = file_id of the observation that established the state.

### 5.4 Policy
`category -> (required_action, finding_op, remediation_op, verify_op)`:

| category | condition | action | finding | remediation | verification |
|---|---|---|---|---|---|
| USER_PROFILE, SECONDARY_IDENTIFIER, SEARCH_INDEX_ENTRY, CACHE_ENTRY, USER_EXPORT, MARKETING_PROFILE, SUPPORT_ATTACHMENT | present | DELETE | REPORT_REMAINING_RECORD | DELETE_RECORD | VERIFY_RECORD_ABSENT |
| SUPPORT_MESSAGE, EMAIL_DELIVERY_LOG | identifiers present | REDACT | REPORT_EXPOSED_PII | REDACT_RECORD | VERIFY_RECORD_REDACTED |
| ANALYTICS_PROFILE, PRODUCT_TELEMETRY | attributable | ANONYMIZE | REPORT_EXPOSED_PII | ANONYMIZE_RECORD | VERIFY_RECORD_ANONYMIZED |
| DATA_FLOW | enabled | DISABLE | REPORT_ACTIVE_DATA_FLOW | DISABLE_DATA_FLOW | VERIFY_DATA_FLOW_DISABLED |
| BACKUP_ARCHIVE | restore_allowed | BLOCK | REPORT_RESTORE_RISK | BLOCK_RESTORE | VERIFY_RESTORE_BLOCKED |
| SECURITY_AUDIT_LOG | retention_until > audit_at | RETAIN | - | - | VERIFY_RECORD_PRESENT |
| LEGAL_HOLD_RECORD | hold active | RETAIN | - | - | VERIFY_RECORD_PRESENT |
| ACCOUNT_TOMBSTONE | direct identifiers present | REDACT | REPORT_EXPOSED_PII | REDACT_RECORD | VERIFY_RECORD_REDACTED |

Category is derived per parser from table/collection/record_type/event name via one shared
mapping table (`policy.CATEGORY_ALIASES`), so a new alias is a one-line change.

### 5.5 Step construction
- NOT_APPLICABLE -> `[]`.
- Build Decision per resource. INCOMPLETE iff any Decision is non-compliant.
- COMPLETE: VERIFICATION per resource with evidence_id = establishing file_id.
- INCOMPLETE: FINDING per non-compliant (evidence_id set), REMEDIATION per non-compliant
  (evidence ""), VERIFICATION per every resource (evidence ""), plus the subject account
  `VERIFY_RECORD_ABSENT` even when the account itself was not observed directly, when the
  account id is recoverable (linked `source_resource_id`, profile lookup, bundle).
- Sort key: (PHASE_ORDER, OP_ORDER, system_id, resource_id). Renumber from 1.
- Serialize with `json.dumps(separators=(",", ":"), ensure_ascii=False)`; CSV quoting handled by
  `csv.writer` (QUOTE_MINIMAL), rows sorted by case_id string ascending (matches template).

### 5.6 LLM usage policy and cost
- Text intent: haiku-5-5, ~375 calls x ~300 tokens -> < $0.20.
- Vision: sonnet-5-5 on 11 PNG + 4 PDF (as images or extracted text) -> ~15 calls, < $1.
- Residue extraction: sonnet-5-5 only for files where an identifier string matched but no
  parser produced an observation for that user; expected < 30 calls.
- Usage ledger (JSONL) records model, input/output tokens, latency per call; `usage_report.md`
  is generated from it by `python code/evaluation/main.py --usage`.
- Secrets: `ANTHROPIC_API_KEY` from env only; `.env` ignored by git; never logged.

## 6. Evaluation workflow (required deliverable)
1. `python code/evaluation/main.py --pred output_sample.csv --gold dataset/sample_output.csv`
   prints: case-level status accuracy, step-level exact-match (set and sequence), field-level
   accuracy per column, sequence accuracy (Kendall-style on shared steps), and a per-case diff.
2. Custom cases (`code/evaluation/custom_cases.py`): (a) historical-email subject with tombstone
   exposing phone, (b) NOT_APPLICABLE marketing opt-out with decoy deletion activity,
   (c) replica drift: primary absent, regional replica present, backup restore allowed,
   (d) evidence captured after audit_at must be ignored. Each has gold steps; run with
   `--custom`.
3. Unit tests (`pytest code/tests`) for identity, every parser, policy table, ordering.
4. Failures found and fixes are logged in `evaluation_report.md` as the work proceeds.

## 7. Build order (hour budget ~8h incl. buffer)
1. Scaffold + identity + CSV loaders + output writer + sample harness (45m).
2. Parsers for the three dominant formats (trace log, service_resource_export, inventory SQL)
   + policy + steps; reach parity on samples citing those (90m).
3. Remaining text formats (bundles, manifests, handover, replicas, misc logs) (90m).
4. LLM layer: intent classifier, vision extraction, residue; caching; usage ledger (60m).
5. Full 518 run, inspect distribution, spot-check 20 cases by hand, fix (60m).
6. Evaluation scorer, custom cases, reports, README, zip, log.txt check (60m).
7. Buffer / judge-prep notes (60m).

## 8. Risks
- R1 Hidden grammar differs from samples for untested formats (syslog, notification logs).
  Mitigation: per-format parser tests from real lines; residue LLM fallback; manual spot check.
- R2 Account id not recoverable for some INCOMPLETE cases -> missing a verification step.
  Mitigation: multi-source account discovery; log cases where it fails.
- R3 Replication drift semantics (which copy to cite). Mitigation: cite the latest file that
  shows the copy present; validated on sample cases 16/22 (read replica).
- R4 API quota/latency. Mitigation: concurrency 4, retry with backoff, full cache, offline mode.
- R5 Time. Mitigation: ship after step 5 if needed; reports are generated, not hand-written.

## 9. NOT in scope
- Agentic tool-using loop per case (slower, nondeterministic, no scoring upside under
  exact-match). Deferred to TODOS.
- OCR without a VLM (tesseract not installed; screenshots are crisp but layout matters).
- Web UI / dashboard.
- Streaming or incremental re-audit.

## 10. What already exists
- Starter `code/main.py` and `code/evaluation/main.py` are empty; nothing to reuse.
- `dataset/sample_output.csv` is the format oracle; `dataset/output.csv` is the header template.
- Python 3.12 with `pypdf`, `PIL`, `pytest`, `sqlite3` already available locally.

---

# /autoplan Review — Phase 1: CEO (strategy & scope)

Mode: SELECTIVE EXPANSION (auto). System audit: fresh clone, one commit (`0eddbbd`), no TODOS.md,
starter entry points empty, no CI, no design doc (/office-hours skipped by autoplan). Landscape
check skipped: synthetic grader, no outside market signal. Outside voices: Codex CLI present but
every model (`gpt-6-astra`, `gpt-5.5`, `gpt-5.5-codex`, `gpt-5.1-codex`, `gpt-5-codex`) is
rejected for this ChatGPT account → `[subagent-only]` for every phase.

## 0A. Premise challenge (with experiments run during review)

Experiments run on the 32 samples before accepting any premise:

| Experiment | Result | Consequence |
|---|---|---|
| Where does every gold `resource_id` live? | 89 in the cited file, 12 only in `system_activity.csv`, 7 in other evidence (some captured after `audit_at`), 3 nowhere | P2 is wrong: the relevant resource set is **activity-cluster ∪ evidence**, not evidence alone |
| Do gold evidence_ids ever cite activity/audit ids? | 0 of 75 | Findings and COMPLETE verifications cite `file_id` only |
| Account `DELETE_RECORD` time vs `reported_at` | median −56 min, within ±2 h for every sample where the account came from activity | Account attribution window = `reported_at` ± 3 h |
| Is the account delete order on a day explained by requested_at / case_id / user_id / email / audit_at? | concordance 34:23, 13:44, 13:43, 34:22, 17:6 | No clean ordering; attribution needs elimination + assignment, not rank |
| Cases whose account id appears in any text evidence (crude line match) | 146/541 at/before audit_at; 235/541 counting later files | ~50% of cases need activity-based account attribution |
| Does evidence state override activity result? | case_25/28: account `DELETE_RECORD FAILED` but evidence shows 404 → gold COMPLETE | Evidence wins on state; activity only supplies the resource set and a fallback state |
| Findings without evidence? | case_20 `account_5037` DELETE FAILED, no evidence → gold has verification only | Findings require an evidence observation; verification covers the whole set |
| Non-deletion texts | 9 obvious (`case_534`–`542`), 375 distinct texts, 5 languages | Intent gate must be multilingual; LLM for the ambiguous tail |

Revised premises (replace §3):
- P1 unchanged (deterministic judgement, LLM perception).
- P2' The relevant resource set for a case = resources the deletion workflow touched
  (`deletion_worker` rows in `system_activity.csv` around `reported_at`, linked to the subject
  through evidence-observed resource ids, LINK_RESOURCE_IDENTITY rows, and an account
  attribution step) ∪ resources observed for the subject in evidence captured ≤ `audit_at`.
  Security audit records come from evidence only (never from activity).
- P2'' State at `audit_at` = latest evidence observation per resource; if no observation exists,
  the activity result is the fallback (SUCCESS → treated, FAILED/PARTIAL/CHECK-still-active →
  untreated) but it can only drive VERIFICATION steps, never FINDING steps.
- P3' Samples carry 3/121 unknowable resources; accept an error floor, target ≥ 29/32 exact.
- P4, P5 unchanged.

## 0B. Existing code leverage
| Sub-problem | Existing | Plan |
|---|---|---|
| CSV/JSON/SQLite/regex | stdlib | reuse |
| PDF text | `pypdf` (pinned in requirements.txt) | reuse |
| Vision + multilingual intent | Anthropic API via urllib | thin client |
| Scorer, attribution, parsers | none | build |

## 0C. Dream state
```
CURRENT: empty starter + oracle  --> THIS PLAN: deterministic audit engine, activity+evidence
truth model, LLM perception, offline-reproducible, scored   --> 12-MONTH: same engine as a
library with live connectors, continuous re-audit, reviewer UI
```
Delta: engine + evaluation loop shipped; connectors/UI are the only missing pieces.

## 0C-bis. Alternatives
```
A  Deterministic engine + LLM perception + activity attribution   Effort M  Risk Low  Completeness 9/10  (chosen)
B  LLM agent per case with tools                                   Effort M  Risk High Completeness 6/10
C  Minimal: 3 formats, regex intent, no vision, no attribution     Effort S  Risk Med  Completeness 4/10
RECOMMENDATION: A (P1 completeness, P5 explicit). B's "agentic" story is recovered by E1 (reconciliation pass).
```

## 0D. Selective expansion (auto cherry-pick)
Complexity check: ~22 files / 3 packages → smell acknowledged; per-format parsers over one shared
Observation type is the explicit choice (P5). Candidates:

| # | Proposal | Effort | Decision | Why |
|---|---|---|---|---|
| E1 | LLM second reader over the 94 evidence files into the same Observation schema; diff against parsers; disagreement log in the report | S | ACCEPT (after samples pass) | Finds parser bugs; strongest judge story (CEO voice #5) |
| E2 | Per-case trace JSON (identifiers, observations, attribution candidates, decisions) | S | ACCEPT | Debuggability + interview |
| E3 | Residue LLM extractor for identifier-bearing lines no parser understood | S | ACCEPT | Closes untested-format risk |
| E4 | Evidence coverage report per file | S | ACCEPT | Parser observability |
| E5 | Review list: zero-observation cases, ambiguous account attribution, low-confidence intent | S | ACCEPT | Surfaces guesses |
| E6 | Hand-adjudicated pseudo-gold: 12 real non-sample cases stratified by format/status | S | ACCEPT | Only test of hidden-grammar generalization (CEO voice #4) |
| E7 | Account attribution self-evaluation on the evidence-known subset | S | ACCEPT | Measures the guess, reported in evaluation_report |
| E8 | Web dashboard | M | SKIP | no value in 12 h |
| E9 | Agentic tool-loop fallback | M | DEFER | only if E3+E5 leave gaps |

## 0E. Temporal interrogation (decided now)
- Observation schema fixed before parsers (present, identifiers, enabled, restore_allowed,
  retention_until, linked_account_id, category, captured_at, file_id).
- Category aliases: one table; `contact_lookup` events in analytics logs = hash-keyed contact
  record → DELETE; `support_records` rows = SUPPORT_MESSAGE unless attachment marker; identity
  tombstones = ACCOUNT_TOMBSTONE.
- Attribution: global per-day one-to-one assignment of unowned account deletes to cases
  without an evidence-linked account, cost = |t_delete − (reported_at − 1 h)|; ties → earlier.
- Citation when several files prove a state: latest capture ≤ audit_at. For the account in a
  COMPLETE case with no account observation: cite the case's primary file (sample case_01).
- Usage ledger from the first API call.

## 0F. Mode
SELECTIVE EXPANSION; approach A; E1–E7 accepted, E9 deferred, E8 skipped.

## CLAUDE SUBAGENT (CEO — strategic independence)
13 findings: (1) resource-set rule unstated — critical; (2) temporal pooling may leak stale
resources — high; (3) `system_activity` dismissed too fast — high; (4) synthetic custom cases are
circular — high; (5) LLM second reader over files — high; (6) deliverables scheduled after the
ship cutoff — high; (7) usage report vs offline contradiction — medium; (8) intent gate only
validated on zero-evidence cases — medium; (9) citation rule guess — medium; (10) zero-evidence
deletion case undefined — medium; (11) hash/phone normalization unverified — medium; (12) output
validator missing — medium; (13) judge story thin on AI — medium.
Disposition: 1, 3 → experiments above, premises rewritten. 2 → resource set now anchored on the
workflow cluster, so bystander files cannot add resources; state still taken from the latest
observation. 4, 5 → E6, E1 accepted. 6 → skeleton README/reports/zip script move to build step 1.
7 → final `output.csv` comes from one online run with the ledger on; `--offline` is a
verification path. 8 → cluster the 375 texts, hand-label the ambiguous tail, define partial
requests ("delete marketing profile only") as NOT_APPLICABLE unless the text authorizes account
deletion. 9 → latest-capture rule + primary-file rule (above). 10 → COMPLETE with account-only
verification when the account is attributable, else flagged. 11 → verify one hash reproduces in
step 1 (sha256 of lowercase email is the first guess). 12 → `check_submission.py` in step 1.
13 → framing: perception (LLM) / judgement (deterministic) / reconciliation (E1).

## CEO DUAL VOICES — CONSENSUS TABLE
```
  Dimension                             Claude   Codex   Consensus
  1. Premises valid?                    NO→fixed N/A     flagged (P2 rewritten)
  2. Right problem to solve?            YES      N/A     N/A (single voice)
  3. Scope calibration correct?         MOSTLY   N/A     N/A
  4. Alternatives sufficiently explored? NO→E1   N/A     flagged
  5. Competitive/market risks covered?  PARTIAL  N/A     flagged (E6)
  6. 6-month trajectory sound?          YES      N/A     N/A
```
Codex unavailable (model_not_found for a ChatGPT account); single critical finding (#1) flagged regardless.

## Sections 1–11 (CEO deep review)
1. Architecture — graph `main → engine → {identity, attribution, evidence→parsers, policy, steps}`;
   `llm.client` is the single seam; `--offline` removes it. Per-file parse isolation added
   (finding 1.1, auto-fixed). Rollback = git revert; outputs are files.
2. Error & rescue — table in §Phase1-ErrorMap below; GAP fixed: unknown category fails at
   parser-load via enum (2.1). LLM: malformed/empty/refusal → one stricter retry → fallback.
3. Security — key from env only; `.env` ignored; prompt injection bounded by schema validation
   and the rule that every emitted resource id must occur literally in the source; paths from
   `__file__`; ledger stores prompt hashes only.
4. Data flow — intent → identity → cluster+observe → attribute → latest/resource → policy →
   steps; shadow paths decided: empty text = deletion+flag; unknown subject = COMPLETE,[]+flag
   (count expected 0); zero observations = account-only verification if attributable.
5. Code quality — one Observation type, one alias table, one order function; `log_generic`
   split by signature registry (5.1).
6. Tests — unit per parser (real lines), table-driven policy, ordering golden (case_11),
   samples 32/32 target (≥29 floor), custom 4 + pseudo-gold 12, determinism (online == offline
   byte-for-byte), attribution self-eval.
7. Performance — parse once, cache; ≤ ~550 LLM calls online (375 intent + 94 reader + ~15
   vision + residue), concurrency 4, ~10 min; offline 0.
8. Observability — per-case trace, coverage report, review list, usage ledger, structured log.
9. Deployment — zip checklist + `check_submission.py` (9.1).
10. Trajectory — reversibility 5/5; debt = parser count; docs = PLAN + README + reports.
11. Design — SKIPPED (no UI).

### Phase1-ErrorMap
```
CODEPATH               | FAILURE                      | EXCEPTION            | RESCUED | ACTION                                  | USER SEES
identity.resolve       | email unknown                | UnknownSubject       | Y       | flag, COMPLETE,[]                        | WARN + review list
evidence.load          | missing/undecodable file     | FileNotFound/Unicode | Y       | skip, coverage row                        | WARN
parsers.*              | unexpected line              | ParseError           | Y       | per-line skip, residue count              | coverage report
sql_snapshot           | dump not executable          | sqlite3.Operational  | Y       | regex INSERT fallback                     | WARN
attribution            | no candidate / tie           | NoCandidate / Tie    | Y       | omit step / pick by rule, flag             | review list
llm.client             | 401/429/5xx/timeout          | AuthError/RateLimited/APIError/Timeout | Y | 401: die(problem,cause,fix); others backoff ×5 then fallback | WARN with case id
llm.*                  | malformed/refusal            | LLMOutputError       | Y       | stricter retry ×1 → fallback              | WARN
policy                 | unknown category             | PolicyError          | Y       | fail at load (enum)                       | error at start
output                 | row count/header wrong       | SubmissionError      | Y       | hard fail before write                    | error
```
Failure Modes Registry: 0 CRITICAL GAPS (every row rescued + tested + visible).

## Phase 1 completion summary
```
Mode SELECTIVE | Audit done | Step 0 done (8 experiments) | S1 1 issue | S2 9 paths, 0 gaps
S3 2 threats mitigated | S4 3 shadow paths decided | S5 1 | S6 diagram + 7 test classes
S7 0 | S8 0 | S9 1 | S10 rev 5/5 | S11 skipped | NOT in scope written | exists written
dream delta written | registries written | TODOS 2 (E9, "agent loop"; "connectors") |
scope 9 proposed / 7 accepted | outside voice subagent-only | Lake 9/9 complete options
```

**Phase 1 complete.** Codex: unavailable. Claude subagent: 13 issues. Consensus: 0/6 confirmed
(single voice), 3 flagged → surfaced at gate. Passing to Phase 2.

# Phase 2: Design review — SKIPPED (no UI scope: CLI + files).

# Phase 3.5: DX review (DX POLISH, persona: hackathon judge / AI code judge)

Product type: CLI tool + reports. Persona: a judge who unzips `code.zip`, reads README, runs one
command, has no API key, and must understand the approach in two minutes.

Developer journey:
| Stage | Judge does | Friction | Status |
|---|---|---|---|
| Discover | opens README | organizer text, not ours | fixed: project README replaces it |
| Install | `pip install -r code/requirements.txt` | pypdf not stdlib | fixed: pinned requirements + ImportError hint |
| Hello world | `python code/main.py` | needed a key / `--offline` flag | fixed: cache-first default, API only on miss **and** key present |
| Real usage | `--sample` scores vs sample_output | ambiguous flag | fixed: writes `output_sample.csv` + prints score |
| Debug | parse failure | aborted run | fixed: never abort per file; totals at end; `die(problem, cause, fix)` with exit codes |
| Upgrade | n/a | — | — |

Empathy narrative: "I unzip, see a README that starts with one sentence on the approach and
three commands. I run `pip install -r code/requirements.txt`, then `python code/main.py`. It
prints '518 cases, 0 API calls (cache), 32/32 sample match, output.csv written, sha256 …' in a
few seconds. The evaluation report shows accuracy tables and twelve hand-checked cases. I know
exactly what the LLM did and what it did not."

TTHW: current plan ~8 min (install, find key, learn flag) → target < 2 min (3 steps, no key).

DX scorecard (plan after fixes): Getting started 9/10, CLI 8/10, errors 8/10, docs 8/10,
upgrade n/a, environment 8/10 (Windows newline/encoding rules explicit), community n/a,
measurement 7/10 (usage ledger + coverage report).

## CLAUDE SUBAGENT (DX — independent review)
9 findings, all accepted (mechanical, P1/P5): cache-first default; requirements.txt + version
check; paths from `__file__` with a dataset-missing message; `die()` helper + exit codes; `--sample`
semantics + `--refresh` + rename cache dir to `code/llm_cache/`; usage_report generated once from
the online run and never overwritten without `--force`; evaluation_report self-contained (custom
cases inline); project README first screen; Windows determinism rules (`newline=""`, utf-8,
sorted listings, timezone-explicit parsing, `--offline --strict`, output sha256 in README).

DX consensus: Codex N/A → single voice; no DISAGREE.

**Phase 3.5 complete.** DX 8.2/10 after fixes. TTHW 8 min → < 2 min. Passing to Phase 3 (eng)
then the gate.

# Phase 3: Eng review (architecture, tests, performance)

## Step 0 — scope challenge
Sub-problems vs existing code: nothing to reuse beyond stdlib/pypdf (see 0B). Minimum change set
that ships: engine + attribution + 3 dominant parsers + intent + validator (≈65% of cases).
Complexity smell (22 files) accepted in Phase 1. Search check skipped (no framework).
Completeness check: the plan already chooses complete options; the eng voice adds the missing
negative-evidence model, which is the load-bearing change.

## CLAUDE SUBAGENT (eng — independent review)
12 findings (confidence in parentheses):
1. Negative evidence carries no subject identifier → every ABSENT proof is dropped by
   "subject_refs ∩ identity set"; ~97/518 cases have no account id in any evidence. CRITICAL (9).
2. Windows encoding/newlines (cp1252 default, CRLF). CRITICAL (10).
3. case_id order: input is numeric (`case_33`…`case_550`); string sort misorders. HIGH (8).
4. Category/state vocabulary is per event, not per system (`contact_lookup` → DELETE;
   backup flags `restore_allowed` / `eligible` / `deletion_block` / `result=ACTIVE`;
   SUPPORT_ATTACHMENT vs SUPPORT_MESSAGE; `message_pii_scan`). HIGH (9).
5. Multi-line trace correlation by `trace_id`; `cloud_runtime_export` nests key=value inside an
   escaped `message="…"`. HIGH (8).
6. Name-only matching collides (3 duplicate names, 102 non-ASCII); anchor image cases on the 12
   LINK_RESOURCE_IDENTITY rows. HIGH (8).
7. Intent gate: missing Portuguese "exclusão"; hedged deletions trip non-deletion markers;
   offline regex fallback silently changes answers; cross-check with 10 CLASSIFY_REQUEST rows. MED (8).
8. Replica drift is presence-only; table order varies → any-copy-present within a file. MED (8).
9. LLM security: schema + enum whitelist + literal-id check; LLM never sets evidence_id/status;
   ledger without headers; zip excludes `.env`; manifest paths `is_relative_to(root)`. MED (7).
10. Missing tests: output validator, byte-identical two-run determinism with concurrency,
    sample gate as pytest, socket-less offline run; scorer compares parsed objects. MED (9).
11. All 24 SQL dumps load in sqlite 3.47; use inventory `snapshot_scope` for per-system cut and
    negative scope; bundles have inner snapshots up to 8 days earlier and extra collections
    (`deleted_customer_references`, `retained_order_records`, `operational_record_history`). MED (9).
12. `residue.py` is nondeterministic in the prediction path; replace with a hand-parse failure
    list; verify model ids with a live call at hour 0 and make them config. MED (7).

Disposition (auto-decided, principles noted):
- 1 → ACCEPT. New `ScopeObservation(file_id, captured_at, system, identifiers_seen, resource_ids_seen)`
  per file/table; ABSENT is proven by "subject identifiers absent from an in-scope
  account_service scope". Account id resolved by `attribution.py` (Phase 1 0E rule, plus
  elimination of evidence-attributed accounts). Hour 1 task. (P1)
- 2 → ACCEPT. `encoding="utf-8", newline=""`, `lineterminator="\n"`, `PYTHONUTF8=1` in run
  scripts, no printing of request text to consoles. (P1)
- 3 → TASTE DECISION (surfaced at gate). Default: preserve input order (numeric), assert set
  equality; `--sort string` escape hatch.
- 4 → ACCEPT. State tables keyed on (format, event|collection|record_type); unknown event →
  listed hard failure in the coverage report, never silent. One fixture per event. (P5)
- 5 → ACCEPT. trace_id state machine; unescape nested message payloads. (P1)
- 6 → ACCEPT. Image/PDF cases anchor on LINK_RESOURCE_IDENTITY rows and on identifiers read by
  the VLM; name is NFKD-folded tiebreaker only. Evidence cited for image findings = the image
  `file_id` (problem statement: images are evidence). (P5)
- 7 → ACCEPT. Strong-positive regex only short-circuits to DELETION; everything else → LLM;
  offline cache miss = hard error (`--offline`) ; CLASSIFY_REQUEST rows used as a cross-check in
  the evaluation report, not as truth. (P1)
- 8 → ACCEPT. any-copy-present within a file. (P5)
- 9 → ACCEPT in full. (P1)
- 10 → ACCEPT in full; tests listed in Section 3 below. (P1)
- 11 → ACCEPT. Per-system cut from `snapshot_scope`; extra collections mapped
  (`deleted_customer_references` → ACCOUNT_TOMBSTONE, `retained_order_records` →
  LEGAL_HOLD_RECORD candidate, `operational_record_history` → inspect in step 3). (P1)
- 12 → ACCEPT: residue extractor becomes a deterministic "unparsed identifier-bearing lines"
  report (E3 redefined); LLM second reader (E1) stays a QA diff outside the prediction path;
  model ids in `code/config.json`, verified by a hello call at start when a key is present. (P5)

## ENG DUAL VOICES — CONSENSUS TABLE
```
  Dimension                        Claude      Codex  Consensus
  1. Architecture sound?           YES+fixes   N/A    N/A (single voice); #1 critical flagged regardless
  2. Test coverage sufficient?     NO→fixed    N/A    flagged → fixed
  3. Performance risks addressed?  YES         N/A    N/A
  4. Security threats covered?     PARTIAL→fix N/A    flagged → fixed
  5. Error paths handled?          PARTIAL→fix N/A    flagged → fixed
  6. Deployment risk manageable?   YES+validator N/A  N/A
```

## Section 1 — Architecture (ASCII)
```
dataset/*.csv ─┐                                   dataset/evidence/** ─┐
               ▼                                                       ▼
   identity.py ──► IdentitySet                        evidence.py (manifest, temporal cut)
   activity.py ──► workflow cluster per case ◄──┐          │ dispatch by signature
               │                                │          ▼
               ▼                                │     parsers/* ──► Observation[] + ScopeObservation[]
   attribution.py ──► account_id (+confidence)  │          │
               │                                └──────────┤ (resource ids observed for subject)
               ▼                                           ▼
            engine.audit_case ──► policy.decide ──► steps.build ──► output.csv
               │                      ▲
               ▼                      │
   llm/{client,intent,vision} (only seam; cache-first; absent when --offline)
   evaluation/{scorer, validator, custom_cases, pseudo_gold, reports}
```
Realistic failure: attribution picks the wrong account in an ambiguous batch → one wrong
verification step, flagged in the review list with its candidate set (visible, not silent).

## Section 2 — Code quality
Unknown-event hard failure list; per-format fixtures; one ordering function; `die()` helper;
model ids in config. No DRY concerns beyond the alias table (single source).

## Section 3 — Test review (diagram)
```
CODE PATHS                                              TESTS
[+] audit/identity.py  current/historical/hash/phone     unit: 6 cases incl. decoy `.jr`, uppercase
[+] audit/activity.py  cluster per case                  unit: sample case_18/20 day fixture
[+] audit/attribution.py                                 self-eval on evidence-known subset (report)
[+] audit/evidence.py  temporal cut, per-snapshot cut    unit: file 1 min after audit_at excluded
[+] parsers/* (14)     one fixture per event/table       unit: 14 files × real lines
[+] audit/policy.py    15 rules + aliases                table-driven test
[+] audit/steps.py     ordering + numbering + evidence   golden: sample case_11 (14 steps), case_26
[+] llm/intent.py      regex short-circuit, cache, offline hard error   unit + 29 labelled texts [→EVAL]
[+] llm/vision.py      schema/literal-id validation       unit with a stub response
[+] main.py            output validator, numeric order    pytest: 518 rows, header, set equality
[+] determinism        online == offline, run twice       pytest: sha256 equality, socket blocked
E2E: samples ≥ 29/32 (floor) target 32/32; custom 4; pseudo-gold 12
COVERAGE target: every branch above; GAPS: 0 planned
```
Test plan artifact written to `~/.gstack/projects/<slug>/`.

## Section 4 — Performance
Parse once per file; attribution is O(cases × candidates) per day; LLM ≤ ~400 calls online
(375 intent + ~15 vision + optional 94 reader), concurrency 4; offline 0. No issues.

## Required outputs
NOT in scope: E8 dashboard, E9 agent loop, connectors, OCR without VLM, streaming.
What already exists: stdlib, pypdf, sample oracle, template header.
Failure modes: 0 critical gaps (table in Phase 1 + items 1–12 above all rescued/tested/visible).
Worktree parallelization: Lane A parsers/ (independent per format) ‖ Lane B audit/ engine +
attribution ‖ Lane C evaluation/ + README; merge then E2E. Conflict: `policy.CATEGORY_ALIASES`
touched by A and B → B owns it, A sends alias requests.

**Phase 3 complete.** Codex: unavailable. Claude subagent: 12 issues, 11 accepted, 1 taste.
Consensus: single voice, 3 flagged → fixed. Passing to the gate.

# Cross-phase themes
- **Resource set / negative evidence** — flagged independently by CEO voice (#1, #3) and eng
  voice (#1), and reproduced by the Phase 1 experiments. High-confidence signal; it rewrites P2.
- **Reproducibility on the judge's machine** — DX (#1, #9) and eng (#2, #10): cache-first
  default, utf-8/newline rules, determinism test, validator.
- **LLM in the prediction path** — CEO (#5 wants a second reader), eng (#12 wants residue out):
  resolved as perception-only in the path (intent, vision) + reader as QA diff.

# Decision Audit Trail
| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| 1 | CEO | Approach A (engine + LLM perception + attribution) | Taste (A vs B close on judge story) | P1,P5 | exact-match grader; reproducible | B agent loop; C minimal |
| 2 | CEO | P2 rewritten: resource set = workflow cluster ∪ evidence; evidence wins on state | Mechanical (experiment-backed) | P1 | 12/121 sample resources only in activity | evidence-only truth |
| 3 | CEO | Accept E1 LLM second reader as QA diff (post-samples) | Borderline scope | P1,P3 | parser bug finder + judge story | — |
| 4 | CEO | Accept E2 trace JSON, E4 coverage report, E5 review list | Mechanical | P1 | observability | — |
| 5 | CEO | E3 residue → deterministic unparsed-lines report (no LLM in path) | Taste (eng #12 vs CEO #5) | P5 | exact-match grader punishes variance | LLM residue extraction |
| 6 | CEO | Accept E6 pseudo-gold 12 real cases, E7 attribution self-eval | Mechanical | P1 | only generalization test available | — |
| 7 | CEO | Skip E8; defer E9 + connectors to TODOS.md | Mechanical | P3 | no 12h value | — |
| 8 | CEO | Per-file isolation, enum-at-load, signature registry, submission validator | Mechanical | P1,P5 | — | — |
| 9 | CEO | Final output.csv from one online run with ledger; offline = verification | Mechanical | P6 | usage report must be real | offline final run |
| 10 | CEO | Citation = latest capture ≤ audit_at; account in COMPLETE w/o observation cites primary file | Taste (sample-derived) | P5 | case_01 pattern | origin-system file |
| 11 | DX | Cache-first default, requirements.txt, `__file__` paths, `die()`, `--sample`/`--refresh`/`--offline --strict`, cache dir `code/llm_cache/` | Mechanical | P1,P5 | judge runs one command | — |
| 12 | DX | usage_report generated once; `--force` to overwrite; evaluation_report self-contained | Mechanical | P1 | judge ignores data files | — |
| 13 | ENG | ScopeObservation + negative evidence model + attribution in hour 1 | Mechanical (critical) | P1 | ~97 cases lack account id in evidence | — |
| 14 | ENG | utf-8/newline/lineterminator/PYTHONUTF8 | Mechanical | P1 | cp1252 crashes | — |
| 15 | ENG | case_id output order: numeric (input order), `--sort string` escape | **Taste** | P5 | "sorted by case_id" ambiguous for case_100 vs case_33 | string sort |
| 16 | ENG | Account attribution when ambiguous: assign (cost = \|t − (reported_at − 1h)\|) rather than omit | **Taste** | P1 | expected accuracy > 50% beats a guaranteed miss | omit step |
| 17 | ENG | State tables keyed per event vocabulary; unknown event = hard failure list | Mechanical | P5 | case_12 contact_lookup | per-system table |
| 18 | ENG | Image/PDF cases anchor on LINK rows + VLM identifiers; cite image file_id | Mechanical | P5 | name collisions | name matching |
| 19 | ENG | Intent: strong-positive regex → DELETION only; rest → LLM; offline miss = hard error | Mechanical | P1 | silent drift | regex fallback |
| 20 | ENG | Replica: any-copy-present within a file | Mechanical | P5 | table order varies | last-parsed-wins |
| 21 | ENG | LLM security controls (schema, literal ids, no evidence_id from LLM, path checks) | Mechanical | P1 | injection | — |
| 22 | ENG | Determinism + validator + offline socket-less tests | Mechanical | P1 | — | — |

# TODOS (written to TODOS.md)
- E9 agentic tool-loop fallback for zero-observation cases (P3, after submission).
- Evidence connectors + continuous re-audit (P3, 12-month ideal).

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR (via /autoplan) | 9 proposals, 7 accepted, 1 deferred; 0 critical gaps |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | UNAVAILABLE (model_not_found) | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (via /autoplan) | 12 issues, 0 critical gaps after fixes |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | SKIPPED (no UI scope) | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 1 | CLEAR (via /autoplan) | score: 5/10 → 8.2/10, TTHW: 8 min → <2 min |

- **CROSS-MODEL:** Codex unavailable; all voices are Claude subagents (independent contexts). Three themes recurred across phases (resource set, reproducibility, LLM-in-path).
- **VERDICT:** CEO + ENG + DX CLEARED — ready to implement after the gate decisions below.

**UNRESOLVED DECISIONS:**
- Gate: confirm revised premises P2'/P2''/P3'.
- Taste #15: case_id output order numeric (recommended) vs string.
- Taste #16: ambiguous account attribution → assign (recommended) vs omit.
