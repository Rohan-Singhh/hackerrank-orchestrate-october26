"""Unit tests for the deterministic core. Run: pytest code/tests -q"""
import json
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))

from audit.data import EvidenceFile  # noqa: E402
from audit.identity import IdentityResolver  # noqa: E402
from audit.observations import ANALYTICS_PROFILE, SUPPORT_MESSAGE, USER_PROFILE, Observation  # noqa: E402
from audit.policy import DELETE, decide  # noqa: E402
from audit.steps import build_steps, sort_steps  # noqa: E402
from llm.intent import regex_verdict  # noqa: E402
from parsers import logs, txt  # noqa: E402

IDS = [{"user_id": "user_1", "name": "Ana Lima", "email": "ana.lima@gmail.com", "phone": "+55 11 9000-0001", "email_hash": "a" * 64, "updated_at": "2026-01-01T00:00:00Z"},
       {"user_id": "user_2", "name": "Ana Lima", "email": "ana.lima.jr@gmail.com", "phone": "+55 11 9000-0002", "email_hash": "b" * 64, "updated_at": "2026-01-01T00:00:00Z"}]
AUDIT = [{"audit_id": "audit_1", "user_id": "user_1", "event_time": "2025-05-01T00:00:00Z", "field_name": "email", "old_value": "old.ana@yahoo.com", "new_value": "ana.lima@gmail.com"},
         {"audit_id": "audit_2", "user_id": "user_1", "event_time": "2025-05-01T00:00:00Z", "field_name": "email_hash", "old_value": "c" * 64, "new_value": "a" * 64}]


def test_identity_current_historical_and_decoys():
    r = IdentityResolver(IDS, AUDIT)
    ids = r.resolve("OLD.ANA@yahoo.com")
    assert ids.user_id == "user_1"
    assert ids.matches("ana.lima@gmail.com") and ids.matches("old.ana@yahoo.com") and ids.matches("C" * 64)
    assert not ids.matches("ana.lima.jr@gmail.com"), "near-miss email must not match"
    assert ids.matches("+55 (11) 9000 0001")
    assert ids.matches_name("Ana  Lima") and not ids.matches("Ana Lima")


def test_policy_tombstone_and_support_user_id_only_is_compliant():
    o = Observation("evidence_1", "2026-09-01T00:00:00Z", "support_1", SUPPORT_MESSAGE, True, identifiers={"user_id": "user_1"}, subject_refs={"user_1"})
    assert decide(o, "2026-09-15T00:00:00Z").compliant
    o2 = Observation("evidence_1", "2026-09-01T00:00:00Z", "support_2", SUPPORT_MESSAGE, True, identifiers={"email": "x@y.z"}, subject_refs={"x@y.z"})
    assert not decide(o2, "2026-09-15T00:00:00Z").compliant
    o3 = Observation("evidence_1", "2026-09-01T00:00:00Z", "analytics_1", ANALYTICS_PROFILE, True, identifiers={"user_id": "user_1"}, subject_refs={"user_1"})
    assert not decide(o3, "2026-09-15T00:00:00Z").compliant, "analytics attributable via user_id must be anonymised"


def test_steps_order_and_numbering_match_sample_case_11():
    from audit.policy import Decision
    ds = [Decision("search_052", "search_service", "SEARCH_INDEX_ENTRY", DELETE, False, "evidence_03"),
          Decision("analytics_2034", "analytics_platform", ANALYTICS_PROFILE, "ANONYMIZE", False, "evidence_03"),
          Decision("support_864", "support_platform", SUPPORT_MESSAGE, "REDACT", False, "evidence_03"),
          Decision("data_flow_9522", "integration_service", "DATA_FLOW", "DISABLE", False, "evidence_03"),
          Decision("account_4248", "account_service", USER_PROFILE, DELETE, True, "evidence_03"),
          Decision("audit_record_9394", "security_service", "SECURITY_AUDIT_LOG", "RETAIN", True, "evidence_03")]
    status, steps = build_steps(ds)
    assert status == "INCOMPLETE"
    assert [s["step_no"] for s in steps] == list(range(1, 15))
    assert [s["operation"] for s in steps][:5] == ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW"]
    assert [s["resource_id"] for s in steps if s["phase"] == "VERIFICATION"] == ["account_4248", "search_052", "audit_record_9394", "support_864", "analytics_2034", "data_flow_9522"]
    assert all(s["evidence_id"] == "" for s in steps if s["phase"] != "FINDING")


def test_sort_resources_string_ascending():
    steps = sort_steps([{"phase": "VERIFICATION", "system_id": "search_service", "resource_id": "search_1308", "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": ""},
                        {"phase": "VERIFICATION", "system_id": "search_service", "resource_id": "search_052", "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": ""}])
    assert [s["resource_id"] for s in steps] == ["search_052", "search_1308"]


def test_intent_regex():
    assert regex_verdict("Please permanently delete the account associated with this email address.")[0] == "DELETION"
    assert regex_verdict("I'd like to download my account information in a portable format. Please keep the account as it is.") == ("NON_DELETION", 0.9)
    assert regex_verdict("Je demande la suppression définitive du compte associé à cette adresse e-mail.")[0] == "DELETION"
    assert regex_verdict("このメールアドレスに関連するアカウントの完全な削除を依頼します。")[0] == "DELETION"
    v, conf = regex_verdict("I deleted this account and received a confirmation, but the same email address is still recognized. Please remove any remaining copy of my profile.")
    assert v == "DELETION"


def test_trace_log_parser_pairs_started_and_completed():
    ef = EvidenceFile("evidence_t", "evidence/logs/x.log", "o", "2026-09-26T10:38:56Z")
    text = """2026-09-26T08:57:37Z level=DEBUG service=account_service trace_id=rb-085737-009 event=profile_lookup_started resource_id=account_7566 lookup_key=l.fontaine46@yahoo.com
2026-09-26T08:57:37Z level=INFO service=account_service trace_id=rb-085737-009 event=profile_lookup_completed resource_id=account_7566 rows=0 http_status=404
2026-07-28T05:51:38Z [DEBUG] [support_platform] [tr-2053433] message_scan_started: instance="support-6" subject_key="menon.arjun@gmail.com" attempt="1"
2026-07-28T05:51:41Z [WARN] [support_platform] [tr-2053433] message_pii_scan: instance="support-6" resource_id="support_2322" matched_value="menon.arjun@gmail.com" detector="direct_identifier"
"""
    res = logs.parse(ef, text)
    by = {o.resource_id: o for o in res.observations}
    assert by["account_7566"].present is False and "l.fontaine46@yahoo.com" in by["account_7566"].subject_refs
    assert by["support_2322"].identifiers.get("email") == "menon.arjun@gmail.com"


def test_handover_parser():
    ef = EvidenceFile("evidence_h", "evidence/records/h.txt", "o", "2026-10-01T12:00:00Z")
    res = txt.parse(ef, "- 2026-10-01T12:00:00Z | search_service | search_052 | SEARCH_INDEX_ENTRY | FOUND | novak.tomasz@icloud.com\n- 2026-10-01T12:00:00Z | account_service | account_4248 | USER_PROFILE | NOT_FOUND | novak.tomasz@icloud.com\n")
    by = {o.resource_id: o for o in res.observations}
    assert by["search_052"].present is True and by["account_4248"].present is False
