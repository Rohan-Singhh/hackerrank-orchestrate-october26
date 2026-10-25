# Ghost Data Still Exists — deletion audit agent

Audits 518 account-deletion requests for a commerce platform and proves, from the supplied
evidence, whether each person's data is really gone. Output: `output.csv` with
`case_id,deletion_status,steps_json`.

**Approach in one sentence:** a deterministic judgement engine (identity resolution,
evidence parsers for 14 formats, workflow reconstruction from the activity stream,
policy table, canonical step ordering) with a language/vision model used only where
rules cannot read: multilingual request intent and screenshot/PDF evidence.

## Run it

```bash
pip install -r code/requirements.txt
python code/main.py            # -> output.csv at the repo root (518 rows), ~5 s without model calls
python code/main.py --sample   # audits the 32 public samples and scores them against dataset/sample_output.csv
```

The dataset is expected at `dataset/` next to `code/` (the organizer layout). Pass
`--dataset <dir>` otherwise.

Model calls are **cache-first**: every answer is stored under `code/llm_cache/` and replayed,
so the judge's run reproduces `output.csv` with zero network calls and no key. To make live
calls, export one of `OPENROUTER_API_KEY` (free models configured in `code/config.json`),
`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. Useful flags:

| flag | effect |
|---|---|
| `--offline` / `--offline --strict` | never call a model; `--strict` fails on a cache miss instead of falling back to the regex gate |
| `--no-llm` | deterministic only (regex intent, screenshots via the data-catalog fallback) |
| `--refresh` | ignore cached model answers |
| `--case case_41` | audit one case and print its trace |
| `--attribution-eval` | measure account attribution on the evidence-known subset |
| `--sort string` | string-sorted case ids instead of the request-file (numeric) order |

Checks: `python code/scripts/check_submission.py` validates the contract (518 rows, enums,
step numbering, evidence rules) and prints the file's sha256; `python -m pytest code/tests -q`
runs the unit tests; `python code/scripts/make_zip.py` builds `code.zip`.

## How it decides

```
request ─ intent gate ─ identity set ─ evidence (reported_at < captured_at ≤ audit_at)
            │ regex+model     │ current+historical emails/hashes/phones  │ 14 parsers → Observation
            ▼                 ▼                                          ▼
   NOT_APPLICABLE, []    subject-linked resources  +  workflow rows (system_activity.csv)
                                     │                        │ account attribution (batch, status↔result)
                                     ▼                        ▼
                         latest observation per resource → policy (deletion_rules.csv) → ordered steps
```

1. **Intent.** A multilingual regex settles clear deletions and explicit "keep my account"
   requests; the rest go to the model (`code/prompts/intent.md`). Non-deletion → `NOT_APPLICABLE`.
2. **Identity.** `user_identities.csv` + `user_audit_log.csv` give every current and historical
   email, email hash and phone. Matching is exact after normalisation (decoys such as
   `smartinez6461.jr@gmail.com` never match `smartinez6461@gmail.com`).
3. **Evidence.** Every file in `evidence_files.csv` is parsed into `Observation` records
   (resource, category, present?, identifiers left, flags such as `enabled` /
   `restore_allowed` / `retention_until`). SQL dumps are executed in sqlite; replicas count as
   copies (any copy present ⇒ present). Only files captured after the workflow's `reported_at`
   and at or before `audit_at` describe the state being audited.
4. **Workflow.** The deletion worker's rows in `system_activity.csv` name the resources the
   workflow touched but not the person. Resources are linked through the evidence; deleted
   accounts (404, no identity left) are attributed per batch using the observed structure of
   the stream (account deletes 40–70 min before `reported_at`; the worker's result matches the
   claimed `reported_status` in 194/198 evidence-linked cases). The attribution accuracy is
   measured on the evidence-known subset and reported.
5. **Policy.** `deletion_rules.csv` → required treatment per category; evidence decides the
   state (a claimed `DELETE_RECORD SUCCESS` with a 200 profile is a ghost). Security audit logs
   and legal holds are retained; tombstones keep only the `user_id`.
6. **Steps.** `COMPLETE` → verification steps citing the proving file; `INCOMPLETE` → findings
   (cited), remediation, verification of every relevant resource; ordered by phase, operation
   table, system, resource.

## Layout

```
code/main.py              CLI
code/audit/               data loaders, identity, observations, activity index, attribution, policy, steps, engine
code/parsers/             sql, json (exports/bundles/manifests), txt (handover), logs (6 syntaxes, 1 vocabulary)
code/llm/                 client (OpenAI-compatible + Anthropic, cache, usage ledger), intent gate, vision reader
code/prompts/             intent.md, vision.md
code/evaluation/          scorer, custom cases, evaluation_report.md, usage_report.md, usage.py
code/scripts/             check_submission.py, make_zip.py
code/tests/               pytest
code/docs/PLAN.md         plan + review trail (how the truth model was derived from the data)
code/audit_trace/         per-case trace JSON (what was linked, attributed and decided)
```
