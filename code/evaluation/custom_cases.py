"""Custom evaluation cases (beyond the 32 public samples).

Each case is a tiny synthetic world (identities, audit log, activity rows,
evidence observations) with a hand-written gold answer that exercises one rule
the public samples do not pin down:

  C1  historical email + tombstone that still carries a phone -> REDACT, status INCOMPLETE
  C2  non-deletion request (marketing opt-out) with a decoy deletion row -> NOT_APPLICABLE, []
  C3  replica drift: primary absent, regional replica present, backup restorable -> findings + verification
  C4  evidence captured one minute after audit_at must be ignored -> COMPLETE on earlier proof
  C5  sync job recreated the account after a successful delete (ghost) -> REPORT_REMAINING_RECORD

Run: python code/evaluation/custom_cases.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))

from audit.activity import ActivityIndex  # noqa: E402
from audit.data import Activity, Dataset, EvidenceFile, Request  # noqa: E402
from audit.engine import Engine, EvidenceStore  # noqa: E402
from audit.identity import IdentityResolver  # noqa: E402
from audit.observations import (ACCOUNT_TOMBSTONE, ANALYTICS_PROFILE, BACKUP_ARCHIVE, SEARCH_INDEX_ENTRY,  # noqa: E402
                                SECURITY_AUDIT_LOG, USER_PROFILE, Observation, ParseResult, ScopeObservation)
from evaluation.scorer import format_report, score  # noqa: E402
from llm.intent import IntentGate  # noqa: E402

H = "f" * 64
IDS = [{"user_id": "user_901", "name": "Test Person", "email": "new.person@example.com", "phone": "+1 555 000 0001", "email_hash": H, "updated_at": "2026-01-01T00:00:00Z"},
       {"user_id": "user_902", "name": "Other Person", "email": "other@example.com", "phone": "+1 555 000 0002", "email_hash": "e" * 64, "updated_at": "2026-01-01T00:00:00Z"}]
AUDIT = [{"audit_id": "audit_901", "user_id": "user_901", "event_time": "2025-06-01T00:00:00Z", "field_name": "email", "old_value": "old.person@example.com", "new_value": "new.person@example.com"}]
FILES = [EvidenceFile("evidence_c1", "evidence/x1.json", "test", "2026-09-10T12:00:00Z"),
         EvidenceFile("evidence_c2", "evidence/x2.sql", "test", "2026-09-10T12:00:00Z"),
         EvidenceFile("evidence_late", "evidence/x3.json", "test", "2026-09-10T12:01:00Z"),
         EvidenceFile("evidence_c5", "evidence/x5.json", "test", "2026-09-10T12:00:00Z")]


def A(aid, t, ev, src, tgt, rid, res, det=""):
    return Activity(aid, t, ev, src, tgt, rid, res, det)


def world():
    acts = [
        # C1 workflow (reported 2026-09-05T12:00)
        A("activity_c1a", "2026-09-05T11:00:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9101", "SUCCESS"),
        A("activity_c1b", "2026-09-05T12:40:00Z", "REDACT_RECORD", "deletion_worker", "identity_service", "tombstone_9101", "FAILED"),
        # C2 decoy deletion row for the non-deletion requester
        A("activity_c2a", "2026-09-05T11:10:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9102", "SUCCESS"),
        # C3 workflow (reported 2026-09-06T12:00)
        A("activity_c3a", "2026-09-06T11:00:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9103", "SUCCESS"),
        A("activity_c3b", "2026-09-06T12:30:00Z", "CHECK_RESTORE_ACCESS", "deletion_worker", "backup_service", "backup_9103", "SUCCESS"),
        # C4 workflow (reported 2026-09-07T12:00)
        A("activity_c4a", "2026-09-07T11:00:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9104", "SUCCESS"),
        # C5 ghost: delete then sync recreated
        A("activity_c5a", "2026-09-08T11:00:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9105", "SUCCESS"),
        A("activity_c5b", "2026-09-08T11:00:30Z", "SYNC_RECORD", "account_service", "account_replication_cluster", "account_9105", "SUCCESS"),
    ]
    parsed = {}
    # evidence_c1: C1 tombstone with phone + audit record; C5 ghost account present again
    parsed["evidence_c1"] = ParseResult(observations=[
        Observation("evidence_c1", "2026-09-10T12:00:00Z", "tombstone_9101", ACCOUNT_TOMBSTONE, True, identifiers={"user_id": "user_901", "phone": "+1 555 000 0001"}, subject_refs={"user_901", "+1 555 000 0001"}, source="test"),
        Observation("evidence_c1", "2026-09-10T12:00:00Z", "audit_record_9101", SECURITY_AUDIT_LOG, True, identifiers={"user_id": "user_901"}, subject_refs={"user_901"}, flags={"retention_until": "2033-01-01T00:00:00Z"}, source="test"),
        Observation("evidence_c1", "2026-09-10T12:00:00Z", "account_9101", USER_PROFILE, False, subject_refs={"old.person@example.com"}, source="test"),
    ])
    # evidence_c2: C3 replica drift: primary absent, replica present; backup restorable
    parsed["evidence_c2"] = ParseResult(observations=[
        Observation("evidence_c2", "2026-09-10T12:00:00Z", "account_9103", USER_PROFILE, False, subject_refs={"c3@example.com"}, source="sql:primary"),
        Observation("evidence_c2", "2026-09-10T12:00:00Z", "account_9103", USER_PROFILE, True, identifiers={"email": "c3@example.com"}, subject_refs={"c3@example.com"}, source="sql:replica_eu", sort_key="2"),
        Observation("evidence_c2", "2026-09-10T12:00:00Z", "backup_9103", BACKUP_ARCHIVE, True, identifiers={"email": "c3@example.com"}, subject_refs={"c3@example.com"}, flags={"restore_allowed": True}, source="sql"),
        Observation("evidence_c2", "2026-09-10T12:00:00Z", "search_9104", SEARCH_INDEX_ENTRY, False, subject_refs={"c4@example.com"}, source="sql"),
        Observation("evidence_c2", "2026-09-10T12:00:00Z", "account_9104", USER_PROFILE, False, subject_refs={"c4@example.com"}, source="sql"),
    ], scopes=[ScopeObservation("evidence_c2", "2026-09-10T12:00:00Z", "account_service", identifiers_seen={"c3@example.com"}, source="sql")])
    # evidence_late: captured one minute AFTER audit_at (must be ignored): would show C4 search present
    parsed["evidence_late"] = ParseResult(observations=[
        Observation("evidence_late", "2026-09-10T12:01:00Z", "search_9104", SEARCH_INDEX_ENTRY, True, identifiers={"email": "c4@example.com"}, subject_refs={"c4@example.com"}, source="test"),
    ])
    parsed["evidence_c5"] = ParseResult(observations=[
        Observation("evidence_c5", "2026-09-10T12:00:00Z", "account_9105", USER_PROFILE, True, identifiers={"email": "c5@example.com", "name": "Ghost Five"}, subject_refs={"c5@example.com"}, source="test"),
    ])
    ids = IDS + [
        {"user_id": "user_903", "name": "C Three", "email": "c3@example.com", "phone": "+1 555 000 0003", "email_hash": "3" * 64, "updated_at": "2026-01-01T00:00:00Z"},
        {"user_id": "user_904", "name": "C Four", "email": "c4@example.com", "phone": "+1 555 000 0004", "email_hash": "4" * 64, "updated_at": "2026-01-01T00:00:00Z"},
        {"user_id": "user_905", "name": "Ghost Five", "email": "c5@example.com", "phone": "+1 555 000 0005", "email_hash": "5" * 64, "updated_at": "2026-01-01T00:00:00Z"},
    ]
    reqs = [
        Request("custom_01", "old.person@example.com", "2026-09-04T12:00:00Z", "COMPLETE", "2026-09-05T12:00:00Z", "2026-09-10T12:00:00Z", "Please delete my account; keeping an internal deleted-account reference is fine."),
        Request("custom_02", "other@example.com", "2026-09-04T12:00:00Z", "NOT_STARTED", "2026-09-05T12:00:00Z", "2026-09-10T12:00:00Z", "Please remove me from marketing lists only. I still want receipts for this account."),
        Request("custom_03", "c3@example.com", "2026-09-05T12:00:00Z", "COMPLETE", "2026-09-06T12:00:00Z", "2026-09-10T12:00:00Z", "Delete my account and make sure backups cannot bring my profile back."),
        Request("custom_04", "c4@example.com", "2026-09-06T12:00:00Z", "COMPLETE", "2026-09-07T12:00:00Z", "2026-09-10T12:00:00Z", "Je demande la suppression définitive du compte."),
        Request("custom_05", "c5@example.com", "2026-09-07T12:00:00Z", "COMPLETE", "2026-09-08T12:00:00Z", "2026-09-10T12:00:00Z", "I deleted this account but the same email is still recognized. Please remove any remaining copy."),
    ]
    ds = Dataset(reqs, ids, AUDIT, acts, [], FILES)
    return ds, parsed


GOLD = {
    "custom_01": ("INCOMPLETE", [
        {"phase": "FINDING", "system_id": "identity_service", "resource_id": "tombstone_9101", "operation": "REPORT_EXPOSED_PII", "state": "PRESENT", "evidence_id": "evidence_c1"},
        {"phase": "REMEDIATION", "system_id": "identity_service", "resource_id": "tombstone_9101", "operation": "REDACT_RECORD", "state": "REDACTED", "evidence_id": ""},
        {"phase": "VERIFICATION", "system_id": "account_service", "resource_id": "account_9101", "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": ""},
        {"phase": "VERIFICATION", "system_id": "security_service", "resource_id": "audit_record_9101", "operation": "VERIFY_RECORD_PRESENT", "state": "PRESENT", "evidence_id": ""},
        {"phase": "VERIFICATION", "system_id": "identity_service", "resource_id": "tombstone_9101", "operation": "VERIFY_RECORD_REDACTED", "state": "REDACTED", "evidence_id": ""},
    ]),
    "custom_02": ("NOT_APPLICABLE", []),
    "custom_03": ("INCOMPLETE", [
        {"phase": "FINDING", "system_id": "account_service", "resource_id": "account_9103", "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "evidence_c2"},
        {"phase": "FINDING", "system_id": "backup_service", "resource_id": "backup_9103", "operation": "REPORT_RESTORE_RISK", "state": "ACTIVE", "evidence_id": "evidence_c2"},
        {"phase": "REMEDIATION", "system_id": "backup_service", "resource_id": "backup_9103", "operation": "BLOCK_RESTORE", "state": "BLOCKED", "evidence_id": ""},
        {"phase": "REMEDIATION", "system_id": "account_service", "resource_id": "account_9103", "operation": "DELETE_RECORD", "state": "ABSENT", "evidence_id": ""},
        {"phase": "VERIFICATION", "system_id": "account_service", "resource_id": "account_9103", "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": ""},
        {"phase": "VERIFICATION", "system_id": "backup_service", "resource_id": "backup_9103", "operation": "VERIFY_RESTORE_BLOCKED", "state": "BLOCKED", "evidence_id": ""},
    ]),
    "custom_04": ("COMPLETE", [
        {"phase": "VERIFICATION", "system_id": "account_service", "resource_id": "account_9104", "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": "evidence_c2"},
        {"phase": "VERIFICATION", "system_id": "search_service", "resource_id": "search_9104", "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": "evidence_c2"},
    ]),
    "custom_05": ("INCOMPLETE", [
        {"phase": "FINDING", "system_id": "account_service", "resource_id": "account_9105", "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "evidence_c5"},
        {"phase": "REMEDIATION", "system_id": "account_service", "resource_id": "account_9105", "operation": "DELETE_RECORD", "state": "ABSENT", "evidence_id": ""},
        {"phase": "VERIFICATION", "system_id": "account_service", "resource_id": "account_9105", "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": ""},
    ]),
}


def run():
    ds, parsed = world()
    store = EvidenceStore(ds.evidence, parsed)
    resolver = IdentityResolver(ds.identities, ds.audit_log)
    activity = ActivityIndex(ds.activities)
    gate = IntentGate(None)
    engine = Engine(ds, store, resolver, activity, gate, {}, {}, {})
    pred = {}
    for r in ds.requests:
        res = engine.audit_case(r)
        pred[r.case_id] = (res.status, res.steps)
    gold = {k: (s, [{"step_no": i + 1, **st} for i, st in enumerate(steps)]) for k, (s, steps) in GOLD.items()}
    return score(pred, gold), pred


def main() -> int:
    rep, pred = run()
    print(format_report(rep))
    return 0 if rep["sequence_exact"] == rep["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
