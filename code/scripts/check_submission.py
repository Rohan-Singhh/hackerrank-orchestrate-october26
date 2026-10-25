"""Validate output.csv against the submission contract before uploading.

    python code/scripts/check_submission.py [output.csv] [--requests dataset/deletion_requests.csv]

Checks: header, one row per request (set equality), status enum, steps_json is a
JSON array, step fields and enums, consecutive step numbers, phase ordering,
evidence rules per status (COMPLETE -> VERIFICATION only with evidence ids;
INCOMPLETE -> at least one FINDING with evidence, remediation/verification
empty evidence; NOT_APPLICABLE -> []), and that cited evidence ids exist.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ALLOWED = {
    ("FINDING", "REPORT_REMAINING_RECORD", "PRESENT"), ("FINDING", "REPORT_EXPOSED_PII", "PRESENT"),
    ("FINDING", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE"), ("FINDING", "REPORT_RESTORE_RISK", "ACTIVE"),
    ("REMEDIATION", "DISABLE_DATA_FLOW", "DISABLED"), ("REMEDIATION", "BLOCK_RESTORE", "BLOCKED"),
    ("REMEDIATION", "DELETE_RECORD", "ABSENT"), ("REMEDIATION", "REDACT_RECORD", "REDACTED"),
    ("REMEDIATION", "ANONYMIZE_RECORD", "ANONYMIZED"), ("VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT"),
    ("VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT"), ("VERIFICATION", "VERIFY_RECORD_REDACTED", "REDACTED"),
    ("VERIFICATION", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"), ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
    ("VERIFICATION", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
}
PHASE = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}
FIELDS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("output", nargs="?", default=str(REPO / "output.csv"))
    ap.add_argument("--requests", default=str(REPO / "dataset" / "deletion_requests.csv"))
    ap.add_argument("--evidence", default=str(REPO / "dataset" / "evidence_files.csv"))
    args = ap.parse_args(argv)
    errors = []
    raw = Path(args.output).read_bytes()
    if b"\r\n" in raw:
        errors.append("CRLF line endings found; expected \\n")
    with open(args.output, encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = list(reader)
    if header != ["case_id", "deletion_status", "steps_json"]:
        errors.append(f"bad header {header}")
    req_ids = [r["case_id"] for r in csv.DictReader(open(args.requests, encoding="utf-8", newline=""))] if Path(args.requests).exists() else None
    ev_ids = {r["file_id"] for r in csv.DictReader(open(args.evidence, encoding="utf-8", newline=""))} if Path(args.evidence).exists() else None
    ids = [r[0] for r in rows]
    if req_ids is not None:
        if len(rows) != len(req_ids):
            errors.append(f"{len(rows)} rows, expected {len(req_ids)}")
        if set(ids) != set(req_ids):
            errors.append(f"case id set differs: missing={sorted(set(req_ids) - set(ids))[:5]} extra={sorted(set(ids) - set(req_ids))[:5]}")
    if len(set(ids)) != len(ids):
        errors.append("duplicate case ids")
    for cid, status, sj in rows:
        if status not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
            errors.append(f"{cid}: bad status {status}")
        try:
            steps = json.loads(sj)
        except json.JSONDecodeError as exc:
            errors.append(f"{cid}: steps_json not JSON: {exc}")
            continue
        if not isinstance(steps, list):
            errors.append(f"{cid}: steps_json not an array")
            continue
        if status == "NOT_APPLICABLE" and steps:
            errors.append(f"{cid}: NOT_APPLICABLE must have []")
        if status == "COMPLETE" and not steps:
            errors.append(f"{cid}: COMPLETE with no verification steps (review)")
        phases = []
        for i, s in enumerate(steps, 1):
            if set(s) != FIELDS:
                errors.append(f"{cid} step {i}: fields {sorted(s)}")
                continue
            if s["step_no"] != i:
                errors.append(f"{cid} step {i}: step_no={s['step_no']}")
            if (s["phase"], s["operation"], s["state"]) not in ALLOWED:
                errors.append(f"{cid} step {i}: disallowed {s['phase']}/{s['operation']}/{s['state']}")
            phases.append(PHASE.get(s["phase"], 9))
            ev = s["evidence_id"]
            if status == "COMPLETE":
                if s["phase"] != "VERIFICATION":
                    errors.append(f"{cid} step {i}: COMPLETE case with {s['phase']}")
                if not ev:
                    errors.append(f"{cid} step {i}: COMPLETE verification without evidence")
            if status == "INCOMPLETE":
                if s["phase"] == "FINDING" and not ev:
                    errors.append(f"{cid} step {i}: finding without evidence")
                if s["phase"] != "FINDING" and ev:
                    errors.append(f"{cid} step {i}: {s['phase']} must have empty evidence_id")
            if ev and ev_ids is not None and ev not in ev_ids and not ev.startswith(("activity_", "audit_")):
                errors.append(f"{cid} step {i}: unknown evidence id {ev}")
        if phases != sorted(phases):
            errors.append(f"{cid}: phases out of order")
        if status == "INCOMPLETE" and not any(s.get("phase") == "FINDING" for s in steps):
            errors.append(f"{cid}: INCOMPLETE without a FINDING")
    sha = hashlib.sha256(raw).hexdigest()
    print(f"{args.output}: {len(rows)} rows, sha256={sha}")
    if errors:
        print(f"{len(errors)} problems:")
        for e in errors[:50]:
            print("  -", e)
        return 1
    print("OK: submission contract satisfied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
